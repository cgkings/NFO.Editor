import os
import sys
import subprocess
import xml.etree.ElementTree as ET
from functools import lru_cache
from queue import Queue
from threading import Thread
from PyQt5.QtWidgets import (
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
    QDesktopWidget,
    QFrame,
)
from PyQt5.QtCore import Qt, pyqtSignal, QSize, QTimer, QSettings, QThread, QObject
from PyQt5.QtGui import (
    QPixmap,
    QIcon,
    QGuiApplication,
    QPalette,
    QColor,
    QFont,
    QImageReader,
)
import concurrent.futures
from enum import Enum


class LoadStage(Enum):
    SCANNING = 1  # 扫描NFO阶段
    PREPARING = 2  # 准备UI阶段
    LOADING = 3  # 加载图片阶段


class BluePalette(QPalette):
    """蓝色主题调色板"""

    def __init__(self):
        super().__init__()
        # 主要颜色 - 使用深蓝色渐变风格
        self.setColor(QPalette.Window, QColor(10, 20, 45))  # 深蓝色背景
        self.setColor(QPalette.WindowText, QColor(255, 255, 255))
        self.setColor(QPalette.Base, QColor(15, 25, 50))
        self.setColor(QPalette.AlternateBase, QColor(20, 30, 55))
        self.setColor(QPalette.Text, QColor(255, 255, 255))
        self.setColor(QPalette.Button, QColor(30, 40, 65))
        self.setColor(QPalette.ButtonText, QColor(255, 255, 255))
        self.setColor(QPalette.Highlight, QColor(65, 105, 225))
        self.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
        # 禁用状态颜色
        self.setColor(QPalette.Disabled, QPalette.WindowText, QColor(128, 128, 128))
        self.setColor(QPalette.Disabled, QPalette.Text, QColor(128, 128, 128))
        self.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(128, 128, 128))


class ImageLoadManager(QObject):
    """图片加载管理器"""

    progress_updated = pyqtSignal(int, int)  # 当前进度, 总数
    image_loaded = pyqtSignal(str, QLabel, QPixmap)  # 图片路径, 标签对象, 图片对象

    def __init__(self, max_workers=None):
        super().__init__()
        self.max_workers = max_workers or min(8, (os.cpu_count() or 4))
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers
        )
        self.queue = Queue()
        self.total_images = 0
        self.loaded_images = 0
        self.is_running = True

    def load_image(self, image_path, label):
        """加载单个图片"""
        try:
            if not self.is_running:
                return False

            reader = QImageReader(image_path)
            if reader.canRead():
                # 获取标签的实际大小
                target_size = label.size()
                # 获取原始图片大小
                original_size = reader.size()

                # 计算缩放比例，保持宽高比
                width_ratio = target_size.width() / original_size.width()
                height_ratio = target_size.height() / original_size.height()
                # 使用较小的比例以确保图片完整显示
                scale_ratio = min(width_ratio, height_ratio)

                # 计算新的尺寸
                new_width = int(original_size.width() * scale_ratio)
                new_height = int(original_size.height() * scale_ratio)

                # 设置缩放尺寸
                reader.setScaledSize(QSize(new_width, new_height))

                image = reader.read()
                if not image.isNull():
                    pixmap = QPixmap.fromImage(image)
                    self.image_loaded.emit(image_path, label, pixmap)
                    self.loaded_images += 1
                    self.progress_updated.emit(self.loaded_images, self.total_images)
                    return True

        except Exception as e:
            print(f"加载图片失败 {image_path}: {str(e)}")
        return False

    def add_images(self, image_paths_and_labels):
        """添加要加载的图片路径和标签对列表"""
        self.total_images = len(image_paths_and_labels)
        self.loaded_images = 0
        futures = []
        for path, label in image_paths_and_labels:
            if not self.is_running:
                break
            future = self.executor.submit(self.load_image, path, label)
            futures.append(future)
        return futures

    def stop(self):
        """停止加载"""
        self.is_running = False
        self.executor.shutdown(wait=False)


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

        # 使用垂直布局
        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(0)
        self.layout.setContentsMargins(0, 0, 0, 0)


class PhotoWallDialog(QDialog):
    update_image = pyqtSignal(QLabel, QPixmap)

    def __init__(self, folder_path=None, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.folder_path = folder_path
        self.all_posters = []
        self._sort_keys = {}  # 初始化排序键字典
        self.resize_timer = QTimer()
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.handle_resize)

        # 获取屏幕DPI信息
        screen = QGuiApplication.primaryScreen()
        self.dpi_scale = screen.logicalDotsPerInch() / 96.0

        # 替换原有的image_queue和image_loader
        self.image_manager = ImageLoadManager()
        self.image_manager.progress_updated.connect(self.update_progress)
        self.image_manager.image_loaded.connect(self.update_image_label)

        # 添加取消按钮状态
        self.is_loading = False

        # 加载上次使用的目录
        self.settings = QSettings("NFOEditor", "PhotoWall")

        self.init_ui()

        # 如果提供了目录路径，加载海报
        if folder_path:
            self.load_posters(folder_path)
        else:
            # 尝试加载上次的目录
            last_dir = self.settings.value("last_directory", "")
            if last_dir and os.path.exists(last_dir):
                self.folder_path = last_dir  # 仅保存路径，不自动加载

    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("大锤 照片墙 v9.5.8")
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
        screen = QDesktopWidget().availableGeometry()
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

        layout.addWidget(self.scroll)

        # 状态栏和进度条布局
        status_layout = QHBoxLayout()

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("加载进度: %v/%m")
        self.progress_bar.setMinimumWidth(200)
        self.progress_bar.hide()  # 初始隐藏
        status_layout.addWidget(self.progress_bar)

        # 取消按钮
        self.cancel_button = QPushButton("取消加载")
        self.cancel_button.clicked.connect(self.cancel_loading)
        self.cancel_button.hide()  # 初始隐藏
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

        # 添加弹性空间
        status_layout.addStretch()

        layout.addLayout(status_layout)

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

        # 添加弹性空间
        layout.addStretch()

        # 连接信号
        self.field_combo.currentIndexChanged.connect(self.on_field_changed)
        self.sorting_group.buttonClicked.connect(self.sort_posters)

        # 设置初始条件
        self.on_field_changed(0)
        if self.sorting_group.buttons():
            self.sorting_group.buttons()[0].setChecked(True)

        return frame

    def update_progress(self, current, total, stage=None):
        """更新进度条"""
        if stage:
            if stage == LoadStage.SCANNING:
                self.progress_bar.setFormat("扫描文件: %v/%m")
            elif stage == LoadStage.PREPARING:
                self.progress_bar.setFormat("准备显示: %v/%m")
            else:
                self.progress_bar.setFormat("加载图片: %v/%m")

        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.update_status(current, total, stage)

    def cancel_loading(self):
        """取消加载"""
        if self.is_loading:
            self.image_manager.stop()
            self.is_loading = False
            self.progress_bar.hide()
            self.cancel_button.hide()
            self.update_status(
                self.image_manager.loaded_images,
                self.image_manager.total_images,
                cancelled=True,
            )

    def on_resize(self, event):
        """窗口大小改变事件处理"""
        super().resizeEvent(event)
        self.resize_timer.start(150)  # 延迟处理以避免频繁重绘

    def handle_resize(self):
        """处理窗口大小改变"""
        if hasattr(self, "all_posters"):
            self.display_current_page()

    def calculate_grid_dimensions(self):
        """计算网格尺寸"""
        available_width = self.scroll.viewport().width()
        spacing = int(15 * self.dpi_scale)

        # 设置为6列
        columns = 8

        # 根据可用宽度计算海报宽度，考虑间距
        poster_width = (available_width - (columns + 1) * spacing) // columns
        # 按照电影海报的标准比例 2:3 计算高度
        poster_height = int(poster_width * 1.5)
        title_height = int(70 * self.dpi_scale)

        return columns, poster_width, poster_height, title_height

    def display_current_page(self):
        """显示所有海报"""
        # 清除现有内容
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 更新进度条状态
        self.progress_bar.setFormat("创建界面元素: %v/%m")
        total_posters = len(self.all_posters)

        # 计算网格尺寸
        max_cols, poster_width, poster_height, title_height = (
            self.calculate_grid_dimensions()
        )

        row = 0
        col = 0

        # 创建一个列表来保存所有的图片路径和对应的标签
        image_paths_and_labels = []

        # 保存所有的引用
        self.current_posters = []

        # 显示所有海报
        for index, (poster_file, folder_name, nfo_data) in enumerate(self.all_posters):
            try:
                # 更新创建进度
                self.progress_bar.setValue(index + 1)
                self.progress_bar.setMaximum(total_posters)
                self.update_status(index + 1, total_posters, "创建界面元素")
                QApplication.processEvents()
                # 创建容器
                container = PosterContainer(
                    poster_width, poster_height, title_height, self.dpi_scale
                )
                container_layout = container.layout

                # 海报图片标签
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

                # 保存标签引用和文件路径
                self.current_posters.append((poster_label, poster_file))
                image_paths_and_labels.append((poster_file, poster_label))

                # 标题区域
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
                title = nfo_data.get("title", "")
                title_label = QLabel(title)
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

                # 年份和评分信息
                info_parts = []
                if year := nfo_data.get("year"):
                    info_parts.append(year)
                if rating := nfo_data.get("rating"):
                    info_parts.append(f"★{float(rating):.1f}")
                if actors := nfo_data.get("actors"):
                    info_parts.append(actors[0] if actors else "")

                info_label = QLabel(" · ".join(info_parts))
                info_label.setStyleSheet(
                    f"""
                    QLabel {{
                        color: rgb(180, 180, 180);
                        font-size: {int(12 * self.dpi_scale)}px;
                    }}
                """
                )
                info_label.setAlignment(Qt.AlignCenter)

                # 组装布局
                title_layout.addWidget(title_label)
                title_layout.addWidget(info_label)

                container_layout.addWidget(poster_label)
                container_layout.addWidget(title_widget)

                # 绑定事件
                poster_path = os.path.dirname(poster_file)
                poster_label.mousePressEvent = lambda e, p=poster_path: self.play_video(
                    p
                )
                title_widget.mousePressEvent = (
                    lambda e, p=poster_path: self.select_in_editor(p)
                )

                # 添加到网格
                self.grid.addWidget(container, row, col)

                # 更新位置
                col += 1
                if col >= max_cols:
                    col = 0
                    row += 1

            except Exception as e:
                print(f"显示海报失败 {poster_file}: {str(e)}")

        # 开始加载图片
        if image_paths_and_labels:
            self.progress_bar.setFormat("加载图片: %v/%m")
            self.image_manager.add_images(image_paths_and_labels)

    def update_image_label(self, path, label, pixmap):
        """更新图片标签"""
        try:
            if label and not label.isHidden():
                label.setPixmap(pixmap)

            # 检查是否所有图片都已加载完成
            if (
                self.image_manager.loaded_images >= self.image_manager.total_images
                and self.is_loading
            ):
                self.progress_bar.hide()
                self.cancel_button.hide()
                self.is_loading = False
        except Exception as e:
            pass

    def clear_current_posters(self):
        """清理当前显示的海报"""
        if hasattr(self, "current_posters"):
            self.current_posters.clear()

    def select_folder(self):
        """选择并打开NFO文件夹"""
        last_dir = self.settings.value("last_directory", "")

        folder_selected = QFileDialog.getExistingDirectory(
            self, "选择NFO文件夹", last_dir
        )

        if folder_selected:
            self.folder_path = folder_selected
            # 保存当前选择的目录
            self.settings.setValue("last_directory", folder_selected)
            self.load_posters(folder_selected)

    @lru_cache(maxsize=100)
    def parse_nfo(self, nfo_path):
        """解析NFO文件（带缓存）"""
        try:
            tree = ET.parse(nfo_path)
            root = tree.getroot()

            # 基本信息
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

            # 演员列表
            actors = []
            for actor in root.findall("actor"):
                name = actor.find("name")
                if name is not None and name.text:
                    actors.append(name.text.strip())

            # 标签
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

    def load_posters(self, folder_path):
        """加载海报并预处理排序数据"""
        if not folder_path or not os.path.exists(folder_path):
            return

        self.all_posters.clear()
        self._sort_keys.clear()

        # 显示进度条
        self.progress_bar.show()
        self.cancel_button.show()
        self.is_loading = True

        # 第一阶段: 扫描文件
        poster_nfo_pairs = []
        total_dirs = sum([len(dirs) for _, dirs, _ in os.walk(folder_path)])
        current_dir = 0

        for root, dirs, files in os.walk(folder_path):
            if not self.is_loading:
                return

            current_dir += 1
            self.update_progress(current_dir, total_dirs, LoadStage.SCANNING)

            poster_file = None
            nfo_file = None
            for file in files:
                if (
                    file.lower().endswith((".jpg", ".jpeg"))
                    and "poster" in file.lower()
                ):
                    poster_file = os.path.join(root, file)
                elif file.lower().endswith(".nfo"):
                    nfo_file = os.path.join(root, file)
            if poster_file and nfo_file:
                poster_nfo_pairs.append((poster_file, nfo_file, root))

        # 第二阶段: 准备UI元素
        total_files = len(poster_nfo_pairs)
        for index, (poster_file, nfo_file, root) in enumerate(poster_nfo_pairs):
            if not self.is_loading:
                return

            try:
                nfo_data = self.parse_nfo(nfo_file)
                folder_name = os.path.basename(root)
                self.all_posters.append((poster_file, folder_name, nfo_data))
                self._update_sort_keys(nfo_data, len(self.all_posters) - 1)

                # 更新准备阶段进度
                self.update_progress(index + 1, total_files, LoadStage.PREPARING)
                QApplication.processEvents()

            except Exception as e:
                print(f"处理文件失败 {poster_file}: {str(e)}")

        # 第三阶段: 显示和加载图片
        self.display_current_page()  # 这里会开始加载图片阶段

    def _update_sort_keys(self, nfo_data, index):
        """更新排序键"""
        # 处理演员排序键 - 使用第一个演员名作为排序键
        actors = nfo_data.get("actors", [])
        actors_key = actors[0] if actors else ""
        if "演员" not in self._sort_keys:
            self._sort_keys["演员"] = []
        self._sort_keys["演员"].append((actors_key, index))

        # 处理系列排序键
        series_key = nfo_data.get("series") or ""
        if "系列" not in self._sort_keys:
            self._sort_keys["系列"] = []
        self._sort_keys["系列"].append((series_key, index))

        # 处理评分排序键
        try:
            rating_key = float(nfo_data.get("rating", 0))
        except (ValueError, TypeError):
            rating_key = 0
        if "评分" not in self._sort_keys:
            self._sort_keys["评分"] = []
        self._sort_keys["评分"].append((rating_key, index))

        # 处理日期排序键
        release = nfo_data.get("release", "")
        # 确保日期格式正确
        try:
            from datetime import datetime

            date_key = datetime.strptime(release, "%Y-%m-%d")
        except:
            date_key = datetime.min

        if "日期" not in self._sort_keys:
            self._sort_keys["日期"] = []
        self._sort_keys["日期"].append((date_key, index))

    def update_status(self, count, total, stage=None):
        """更新状态栏"""
        stage_text = ""
        if stage == LoadStage.SCANNING:
            stage_text = "扫描文件"
        elif stage == LoadStage.PREPARING:
            stage_text = "准备显示"
        elif stage == LoadStage.LOADING:
            stage_text = "加载图片"

        if stage:
            if total:
                self.status_label.setText(f"{stage_text}: {count}/{total}")
            else:
                self.status_label.setText(f"{stage_text}: {count}")
        else:
            self.status_label.setText(f"共加载 {count} 个影片")

    def sort_posters(self):
        """使用预处理的排序键进行排序"""
        if not self.sorting_group.checkedButton():
            return

        sort_by = self.sorting_group.checkedButton().text()

        # 使用预处理的排序键进行排序
        if sort_by in self._sort_keys:
            # 对索引进行排序，评分和日期默认降序，其他升序
            reverse = sort_by in ["评分", "日期"]
            sorted_pairs = sorted(
                self._sort_keys[sort_by],
                key=lambda x: x[0] or "",  # 处理None值
                reverse=reverse,
            )

            # 使用排序后的索引重组海报列表
            sorted_indices = [pair[1] for pair in sorted_pairs]
            self.all_posters = [self.all_posters[i] for i in sorted_indices]

            # 更新显示
            self.display_current_page()

    def on_field_changed(self, index):
        """字段改变处理"""
        self.condition_combo.clear()
        self.filter_entry.clear()
        if self.field_combo.currentText() == "评分":
            self.condition_combo.addItems(["大于", "小于"])
        else:
            self.condition_combo.addItems(["包含", "不包含"])

    def apply_filter(self):
        """应用筛选"""
        field = self.field_combo.currentText()
        condition = self.condition_combo.currentText()
        filter_text = self.filter_entry.text().strip()

        if not filter_text:
            self.display_current_page()
            self.update_status(len(self.all_posters))
            return

        filtered = []
        for poster_info in self.all_posters:
            _, _, nfo_data = poster_info

            value = ""
            if field == "标题":
                value = nfo_data.get("title", "")
            elif field == "标签":
                value = ", ".join(nfo_data.get("tags", []))
            elif field == "演员":
                value = ", ".join(nfo_data.get("actors", []))
            elif field == "系列":
                value = nfo_data.get("series", "")
            elif field == "评分":
                value = nfo_data.get("rating", "0")

            match = False
            if field == "评分":
                try:
                    current_value = float(value)
                    filter_value = float(filter_text)
                    if condition == "大于":
                        match = current_value > filter_value
                    else:  # 小于
                        match = current_value < filter_value
                except ValueError:
                    continue
            else:
                if condition == "包含":
                    match = filter_text.lower() in value.lower()
                else:  # 不包含
                    match = filter_text.lower() not in value.lower()

            if match:
                filtered.append(poster_info)

        # 更新显示
        self.all_posters = filtered
        self.display_current_page()
        self.update_status(len(filtered), len(self.all_posters))

    def select_in_editor(self, folder_path):
        """在编辑器中选择相应文件夹"""
        try:
            if self.parent_window:
                self.parent_window.select_folder_in_tree(folder_path)
                self.parent_window.activateWindow()
                self.parent_window.raise_()
            else:
                # 获取当前程序所在目录
                if getattr(sys, "frozen", False):
                    # 如果是打包后的 exe
                    current_dir = os.path.dirname(sys.executable)
                else:
                    # 如果是 py 脚本
                    current_dir = os.path.dirname(os.path.abspath(__file__))

                # 根据当前程序运行方式选择对应的编辑器程序
                is_py = os.path.splitext(sys.argv[0])[1].lower() == ".py"
                editor_name = "NFO.Editor.Qt5.py" if is_py else "NFO.Editor.Qt5.exe"
                editor_path = os.path.join(current_dir, editor_name)

                if not os.path.exists(editor_path):
                    QMessageBox.critical(self, "错误", f"找不到编辑器程序{editor_name}")
                    return

                if is_py:
                    # 如果是 .py 文件，使用 Python 解释器启动
                    args = [sys.executable]
                    if sys.platform == "win32":
                        pythonw = os.path.join(
                            os.path.dirname(sys.executable), "pythonw.exe"
                        )
                        if os.path.exists(pythonw):
                            args = [pythonw]
                    args.extend(
                        [
                            editor_path,
                            "--base-path",
                            os.path.dirname(folder_path),
                            "--select-folder",
                            folder_path,
                        ]
                    )
                else:
                    # 如果是 .exe 文件，直接启动
                    args = [
                        editor_path,
                        "--base-path",
                        os.path.dirname(folder_path),
                        "--select-folder",
                        folder_path,
                    ]

                subprocess.Popen(args)

        except Exception as e:
            QMessageBox.critical(self, "错误", f"启动编辑器失败: {str(e)}")

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

    def closeEvent(self, event):
        """关闭窗口时清理资源"""
        if hasattr(self, "image_manager"):
            self.image_manager.stop()
        super().closeEvent(event)


def main():
    # 在创建 QApplication 之前设置高DPI属性
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

    app = QApplication(sys.argv)

    # 设置蓝色主题
    app.setPalette(BluePalette())
    app.setStyle("Fusion")

    # 设置全局字体
    font = app.font()
    font.setFamily("Microsoft YaHei UI")
    app.setFont(font)

    # 解析命令行参数
    folder_path = None
    if len(sys.argv) > 1:
        folder_path = sys.argv[1]

    window = PhotoWallDialog(folder_path)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
