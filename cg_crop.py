import os
import sys
import argparse
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict
from pathlib import Path

from PyQt5.QtWidgets import (
    QDialog, QLabel, QPushButton, QFileDialog, QMessageBox, QGroupBox,
    QVBoxLayout, QHBoxLayout, QCheckBox, QRadioButton, QWidget, QButtonGroup,
    QSpinBox, QDoubleSpinBox, QApplication
)
from PyQt5.QtCore import QSize, Qt, QRect, pyqtSignal
from PyQt5.QtGui import QIcon, QImage, QImageReader, QPixmap, QPainter, QPen, QTransform, QColor


# ================================ 配置管理 ================================
class CropConfig:
    """裁剪工具配置中心"""
    APP_VERSION = "v9.6.6"
    APP_TITLE = "大锤 EMBY海报裁剪工具"
    WINDOW_SIZE = (1200, 680)
    IMAGE_DISPLAY_SIZE = (800, 538)
    RIGHT_PANEL_WIDTH = 300
    
    # 预设比例配置
    PRESET_RATIOS = [
        ("2:3", 1.5), 
        ("mdcx", 1.419), 
        ("16:9", 0.56)
    ]
    DEFAULT_RATIO = 1.419  # 默认mdcx比例
    
    # 水印配置
    WATERMARK_MARKS = {
        "sub": "sub.png",
        "youma": "youma.png", 
        "wuma": "wuma.png",
        "leak": "leak.png",
        "umr": "umr.png",
    }
    
    WATERMARK_POSITIONS = {
        "sub": (0, 0),
        "youma": (None, 0),
        "wuma": (None, 0), 
        "leak": (None, 0),
        "umr": (None, 0),
    }
    
    # 样式配置
    MAIN_STYLE = """
        QDialog { background-color: #F9FAFB; }
        QRadioButton, QCheckBox {
            color: #4B5563; font-size: 13px; padding: 4px;
        }
        QRadioButton:hover, QCheckBox:hover {
            background-color: #F3F4F6; border-radius: 4px;
        }
    """
    
    GROUP_STYLE = """
        QGroupBox {
            background-color: white; border: 1px solid #E5E7EB;
            border-radius: 6px; font-weight: bold; margin-top: 16px;
            padding: 8px; color: #374151;
        }
        QGroupBox::title {
            subcontrol-origin: margin; left: 8px; padding: 0 5px;
        }
        QLabel { font-weight: normal; color: #4B5563; font-size: 13px; }
    """
    
    BUTTON_STYLE = """
        QPushButton {
            background-color: #F3F4F6; color: #374151;
            border: 1px solid #D1D5DB; border-radius: 4px;
            padding: 8px 15px; font-size: 13px;
        }
        QPushButton:hover {
            background-color: #E5E7EB; border-color: #9CA3AF;
        }
        QPushButton:pressed {
            background-color: #D1D5DB; border-color: #6B7280;
        }
    """
    
    PRIMARY_BUTTON_STYLE = """
        QPushButton {
            background-color: #3B82F6; color: white;
            border-radius: 4px; font-size: 14px; font-weight: 500;
        }
        QPushButton:hover { background-color: #2563EB; }
    """
    
    SUCCESS_BUTTON_STYLE = """
        QPushButton {
            background-color: #10B981; color: white;
            border-radius: 4px; font-size: 14px; font-weight: 500;
        }
        QPushButton:hover { background-color: #059669; }
    """
    
    IMAGE_LABEL_STYLE = """
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
    
    # 裁剪框颜色 - 藏蓝色
    CROP_OVERLAY_COLOR = "#0704ed"


# ================================ 数据结构 ================================
@dataclass
class CropParams:
    """裁剪参数数据类"""
    target_ratio: float = CropConfig.DEFAULT_RATIO
    rotation_angle: int = 0
    crop_rect: Optional[QRect] = None
    image_size: Optional[QSize] = None
    scale_factor_x: float = 1.0
    scale_factor_y: float = 1.0
    offset_x: int = 0
    offset_y: int = 0

@dataclass
class WatermarkSettings:
    """水印设置数据类"""
    enable_subtitle: bool = False
    mark_type: str = "none"  # none, umr, leak, wuma, youma
    
    def get_active_marks(self) -> List[str]:
        """获取活跃的水印列表"""
        marks = []
        if self.enable_subtitle:
            marks.append("sub")
        if self.mark_type != "none":
            marks.append(self.mark_type)
        return marks


# ================================ 异常定义 ================================
class CropError(Exception):
    """裁剪相关错误基类"""
    pass

class ImageLoadError(CropError):
    """图片加载错误"""
    pass

class CropCalculationError(CropError):
    """裁剪计算错误"""
    pass

class WatermarkError(CropError):
    """水印处理错误"""
    pass


# ================================ 工具函数 ================================
def get_resource_path(relative_path: str) -> str:
    """获取资源文件的绝对路径"""
    if getattr(sys, "frozen", False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


# ================================ 功能模块 ================================
class ImageProcessor:
    """图片处理器 - 专门处理图片相关操作"""
    
    def __init__(self):
        self.original_pixmap: Optional[QPixmap] = None
        self.original_pixmap_backup: Optional[QPixmap] = None  # 修复：保存原图备份
        self.scaled_pixmap: Optional[QPixmap] = None
        self.original_size: Optional[QSize] = None
        self.current_rotation: int = 0  # 修复：跟踪当前旋转角度
    
    def load_image(self, image_path: str) -> Tuple[QPixmap, QSize]:
        """加载图片并返回pixmap和原始尺寸"""
        try:
            reader = QImageReader(image_path)
            reader.setAutoTransform(True)
            
            self.original_size = reader.size()
            if not self.original_size or self.original_size.isNull():
                raise ImageLoadError("无法读取图片尺寸信息")
            
            self.original_pixmap = QPixmap(image_path)
            if self.original_pixmap.isNull():
                raise ImageLoadError("无法加载图片文件")
            
            # 修复：保存原图备份并重置旋转
            self.original_pixmap_backup = self.original_pixmap.copy()
            self.current_rotation = 0
            
            return self.original_pixmap, self.original_size
            
        except Exception as e:
            raise ImageLoadError(f"加载图片失败: {e}")
    
    def rotate_image(self, angle: int) -> QPixmap:
        """修复：基于原图和累积角度进行旋转，避免质量损失"""
        try:
            if self.original_pixmap_backup is None or self.original_pixmap_backup.isNull():
                raise CropError("原图备份为空，无法旋转")
            
            self.current_rotation = (self.current_rotation + angle) % 360
            
            if self.current_rotation == 0:
                rotated_pixmap = self.original_pixmap_backup.copy()
            else:
                transform = QTransform().rotate(self.current_rotation)
                rotated_pixmap = self.original_pixmap_backup.transformed(transform, Qt.SmoothTransformation)
            
            # 更新当前显示的pixmap
            self.original_pixmap = rotated_pixmap
            return rotated_pixmap
            
        except Exception as e:
            raise CropError(f"旋转图片失败: {e}")
    
    def scale_image_for_display(self, pixmap: QPixmap, display_size: Tuple[int, int]) -> Tuple[QPixmap, int, int]:
        """缩放图片以适应显示区域，返回缩放后的pixmap和偏移量"""
        try:
            if pixmap.isNull():
                raise CropError("图片为空，无法缩放")
            
            width, height = display_size
            scaled_pixmap = pixmap.scaled(width, height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            
            offset_x = (width - scaled_pixmap.width()) // 2
            offset_y = (height - scaled_pixmap.height()) // 2
            
            return scaled_pixmap, offset_x, offset_y
            
        except Exception as e:
            raise CropError(f"缩放图片失败: {e}")
    
    def calculate_scale_factors(self, original_size: QSize, scaled_size: QSize, rotation_angle: int) -> Tuple[float, float]:
        """修复：计算缩放因子，正确处理旋转后的尺寸关系"""
        try:
            if rotation_angle in [90, 270]:
                # 旋转90度或270度时，原始尺寸的宽高对应关系互换
                # 但要基于旋转后的实际尺寸计算
                actual_original_width = original_size.height()
                actual_original_height = original_size.width()
            else:
                actual_original_width = original_size.width()
                actual_original_height = original_size.height()
            
            scale_factor_x = actual_original_width / scaled_size.width()
            scale_factor_y = actual_original_height / scaled_size.height()
            
            return scale_factor_x, scale_factor_y
            
        except ZeroDivisionError:
            raise CropCalculationError("缩放因子计算错误：除零错误")
        except Exception as e:
            raise CropCalculationError(f"计算缩放因子失败: {e}")


class CropCalculator:
    """裁剪计算器 - 专门处理裁剪相关计算"""
    
    @staticmethod
    def calculate_crop_size(img_width: int, img_height: int, ratio: float) -> Tuple[int, int]:
        """修复：计算裁剪框尺寸，使用round提高精度"""
        try:
            if img_width <= 0 or img_height <= 0 or ratio <= 0:
                raise CropCalculationError("图片尺寸或比例参数无效")
            
            # 修复：使用round而不是int，避免精度损失
            height_by_width = round(img_width * ratio)
            width_by_height = round(img_height / ratio)
            
            if height_by_width <= img_height:
                # 以宽度为基准
                return img_width, height_by_width
            else:
                # 以高度为基准
                return width_by_height, img_height
                
        except Exception as e:
            raise CropCalculationError(f"计算裁剪尺寸失败: {e}")
    
    @staticmethod
    def initialize_crop_rect(img_width: int, img_height: int, ratio: float, 
                           offset_x: int, offset_y: int) -> QRect:
        """初始化裁剪框，居中放置"""
        try:
            crop_width, crop_height = CropCalculator.calculate_crop_size(img_width, img_height, ratio)
            
            # 居中放置
            x = offset_x + (img_width - crop_width) // 2
            y = offset_y + (img_height - crop_height) // 2
            
            return QRect(x, y, crop_width, crop_height)
            
        except Exception as e:
            raise CropCalculationError(f"初始化裁剪框失败: {e}")
    
    @staticmethod
    def get_crop_coordinates(crop_rect: QRect, crop_params: CropParams) -> Tuple[int, int, int, int]:
        """获取基于原始分辨率的裁剪坐标"""
        try:
            if crop_rect is None or crop_rect.isNull():
                raise CropCalculationError("裁剪框无效")
            
            # 计算显示图片上的相对位置
            relative_x = crop_rect.x() - crop_params.offset_x
            relative_y = crop_rect.y() - crop_params.offset_y
            
            # 转换为原始分辨率下的坐标
            original_x = max(0, round(relative_x * crop_params.scale_factor_x))
            original_y = max(0, round(relative_y * crop_params.scale_factor_y))
            original_width = round(crop_rect.width() * crop_params.scale_factor_x)
            original_height = round(crop_rect.height() * crop_params.scale_factor_y)
            
            return (original_x, original_y, original_width, original_height)
            
        except Exception as e:
            raise CropCalculationError(f"计算裁剪坐标失败: {e}")
    
    @staticmethod
    def is_horizontal_image(image_size: QSize, rotation_angle: int) -> bool:
        """判断当前显示的图片是否为横向"""
        if image_size is None or image_size.isNull():
            return True
        
        # 考虑旋转角度
        if rotation_angle in [90, 270]:
            return image_size.height() > image_size.width()
        else:
            return image_size.width() > image_size.height()


class WatermarkProcessor:
    """水印处理器 - 专门处理水印相关操作"""
    
    def __init__(self, watermark_path: str = ""):
        self.watermark_path = watermark_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "img"
        )
    
    def get_watermark_path(self, mark_type: str) -> str:
        """获取水印图片路径"""
        mark_file = CropConfig.WATERMARK_MARKS.get(mark_type, "")
        return os.path.join(self.watermark_path, mark_file)
    
    def apply_watermarks(self, image: QImage, settings: WatermarkSettings) -> QImage:
        """应用水印到图片"""
        try:
            marks = settings.get_active_marks()
            if not marks:
                return image
            
            if image.format() != QImage.Format_RGB32:
                image = image.convertToFormat(QImage.Format_RGB32)
            
            painter = QPainter(image)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            
            for mark_type in marks:
                self._apply_single_watermark(painter, image, mark_type)
            
            painter.end()
            return image
            
        except Exception as e:
            raise WatermarkError(f"应用水印失败: {e}")
    
    def _apply_single_watermark(self, painter: QPainter, image: QImage, mark_type: str):
        """修复：应用单个水印，处理位置空值问题"""
        try:
            mark_path = self.get_watermark_path(mark_type)
            if not os.path.exists(mark_path):
                return
            
            watermark = QImage(mark_path)
            if watermark.isNull():
                return
            
            # 计算水印尺寸（高度为图片高度的1/4）
            mark_height = round(image.height() / 4)  # 修复：简化比例计算
            mark_width = round(mark_height * watermark.width() / watermark.height())
            
            watermark = watermark.scaled(
                mark_width, mark_height, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            
            # 修复：获取位置并处理空值
            pos = CropConfig.WATERMARK_POSITIONS.get(mark_type, (0, 0))
            x = pos[0] if pos[0] is not None else (image.width() - mark_width)
            y = pos[1] if pos[1] is not None else 0  # 修复：处理y坐标空值
            painter.drawImage(x, y, watermark)
            
        except Exception as e:
            print(f"应用水印 {mark_type} 失败: {e}")


class ImageSaver:
    """图片保存器 - 专门处理图片保存"""
    
    @staticmethod
    def save_images(cropped_image: QImage, full_image: QImage, original_path: str) -> Tuple[str, str]:
        """修复：保存图片，支持更多图片格式"""
        try:
            directory = os.path.dirname(original_path)
            filename = os.path.basename(original_path)
            
            # 修复：改进文件名处理逻辑，支持更多格式
            if filename.lower().endswith("-fanart.jpg"):
                base_name = filename[:-11]  # 去掉"-fanart.jpg"
            elif filename.lower().endswith("-fanart.jpeg"):
                base_name = filename[:-12]  # 去掉"-fanart.jpeg"
            elif filename.lower().endswith("-fanart.png"):
                base_name = filename[:-11]  # 去掉"-fanart.png"
            else:
                # 通用处理：去掉文件扩展名
                base_name = os.path.splitext(filename)[0]
            
            poster_path = os.path.join(directory, f"{base_name}-poster.jpg")
            thumb_path = os.path.join(directory, f"{base_name}-thumb.jpg")
            
            if not cropped_image.save(poster_path, "JPEG", quality=95):
                raise CropError("保存poster失败")
            if not full_image.save(thumb_path, "JPEG", quality=95):
                raise CropError("保存thumb失败")
            
            return poster_path, thumb_path
            
        except Exception as e:
            raise CropError(f"保存图片失败: {e}")


# ================================ UI组件 ================================
class CropDisplayWidget(QLabel):
    """裁剪显示组件 - 只负责显示和交互"""
    
    original_info_updated = pyqtSignal(int, int)
    crop_info_updated = pyqtSignal(QRect)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(*CropConfig.IMAGE_DISPLAY_SIZE)
        
        # 初始化组件
        self.crop_params = CropParams()
        self.image_processor = ImageProcessor()
        
        # 交互状态
        self.dragging = False
        self.drag_start: Optional[QPoint] = None
        
        self._setup_widget()
    
    def _setup_widget(self):
        """设置控件"""
        self.setStyleSheet(CropConfig.IMAGE_LABEL_STYLE)
        self.setAlignment(Qt.AlignCenter)
        self.setText("请点击右上角的'打开图片'按钮或拖拽图片到此处")
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
    
    def set_target_ratio(self, ratio: float):
        """设置目标高宽比例"""
        self.crop_params.target_ratio = ratio
        if self.image_processor.scaled_pixmap:
            self._initialize_crop_rect()
            self.update()
    
    def set_image(self, image_path: str):
        """加载图片"""
        try:
            # 加载图片
            pixmap, original_size = self.image_processor.load_image(image_path)
            self.crop_params.image_size = original_size
            self.crop_params.rotation_angle = 0  # 重置旋转角度
            
            # 发送原图信息更新信号
            self.original_info_updated.emit(original_size.width(), original_size.height())
            
            # 更新显示
            self._update_display(pixmap)
            self._initialize_crop_rect()
            self.update()
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载图片失败: {e}")
    
    def rotate_image(self, angle: int):
        """修复：旋转图片，基于原图进行旋转"""
        try:
            if not self.image_processor.original_pixmap_backup:
                return
            
            # 修复：使用新的旋转逻辑
            rotated_pixmap = self.image_processor.rotate_image(angle)
            
            # 更新旋转角度
            self.crop_params.rotation_angle = self.image_processor.current_rotation
            
            # 更新显示
            self._update_display(rotated_pixmap)
            self._initialize_crop_rect()
            self.update()
            
            # 发送原图信息更新信号（旋转后的尺寸）
            current_size = rotated_pixmap.size()
            self.original_info_updated.emit(current_size.width(), current_size.height())
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"旋转图片失败: {e}")
    
    def _update_display(self, pixmap: QPixmap):
        """更新显示相关参数"""
        try:
            # 缩放图片以适应显示区域
            scaled_pixmap, offset_x, offset_y = self.image_processor.scale_image_for_display(
                pixmap, CropConfig.IMAGE_DISPLAY_SIZE
            )
            
            self.image_processor.scaled_pixmap = scaled_pixmap
            self.crop_params.offset_x = offset_x
            self.crop_params.offset_y = offset_y
            
            # 更新缩放因子
            if self.crop_params.image_size:
                scale_x, scale_y = self.image_processor.calculate_scale_factors(
                    self.crop_params.image_size, scaled_pixmap.size(), self.crop_params.rotation_angle
                )
                self.crop_params.scale_factor_x = scale_x
                self.crop_params.scale_factor_y = scale_y
                
        except Exception as e:
            print(f"更新显示失败: {e}")
    
    def _initialize_crop_rect(self):
        """初始化裁剪框"""
        try:
            if not self.image_processor.scaled_pixmap:
                return
            
            img_width = self.image_processor.scaled_pixmap.width()
            img_height = self.image_processor.scaled_pixmap.height()
            
            self.crop_params.crop_rect = CropCalculator.initialize_crop_rect(
                img_width, img_height, self.crop_params.target_ratio,
                self.crop_params.offset_x, self.crop_params.offset_y
            )
            
            self.crop_info_updated.emit(self.crop_params.crop_rect)
            
        except Exception as e:
            print(f"初始化裁剪框失败: {e}")
    
    def get_crop_coordinates(self) -> Optional[Tuple[int, int, int, int]]:
        """获取基于原始分辨率的裁剪坐标"""
        try:
            if (not self.image_processor.original_pixmap or 
                not self.image_processor.scaled_pixmap or 
                self.crop_params.crop_rect is None or 
                self.crop_params.crop_rect.isNull()):
                return None
            
            return CropCalculator.get_crop_coordinates(self.crop_params.crop_rect, self.crop_params)
            
        except Exception as e:
            print(f"获取裁剪坐标失败: {e}")
            return None
    
    # ========== 事件处理 ==========
    def paintEvent(self, event):
        """绘制事件"""
        super().paintEvent(event)
        if not self.image_processor.scaled_pixmap or self.image_processor.scaled_pixmap.isNull():
            return
            
        painter = QPainter(self)
        painter.drawPixmap(self.crop_params.offset_x, self.crop_params.offset_y, 
                          self.image_processor.scaled_pixmap)
        
        if self.crop_params.crop_rect is not None and not self.crop_params.crop_rect.isNull():
            self._draw_crop_overlay(painter)
    
    def _draw_crop_overlay(self, painter: QPainter):
        """绘制裁剪框和辅助线"""
        # 绘制裁剪框
        crop_color = QColor(CropConfig.CROP_OVERLAY_COLOR)
        pen = QPen(crop_color, 3)
        painter.setPen(pen)
        painter.drawRect(self.crop_params.crop_rect)
        
        # 绘制三分线
        pen = QPen(crop_color, 1, Qt.DashLine)
        painter.setPen(pen)
        
        # 横向三分线
        for i in range(1, 3):
            y = self.crop_params.crop_rect.top() + i * self.crop_params.crop_rect.height() // 3
            painter.drawLine(self.crop_params.crop_rect.left(), y, 
                           self.crop_params.crop_rect.right(), y)
        
        # 纵向三分线
        for i in range(1, 3):
            x = self.crop_params.crop_rect.left() + i * self.crop_params.crop_rect.width() // 3
            painter.drawLine(x, self.crop_params.crop_rect.top(), 
                           x, self.crop_params.crop_rect.bottom())
    
    def mousePressEvent(self, event):
        """鼠标按下事件"""
        if (event.button() == Qt.LeftButton and 
            self.crop_params.crop_rect is not None and 
            not self.crop_params.crop_rect.isNull() and 
            self.crop_params.crop_rect.contains(event.pos())):
            self.dragging = True
            self.drag_start = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
    
    def mouseMoveEvent(self, event):
        """鼠标移动事件"""
        if (self.crop_params.crop_rect is None or 
            self.crop_params.crop_rect.isNull() or 
            not self.image_processor.scaled_pixmap):
            return
        
        # 更新光标
        if self.crop_params.crop_rect.contains(event.pos()):
            self.setCursor(Qt.OpenHandCursor if not self.dragging else Qt.ClosedHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        
        # 处理拖动
        if self.dragging and self.drag_start:
            self._handle_drag(event.pos())
    
    def _handle_drag(self, pos):
        """修复：处理拖拽逻辑，改进边界检查"""
        try:
            delta_x = pos.x() - self.drag_start.x()
            delta_y = pos.y() - self.drag_start.y()
            new_rect = QRect(self.crop_params.crop_rect)
            
            is_horizontal = CropCalculator.is_horizontal_image(
                self.crop_params.image_size, self.crop_params.rotation_angle
            )
            
            if is_horizontal:
                # 横向图片主要允许水平移动
                new_rect.moveLeft(self.crop_params.crop_rect.left() + delta_x)
            else:
                # 竖向图片主要允许垂直移动
                new_rect.moveTop(self.crop_params.crop_rect.top() + delta_y)
            
            # 修复：统一的边界约束
            new_rect = self._constrain_rect(new_rect)
            
            self.crop_params.crop_rect = new_rect
            self.drag_start = pos
            self.crop_info_updated.emit(self.crop_params.crop_rect)
            self.update()
            
        except Exception as e:
            print(f"处理拖拽失败: {e}")
    
    def _constrain_rect(self, rect: QRect) -> QRect:
        """修复：统一的边界约束方法"""
        if not self.image_processor.scaled_pixmap:
            return rect
        
        # 获取图片边界
        img_left = self.crop_params.offset_x
        img_top = self.crop_params.offset_y
        img_right = img_left + self.image_processor.scaled_pixmap.width()
        img_bottom = img_top + self.image_processor.scaled_pixmap.height()
        
        # 约束水平边界
        if rect.left() < img_left:
            rect.moveLeft(img_left)
        elif rect.right() > img_right:
            rect.moveRight(img_right)
        
        # 约束垂直边界
        if rect.top() < img_top:
            rect.moveTop(img_top)
        elif rect.bottom() > img_bottom:
            rect.moveBottom(img_bottom)
        
        return rect
    
    def mouseReleaseEvent(self, event):
        """鼠标释放事件"""
        if event.button() == Qt.LeftButton:
            self.dragging = False
            if (self.crop_params.crop_rect is not None and 
                not self.crop_params.crop_rect.isNull() and 
                self.crop_params.crop_rect.contains(event.pos())):
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
    
    # ========== 拖放事件 ==========
    def dragEnterEvent(self, event):
        """拖拽进入事件"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if self._is_image_file(url.toLocalFile()):
                    event.acceptProposedAction()
                    return
        event.ignore()
    
    def dragMoveEvent(self, event):
        """拖拽移动事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()
    
    def dropEvent(self, event):
        """拖放事件"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                if self._is_image_file(file_path):
                    # 通知父窗口更新路径
                    parent = self.parent()
                    while parent and not isinstance(parent, EmbyPosterCrop):
                        parent = parent.parent()
                    if parent:
                        parent.image_path = file_path
                    
                    self.set_image(file_path)
                    event.acceptProposedAction()
                    return
        event.ignore()
    
    def _is_image_file(self, file_path: str) -> bool:
        """检查是否为图片文件"""
        return any(file_path.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".bmp"])


class RatioControlWidget(QGroupBox):
    """比例控制组件"""
    
    ratio_changed = pyqtSignal(float)
    
    def __init__(self, parent=None):
        super().__init__("裁剪比例设置", parent)
        self.setStyleSheet(CropConfig.GROUP_STYLE)
        
        self.ratio_group = QButtonGroup()
        self.custom_ratio_input = QDoubleSpinBox()
        self.apply_ratio_btn = QPushButton("应用比例")
        
        self._setup_ui()
        self._connect_signals()
    
    def _setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout(self)
        
        # 预设比例按钮
        preset_layout = QHBoxLayout()
        
        for text, ratio in CropConfig.PRESET_RATIOS:
            radio = QRadioButton(text)
            radio.setProperty("ratio", ratio)
            if ratio == CropConfig.DEFAULT_RATIO:  # 默认选中mdcx
                radio.setChecked(True)
            self.ratio_group.addButton(radio)
            preset_layout.addWidget(radio)
            
        layout.addLayout(preset_layout)
        
        # 自定义比例输入
        custom_layout = QHBoxLayout()
        custom_layout.addWidget(QLabel("自定义比例:"))
        
        self.custom_ratio_input.setRange(0.1, 10.0)
        self.custom_ratio_input.setValue(CropConfig.DEFAULT_RATIO)
        self.custom_ratio_input.setSingleStep(0.001)
        self.custom_ratio_input.setDecimals(3)  # 支持3位小数
        custom_layout.addWidget(self.custom_ratio_input)
        
        layout.addLayout(custom_layout)
        
        # 应用按钮
        self.apply_ratio_btn.setFixedHeight(30)
        layout.addWidget(self.apply_ratio_btn)
    
    def _connect_signals(self):
        """连接信号"""
        self.ratio_group.buttonClicked.connect(self._on_preset_ratio_selected)
        self.apply_ratio_btn.clicked.connect(self._apply_custom_ratio)
        
        # 初始化时同步默认选中的按钮
        checked_button = self.ratio_group.checkedButton()
        if checked_button:
            self._on_preset_ratio_selected(checked_button)
    
    def _on_preset_ratio_selected(self, button):
        """预设比例被选中时的处理"""
        ratio = button.property("ratio")
        self.custom_ratio_input.setValue(ratio)
        self._apply_custom_ratio()
    
    def _apply_custom_ratio(self):
        """应用自定义比例"""
        ratio = self.custom_ratio_input.value()
        self.ratio_changed.emit(ratio)


class WatermarkControlWidget(QGroupBox):
    """水印控制组件"""
    
    def __init__(self, parent=None):
        super().__init__("添加水印:", parent)
        self.setStyleSheet(CropConfig.GROUP_STYLE)
        
        self.sub_check = QCheckBox("字幕")
        self.mark_group = QButtonGroup()
        
        self._setup_ui()
    
    def _setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout(self)
        
        # 字幕水印
        layout.addWidget(self.sub_check)
        
        # 分类水印
        categories_layout = QVBoxLayout()
        
        # 创建水印选项
        mark_options = [
            [("有码", "youma"), ("无码", "wuma"), ("流出", "leak")],
            [("破解", "umr"), ("无", "none")]
        ]
        
        for row_options in mark_options:
            row_layout = QHBoxLayout()
            for text, value in row_options:
                radio = QRadioButton(text)
                radio.setProperty("value", value)
                self.mark_group.addButton(radio)
                row_layout.addWidget(radio)
            categories_layout.addLayout(row_layout)
        
        layout.addLayout(categories_layout)
    
    def get_watermark_settings(self) -> WatermarkSettings:
        """获取水印设置"""
        settings = WatermarkSettings()
        settings.enable_subtitle = self.sub_check.isChecked()
        
        checked_mark = self.mark_group.checkedButton()
        if checked_mark:
            settings.mark_type = checked_mark.property("value") or "none"
        
        return settings


class InfoDisplayWidget(QGroupBox):
    """信息显示组件"""
    
    def __init__(self, parent=None):
        super().__init__("图片信息", parent)
        self.setFixedHeight(168)
        self.setStyleSheet(CropConfig.GROUP_STYLE)
        
        self.info_labels: Dict[str, QLabel] = {}
        self._setup_ui()
    
    def _setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(1)
        layout.setContentsMargins(10, 3, 5, 3)
        
        # 创建信息标签
        info_items = [
            ("原图尺寸:", "800, 538"),
            ("裁剪尺寸:", "377, 538"), 
            ("裁剪位置:", "421, 0, 800, 538"),
            ("高宽比例:", "1.419")
        ]
        
        for label_text, default_value in info_items:
            layout.addWidget(QLabel(label_text))
            value_label = QLabel(default_value)
            self.info_labels[label_text] = value_label
            layout.addWidget(value_label)
            layout.addSpacing(5)
        
        layout.addStretch()
    
    def update_original_info(self, width: int, height: int):
        """更新原图信息"""
        self.info_labels["原图尺寸:"].setText(f"{width}, {height}")
    
    def update_crop_info(self, crop_rect: QRect, crop_params: CropParams):
        """更新裁剪信息"""
        if crop_rect is None or crop_rect.isNull():
            return
            
        try:
            coords = CropCalculator.get_crop_coordinates(crop_rect, crop_params)
            if coords:
                x, y, w, h = coords
                self.info_labels["裁剪尺寸:"].setText(f"{w}, {h}")
                self.info_labels["裁剪位置:"].setText(f"{x}, {y}, {x+w}, {y+h}")
                ratio = h / w if w > 0 else 0
                self.info_labels["高宽比例:"].setText(f"{ratio:.2f}")
        except Exception as e:
            print(f"更新裁剪信息失败: {e}")


# ================================ 主窗口 ================================
class EmbyPosterCrop(QDialog):
    """主窗口 - 组装各个组件"""
    
    def __init__(self, parent=None, nfo_base_name=None):
        super().__init__(parent)
        self.nfo_base_name = nfo_base_name
        self.image_path: Optional[str] = None
        
        # 初始化组件
        self.watermark_processor = WatermarkProcessor()
        
        self._setup_window()
        self._setup_ui()
        self._connect_signals()
    
    def _setup_window(self):
        """设置窗口属性"""
        self.setWindowTitle(f"{CropConfig.APP_TITLE} {CropConfig.APP_VERSION}")
        self.setFixedSize(*CropConfig.WINDOW_SIZE)
        self.setStyleSheet(CropConfig.MAIN_STYLE)
        
        # 设置窗口图标
        try:
            icon_path = get_resource_path("cg_crop.ico")
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except Exception as e:
            print(f"设置窗口图标失败: {e}")
    
    def _setup_ui(self):
        """设置UI界面"""
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(10, 5, 10, 5)
        
        # 左侧：图片显示区域
        left_container = self._create_left_panel()
        
        # 右侧：控制面板
        right_panel = self._create_right_panel()
        
        main_layout.addWidget(left_container)
        main_layout.addWidget(right_panel)
    
    def _create_left_panel(self) -> QWidget:
        """创建左侧面板"""
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setSpacing(10)
        
        # 图片显示区域
        self.crop_display = CropDisplayWidget()
        left_layout.addWidget(self.crop_display)
        
        # 旋转按钮
        rotation_container = self._create_rotation_buttons()
        left_layout.addWidget(rotation_container)
        
        return left_container
    
    def _create_rotation_buttons(self) -> QWidget:
        """创建旋转按钮"""
        rotation_container = QWidget()
        rotation_layout = QHBoxLayout(rotation_container)
        rotation_layout.setContentsMargins(0, 0, 0, 0)
        
        self.rotate_left_btn = QPushButton("↶ 向左旋转")
        self.rotate_right_btn = QPushButton("↷ 向右旋转")
        
        for btn in [self.rotate_left_btn, self.rotate_right_btn]:
            btn.setStyleSheet(CropConfig.BUTTON_STYLE)
            rotation_layout.addWidget(btn)
        
        return rotation_container
    
    def _create_right_panel(self) -> QWidget:
        """创建右侧控制面板"""
        right_panel = QWidget()
        right_panel.setFixedWidth(CropConfig.RIGHT_PANEL_WIDTH)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(15)
        
        # 添加各个控件组
        components = [
            self._create_open_button(),
            self._create_ratio_control(),
            self._create_info_display(),
            self._create_watermark_control(),
            self._create_action_buttons()
        ]
        
        for component in components:
            right_layout.addWidget(component)
        
        return right_panel
    
    def _create_open_button(self) -> QPushButton:
        """创建打开图片按钮"""
        self.open_button = QPushButton("打开图片")
        self.open_button.setFixedHeight(40)
        self.open_button.setStyleSheet(CropConfig.PRIMARY_BUTTON_STYLE)
        return self.open_button
    
    def _create_ratio_control(self) -> RatioControlWidget:
        """创建比例控制组"""
        self.ratio_control = RatioControlWidget()
        return self.ratio_control
    
    def _create_info_display(self) -> InfoDisplayWidget:
        """创建信息显示组"""
        self.info_display = InfoDisplayWidget()
        return self.info_display
    
    def _create_watermark_control(self) -> WatermarkControlWidget:
        """创建水印控制组"""
        self.watermark_control = WatermarkControlWidget()
        return self.watermark_control
    
    def _create_action_buttons(self) -> QWidget:
        """创建操作按钮组"""
        buttons_container = QWidget()
        buttons_layout = QVBoxLayout(buttons_container)
        
        # 裁剪并关闭按钮
        self.cut_close_button = QPushButton("裁剪并关闭")
        self.cut_close_button.setFixedHeight(40)
        self.cut_close_button.setStyleSheet(CropConfig.SUCCESS_BUTTON_STYLE)
        buttons_layout.addWidget(self.cut_close_button)
        
        # 底部按钮行
        bottom_layout = QHBoxLayout()
        
        self.cut_button = QPushButton("裁剪")
        self.close_button = QPushButton("关闭")
        
        for btn in [self.cut_button, self.close_button]:
            btn.setFixedSize(133, 40)
            btn.setStyleSheet(CropConfig.BUTTON_STYLE)
            bottom_layout.addWidget(btn)
        
        buttons_layout.addLayout(bottom_layout)
        return buttons_container
    
    def _connect_signals(self):
        """连接信号和槽"""
        # 按钮信号
        self.open_button.clicked.connect(self.open_image)
        self.rotate_left_btn.clicked.connect(lambda: self.crop_display.rotate_image(-90))
        self.rotate_right_btn.clicked.connect(lambda: self.crop_display.rotate_image(90))
        self.cut_close_button.clicked.connect(self.cut_and_close)
        self.cut_button.clicked.connect(self.cut_image)
        self.close_button.clicked.connect(self.close)
        
        # 图片显示信号
        self.crop_display.original_info_updated.connect(self.info_display.update_original_info)
        self.crop_display.crop_info_updated.connect(
            lambda rect: self.info_display.update_crop_info(rect, self.crop_display.crop_params)
        )
        
        # 比例控制信号
        self.ratio_control.ratio_changed.connect(self.crop_display.set_target_ratio)
    
    def open_image(self):
        """打开图片"""
        start_dir = ""
        if self.image_path:
            start_dir = os.path.dirname(self.image_path)
        
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", start_dir,
            "图片文件 (*.jpg *.jpeg *.png *.bmp);;所有文件 (*.*)"
        )
        
        if file_path:
            self.load_initial_image(file_path)
    
    def _perform_crop_operation(self) -> bool:
        """修复：提取公共的裁剪操作逻辑"""
        if not self.image_path:
            QMessageBox.warning(self, "警告", "请先打开图片")
            return False
        
        try:
            # 获取裁剪坐标
            coords = self.crop_display.get_crop_coordinates()
            if not coords:
                raise CropError("无效的裁剪区域")
            
            x, y, w, h = coords
            
            # 加载并处理图片
            image = QImage(self.image_path)
            if image.isNull():
                raise ImageLoadError("无法读取原图")
            
            # 应用旋转
            if self.crop_display.crop_params.rotation_angle != 0:
                transform = QTransform().rotate(self.crop_display.crop_params.rotation_angle)
                image = image.transformed(transform, Qt.SmoothTransformation)
            
            # 执行裁剪
            cropped = image.copy(x, y, w, h)
            
            # 获取并应用水印
            watermark_settings = self.watermark_control.get_watermark_settings()
            marks = watermark_settings.get_active_marks()
            if marks:
                cropped = self.watermark_processor.apply_watermarks(cropped, watermark_settings)
                image = self.watermark_processor.apply_watermarks(image, watermark_settings)
            
            # 保存图片
            poster_path, thumb_path = ImageSaver.save_images(cropped, image, self.image_path)
            QMessageBox.information(self, "成功", f"裁剪成功！\n保存位置：{poster_path}")
            return True
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"裁剪失败：{e}")
            return False
    
    def cut_image(self):
        """修复：裁剪图片，使用公共逻辑"""
        self._perform_crop_operation()
    
    def cut_and_close(self):
        """修复：裁剪并关闭窗口，使用公共逻辑"""
        if self._perform_crop_operation():
            self.accept()
    
    def load_initial_image(self, image_path: str):
        """加载初始图片"""
        if image_path and os.path.exists(image_path):
            self.image_path = image_path
            self.crop_display.set_image(image_path)
    
    def set_watermark_options(self, enable_subtitle: bool = False, mark_type: str = "none"):
        """设置水印选项 - 用于命令行参数"""
        try:
            if enable_subtitle:
                self.watermark_control.sub_check.setChecked(True)
                
            if mark_type != "none":
                for button in self.watermark_control.mark_group.buttons():
                    if button.property("value") == mark_type:
                        button.setChecked(True)
                        break
        except Exception as e:
            print(f"设置水印选项失败: {e}")


# ================================ 程序入口 ================================
if __name__ == "__main__":
    try:
        # 启用高DPI支持
        if hasattr(Qt, "AA_EnableHighDpiScaling"):
            QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        if hasattr(Qt, "AA_UseHighDpiPixmaps"):
            QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        
        # 设置应用图标
        try:
            icon_path = get_resource_path("cg_crop.ico")
            if os.path.exists(icon_path):
                app.setWindowIcon(QIcon(icon_path))
        except Exception as e:
            print(f"设置应用图标失败: {e}")
        
        # 解析命令行参数
        parser = argparse.ArgumentParser(description="图片裁剪工具")
        parser.add_argument("--image", help="要处理的图片路径")
        parser.add_argument("--nfo-name", help="NFO文件基础名称") 
        parser.add_argument("--subtitle", action="store_true", help="是否添加字幕水印")
        parser.add_argument("--mark-type", choices=["none", "umr", "leak", "wuma", "youma"], 
                          default="none", help="水印类型")
        args = parser.parse_args()
        
        # 创建主窗口
        window = EmbyPosterCrop(nfo_base_name=args.nfo_name)
        
        # 加载图片和设置选项
        if args.image and os.path.exists(args.image):
            window.load_initial_image(args.image)
            
            # 设置水印选项
            window.set_watermark_options(args.subtitle, args.mark_type)
        
        window.show()
        sys.exit(app.exec_())
        
    except Exception as e:
        print(f"启动失败：{e}")
        sys.exit(1)