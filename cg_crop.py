import sys
import os
from PyQt5 import QtWidgets
from PyQt5.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QFileDialog, QGroupBox, QLabel, QMessageBox, QRadioButton, QVBoxLayout, QHBoxLayout, 
                           QPushButton, QWidget)
from PyQt5.QtCore import Qt, QRect, pyqtSignal
from PyQt5.QtGui import QIcon, QImage, QPixmap, QPainter, QPen

def get_resource_path(relative_path):
    """获取资源文件的绝对路径"""
    if getattr(sys, 'frozen', False):
        # 如果是打包后的exe
        base_path = sys._MEIPASS
    else:
        # 如果是直接运行的py脚本
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

class CropLabel(QLabel):
    original_info_updated = pyqtSignal(int, int)  # 发送原图宽度和高度
    crop_info_updated = pyqtSignal(QRect)  # 发送裁剪框信息

    def __init__(self, parent=None):
        super().__init__(parent)
        # 固定显示区域大小
        self.setFixedSize(800, 538)
        self.setStyleSheet("""
            QLabel {
                background-color: #f0f0f0;
                border: 2px solid #CCCCCC;  /* 添加边框 */
            }
        """)
        self.setAlignment(Qt.AlignCenter)
        self.setText("请点击右上角的'打开图片'按钮")
        
        self.pixmap = None
        self.scaled_pixmap = None
        self.offset_x = 0
        self.offset_y = 0
        self.crop_rect = None
        self.dragging = False
        self.drag_start = None
        self.setMouseTracking(True)

    def set_image(self, image_path):
        self.pixmap = QPixmap(image_path)
        if not self.pixmap.isNull():
            # 发送原图信息
            self.original_info_updated.emit(self.pixmap.width(), self.pixmap.height())

            # 固定缩放到 800×538
            self.scaled_pixmap = self.pixmap.scaled(800, 538, Qt.KeepAspectRatio, Qt.SmoothTransformation)

            # 计算居中偏移
            self.offset_x = (800 - self.scaled_pixmap.width()) // 2
            self.offset_y = (538 - self.scaled_pixmap.height()) // 2

            # 初始化固定尺寸的裁剪框
            self.initialize_crop_rect()
            self.update()  

    def initialize_crop_rect(self):
        if self.scaled_pixmap:
            # 获取图片实际显示尺寸
            img_width = self.scaled_pixmap.width()
            img_height = self.scaled_pixmap.height()
            
            # 计算裁剪框高度，不超过图片高度和538
            crop_height = min(538, img_height)
            # 按2:3比例计算宽度
            crop_width = int(crop_height * 0.7)
            
            # 确保裁剪框不超出图片范围
            if crop_width > img_width:
                crop_width = img_width
                crop_height = int(crop_width * 3 / 2)
                
            # 水平居中放置
            x = self.offset_x + (img_width - crop_width) // 2
            y = self.offset_y + (img_height - crop_height) // 2
            
            self.crop_rect = QRect(x, y, crop_width, crop_height)
            # 发送初始裁剪框信息
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
                        self.crop_rect.left(), y,
                        self.crop_rect.right(), y
                    )
                
                # 纵向三分线
                for i in range(1, 3):
                    x = self.crop_rect.left() + i * self.crop_rect.width() // 3
                    painter.drawLine(
                        x, self.crop_rect.top(),
                        x, self.crop_rect.bottom()
                    )

    def mousePressEvent(self, event):
        if (event.button() == Qt.LeftButton and self.crop_rect 
            and self.crop_rect.contains(event.pos())):
            self.dragging = True
            self.drag_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if not self.crop_rect:
            return
            
        # 更新光标
        if self.crop_rect.contains(event.pos()):
            self.setCursor(Qt.OpenHandCursor if not self.dragging else Qt.ClosedHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
            
        # 处理拖动
        if self.dragging and self.drag_start:
            delta = event.pos() - self.drag_start
            new_rect = self.crop_rect.translated(delta)
            
            # 限制在图片范围内
            if self.scaled_pixmap:
                if new_rect.left() < self.offset_x:
                    new_rect.moveLeft(self.offset_x)
                if new_rect.right() > self.offset_x + self.scaled_pixmap.width():
                    new_rect.moveRight(self.offset_x + self.scaled_pixmap.width())
                if new_rect.top() < self.offset_y:
                    new_rect.moveTop(self.offset_y)
                if new_rect.bottom() > self.offset_y + self.scaled_pixmap.height():
                    new_rect.moveBottom(self.offset_y + self.scaled_pixmap.height())
                
                self.crop_rect = new_rect
                self.drag_start = event.pos()
                # 发送裁剪框信息
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
        """更新缩放后的图片"""
        if self.pixmap:
            self.scaled_pixmap = self.pixmap.scaled(
                800, 538,
                Qt.KeepAspectRatio, 
                Qt.SmoothTransformation
            )
            self.offset_x = (800 - self.scaled_pixmap.width()) // 2
            self.offset_y = (538 - self.scaled_pixmap.height()) // 2
            self.initialize_crop_rect()
            self.update()

    def get_crop_coordinates(self):
        """获取相对于原图的裁剪坐标"""
        if not self.pixmap or not self.scaled_pixmap or not self.crop_rect:
            return None
            
        # 计算缩放比例
        scale_x = self.pixmap.width() / self.scaled_pixmap.width()
        scale_y = self.pixmap.height() / self.scaled_pixmap.height()
        
        # 转换坐标到原图比例
        x = (self.crop_rect.x() - self.offset_x) * scale_x
        y = (self.crop_rect.y() - self.offset_y) * scale_y
        w = self.crop_rect.width() * scale_x
        h = self.crop_rect.height() * scale_y
        
        # 确保坐标不超出原图范围
        x = max(0, int(x))
        y = max(0, int(y))
        w = min(int(w), self.pixmap.width() - x)
        h = min(int(h), self.pixmap.height() - y)
        
        return (x, y, w, h)

class WatermarkConfig:
    """水印配置管理"""
    def __init__(self):
        # 水印图片路径（相对于脚本所在目录的img文件夹）
        self.watermark_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'img')

    def get_watermark_path(self, mark_type):
        """获取水印图片路径"""
        mark_files = {
            'sub': 'sub.png',
            'youma': 'youma.png',
            'wuma': 'wuma.png',
            'leak': 'leak.png',
            'umr': 'umr.png'
        }
        return os.path.join(self.watermark_path, mark_files.get(mark_type, ''))

    def apply_watermark(self, image, marks):
        if not marks:
            return image
        
        if image.format() != QImage.Format_RGB32:
            image = image.convertToFormat(QImage.Format_RGB32)

        painter = QPainter(image)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        
        positions = {
            'sub': (0, 0),
            'youma': (None, 0),
            'wuma': (None, 0),
            'leak': (None, 0),
            'umr': (None, 0)
        }
        
        for mark_type in marks:
            mark_path = self.get_watermark_path(mark_type)
            if os.path.exists(mark_path):
                watermark = QImage(mark_path)
                if not watermark.isNull():
                    # 水印高度为被加水印图片高度的10/40
                    mark_height = int(image.height() * 10 / 40)
                    # 根据原水印图片的宽高比计算水印宽度
                    mark_width = int(mark_height * watermark.width() / watermark.height())
                    
                    watermark = watermark.scaled(
                        mark_width, mark_height,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
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
        self.setWindowTitle("EMBY海报裁剪工具 v1.0.0")
        self.setMinimumSize(1200, 640)
        
        # 设置窗口图标
        try:
            icon_path = get_resource_path('chuizi.ico')
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except Exception as e:
            print(f"设置窗口图标失败: {str(e)}")
            
        self.setup_ui()

    def setup_ui(self):
        """设置UI界面"""
        self.setWindowTitle("封面图片裁剪")
        self.setFixedSize(1200, 640)  # 固定窗口大小
        
        # 主布局
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(10, 5, 10, 5) # 设置布局的边距(左,上,右,下)
        
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
        self.open_button.setStyleSheet("""
            QPushButton {
                background-color: #CCCCCC;
                color: black;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #4C6EFF;
                color: white;
            }
        """)
        self.open_button.clicked.connect(self.open_image)
        right_layout.addWidget(self.open_button)
        
        # 2. 图片信息显示
        info_group = QGroupBox("图片信息")
        info_group.setFixedHeight(233)  # 设置固定高度
        info_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #CCCCCC;
                border-radius: 5px;
                margin-top: 16px;
                padding: 5px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 5px;
                padding: 0 5px;
            }
            QLabel {
                font-weight: normal;
                font-size: 9pt;
            }
        """)
        info_layout = QVBoxLayout(info_group)
        info_layout.setSpacing(1)  # 设置布局的整体间距
        info_layout.setContentsMargins(10, 3, 5, 3)  # 设置布局的边距(左,上,右,下)

        # 原图尺寸
        original_size_label = QLabel("原图尺寸:")
        self.original_size = QLabel("800, 538")
        info_layout.addWidget(original_size_label, )
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
        right_layout.addStretch()
        
        # 4. 底部按钮
        buttons_layout = QVBoxLayout()
        
        # 裁剪并关闭按钮
        self.cut_close_button = QPushButton("裁剪并关闭")
        self.cut_close_button.setFixedHeight(40)
        self.cut_close_button.setStyleSheet("""
            QPushButton {
                background-color: #5E95CC;
                color: white;
                border-radius: 4px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #4C7EBF;
            }
        """)
        self.cut_close_button.clicked.connect(self.cut_and_close)
        buttons_layout.addWidget(self.cut_close_button)
        
        # 裁剪和关闭按钮
        bottom_buttons = QHBoxLayout()
        for text, slot in [("裁剪", self.cut_image), ("关闭", self.close)]:
            btn = QPushButton(text)
            btn.setFixedSize(133, 40)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #CCCCCC;
                    color: black;
                    border-radius: 4px;
                    font-size: 14px;
                }
                QPushButton:hover {
                    background-color: #4C6EFF;
                    color: white;
                }
            """)
            btn.clicked.connect(slot)
            bottom_buttons.addWidget(btn)
        
        buttons_layout.addLayout(bottom_buttons)
        right_layout.addLayout(buttons_layout)
        
        # 将左右两侧添加到主布局
        main_layout.addWidget(self.image_label)
        main_layout.addWidget(right_panel)

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
        if hasattr(self, 'image_path'):
            start_dir = os.path.dirname(self.image_path)
            
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", start_dir,
            "图片文件 (*.jpg *.jpeg *.png *.bmp);;所有文件 (*.*)"
        )
        
        if file_path:
            try:
                self.image_path = file_path
                self.image_label.set_image(file_path)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"无法加载图片：{str(e)}")

    def cut_image(self):
        """裁剪图片并保存"""
        if not hasattr(self, 'image_path'):
            QMessageBox.warning(self, "警告", "请先打开图片")
            return False
            
        try:
            # 获取裁剪坐标
            coords = self.image_label.get_crop_coordinates()
            if not coords:
                raise ValueError("无效的裁剪区域")
            
            x, y, w, h = coords
                        
            # 获取水印设置并应用
            marks = []
            if self.sub_check.isChecked():
                marks.append('sub')
            checked_mark = self.mark_group.checkedButton()
            if checked_mark and checked_mark.property('value') != 'none':
                marks.append(checked_mark.property('value'))


            # 裁剪并应用水印到poster
            image = QImage(self.image_path)
            if image.isNull():
                raise ValueError("无法读取原图")
            cropped = image.copy(x, y, w, h)
            if marks:
                watermark_config = WatermarkConfig()
                cropped = watermark_config.apply_watermark(cropped, marks)
                # 同样应用水印到原图(thumb)
                image = watermark_config.apply_watermark(image, marks)

            # 生成新的文件名
            directory = os.path.dirname(self.image_path)
            filename = os.path.basename(self.image_path)

            # 替换文件名中的-fanart为-poster或-thumb
            base_name = filename.replace('-fanart.jpg', '')
            if base_name == filename:  # 如果没有-fanart后缀
                base_name = os.path.splitext(filename)[0]  # 移除扩展名
            
            # 保存poster
            poster_path = os.path.join(directory, f"{base_name}-poster.jpg")
            if not cropped.save(poster_path, "JPEG", quality=95):
                raise ValueError("保存poster失败")
            
            # 保存thumb
            thumb_path = os.path.join(directory, f"{base_name}-thumb.jpg")
            if not image.save(thumb_path, "JPEG", quality=95):
                raise ValueError("保存thumb失败")
            
            QMessageBox.information(self, "成功", f"裁剪成功！\n保存位置：{poster_path}")
            return True
            
        except Exception as e:
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

def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle('Fusion')
    
    # Set application icon
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'img', 'icon.ico')
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    
    window = EmbyPosterCrop()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()