"""
nfo_editor_ui.py
================
NFO Editor UI 层 - PySide6 + qfluentwidgets 实现

设计目标:
  - 现代 Fluent Design 视觉风格
  - 三段 QSplitter 自由拖动布局
  - Qt6 自动 DPI 处理
  - 图片自动按容器尺寸保比例缩放
  - 与子类完全解耦,只通过命名属性契约

==== 接口契约(子类 NFOEditorQt6 依赖的所有 UI 元素) ====

工具栏按钮 (均为 QPushButton 派生):
    btn_open_folder       "选择nfo目录"
    btn_select_target     "选择整理目录"
    btn_open_nfo          "🖊"  打开 NFO
    btn_open_dir          "📁"  打开文件夹
    btn_play_video        "⏯"  播放视频
    btn_unify_actor       "🔗"  统一演员
    btn_refresh           "🔁"  刷新
    btn_photo_wall        "🖼"  照片墙
    btn_move              "🔜"  移动到目标
    btn_settings          "⚙️"  设置

操作区按钮:
    btn_save / btn_batch_fill / btn_batch_add / btn_filter

番号区按钮:
    copy_num_button / play_trailer_button

输入控件:
    show_images_checkbox / field_combo / condition_combo
    filter_entry / sorting_group

数据控件:
    fields_entries (dict[str, QWidget])
        "num"    -> QLabel    (.text(), .setText(), 可覆盖 mousePressEvent)
        其余     -> TextEdit  (.toPlainText(), .setPlainText())
    file_tree / sorted_tree              TreeWidget
    poster_label / thumb_label           AdaptiveImageLabel
    poster_resolution_label / thumb_resolution_label / release_label / save_time_label
    status_bar (来自 QMainWindow.statusBar())

提供方法:
    set_target_panel_visible(visible: bool)
    restore_window_state()

==== qfluentwidgets 关键注意点 ====
import qfluentwidgets 时,某些静态资源会触发 QPixmap 等 QWidget 派生对象的构造,
所以**必须先 QApplication(sys.argv) 再 import qfluentwidgets**。
本模块的 import 顺序已处理,使用者只需正常 `from nfo_editor_ui import NFOEditorQt`,
前提是调用方在 import 前已经构造了 QApplication。
"""

import os
import sys

from PySide6.QtCore import Qt, QSettings, QSize
from PySide6.QtGui import QCursor, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QPushButton,
    QSplitter,
    QStyle,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)


# Fluent 风格的 QTreeWidget QSS - 模拟 qfluentwidgets TreeWidget 视觉
# 但不带它的平滑滚动动画
_TREE_QSS = """
QTreeWidget {
    background-color: transparent;
    border: none;
    outline: 0;
    font-size: 9pt;
}
QTreeWidget::item {
    height: 28px;
    padding: 0px 4px;
    border-radius: 4px;
    margin: 1px 2px;
    color: #1f1f1f;
}
QTreeWidget::item:hover {
    background-color: rgba(0, 120, 212, 25);
}
QTreeWidget::item:selected {
    background-color: rgba(0, 120, 212, 60);
    color: #000000;
}
QTreeWidget::item:selected:!focus {
    background-color: rgba(0, 0, 0, 30);
    color: #1f1f1f;
}
QHeaderView::section {
    background-color: rgba(0, 0, 0, 8);
    padding: 4px 6px;
    border: none;
    border-right: 1px solid rgba(0, 0, 0, 20);
    font-weight: 600;
}
QHeaderView::section:last {
    border-right: none;
}
"""


def get_resource_path(relative_path: str) -> str:
    """资源路径解析,同时兼容 PyInstaller 打包和开发运行。"""
    if getattr(sys, "frozen", False):
        base_path = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


# ============================================================
#  延迟导入 qfluentwidgets
#  - 在模块顶层 import 会导致"先于 QApplication"的错误
#  - 改用函数级延迟导入,保证调用时 QApplication 已存在
# ============================================================

def _import_qfluentwidgets():
    """在 QApplication 已存在时才 import qfluentwidgets,返回所需符号字典。

    注意:刻意不引入 qfluentwidgets 的 TreeWidget,
    因为它自带的平滑滚动动画无法可靠关闭,
    改用原生 QTreeWidget + 自定义 QSS 模拟 Fluent 视觉风格。
    """
    from qfluentwidgets import (
        BodyLabel,
        CaptionLabel,
        CheckBox,
        ComboBox,
        FluentIcon,
        LineEdit,
        PrimaryPushButton,
        PushButton,
        RadioButton,
        StrongBodyLabel,
        TextEdit,
        Theme,
        setTheme,
        setThemeColor,
    )
    return {
        "BodyLabel": BodyLabel,
        "CaptionLabel": CaptionLabel,
        "CheckBox": CheckBox,
        "ComboBox": ComboBox,
        "FluentIcon": FluentIcon,
        "LineEdit": LineEdit,
        "PrimaryPushButton": PrimaryPushButton,
        "PushButton": PushButton,
        "RadioButton": RadioButton,
        "StrongBodyLabel": StrongBodyLabel,
        "TextEdit": TextEdit,
        "Theme": Theme,
        "setTheme": setTheme,
        "setThemeColor": setThemeColor,
    }


# ============================================================
#  AdaptiveImageLabel - 自动按比例缩放的图片标签
# ============================================================

class AdaptiveImageLabel(QLabel):
    """图片标签,setPixmap(原图) 后任何尺寸变化都会自动按比例缩放。

    用法:
        label = AdaptiveImageLabel(aspect_ratio=1.5)  # height/width
        label.setPixmap(QPixmap(path))   # 原图,无需先 scaled

    aspect_ratio: 容器自身的高宽比(height / width)。
      - poster 用 1.5 (2:3 竖向)
      - thumb  用 0.5625 (16:9 横向)
      - None 表示不约束,自由比例
    设置后控件会根据宽度自动算出高度,通过 heightForWidth 通知 layout。

    setText() / clear() 会清空缓存,回到文本占位状态。
    """

    def __init__(self, text="", aspect_ratio=None, *args, **kwargs):
        super().__init__(text, *args, **kwargs)
        self._source_pixmap = None
        self._aspect_ratio = aspect_ratio
        self.setAlignment(Qt.AlignCenter)
        self.setScaledContents(False)
        self.setMinimumSize(80, 80)

        # 启用 heightForWidth,使 layout 按宽度推算高度
        if aspect_ratio is not None:
            sp = self.sizePolicy()
            sp.setHeightForWidth(True)
            self.setSizePolicy(sp)

    def hasHeightForWidth(self) -> bool:
        return self._aspect_ratio is not None

    def heightForWidth(self, width: int) -> int:
        if self._aspect_ratio is not None and width > 0:
            return int(width * self._aspect_ratio)
        return super().heightForWidth(width)

    def setPixmap(self, pixmap):
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            self._source_pixmap = pixmap
            self._refresh_pixmap()
        else:
            self._source_pixmap = None
            super().setPixmap(pixmap)

    def setText(self, text):
        self._source_pixmap = None
        super().setText(text)

    def clear(self):
        self._source_pixmap = None
        super().clear()

    def _refresh_pixmap(self):
        """高 DPI 适配的缩放:

        Qt6 中 widget.size() 返回逻辑像素,但底层渲染使用物理像素。
        如果直接缩到 logical_size,在 DPR=1.5 的屏幕上会被 Qt 再放大 1.5x
        导致模糊。正确做法:
          1. 缩到 logical_size * DPR(物理像素尺寸)
          2. setDevicePixelRatio(DPR) 告诉 Qt 这是高分图
          3. 显示时 Qt 会按 logical_size 显示但保留物理细节
        """
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return
        size = self.size()
        if size.width() <= 1 or size.height() <= 1:
            return
        dpr = self.devicePixelRatioF() or 1.0
        target_size = size * dpr
        scaled = self._source_pixmap.scaled(
            target_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(dpr)
        super().setPixmap(scaled)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_pixmap()


# ============================================================
#  _ImagePreviewContainer - 维持 poster/thumb 比例的容器
# ============================================================

class _ImagePreviewContainer(QWidget):
    """图片区子容器,在 resizeEvent 中按比例分配 poster/thumb 尺寸。

    Poster: 2:3 (width:height = 2:3,即 width = height * 2/3)
    Thumb:  16:9 (width:height = 16:9,即 height = width * 9/16)

    布局逻辑(给定容器高度 H 和宽度 W):
      1. 预留底部 20px 给分辨率标签
      2. 可用图片高度 h = H - 20
      3. Poster 尺寸: poster_w = h * 2/3, poster_h = h
      4. 剩余宽度给 Thumb: thumb_w = W - poster_w - spacing
      5. Thumb 维持 16:9,高度 = thumb_w * 9/16,
         但不超过 h(否则会撑破容器)
      6. 如果 thumb 的 16:9 高度 < h,在垂直方向居中
    """

    SPACING = 8
    RESOLUTION_LABEL_HEIGHT = 20

    def __init__(self, parent_owner=None):
        super().__init__()
        self._parent_owner = parent_owner
        self.poster_label = None
        self.thumb_label = None
        self.poster_resolution_label = None
        self.thumb_resolution_label = None
        # 让容器在父 layout 里占满分配空间
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def setup_children(self, poster_label, poster_res_label,
                       thumb_label, thumb_res_label):
        """注入由父类创建的图片和分辨率标签,容器只负责布局。"""
        self.poster_label = poster_label
        self.thumb_label = thumb_label
        self.poster_resolution_label = poster_res_label
        self.thumb_resolution_label = thumb_res_label

        # 让所有子控件成为本容器的子控件
        for w in (poster_label, poster_res_label,
                  thumb_label, thumb_res_label):
            w.setParent(self)

        self._layout_children()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_children()

    def _layout_children(self):
        if self.poster_label is None:
            return

        W = self.width()
        H = self.height()
        if W <= 1 or H <= 1:
            return

        # 图片高度 = 总高度 - 分辨率标签高度
        h = H - self.RESOLUTION_LABEL_HEIGHT - 2

        # Poster 维持 2:3
        poster_w = int(h * 2 / 3)
        # Poster 宽度上限,防止极端情况
        poster_w = min(poster_w, int(W * 0.45))
        poster_h = h

        # 剩余宽度给 Thumb
        thumb_w = W - poster_w - self.SPACING
        if thumb_w < 100:
            thumb_w = 100  # 兜底

        # Thumb 维持 16:9,但高度不超过 h
        thumb_h_by_ratio = int(thumb_w * 9 / 16)
        thumb_h = min(thumb_h_by_ratio, h)
        # 如果按 16:9 算出来高度不够 h,thumb 在垂直方向居中
        thumb_y_offset = (h - thumb_h) // 2

        # 实际宽度可能因为高度限制需要回缩(维持 16:9)
        if thumb_h_by_ratio > h:
            thumb_w = int(h * 16 / 9)

        # 设置几何
        self.poster_label.setGeometry(0, 0, poster_w, poster_h)
        self.poster_resolution_label.setGeometry(
            0, poster_h + 2, poster_w, self.RESOLUTION_LABEL_HEIGHT
        )

        thumb_x = poster_w + self.SPACING
        self.thumb_label.setGeometry(thumb_x, thumb_y_offset, thumb_w, thumb_h)
        self.thumb_resolution_label.setGeometry(
            thumb_x, h + 2, thumb_w, self.RESOLUTION_LABEL_HEIGHT
        )


# ============================================================
#  NFOEditorQt - 主窗口 UI 类
# ============================================================

class NFOEditorQt(QMainWindow):
    """NFO 编辑器主窗口 UI 层。只构建控件和布局,业务逻辑在子类。"""

    # 字段配置: (key, label_text, height, expanding)
    # height: TextEdit 目标高度
    #   1 行 ≈ 32px,2 行 ≈ 56px (容纳两行文字 + 内边距)
    # expanding: 是否垂直可拉伸(目前关闭,严格按行数限制)
    _TEXT_FIELDS = [
        ("title",  "标题",  32,  False),   # 1 行
        ("plot",   "简介",  56,  False),   # 2 行
        ("tags",   "标签",  56,  False),   # 2 行
        ("actors", "演员",  32,  False),   # 1 行
        ("series", "系列",  32,  False),   # 1 行
        ("rating", "评分",  32,  False),   # 1 行
    ]

    _ICON_BUTTONS = [
        ("btn_open_nfo",    "🖊", "打开选中的NFO文件"),
        ("btn_open_dir",    "📁", "打开选中的文件夹"),
        ("btn_play_video",  "⏯", "播放选中的视频文件"),
        ("btn_unify_actor", "🔗", "统一演员名并重命名文件夹"),
        ("btn_refresh",     "🔁", "刷新文件列表  (F5)"),
        ("btn_photo_wall",  "🖼", "打开海报照片墙"),
        ("btn_move",        "🔜", "移动nfo所在文件夹到目标目录  (Ctrl+→)"),
    ]

    def __init__(self):
        super().__init__()

        # 延迟加载 qfluentwidgets(此时 QApplication 已构造)
        self._fw = _import_qfluentwidgets()

        # ⚠️ 关键顺序:在创建任何 fluent widget 之前先设置主题
        # 否则主题切换会触发已存在 widget 的 paintEvent,
        # 而此时 widget 的 backing store 可能未就绪 ->
        # "QPainter::begin: Paint device returned engine == 0, type: 3"
        self._fw["setTheme"](self._fw["Theme"].LIGHT)
        self._fw["setThemeColor"]("#0078D4")

        # 业务状态(子类会读写)
        self.current_file_path = None
        self.folder_path = None
        self.current_target_path = None
        self.fields_entries = {}

        self.setWindowTitle("大锤 NFO Editor v9.8.0")
        self.resize(1400, 900)
        self.setMinimumSize(1000, 650)

        self.status_bar = self.statusBar()
        self.status_bar.showMessage("就绪")

        self._setup_icon()
        self._setup_font()
        self._setup_ui()
        self._center_window()

    def _setup_icon(self):
        try:
            icon_path = get_resource_path("chuizi.ico")
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except OSError:
            pass

    def _setup_font(self):
        font = QFont("Microsoft YaHei UI", 9)
        self.setFont(font)

    def _center_window(self):
        screen = QApplication.screenAt(QCursor.pos())
        if not screen:
            screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = self.frameGeometry()
        geo.moveCenter(screen.availableGeometry().center())
        self.move(geo.topLeft())

    # ------------------------------------------------------------
    #  窗口状态持久化
    # ------------------------------------------------------------

    def closeEvent(self, event):
        self._save_window_state()
        super().closeEvent(event)

    def _save_window_state(self):
        settings = QSettings("NFOEditor", "WindowState")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("windowState", self.saveState())
        if hasattr(self, "main_splitter"):
            settings.setValue("splitterState", self.main_splitter.saveState())

    def restore_window_state(self):
        settings = QSettings("NFOEditor", "WindowState")
        geometry = settings.value("geometry")
        state = settings.value("windowState")
        splitter_state = settings.value("splitterState")
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)
        if splitter_state and hasattr(self, "main_splitter"):
            self.main_splitter.restoreState(splitter_state)
        self._ensure_visible_on_screen()

    def _ensure_visible_on_screen(self):
        frame = self.frameGeometry()
        visible = any(
            s.availableGeometry().intersects(frame)
            for s in QApplication.screens()
        )
        if not visible:
            primary = QApplication.primaryScreen()
            if primary:
                frame.moveCenter(primary.availableGeometry().center())
                self.move(frame.topLeft())

    # ------------------------------------------------------------
    #  主布局
    # ------------------------------------------------------------

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(8, 6, 8, 4)
        root.setSpacing(6)

        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_sort_filter_bar())

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(6)

        self.file_tree_panel = self._build_file_tree_panel()
        self.main_splitter.addWidget(self.file_tree_panel)

        self.target_tree_panel = self._build_target_tree_panel()
        self.main_splitter.addWidget(self.target_tree_panel)
        self.target_tree_panel.hide()

        self.editor_panel = self._build_editor_panel()
        self.main_splitter.addWidget(self.editor_panel)

        self.main_splitter.setSizes([360, 280, 560])
        self.main_splitter.setStretchFactor(0, 4)
        self.main_splitter.setStretchFactor(1, 3)
        self.main_splitter.setStretchFactor(2, 6)

        root.addWidget(self.main_splitter, 1)

    def set_target_panel_visible(self, visible: bool):
        """显示/隐藏中间目标目录面板。替代原 QGridLayout columnStretch 折叠。"""
        if visible:
            self.target_tree_panel.show()
            self.sorted_tree.show()
            sizes = self.main_splitter.sizes()
            if sizes[1] <= 0:
                total = sum(sizes) or 1200
                self.main_splitter.setSizes([
                    int(total * 4 / 13),
                    int(total * 3 / 13),
                    int(total * 6 / 13),
                ])
        else:
            self.target_tree_panel.hide()

    # ------------------------------------------------------------
    #  顶部工具栏
    # ------------------------------------------------------------

    def _build_toolbar(self) -> QFrame:
        PushButton = self._fw["PushButton"]
        CheckBox = self._fw["CheckBox"]
        FluentIcon = self._fw["FluentIcon"]

        bar = QFrame()
        bar.setObjectName("toolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(6)

        self.btn_open_folder = PushButton("选择nfo目录")
        self.btn_open_folder.setIcon(FluentIcon.FOLDER)
        self.btn_open_folder.setToolTip("选择目录以加载NFO文件")
        self.btn_open_folder.setMinimumHeight(34)
        layout.addWidget(self.btn_open_folder)

        self.btn_select_target = PushButton("选择整理目录")
        self.btn_select_target.setIcon(FluentIcon.SEND)
        self.btn_select_target.setToolTip("选择后方显示整理目录列表")
        self.btn_select_target.setMinimumHeight(34)
        layout.addWidget(self.btn_select_target)

        layout.addWidget(self._make_vsep())

        for attr, emoji, tip in self._ICON_BUTTONS:
            btn = PushButton(emoji)
            btn.setFixedSize(40, 34)
            btn.setToolTip(tip)
            setattr(self, attr, btn)
            layout.addWidget(btn)

        layout.addStretch(1)

        self.show_images_checkbox = CheckBox("显示图片")
        self.show_images_checkbox.setToolTip("显示或隐藏图片")
        layout.addWidget(self.show_images_checkbox)

        self.btn_settings = PushButton("⚙️")
        self.btn_settings.setFixedSize(40, 34)
        self.btn_settings.setToolTip("打开设置")
        layout.addWidget(self.btn_settings)

        return bar

    def _make_vsep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Plain)
        sep.setFixedWidth(1)
        sep.setStyleSheet("QFrame { color: rgba(0, 0, 0, 30); }")
        return sep

    # ------------------------------------------------------------
    #  排序/筛选栏
    # ------------------------------------------------------------

    def _build_sort_filter_bar(self) -> QFrame:
        BodyLabel = self._fw["BodyLabel"]
        ComboBox = self._fw["ComboBox"]
        LineEdit = self._fw["LineEdit"]
        PushButton = self._fw["PushButton"]
        RadioButton = self._fw["RadioButton"]
        FluentIcon = self._fw["FluentIcon"]

        bar = QFrame()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(2, 0, 2, 2)
        layout.setSpacing(8)

        layout.addWidget(BodyLabel("排序:"))

        self.sorting_group = QButtonGroup(self)
        for text in [
            "日期 (Release Date)",
            "演员 (Actors)",
            "系列 (Series)",
            "评分 (Rating)",
        ]:
            rb = RadioButton(text)
            self.sorting_group.addButton(rb)
            layout.addWidget(rb)

        layout.addSpacing(20)
        layout.addWidget(BodyLabel("筛选:"))

        self.field_combo = ComboBox()
        self.field_combo.setFixedWidth(90)
        self.field_combo.addItems(["标题", "标签", "演员", "系列", "评分"])
        layout.addWidget(self.field_combo)

        self.condition_combo = ComboBox()
        self.condition_combo.setFixedWidth(90)
        layout.addWidget(self.condition_combo)

        self.filter_entry = LineEdit()
        self.filter_entry.setFixedWidth(180)
        self.filter_entry.setPlaceholderText("输入筛选条件")
        layout.addWidget(self.filter_entry)

        def on_field_changed(_index: int):
            self.condition_combo.clear()
            self.filter_entry.clear()
            if self.field_combo.currentText() == "评分":
                self.condition_combo.addItems(["大于", "小于"])
            else:
                self.condition_combo.addItems(["包含", "不包含"])

        self.field_combo.currentIndexChanged.connect(on_field_changed)
        on_field_changed(0)

        self.btn_filter = PushButton("筛选")
        self.btn_filter.setIcon(FluentIcon.FILTER)
        self.btn_filter.setFixedSize(80, 30)
        self.btn_filter.setToolTip("根据条件筛选文件列表")
        layout.addWidget(self.btn_filter)

        layout.addStretch(1)
        return bar

    # ------------------------------------------------------------
    #  左:文件树
    # ------------------------------------------------------------

    def _build_file_tree_panel(self) -> QFrame:
        """文件列表面板。

        改用原生 QTreeWidget(非 qfluentwidgets.TreeWidget),
        因为后者自带的平滑滚动动画无法可靠关闭。
        视觉风格通过 _TREE_QSS 模拟 Fluent Design。
        """
        StrongBodyLabel = self._fw["StrongBodyLabel"]

        panel = QFrame()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        layout.addWidget(StrongBodyLabel("📂 文件列表"))

        # 外框 frame 提供视觉边界
        tree_frame = QFrame()
        tree_frame.setObjectName("fileTreeFrame")
        tree_frame.setStyleSheet(
            "#fileTreeFrame {"
            " border: 1px solid rgba(0, 0, 0, 40);"
            " border-radius: 6px;"
            " background-color: rgba(255, 255, 255, 0.5);"
            "}"
        )
        tree_frame_layout = QVBoxLayout(tree_frame)
        tree_frame_layout.setContentsMargins(2, 2, 2, 2)
        tree_frame_layout.setSpacing(0)

        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["一级目录", "二级目录", "NFO文件"])
        self.file_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.file_tree.setRootIsDecorated(False)
        self.file_tree.setFrameShape(QFrame.NoFrame)
        self.file_tree.setStyleSheet(_TREE_QSS)

        header = self.file_tree.header()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        self.file_tree.setColumnWidth(0, 180)
        self.file_tree.setColumnWidth(1, 180)
        self.file_tree.setColumnHidden(2, True)

        tree_frame_layout.addWidget(self.file_tree)
        layout.addWidget(tree_frame, 1)
        return panel

    # ------------------------------------------------------------
    #  中:目标目录
    # ------------------------------------------------------------

    def _build_target_tree_panel(self) -> QFrame:
        StrongBodyLabel = self._fw["StrongBodyLabel"]

        panel = QFrame()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        layout.addWidget(StrongBodyLabel("🎯 目标目录"))

        tree_frame = QFrame()
        tree_frame.setObjectName("targetTreeFrame")
        tree_frame.setStyleSheet(
            "#targetTreeFrame {"
            " border: 1px solid rgba(0, 0, 0, 40);"
            " border-radius: 6px;"
            " background-color: rgba(255, 255, 255, 0.5);"
            "}"
        )
        tree_frame_layout = QVBoxLayout(tree_frame)
        tree_frame_layout.setContentsMargins(2, 2, 2, 2)
        tree_frame_layout.setSpacing(0)

        self.sorted_tree = QTreeWidget()
        self.sorted_tree.setHeaderLabels(["目标文件夹"])
        self.sorted_tree.setRootIsDecorated(False)
        self.sorted_tree.setFrameShape(QFrame.NoFrame)
        self.sorted_tree.setStyleSheet(_TREE_QSS)

        tree_frame_layout.addWidget(self.sorted_tree)
        layout.addWidget(tree_frame, 1)
        return panel

    def _build_editor_panel(self) -> QFrame:
        panel = QFrame()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(8)

        # 图片区可拉伸,字段区和操作区固定大小
        # 这样窗口变高时,多余空间给图片,字段不会变形
        layout.addWidget(self._build_image_area(), 1)
        layout.addWidget(self._build_fields_area(), 0)
        layout.addWidget(self._build_ops_area(), 0)
        return panel

    def _build_image_area(self) -> QFrame:
        """构建图片预览区。

        布局:[60px "封面:" 标签] + [自适应比例的 Poster + Thumb 容器]

        Poster 维持 2:3(width:height),Thumb 维持 16:9。
        当图片区高度变化时,Poster 跟随高度(宽度=高度*2/3),
        Thumb 高度不超过 Poster 高度,自适应剩余宽度。
        """
        BodyLabel = self._fw["BodyLabel"]
        CaptionLabel = self._fw["CaptionLabel"]

        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)

        # 左侧 "封面:" 文字,对齐下方字段标签左缘
        section_label = BodyLabel("封面:")
        section_label.setFixedWidth(60)
        section_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        section_label.setContentsMargins(0, 4, 0, 0)
        layout.addWidget(section_label)

        # 自适应图片容器(handles aspect ratio)
        self._image_container = _ImagePreviewContainer(parent_owner=self)
        layout.addWidget(self._image_container, 1)

        # 创建子控件 - 实际显示交给 _image_container
        # 这两个属性是子类约定的接口,必须存在
        self.poster_label = AdaptiveImageLabel("封面图 (poster)")
        self.poster_label.setObjectName("posterImage")
        self.poster_label.setStyleSheet(
            "#posterImage {"
            " border: 1px solid rgba(0, 0, 0, 30);"
            " border-radius: 4px;"
            " background-color: rgba(0, 0, 0, 8);"
            " color: rgba(0, 0, 0, 120);"
            "}"
        )
        self.poster_label.setCursor(Qt.PointingHandCursor)

        self.thumb_label = AdaptiveImageLabel("缩略图 (thumb)")
        self.thumb_label.setObjectName("thumbImage")
        self.thumb_label.setStyleSheet(
            "#thumbImage {"
            " border: 1px solid rgba(0, 0, 0, 30);"
            " border-radius: 4px;"
            " background-color: rgba(0, 0, 0, 8);"
            " color: rgba(0, 0, 0, 120);"
            "}"
        )
        self.thumb_label.setCursor(Qt.PointingHandCursor)

        self.poster_resolution_label = CaptionLabel("分辨率: 未知")
        self.poster_resolution_label.setAlignment(Qt.AlignCenter)
        self.thumb_resolution_label = CaptionLabel("分辨率: 未知")
        self.thumb_resolution_label.setAlignment(Qt.AlignCenter)

        # 让容器持有这些控件并布局
        self._image_container.setup_children(
            self.poster_label, self.poster_resolution_label,
            self.thumb_label, self.thumb_resolution_label,
        )

        # 图片区高度策略:最少 260,默认 400,允许拉伸到 600
        frame.setMinimumHeight(260)
        frame.setMaximumHeight(600)
        return frame

    def _build_fields_area(self) -> QFrame:
        frame = QFrame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addLayout(self._build_num_row())

        for key, label_text, height, expanding in self._TEXT_FIELDS:
            layout.addLayout(
                self._build_text_field_row(key, label_text, height, expanding)
            )
        return frame

    def _build_num_row(self) -> QHBoxLayout:
        BodyLabel = self._fw["BodyLabel"]
        PushButton = self._fw["PushButton"]

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        label = BodyLabel("番号:")
        label.setFixedWidth(60)
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(label)

        # 番号 QLabel - 故意用原生 QLabel,因为子类要覆盖 mousePressEvent
        # 设固定高度让它和按钮 30px 在同一基线
        num_label = QLabel("")
        num_label.setCursor(Qt.PointingHandCursor)
        num_label.setStyleSheet(
            "QLabel {"
            " color: #0078D4;"
            " text-decoration: underline;"
            " padding: 0px 6px;"
            "}"
            "QLabel:hover { color: #106EBE; }"
        )
        num_label.setMinimumWidth(120)
        num_label.setMaximumWidth(220)
        num_label.setFixedHeight(30)
        num_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        row.addWidget(num_label)
        self.fields_entries["num"] = num_label

        self.copy_num_button = PushButton("📋")
        self.copy_num_button.setFixedSize(34, 30)
        self.copy_num_button.setToolTip("复制番号")
        row.addWidget(self.copy_num_button)

        self.play_trailer_button = PushButton("🎬")
        self.play_trailer_button.setFixedSize(34, 30)
        self.play_trailer_button.setToolTip("播放预告片")
        row.addWidget(self.play_trailer_button)

        row.addSpacing(16)

        date_label = BodyLabel("发行日期:")
        date_label.setAlignment(Qt.AlignVCenter)
        row.addWidget(date_label)

        self.release_label = BodyLabel("")
        self.release_label.setMinimumWidth(100)
        self.release_label.setAlignment(Qt.AlignVCenter)
        row.addWidget(self.release_label)

        row.addStretch(1)
        return row

    def _build_text_field_row(self, key: str, label_text: str,
                              height: int, expanding: bool) -> QHBoxLayout:
        """构建一行 [60px 标签] + [TextEdit] 的字段行。

        对齐策略:
          - 单行字段(height<=40): 标签 AlignVCenter,与单行文字基线对齐
          - 多行字段(height>=50): 标签 AlignTop + 4px 顶部内边距,贴第一行文字
        """
        BodyLabel = self._fw["BodyLabel"]
        TextEdit = self._fw["TextEdit"]

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        label = BodyLabel(f"{label_text}:")
        label.setFixedWidth(60)
        if height >= 50:
            # 多行字段:标签贴顶
            label.setAlignment(Qt.AlignRight | Qt.AlignTop)
            label.setContentsMargins(0, 4, 0, 0)
        else:
            # 单行字段:标签居中,和 TextEdit 单行文字基线对齐
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(label)

        te = TextEdit()
        te.setTabChangesFocus(True)
        te.setFixedHeight(height)
        te.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # 修复: 固定高度的小 TextEdit 在空内容时仍会显示竖直滚动条。
        # 根因是 qfluentwidgets TextEdit 的默认文档边距把内容撑过控件高度。
        # 处理: 收紧文档边距; 单行字段彻底关掉竖滚动条,
        # 多行字段(简介/标签)保留"按需"以便内容超出时仍可滚动。
        te.document().setDocumentMargin(2)
        te.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        if height < 50:
            te.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        else:
            te.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        row.addWidget(te, 1)
        self.fields_entries[key] = te
        return row

    def _build_ops_area(self) -> QFrame:
        """操作按钮区。

        V5 修复说明:
        - 底部 3 个主操作按钮改用原生 QPushButton,不再使用 qfluentwidgets PushButton。
          原因:qfluentwidgets 的 PushButton 依赖内部 painter 绘制背景和图标文字布局,
          一旦叠加自定义 QSS,容易出现图标文字重叠、边框消失、pressed 状态失效。
        - 这里用原生 QPushButton + 完整 QSS,明确声明背景、边框、hover、pressed、disabled。
        - 图标统一用 Qt 标准图标,并显式设置 iconSize,文本前加两个空格形成稳定图标文字间距。
        - 3 个按钮 stretch 全部为 1,保持等宽。
        """
        CaptionLabel = self._fw["CaptionLabel"]

        frame = QFrame()
        layout = QVBoxLayout(frame)
        # 左缘 68px = 60(label) + 8(spacing),对齐字段输入框左缘
        layout.setContentsMargins(68, 8, 0, 4)
        layout.setSpacing(4)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(18)

        # 原生 QPushButton 完整胶囊样式。不要再只写 border-radius,否则会覆盖默认外观。
        pill_qss = """
        QPushButton#BottomPillActionButton {
            background-color: #ffffff;
            border: 1px solid rgba(0, 0, 0, 55);
            border-radius: 19px;
            color: #202020;
            font-size: 9pt;
            min-height: 38px;
            max-height: 38px;
            padding: 0px 22px;
            text-align: center;
        }
        QPushButton#BottomPillActionButton:hover {
            background-color: rgba(0, 0, 0, 7);
            border: 1px solid rgba(0, 0, 0, 85);
        }
        QPushButton#BottomPillActionButton:pressed {
            background-color: rgba(0, 0, 0, 18);
            border: 1px solid rgba(0, 0, 0, 110);
            padding-top: 1px;
            padding-left: 23px;
            padding-right: 21px;
        }
        QPushButton#BottomPillActionButton:disabled {
            background-color: rgba(255, 255, 255, 120);
            border: 1px solid rgba(0, 0, 0, 25);
            color: rgba(0, 0, 0, 90);
        }
        """

        sp = QStyle.StandardPixmap if hasattr(QStyle, "StandardPixmap") else QStyle

        def make_pill_button(text: str, icon_enum, tooltip: str) -> QPushButton:
            button = QPushButton(f"  {text}")
            button.setObjectName("BottomPillActionButton")
            button.setIcon(self.style().standardIcon(icon_enum))
            button.setIconSize(QSize(18, 18))
            button.setToolTip(tooltip)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumWidth(180)
            button.setFixedHeight(38)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setStyleSheet(pill_qss)
            return button

        self.btn_save = make_pill_button(
            "保存更改",
            sp.SP_DialogSaveButton,
            "保存当前 NFO 文件 (Save Changes)",
        )
        self.btn_batch_fill = make_pill_button(
            "批量填充",
            sp.SP_FileDialogDetailedView,
            "批量填充选中文件的字段 (Batch Filling)",
        )
        self.btn_batch_add = make_pill_button(
            "批量新增",
            sp.SP_FileDialogNewFolder,
            "批量新增标签到选中文件 (Batch Add)",
        )

        # 等宽按钮:三个 stretch 都用 1,中间间隔由 row.setSpacing(18) 控制。
        row.addWidget(self.btn_save, 1)
        row.addWidget(self.btn_batch_fill, 1)
        row.addWidget(self.btn_batch_add, 1)

        layout.addLayout(row)

        self.save_time_label = CaptionLabel("")
        self.save_time_label.setMinimumHeight(18)
        layout.addWidget(self.save_time_label)
        return frame


# ============================================================
#  Standalone preview
# ============================================================

if __name__ == "__main__":
    # 关键顺序:必须先 QApplication 再构造 NFOEditorQt
    # (NFOEditorQt 内部会 import qfluentwidgets)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = NFOEditorQt()
    window.show()
    sys.exit(app.exec())
