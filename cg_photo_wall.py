import os
import sys
import subprocess
import xml.etree.ElementTree as ET
from functools import lru_cache
from threading import Lock
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QScrollArea,
    QWidget,
    QFileDialog,
    QMessageBox,
    QButtonGroup,
    QRadioButton,
    QComboBox,
    QLineEdit,
    QFrame,
)
from PySide6.QtCore import Qt, Signal, QSize, QTimer, QSettings, QObject
from PySide6.QtGui import (
    QPixmap,
    QIcon,
    QGuiApplication,
    QPalette,
    QColor,
    QImageReader,
)
import concurrent.futures
from enum import Enum
from nfo_utils import is_path_within, preferred_image_file, preferred_nfo_file


class LoadStage(Enum):
    SCANNING = 1
    LOADING = 2


class BluePalette(QPalette):
    """蓝色主题调色板"""

    def __init__(self):
        super().__init__()
        self.setColor(QPalette.Window, QColor(10, 20, 45))
        self.setColor(QPalette.WindowText, QColor(255, 255, 255))
        self.setColor(QPalette.Base, QColor(15, 25, 50))
        self.setColor(QPalette.AlternateBase, QColor(20, 30, 55))
        self.setColor(QPalette.Text, QColor(255, 255, 255))
        self.setColor(QPalette.Button, QColor(30, 40, 65))
        self.setColor(QPalette.ButtonText, QColor(255, 255, 255))
        self.setColor(QPalette.Highlight, QColor(65, 105, 225))
        self.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
        self.setColor(QPalette.Disabled, QPalette.WindowText, QColor(128, 128, 128))
        self.setColor(QPalette.Disabled, QPalette.Text, QColor(128, 128, 128))
        self.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(128, 128, 128))


class ImageLoadManager(QObject):
    """高 DPI 图片加载管理器；每次 add_images 使用独立批次计数。"""

    progress_updated = Signal(int, int, int)  # batch_id, current, total
    image_loaded = Signal(int, str, QLabel, object, float)
    all_done = Signal(int)

    def __init__(self, max_workers=None):
        super().__init__()
        self.max_workers = max_workers or min(8, (os.cpu_count() or 4))
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        )
        self.is_running = True
        self._lock = Lock()
        self._next_batch_id = 0
        self._batches = {}
        self._futures = set()

    def _mark_done(self, batch_id, success=False):
        emit_done = False
        with self._lock:
            state = self._batches.get(batch_id)
            if state is None:
                return
            if success:
                state["loaded"] += 1
            state["completed"] += 1
            current = state["loaded"]
            total = state["total"]
            if state["completed"] >= total and not state["done"]:
                state["done"] = True
                emit_done = True

        self.progress_updated.emit(batch_id, current, total)
        if emit_done:
            self.all_done.emit(batch_id)

    def load_image(
        self, batch_id, image_path, label, target_width, target_height, dpr=1.0
    ):
        success = False
        try:
            if not self.is_running:
                return False

            dpr = max(1.0, float(dpr or 1.0))
            physical_width = max(1, int(round(target_width * dpr)))
            physical_height = max(1, int(round(target_height * dpr)))

            reader = QImageReader(image_path)
            reader.setAutoTransform(True)
            if reader.canRead():
                original_size = reader.size()
                if original_size.width() > 0 and original_size.height() > 0:
                    width_ratio = physical_width / original_size.width()
                    height_ratio = physical_height / original_size.height()
                    scale_ratio = min(width_ratio, height_ratio)
                    new_width = max(1, int(round(original_size.width() * scale_ratio)))
                    new_height = max(1, int(round(original_size.height() * scale_ratio)))
                    reader.setScaledSize(QSize(new_width, new_height))

                image = reader.read()
                if self.is_running and not image.isNull():
                    self.image_loaded.emit(batch_id, image_path, label, image, dpr)
                    success = True
                    return True
        except Exception as exc:
            print(f"加载图片失败 {image_path}: {exc}")
        finally:
            self._mark_done(batch_id, success=success)
        return False

    def add_images(self, image_paths_and_labels, target_width, target_height, dpr=1.0):
        items = list(image_paths_and_labels)
        with self._lock:
            self._next_batch_id += 1
            batch_id = self._next_batch_id
            self._batches[batch_id] = {
                "total": len(items), "loaded": 0, "completed": 0, "done": False
            }

        if not items:
            self.all_done.emit(batch_id)
            return batch_id

        for path, label in items:
            if not self.is_running:
                break
            future = self.executor.submit(
                self.load_image,
                batch_id, path, label, target_width, target_height, dpr,
            )
            with self._lock:
                self._futures.add(future)
            future.add_done_callback(self._discard_future)
        return batch_id

    def _discard_future(self, future):
        with self._lock:
            self._futures.discard(future)

    def stop(self):
        self.is_running = False
        with self._lock:
            futures = list(self._futures)
        for future in futures:
            future.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)


class PosterContainer(QFrame):
    """海报容器组件"""

    def __init__(
        self, poster_width, poster_height, title_height, dpi_scale, parent=None
    ):
        super().__init__(parent)
        self.setFixedSize(poster_width, poster_height + title_height)
        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: rgb(25, 25, 25);
                border-radius: {int(6 * dpi_scale)}px;
                border: 1px solid rgb(40, 40, 40);
            }}
            QFrame:hover {{
                background-color: rgb(35, 35, 35);
                border: 1px solid rgb(60, 60, 60);
            }}
        """
        )

        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(0)
        self.layout.setContentsMargins(0, 0, 0, 0)


class PhotoWallDialog(QDialog):
    """优化后的照片墙对话框 - 简化版"""

    update_image = Signal(QLabel, QPixmap)

    def __init__(self, folder_path=None, parent=None):
        super().__init__(parent)
        # parent_window 与 Qt parent 解耦：Qt parent 决定对象树归属，
        # parent_window 仅用于回调编辑器，通过 set_editor() 单独注入。
        self.parent_window = None
        self.folder_path = folder_path
        self.all_posters = []          # 当前墙面正在展示的数据
        self.master_posters = []       # 全量数据，优先从 NFO Editor 内存缓存接收
        self.current_wall_source = []  # 当前墙面的筛选基准，例如某个演员/系列下的全部影片
        self._sort_keys = {}
        self.view_mode = "wall"
        self.active_category_type = None
        self.active_category_value = None
        self._category_index_cache = {}

        # UI容器列表
        self.poster_containers = []

        # 优化窗口大小改变处理
        self.resize_timer = QTimer()
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.handle_resize)

        # 获取屏幕DPI信息
        screen = QGuiApplication.primaryScreen()
        self.dpi_scale = screen.logicalDotsPerInch() / 96.0

        # 图片加载管理
        self._pending_image_batches = set()
        self.image_manager = None
        self._reset_image_manager()

        self.is_loading = False
        self.settings = QSettings("NFOEditor", "PhotoWall")

        # UI刷新计数器（每N个容器刷新一次界面）
        self.ui_refresh_interval = 50

        # 分页加载：避免一次性渲染海量 QLabel 导致文字消失
        self.PAGE_SIZE = 300          # 每页最多显示的容器数量
        self.displayed_count = 0      # 当前已渲染到 grid 的容器数量（= window_start + 已渲染数）
        self.window_start = 0         # 滑动窗口起点（当前 grid 中第一个 poster 对应 all_posters 的索引）
        self._load_more_btn = None    # 占位（兼容旧引用，不再显示）
        self._auto_scroll_loading = False   # 防止滚动回调重入

        # 滚动自动加载防抖计时器
        self._scroll_timer = QTimer()
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._on_scroll_check)

        # 分类筛选防抖
        self._category_filter_timer = QTimer()
        self._category_filter_timer.setSingleShot(True)
        self._category_filter_timer.timeout.connect(self._refresh_category_view_if_active)

        # 分类页分批渲染，避免一次性创建大量按钮导致界面卡顿
        self._category_render_timer = QTimer()
        self._category_render_timer.setSingleShot(True)
        self._category_render_timer.timeout.connect(self._render_category_chunk)
        self._category_render_items = []
        self._category_render_index = 0
        self._category_render_columns = 6
        self._category_render_card_w = 140
        self._category_render_card_h = 64
        self.CATEGORY_CHUNK_SIZE = 96

        self.init_ui()

        # 延迟加载：等窗口完成布局（show）后再调用，确保 viewport 宽度正确
        if folder_path:
            QTimer.singleShot(0, lambda: self.load_posters(folder_path))
        else:
            last_dir = self.settings.value("last_directory", "")
            if last_dir and os.path.exists(last_dir):
                self.folder_path = last_dir

    def set_editor(self, editor):
        """注入编辑器引用（与 Qt parent 完全解耦，仅用于回调定位）。"""
        self.parent_window = editor
        return self


    def load_from_editor_cache(self, editor):
        """从 NFO Editor 主窗口加载照片墙数据的兼容入口。

        nfo_editor.py 当前会调用:
            dialog.load_from_editor_cache(self)

        这里负责注入主窗口引用，并复用主窗口已经加载好的 nfo_cache / nfo_files。
        """
        self.set_editor(editor)

        folder_path = getattr(editor, "folder_path", None)
        if not folder_path:
            try:
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "提示", "主窗口尚未选择 NFO 目录")
            except Exception:
                pass
            return

        self.folder_path = folder_path
        self.load_posters(folder_path)

    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("大锤 照片墙 v9.7.7")
        self.setStyleSheet(
            """
            QDialog {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                        stop:0 rgb(10, 20, 45),
                                        stop:1 rgb(20, 40, 80));
            }
            QScrollArea {
                border: none;
                background: transparent;
            }
            QPushButton {
                background-color: rgb(50, 50, 50);
                color: rgb(240, 240, 240);
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgb(60, 60, 60);
            }
            QComboBox {
                background-color: rgb(35, 35, 35);
                color: rgb(240, 240, 240);
                border: 1px solid rgb(50, 50, 50);
                border-radius: 4px;
                padding: 4px;
            }
            QLineEdit {
                background-color: rgb(35, 35, 35);
                color: rgb(240, 240, 240);
                border: 1px solid rgb(50, 50, 50);
                border-radius: 4px;
                padding: 4px;
            }
            QRadioButton {
                color: rgb(240, 240, 240);
            }
            QLabel {
                color: rgb(240, 240, 240);
            }
            """
        )

        # 获取主屏幕大小
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(int(screen.width() * 0.75), int(screen.height() * 0.75))

        # 设置为独立窗口
        self.setWindowFlags(
            Qt.Window
            | Qt.WindowSystemMenuHint
            | Qt.WindowMinMaxButtonsHint
            | Qt.WindowCloseButtonHint
        )

        # 主布局
        layout = QVBoxLayout(self)
        layout.setSpacing(int(10 * self.dpi_scale))
        layout.setContentsMargins(
            int(15 * self.dpi_scale),
            int(15 * self.dpi_scale),
            int(15 * self.dpi_scale),
            int(15 * self.dpi_scale),
        )

        # 工具栏
        toolbar = self.create_toolbar()
        layout.addWidget(toolbar)

        # 滚动区域
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setStyleSheet(
            """
            QScrollBar:vertical {
                border: none;
                background: rgb(25, 25, 25);
                width: 10px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgb(50, 50, 50);
                min-height: 20px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgb(70, 70, 70);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
            """
        )

        # 内容容器
        self.content_widget = QWidget()
        self.grid = QGridLayout(self.content_widget)
        self.grid.setSpacing(int(15 * self.dpi_scale))
        self.scroll.setWidget(self.content_widget)

        # 连接滚动条信号 → 触发自动加载（防抖 200ms）
        self.scroll.verticalScrollBar().valueChanged.connect(
            lambda _: self._scroll_timer.start(200)
        )

        layout.addWidget(self.scroll)

        # 状态栏和进度条布局
        status_layout = QHBoxLayout()

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("加载进度: %v/%m")
        self.progress_bar.setMinimumWidth(200)
        self.progress_bar.hide()
        status_layout.addWidget(self.progress_bar)

        # 取消按钮
        self.cancel_button = QPushButton("取消加载")
        self.cancel_button.clicked.connect(self.cancel_loading)
        self.cancel_button.hide()
        status_layout.addWidget(self.cancel_button)

        # 状态标签
        self.status_label = QLabel()
        self.status_label.setStyleSheet(
            f"""
            QLabel {{
                color: rgb(180, 180, 180);
                font-size: {int(12 * self.dpi_scale)}px;
                padding: {int(8 * self.dpi_scale)}px;
                background-color: rgb(25, 25, 25);
                border-radius: 4px;
            }}
            """
        )
        status_layout.addWidget(self.status_label)

        status_layout.addStretch()
        layout.addLayout(status_layout)

        # 「加载更多」按钮（分页用）
        self._load_more_btn = QPushButton("加载更多...")
        self._load_more_btn.setStyleSheet("""
            QPushButton {
                background-color: rgb(50, 80, 50);
                color: rgb(220, 220, 220);
                border: none;
                border-radius: 4px;
                padding: 10px 30px;
                font-size: 14px;
            }
            QPushButton:hover { background-color: rgb(60, 100, 60); }
        """)
        self._load_more_btn.clicked.connect(self._show_next_page)
        self._load_more_btn.hide()
        layout.addWidget(self._load_more_btn, alignment=Qt.AlignHCenter)

        # 设置窗口图标
        try:
            icon_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "cg_photo_wall.ico"
            )
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except Exception:
            pass

        # 连接重绘事件
        self.resizeEvent = self.on_resize

    def create_toolbar(self):
        """创建工具栏"""
        toolbar = QFrame()
        toolbar.setStyleSheet(
            """
                QFrame {
                    background-color: rgb(25, 25, 25);
                    border-radius: 4px;
                }
                """
        )
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setSpacing(int(10 * self.dpi_scale))
        toolbar_layout.setContentsMargins(
            int(10 * self.dpi_scale),
            int(10 * self.dpi_scale),
            int(10 * self.dpi_scale),
            int(10 * self.dpi_scale),
        )

        # 选择目录按钮
        select_button = QPushButton("选择目录")
        select_button.setMinimumHeight(int(32 * self.dpi_scale))
        select_button.clicked.connect(self.select_folder)
        toolbar_layout.addWidget(select_button)

        self.all_wall_button = QPushButton("全部影片")
        self.all_wall_button.setMinimumHeight(int(32 * self.dpi_scale))
        self.all_wall_button.clicked.connect(self.show_all_wall)
        toolbar_layout.addWidget(self.all_wall_button)

        self.category_button = QPushButton("分类")
        self.category_button.setMinimumHeight(int(32 * self.dpi_scale))
        self.category_button.clicked.connect(self.show_category_view)
        toolbar_layout.addWidget(self.category_button)

        self.category_basis_combo = QComboBox()
        self.category_basis_combo.addItems(["演员", "系列"])
        self.category_basis_combo.setFixedWidth(int(80 * self.dpi_scale))
        self.category_basis_combo.currentIndexChanged.connect(
            lambda _: self.show_category_view() if self.view_mode == "category" else None
        )
        toolbar_layout.addWidget(self.category_basis_combo)

        self.category_filter_entry = QLineEdit()
        self.category_filter_entry.setPlaceholderText("筛选分类")
        self.category_filter_entry.setFixedWidth(int(140 * self.dpi_scale))
        self.category_filter_entry.textChanged.connect(
            lambda _: self._category_filter_timer.start(250)
        )
        toolbar_layout.addWidget(self.category_filter_entry)

        self.active_category_label = QLabel("")
        self.active_category_label.setStyleSheet(
            "QLabel { color: #DBEAFE; font-weight: 700; padding: 0 8px; }"
        )
        toolbar_layout.addWidget(self.active_category_label)

        # 排序和筛选面板
        filter_frame = self.create_filter_panel()
        toolbar_layout.addWidget(filter_frame)

        return toolbar

    def create_filter_panel(self):
        """创建过滤面板"""
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(
            int(10 * self.dpi_scale),
            int(5 * self.dpi_scale),
            int(10 * self.dpi_scale),
            int(5 * self.dpi_scale),
        )
        layout.setSpacing(int(15 * self.dpi_scale))

        # 排序选项
        sort_label = QLabel("排序:")
        sort_label.setStyleSheet(f"font-size: {int(12 * self.dpi_scale)}px;")
        layout.addWidget(sort_label)

        self.sorting_group = QButtonGroup(self)
        sort_options = ["日期", "演员", "系列", "评分"]
        for text in sort_options:
            radio = QRadioButton(text)
            radio.setStyleSheet(f"font-size: {int(12 * self.dpi_scale)}px;")
            self.sorting_group.addButton(radio)
            layout.addWidget(radio)

        # 筛选选项
        self.field_combo = QComboBox()
        self.field_combo.addItems(["标题", "标签", "演员", "系列", "评分"])
        self.field_combo.setFixedWidth(int(80 * self.dpi_scale))
        self.field_combo.setStyleSheet(f"font-size: {int(12 * self.dpi_scale)}px;")
        layout.addWidget(self.field_combo)

        self.condition_combo = QComboBox()
        self.condition_combo.setFixedWidth(int(80 * self.dpi_scale))
        self.condition_combo.setStyleSheet(f"font-size: {int(12 * self.dpi_scale)}px;")
        layout.addWidget(self.condition_combo)

        self.filter_entry = QLineEdit()
        self.filter_entry.setFixedWidth(int(120 * self.dpi_scale))
        self.filter_entry.setStyleSheet(f"font-size: {int(12 * self.dpi_scale)}px;")
        layout.addWidget(self.filter_entry)

        # 筛选按钮
        filter_button = QPushButton("筛选")
        filter_button.setFixedWidth(int(60 * self.dpi_scale))
        filter_button.clicked.connect(self.apply_filter)
        layout.addWidget(filter_button)

        layout.addStretch()

        # 连接信号
        self.field_combo.currentIndexChanged.connect(self.on_field_changed)
        self.sorting_group.buttonClicked.connect(self.sort_posters)

        # 设置初始条件
        self.on_field_changed(0)
        if self.sorting_group.buttons():
            self.sorting_group.buttons()[0].setChecked(True)

        return frame

    def calculate_grid_dimensions(self):
        """计算网格尺寸"""
        available_width = self.scroll.viewport().width()
        # 保护：viewport 未渲染时返回 0，用窗口宽度兜底
        if available_width <= 0:
            available_width = self.width() - 30
        # 仍然无效时给一个最小合理值
        if available_width <= 0:
            available_width = 800
        spacing = int(15 * self.dpi_scale)

        columns = 8
        poster_width = max(80, (available_width - (columns + 1) * spacing) // columns)
        poster_height = int(poster_width * 1.5)
        title_height = int(70 * self.dpi_scale)

        return columns, poster_width, poster_height, title_height

    def _get_device_pixel_ratio(self):
        try:
            return max(1.0, float(self.devicePixelRatioF()))
        except Exception:
            screen = self.windowHandle().screen() if self.windowHandle() else QGuiApplication.primaryScreen()
            return max(1.0, float(screen.devicePixelRatio() if screen else 1.0))

    def _reset_image_manager(self):
        old_manager = getattr(self, "image_manager", None)
        if old_manager is not None:
            try:
                old_manager.progress_updated.disconnect(self.update_progress)
                old_manager.image_loaded.disconnect(self.update_image_label)
                old_manager.all_done.disconnect(self._on_image_load_all_done)
            except (RuntimeError, TypeError):
                pass
            try:
                old_manager.stop()
            except Exception:
                pass
        self._pending_image_batches = set()
        self.image_manager = ImageLoadManager()
        self.image_manager.progress_updated.connect(self.update_progress)
        self.image_manager.image_loaded.connect(self.update_image_label)
        self.image_manager.all_done.connect(self._on_image_load_all_done)

    def _queue_image_batch(self, queue, width, height):
        if not queue:
            return None
        if not getattr(self.image_manager, "is_running", False):
            self._reset_image_manager()
        batch_id = self.image_manager.add_images(
            queue, width, height, self._get_device_pixel_ratio()
        )
        self._pending_image_batches.add(batch_id)
        return batch_id

    def _clear_grid_widgets(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _find_poster_for_folder(self, folder_path):
        try:
            if not folder_path or not os.path.isdir(folder_path):
                return None
            candidates = []
            for file_name in os.listdir(folder_path):
                lower = file_name.lower()
                if lower.endswith((".jpg", ".jpeg", ".png", ".webp")) and "poster" in lower:
                    full_path = os.path.join(folder_path, file_name)
                    priority = 0 if lower.endswith(("-poster.jpg", "-poster.jpeg", "-poster.png", "-poster.webp")) else 1
                    candidates.append((priority, file_name.lower(), full_path))
            if not candidates:
                return None
            candidates.sort()
            return candidates[0][2]
        except OSError:
            return None

    def _normalize_editor_cache_data(self, data):
        release = str(data.get("release") or "")
        year = ""
        if release:
            year = release.split("-")[0]
        rating = data.get("rating", "0")
        if rating is None:
            rating = "0"
        actors = data.get("actors") or []
        if isinstance(actors, str):
            actors = [a.strip() for a in actors.split(",") if a.strip()]
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        return {
            "title": data.get("title") or "",
            "year": year,
            "series": data.get("series") or "",
            "rating": str(rating),
            "actors": actors,
            "tags": tags,
            "release": release,
        }

    def _build_posters_from_editor_cache(self, folder_path):
        """优先复用主窗口已经加载好的 nfo_cache，避免照片墙再次 os.walk + ET.parse。"""
        editor = self.parent_window
        if not editor:
            return []

        cache = getattr(editor, "nfo_cache", None)
        nfo_paths = list(getattr(editor, "nfo_files", []) or [])
        if not cache or not nfo_paths:
            return []

        posters = []
        for nfo_path in nfo_paths:
            try:
                if not nfo_path or not os.path.exists(nfo_path):
                    continue
                if not is_path_within(nfo_path, folder_path):
                    continue
                data = cache.get(nfo_path)
                if not data:
                    data = self.parse_nfo(nfo_path)
                nfo_dir = os.path.dirname(nfo_path)
                poster_file = self._find_poster_for_folder(nfo_dir)
                if not poster_file:
                    continue
                posters.append((
                    poster_file,
                    os.path.basename(nfo_dir),
                    self._normalize_editor_cache_data(data),
                ))
            except Exception as e:
                print(f"从主窗口缓存读取照片墙数据失败 {nfo_path}: {str(e)}")
        return posters

    def _rebuild_sort_keys_for_current_list(self):
        self._sort_keys.clear()
        for index, (_, _, nfo_data) in enumerate(self.all_posters):
            self._update_sort_keys(nfo_data, index)
        self._rebuild_series_sort_keys()

    def _render_first_page(self):
        self.view_mode = "wall"
        self._reset_image_manager()
        self._clear_grid_widgets()
        self.poster_containers.clear()
        self.displayed_count = 0
        self.window_start = 0
        self._load_more_btn.hide()
        columns, poster_width, poster_height, title_height = self.calculate_grid_dimensions()
        self._render_page(columns, poster_width, poster_height, title_height)

    def load_posters(self, folder_path):
        """加载照片墙。

        从主程序打开时，优先复用 NFO Editor 已经加载好的 nfo_cache/nfo_files；
        只有独立运行或主程序缓存为空时，才退回扫描磁盘和解析 NFO。
        """
        if not folder_path or not os.path.exists(folder_path):
            return

        self.folder_path = folder_path
        self.clear_all_data()

        self.progress_bar.show()
        self.cancel_button.show()
        self.is_loading = True

        try:
            self.progress_bar.setFormat("读取主程序缓存...")
            self.update_status(0, None, LoadStage.LOADING)
            QApplication.processEvents()

            cached_posters = self._build_posters_from_editor_cache(folder_path)
            if cached_posters:
                self.master_posters = cached_posters
                source_text = "主程序缓存"
            else:
                self.progress_bar.setFormat("扫描文件中...")
                self.update_status(0, None, LoadStage.SCANNING)
                QApplication.processEvents()

                poster_nfo_pairs = []
                for root, dirs, files in os.walk(folder_path):
                    if not self.is_loading:
                        return
                    dirs.sort(key=str.lower)
                    preferred_stem = os.path.basename(root)
                    poster_file = preferred_image_file(root, "poster", preferred_stem)
                    nfo_file = preferred_nfo_file(root, preferred_stem)
                    if poster_file and nfo_file:
                        poster_nfo_pairs.append((poster_file, nfo_file, root))

                total_items = len(poster_nfo_pairs)
                if total_items == 0:
                    self.update_status(0)
                    self.progress_bar.hide()
                    self.cancel_button.hide()
                    self.is_loading = False
                    return

                self.progress_bar.setFormat("加载影片: %v/%m")
                self.progress_bar.setMaximum(total_items)

                for index, (poster_file, nfo_file, root) in enumerate(poster_nfo_pairs):
                    if not self.is_loading:
                        return
                    try:
                        nfo_data = self.parse_nfo(nfo_file)
                        folder_name = os.path.basename(root)
                        self.master_posters.append((poster_file, folder_name, nfo_data))
                        if (index + 1) % self.ui_refresh_interval == 0:
                            self.progress_bar.setValue(index + 1)
                            self.update_status(index + 1, total_items, LoadStage.LOADING)
                            QApplication.processEvents()
                    except Exception as e:
                        print(f"处理文件失败 {nfo_file}: {str(e)}")

                source_text = "磁盘扫描"

            self.all_posters = self.master_posters.copy()
            self.current_wall_source = self.master_posters.copy()
            self._rebuild_sort_keys_for_current_list()

            self.progress_bar.setFormat("准备渲染...")
            self.progress_bar.setValue(0)
            QApplication.processEvents()

            self._render_first_page()
            self.update_status(len(self.all_posters))
            self.status_label.setText(f"共显示 {len(self.all_posters)} 个影片（来源：{source_text}）")

        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载海报失败: {str(e)}")
            self.is_loading = False
            self.progress_bar.hide()
            self.cancel_button.hide()

    def _render_page(self, columns, poster_width, poster_height, title_height):
        """向下渲染下一页容器到 grid，异步加载这一页的图片。
        追加完成后自动裁剪顶部，保持滑动窗口 ≤ 2×PAGE_SIZE 个 widget。
        """
        start = self.displayed_count
        end = min(start + self.PAGE_SIZE, len(self.all_posters))
        if start >= end:
            return

        image_load_queue = []

        for data_index in range(start, end):
            poster_file, folder_name, nfo_data = self.all_posters[data_index]
            grid_index = data_index - self.window_start  # grid行/列位置

            container_info = self.create_single_container(
                grid_index, poster_file, nfo_data, poster_width, poster_height, title_height, columns
            )
            if container_info:
                while len(self.poster_containers) <= data_index - self.window_start:
                    self.poster_containers.append(None)
                self.poster_containers[data_index - self.window_start] = container_info
                image_load_queue.append((poster_file, container_info["poster_label"]))

        self.displayed_count = end

        # ---- 裁剪顶部：维持窗口 ≤ 2×PAGE_SIZE，使 window_start 持续推进 ----
        MAX_RENDERED = 2 * self.PAGE_SIZE
        if len(self.poster_containers) > MAX_RENDERED:
            to_trim = len(self.poster_containers) - MAX_RENDERED
            vbar = self.scroll.verticalScrollBar()
            scroll_before = vbar.value()
            content_h_before = self.content_widget.sizeHint().height()

            for _ in range(to_trim):
                ci = self.poster_containers.pop(0)
                if ci and ci.get("container"):
                    self.grid.removeWidget(ci["container"])
                    ci["container"].deleteLater()
            self.window_start += to_trim

            # 重新为所有剩余 widget 设置新的 grid 位置（顶部行号归零）
            for rel_idx, ci in enumerate(self.poster_containers):
                if ci and ci.get("container"):
                    row = rel_idx // columns
                    col = rel_idx % columns
                    self.grid.addWidget(ci["container"], row, col)

            # 裁剪后补偿滚动位置（顶部高度减少，滚动条需相应上移）
            def _fix_scroll(sb=scroll_before, ch_before=content_h_before):
                QApplication.processEvents()
                ch_after = self.content_widget.sizeHint().height()
                removed_h = max(0, ch_before - ch_after)
                self.scroll.verticalScrollBar().setValue(max(1, sb - removed_h))

            QTimer.singleShot(30, _fix_scroll)

        # 异步加载这一页的图片。按 DPR 读取物理像素，避免高 DPI 显示模糊。
        if image_load_queue:
            if not getattr(self.image_manager, "is_running", False):
                self._reset_image_manager()
            self.progress_bar.setFormat("加载图片: %v/%m")
            self.progress_bar.show()
            self.cancel_button.show()
            self.is_loading = True
            self._queue_image_batch(image_load_queue, poster_width, poster_height)

        # 始终隐藏"加载更多"按钮（自动滚动已完全取代手动按钮）
        self._load_more_btn.hide()

        self.update_status(self.displayed_count)

    def _on_scroll_check(self):
        """滚动防抖回调：检测是否到达底部（加载下一页）或顶部（滑动到上一页）"""
        if self._auto_scroll_loading:
            return

        vbar = self.scroll.verticalScrollBar()
        val = vbar.value()
        maximum = vbar.maximum()

        # ---- 到达底部：向下追加下一页 ----
        if maximum > 0 and val >= maximum - max(200, int(maximum * 0.08)):
            if self.displayed_count < len(self.all_posters) and not self.is_loading:
                self._auto_scroll_loading = True
                try:
                    columns, poster_width, poster_height, title_height = self.calculate_grid_dimensions()
                    self._render_page(columns, poster_width, poster_height, title_height)
                finally:
                    self._auto_scroll_loading = False

        # ---- 到达顶部：向上滑动窗口（显示上一页）----
        elif val == 0 and self.window_start > 0:
            self._load_prev_page()

    def _load_prev_page(self):
        """滑动窗口向上：在顶部渲染上一页，同时从底部移除等量容器，保持内存稳定"""
        if self._auto_scroll_loading or self.window_start <= 0:
            return

        self._auto_scroll_loading = True
        try:
            prev_start = max(0, self.window_start - self.PAGE_SIZE)
            prev_count = self.window_start - prev_start  # 要插入的条目数

            columns, poster_width, poster_height, title_height = self.calculate_grid_dimensions()

            # ---- 1. 移除底部等量容器（维持总渲染数在 2*PAGE_SIZE 附近）----
            current_rendered = self.displayed_count - self.window_start
            to_remove = min(prev_count, current_rendered)
            for _ in range(to_remove):
                last_idx = len(self.poster_containers) - 1
                if last_idx < 0:
                    break
                ci = self.poster_containers.pop(last_idx)
                if ci and ci.get("container"):
                    self.grid.removeWidget(ci["container"])
                    ci["container"].deleteLater()
            self.displayed_count -= to_remove

            # ---- 2. 移动现有 grid 中的容器往后（行/列偏移 prev_count 格）----
            # 先把所有现有容器取出，后面重新插入
            for ci in self.poster_containers:
                if ci and ci.get("container"):
                    self.grid.removeWidget(ci["container"])

            # ---- 3. 在顶部插入上一页容器 ----
            new_containers = []
            image_load_queue = []
            for data_index in range(prev_start, self.window_start):
                poster_file, folder_name, nfo_data = self.all_posters[data_index]
                grid_index = data_index - prev_start  # 相对于新 window_start
                ci = self.create_single_container(
                    grid_index, poster_file, nfo_data, poster_width, poster_height, title_height, columns
                )
                if ci:
                    new_containers.append(ci)
                    image_load_queue.append((poster_file, ci["poster_label"]))
                else:
                    new_containers.append(None)

            # ---- 4. 更新 poster_containers 并重新布局所有容器 ----
            self.poster_containers = new_containers + self.poster_containers
            self.window_start = prev_start

            # 重新按新 grid_index 排列所有容器
            for rel_idx, ci in enumerate(self.poster_containers):
                if ci and ci.get("container"):
                    row = rel_idx // columns
                    col = rel_idx % columns
                    self.grid.addWidget(ci["container"], row, col)

            # ---- 5. 异步加载新插入的图片 ----
            if image_load_queue:
                if not getattr(self.image_manager, "is_running", False):
                    self._reset_image_manager()
                self._queue_image_batch(image_load_queue, poster_width, poster_height)

            # 滚动到刚加载内容的末尾（约一页高度处），避免立刻再次触发顶部检测
            QTimer.singleShot(50, lambda: self.scroll.verticalScrollBar().setValue(
                max(1, self.scroll.verticalScrollBar().maximum() // 4)
            ))

            self.update_status(self.displayed_count)

        except Exception as e:
            print(f"向上加载上一页失败: {str(e)}")
            import traceback
            traceback.print_exc()
        finally:
            self._auto_scroll_loading = False

    def _show_next_page(self):
        """「加载更多」按钮回调"""
        if self.displayed_count >= len(self.all_posters):
            return
        columns, poster_width, poster_height, title_height = self.calculate_grid_dimensions()
        self._render_page(columns, poster_width, poster_height, title_height)

    def create_single_container(self, index, poster_file, nfo_data, poster_width, poster_height, title_height, columns):
        """创建单个容器并显示"""
        try:
            row = index // columns
            col = index % columns

            # 创建容器
            container = PosterContainer(
                poster_width, poster_height, title_height, self.dpi_scale
            )
            container_layout = container.layout

            # 创建海报图片标签
            poster_label = QLabel()
            poster_label.setObjectName(f"poster_{row}_{col}")
            poster_label.setAlignment(Qt.AlignCenter)
            poster_label.setFixedSize(poster_width, poster_height)
            poster_label.setCursor(Qt.PointingHandCursor)
            poster_label.setStyleSheet(
                """
                QLabel {
                    background-color: rgb(18, 18, 18);
                    border-top-left-radius: 4px;
                    border-top-right-radius: 4px;
                }
                """
            )

            # 创建标题区域
            title_widget = QWidget()
            title_widget.setFixedSize(poster_width, title_height)
            title_widget.setCursor(Qt.PointingHandCursor)

            title_layout = QVBoxLayout(title_widget)
            title_layout.setSpacing(int(2 * self.dpi_scale))
            title_layout.setContentsMargins(
                int(10 * self.dpi_scale),
                int(5 * self.dpi_scale),
                int(10 * self.dpi_scale),
                int(5 * self.dpi_scale),
            )

            # 标题标签
            title_label = QLabel()
            title_label.setStyleSheet(
                f"""
                QLabel {{
                    color: rgb(240, 240, 240);
                    font-size: {int(13 * self.dpi_scale)}px;
                    font-weight: bold;
                }}
                """
            )
            title_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
            title_label.setWordWrap(True)
            title_label.setFixedHeight(int(title_height * 0.6))

            # 信息标签
            info_label = QLabel()
            info_label.setStyleSheet(
                f"""
                QLabel {{
                    color: rgb(180, 180, 180);
                    font-size: {int(12 * self.dpi_scale)}px;
                }}
                """
            )
            info_label.setAlignment(Qt.AlignCenter)

            title_layout.addWidget(title_label)
            title_layout.addWidget(info_label)
            container_layout.addWidget(poster_label)
            container_layout.addWidget(title_widget)

            # 添加到网格
            self.grid.addWidget(container, row, col)

            # 更新文本内容
            title = nfo_data.get("title", "")
            title_label.setText(title)

            info_parts = []
            if year := nfo_data.get("year"):
                info_parts.append(year)
            if rating := nfo_data.get("rating"):
                try:
                    rating_float = float(rating)
                    info_parts.append(f"★{rating_float:.1f}")
                except (ValueError, TypeError):
                    pass
            if actors := nfo_data.get("actors"):
                if actors and isinstance(actors, list):
                    info_parts.append(actors[0])

            info_label.setText(" · ".join(info_parts))

            # 确保所有组件都可见
            poster_label.show()
            title_label.show()
            info_label.show()
            title_widget.show()
            container.show()

            # 绑定事件
            poster_path = os.path.dirname(poster_file)
            if poster_path and os.path.exists(poster_path):
                poster_label.mousePressEvent = lambda e, p=poster_path: self.play_video(p)
                title_widget.mousePressEvent = lambda e, p=poster_path: self.select_in_editor(p)

            # 返回容器信息
            container_info = {
                "container": container,
                "poster_label": poster_label,
                "title_label": title_label,
                "info_label": info_label,
                "row": row,
                "col": col,
            }

            return container_info

        except Exception as e:
            print(f"创建容器失败，索引 {index}: {str(e)}")
            return None

    def clear_all_data(self):
        """清空所有数据，并使旧图片加载批次失效。"""
        self._stop_category_rendering()
        self._reset_image_manager()
        self._clear_grid_widgets()
        self.all_posters.clear()
        self.master_posters.clear()
        self.current_wall_source.clear()
        self._sort_keys.clear()
        self._category_index_cache.clear()
        self.poster_containers.clear()
        self.displayed_count = 0
        self.window_start = 0
        self.view_mode = "wall"
        self.active_category_type = None
        self.active_category_value = None
        self.parse_nfo.cache_clear()

    def _update_sort_keys(self, nfo_data, index):
        """更新排序键"""
        # 处理评分排序键
        try:
            rating_str = nfo_data.get("rating", "0")
            rating_key = float(rating_str if rating_str and rating_str.strip() else "0")
        except (ValueError, TypeError):
            rating_key = 0.0

        if "评分" not in self._sort_keys:
            self._sort_keys["评分"] = []
        self._sort_keys["评分"].append((rating_key, index, len(self._sort_keys.get("评分", []))))

        # 演员
        actors = nfo_data.get("actors", [])
        actors_key = actors[0] if actors else ""
        if "演员" not in self._sort_keys:
            self._sort_keys["演员"] = []
        self._sort_keys["演员"].append(
            (actors_key, index, len(self._sort_keys.get("演员", [])))
        )

        # 系列（先只存 series_name，全量加载后调用 _rebuild_series_sort_keys 用计数重建）
        series_key = nfo_data.get("series") or ""
        if "系列" not in self._sort_keys:
            self._sort_keys["系列"] = []
        self._sort_keys["系列"].append(
            (series_key, index, len(self._sort_keys.get("系列", [])), 0, 0)
            # 占位 5 元组：(series_name, data_index, counter, empty_flag, neg_count)
            # empty_flag 和 neg_count 会在 _rebuild_series_sort_keys 中填入真实值
        )

        # 日期
        release = nfo_data.get("release", "")
        try:
            from datetime import datetime

            date_key = datetime.strptime(release, "%Y-%m-%d")
        except:
            from datetime import datetime
            date_key = datetime.min

        if "日期" not in self._sort_keys:
            self._sort_keys["日期"] = []
        self._sort_keys["日期"].append(
            (date_key, index, len(self._sort_keys.get("日期", [])))
        )

    def _rebuild_series_sort_keys(self):
        """全量加载完成后，根据系列出现次数重建 '系列' 排序键。
        排序规则：同一系列作品数越多越靠前；空系列排末尾；同系列内按计数器保持原顺序。
        5元组格式：(series_name, data_index, counter, empty_flag, neg_count)
          empty_flag: 0=有系列 / 1=无系列（确保空系列排末尾）
          neg_count:  -count（使 sort 升序时 count 大的排前面）
        """
        if "系列" not in self._sort_keys:
            return

        # 统计每个系列的出现次数
        series_count: dict = {}
        for (name, _, _, _, _) in self._sort_keys["系列"]:
            series_count[name] = series_count.get(name, 0) + 1

        # 重建（保持 data_index 和 counter 不变，更新 empty_flag / neg_count）
        rebuilt = []
        for (name, data_index, counter, _, _) in self._sort_keys["系列"]:
            empty_flag = 0 if name else 1
            neg_count = -series_count.get(name, 0)
            rebuilt.append((name, data_index, counter, empty_flag, neg_count))

        self._sort_keys["系列"] = rebuilt

    def _collect_categories(self, category_type):
        """按演员或系列收集分类。返回 [(name, indices)]，默认按影片数量从多到少排序。"""
        cache_key = (category_type, len(self.master_posters))
        cached = self._category_index_cache.get("__cache__", {}).get(cache_key)
        if cached is not None:
            return cached

        buckets = {}
        for index, (_, _, nfo_data) in enumerate(self.master_posters):
            if category_type == "演员":
                values = nfo_data.get("actors") or []
                if isinstance(values, str):
                    values = [v.strip() for v in values.split(",") if v.strip()]
                if not values:
                    values = ["(无演员)"]
            else:
                value = (nfo_data.get("series") or "").strip()
                values = [value or "(未分系列)"]

            for value in values:
                value = str(value).strip() or "(空)"
                buckets.setdefault(value, []).append(index)

        items = list(buckets.items())
        # 数量多的排前面；同数量按名称排；空值类放后面
        items.sort(key=lambda kv: (kv[0].startswith("("), -len(kv[1]), kv[0].lower()))

        self._category_index_cache.setdefault("__cache__", {})[cache_key] = items
        return items

    def _refresh_category_view_if_active(self):
        if self.view_mode == "category":
            self.show_category_view()

    def _category_card_style(self):
        return """
            QPushButton {
                background-color: rgba(255, 255, 255, 248);
                color: #111827;
                border: 1px solid #D1D5DB;
                border-radius: 8px;
                padding: 6px;
                font-size: 13px;
                font-weight: 700;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #DBEAFE;
                border: 2px solid #3B82F6;
                color: #0F172A;
            }
            QPushButton:pressed {
                background-color: #2563EB;
                border: 2px solid #1D4ED8;
                color: white;
                padding-top: 8px;
                padding-left: 8px;
            }
        """

    def _category_card_loading_style(self):
        return """
            QPushButton {
                background-color: #2563EB;
                color: white;
                border: 2px solid #1D4ED8;
                border-radius: 8px;
                padding: 6px;
                font-size: 13px;
                font-weight: 800;
            }
        """

    def _stop_category_rendering(self):
        if hasattr(self, "_category_render_timer"):
            self._category_render_timer.stop()
        self._category_render_items = []
        self._category_render_index = 0

    def _prepare_category_layout_metrics(self):
        viewport_width = self.scroll.viewport().width()
        if viewport_width <= 0:
            viewport_width = max(900, self.width() - 40)

        if viewport_width >= 1500:
            columns = 10
        elif viewport_width >= 1300:
            columns = 9
        elif viewport_width >= 1100:
            columns = 8
        elif viewport_width >= 900:
            columns = 7
        else:
            columns = 5

        spacing = int(10 * self.dpi_scale)
        card_w = max(120, (viewport_width - (columns + 1) * spacing) // columns)
        card_h = int(66 * self.dpi_scale)
        return columns, card_w, card_h

    def _render_category_chunk(self):
        """分批创建分类卡片，减少点击“分类”时的同步卡顿。"""
        if self.view_mode != "category":
            return

        start = self._category_render_index
        end = min(start + self.CATEGORY_CHUNK_SIZE, len(self._category_render_items))
        if start >= end:
            self.progress_bar.hide()
            self.cancel_button.hide()
            self.status_label.setText(
                f"{self.category_basis_combo.currentText()}分类：共 {len(self._category_render_items)} 个分类，"
                f"影片总数 {len(self.master_posters)}，已按影片数量排序"
            )
            return

        columns = self._category_render_columns
        card_w = self._category_render_card_w
        card_h = self._category_render_card_h

        for index in range(start, end):
            name, indices = self._category_render_items[index]
            row = index // columns
            col = index % columns

            count = len(indices)
            button = QPushButton(f"{name}\n{count} 部")
            button.setFixedSize(card_w, card_h)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(f"{self.category_basis_combo.currentText()}: {name}，共 {count} 部影片")
            button.setStyleSheet(self._category_card_style())
            button.clicked.connect(
                lambda checked=False, b=button, ct=self.category_basis_combo.currentText(), n=name:
                    self._on_category_card_clicked(b, ct, n)
            )
            self.grid.addWidget(button, row, col)

        self._category_render_index = end
        self.progress_bar.setMaximum(max(1, len(self._category_render_items)))
        self.progress_bar.setValue(end)
        self.status_label.setText(
            f"正在渲染分类：{end}/{len(self._category_render_items)}"
        )

        if end < len(self._category_render_items):
            self._category_render_timer.start(0)
        else:
            self.progress_bar.hide()
            self.cancel_button.hide()
            self.status_label.setText(
                f"{self.category_basis_combo.currentText()}分类：共 {len(self._category_render_items)} 个分类，"
                f"影片总数 {len(self.master_posters)}，已按影片数量排序"
            )

    def _on_category_card_clicked(self, button, category_type, category_value):
        """分类卡片点击后立即给视觉反馈，再打开对应影片墙。"""
        try:
            button.setText(f"{category_value}\n正在打开...")
            button.setStyleSheet(self._category_card_loading_style())
            button.setEnabled(False)
            self.status_label.setText(f"正在打开 {category_type}「{category_value}」...")
            QApplication.processEvents()
        except Exception:
            pass

        QTimer.singleShot(
            0,
            lambda ct=category_type, cv=category_value: self.show_category_wall(ct, cv)
        )

    def show_category_view(self):
        """显示分类列表。分类支持按演员/系列切换，也支持分类名称筛选。"""
        if not self.master_posters:
            QMessageBox.warning(self, "提示", "请先加载照片墙数据")
            return

        self._stop_category_rendering()
        self.view_mode = "category"
        self.active_category_type = None
        self.active_category_value = None
        self.is_loading = False

        # 分类页不需要停止/重建图片线程池，避免额外卡顿；仅清 UI。
        self.progress_bar.setFormat("渲染分类: %v/%m")
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.cancel_button.hide()
        self._clear_grid_widgets()
        self.poster_containers.clear()
        self.displayed_count = 0
        self.window_start = 0
        self._load_more_btn.hide()

        category_type = self.category_basis_combo.currentText() if hasattr(self, "category_basis_combo") else "演员"
        filter_text = ""
        if hasattr(self, "category_filter_entry"):
            filter_text = self.category_filter_entry.text().strip().lower()

        all_items = self._collect_categories(category_type)
        if filter_text:
            all_items = [
                (name, indices)
                for name, indices in all_items
                if filter_text in name.lower()
            ]

        # 这个字典只存当前筛选结果，供点击卡片时 O(1) 查找。
        cache_root = self._category_index_cache.get("__cache__", {})
        self._category_index_cache = {"__cache__": cache_root}
        self._category_index_cache.update({name: indices for name, indices in all_items})

        columns, card_w, card_h = self._prepare_category_layout_metrics()
        self._category_render_items = all_items
        self._category_render_index = 0
        self._category_render_columns = columns
        self._category_render_card_w = card_w
        self._category_render_card_h = card_h

        if hasattr(self, "active_category_label"):
            self.active_category_label.setText(f"分类：{category_type} · 数量优先")

        self.status_label.setText(f"准备渲染 {len(all_items)} 个分类...")
        self._category_render_timer.start(0)

    def show_category_wall(self, category_type, category_value):
        """点击分类后显示对应影片墙。"""
        if not self.master_posters:
            return

        self._stop_category_rendering()

        indices = self._category_index_cache.get(category_value)
        if indices is None:
            indices = dict(self._collect_categories(category_type)).get(category_value, [])

        self.active_category_type = category_type
        self.active_category_value = category_value
        self.current_wall_source = [self.master_posters[i] for i in indices]
        self.all_posters = self.current_wall_source.copy()
        self._rebuild_sort_keys_for_current_list()

        if hasattr(self, "active_category_label"):
            self.active_category_label.setText(f"{category_type}：{category_value} · {len(self.all_posters)} 部")

        self._render_first_page()
        self.status_label.setText(
            f"{category_type}「{category_value}」：共显示 {len(self.all_posters)} 个影片"
        )

    def show_all_wall(self):
        """回到全部影片墙。"""
        if not self.master_posters:
            return

        self._stop_category_rendering()
        self.active_category_type = None
        self.active_category_value = None
        self.current_wall_source = self.master_posters.copy()
        self.all_posters = self.current_wall_source.copy()
        self._rebuild_sort_keys_for_current_list()

        if hasattr(self, "active_category_label"):
            self.active_category_label.setText("全部影片")

        self._render_first_page()
        self.update_status(len(self.all_posters))


    def play_video(self, folder_path):
        """播放视频文件"""
        if not folder_path or not os.path.exists(folder_path):
            return

        video_extensions = [
            ".mp4",
            ".mkv",
            ".avi",
            ".mov",
            ".rm",
            ".mpeg",
            ".ts",
            ".strm",
        ]

        for ext in video_extensions:
            for file in os.listdir(folder_path):
                if file.lower().endswith(ext):
                    video_path = os.path.join(folder_path, file)
                    try:
                        if ext == ".strm":
                            with open(video_path, "r", encoding="utf-8") as f:
                                strm_url = f.readline().strip()
                            if strm_url:
                                subprocess.Popen(["mpvnet", strm_url])
                            else:
                                QMessageBox.critical(self, "错误", "STRM文件内容为空")
                        else:
                            subprocess.Popen(["mpvnet", video_path])
                        return
                    except Exception as e:
                        QMessageBox.critical(self, "错误", f"播放视频失败: {str(e)}")
                        return

        QMessageBox.warning(self, "警告", "未找到匹配的视频文件")

    def select_in_editor(self, folder_path):
        """在编辑器中选择相应文件夹"""
        try:
            if self.parent_window:
                self.parent_window.select_folder_in_tree(folder_path)
                # 还原最小化，然后激活到前台（平级窗口可正常置顶）
                self.parent_window.setWindowState(
                    self.parent_window.windowState() & ~Qt.WindowMinimized | Qt.WindowActive
                )
                self.parent_window.activateWindow()
                self.parent_window.raise_()
            else:
                if getattr(sys, "frozen", False):
                    current_dir = os.path.dirname(sys.executable)
                else:
                    current_dir = os.path.dirname(os.path.abspath(__file__))

                is_py = not getattr(sys, "frozen", False)

                # PySide6 版优先使用当前源码入口或发布包根目录的 NFOEditor.exe。
                if is_py:
                    candidate_paths = [
                        os.path.join(current_dir, "nfo_editor.py"),
                        os.path.join(current_dir, "NFO_Editor_Qt5.py"),
                        os.path.join(current_dir, "NFO.Editor.Qt5.py"),
                    ]
                else:
                    candidate_paths = [
                        os.path.join(current_dir, "NFOEditor.exe"),
                        os.path.normpath(os.path.join(current_dir, "..", "..", "NFOEditor.exe")),
                        os.path.normpath(os.path.join(current_dir, "..", "NFOEditor", "NFOEditor.exe")),
                        os.path.join(current_dir, "NFO_Editor_Qt5.exe"),
                        os.path.join(current_dir, "NFO.Editor.Qt5.exe"),
                    ]

                editor_path = next(
                    (path for path in candidate_paths if os.path.isfile(path)),
                    None,
                )

                if not editor_path:
                    searched = ", ".join(os.path.basename(path) for path in candidate_paths)
                    QMessageBox.critical(
                        self, "错误",
                        f"找不到编辑器程序，已查找：{searched}"
                    )
                    return

                extra_args = [
                    "--base-path", os.path.dirname(folder_path),
                    "--select-folder", folder_path,
                ]

                if is_py:
                    python_exe = sys.executable
                    if sys.platform == "win32":
                        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                        if os.path.exists(pythonw):
                            python_exe = pythonw
                    args = [python_exe, editor_path] + extra_args
                else:
                    args = [editor_path] + extra_args

                subprocess.Popen(args)

        except Exception as e:
            QMessageBox.critical(self, "错误", f"启动编辑器失败: {str(e)}")

    def disable_sorting_controls(self):
        """禁用排序和筛选相关的控件"""
        for button in self.sorting_group.buttons():
            button.setEnabled(False)

        if hasattr(self, "field_combo"):
            self.field_combo.setEnabled(False)
        if hasattr(self, "condition_combo"):
            self.condition_combo.setEnabled(False)
        if hasattr(self, "filter_entry"):
            self.filter_entry.setEnabled(False)

    def enable_sorting_controls(self):
        """启用排序和筛选相关的控件"""
        for button in self.sorting_group.buttons():
            button.setEnabled(True)

        if hasattr(self, "field_combo"):
            self.field_combo.setEnabled(True)
        if hasattr(self, "condition_combo"):
            self.condition_combo.setEnabled(True)
        if hasattr(self, "filter_entry"):
            self.filter_entry.setEnabled(True)

    def sort_posters(self):
        """排序函数 - 重排当前影片墙后重新分页渲染第一页。"""
        if self.view_mode == "category":
            return
        if not self.sorting_group.checkedButton():
            return

        sort_by = self.sorting_group.checkedButton().text()
        if sort_by not in self._sort_keys:
            QMessageBox.warning(self, "警告", f"未找到 {sort_by} 的排序数据")
            return

        try:
            self.disable_sorting_controls()

            self.progress_bar.setFormat(f"正在 {sort_by} 排序...")
            self.progress_bar.setValue(0)
            self.progress_bar.setMaximum(100)
            self.progress_bar.show()
            QApplication.processEvents()

            # ---- 1. 纯内存排序，得到新顺序索引 ----
            sort_tuples = self._sort_keys[sort_by]

            if sort_by == "评分":
                sorted_tuples = sorted(
                    sort_tuples,
                    key=lambda x: (
                        -(x[0] if x[0] is not None else 0),  # 高分在前
                        x[2],
                    ),
                )
            elif sort_by == "日期":
                sorted_tuples = sorted(
                    sort_tuples,
                    key=lambda x: x[0],
                    reverse=True,  # 新日期在前
                )
            elif sort_by == "系列":
                # key = (空系列放末尾, -series_count, series_name, counter)
                # 使用 _sort_keys["系列"] 中已预计算的 (-count, name) 元组
                sorted_tuples = sorted(
                    sort_tuples,
                    key=lambda x: (x[3], x[4], x[0], x[2]),  # (空标志, -count, name, counter)
                )
            else:  # 演员
                sorted_tuples = sorted(
                    sort_tuples,
                    key=lambda x: (x[0] or "", x[2]),
                )

            sorted_indices = [t[1] for t in sorted_tuples]
            self.progress_bar.setValue(20)
            QApplication.processEvents()

            # ---- 2. 按新顺序重建 all_posters ----
            self.all_posters = [self.all_posters[i] for i in sorted_indices]
            if not self.filter_entry.text().strip():
                self.current_wall_source = self.all_posters.copy()
            self.progress_bar.setValue(40)
            QApplication.processEvents()

            # ---- 3. 销毁所有已渲染的 widget ----
            while self.grid.count():
                item = self.grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.poster_containers.clear()
            self.displayed_count = 0
            self.window_start = 0
            self._load_more_btn.hide()

            self.progress_bar.setValue(60)
            QApplication.processEvents()

            # ---- 4. 重建排序键（索引已重置）----
            self._sort_keys.clear()
            for new_index, (_, _, nfo_data) in enumerate(self.all_posters):
                self._update_sort_keys(nfo_data, new_index)
            # 重新计算系列排序键（需要知道全局 count）
            self._rebuild_series_sort_keys()

            self.progress_bar.setValue(80)
            QApplication.processEvents()

            # ---- 5. 渲染第一页 ----
            columns, poster_width, poster_height, title_height = self.calculate_grid_dimensions()
            self._render_page(columns, poster_width, poster_height, title_height)

            self.progress_bar.setValue(100)
            self.update_status(len(self.all_posters))

        except Exception as e:
            print(f"排序失败: {str(e)}")
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self, "警告", f"排序过程中发生错误: {str(e)}")
        finally:
            self.progress_bar.hide()
            self.enable_sorting_controls()
    
    def _movie_matches_filter(self, nfo_data, field, condition, filter_text):
        value = ""
        if field == "标题":
            value = nfo_data.get("title") or ""
        elif field == "标签":
            value = ", ".join(t for t in (nfo_data.get("tags") or []) if t)
        elif field == "演员":
            value = ", ".join(a for a in (nfo_data.get("actors") or []) if a)
        elif field == "系列":
            value = nfo_data.get("series") or ""
        elif field == "评分":
            value = str(nfo_data.get("rating") or "0")

        if field == "评分":
            try:
                current_value = float(value)
                filter_value = float(filter_text)
                return current_value > filter_value if condition == "大于" else current_value < filter_value
            except ValueError:
                return False

        value_lower = value.lower()
        text_lower = filter_text.lower()
        return text_lower in value_lower if condition == "包含" else text_lower not in value_lower

    def apply_filter(self):
        """筛选函数 - 对当前影片源的全部数据筛选，而不是只筛选已渲染的第一页。"""
        if self.view_mode == "category":
            self.show_category_view()
            return

        field = self.field_combo.currentText()
        condition = self.condition_combo.currentText()
        filter_text = self.filter_entry.text().strip()

        try:
            source = self.current_wall_source or self.master_posters
            if not filter_text:
                self.all_posters = source.copy()
            else:
                self.all_posters = [
                    poster
                    for poster in source
                    if self._movie_matches_filter(poster[2], field, condition, filter_text)
                ]

            self._rebuild_sort_keys_for_current_list()
            self._render_first_page()
            self.update_status(len(self.all_posters))

        except Exception as e:
            print(f"筛选失败: {str(e)}")
            QMessageBox.warning(self, "警告", "筛选过程中发生错误")

    def on_field_changed(self, index):
        """字段改变处理"""
        self.condition_combo.clear()
        self.filter_entry.clear()
        if self.field_combo.currentText() == "评分":
            self.condition_combo.addItems(["大于", "小于"])
        else:
            self.condition_combo.addItems(["包含", "不包含"])

    def update_progress(self, batch_id, current, total):
        """仅显示仍属于当前墙面的批次进度。"""
        if batch_id not in self._pending_image_batches:
            return
        self.progress_bar.setMaximum(max(1, total))
        self.progress_bar.setValue(current)

    def update_status(self, count, total=None, stage=None, cancelled=False):
        """更新状态栏"""
        if cancelled:
            self.status_label.setText(f"已取消加载，已加载 {count}/{total} 个影片")
            return

        if total is None:
            self.status_label.setText(f"共显示 {count} 个影片")
        else:
            stage_text = ""
            if stage == LoadStage.SCANNING:
                stage_text = "扫描文件"
            elif stage == LoadStage.LOADING:
                stage_text = "加载影片"

            if stage_text:
                self.status_label.setText(f"{stage_text}: {count}/{total}")

    def update_image_label(self, batch_id, path, label, image, dpr=1.0):
        """主线程更新图片；旧墙面批次和已销毁标签会被安全忽略。"""
        if batch_id not in self._pending_image_batches:
            return
        try:
            if label and not label.isHidden() and image is not None and not image.isNull():
                pixmap = QPixmap.fromImage(image)
                pixmap.setDevicePixelRatio(max(1.0, float(dpr or 1.0)))
                label.setPixmap(pixmap)
                label.show()
        except RuntimeError:
            # QLabel 已在分页裁剪/筛选切换时 deleteLater。
            return
        except Exception as exc:
            print(f"更新图片标签失败: {exc}")

    def _on_image_load_all_done(self, batch_id):
        """一个图片批次结束；仅当全部批次结束后隐藏进度条。"""
        self._pending_image_batches.discard(batch_id)
        if self._pending_image_batches:
            return
        self.progress_bar.hide()
        self.cancel_button.hide()
        self.is_loading = False
        self.update_status(len(self.all_posters))

    def on_resize(self, event):
        """窗口大小改变事件处理"""
        super().resizeEvent(event)
        self.resize_timer.start(150)

    def handle_resize(self):
        """处理窗口大小改变"""
        if not self.poster_containers:
            return

        try:
            columns, poster_width, poster_height, title_height = (
                self.calculate_grid_dimensions()
            )

            image_reload_queue = []

            for index, container_info in enumerate(self.poster_containers):
                if not container_info:
                    continue

                row = index // columns
                col = index % columns

                try:
                    container = container_info["container"]
                    container.setFixedSize(poster_width, poster_height + title_height)

                    poster_label = container_info["poster_label"]
                    poster_label.setFixedSize(poster_width, poster_height)

                    title_widget = container_info["title_label"].parent()
                    title_widget.setFixedSize(poster_width, title_height)

                    self.grid.removeWidget(container)
                    self.grid.addWidget(container, row, col)

                    # 收集需要重新加载的图片。index 是当前窗口内的相对索引，需要加 window_start。
                    data_index = self.window_start + index
                    if data_index < len(self.all_posters):
                        poster_file = self.all_posters[data_index][0]
                        image_reload_queue.append((poster_file, poster_label))

                except Exception as e:
                    print(f"更新容器 {index} 失败: {str(e)}")

            # 重新以新尺寸加载图片
            if image_reload_queue and not self.is_loading:
                self._queue_image_batch(image_reload_queue, poster_width, poster_height)

        except Exception as e:
            print(f"处理窗口大小改变失败: {str(e)}")

    def select_folder(self):
        """选择并打开NFO文件夹"""
        last_dir = self.settings.value("last_directory", "")

        folder_selected = QFileDialog.getExistingDirectory(
            self, "选择NFO文件夹", last_dir
        )

        if folder_selected:
            self.folder_path = folder_selected
            self.settings.setValue("last_directory", folder_selected)
            self.load_posters(folder_selected)

    @lru_cache(maxsize=1000)
    def parse_nfo(self, nfo_path):
        """解析NFO文件（带缓存）"""
        try:
            tree = ET.parse(nfo_path)
            root = tree.getroot()

            title = root.find("title")
            title = title.text if title is not None else ""

            year = ""
            release = root.find("release")
            if release is not None and release.text:
                try:
                    year = release.text.split("-")[0]
                except:
                    year = ""

            series = root.find("series")
            series = series.text if series is not None else ""

            rating = root.find("rating")
            rating = rating.text if rating is not None else "0"

            actors = []
            for actor in root.findall("actor"):
                name = actor.find("name")
                if name is not None and name.text:
                    actors.append(name.text.strip())

            tags = []
            for tag in root.findall("tag"):
                if tag is not None and tag.text:
                    tags.append(tag.text.strip())

            return {
                "title": title,
                "year": year,
                "series": series,
                "rating": rating,
                "actors": actors,
                "tags": tags,
                "release": release.text if release is not None else "",
            }
        except Exception as e:
            print(f"解析NFO文件失败 {nfo_path}: {str(e)}")
            return {}

    def cancel_loading(self):
        """取消当前墙面的全部图片加载批次。"""
        if self.is_loading:
            pending = len(self._pending_image_batches)
            self._reset_image_manager()
            self.is_loading = False
            self.progress_bar.hide()
            self.cancel_button.hide()
            self.status_label.setText(f"已取消图片加载（{pending} 个批次）")

    def closeEvent(self, event):
        """关闭窗口时清理资源"""
        try:
            self._stop_category_rendering()
            if self.is_loading:
                self.cancel_loading()

            if hasattr(self, "image_manager") and self.image_manager:
                self.image_manager.stop()

            self._clear_grid_widgets()
            self.poster_containers.clear()
            self.all_posters.clear()
            self.master_posters.clear()

            if self.folder_path and os.path.exists(self.folder_path):
                self.settings.setValue("last_directory", self.folder_path)

        except Exception as e:
            print(f"清理资源失败: {str(e)}")
        finally:
            super().closeEvent(event)


def main():
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

    app = QApplication(sys.argv)

    app.setPalette(BluePalette())
    app.setStyle("Fusion")

    font = app.font()
    font.setFamily("Microsoft YaHei UI")
    app.setFont(font)

    folder_path = None
    if len(sys.argv) > 1:
        folder_path = sys.argv[1]

    window = PhotoWallDialog(folder_path)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
