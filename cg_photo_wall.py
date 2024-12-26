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
    QFrame,
    QLabel,
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
)
from PyQt5.QtCore import Qt, pyqtSignal, QSize, QTimer
from PyQt5.QtGui import QPixmap, QIcon, QGuiApplication


class ImageLoader(Thread):
    """图片加载线程"""

    def __init__(self, queue, callback):
        super().__init__(daemon=True)
        self.queue = queue
        self.callback = callback
        self.running = True

    def run(self):
        while self.running:
            try:
                path, label, size = self.queue.get()
                if path is None:
                    break

                pixmap = QPixmap(path)
                if not pixmap.isNull():
                    scaled_pixmap = pixmap.scaled(
                        size, Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                    self.callback(label, scaled_pixmap)

            except Exception as e:
                print(f"加载图片失败 {path}: {str(e)}")
            finally:
                self.queue.task_done()

    def stop(self):
        self.running = False
        self.queue.put((None, None, None))


class PhotoWallDialog(QDialog):
    update_image = pyqtSignal(QLabel, QPixmap)

    def __init__(self, folder_path=None, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.folder_path = folder_path
        self.all_posters = []
        self.image_queue = Queue()
        self.resize_timer = QTimer()
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.handle_resize)

        # 获取屏幕DPI信息
        screen = QGuiApplication.primaryScreen()
        self.dpi_scale = screen.logicalDotsPerInch() / 96.0

        # 创建图片加载线程
        self.update_image.connect(self.update_image_label)
        self.image_loader = ImageLoader(
            self.image_queue,
            lambda label, pixmap: self.update_image.emit(label, pixmap),
        )
        self.image_loader.start()

        self.init_ui()

        if folder_path:
            self.load_posters(folder_path)

    def update_image_label(self, label, pixmap):
        """在主线程中更新图片标签"""
        if label and not label.isHidden():
            label.setPixmap(pixmap)

    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("大锤 NFO照片墙 v9.5.4")

        # 获取主屏幕大小
        screen = QDesktopWidget().availableGeometry()
        self.resize(int(screen.width() * 0.4), int(screen.height() * 0.6))

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
            int(10 * self.dpi_scale),
            int(10 * self.dpi_scale),
            int(10 * self.dpi_scale),
            int(10 * self.dpi_scale),
        )

        # 工具栏
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setSpacing(int(10 * self.dpi_scale))

        # 选择目录按钮
        select_button = QPushButton("选择目录")
        select_button.setMinimumHeight(int(30 * self.dpi_scale))
        select_button.clicked.connect(self.select_folder)
        toolbar_layout.addWidget(select_button)

        # 排序和筛选面板
        filter_frame = self.create_filter_panel()
        toolbar_layout.addWidget(filter_frame)

        layout.addWidget(toolbar)

        # 状态栏
        self.status_label = QLabel()
        self.status_label.setStyleSheet(
            f"""
            QLabel {{
                color: rgb(200, 200, 200);
                font-size: {int(12 * self.dpi_scale)}px;
                padding: {int(5 * self.dpi_scale)}px;
                background-color: rgb(30, 30, 30);
            }}
        """
        )
        layout.addWidget(self.status_label)

        # 滚动区域
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # 内容容器
        self.content_widget = QWidget()
        self.grid = QGridLayout(self.content_widget)
        self.grid.setSpacing(int(10 * self.dpi_scale))
        self.scroll.setWidget(self.content_widget)

        layout.addWidget(self.scroll)

        # 设置窗口图标
        try:
            icon_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "chuizi.ico"
            )
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except Exception:
            pass

        # 连接重绘事件
        self.resizeEvent = self.on_resize

    def create_filter_panel(self):
        """创建过滤面板"""
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(
            int(1 * self.dpi_scale),
            int(1 * self.dpi_scale),
            int(1 * self.dpi_scale),
            int(1 * self.dpi_scale),
        )
        layout.setSpacing(int(10 * self.dpi_scale))

        # 排序选项
        sort_label = QLabel("排序 (Sort by):")
        sort_label.setStyleSheet(f"font-size: {int(12 * self.dpi_scale)}px;")
        layout.addWidget(sort_label)

        self.sorting_group = QButtonGroup(self)
        sort_options = [
            "文件名 (Filename)",
            "演员 (Actors)",
            "系列 (Series)",
            "评分 (Rating)",
        ]
        for text in sort_options:
            radio = QRadioButton(text)
            radio.setStyleSheet(f"font-size: {int(12 * self.dpi_scale)}px;")
            self.sorting_group.addButton(radio)
            layout.addWidget(radio)

        # 筛选选项
        self.field_combo = QComboBox()
        self.field_combo.addItems(["标题", "标签", "演员", "系列", "评分"])
        self.field_combo.setStyleSheet(f"font-size: {int(12 * self.dpi_scale)}px;")
        layout.addWidget(self.field_combo)

        self.condition_combo = QComboBox()
        self.condition_combo.setStyleSheet(f"font-size: {int(12 * self.dpi_scale)}px;")
        layout.addWidget(self.condition_combo)

        self.filter_entry = QLineEdit()
        self.filter_entry.setFixedWidth(int(100 * self.dpi_scale))
        self.filter_entry.setStyleSheet(f"font-size: {int(12 * self.dpi_scale)}px;")
        layout.addWidget(self.filter_entry)

        # 筛选按钮
        filter_button = QPushButton("筛选")
        filter_button.setFixedWidth(int(45 * self.dpi_scale))
        filter_button.setStyleSheet(f"font-size: {int(12 * self.dpi_scale)}px;")
        filter_button.clicked.connect(self.apply_filter)
        layout.addWidget(filter_button)

        # 连接信号
        self.field_combo.currentIndexChanged.connect(self.on_field_changed)
        self.sorting_group.buttonClicked.connect(self.sort_posters)

        # 设置初始条件
        self.on_field_changed(0)
        if self.sorting_group.buttons():
            self.sorting_group.buttons()[0].setChecked(True)

        return frame

    def on_resize(self, event):
        """窗口大小改变事件处理"""
        super().resizeEvent(event)
        # 使用计时器延迟处理以避免频繁重绘
        self.resize_timer.start(150)

    def handle_resize(self):
        """处理窗口大小改变"""
        if hasattr(self, "all_posters"):
            self.display_current_page()

    def calculate_grid_dimensions(self):
        """计算网格尺寸"""
        available_width = self.scroll.viewport().width()
        min_poster_width = int(180 * self.dpi_scale)
        spacing = int(10 * self.dpi_scale)

        # 计算每行可以容纳的海报数量
        columns = max(1, (available_width - spacing) // (min_poster_width + spacing))

        # 计算实际海报宽度（均匀分布）
        poster_width = (available_width - (columns + 1) * spacing) // columns
        poster_height = int(poster_width * 1.5)  # 保持宽高比
        title_height = int(60 * self.dpi_scale)

        return columns, poster_width, poster_height, title_height

    def display_current_page(self):
        """显示所有海报"""
        # 清除现有内容
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 计算网格尺寸
        max_cols, poster_width, poster_height, title_height = (
            self.calculate_grid_dimensions()
        )

        row = 0
        col = 0

        for poster_file, folder_name, nfo_data in self.all_posters:
            try:
                # 创建容器
                container = QFrame()
                container.setFixedSize(poster_width, poster_height + title_height)
                container.setStyleSheet(
                    f"""
                    QFrame {{
                        background-color: rgb(20, 20, 20);
                        border-radius: {int(3 * self.dpi_scale)}px;
                    }}
                    QFrame:hover {{
                        background-color: rgb(40, 40, 40);
                    }}
                """
                )

                container_layout = QVBoxLayout(container)
                container_layout.setSpacing(0)
                container_layout.setContentsMargins(0, 0, 0, 0)

                # 海报图片
                poster_label = QLabel()
                poster_label.setAlignment(Qt.AlignCenter)
                poster_label.setFixedSize(poster_width, poster_height)
                poster_label.setCursor(Qt.PointingHandCursor)

                # 添加至加载队列
                self.image_queue.put(
                    (poster_file, poster_label, QSize(poster_width, poster_height))
                )

                # 标题区域
                title_widget = QWidget()
                title_widget.setFixedSize(poster_width, title_height)
                title_widget.setCursor(Qt.PointingHandCursor)

                title_layout = QVBoxLayout(title_widget)
                title_layout.setSpacing(int(2 * self.dpi_scale))
                title_layout.setContentsMargins(
                    int(5 * self.dpi_scale),
                    int(2 * self.dpi_scale),
                    int(5 * self.dpi_scale),
                    int(2 * self.dpi_scale),
                )

                # 标题标签
                title = nfo_data.get("title", "")
                title_label = QLabel(title)
                title_label.setStyleSheet(
                    f"""
                    QLabel {{
                        color: rgb(200, 200, 200);
                        font-size: {int(16 * self.dpi_scale)}px;
                    }}
                """
                )
                title_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
                title_label.setWordWrap(True)
                title_label.setFixedHeight(int(title_height * 0.7))

                # 年份和评分
                info_parts = []
                if year := nfo_data.get("year"):
                    info_parts.append(year)
                if rating := nfo_data.get("rating"):
                    info_parts.append(f"★{float(rating):.1f}")

                info_label = QLabel(" · ".join(info_parts))
                info_label.setStyleSheet(
                    f"""
                    QLabel {{
                        color: rgb(140, 140, 140);
                        font-size: {int(18 * self.dpi_scale)}px;
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

                self.grid.addWidget(container, row, col)

                # 更新位置
                col += 1
                if col >= max_cols:
                    col = 0
                    row += 1

            except Exception as e:
                print(f"显示海报失败 {poster_file}: {str(e)}")

    def select_in_editor(self, folder_path):
        """在编辑器中选择相应文件夹"""
        try:
            if self.parent_window:
                # 如果是从编辑器启动的，直接调用父窗口方法
                self.parent_window.select_folder_in_tree(folder_path)
                self.parent_window.activateWindow()
                self.parent_window.raise_()
            else:
                # 获取当前脚本所在目录
                current_dir = os.path.dirname(os.path.abspath(__file__))
                editor_path = os.path.join(current_dir, "NFO.Editor.Qt5.py")

                if not os.path.exists(editor_path):
                    QMessageBox.critical(
                        self, "错误", "找不到编辑器程序NFO.Editor.Qt5.py"
                    )
                    return

                # 构建命令行参数
                args = [sys.executable]

                # 如果是Windows系统且存在pythonw.exe，使用它来避免显示控制台窗口
                if sys.platform == "win32":
                    pythonw = os.path.join(
                        os.path.dirname(sys.executable), "pythonw.exe"
                    )
                    if os.path.exists(pythonw):
                        args = [pythonw]

                # 添加其他参数
                args.extend(
                    [
                        editor_path,
                        "--base-path",
                        os.path.dirname(folder_path),
                        "--select-folder",
                        folder_path,
                    ]
                )

                # 启动编辑器进程
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

    def select_folder(self):
        """选择文件夹"""
        folder = QFileDialog.getExistingDirectory(self, "选择NFO文件夹")
        if folder:
            self.folder_path = folder
            self.load_posters(folder)

    def load_posters(self, folder_path):
        """加载海报"""
        if not folder_path or not os.path.exists(folder_path):
            return

        self.all_posters.clear()

        # 遍历文件夹
        for root, _, files in os.walk(folder_path):
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
                try:
                    nfo_data = self.parse_nfo(nfo_file)
                    folder_name = os.path.basename(root)
                    self.all_posters.append((poster_file, folder_name, nfo_data))
                except Exception as e:
                    print(f"处理文件失败 {poster_file}: {str(e)}")

        # 显示海报
        self.display_current_page()
        self.update_status(len(self.all_posters))

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
            }
        except Exception as e:
            print(f"解析NFO文件失败 {nfo_path}: {str(e)}")
            return {}

    def sort_posters(self, button=None):
        """排序海报"""
        if not self.sorting_group.checkedButton():
            return

        sort_by = self.sorting_group.checkedButton().text()

        def get_sort_key(poster_info):
            _, _, nfo_data = poster_info

            if nfo_data is None:
                return ""

            if "演员" in sort_by:
                return ", ".join(sorted(nfo_data.get("actors", [])))
            elif "系列" in sort_by:
                return (nfo_data.get("series") or "").lower()
            elif "评分" in sort_by:
                try:
                    return float(nfo_data.get("rating", 0))
                except (ValueError, TypeError):
                    return 0
            else:  # 文件名
                return (nfo_data.get("title") or "").lower()

        # 排序
        self.all_posters.sort(key=get_sort_key)

        # 评分倒序
        if "评分" in sort_by:
            self.all_posters.reverse()

        self.display_current_page()

    def on_field_changed(self, index):
        """字段改变处理"""
        self.condition_combo.clear()
        self.filter_entry.clear()
        if self.field_combo.currentText() == "评分":
            self.condition_combo.addItems(["大于", "小于"])
        else:
            self.condition_combo.addItems(["包含", "不包含"])

    def update_status(self, count, total=None):
        """更新状态栏"""
        if total is None:
            self.status_label.setText(f"共加载 {count} 个影片")
        else:
            self.status_label.setText(f"筛选结果: {count} / 总计 {total} 个影片")

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

    def closeEvent(self, event):
        """关闭窗口时清理资源"""
        if hasattr(self, "image_loader") and self.image_loader:
            self.image_loader.stop()
            self.image_loader.join()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)

    # 解析命令行参数
    folder_path = None
    if len(sys.argv) > 1:
        folder_path = sys.argv[1]

    window = PhotoWallDialog(folder_path)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
