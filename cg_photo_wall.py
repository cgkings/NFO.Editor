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
                target_size = label.size()
                original_size = reader.size()

                width_ratio = target_size.width() / original_size.width()
                height_ratio = target_size.height() / original_size.height()
                scale_ratio = min(width_ratio, height_ratio)

                new_width = int(original_size.width() * scale_ratio)
                new_height = int(original_size.height() * scale_ratio)

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

        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(0)
        self.layout.setContentsMargins(0, 0, 0, 0)


class PhotoWallDialog(QDialog):
    """优化后的照片墙对话框"""

    update_image = pyqtSignal(QLabel, QPixmap)

    def __init__(self, folder_path=None, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.folder_path = folder_path
        self.all_posters = []
        self._sort_keys = {}

        # 新增：存储所有创建的框体和映射关系
        self.poster_containers = []
        self.data_to_container_map = {}

        # 优化窗口大小改变处理
        self.resize_timer = QTimer()
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.handle_resize)

        # 获取屏幕DPI信息
        screen = QGuiApplication.primaryScreen()
        self.dpi_scale = screen.logicalDotsPerInch() / 96.0

        # 图片加载管理
        self.image_manager = ImageLoadManager()
        self.image_manager.progress_updated.connect(self.update_progress)
        self.image_manager.image_loaded.connect(self.update_image_label)

        self.is_loading = False
        self.settings = QSettings("NFOEditor", "PhotoWall")

        self.init_ui()

        # 加载目录
        if folder_path:
            self.load_posters(folder_path)
        else:
            last_dir = self.settings.value("last_directory", "")
            if last_dir and os.path.exists(last_dir):
                self.folder_path = last_dir

    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("大锤 照片墙 v9.5.9")
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

    def calculate_grid_dimensions(self):
        """计算网格尺寸"""
        available_width = self.scroll.viewport().width()
        spacing = int(15 * self.dpi_scale)

        # 设置为8列
        columns = 8

        # 根据可用宽度计算海报宽度，考虑间距
        poster_width = (available_width - (columns + 1) * spacing) // columns
        # 按照电影海报的标准比例 2:3 计算高度
        poster_height = int(poster_width * 1.5)
        title_height = int(70 * self.dpi_scale)

        return columns, poster_width, poster_height, title_height

    def load_posters(self, folder_path):
        """优化后的海报加载函数"""
        if not folder_path or not os.path.exists(folder_path):
            return

        # 清理现有数据
        self.all_posters.clear()
        self._sort_keys.clear()
        self.poster_containers.clear()
        self.data_to_container_map.clear()

        # 显示进度条
        self.progress_bar.show()
        self.cancel_button.show()
        self.is_loading = True

        try:
            # 第一阶段: 扫描文件
            poster_nfo_pairs = []
            total_dirs = sum([len(dirs) for _, dirs, _ in os.walk(folder_path)])
            current_dir = 0

            # 扫描文件获取所有NFO和海报对
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

            # 第二阶段: 创建空UI框体
            total_items = len(poster_nfo_pairs)
            if total_items == 0:
                return

            # 计算网格尺寸并创建框体
            columns, poster_width, poster_height, title_height = (
                self.calculate_grid_dimensions()
            )
            self.create_empty_containers(
                total_items, poster_width, poster_height, title_height
            )

            # 第三阶段: 解析NFO并填充数据
            for index, (poster_file, nfo_file, root) in enumerate(poster_nfo_pairs):
                if not self.is_loading:
                    return

                try:
                    nfo_data = self.parse_nfo(nfo_file)
                    folder_name = os.path.basename(root)
                    self.all_posters.append((poster_file, folder_name, nfo_data))
                    self._update_sort_keys(nfo_data, len(self.all_posters) - 1)

                    # 更新对应容器的内容
                    self.update_container_content(index, poster_file, nfo_data)

                    # 更新进度
                    self.update_progress(index + 1, total_items, LoadStage.PREPARING)
                    QApplication.processEvents()

                except Exception as e:
                    print(f"处理NFO文件失败 {nfo_file}: {str(e)}")

            # 第四阶段: 加载图片
            self.load_poster_images()

        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载海报失败: {str(e)}")
            self.is_loading = False
            self.progress_bar.hide()
            self.cancel_button.hide()

    def _update_sort_keys(self, nfo_data, index):
        """更新排序键"""
        # 处理评分排序键 - 修复评分转换
        try:
            rating_str = nfo_data.get("rating", "0")
            # 确保空字符串或None时使用0
            rating_key = float(rating_str if rating_str and rating_str.strip() else "0")
        except (ValueError, TypeError):
            rating_key = 0.0

        if "评分" not in self._sort_keys:
            self._sort_keys["评分"] = []
        # 使用元组确保相同评分时保持稳定排序
        self._sort_keys["评分"].append(
            (rating_key, index, len(self._sort_keys.get("评分", [])))
        )

        # 其他排序键的处理...
        actors = nfo_data.get("actors", [])
        actors_key = actors[0] if actors else ""
        if "演员" not in self._sort_keys:
            self._sort_keys["演员"] = []
        self._sort_keys["演员"].append(
            (actors_key, index, len(self._sort_keys.get("演员", [])))
        )

        series_key = nfo_data.get("series") or ""
        if "系列" not in self._sort_keys:
            self._sort_keys["系列"] = []
        self._sort_keys["系列"].append(
            (series_key, index, len(self._sort_keys.get("系列", [])))
        )

        release = nfo_data.get("release", "")
        try:
            from datetime import datetime

            date_key = datetime.strptime(release, "%Y-%m-%d")
        except:
            date_key = datetime.min

        if "日期" not in self._sort_keys:
            self._sort_keys["日期"] = []
        self._sort_keys["日期"].append(
            (date_key, index, len(self._sort_keys.get("日期", [])))
        )

    def create_empty_containers(
        self, total_items, poster_width, poster_height, title_height
    ):
        """创建所有空的海报容器"""
        # 清除现有内容
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.poster_containers.clear()
        columns = 8  # 固定列数

        for index in range(total_items):
            try:
                # 计算位置
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

                # 创建标题标签
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

                # 创建信息标签
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

                # 组装布局
                title_layout.addWidget(title_label)
                title_layout.addWidget(info_label)
                container_layout.addWidget(poster_label)
                container_layout.addWidget(title_widget)

                # 添加到网格
                self.grid.addWidget(container, row, col)

                # 保存框体信息
                container_info = {
                    "container": container,
                    "poster_label": poster_label,
                    "title_label": title_label,
                    "info_label": info_label,
                    "row": row,
                    "col": col,
                }
                self.poster_containers.append(container_info)

            except Exception as e:
                print(f"创建容器失败，索引 {index}: {str(e)}")

    def update_container_content(self, index, poster_file, nfo_data):
        """更新容器内容 - 修复版本"""
        if index >= len(self.poster_containers):
            return

        try:
            container_info = self.poster_containers[index]
            if not container_info:
                print(f"容器信息不存在，索引 {index}")
                return

            if not all(
                key in container_info
                for key in ["title_label", "info_label", "poster_label"]
            ):
                print(f"容器信息不完整，索引 {index}")
                return

            # 更新标题
            title = nfo_data.get("title", "")
            container_info["title_label"].setText(title)

            # 更新信息
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

            container_info["info_label"].setText(" · ".join(info_parts))

            # 绑定事件处理
            poster_path = os.path.dirname(poster_file)
            if poster_path and os.path.exists(poster_path):
                container_info["poster_label"].mousePressEvent = (
                    lambda e, p=poster_path: self.play_video(p)
                )
                container_info["title_label"].parent().mousePressEvent = (
                    lambda e, p=poster_path: self.select_in_editor(p)
                )
            else:
                print(f"海报路径无效 {poster_file}")

            # 更新映射关系
            self.data_to_container_map[index] = container_info

        except Exception as e:
            print(f"更新容器内容失败，索引 {index}: {str(e)}")

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

    def load_poster_images(self):
        """加载海报图片 - 修复版本"""
        if not self.poster_containers or not self.all_posters:
            return

        image_paths_and_labels = []
        failed_images = []

        for index, (poster_file, _, _) in enumerate(self.all_posters):
            if index < len(self.poster_containers):
                if os.path.exists(poster_file):
                    container_info = self.poster_containers[index]
                    image_paths_and_labels.append(
                        (poster_file, container_info["poster_label"])
                    )
                else:
                    failed_images.append(poster_file)
                    # 如果图片不存在，设置一个默认的占位图
                    if index < len(self.poster_containers):
                        container_info = self.poster_containers[index]
                        container_info["poster_label"].setText("图片未找到")
                        container_info["poster_label"].setStyleSheet(
                            """
                            QLabel {
                                background-color: rgb(30, 30, 30);
                                color: rgb(150, 150, 150);
                                border-top-left-radius: 4px;
                                border-top-right-radius: 4px;
                            }
                        """
                        )

        if failed_images:
            print(f"以下图片未找到: {failed_images}")

        if image_paths_and_labels:
            self.progress_bar.setFormat("加载图片: %v/%m")
            self.image_manager.add_images(image_paths_and_labels)

    def sort_posters(self):
        """排序函数 - 完整修复版本"""
        if not self.sorting_group.checkedButton() or not self.all_posters:
            return

        sort_by = self.sorting_group.checkedButton().text()
        if sort_by not in self._sort_keys:
            QMessageBox.warning(self, "警告", f"未找到{sort_by}的排序数据")
            return

        if not self._sort_keys[sort_by]:
            QMessageBox.warning(self, "警告", f"无可用的{sort_by}数据进行排序")
            return

        # 禁用所有排序按钮
        for button in self.sorting_group.buttons():
            button.setEnabled(False)

        self.is_loading = True  # 设置加载状态
        self.progress_bar.show()
        self.cancel_button.show()

        try:
            # 使用预处理的排序键进行排序
            if sort_by in self._sort_keys and self._sort_keys[sort_by]:
                # 对索引进行排序，使用三元组确保稳定排序
                reverse = sort_by in ["评分", "日期"]
                sorted_tuples = sorted(
                    self._sort_keys[sort_by],
                    key=lambda x: (x[0] or "", x[2]),  # 使用原始顺序作为次要排序键
                    reverse=reverse,
                )

                # 获取排序后的索引
                sorted_indices = [t[1] for t in sorted_tuples]

                # 重新排序 all_posters
                self.all_posters = [self.all_posters[i] for i in sorted_indices]

                # 清除现有图片
                for container_info in self.poster_containers:
                    if container_info and "poster_label" in container_info:
                        container_info["poster_label"].clear()
                        container_info["poster_label"].setText("加载中...")

                # 重新构建排序键以保持一致性
                old_sort_keys = self._sort_keys.copy()
                self._sort_keys.clear()

                # 更新容器内容和重建排序键
                image_paths_and_labels = []
                for new_index, original_index in enumerate(sorted_indices):
                    try:
                        poster_file, _, nfo_data = self.all_posters[new_index]
                        self._update_sort_keys(nfo_data, new_index)  # 重建排序键

                        # 更新容器内容
                        self.update_container_content(new_index, poster_file, nfo_data)

                        # 准备重新加载图片
                        if os.path.exists(poster_file):
                            container_info = self.poster_containers[new_index]
                            image_paths_and_labels.append(
                                (poster_file, container_info["poster_label"])
                            )
                    except Exception as e:
                        print(f"更新容器内容失败，索引 {new_index}: {str(e)}")

                # 重置图片加载管理器
                if hasattr(self, "image_manager"):
                    self.image_manager.stop()
                    self.image_manager.executor.shutdown(wait=True)  # 等待所有任务完成
                self.image_manager = ImageLoadManager()
                self.image_manager.progress_updated.connect(self.update_progress)
                self.image_manager.image_loaded.connect(self.update_image_label)

                # 重新加载图片
                if image_paths_and_labels:
                    self.progress_bar.setFormat("重新加载图片: %v/%m")
                    self.image_manager.add_images(image_paths_and_labels)
                else:
                    # 如果没有图片需要加载，直接完成
                    self.is_loading = False
                    self.progress_bar.hide()
                    self.cancel_button.hide()

        except Exception as e:
            print(f"排序失败: {str(e)}")
            QMessageBox.warning(self, "警告", "排序过程中发生错误")
            self.is_loading = False
            self.progress_bar.hide()
            self.cancel_button.hide()
        finally:
            # 重新启用排序按钮
            for button in self.sorting_group.buttons():
                button.setEnabled(True)

    def apply_filter(self):
        """优化后的筛选函数"""
        field = self.field_combo.currentText()
        condition = self.condition_combo.currentText()
        filter_text = self.filter_entry.text().strip()

        try:
            visible_count = 0

            if not filter_text:
                # 显示所有内容
                for index, (poster_file, _, nfo_data) in enumerate(self.all_posters):
                    if index < len(self.poster_containers):
                        container_info = self.poster_containers[index]
                        container_info["container"].show()
                        visible_count += 1

                self.update_status(visible_count)
                return

            # 筛选逻辑
            for index, (poster_file, _, nfo_data) in enumerate(self.all_posters):
                if index >= len(self.poster_containers):
                    continue

                container_info = self.poster_containers[index]
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
                        match = False
                else:
                    if condition == "包含":
                        match = filter_text.lower() in value.lower()
                    else:  # 不包含
                        match = filter_text.lower() not in value.lower()

                # 根据匹配结果显示或隐藏容器
                if match:
                    container_info["container"].show()
                    visible_count += 1
                else:
                    container_info["container"].hide()

            self.update_status(visible_count)

        except Exception as e:
            print(f"筛选失败: {str(e)}")

    def on_field_changed(self, index):
        """字段改变处理"""
        self.condition_combo.clear()
        self.filter_entry.clear()
        if self.field_combo.currentText() == "评分":
            self.condition_combo.addItems(["大于", "小于"])
        else:
            self.condition_combo.addItems(["包含", "不包含"])

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

    def update_status(self, count, total=None, stage=None, cancelled=False):
        """更新状态栏 - 修复版本"""
        if cancelled:
            self.status_label.setText(f"已取消加载，已加载 {count}/{total} 个影片")
            return

        if total is None:
            self.status_label.setText(f"共显示 {count} 个影片")
        else:
            stage_text = ""
            if stage == LoadStage.SCANNING:
                stage_text = "扫描文件"
            elif stage == LoadStage.PREPARING:
                stage_text = "准备显示"
            elif stage == LoadStage.LOADING:
                stage_text = "加载图片"

            if stage_text:
                self.status_label.setText(f"{stage_text}: {count}/{total}")

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
            print(f"更新图片标签失败: {str(e)}")

    def on_resize(self, event):
        """窗口大小改变事件处理"""
        super().resizeEvent(event)
        self.resize_timer.start(150)  # 延迟处理以避免频繁重绘

    def handle_resize(self):
        """处理窗口大小改变 - 修复版本"""
        if not self.poster_containers:
            return

        try:
            # 重新计算网格尺寸
            columns, poster_width, poster_height, title_height = (
                self.calculate_grid_dimensions()
            )
            spacing = int(15 * self.dpi_scale)

            # 计算每行的宽度
            row_width = columns * poster_width + (columns - 1) * spacing

            # 更新网格布局的间距
            self.grid.setSpacing(spacing)

            # 更新所有容器的大小
            for index, container_info in enumerate(self.poster_containers):
                if not container_info:  # 增加空值检查
                    continue

                # 计算新的位置
                row = index // columns
                col = index % columns

                try:
                    # 更新容器大小
                    container = container_info["container"]
                    container.setFixedSize(poster_width, poster_height + title_height)

                    # 更新海报标签大小
                    poster_label = container_info["poster_label"]
                    poster_label.setFixedSize(poster_width, poster_height)

                    # 更新标题区域大小
                    title_widget = container_info["title_label"].parent()
                    title_widget.setFixedSize(poster_width, title_height)
                    title_widget.layout().setContentsMargins(
                        int(10 * self.dpi_scale),
                        int(5 * self.dpi_scale),
                        int(10 * self.dpi_scale),
                        int(5 * self.dpi_scale),
                    )

                    # 更新网格位置
                    self.grid.addWidget(container, row, col)

                except Exception as e:
                    print(f"更新容器 {index} 失败: {str(e)}")

            # 更新内容区域的大小
            total_items = len(self.poster_containers)
            total_rows = (total_items + columns - 1) // columns
            content_height = (
                total_rows * (poster_height + title_height) + (total_rows - 1) * spacing
            )
            self.content_widget.setMinimumSize(row_width, content_height)

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
            # 保存当前选择的目录
            self.settings.setValue("last_directory", folder_selected)
            self.load_posters(folder_selected)

    @lru_cache(maxsize=100)
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

    def closeEvent(self, event):
        """关闭窗口时清理资源 - 完整修复版本"""
        try:
            # 取消正在进行的加载和排序
            if self.is_loading:
                self.cancel_loading()

            # 停止图片加载
            if hasattr(self, "image_manager"):
                self.image_manager.stop()
                self.image_manager.executor.shutdown(wait=True)  # 确保完全停止

            # 清理容器引用
            self.poster_containers.clear()
            self.data_to_container_map.clear()

            # 清理排序数据
            self._sort_keys.clear()

            # 清理缓存
            self.parse_nfo.cache_clear()

            # 保存设置
            if self.folder_path and os.path.exists(self.folder_path):
                self.settings.setValue("last_directory", self.folder_path)

        except Exception as e:
            print(f"清理资源失败: {str(e)}")
        finally:
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
