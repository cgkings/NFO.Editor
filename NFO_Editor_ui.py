import sys
import os
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QVBoxLayout,
    QWidget,
    QPushButton,
    QLabel,
    QFrame,
    QTreeWidget,
    QRadioButton,
    QButtonGroup,
    QComboBox,
    QLineEdit,
    QTextEdit,
    QCheckBox,
    QGridLayout,
    QSizePolicy,
)
from PyQt5.QtCore import Qt, QSettings
from PyQt5.QtGui import QIcon, QCursor, QPixmap, QFont


def get_resource_path(relative_path):
    if getattr(sys, "frozen", False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


class NFOEditorQt(QMainWindow):
    def __init__(self):
        super().__init__()
        self.current_file_path = None
        self.folder_path = None
        self.current_target_path = None
        self.fields_entries = {}

        self.screen_dpi = self.screen().logicalDotsPerInch()
        self.scale_factor = self.screen_dpi / 96.0

        self.setWindowTitle("大锤 NFO Editor v9.7.6")
        self.resize(1280, 800)

        self.status_bar = self.statusBar()
        self.status_bar.showMessage("就绪")

        try:
            icon_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "chuizi.ico"
            )
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except Exception:
            pass

        self._update_dpi_scale()
        self.setup_ui()
        self.center_window()

    def _update_dpi_scale(self):
        """更新DPI缩放相关设置"""
        base_font_size = 10
        self.default_font = self.font()
        self.default_font.setPointSize(int(base_font_size * self.scale_factor))
        self.setFont(self.default_font)
        self._update_style_sheet()

    def _update_style_sheet(self):
        """更新样式表以适应DPI"""
        self.setStyleSheet(
            f"""
            QMainWindow {{
                font-size: {int(10 * self.scale_factor)}pt;
            }}
            QPushButton {{
                font-size: {int(10 * self.scale_factor)}pt;
                padding: {int(4 * self.scale_factor)}px;
                margin: {int(2 * self.scale_factor)}px;
            }}
            QLabel {{
                font-size: {int(10 * self.scale_factor)}pt;
                padding: {int(2 * self.scale_factor)}px;
            }}
            QTreeWidget {{
                font-size: {int(10 * self.scale_factor)}pt;
            }}
            QComboBox {{
                font-size: {int(10 * self.scale_factor)}pt;
                padding: {int(4 * self.scale_factor)}px;
            }}
            QLineEdit, QTextEdit {{
                font-size: {int(10 * self.scale_factor)}pt;
                padding: {int(4 * self.scale_factor)}px;
            }}
            QCheckBox, QRadioButton {{
                font-size: {int(10 * self.scale_factor)}pt;
                spacing: {int(5 * self.scale_factor)}px;
            }}
        """
        )

    def center_window(self):
        """确保窗口在正确的显示器上居中"""
        screen = QApplication.screenAt(QCursor.pos())
        if not screen:
            screen = QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry()
        window_geometry = self.frameGeometry()
        window_geometry.moveCenter(screen_geometry.center())
        self.move(window_geometry.topLeft())

    def closeEvent(self, event):
        self.save_window_state()
        super().closeEvent(event)

    def save_window_state(self):
        settings = QSettings("NFOEditor", "WindowState")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("windowState", self.saveState())

    def restore_window_state(self):
        settings = QSettings("NFOEditor", "WindowState")
        geometry = settings.value("geometry")
        state = settings.value("windowState")
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)
        self.ensure_visible_on_screen()

    def ensure_visible_on_screen(self):
        frame = self.frameGeometry()
        visible = any(
            screen.availableGeometry().intersects(frame)
            for screen in QApplication.screens()
        )
        if not visible:
            screen = QApplication.primaryScreen()
            center_point = screen.availableGeometry().center()
            frame.moveCenter(center_point)
            self.move(frame.topLeft())

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_grid = QGridLayout(central_widget)
        main_grid.setSpacing(int(1 * self.scale_factor))
        main_grid.setContentsMargins(
            int(1 * self.scale_factor),
            int(1 * self.scale_factor),
            int(1 * self.scale_factor),
            int(1 * self.scale_factor),
        )

        top_frame = self.create_top_panel()
        main_grid.addWidget(top_frame, 0, 0, 1, 3)

        sorting_frame = self.create_sorting_panel()
        main_grid.addWidget(sorting_frame, 1, 0, 1, 3)

        file_tree_frame = self.create_file_tree()
        main_grid.addWidget(file_tree_frame, 2, 0)

        target_tree_frame = self.create_target_tree()
        main_grid.addWidget(target_tree_frame, 2, 1)

        editor_frame = self.create_editor_panel()
        main_grid.addWidget(editor_frame, 2, 2)

        main_grid.setColumnStretch(0, 3)
        main_grid.setColumnStretch(1, 2)
        main_grid.setColumnStretch(2, 3)

        main_grid.setRowStretch(0, 0)
        main_grid.setRowStretch(1, 0)
        main_grid.setRowStretch(2, 1)

    def create_top_panel(self):
        frame = QFrame()
        grid = QGridLayout(frame)
        grid.setSpacing(int(1 * self.scale_factor))
        grid.setContentsMargins(
            int(10 * self.scale_factor),
            int(1 * self.scale_factor),
            int(10 * self.scale_factor),
            int(1 * self.scale_factor),
        )

        button_height = int(40 * self.scale_factor)
        buttons_info = [
            ("选择nfo目录",  "选择目录以加载NFO文件",             int(150 * self.scale_factor)),
            ("选择整理目录", "选择后方显示整理目录列表",           int(150 * self.scale_factor)),
            ("🖊",           "打开选中的NFO文件",                  int(40  * self.scale_factor)),
            ("📁",           "打开选中的文件夹",                   int(40  * self.scale_factor)),
            ("⏯",           "播放选中的视频文件",                  int(40  * self.scale_factor)),
            ("🔗",           "统一演员名并重命名文件夹",           int(40  * self.scale_factor)),
            ("🔁",           "刷新文件列表,快捷键F5",              int(40  * self.scale_factor)),
            ("🖼",           "打开海报照片墙",                     int(40  * self.scale_factor)),
            ("🔜",           "移动nfo所在文件夹到目标目录,快捷键方向键→", int(40 * self.scale_factor)),
            ("⚙️",           "打开设置",                          int(40  * self.scale_factor)),
        ]

        for col, (text, tooltip, width) in enumerate(buttons_info):
            btn = QPushButton(text)
            btn.setFixedSize(width, button_height)
            btn.setToolTip(tooltip)
            if text == "⚙️":
                btn.setObjectName("settings_button")
            grid.addWidget(btn, 0, col)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        grid.addWidget(spacer, 0, len(buttons_info))

        self.show_images_checkbox = QCheckBox("显示图片")
        self.show_images_checkbox.setToolTip("显示或隐藏图片")
        grid.addWidget(self.show_images_checkbox, 0, len(buttons_info) + 1, Qt.AlignRight)

        return frame

    def create_sorting_panel(self):
        frame = QFrame()
        grid = QGridLayout(frame)
        grid.setContentsMargins(
            int(1 * self.scale_factor),
            int(1 * self.scale_factor),
            int(1 * self.scale_factor),
            int(1 * self.scale_factor),
        )
        grid.setSpacing(int(2 * self.scale_factor))

        sort_label = QLabel("排序 (Sort by):")
        grid.addWidget(sort_label, 0, 0)

        self.sorting_group = QButtonGroup(self)
        sort_options = [
            "日期 (Release Date)",
            "演员 (Actors)",
            "系列 (Series)",
            "评分 (Rating)",
        ]
        for col, text in enumerate(sort_options, 1):
            radio = QRadioButton(text)
            self.sorting_group.addButton(radio)
            grid.addWidget(radio, 0, col)

        self.field_combo = QComboBox()
        self.field_combo.setFixedWidth(int(65 * self.scale_factor))
        self.field_combo.addItems(["标题", "标签", "演员", "系列", "评分"])
        grid.addWidget(self.field_combo, 0, len(sort_options) + 1)

        self.condition_combo = QComboBox()
        self.condition_combo.setFixedWidth(int(65 * self.scale_factor))
        grid.addWidget(self.condition_combo, 0, len(sort_options) + 2)

        self.filter_entry = QLineEdit()
        self.filter_entry.setFixedWidth(int(100 * self.scale_factor))
        grid.addWidget(self.filter_entry, 0, len(sort_options) + 3)

        def on_field_changed(index):
            self.condition_combo.clear()
            self.filter_entry.clear()
            if self.field_combo.currentText() == "评分":
                self.condition_combo.addItems(["大于", "小于"])
            else:
                self.condition_combo.addItems(["包含", "不包含"])

        self.field_combo.currentIndexChanged.connect(on_field_changed)
        # 注意：此处不再重复连接 condition_combo 的 clear()，
        # on_field_changed 已经包含了 filter_entry.clear()，避免冗余。

        on_field_changed(0)

        filter_button = QPushButton("筛选")
        filter_button.setFixedSize(
            int(45 * self.scale_factor), int(30 * self.scale_factor)
        )
        filter_button.setToolTip("根据条件筛选文件列表")
        grid.addWidget(filter_button, 0, len(sort_options) + 4)

        grid.setColumnStretch(len(sort_options) + 5, 1)
        return frame

    def create_file_tree(self):
        frame = QFrame()
        grid = QGridLayout(frame)
        grid.setContentsMargins(
            int(1 * self.scale_factor),
            int(1 * self.scale_factor),
            int(1 * self.scale_factor),
            int(1 * self.scale_factor),
        )
        grid.setSpacing(0)

        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["一级目录", "二级目录", "NFO文件"])
        self.file_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.file_tree.setColumnWidth(0, int(160 * self.scale_factor))
        self.file_tree.setColumnWidth(1, int(160 * self.scale_factor))
        self.file_tree.setColumnHidden(2, True)
        grid.addWidget(self.file_tree, 0, 0)

        return frame

    def create_target_tree(self):
        frame = QFrame()
        grid = QGridLayout(frame)
        grid.setContentsMargins(
            int(1 * self.scale_factor),
            int(1 * self.scale_factor),
            int(1 * self.scale_factor),
            int(1 * self.scale_factor),
        )
        grid.setSpacing(0)

        self.sorted_tree = QTreeWidget()
        self.sorted_tree.setHeaderLabels(["目标文件夹"])
        grid.addWidget(self.sorted_tree, 0, 0)

        return frame

    def create_editor_panel(self):
        content = QWidget()
        grid = QGridLayout(content)

        left_margin = int(10 * self.scale_factor)
        right_margin = int(20 * self.scale_factor)
        grid.setContentsMargins(left_margin, int(5 * self.scale_factor), right_margin, int(5 * self.scale_factor))
        grid.setSpacing(int(2 * self.scale_factor))

        image_frame = self.create_image_preview()
        grid.addWidget(image_frame, 0, 0)

        fields_frame = self.create_fields_section()
        grid.addWidget(fields_frame, 1, 0)

        operations_frame = self.create_operations_section()
        grid.addWidget(operations_frame, 2, 0)

        grid.setRowStretch(0, 2)
        grid.setRowStretch(1, 4)
        grid.setRowStretch(2, 1)

        return content

    def create_image_preview(self):
        frame = QFrame()
        grid = QGridLayout(frame)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(int(2 * self.scale_factor))

        image_label = QLabel("图片:")
        image_label.setFixedWidth(int(50 * self.scale_factor))
        image_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(image_label, 0, 0)

        image_container = QWidget()
        image_layout = QHBoxLayout(image_container)
        image_layout.setContentsMargins(int(2 * self.scale_factor), 0, 0, 0)
        image_layout.setSpacing(int(10 * self.scale_factor))

        sizes = self.calculate_dynamic_sizes()

        # ---- Poster ----
        poster_container = QWidget()
        poster_container_layout = QVBoxLayout(poster_container)
        poster_container_layout.setContentsMargins(0, 0, 0, 0)
        poster_container_layout.setSpacing(int(2 * self.scale_factor))

        poster_frame = QFrame()
        poster_frame.setFixedSize(sizes['poster_width'], sizes['poster_height'])
        poster_frame.setStyleSheet("border: 1px solid #A0A0A0")
        poster_layout = QGridLayout(poster_frame)
        poster_layout.setContentsMargins(0, 0, 0, 0)

        self.poster_label = QLabel("封面图 (poster)")
        self.poster_label.setAlignment(Qt.AlignCenter)
        self.poster_label.setFixedSize(sizes['poster_width'], sizes['poster_height'])
        poster_layout.addWidget(self.poster_label, 0, 0)

        self.poster_resolution_label = QLabel("分辨率: 未知")
        self.poster_resolution_label.setAlignment(Qt.AlignCenter)
        self.poster_resolution_label.setStyleSheet("color: gray; font-size: 9pt;")
        self.poster_resolution_label.setFixedHeight(int(16 * self.scale_factor))

        poster_container_layout.addWidget(poster_frame)
        poster_container_layout.addWidget(self.poster_resolution_label)

        # ---- Thumb ----
        thumb_container = QWidget()
        thumb_container_layout = QVBoxLayout(thumb_container)
        thumb_container_layout.setContentsMargins(0, 0, 0, 0)
        thumb_container_layout.setSpacing(int(2 * self.scale_factor))

        thumb_frame = QFrame()
        thumb_frame.setFixedSize(sizes['thumb_width'], sizes['thumb_height'])
        thumb_frame.setStyleSheet("border: 1px solid #A0A0A0")
        thumb_layout = QGridLayout(thumb_frame)
        thumb_layout.setContentsMargins(0, 0, 0, 0)

        self.thumb_label = QLabel("缩略图 (thumb)")
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setFixedSize(sizes['thumb_width'], sizes['thumb_height'])
        thumb_layout.addWidget(self.thumb_label, 0, 0)

        self.thumb_resolution_label = QLabel("分辨率: 未知")
        self.thumb_resolution_label.setAlignment(Qt.AlignCenter)
        self.thumb_resolution_label.setStyleSheet("color: gray; font-size: 9pt;")
        self.thumb_resolution_label.setFixedHeight(int(16 * self.scale_factor))

        thumb_container_layout.addWidget(thumb_frame)
        thumb_container_layout.addWidget(self.thumb_resolution_label)

        image_layout.addWidget(poster_container)
        image_layout.addWidget(thumb_container)
        image_layout.addStretch()

        grid.addWidget(image_container, 0, 1)

        # Bug fix: poster 应使用 "poster" 关键字，thumb 使用 "fanart"
        # 原代码两处都传 "fanart"，导致点击封面图时找不到 poster.jpg
        self.poster_label.mousePressEvent = lambda e: self.open_image_and_crop("poster")
        self.thumb_label.mousePressEvent = lambda e: self.open_image_and_crop("fanart")

        return frame

    def create_fields_section(self):
        frame = QFrame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(int(2 * self.scale_factor))

        fields = {
            "num":    ("番号", 2),
            "title":  ("标题", 2.5),
            "plot":   ("简介", 3),
            "tags":   ("标签", 2.5),
            "actors": ("演员", 1.5),
            "series": ("系列", 1.5),
            "rating": ("评分", 1.5),
        }

        sizes = self.calculate_dynamic_sizes()

        for field, (label_text, height) in fields.items():
            field_frame = QFrame()
            field_layout = QHBoxLayout(field_frame)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.setSpacing(int(5 * self.scale_factor))

            label = QLabel(f"{label_text}:")
            label.setFixedWidth(int(50 * self.scale_factor))
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            field_layout.addWidget(label)

            if field == "num":
                container = QFrame()
                container_layout = QHBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
                container_layout.setSpacing(int(5 * self.scale_factor))

                num_frame = QFrame()
                num_layout = QHBoxLayout(num_frame)
                num_layout.setContentsMargins(0, 0, 0, 0)

                entry = QLabel()
                entry.setCursor(Qt.PointingHandCursor)
                entry.setStyleSheet("color: blue; text-decoration: underline;")
                entry.setMinimumWidth(int(sizes['text_width'] * 0.6))
                entry.setMaximumWidth(int(sizes['text_max_width'] * 0.6))
                num_layout.addWidget(entry)

                self.copy_num_button = QPushButton("📋")
                self.copy_num_button.setFixedSize(int(30 * self.scale_factor), int(30 * self.scale_factor))
                self.copy_num_button.setToolTip("复制番号")
                num_layout.addWidget(self.copy_num_button)

                self.play_trailer_button = QPushButton("🎬")
                self.play_trailer_button.setFixedSize(int(30 * self.scale_factor), int(30 * self.scale_factor))
                self.play_trailer_button.setToolTip("播放预告片")
                num_layout.addWidget(self.play_trailer_button)

                year_frame = QFrame()
                year_layout = QHBoxLayout(year_frame)
                year_layout.setContentsMargins(0, 0, 0, 0)
                year_label = QLabel("发行日期：")
                year_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                year_layout.addWidget(year_label)
                self.release_label = QLabel()
                self.release_label.setObjectName("release_label")
                year_layout.addWidget(self.release_label)

                container_layout.addWidget(num_frame)
                container_layout.addWidget(year_frame)
                field_layout.addWidget(container)

                entry.setFixedHeight(int(22 * self.scale_factor))
                self.fields_entries[field] = entry
            else:
                entry = QTextEdit()
                entry.setFixedHeight(int(22 * self.scale_factor * height))
                entry.setMinimumWidth(sizes['text_width'])
                entry.setMaximumWidth(sizes['text_max_width'])
                entry.setTabChangesFocus(True)
                field_layout.addWidget(entry)
                self.fields_entries[field] = entry

            field_layout.addStretch(1)
            layout.addWidget(field_frame)

        return frame

    def create_operations_section(self):
        frame = QFrame()
        grid = QGridLayout(frame)
        grid.setContentsMargins(
            int(15 * self.scale_factor), int(20 * self.scale_factor), 0, 0
        )
        grid.setSpacing(int(15 * self.scale_factor))

        buttons = [
            ("保存更改 (Save Changes)", int(205 * self.scale_factor)),
            ("批量填充 (Batch Filling)", int(205 * self.scale_factor)),
            ("批量新增 (Batch Add)",    int(205 * self.scale_factor)),
        ]

        button_frame = QFrame()
        button_layout = QHBoxLayout(button_frame)
        button_layout.setSpacing(int(15 * self.scale_factor))
        button_layout.setContentsMargins(0, 0, 0, 0)

        for text, width in buttons:
            btn = QPushButton(text)
            btn.setFixedSize(width, int(50 * self.scale_factor))
            button_layout.addWidget(btn)

        button_layout.addStretch()
        grid.addWidget(button_frame, 0, 0)

        self.save_time_label = QLabel()
        self.save_time_label.setMinimumHeight(int(30 * self.scale_factor))
        grid.addWidget(self.save_time_label, 1, 0)

        return frame

    def calculate_dynamic_sizes(self):
        """根据窗口大小计算动态尺寸。
        
        Bug fix: 原实现使用 max(1.0, ...) 导致窗口缩小时图片框不跟着缩，
        会溢出布局。现改为允许 scale < 1.0 以正确响应窗口缩小。
        """
        available_width = self.width() - 600
        available_height = self.height() - 200

        base_width = 800
        base_height = 600

        # 允许放大也允许缩小（去掉原来的 max(1.0, ...)）
        width_scale = available_width / base_width if available_width > 0 else 1.0
        height_scale = available_height / base_height if available_height > 0 else 1.0
        scale = max(0.5, min(width_scale, height_scale))  # 最小保留 0.5 防止极端情况

        return {
            'poster_width':   int(180 * self.scale_factor * scale),
            'poster_height':  int(270 * self.scale_factor * scale),
            'thumb_width':    int(402 * self.scale_factor * scale),
            'thumb_height':   int(270 * self.scale_factor * scale),
            'text_width':     int(590 * self.scale_factor * scale),
            'text_max_width': int(800 * self.scale_factor * scale),
        }

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'poster_label') and hasattr(self, 'thumb_label'):
            self.update_layout_sizes()

    def update_layout_sizes(self):
        """窗口大小改变时同步更新图片框和文本框尺寸"""
        sizes = self.calculate_dynamic_sizes()

        if hasattr(self, 'poster_label'):
            poster_frame = self.poster_label.parent()
            poster_frame.setFixedSize(sizes['poster_width'], sizes['poster_height'])
            self.poster_label.setFixedSize(sizes['poster_width'], sizes['poster_height'])

        if hasattr(self, 'thumb_label'):
            thumb_frame = self.thumb_label.parent()
            thumb_frame.setFixedSize(sizes['thumb_width'], sizes['thumb_height'])
            self.thumb_label.setFixedSize(sizes['thumb_width'], sizes['thumb_height'])

        for field, widget in self.fields_entries.items():
            if field != "num":
                widget.setMinimumWidth(sizes['text_width'])
                widget.setMaximumWidth(sizes['text_max_width'])
            else:
                widget.setMinimumWidth(int(sizes['text_width'] * 0.6))
                widget.setMaximumWidth(int(sizes['text_max_width'] * 0.6))

        if self.show_images_checkbox.isChecked() and self.current_file_path:
            self.display_image()


if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = NFOEditorQt()
    window.restore_window_state()
    window.show()

    sys.exit(app.exec_())
