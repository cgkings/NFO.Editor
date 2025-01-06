# 基础必需的PyQt模块，保留必要的部分
from PyQt5.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QGroupBox,
    QVBoxLayout,
    QHBoxLayout,
    QCheckBox,
    QRadioButton,
    QWidget,
    QButtonGroup,
)
from PyQt5.QtCore import QSize, Qt, QRect, pyqtSignal
from PyQt5.QtGui import QIcon, QImage, QImageReader, QPixmap, QPainter, QPen, QTransform

# 基础系统模块
import os
import sys


def get_resource_path(relative_path):
    """获取资源文件的绝对路径"""
    if getattr(sys, "frozen", False):
        # 如果是打包后的exe
        base_path = sys._MEIPASS
    else:
        # 如果是直接运行的py脚本
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


class CropLabel(QLabel):
    original_info_updated = pyqtSignal(int, int)  # 发送原图宽度和高度
    crop_info_updated = pyqtSignal(QRect)  # 发送裁剪框信息
    orientation_changed = pyqtSignal(bool)  # 发送图片是否为横向的信号

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(800, 538)
        self.rotation_angle = 0
        self.image_is_horizontal = True
        self.pixmap = None
        self.scaled_pixmap = None
        self.original_size = None  # 保存原始图片尺寸
        self.offset_x = 0
        self.offset_y = 0
        self.crop_rect = None
        self.dragging = False
        self.drag_start = None
        self.setMouseTracking(True)
        self.setAcceptDrops(True)

        # 添加图片实际方向标志（不受显示区域影响）
        self.image_is_horizontal = True

        self.setStyleSheet(
            """
            QLabel {
                background-color: #F9FAFB;
                border: 2px dashed #60A5FA;
                border-radius: 6px;
            }
            QLabel:hover {
                border-color: #60A5FA;
                background-color: #F3F4F6;
            }
        """
        )
        self.setAlignment(Qt.AlignCenter)
        self.setText("请点击右上角的'打开图片'按钮或拖拽图片到此处")

    def rotate_image(self, angle):
        """旋转图片并保持裁剪框的2:3比例"""
        if not self.pixmap or self.pixmap.isNull():
            return

        # 更新角度并旋转图片
        self.rotation_angle = (self.rotation_angle + angle) % 360
        transform = QTransform().rotate(angle)
        self.pixmap = self.pixmap.transformed(transform, Qt.SmoothTransformation)

        # 更新缩放图片
        self.scaled_pixmap = self.pixmap.scaled(
            800, 538, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )

        # 更新偏移量
        self.offset_x = (800 - self.scaled_pixmap.width()) // 2
        self.offset_y = (538 - self.scaled_pixmap.height()) // 2

        # 更新图片方向
        old_is_horizontal = self.image_is_horizontal
        self.image_is_horizontal = not self.image_is_horizontal

        # 重新初始化裁剪框，这将确保2:3的比例
        self.initialize_crop_rect()
        self.update()

        # 发送信号
        self.original_info_updated.emit(self.pixmap.width(), self.pixmap.height())

    def dragEnterEvent(self, event):
        """处理拖拽进入事件"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                # 检查是否是支持的图片格式
                file_path = url.toLocalFile()
                if any(
                    file_path.lower().endswith(ext)
                    for ext in [".jpg", ".jpeg", ".png", ".bmp"]
                ):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragMoveEvent(self, event):
        """处理拖拽移动事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """处理拖拽释放事件"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                if any(
                    file_path.lower().endswith(ext)
                    for ext in [".jpg", ".jpeg", ".png", ".bmp"]
                ):
                    # 更新父窗口的图片路径
                    parent = self.parent()
                    while parent and not isinstance(parent, EmbyPosterCrop):
                        parent = parent.parent()
                    if parent:
                        parent.image_path = file_path

                    # 设置图片
                    self.set_image(file_path)
                    event.acceptProposedAction()
                    return
        event.ignore()

    def set_image(self, image_path):
        """加载图片并保存原始尺寸信息"""
        # 首先读取图片信息
        reader = QImageReader(image_path)
        reader.setAutoTransform(True)

        # 保存原始尺寸
        self.original_size = reader.size()
        if self.original_size.isValid():
            self.original_info_updated.emit(
                self.original_size.width(), self.original_size.height()
            )
            # print(
            #     f"原始图片尺寸: {self.original_size.width()}x{self.original_size.height()}"
            # )

        # 创建显示用的缩放版本
        self.pixmap = QPixmap(image_path)
        if not self.pixmap.isNull():
            self.scaled_pixmap = self.pixmap.scaled(
                800, 538, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )

            # 计算缩放比例
            self.scale_factor_x = (
                self.original_size.width() / self.scaled_pixmap.width()
            )
            self.scale_factor_y = (
                self.original_size.height() / self.scaled_pixmap.height()
            )

            # 更新偏移量
            self.offset_x = (800 - self.scaled_pixmap.width()) // 2
            self.offset_y = (538 - self.scaled_pixmap.height()) // 2

            # 更新方向标志
            self.image_is_horizontal = (
                self.original_size.width() > self.original_size.height()
            )

            # 初始化裁剪框
            self.initialize_crop_rect()
            self.update()

    def initialize_crop_rect(self):
        """初始化裁剪框，严格保持2:3比例"""
        if not self.scaled_pixmap:
            return

        img_width = self.scaled_pixmap.width()
        img_height = self.scaled_pixmap.height()

        # 严格的2:3比例
        TARGET_RATIO = 2 / 3  # width:height = 2:3

        if self.image_is_horizontal:
            # 横向图片：以高度为基准
            crop_height = img_height
            crop_width = int(crop_height * TARGET_RATIO)  # 保持2:3比例
        else:
            # 竖向图片：以宽度为基准
            crop_width = img_width
            crop_height = int(crop_width / TARGET_RATIO)

            # 如果高度超出，则重新以高度为基准
            if crop_height > img_height:
                crop_height = img_height
                crop_width = int(crop_height * TARGET_RATIO)

        # 居中放置裁剪框
        x = self.offset_x + (img_width - crop_width) // 2
        y = self.offset_y + (img_height - crop_height) // 2

        actual_ratio = crop_height / crop_width
        # print(f"裁剪框初始化: {crop_width}x{crop_height}, 比例: {actual_ratio:.3f}")

        self.crop_rect = QRect(x, y, crop_width, crop_height)
        self.crop_info_updated.emit(self.crop_rect)

    def paintEvent(self, event):
        super().paintEvent(event)

        if self.scaled_pixmap and not self.scaled_pixmap.isNull():
            painter = QPainter(self)

            # 绘制图片
            painter.drawPixmap(self.offset_x, self.offset_y, self.scaled_pixmap)

            # 使用红色粗线绘制裁剪框
            if self.crop_rect:
                pen = QPen(Qt.red)
                pen.setWidth(3)  # 设置线宽为3像素
                painter.setPen(pen)
                painter.drawRect(self.crop_rect)

                # 使用相同的红色绘制三分线，但线条细一些
                pen.setWidth(1)
                pen.setStyle(Qt.DashLine)
                painter.setPen(pen)

                # 横向三分线
                for i in range(1, 3):
                    y = self.crop_rect.top() + i * self.crop_rect.height() // 3
                    painter.drawLine(
                        self.crop_rect.left(), y, self.crop_rect.right(), y
                    )

                # 纵向三分线
                for i in range(1, 3):
                    x = self.crop_rect.left() + i * self.crop_rect.width() // 3
                    painter.drawLine(
                        x, self.crop_rect.top(), x, self.crop_rect.bottom()
                    )

    def mousePressEvent(self, event):
        if (
            event.button() == Qt.LeftButton
            and self.crop_rect
            and self.crop_rect.contains(event.pos())
        ):
            self.dragging = True
            self.drag_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        """改进的鼠标移动处理，支持根据图片实际方向移动"""
        if not self.crop_rect or not self.scaled_pixmap:
            return

        # 更新光标
        if self.crop_rect.contains(event.pos()):
            self.setCursor(
                Qt.OpenHandCursor if not self.dragging else Qt.ClosedHandCursor
            )
        else:
            self.setCursor(Qt.ArrowCursor)

        # 处理拖动
        if self.dragging and self.drag_start:
            # 获取水平和垂直方向的移动距离
            delta_x = event.pos().x() - self.drag_start.x()
            delta_y = event.pos().y() - self.drag_start.y()
            new_rect = QRect(self.crop_rect)

            # 根据图片实际方向决定移动方式
            if self.image_is_horizontal:
                # 横向图片只允许水平移动
                new_rect.moveLeft(self.crop_rect.left() + delta_x)

                # 检查水平边界
                if new_rect.left() < self.offset_x:
                    new_rect.moveLeft(self.offset_x)
                elif new_rect.right() > self.offset_x + self.scaled_pixmap.width():
                    new_rect.moveRight(self.offset_x + self.scaled_pixmap.width())

                # 保持垂直位置不变
                new_rect.moveTop(self.crop_rect.top())
            else:
                # 竖向图片只允许垂直移动
                new_rect.moveTop(self.crop_rect.top() + delta_y)

                # 检查垂直边界
                if new_rect.top() < self.offset_y:
                    new_rect.moveTop(self.offset_y)
                elif new_rect.bottom() > self.offset_y + self.scaled_pixmap.height():
                    new_rect.moveBottom(self.offset_y + self.scaled_pixmap.height())

                # 保持水平位置不变
                new_rect.moveLeft(self.crop_rect.left())

            self.crop_rect = new_rect
            self.drag_start = event.pos()
            self.crop_info_updated.emit(self.crop_rect)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = False
            if self.crop_rect and self.crop_rect.contains(event.pos()):
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.ArrowCursor)

    def update_scaled_pixmap(self):
        """更新缩放后的图片显示"""
        try:
            if not self.pixmap or self.pixmap.isNull():
                return

            # 计算保持纵横比的缩放尺寸
            scaled_size = self.pixmap.size()
            scaled_size.scale(800, 538, Qt.KeepAspectRatio)

            # 创建新的缩放图片
            self.scaled_pixmap = self.pixmap.scaled(
                scaled_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )

            # 计算偏移以居中显示
            self.offset_x = (800 - self.scaled_pixmap.width()) // 2
            self.offset_y = (538 - self.scaled_pixmap.height()) // 2

            # 初始化或更新裁剪框
            self.initialize_crop_rect()

            # 触发重绘
            self.update()

        except Exception as e:
            print(f"更新缩放图片时出错: {str(e)}")

    def get_crop_coordinates(self):
        """获取基于原始分辨率的裁剪坐标"""
        if not self.pixmap or not self.scaled_pixmap or not self.crop_rect:
            return None

        try:
            # 计算显示图片上的相对位置
            relative_x = self.crop_rect.x() - self.offset_x
            relative_y = self.crop_rect.y() - self.offset_y

            # 转换为原始分辨率下的坐标
            if self.rotation_angle == 0:
                original_x = int(relative_x * self.scale_factor_x)
                original_y = int(relative_y * self.scale_factor_y)
                original_width = int(self.crop_rect.width() * self.scale_factor_x)
                original_height = int(self.crop_rect.height() * self.scale_factor_y)
            else:
                # 处理旋转情况
                rotated_size = self.original_size
                if self.rotation_angle in [90, 270]:
                    rotated_size = QSize(
                        self.original_size.height(), self.original_size.width()
                    )

                # 根据旋转角度计算实际坐标
                if self.rotation_angle == 90:
                    original_x = int(relative_y * self.scale_factor_x)
                    original_y = int(
                        (
                            self.scaled_pixmap.width()
                            - relative_x
                            - self.crop_rect.width()
                        )
                        * self.scale_factor_y
                    )
                    original_width = int(self.crop_rect.height() * self.scale_factor_x)
                    original_height = int(self.crop_rect.width() * self.scale_factor_y)
                elif self.rotation_angle == 270:
                    original_x = int(
                        (
                            self.scaled_pixmap.height()
                            - relative_y
                            - self.crop_rect.height()
                        )
                        * self.scale_factor_x
                    )
                    original_y = int(relative_x * self.scale_factor_y)
                    original_width = int(self.crop_rect.height() * self.scale_factor_x)
                    original_height = int(self.crop_rect.width() * self.scale_factor_y)
                elif self.rotation_angle == 180:
                    original_x = int(
                        (
                            self.scaled_pixmap.width()
                            - relative_x
                            - self.crop_rect.width()
                        )
                        * self.scale_factor_x
                    )
                    original_y = int(
                        (
                            self.scaled_pixmap.height()
                            - relative_y
                            - self.crop_rect.height()
                        )
                        * self.scale_factor_y
                    )
                    original_width = int(self.crop_rect.width() * self.scale_factor_x)
                    original_height = int(self.crop_rect.height() * self.scale_factor_y)

            # 确保高宽比严格保持2:3
            actual_ratio = original_height / original_width
            if abs(actual_ratio - 1.5) > 0.01:  # 允许0.01的误差
                # print(f"警告：裁剪比例不准确 ({actual_ratio:.3f})")
                original_height = int(original_width * 1.5)

            # print(
            #     f"最终裁剪坐标: {original_x}x{original_y}, "
            #     f"尺寸: {original_width}x{original_height}, "
            #     f"比例: {original_height/original_width:.3f}"
            # )

            return (original_x, original_y, original_width, original_height)

        except Exception as e:
            print(f"计算裁剪坐标时出错: {str(e)}")
            import traceback

            traceback.print_exc()
            return None


class WatermarkConfig:
    """水印配置管理"""

    def __init__(self):
        # 水印图片路径（相对于脚本所在目录的img文件夹）
        self.watermark_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "img"
        )

    def get_watermark_path(self, mark_type):
        """获取水印图片路径"""
        mark_files = {
            "sub": "sub.png",
            "youma": "youma.png",
            "wuma": "wuma.png",
            "leak": "leak.png",
            "umr": "umr.png",
        }
        return os.path.join(self.watermark_path, mark_files.get(mark_type, ""))

    def apply_watermark(self, image, marks):
        if not marks:
            return image

        if image.format() != QImage.Format_RGB32:
            image = image.convertToFormat(QImage.Format_RGB32)

        painter = QPainter(image)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        positions = {
            "sub": (0, 0),
            "youma": (None, 0),
            "wuma": (None, 0),
            "leak": (None, 0),
            "umr": (None, 0),
        }

        for mark_type in marks:
            mark_path = self.get_watermark_path(mark_type)
            if os.path.exists(mark_path):
                watermark = QImage(mark_path)
                if not watermark.isNull():
                    # 水印高度为被加水印图片高度的10/40
                    mark_height = int(image.height() * 10 / 40)
                    # 根据原水印图片的宽高比计算水印宽度
                    mark_width = int(
                        mark_height * watermark.width() / watermark.height()
                    )

                    watermark = watermark.scaled(
                        mark_width,
                        mark_height,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )

                    pos = positions.get(mark_type, (0, 0))
                    x = pos[0] if pos[0] is not None else (image.width() - mark_width)
                    painter.drawImage(x, pos[1], watermark)

        painter.end()
        return image


class EmbyPosterCrop(QDialog):
    def __init__(self, parent=None, nfo_base_name=None):
        super().__init__(parent)
        self.nfo_base_name = nfo_base_name
        self.setWindowTitle("大锤 EMBY海报裁剪工具 v9.5.9")
        self.setMinimumSize(1200, 640)

        # 设置窗口图标
        try:
            icon_path = get_resource_path("cg_crop.ico")
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except Exception as e:
            print(f"设置窗口图标失败: {str(e)}")

        self.setup_ui()
        self.setup_styles()

    def setup_styles(self):
        self.setStyleSheet(
            """
            QDialog {
                background-color: #F9FAFB;
            }
            QRadioButton, QCheckBox {
                color: #4B5563;
                font-size: 13px;
                padding: 4px;
            }
            QRadioButton:hover, QCheckBox:hover {
                background-color: #F3F4F6;
                border-radius: 4px;
            }
        """
        )

    def setup_ui(self):
        """设置UI界面"""
        self.setWindowTitle("封面图片裁剪")
        self.setFixedSize(1200, 620)  # 固定窗口大小

        # 主布局
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(10, 5, 10, 5)  # 设置布局的边距(左,上,右,下)

        # 左侧：图片显示区域
        self.image_label = CropLabel()

        # 右侧面板
        right_panel = QWidget()
        right_panel.setFixedWidth(300)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(15)

        # 1. 打开图片按钮
        self.open_button = QPushButton("打开图片")
        self.open_button.setFixedHeight(40)
        self.open_button.setStyleSheet(
            """
            QPushButton {
                background-color: #3B82F6;
                color: white;
                border-radius: 4px;
                font-size: 14px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #2563EB;
            }
        """
        )
        self.open_button.clicked.connect(self.open_image)
        right_layout.addWidget(self.open_button)

        # 2. 图片信息显示
        info_group = QGroupBox("图片信息")
        info_group.setFixedHeight(168)  # 设置固定高度
        info_group.setStyleSheet(
            """
            QGroupBox {
                background-color: white;
                border: 1px solid #E5E7EB;
                border-radius: 6px;
                font-weight: bold;
                margin-top: 16px;
                padding: 8px;
                color: #374151;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 5px;
            }
            QLabel {
                font-weight: normal;
                color: #4B5563;
                font-size: 13px;
            }
        """
        )
        info_layout = QVBoxLayout(info_group)
        info_layout.setSpacing(1)  # 设置布局的整体间距
        info_layout.setContentsMargins(10, 3, 5, 3)  # 设置布局的边距(左,上,右,下)

        # 原图尺寸
        original_size_label = QLabel("原图尺寸:")
        self.original_size = QLabel("800, 538")
        info_layout.addWidget(
            original_size_label,
        )
        info_layout.addWidget(self.original_size)
        info_layout.addSpacing(5)  # 增加间距

        # 裁剪尺寸
        crop_size_label = QLabel("裁剪尺寸:")
        self.crop_size = QLabel("377, 538")
        info_layout.addWidget(crop_size_label)
        info_layout.addWidget(self.crop_size)
        info_layout.addSpacing(5)  # 增加间距

        # 裁剪位置
        crop_pos_label = QLabel("裁剪位置:")
        self.crop_pos = QLabel("421, 0, 800, 538")
        info_layout.addWidget(crop_pos_label)
        info_layout.addWidget(self.crop_pos)
        info_layout.addSpacing(5)  # 增加间距

        # 高宽比例
        ratio_label = QLabel("高宽比例:")
        self.ratio = QLabel("1.42")
        info_layout.addWidget(ratio_label)
        info_layout.addWidget(self.ratio)
        info_layout.addSpacing(5)  # 增加间距

        # 在底部添加弹性空间
        info_layout.addStretch()

        right_layout.addWidget(info_group)

        # 连接信号
        self.image_label.original_info_updated.connect(self.update_original_info)
        self.image_label.crop_info_updated.connect(self.update_crop_info)
        # 3. 水印设置
        watermark_group = QGroupBox("添加水印:")
        watermark_layout = QVBoxLayout(watermark_group)

        # 水印设置组使用相同样式
        watermark_group.setStyleSheet(info_group.styleSheet())

        # 字幕水印
        self.sub_check = QCheckBox("字幕")
        watermark_layout.addWidget(self.sub_check)

        # 分类水印
        categories_layout = QVBoxLayout()
        self.mark_group = QButtonGroup()

        # 第一行：有码、无码、流出
        row1_layout = QHBoxLayout()
        for text, value in [("有码", "youma"), ("无码", "wuma"), ("流出", "leak")]:
            radio = QRadioButton(text)
            radio.setProperty("value", value)
            self.mark_group.addButton(radio)
            row1_layout.addWidget(radio)
        categories_layout.addLayout(row1_layout)

        # 第二行：破解、无
        row2_layout = QHBoxLayout()
        for text, value in [("破解", "umr"), ("无", "none")]:
            radio = QRadioButton(text)
            radio.setProperty("value", value)
            self.mark_group.addButton(radio)
            row2_layout.addWidget(radio)
        categories_layout.addLayout(row2_layout)

        # 第三行：4K、8K、无
        resolution_layout = QHBoxLayout()
        resolution_group = QButtonGroup()
        for text in ["4K", "8K", "无"]:
            radio = QRadioButton(text)
            resolution_group.addButton(radio)
            resolution_layout.addWidget(radio)
        categories_layout.addLayout(resolution_layout)

        watermark_layout.addLayout(categories_layout)
        right_layout.addWidget(watermark_group)

        # 添加伸缩器
        # right_layout.addStretch()  # 设置较小的拉伸因子

        # 4. 底部按钮
        buttons_layout = QVBoxLayout()

        # 裁剪并关闭按钮
        self.cut_close_button = QPushButton("裁剪并关闭")
        self.cut_close_button.setFixedHeight(40)
        self.cut_close_button.setStyleSheet(
            """
            QPushButton {
                background-color: #10B981;
                color: white;
                border-radius: 4px;
                font-size: 14px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #059669;
            }
        """
        )
        self.cut_close_button.clicked.connect(self.cut_and_close)
        buttons_layout.addWidget(self.cut_close_button)

        # 裁剪和关闭按钮
        bottom_buttons = QHBoxLayout()
        for text, slot in [("裁剪", self.cut_image), ("关闭", self.close)]:
            btn = QPushButton(text)
            btn.setFixedSize(133, 40)
            btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #F3F4F6;
                    color: #374151;
                    border: 2px solid #D1D5DB;  /* 添加明显的边框 */
                    border-radius: 4px;
                    font-size: 14px;
                }
                QPushButton:hover {
                    background-color: #E5E7EB;
                    border-color: #9CA3AF;  /* 鼠标悬停时边框颜色加深 */
                }
                QPushButton:pressed {
                    background-color: #D1D5DB;
                    border-color: #6B7280;  /* 按下时边框颜色更深 */
                }
                """
            )
            btn.clicked.connect(slot)
            bottom_buttons.addWidget(btn)

        buttons_layout.addLayout(bottom_buttons)
        right_layout.addLayout(buttons_layout)

        # 创建左侧布局容器
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setSpacing(10)

        # 添加图片显示区域
        left_layout.addWidget(self.image_label)

        # 创建旋转按钮容器
        rotation_container = QWidget()
        rotation_layout = QHBoxLayout(rotation_container)
        rotation_layout.setContentsMargins(0, 0, 0, 0)

        # 创建旋转按钮
        self.rotate_left_btn = QPushButton("↶ 向左旋转")
        self.rotate_right_btn = QPushButton("↷ 向右旋转")

        # 设置旋转按钮样式
        rotation_button_style = """
            QPushButton {
                background-color: #F3F4F6;
                color: #374151;
                border: 1px solid #D1D5DB;
                border-radius: 4px;
                padding: 8px 15px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #E5E7EB;
                border-color: #9CA3AF;
            }
            QPushButton:pressed {
                background-color: #D1D5DB;
                border-color: #6B7280;
            }
        """
        self.rotate_left_btn.setStyleSheet(rotation_button_style)
        self.rotate_right_btn.setStyleSheet(rotation_button_style)

        # 添加按钮到布局
        rotation_layout.addWidget(self.rotate_left_btn)
        rotation_layout.addWidget(self.rotate_right_btn)

        # 连接旋转按钮信号
        self.rotate_left_btn.clicked.connect(lambda: self.rotate_image(-90))
        self.rotate_right_btn.clicked.connect(lambda: self.rotate_image(90))

        # 添加旋转按钮容器到左侧布局
        left_layout.addWidget(rotation_container)

        # 将左侧容器添加到主布局
        main_layout.addWidget(left_container)
        main_layout.addWidget(right_panel)
        # main_layout.addWidget(self.image_label)

    def rotate_image(self, angle):
        """旋转图片

        Args:
            angle: 旋转角度（90 或 -90）
        """
        if hasattr(self, "image_label"):
            self.image_label.rotate_image(angle)

    def update_original_info(self, width, height):
        """更新原图信息"""
        self.original_size.setText(f"{width}, {height}")

    def update_crop_info(self, crop_rect):
        """更新裁剪信息"""
        if crop_rect:
            # 获取实际裁剪框相对于原图的坐标
            coords = self.image_label.get_crop_coordinates()
            if coords:
                x, y, w, h = coords
                self.crop_size.setText(f"{w}, {h}")
                self.crop_pos.setText(f"{x}, {y}, {x+w}, {y+h}")
                ratio = h / w
                self.ratio.setText(f"{ratio:.2f}")

    def open_image(self):
        """打开图片"""
        start_dir = ""
        if hasattr(self, "image_path"):
            start_dir = os.path.dirname(self.image_path)

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择图片",
            start_dir,
            "图片文件 (*.jpg *.jpeg *.png *.bmp);;所有文件 (*.*)",
        )

        if file_path:
            try:
                self.image_path = file_path
                self.image_label.set_image(file_path)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"无法加载图片：{str(e)}")

    def cut_image(self):
        """以原始分辨率裁剪图片并保存"""
        if not hasattr(self, "image_path"):
            QMessageBox.warning(self, "警告", "请先打开图片")
            return False

        try:
            # 获取裁剪坐标（基于原始分辨率）
            coords = self.image_label.get_crop_coordinates()
            if not coords:
                raise ValueError("无效的裁剪区域")

            x, y, w, h = coords
            # print(f"裁剪参数: x={x}, y={y}, w={w}, h={h}")

            # 验证裁剪比例
            crop_ratio = h / w
            if abs(crop_ratio - 1.5) > 0.01:  # 允许0.01的误差
                raise ValueError(f"裁剪比例不正确: {crop_ratio:.3f}, 应为1.5")

            # 加载原始图片
            image = QImage(self.image_path)
            if image.isNull():
                raise ValueError("无法读取原图")

            # print(f"原图尺寸: {image.width()}x{image.height()}")

            # 如果有旋转，先旋转图片
            if self.image_label.rotation_angle != 0:
                transform = QTransform().rotate(self.image_label.rotation_angle)
                image = image.transformed(transform, Qt.SmoothTransformation)
                # print(f"旋转后尺寸: {image.width()}x{image.height()}")

            # 执行裁剪
            cropped = image.copy(x, y, w, h)
            # print(
            #     f"裁剪后尺寸: {cropped.width()}x{cropped.height()}, "
            #     f"比例: {cropped.height()/cropped.width():.3f}"
            # )

            # 验证最终尺寸
            final_ratio = cropped.height() / cropped.width()
            if abs(final_ratio - 1.5) > 0.01:
                raise ValueError(f"最终比例不正确: {final_ratio:.3f}, 应为1.5")

            # 获取水印设置
            marks = []
            if self.sub_check.isChecked():
                marks.append("sub")
            checked_mark = self.mark_group.checkedButton()
            if checked_mark and checked_mark.property("value") != "none":
                marks.append(checked_mark.property("value"))

            # 应用水印
            if marks:
                watermark_config = WatermarkConfig()
                cropped = watermark_config.apply_watermark(cropped, marks)
                image = watermark_config.apply_watermark(image, marks)

            # 生成保存路径
            directory = os.path.dirname(self.image_path)
            filename = os.path.basename(self.image_path)
            base_name = filename.replace("-fanart.jpg", "")
            if base_name == filename:
                base_name = os.path.splitext(filename)[0]

            # 保存图片
            poster_path = os.path.join(directory, f"{base_name}-poster.jpg")
            thumb_path = os.path.join(directory, f"{base_name}-thumb.jpg")

            # print(f"保存poster到: {poster_path}")
            if not cropped.save(poster_path, "JPEG", quality=95):
                raise ValueError("保存poster失败")

            # print(f"保存thumb到: {thumb_path}")
            if not image.save(thumb_path, "JPEG", quality=95):
                raise ValueError("保存thumb失败")

            QMessageBox.information(
                self, "成功", f"裁剪成功！\n保存位置：{poster_path}"
            )
            return True

        except Exception as e:
            print(f"裁剪失败: {str(e)}")
            import traceback

            traceback.print_exc()
            QMessageBox.critical(self, "错误", f"裁剪失败：{str(e)}")
            return False

    def cut_and_close(self):
        """裁剪并关闭窗口"""
        if self.cut_image():
            self.accept()  # 使用accept而不是close，这样可以返回QDialog.Accepted

    def load_initial_image(self, image_path):
        """支持外部调用直接加载图片"""
        if image_path and os.path.exists(image_path):
            self.image_path = image_path
            self.image_label.set_image(image_path)


if __name__ == "__main__":
    import argparse

    try:
        from PyQt5 import QtWidgets  # 移到这里，因为只在主入口使用

        # 创建QApplication实例
        app = QtWidgets.QApplication(sys.argv)
        app.setStyle("Fusion")

        # 设置应用程序图标
        try:
            icon_path = get_resource_path("cg_crop.ico")
            if os.path.exists(icon_path):
                app.setWindowIcon(QIcon(icon_path))
        except Exception as e:
            print(f"设置窗口图标失败: {str(e)}")

        # 创建参数解析器
        parser = argparse.ArgumentParser(description="图片裁剪工具")
        parser.add_argument("--image", help="要处理的图片路径")
        parser.add_argument("--nfo-name", help="NFO文件基础名称")
        parser.add_argument("--subtitle", action="store_true", help="是否添加字幕水印")
        parser.add_argument(
            "--mark-type",
            choices=["none", "umr", "leak", "wuma", "youma"],
            default="none",
            help="水印类型",
        )

        # 解析命令行参数
        args = parser.parse_args()

        # 创建主窗口
        window = EmbyPosterCrop(nfo_base_name=args.nfo_name)

        # 如果提供了图片路径且文件存在，则加载图片
        if args.image and os.path.exists(args.image):
            window.load_initial_image(args.image)

            # 设置水印选项
            if args.subtitle:
                window.sub_check.setChecked(True)

            # 设置水印类型
            if args.mark_type != "none":
                for button in window.mark_group.buttons():
                    if button.property("value") == args.mark_type:
                        button.setChecked(True)
                        break

        window.show()
        sys.exit(app.exec_())

    except Exception as e:
        print(f"启动失败：{str(e)}")
        sys.exit(1)
