import sys
import os
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QFileDialog, QLabel, QMainWindow, QCheckBox, QButtonGroup, QPushButton, QRadioButton, QSlider
from PyQt5.QtGui import QPainter, QPen, QPixmap, QFont
from PyQt5.QtCore import Qt
from PIL import Image

class ImageLabel(QtWidgets.QLabel):
    def __init__(self, parent=None):
        super(ImageLabel, self).__init__(parent)
        self.original_pixmap = None
        self.crop_rect = QtCore.QRect(210, 0, 381, 540)  # 初始矩形尺寸

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        if self.original_pixmap:
            # 缩放后的图像的最大宽度为 800 像素，高度为 540 像素，并且保持图像的纵横比
            painter.drawPixmap(self.rect(), self.original_pixmap.scaled(800, 540, QtCore.Qt.KeepAspectRatio))
        painter.setPen(QPen(QtCore.Qt.blue, 6))  # 使矩形框更粗
        painter.drawRect(self.crop_rect)

    def update_image(self, pixmap):
        self.original_pixmap = pixmap
        self.update()  # 触发重绘

    def update_rectangle_position(self, value):
        x_position = max(0, min(value, 800 - self.crop_rect.width()))
        self.crop_rect.moveLeft(x_position)
        self.update()  # 触发重绘

class Ui_Dialog_cut_poster(QMainWindow):
    def setupUi(self, Dialog_cut_poster):
        Dialog_cut_poster.setObjectName("Dialog_cut_poster")
        Dialog_cut_poster.resize(1130, 630)
        Dialog_cut_poster.setWindowTitle("封面裁剪工具")
        self.centralwidget = QtWidgets.QWidget(Dialog_cut_poster)
        self.centralwidget.setObjectName("centralwidget")

        self.widget_cutimage = ImageLabel(self.centralwidget)
        self.widget_cutimage.setGeometry(QtCore.QRect(10, 10, 800, 540))
        self.widget_cutimage.setStyleSheet("background-color: rgb(200, 200, 200);")

        # 创建并配置滑动条，滑动条的左上角坐标是 (20, 850)，宽度为 800 像素，高度为 20 像素
        self.slider = QSlider(QtCore.Qt.Horizontal, self.centralwidget)  # 创建一个水平方向的滑动条
        self.slider.setGeometry(20, 580, 800, 20)  # 设置滑动条的位置和大小（x, y, width, height）
        self.slider.setMinimum(0)  # 设置滑动条的最小值
        self.slider.setMaximum(420)  # 设置滑动条的最大值
        self.slider.setValue(210)  # 设置滑动条的初始值
        # 连接滑动条的值变化信号到更新裁剪矩形位置的方法
        self.slider.valueChanged.connect(self.widget_cutimage.update_rectangle_position)

        # 创建并配置控制面板，控制面板的左上角坐标是 (850, 20)，宽度为 220 像素，高度为 900 像素
        self.control_panel = QtWidgets.QWidget(self.centralwidget)
        self.control_panel.setGeometry(QtCore.QRect(850, 20, 250, 900))
        self.setup_control_panel()

        Dialog_cut_poster.setCentralWidget(self.centralwidget)
        QtCore.QMetaObject.connectSlotsByName(Dialog_cut_poster)

    def setup_control_panel(self):
        self.pushButton_open_pic = QPushButton("打开图片", self.control_panel)
        self.pushButton_open_pic.setGeometry(QtCore.QRect(20, 5, 200, 50))
        self.pushButton_open_pic.setFont(QFont("Arial", 12))
        self.pushButton_open_pic.clicked.connect(self.open_image)

        # 信息显示
        self.label_info_original_size = QLabel("原图尺寸:800 × 540", self.control_panel)
        self.label_info_original_size.setGeometry(QtCore.QRect(20, 80, 220, 30))
        self.label_info_original_size.setFont(QFont("Arial", 11))

        self.label_info_crop_size = QLabel("裁剪尺寸:381 × 540", self.control_panel)
        self.label_info_crop_size.setGeometry(QtCore.QRect(20, 120, 220, 30))
        self.label_info_crop_size.setFont(QFont("Arial", 11))

        self.label_info_aspect_ratio = QLabel("高宽比例:1.50", self.control_panel)
        self.label_info_aspect_ratio.setGeometry(QtCore.QRect(20, 160, 220, 30))
        self.label_info_aspect_ratio.setFont(QFont("Arial", 11))

        self.label_watermark = QLabel("添加水印:", self.control_panel)
        self.label_watermark.setFont(QFont("Arial", 13, QFont.Bold))
        self.label_watermark.setGeometry(QtCore.QRect(20, 200, 200, 30))

        self.checkBox_subtitle = QCheckBox("字幕", self.control_panel)
        self.checkBox_subtitle.setGeometry(QtCore.QRect(20, 240, 200, 30))
        self.checkBox_subtitle.setFont(QFont("Arial", 12))

        self.radio_group = QButtonGroup(self.control_panel)  # 使单选按钮互斥
        self.radio_buttons = []
        options = ["有码", "无码", "流出", "破解", "无"]
        for i, text in enumerate(options):
            radio = QRadioButton(text, self.control_panel)
            radio.setGeometry(QtCore.QRect(20, 270 + i * 30, 200, 30))
            radio.setFont(QFont("Arial", 12))
            self.radio_group.addButton(radio)
            self.radio_buttons.append(radio)
        self.radio_group.buttons()[4].setChecked(True)  # 默认选择“无”

        self.pushButton_cut_close = QPushButton("裁剪并关闭", self.control_panel)
        self.pushButton_cut_close.setGeometry(QtCore.QRect(20, 450, 200, 50))
        self.pushButton_cut_close.setFont(QFont("Arial", 12))
        self.pushButton_cut_close.clicked.connect(self.crop_and_close)

        self.pushButton_cut = QPushButton("裁剪", self.control_panel)
        self.pushButton_cut.setGeometry(QtCore.QRect(20, 520, 95, 50))
        self.pushButton_cut.setFont(QFont("Arial", 12))
        self.pushButton_cut.clicked.connect(self.crop_image)

        self.pushButton_close = QPushButton("关闭", self.control_panel)
        self.pushButton_close.setGeometry(QtCore.QRect(125, 520, 95, 50))
        self.pushButton_close.setFont(QFont("Arial", 12))
        self.pushButton_close.clicked.connect(self.close_app)

    # 配合NFO.Editor.py调用
    def load_image(self, image_path):
        pixmap = QPixmap(image_path)
        self.widget_cutimage.update_image(pixmap)
        self.label_info_original_size.setText(f"原图尺寸：\n{pixmap.width()} × {pixmap.height()}")

    def open_image(self):
        fileName, _ = QFileDialog.getOpenFileName(self, "Open Image", "", "Image Files (*.png *.jpg *.jpeg *.bmp)")
        if fileName:
            self.image_path = fileName
            pixmap = QPixmap(fileName)
            self.widget_cutimage.update_image(pixmap)
            self.label_info_original_size.setText(f"原图尺寸：\n{pixmap.width()} × {pixmap.height()}")

    def crop_and_close(self):
        self.crop_image()
        self.close_app()

    def crop_image(self):
        if not hasattr(self, 'image_path'):
            print("未加载任何图片")
            return

        print("开始裁剪图片...")
        # Load the original image
        original_image = Image.open(self.image_path)
        original_width, original_height = original_image.size
        display_width, display_height = 800, 540

        # Get the crop rectangle coordinates
        left = int(self.widget_cutimage.crop_rect.left() * (original_width / display_width))
        top = int(self.widget_cutimage.crop_rect.top() * (original_height / display_height))
        right = int(self.widget_cutimage.crop_rect.right() * (original_width / display_width))
        bottom = int(self.widget_cutimage.crop_rect.bottom() * (original_height / display_height))

        # Crop the image
        cropped_image = original_image.crop((left, top, right, bottom))

        # Save the cropped image in the same directory as the opened file
        directory = os.path.dirname(self.image_path)
        poster_path = os.path.join(directory, 'poster.jpg')
        cropped_image.save(poster_path)

        print(f"裁剪区域: Left={left}, Top={top}, Right={right}, Bottom={bottom}")
        print(f"裁剪后的图片已保存为 {poster_path}")

        # Add watermarks if selected
        self.add_watermark(poster_path)
        self.add_watermark(self.image_path, output_path=os.path.join(directory, 'thumb.jpg'))

    def add_watermark(self, image_path, output_path=None):
        print(f"开始为 {image_path} 添加水印...")

        # Delete existing poster and thumb images to avoid duplication
        if output_path and os.path.exists(output_path):
            os.remove(output_path)
            print(f"已删除原有的图片: {output_path}")

        image = Image.open(image_path).convert("RGBA")

        # Create a transparent image for the watermark
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))

        script_dir = os.path.dirname(os.path.realpath(__file__))

        # Add subtitle watermark if selected
        if self.checkBox_subtitle.isChecked():
            subtitle_image_path = os.path.join(script_dir, 'sub.png')
            subtitle_image = Image.open(subtitle_image_path).convert("RGBA")
            print(f"读取字幕水印信息: 尺寸={subtitle_image.size}")

            # Calculate new size, keeping aspect ratio, with the longest side being 1/4 of the target image size
            target_width, target_height = image.size
            if subtitle_image.width > subtitle_image.height:
                new_width = target_width // 4
                new_height = int((new_width / subtitle_image.width) * subtitle_image.height)
            else:
                new_height = target_height // 4
                new_width = int((new_height / subtitle_image.height) * subtitle_image.width)

            # Resize subtitle image
            subtitle_image = subtitle_image.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Paste subtitle image on the top left corner
            layer.paste(subtitle_image, (0, 0), subtitle_image)
            print(f"字幕水印已添加")

        # Add right top watermark if selected
        watermark_image_path = None
        for radio in self.radio_buttons:
            if radio.isChecked():
                watermark_text = radio.text()
                if watermark_text == "有码":
                    watermark_image_path = os.path.join(script_dir, 'youma.png')
                elif watermark_text == "无码":
                    watermark_image_path = os.path.join(script_dir, 'wuma.png')
                elif watermark_text == "流出":
                    watermark_image_path = os.path.join(script_dir, 'leak.png')
                elif watermark_text == "破解":
                    watermark_image_path = os.path.join(script_dir, 'umr.png')
                break

        if watermark_image_path:
            watermark_image = Image.open(watermark_image_path).convert("RGBA")
            print(f"读取水印信息: 尺寸={watermark_image.size}")

            # Calculate new size, keeping aspect ratio, with the longest side being 1/4 of the target image size
            if watermark_image.width > watermark_image.height:
                new_width = target_width // 4
                new_height = int((new_width / watermark_image.width) * watermark_image.height)
            else:
                new_height = target_height // 4
                new_width = int((new_height / watermark_image.height) * watermark_image.width)

            # Resize watermark image
            watermark_image = watermark_image.resize((new_width, new_height), Image.Resampling.LANCZOS)

            # Paste watermark image on the top right corner
            watermark_position = (image.width - watermark_image.width, 0)
            layer.paste(watermark_image, watermark_position, watermark_image)
            print(f"水印已添加在位置 {watermark_position}")

        # Composite the watermark with the original image
        watermarked_image = Image.alpha_composite(image, layer)
        print("图片合成完成")

        # Save the watermarked image
        watermarked_image = watermarked_image.convert("RGB")  # Remove alpha for saving in JPEG format
        if not output_path:
            output_path = image_path
        watermarked_image.save(output_path)
        print(f"合成后的图片已保存为 {output_path}")

    def close_app(self):
        self.close()

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    Dialog = QMainWindow()
    ui = Ui_Dialog_cut_poster()
    ui.setupUi(Dialog)
    Dialog.show()
    sys.exit(app.exec_())
