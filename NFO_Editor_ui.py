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
from PyQt5.QtCore import Qt, QSize, QSettings
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

        # 程序启动时获取一次DPI即可
        self.screen_dpi = self.screen().logicalDotsPerInch()
        self.scale_factor = self.screen_dpi / 96.0

        self.setWindowTitle("大锤 NFO Editor v9.7.5")
        self.resize(1280, 800)

        # 初始化状态栏
        self.status_bar = self.statusBar()
        self.status_bar.showMessage("就绪")  # 设置默认状态信息

        try:
            icon_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "chuizi.ico"
            )
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except Exception:
            pass

        # 初始化UI时应用DPI缩放
        self._update_dpi_scale()
        self.setup_ui()
        self.center_window()

    def _update_dpi_scale(self):
        """更新DPI缩放相关的设置"""
        # 更新字体大小
        base_font_size = 10
        self.default_font = self.font()
        self.default_font.setPointSize(int(base_font_size * self.scale_factor))
        self.setFont(self.default_font)

        # 更新样式表
        self._update_style_sheet()

    def _update_style_sheet(self):
        """更新样式表以适应新的DPI"""
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
        """确保窗口在正确的显示器上居中显示"""
        # 获取应用程序当前所在的屏幕
        screen = QApplication.screenAt(QCursor.pos())
        if not screen:
            screen = QApplication.primaryScreen()

        # 获取屏幕几何信息
        screen_geometry = screen.availableGeometry()

        # 计算窗口位置
        window_geometry = self.frameGeometry()
        center_point = screen_geometry.center()
        window_geometry.moveCenter(center_point)

        # 移动窗口
        self.move(window_geometry.topLeft())

    def closeEvent(self, event):
        """窗口关闭时保存状态"""
        self.save_window_state()
        super().closeEvent(event)

    def save_window_state(self):
        """保存窗口状态"""
        settings = QSettings("NFOEditor", "WindowState")  # type: ignore
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("windowState", self.saveState())

    def restore_window_state(self):
        """恢复窗口状态"""
        settings = QSettings("NFOEditor", "WindowState")  # type: ignore
        geometry = settings.value("geometry")
        state = settings.value("windowState")

        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)

        # 确保窗口在可见区域内
        self.ensure_visible_on_screen()

    def ensure_visible_on_screen(self):
        """确保窗口在可见区域内"""
        frame = self.frameGeometry()
        visible = False

        # 检查窗口是否在任何可用屏幕上可见
        for screen in QApplication.screens():
            if screen.availableGeometry().intersects(frame):
                visible = True
                break

        # 如果窗口不可见，将其移动到主屏幕中央
        if not visible:
            screen = QApplication.primaryScreen()
            center_point = screen.availableGeometry().center()
            frame.moveCenter(center_point)
            self.move(frame.topLeft())

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Main grid layout
        main_grid = QGridLayout(central_widget)
        main_grid.setSpacing(int(1 * self.scale_factor))
        main_grid.setContentsMargins(
            int(1 * self.scale_factor),
            int(1 * self.scale_factor),
            int(1 * self.scale_factor),
            int(1 * self.scale_factor),
        )

        # Top button panel
        top_frame = self.create_top_panel()
        main_grid.addWidget(top_frame, 0, 0, 1, 3)

        # Sorting and filtering panel
        sorting_frame = self.create_sorting_panel()
        main_grid.addWidget(sorting_frame, 1, 0, 1, 3)

        # File tree (left panel)
        file_tree_frame = self.create_file_tree()
        main_grid.addWidget(file_tree_frame, 2, 0)

        # Target tree (middle panel)
        target_tree_frame = self.create_target_tree()
        main_grid.addWidget(target_tree_frame, 2, 1)

        # Editor panel (right panel)
        editor_frame = self.create_editor_panel()
        main_grid.addWidget(editor_frame, 2, 2)

        # Set column stretches
        main_grid.setColumnStretch(0, 3)  # File tree
        main_grid.setColumnStretch(1, 2)  # Target tree
        main_grid.setColumnStretch(2, 3)  # Editor panel

        # Set row stretches - 让包含树和编辑器的行获得更多空间
        main_grid.setRowStretch(0, 0)  # Top panel
        main_grid.setRowStretch(1, 0)  # Sorting panel
        main_grid.setRowStretch(2, 1)  # Main content row

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

        # 按钮配置，包含DPI缩放的尺寸
        button_height = int(40 * self.scale_factor)
        buttons_info = [
            (
                "选择nfo目录",
                None,
                "选择目录以加载NFO文件",
                int(150 * self.scale_factor),
            ),
            (
                "选择整理目录",
                None,
                "选择后方显示整理目录列表",
                int(150 * self.scale_factor),
            ),
            ("🖊", None, "打开选中的NFO文件", int(40 * self.scale_factor)),
            ("📁", None, "打开选中的文件夹", int(40 * self.scale_factor)),
            ("⏯", None, "播放选中的视频文件", int(40 * self.scale_factor)),
            ("🔗", None, "统一演员名并重命名文件夹", int(40 * self.scale_factor)),
            ("🔁", None, "刷新文件列表,快捷键F5", int(40 * self.scale_factor)),
            ("🖼", None, "打开海报照片墙", int(40 * self.scale_factor)),
            (
                "🔜",
                None,
                "移动nfo所在文件夹到目标目录,快捷键方向键→",
                int(40 * self.scale_factor),
            ),
            ("⚙️", None, "打开设置", int(40 * self.scale_factor)),  # 新增设置按钮
        ]

        for col, (text, func, tooltip, width) in enumerate(buttons_info):
            btn = QPushButton(text)
            btn.setFixedSize(width, button_height)
            btn.setToolTip(tooltip)
            # 为设置按钮设置对象名称，便于后续识别
            if text == "⚙️":
                btn.setObjectName("settings_button")
            grid.addWidget(btn, 0, col)

        # 将checkbox移到最右侧
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        grid.addWidget(spacer, 0, len(buttons_info))

        self.show_images_checkbox = QCheckBox("显示图片")
        self.show_images_checkbox.setToolTip("显示或隐藏图片")
        grid.addWidget(
            self.show_images_checkbox, 0, len(buttons_info) + 1, Qt.AlignRight
        )

        return frame

    def update_image_label(self, label, pixmap):
        """更新图片标签，确保正确的DPI缩放"""
        if pixmap:
            scaled_pixmap = pixmap.scaled(
                label.size() * self.scale_factor,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            label.setPixmap(scaled_pixmap)

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

        # Sorting options
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

        # Filter options
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
        self.condition_combo.currentIndexChanged.connect(
            lambda x: self.filter_entry.clear()
        )

        # Initialize default conditions
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
        # 添加多选支持
        self.file_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        # 根据DPI缩放调整列宽
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
        
        # 简单的边距控制
        left_margin = int(10 * self.scale_factor)
        right_margin = int(20 * self.scale_factor)
        
        grid.setContentsMargins(left_margin, int(5 * self.scale_factor), right_margin, int(5 * self.scale_factor))
        grid.setSpacing(int(2 * self.scale_factor))

        # 创建各区域
        image_frame = self.create_image_preview()
        grid.addWidget(image_frame, 0, 0)

        fields_frame = self.create_fields_section()
        grid.addWidget(fields_frame, 1, 0)

        operations_frame = self.create_operations_section()
        grid.addWidget(operations_frame, 2, 0)

        # 简单的行拉伸
        grid.setRowStretch(0, 2)
        grid.setRowStretch(1, 4)
        grid.setRowStretch(2, 1)

        return content

    def create_image_preview(self):
        frame = QFrame()
        grid = QGridLayout(frame)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(int(2 * self.scale_factor))

        # 图片标签 - 固定宽度
        image_label = QLabel("图片:")
        image_label.setFixedWidth(int(50 * self.scale_factor))
        image_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(image_label, 0, 0)

        # 图片容器
        image_container = QWidget()
        image_layout = QHBoxLayout(image_container)
        image_layout.setContentsMargins(int(2 * self.scale_factor), 0, 0, 0)
        image_layout.setSpacing(int(10 * self.scale_factor))

        # 计算初始尺寸
        sizes = self.calculate_dynamic_sizes()

        # Poster frame with resolution label - 使用垂直布局
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
        
        # 添加分辨率标签
        self.poster_resolution_label = QLabel("分辨率: 未知")
        self.poster_resolution_label.setAlignment(Qt.AlignCenter)
        self.poster_resolution_label.setStyleSheet("color: gray; font-size: 9pt;")
        self.poster_resolution_label.setFixedHeight(int(16 * self.scale_factor))
        
        poster_container_layout.addWidget(poster_frame)
        poster_container_layout.addWidget(self.poster_resolution_label)

        # Thumb frame with resolution label - 使用垂直布局
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
        
        # 添加分辨率标签
        self.thumb_resolution_label = QLabel("分辨率: 未知")
        self.thumb_resolution_label.setAlignment(Qt.AlignCenter)
        self.thumb_resolution_label.setStyleSheet("color: gray; font-size: 9pt;")
        self.thumb_resolution_label.setFixedHeight(int(16 * self.scale_factor))
        
        thumb_container_layout.addWidget(thumb_frame)
        thumb_container_layout.addWidget(self.thumb_resolution_label)

        # 添加到布局
        image_layout.addWidget(poster_container)
        image_layout.addWidget(thumb_container)
        image_layout.addStretch()

        grid.addWidget(image_container, 0, 1)

        self.poster_label.mousePressEvent = lambda e: self.open_image_and_crop("fanart")
        self.thumb_label.mousePressEvent = lambda e: self.open_image_and_crop("fanart")

        return frame

    def create_fields_section(self):
        frame = QFrame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(int(2 * self.scale_factor))

        fields = {
            "num": ("番号", 2),
            "title": ("标题", 2.5),
            "plot": ("简介", 3),
            "tags": ("标签", 2.5),
            "actors": ("演员", 1.5),
            "series": ("系列", 1.5),
            "rating": ("评分", 1.5),
        }

        # 获取动态尺寸
        sizes = self.calculate_dynamic_sizes()

        for field, (label_text, height) in fields.items():
            field_frame = QFrame()
            field_layout = QHBoxLayout(field_frame)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.setSpacing(int(5 * self.scale_factor))

            # 标签 - 固定宽度保持对齐
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
                
                # 按钮固定尺寸
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
            ("批量新增 (Batch Add)", int(205 * self.scale_factor)),
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
        """根据窗口大小计算动态尺寸"""
        # 获取编辑区域的可用宽度
        available_width = self.width() - 600  # 减去左侧文件树和目标树的大概宽度
        available_height = self.height() - 200  # 减去顶部按钮和底部操作区域
        
        # 计算缩放比例
        base_width = 800  # 基础宽度
        base_height = 600  # 基础高度
        
        width_scale = max(1.0, available_width / base_width)
        height_scale = max(1.0, available_height / base_height)
        
        # 使用较小的缩放比例，保持比例协调
        scale = min(width_scale, height_scale)
        
        return {
            'poster_width': int(180 * self.scale_factor * scale),
            'poster_height': int(270 * self.scale_factor * scale),
            'thumb_width': int(402 * self.scale_factor * scale),
            'thumb_height': int(270 * self.scale_factor * scale),
            'text_width': int(590 * self.scale_factor * scale),
            'text_max_width': int(800 * self.scale_factor * scale)
        }

    def resizeEvent(self, event):
        """窗口大小改变时重新调整布局"""
        super().resizeEvent(event)
        if hasattr(self, 'poster_label') and hasattr(self, 'thumb_label'):
            self.update_layout_sizes()

    def update_layout_sizes(self):
        """更新布局尺寸"""
        sizes = self.calculate_dynamic_sizes()
        
        # 更新图片框大小
        if hasattr(self, 'poster_label'):
            poster_frame = self.poster_label.parent()
            poster_frame.setFixedSize(sizes['poster_width'], sizes['poster_height'])
            self.poster_label.setFixedSize(sizes['poster_width'], sizes['poster_height'])
        
        if hasattr(self, 'thumb_label'):
            thumb_frame = self.thumb_label.parent()
            thumb_frame.setFixedSize(sizes['thumb_width'], sizes['thumb_height'])
            self.thumb_label.setFixedSize(sizes['thumb_width'], sizes['thumb_height'])
        
        # 更新文本框大小
        for field, widget in self.fields_entries.items():
            if field != "num":
                widget.setMinimumWidth(sizes['text_width'])
                widget.setMaximumWidth(sizes['text_max_width'])
            else:
                widget.setMinimumWidth(int(sizes['text_width'] * 0.6))
                widget.setMaximumWidth(int(sizes['text_max_width'] * 0.6))

        # 自动刷新当前显示的图片
        if self.show_images_checkbox.isChecked() and self.current_file_path:
            self.display_image()

if __name__ == "__main__":
    # 设置高DPI支持
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = NFOEditorQt()
    window.restore_window_state()
    window.show()

    sys.exit(app.exec_())
