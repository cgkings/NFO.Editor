import os
import sys
import re
import logging
from datetime import datetime
from xml.etree import ElementTree as ET
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QTextEdit, QProgressBar,
    QFileDialog, QMessageBox,
)
from PyQt5.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass, field

# 配置常量
class Config:
    DEFAULT_FOLDER_FORMAT = "filename smart_actor"
    SUPPORTED_NFO_EXTENSIONS = ['.nfo']
    INVALID_FILENAME_CHARS = r'[\\/:*?"<>|]'
    APP_VERSION = "v9.7.1"
    WINDOW_MIN_SIZE = (900, 900)
    # 新增日志配置
    LOG_FOLDER = "log"
    LOG_DATE_FORMAT = "%Y%m%d_%H%M%S"
    LOG_FILE_FORMAT = "rename-{}.log"

    # 样式常量
    MAIN_STYLE = """
        QMainWindow { background-color: #f5f5f5; }
        QWidget { font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; }
        QLineEdit {
            padding: 8px; border: 1px solid #dcdcdc; border-radius: 6px;
            background-color: white;
        }
        QLineEdit:focus { border: 1px solid #2196F3; }
        QPushButton {
            padding: 8px 16px; border-radius: 6px; font-weight: bold;
            background-color: #f0f0f0; border: none;
        }
        QPushButton:hover { background-color: #e0e0e0; }
        QPushButton:pressed { background-color: #d0d0d0; }
        QTextEdit {
            border: 1px solid #dcdcdc; border-radius: 6px;
            background-color: white; padding: 8px;
            font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
            font-size: 12px;
        }
        QProgressBar {
            border: none; border-radius: 6px; background-color: #f0f0f0;
            text-align: center; min-height: 12px;
        }
        QProgressBar::chunk { border-radius: 6px; background-color: #2196F3; }
        QCheckBox { padding: 5px; }
        QCheckBox::indicator {
            width: 18px; height: 18px; border-radius: 4px; border: 2px solid #dcdcdc;
        }
        QCheckBox::indicator:checked {
            background-color: #2196F3; border-color: #2196F3;
        }
    """
    
    CONTAINER_STYLE = """
        background-color: white; border-radius: 8px; padding: 10px;
    """
    
    PRIMARY_BUTTON_STYLE = """
        QPushButton {
            background-color: #2196F3; color: white;
            font-size: 14px; font-weight: bold;
        }
        QPushButton:hover { background-color: #1976D2; }
        QPushButton:pressed { background-color: #0D47A1; }
    """

class LogManager:
    """日志管理器"""
    
    def __init__(self):
        self.log_file_path = None
        self.logger = None
        self._setup_logger()
    
    def _setup_logger(self):
        """设置日志记录器"""
        try:
            # 确定日志目录
            if getattr(sys, "frozen", False):
                # 打包后的exe文件
                base_dir = Path(sys.executable).parent
            else:
                # 开发环境
                base_dir = Path(__file__).parent
            
            log_dir = base_dir / Config.LOG_FOLDER
            log_dir.mkdir(exist_ok=True)
            
            # 生成日志文件名
            timestamp = datetime.now().strftime(Config.LOG_DATE_FORMAT)
            log_filename = Config.LOG_FILE_FORMAT.format(timestamp)
            self.log_file_path = log_dir / log_filename
            
            # 配置日志记录器
            self.logger = logging.getLogger('RenameLogger')
            self.logger.setLevel(logging.INFO)
            
            # 清除已有的处理器
            self.logger.handlers.clear()
            
            # 文件处理器
            file_handler = logging.FileHandler(
                self.log_file_path, 
                encoding='utf-8',
                mode='w'
            )
            file_handler.setLevel(logging.INFO)
            
            # 日志格式
            formatter = logging.Formatter(
                '%(asctime)s [%(levelname)s] %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(formatter)
            
            self.logger.addHandler(file_handler)
            
            # 记录开始信息
            self.logger.info("="*60)
            self.logger.info(f"批量改名工具 {Config.APP_VERSION} 日志开始")
            self.logger.info("="*60)
            
        except Exception as e:
            print(f"日志系统初始化失败: {e}")
            self.logger = None
    
    def log_info(self, message: str):
        """记录信息日志"""
        if self.logger:
            self.logger.info(message)
    
    def log_warning(self, message: str):
        """记录警告日志"""
        if self.logger:
            self.logger.warning(message)
    
    def log_error(self, message: str):
        """记录错误日志"""
        if self.logger:
            self.logger.error(message)
    
    def log_success(self, message: str):
        """记录成功操作日志"""
        if self.logger:
            self.logger.info(f"[SUCCESS] {message}")
    
    def close(self):
        """关闭日志记录器"""
        if self.logger:
            self.logger.info("="*60)
            self.logger.info("批量改名工具日志结束")
            self.logger.info("="*60)
            
            for handler in self.logger.handlers:
                handler.close()
                self.logger.removeHandler(handler)

@dataclass
class NFOFields:
    """NFO文件字段数据类 - 精简版"""
    # 基础字段
    title: str = ""
    number: str = ""
    filename: str = ""
    
    # 演员相关
    actor: str = ""
    smart_actor: str = ""
    
    # 制作信息
    director: str = ""
    series: str = ""
    studio: str = ""
    publisher: str = ""
    year: str = ""
    
    # 技术信息
    runtime: str = ""
    rating: str = ""
    mosaic: str = ""
    definition: str = ""
    four_k: str = ""


class NFOParser:
    """NFO文件解析器 - 精简版"""
    
    # 精简的字段映射配置
    FIELD_MAPPINGS = {
        'title': ['.//title'],
        'number': ['.//num', './/id', './/number'],
        'director': ['.//director'],
        'series': ['.//series', './/set'],
        'studio': ['.//studio'],
        'publisher': ['.//publisher'],
        'year': ['.//year'],
        'runtime': ['.//runtime'],
        'rating': ['.//rating'],
        'mosaic': ['.//mosaic'],
        'definition': ['.//definition', './/resolution'],
    }
    
    def __init__(self, actor_mapping: Optional[Dict[str, str]] = None):
        self.actor_mapping = actor_mapping or {}
    
    def parse_nfo_file(self, nfo_path: str) -> NFOFields:
        """解析NFO文件并返回字段对象"""
        try:
            tree = ET.parse(nfo_path)
            root = tree.getroot()
            fields = NFOFields()
            
            # 设置文件名
            fields.filename = Path(nfo_path).stem
            
            # 解析基本字段
            for field_name, xpath_list in self.FIELD_MAPPINGS.items():
                setattr(fields, field_name, self._find_first_valid_text(root, xpath_list))
            
            # 处理特殊字段
            self._process_special_fields(fields)
            
            # 解析演员信息
            self._parse_actors(root, fields)
            
            return fields
            
        except Exception as e:
            raise Exception(f"解析NFO文件失败 {nfo_path}: {e}")
    
    def _process_special_fields(self, fields: NFOFields):
        """处理特殊字段"""
        # 处理评分
        if fields.rating:
            try:
                rating_value = float(fields.rating)
                fields.rating = f"{rating_value:.1f}"
            except ValueError:
                fields.rating = ""
        
        # 处理4K标识
        fields.four_k = "4K" if any(keyword in fields.definition.lower() 
                                  for keyword in ['4k', '2160']) else ""
    
    def _parse_actors(self, root: ET.Element, fields: NFOFields):
        """解析演员信息"""
        actors = []
        
        for actor in root.findall(".//actor"):
            name_element = actor.find("name")
            if name_element is not None and name_element.text:
                original_name = name_element.text.strip()
                # 应用映射关系
                mapped_name = self.actor_mapping.get(original_name, original_name)
                actors.append(mapped_name)
        
        if actors:
            fields.actor = ",".join(actors)
            fields.smart_actor = self._generate_smart_actor(actors)
    
    def _generate_smart_actor(self, actors: List[str]) -> str:
        """生成智能演员显示 - 更加智能的逻辑"""
        count = len(actors)
        if count == 0:
            return ""
        elif count == 1:
            return actors[0]
        elif count == 2:
            return f"{actors[0]},{actors[1]}"
        elif count == 3:
            return f"{actors[0]},{actors[1]},{actors[2]}"
        else:  # 3个以上
            return f"{actors[0]},{actors[1]},{actors[2]}等演员"
    
    def _find_first_valid_text(self, root: ET.Element, xpath_list: List[str]) -> str:
        """从xpath列表中查找第一个有效的文本值"""
        for xpath in xpath_list:
            element = root.find(xpath)
            if element is not None and element.text:
                return element.text.strip()
        return ""


class NFOModifier:
    """NFO文件修改器 - 规范化增强版"""
    
    def __init__(self, actor_mapping: Dict[str, str], series_mapping: Optional[Dict[str, str]] = None):
        self.actor_mapping = actor_mapping
        self.series_mapping = series_mapping or {}
        
        # 标准字段顺序（符合NFO规范）
        self.standard_field_order = [
            'plot', 'outline', 'originalplot', 'tagline',
            'premiered', 'releasedate', 'release', 
            'num', 'title', 'originaltitle', 'sorttitle',
            'mpaa', 'customrating', 'countrycode',
            'actor', 'director', 'rating', 'criticrating', 'votes',
            'year', 'runtime',
            'set', 'series',  # series 放在 set 之后（关键修复）
            'studio', 'maker', 'publisher', 'label',
            'tag', 'genre',
            'poster', 'cover', 'trailer', 'website', 'javdbid'
        ]
    
    def modify_nfo_file(self, nfo_path: str) -> Tuple[bool, List[str], Dict[str, int], Dict[str, any]]:
        """修改NFO文件中的演员名称和系列信息，并规范化结构"""
        try:
            tree = ET.parse(nfo_path)
            root = tree.getroot()
            
            stats = {'actor': 0, 'tag': 0, 'genre': 0, 'series': 0, 'set': 0}
            detailed_logs = {}  # 存储详细修改信息
            modified = False
            all_actors = []
            
            # 记录NFO文件路径
            detailed_logs['file_path'] = nfo_path
            
            # 1. 处理演员相关元素
            for element_type, xpath in [('actor', './/actor'), ('tag', './/tag'), ('genre', './/genre')]:
                elem_modified, actors, count, changes = self._modify_elements_with_log(root, xpath, element_type == 'actor')
                if elem_modified:
                    modified = True
                    if element_type == 'actor':
                        all_actors.extend(actors)
                        stats['actor'] = len([a for a in actors if a])
                        detailed_logs['actor_changes'] = changes
                    else:
                        stats[element_type] = count
                        detailed_logs[f'{element_type}_changes'] = changes
            
            # 2. 处理系列信息（修复版）
            if self.series_mapping:
                series_modified, series_stats, series_logs = self._modify_series_with_log(root)
                if series_modified:
                    modified = True
                    stats.update(series_stats)
                    detailed_logs.update(series_logs)
            
            # 3. 规范化NFO结构
            structure_modified, structure_logs = self._normalize_nfo_structure_with_log(root)
            if structure_modified:
                modified = True
                detailed_logs['structure_changes'] = structure_logs
            
            if modified:
                tree.write(nfo_path, encoding="utf-8", xml_declaration=True)
            
            return modified, all_actors, stats, detailed_logs
            
        except Exception as e:
            raise Exception(f"修改NFO文件失败 {nfo_path}: {e}")
    
    def _modify_elements_with_log(self, root: ET.Element, xpath: str, is_actor: bool) -> Tuple[bool, List[str], int, List[str]]:
        """修改元素并记录详细变化"""
        modified, actors, count = False, [], 0
        changes = []  # 记录具体变化
        
        for element in root.findall(xpath):
            if is_actor:
                name_element = element.find("name")
                if name_element is not None and name_element.text:
                    original_name = name_element.text.strip()
                    mapped_name = self.actor_mapping.get(original_name, original_name)
                    
                    if mapped_name != original_name:
                        name_element.text = mapped_name
                        modified = True
                        changes.append(f"{original_name} → {mapped_name}")
                    
                    actors.append(mapped_name)
            else:
                if element.text and element.text.strip() in self.actor_mapping:
                    original_text = element.text.strip()
                    mapped_name = self.actor_mapping[original_text]
                    if mapped_name != original_text:
                        element.text = mapped_name
                        modified = True
                        count += 1
                        changes.append(f"{original_text} → {mapped_name}")
        
        return modified, actors, count, changes
    
    def _modify_series_with_log(self, root: ET.Element) -> Tuple[bool, Dict[str, int], Dict[str, str]]:
        """增强的系列修改功能 - 带详细日志"""
        stats = {'series': 0, 'set': 0, 'tag': 0, 'genre': 0}
        logs = {}
        modified = False
        
        # 获取番号
        number = self._find_first_valid_text(root, ['.//num', './/id', './/number'])
        if not number:
            return False, stats, logs
        
        # 查找对应的系列
        expected_series = self.series_mapping.get(number.strip())
        if not expected_series:
            return False, stats, logs
        
        # 1. 修复 series 字段
        series_change = self._update_series_field_with_log(root, expected_series)
        if series_change:
            modified = True
            stats['series'] = 1
            logs['series_change'] = series_change
        
        # 2. 修复 set 字段（规范结构）
        set_change = self._update_set_field_with_log(root, expected_series)
        if set_change:
            modified = True
            stats['set'] = 1
            logs['set_change'] = set_change
        
        # 3. 联动更新 tag 字段
        tag_change = self._update_series_in_tags_with_log(root, expected_series)
        if tag_change:
            modified = True
            stats['tag'] = 1
            logs['tag_change'] = tag_change
        
        # 4. 联动更新 genre 字段
        genre_change = self._update_series_in_genres_with_log(root, expected_series)
        if genre_change:
            modified = True
            stats['genre'] = 1
            logs['genre_change'] = genre_change
        
        return modified, stats, logs
    
    def _update_series_field_with_log(self, root: ET.Element, expected_series: str) -> Optional[str]:
        """更新 series 字段并记录变化"""
        series_element = root.find('.//series')
        
        if series_element is None:
            # 创建新的 series 元素
            series_element = ET.Element('series')
            series_element.text = expected_series
            # 先添加到末尾，稍后会通过规范化调整位置
            root.append(series_element)
            return f"空 → {expected_series}"
        else:
            # 更新现有 series 元素
            current_series = series_element.text or ""
            if current_series.strip() != expected_series:
                old_value = current_series.strip() or "空"
                series_element.text = expected_series
                return f"{old_value} → {expected_series}"
        
        return None
    
    def _update_set_field_with_log(self, root: ET.Element, expected_series: str) -> Optional[str]:
        """更新 set 字段（修复结构问题）并记录变化"""
        set_element = root.find('.//set')
        
        if set_element is None:
            # 创建规范的 set 元素结构
            set_element = ET.Element('set')
            name_element = ET.SubElement(set_element, 'name')
            name_element.text = expected_series
            root.append(set_element)
            return f"空 → {expected_series}"
        else:
            # 修复现有 set 元素结构
            name_element = set_element.find('name')
            
            if name_element is None:
                # 如果 set 直接包含文本，转换为规范结构
                old_text = set_element.text or "空"
                set_element.text = None
                name_element = ET.SubElement(set_element, 'name')
                name_element.text = expected_series
                return f"{old_text} → {expected_series} (结构修复)"
            else:
                # 更新现有 name 元素
                current_name = name_element.text or ""
                if current_name.strip() != expected_series:
                    old_value = current_name.strip() or "空"
                    name_element.text = expected_series
                    return f"{old_value} → {expected_series}"
        
        return None
    
    def _update_series_in_tags_with_log(self, root: ET.Element, expected_series: str) -> Optional[str]:
        """联动更新 tag 字段中的系列信息"""
        series_tag = f"系列: {expected_series}"
        
        # 查找现有的系列标签
        for tag in root.findall('.//tag'):
            if tag.text and tag.text.startswith('系列:'):
                if tag.text.strip() != series_tag:
                    old_value = tag.text.strip()
                    tag.text = series_tag
                    return f"{old_value} → {series_tag}"
                return None
        
        # 如果没有系列标签，创建新的
        new_tag = ET.Element('tag')
        new_tag.text = series_tag
        root.append(new_tag)
        return f"空 → {series_tag}"
    
    def _update_series_in_genres_with_log(self, root: ET.Element, expected_series: str) -> Optional[str]:
        """联动更新 genre 字段中的系列信息"""
        series_genre = f"系列: {expected_series}"
        
        # 查找现有的系列类型
        for genre in root.findall('.//genre'):
            if genre.text and genre.text.startswith('系列:'):
                if genre.text.strip() != series_genre:
                    old_value = genre.text.strip()
                    genre.text = series_genre
                    return f"{old_value} → {series_genre}"
                return None
        
        # 如果没有系列类型，创建新的
        new_genre = ET.Element('genre')
        new_genre.text = series_genre
        root.append(new_genre)
        return f"空 → {series_genre}"
    
    def _normalize_nfo_structure_with_log(self, root: ET.Element) -> Tuple[bool, List[str]]:
        """规范化NFO文件结构并记录变化"""
        logs = []
        modified = False
        
        # 1. 修复 actor 元素结构
        actor_fixes = self._fix_actor_elements_with_log(root)
        if actor_fixes:
            modified = True
            logs.extend(actor_fixes)
        
        # 2. 修复 set 元素结构
        set_fixes = self._fix_set_elements_with_log(root)
        if set_fixes:
            modified = True
            logs.extend(set_fixes)
        
        # 3. 重新排序所有元素
        reorder_result = self._reorder_elements_with_log(root)
        if reorder_result:
            modified = True
            logs.append(reorder_result)
        
        return modified, logs
    
    def _fix_actor_elements_with_log(self, root: ET.Element) -> List[str]:
        """修复 actor 元素结构并记录"""
        logs = []
        count = 0
        
        for actor in root.findall('.//actor'):
            name_element = actor.find('name')
            if name_element is None and actor.text:
                # 如果 actor 直接包含文本，转换为 name 子元素
                name_element = ET.SubElement(actor, 'name')
                name_element.text = actor.text.strip()
                actor.text = None
                count += 1
            
            # 确保有 type 子元素
            type_element = actor.find('type')
            if type_element is None:
                type_element = ET.SubElement(actor, 'type')
                type_element.text = 'Actor'
        
        if count > 0:
            logs.append(f"修复actor元素结构: {count}个")
        
        return logs
    
    def _fix_set_elements_with_log(self, root: ET.Element) -> List[str]:
        """修复 set 元素结构并记录"""
        logs = []
        count = 0
        
        for set_elem in root.findall('.//set'):
            name_element = set_elem.find('name')
            if name_element is None and set_elem.text:
                # 如果 set 直接包含文本，转换为 name 子元素
                name_element = ET.SubElement(set_elem, 'name')
                name_element.text = set_elem.text.strip()
                set_elem.text = None
                count += 1
        
        if count > 0:
            logs.append(f"修复set元素结构: {count}个")
        
        return logs
    
    def _reorder_elements_with_log(self, root: ET.Element) -> Optional[str]:
        """按标准顺序重新排列元素并记录"""
        original_order = [elem.tag for elem in root]
        
        # 按标准顺序重新排列元素
        all_elements = list(root)
        ordered_elements = []
        remaining_elements = all_elements.copy()
        
        # 按标准顺序添加元素
        for field_name in self.standard_field_order:
            matching_elements = [elem for elem in remaining_elements if elem.tag == field_name]
            ordered_elements.extend(matching_elements)
            for elem in matching_elements:
                remaining_elements.remove(elem)
        
        # 添加不在标准顺序中的元素
        ordered_elements.extend(remaining_elements)
        
        # 重新构建 root
        root.clear()
        for elem in ordered_elements:
            root.append(elem)
        
        # 检查是否有变化
        new_order = [elem.tag for elem in root]
        if original_order != new_order:
            return f"元素重排序: {len(all_elements)}个元素"
        
        return None
    
    def _find_first_valid_text(self, root: ET.Element, xpath_list: List[str]) -> str:
        """从xpath列表中查找第一个有效的文本值"""
        for xpath in xpath_list:
            element = root.find(xpath)
            if element is not None and element.text:
                return element.text.strip()
        return ""


class FolderRenamer:
    """文件夹重命名器"""
    
    def __init__(self, format_string: str = ""):
        self.format_string = format_string.strip() or Config.DEFAULT_FOLDER_FORMAT
    
    def generate_folder_name(self, fields: NFOFields) -> str:
        """根据字段和格式生成文件夹名称"""
        if not self.format_string:
            return fields.filename
        
        result = self.format_string
        
        # 获取所有字段（排除私有属性）
        field_dict = {
            name: getattr(fields, name) 
            for name in dir(fields) 
            if not name.startswith('_') and not callable(getattr(fields, name))
        }
        
        # 添加特殊字段映射
        field_dict['4k'] = fields.four_k
        
        # 按字段名长度降序替换，避免短字段名影响长字段名
        for field_name, field_value in sorted(field_dict.items(), key=lambda x: len(x[0]), reverse=True):
            pattern = r'(?<!\w)' + re.escape(field_name) + r'(?!\w)'
            if re.search(pattern, result):
                clean_value = self._clean_filename(str(field_value)) if field_value else ""
                result = re.sub(pattern, clean_value, result)
        
        # 清理最终结果
        result = self._clean_filename(result)
        result = re.sub(r'\s+', ' ', result).strip()
        
        return result if result else fields.filename
    
    def rename_folder(self, folder_path: str, new_name: str) -> bool:
        """重命名文件夹"""
        try:
            parent_path = Path(folder_path).parent
            new_path = parent_path / new_name
            
            if new_path.exists() and new_path != Path(folder_path):
                raise Exception(f"目标文件夹已存在: {new_path}")
            
            Path(folder_path).rename(new_path)
            return True
            
        except Exception as e:
            raise Exception(f"重命名文件夹失败: {e}")
    
    def _clean_filename(self, filename: str) -> str:
        """清理文件名中的非法字符"""
        return re.sub(Config.INVALID_FILENAME_CHARS, "_", filename)


class RenameWorker(QThread):
    """重命名工作线程"""    
    progressUpdated = pyqtSignal(int, int)
    logUpdated = pyqtSignal(str)  # UI显示日志（仅显示有修改的操作）
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, directory: str, actor_mapping: Dict[str, str], 
                 rename_folders: bool, folder_format: str = "",
                 series_mapping: Optional[Dict[str, str]] = None):
        super().__init__()
        self.directory = directory
        self.actor_mapping = actor_mapping
        self.series_mapping = series_mapping or {}
        self.rename_folders = rename_folders
        
        # 初始化组件
        self.nfo_parser = NFOParser(actor_mapping)
        self.nfo_modifier = NFOModifier(actor_mapping, series_mapping)
        self.folder_renamer = FolderRenamer(folder_format)
        
        # 初始化日志管理器
        self.log_manager = LogManager()

    def run(self):
        try:
            self.log_manager.log_info(f"开始处理目录: {self.directory}")
            self.log_manager.log_info(f"演员映射数量: {len(self.actor_mapping)}")
            self.log_manager.log_info(f"系列映射数量: {len(self.series_mapping)}")
            self.log_manager.log_info(f"重命名文件夹: {'是' if self.rename_folders else '否'}")
            
            self._process_directory()
            
            self.log_manager.log_info("所有处理完成")
            self.log_manager.close()
            self.finished.emit()
        except Exception as e:
            self.log_manager.log_error(f"处理过程出错: {e}")
            self.log_manager.close()
            self.error.emit(str(e))

    def _process_directory(self):
        """处理目录"""
        folders_to_process = self._collect_folders_with_nfo()
        total_folders = len(folders_to_process)
        
        log_msg = f"找到 {total_folders} 个包含NFO文件的文件夹"
        self.log_manager.log_info(log_msg)
        
        if total_folders == 0:
            no_folder_msg = "没有找到需要处理的文件夹"
            self.log_manager.log_info(no_folder_msg)
            return
        
        for i, (folder_path, nfo_path) in enumerate(folders_to_process, 1):
            try:
                self._process_single_folder(folder_path, nfo_path, i, total_folders)
            except Exception as e:
                error_msg = f"处理文件夹 {folder_path} 时出错: {e}"
                self.log_manager.log_error(error_msg)

    def _collect_folders_with_nfo(self) -> List[Tuple[str, str]]:
        """收集包含NFO文件的文件夹"""
        folders_with_nfo = []
        
        for root, dirs, _ in os.walk(self.directory):
            for folder in dirs:
                folder_path = os.path.join(root, folder)
                nfo_path = self._find_nfo_file(folder_path)
                
                if nfo_path and Path(nfo_path).parent == Path(folder_path):
                    folders_with_nfo.append((folder_path, nfo_path))
        
        return folders_with_nfo
    
    def _process_single_folder(self, folder_path: str, nfo_path: str, current: int, total: int):
        """处理单个文件夹 - 优化日志版"""
        folder_name = Path(folder_path).name
        nfo_name = Path(nfo_path).name
        
        # 详细日志：开始处理
        start_msg = f"[{current}/{total}] 开始处理文件夹: {folder_name}"
        self.log_manager.log_info(start_msg)
        self.log_manager.log_info(f"NFO文件路径: {nfo_path}")
        
        # 解析NFO文件
        try:
            nfo_fields = self.nfo_parser.parse_nfo_file(nfo_path)
            self.log_manager.log_info(f"成功解析NFO文件: {nfo_name}")
        except Exception as e:
            error_msg = f"解析NFO文件失败: {nfo_name} - {e}"
            self.log_manager.log_error(error_msg)
            return
        
        # 修改NFO文件信息
        nfo_modified, modified_fields = self._modify_nfo_info_optimized(nfo_path, nfo_name)
        
        # 重命名文件夹
        folder_renamed = False
        if self.rename_folders:
            folder_renamed = self._rename_folder_if_needed_optimized(folder_path, nfo_fields, folder_name)
        
        # UI日志：只显示有变化的操作
        if nfo_modified or folder_renamed:
            ui_messages = []
            if nfo_modified:
                ui_messages.append(f"{', '.join(modified_fields)}字段已修改")
            if folder_renamed:
                ui_messages.append("文件夹已重命名")
            
            ui_msg = f"{nfo_name} - {', '.join(ui_messages)}"
            self.logUpdated.emit(ui_msg)
        
        # 更新进度
        self.progressUpdated.emit(current, total)
    
    def _modify_nfo_info_optimized(self, nfo_path: str, nfo_name: str) -> Tuple[bool, List[str]]:
        """修改NFO文件信息 - 优化版"""
        try:
            modified, new_actors, stats, detailed_logs = self.nfo_modifier.modify_nfo_file(nfo_path)
            
            # 详细日志：记录所有信息
            if 'structure_changes' in detailed_logs:
                for change in detailed_logs['structure_changes']:
                    self.log_manager.log_info(f"NFO规范化 - {change}")
            
            modified_fields = []
            
            if modified:
                # 演员信息
                if stats['actor'] > 0:
                    modified_fields.append("演员")
                    if 'actor_changes' in detailed_logs:
                        for change in detailed_logs['actor_changes']:
                            self.log_manager.log_success(f"演员字段修改: {change}")
                
                # 标签信息
                if stats['tag'] > 0:
                    modified_fields.append("标签")
                    if 'tag_changes' in detailed_logs:
                        for change in detailed_logs['tag_changes']:
                            self.log_manager.log_success(f"标签字段修改: {change}")
                
                # 类型信息
                if stats['genre'] > 0:
                    modified_fields.append("类型")
                    if 'genre_changes' in detailed_logs:
                        for change in detailed_logs['genre_changes']:
                            self.log_manager.log_success(f"类型字段修改: {change}")
                
                # 系列信息
                if stats['series'] > 0:
                    modified_fields.append("系列")
                    if 'series_change' in detailed_logs:
                        self.log_manager.log_success(f"系列字段修改: {detailed_logs['series_change']}")
                    if 'set_change' in detailed_logs:
                        self.log_manager.log_success(f"set字段修改: {detailed_logs['set_change']}")
                
                self.log_manager.log_success(f"NFO文件修改完成: {nfo_name}")
            else:
                self.log_manager.log_info(f"NFO文件无需修改: {nfo_name}")
            
            return modified, modified_fields
            
        except Exception as e:
            error_msg = f"修改NFO文件失败: {nfo_name} - {e}"
            self.log_manager.log_error(error_msg)
            return False, []
    
    def _rename_folder_if_needed_optimized(self, folder_path: str, nfo_fields: NFOFields, folder_name: str) -> bool:
        """根据需要重命名文件夹 - 优化版"""
        try:
            expected_name = self.folder_renamer.generate_folder_name(nfo_fields)
            
            if folder_name != expected_name:
                self.folder_renamer.rename_folder(folder_path, expected_name)
                self.log_manager.log_success(f"文件夹重命名: {folder_name} → {expected_name}")
                return True
            else:
                self.log_manager.log_info(f"文件夹名称符合规范: {folder_name}")
                return False
                
        except Exception as e:
            error_msg = f"重命名文件夹失败: {folder_name} - {e}"
            self.log_manager.log_error(error_msg)
            return False
    
    def _find_nfo_file(self, folder_path: str) -> Optional[str]:
        """在文件夹中查找NFO文件"""
        for file_path in Path(folder_path).iterdir():
            if file_path.is_file() and file_path.suffix.lower() in Config.SUPPORTED_NFO_EXTENSIONS:
                return str(file_path)
        return None


class ActorMappingLoader:
    """演员映射加载器"""
    
    @staticmethod
    def find_mapping_file() -> Optional[str]:
        """查找映射文件"""
        # 确定基础路径
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).parent
            base_path = Path(sys._MEIPASS)
        else:
            exe_dir = base_path = Path(__file__).parent
        
        # 优先查找外部配置文件
        external_mapping = exe_dir / "mapping_actor.xml"
        if external_mapping.exists():
            return str(external_mapping)
        
        # 查找内置配置文件
        internal_mapping = base_path / "mapping_actor.xml"
        return str(internal_mapping) if internal_mapping.exists() else None
    
    @staticmethod
    def load_mapping(mapping_file: str) -> Dict[str, str]:
        """从XML文件加载演员映射"""
        mapping = {}
        
        try:
            context = ET.iterparse(mapping_file, events=("start",))
            for event, elem in context:
                if elem.tag == "a":
                    zh_cn = elem.get("zh_cn")
                    if zh_cn:
                        keywords = elem.get("keyword", "").strip(",").split(",")
                        for keyword in (k.strip() for k in keywords if k.strip()):
                            mapping[keyword] = zh_cn
                elem.clear()
        
        except Exception as e:
            raise Exception(f"加载映射文件失败: {e}")
        
        return mapping


class SeriesMappingLoader:
    """系列映射加载器"""
    
    @staticmethod
    def find_series_mapping_file() -> Optional[str]:
        """查找系列映射文件"""
        # 确定基础路径
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).parent
            base_path = Path(sys._MEIPASS)
        else:
            exe_dir = base_path = Path(__file__).parent
        
        # 优先查找外部配置文件
        external_mapping = exe_dir / "series_mapping.xml"
        if external_mapping.exists():
            return str(external_mapping)
        
        # 查找内置配置文件
        internal_mapping = base_path / "series_mapping.xml"
        return str(internal_mapping) if internal_mapping.exists() else None
    
    @staticmethod
    def load_series_mapping(mapping_file: str) -> Dict[str, str]:
        """从XML文件加载系列映射"""
        mapping = {}
        
        try:
            context = ET.iterparse(mapping_file, events=("start",))
            for event, elem in context:
                if elem.tag == "map":
                    code = elem.get("code")
                    series = elem.get("series")
                    if code and series:
                        mapping[code.strip()] = series.strip()
                elem.clear()
        
        except Exception as e:
            raise Exception(f"加载系列映射文件失败: {e}")
        
        return mapping


class RenameToolGUI(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.actor_mapping = {}
        self.series_mapping = {}
        self.mapping_file_path = None
        self.series_mapping_file_path = None
        self.worker = None
        self.init_ui()
        self.load_mappings()

    def init_ui(self):
        """初始化用户界面"""
        self.setWindowTitle(f"大锤 批量改名工具 {Config.APP_VERSION}")
        self.setMinimumSize(*Config.WINDOW_MIN_SIZE)
        self.setStyleSheet(Config.MAIN_STYLE)
        
        # 居中窗口和设置图标
        self._center_window()
        self._setup_icon()
        
        # 创建界面
        self._create_widgets()
    
    def _center_window(self):
        """窗口居中"""
        screen_geometry = QApplication.desktop().availableGeometry()
        x = (screen_geometry.width() - self.width()) // 2
        y = (screen_geometry.height() - self.height()) // 2
        self.move(x, y)
    
    def _setup_icon(self):
        """设置窗口图标"""
        try:
            if getattr(sys, "frozen", False):
                application_path = sys._MEIPASS
            else:
                application_path = os.path.dirname(os.path.abspath(__file__))
            
            icon_path = os.path.join(application_path, "chuizi.ico")
            if os.path.exists(icon_path):
                icon = QIcon(icon_path)
                for size in [16, 32, 64, 128]:
                    icon.addFile(icon_path, QSize(size, size))
                self.setWindowIcon(icon)
        except Exception as e:
            print(f"图标设置失败: {e}")
    
    def _create_widgets(self):
        """创建界面组件"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 添加各个组件
        layout.addWidget(self._create_path_container())
        layout.addWidget(self._create_options_container())
        
        # 映射文件路径显示
        self.mapping_label = QLabel()
        self.mapping_label.setStyleSheet(f"color: #2196F3; {Config.CONTAINER_STYLE}")
        layout.addWidget(self.mapping_label)
        
        # 系列映射文件路径显示
        self.series_mapping_label = QLabel()
        self.series_mapping_label.setStyleSheet(f"color: #2196F3; {Config.CONTAINER_STYLE}")
        layout.addWidget(self.series_mapping_label)
        
        layout.addWidget(self._create_log_container(), stretch=1)
        layout.addWidget(self._create_bottom_container())
    
    def _create_container(self, name: str) -> QWidget:
        """创建通用容器"""
        container = QWidget()
        container.setObjectName(name)
        container.setStyleSheet(f"QWidget#{name} {{ {Config.CONTAINER_STYLE} }}")
        return container
    
    def _create_path_container(self):
        """创建路径选择容器"""
        container = self._create_container("pathContainer")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        
        layout.addWidget(QLabel("工作目录："))
        
        self.path_entry = QLineEdit()
        self.path_entry.setText(os.path.dirname(os.path.abspath(__file__)))
        layout.addWidget(self.path_entry)
        
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self.browse_folder)
        layout.addWidget(browse_btn)
        
        return container
    
    def _create_options_container(self):
        """创建选项容器"""
        container = self._create_container("optionsContainer")
        layout = QVBoxLayout(container)
        
        # 第一行：演员映射选项
        first_row = QHBoxLayout()
        self.modify_actors_cb = QCheckBox("修改演员信息（应用演员映射）")
        self.modify_actors_cb.setChecked(True)  # 默认选中
        first_row.addWidget(self.modify_actors_cb)
        first_row.addStretch()
        layout.addLayout(first_row)
        
        # 第二行：系列映射选项
        second_row = QHBoxLayout()
        self.modify_series_cb = QCheckBox("修改系列信息（应用系列映射）")
        self.modify_series_cb.setChecked(True)  # 默认选中
        second_row.addWidget(self.modify_series_cb)
        second_row.addStretch()
        layout.addLayout(second_row)
        
        # 第三行：重命名文件夹选项
        third_row = QHBoxLayout()
        self.rename_folders_cb = QCheckBox("同时重命名文件夹")
        self.rename_folders_cb.setChecked(True)  # 默认选中
        third_row.addWidget(self.rename_folders_cb)
        third_row.addStretch()
        layout.addLayout(third_row)
        
        # 文件夹命名格式
        format_row = QHBoxLayout()
        format_row.addWidget(QLabel("文件夹命名格式："))
        
        self.folder_format_entry = QLineEdit()
        self.folder_format_entry.setText(Config.DEFAULT_FOLDER_FORMAT)
        self.folder_format_entry.setPlaceholderText("例如: number smart_actor 或 filename smart_actor")
        format_row.addWidget(self.folder_format_entry)
        
        help_btn = QPushButton("说明")
        help_btn.setMaximumWidth(80)
        help_btn.clicked.connect(self.show_field_help)
        format_row.addWidget(help_btn)
        
        layout.addLayout(format_row)
        
        return container
    
    def _create_log_container(self):
        """创建日志容器"""
        container = self._create_container("logContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        
        return container
    
    def _create_bottom_container(self):
        """创建底部容器"""
        container = self._create_container("bottomContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setAlignment(Qt.AlignCenter)
        self.progress_bar.setMinimumHeight(20)
        layout.addWidget(self.progress_bar)
        
        # 执行按钮
        execute_btn = QPushButton("执行")
        execute_btn.setMinimumHeight(45)
        execute_btn.setStyleSheet(Config.PRIMARY_BUTTON_STYLE)
        execute_btn.clicked.connect(self.execute_rename)
        layout.addWidget(execute_btn)
        
        return container
        
    def show_field_help(self):
        """显示字段帮助"""
        help_text = """
支持的字段名称：

基础字段：
• title - 标题
• number - 番号
• filename - nfo文件名（不含扩展名）

演员相关：
• actor - 所有演员（多个用逗号分隔）
• smart_actor - 智能演员显示（推荐使用）
├─ 1个演员：直接显示演员名
├─ 2个演员：演员1,演员2
├─ 3个演员：演员1,演员2,演员3
└─ 3个以上：演员1,演员2,演员3等演员

制作信息：
• director - 导演
• series - 系列
• studio - 片商
• publisher - 发行商
• year - 年份

技术信息：
• runtime - 时长
• rating - 评分
• mosaic - 有码/无码
• definition - 分辨率
• 4k - 4K标识

使用示例：
• "number smart_actor" → "PRED-001 桥本有菜"
• "filename smart_actor" → "PRED-001 桥本有菜"（使用NFO文件名）
• "smart_actor title rating" → "桥本有菜等演员 完全主观 9.2"

注意：
• smart_actor字段会根据演员数量智能调整显示方式
• 所有演员名都会自动应用映射关系
        """
                
        QMessageBox.information(self, "字段说明", help_text)
    
    def load_mappings(self):
        """加载所有映射文件"""
        self.load_actor_mapping()
        self.load_series_mapping()
    
    def load_actor_mapping(self):
        """加载演员映射"""
        try:
            self.mapping_file_path = ActorMappingLoader.find_mapping_file()
            if self.mapping_file_path:
                self.actor_mapping = ActorMappingLoader.load_mapping(self.mapping_file_path)
                self.log_text.append(f"成功加载 {len(self.actor_mapping)} 个演员映射关系")
                
                # 更新映射文件路径显示
                if "mapping_actor.xml" in self.mapping_file_path and sys.executable not in self.mapping_file_path:
                    self.mapping_label.setText(f"演员映射 (外部): {self.mapping_file_path}")
                else:
                    self.mapping_label.setText(f"演员映射 (内置): {self.mapping_file_path}")
            else:
                self.mapping_label.setText("演员映射: 未找到配置文件")
                self.log_text.append("警告：未找到演员映射配置文件")
        except Exception as e:
            self.mapping_label.setText("演员映射: 配置文件加载失败")
            self.log_text.append(f"加载演员映射文件出错: {e}")
    
    def load_series_mapping(self):
        """加载系列映射"""
        try:
            self.series_mapping_file_path = SeriesMappingLoader.find_series_mapping_file()
            if self.series_mapping_file_path:
                self.series_mapping = SeriesMappingLoader.load_series_mapping(self.series_mapping_file_path)
                self.log_text.append(f"成功加载 {len(self.series_mapping)} 个系列映射关系")
                
                # 更新系列映射文件路径显示
                if "series_mapping.xml" in self.series_mapping_file_path and sys.executable not in self.series_mapping_file_path:
                    self.series_mapping_label.setText(f"系列映射 (外部): {self.series_mapping_file_path}")
                else:
                    self.series_mapping_label.setText(f"系列映射 (内置): {self.series_mapping_file_path}")
            else:
                self.series_mapping_label.setText("系列映射: 未找到配置文件")
                self.log_text.append("提示：未找到系列映射配置文件，可通过javdb爬虫生成")
        except Exception as e:
            self.series_mapping_label.setText("系列映射: 配置文件加载失败")
            self.log_text.append(f"加载系列映射文件出错: {e}")
   
    def browse_folder(self):
        """浏览文件夹"""
        folder = QFileDialog.getExistingDirectory(self, "选择工作目录")
        if folder:
            self.path_entry.setText(folder)
    
    def execute_rename(self):
        """执行重命名操作"""
        if hasattr(self, "worker") and self.worker and self.worker.isRunning():
            return
        
        directory = self.path_entry.text().strip()
        if not directory or not os.path.isdir(directory):
            QMessageBox.critical(self, "错误", f"路径 '{directory}' 不是一个有效的目录")
            return
        
        # 检查是否至少有一个映射文件可用
        has_actor_mapping = self.modify_actors_cb.isChecked() and self.mapping_file_path and os.path.exists(self.mapping_file_path)
        has_series_mapping = self.modify_series_cb.isChecked() and self.series_mapping_file_path and os.path.exists(self.series_mapping_file_path)
        
        if not has_actor_mapping and not has_series_mapping and not self.rename_folders_cb.isChecked():
            QMessageBox.critical(self, "错误", "请至少选择一项操作：修改演员信息、修改系列信息或重命名文件夹")
            return
        
        try:
            self.log_text.clear()
            self.progress_bar.setValue(0)
            
            # 简单的开始提示
            self.log_text.append("开始处理，只显示有修改的文件...")
            self.log_text.append("")
            
            folder_format = self.folder_format_entry.text().strip() or Config.DEFAULT_FOLDER_FORMAT
            
            # 准备映射数据
            actor_mapping = self.actor_mapping if has_actor_mapping else {}
            series_mapping = self.series_mapping if has_series_mapping else {}
            
            self.worker = RenameWorker(
                directory, actor_mapping,
                self.rename_folders_cb.isChecked(), folder_format,
                series_mapping
            )
            
            self.worker.progressUpdated.connect(self.update_progress)
            self.worker.logUpdated.connect(self.update_ui_log)
            self.worker.finished.connect(self.on_worker_finished)
            self.worker.error.connect(self.handle_error)
            
            self.worker.start()
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"处理过程中出现错误: {e}")
            self.log_text.append("处理出错")
    
    def update_progress(self, current: int, total: int):
        """更新进度条"""
        if total > 0:
            progress = int((current / total) * 100)
            self.progress_bar.setValue(progress)
            self.progress_bar.setFormat(f"{current}/{total} ({progress}%)")
        else:
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("0/0 (0%)")

    def on_worker_finished(self):
        """工作线程完成"""
        self.log_text.append("")
        self.log_text.append("🎉 所有处理完成！")
        self.progress_bar.setFormat("完成")
        
        # 显示日志文件路径
        if hasattr(self.worker, 'log_manager') and self.worker.log_manager.log_file_path:
            self.log_text.append(f"详细日志: {self.worker.log_manager.log_file_path}")
    
    def handle_error(self, error_message: str):
        """处理工作线程错误"""
        QMessageBox.critical(self, "错误", f"处理过程中出现错误: {error_message}")
        self.log_text.append(f"处理出错: {error_message}")

    def update_ui_log(self, message: str):
        """更新UI日志 - 只显示有变化的操作"""
        if message:
            self.log_text.append(message)
            
            # 确保最新日志可见
            scroll_bar = self.log_text.verticalScrollBar()
            scroll_bar.setValue(scroll_bar.maximum())


def start_rename_process(directory: Optional[str] = None):
    """启动重命名程序"""
    # 启用高DPI支持
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    
    app = QApplication(sys.argv)
    window = RenameToolGUI()
    
    if directory:
        window.path_entry.setText(directory)
    
    window.show()
    sys.exit(app.exec_())


def create_rename_worker(directory: str, actor_mapping: Dict[str, str], 
                        rename_folders: bool, folder_format: str = "",
                        series_mapping: Optional[Dict[str, str]] = None) -> RenameWorker:
    """便利函数：创建RenameWorker实例，保持向后兼容性"""
    return RenameWorker(directory, actor_mapping, rename_folders, folder_format, series_mapping)


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            directory_path = sys.argv[1]
            if not os.path.isdir(directory_path):
                QMessageBox.critical(
                    None, "错误", f"路径 '{directory_path}' 不是一个有效的目录"
                )
                sys.exit(1)
            start_rename_process(directory_path)
        else:
            start_rename_process()
    except Exception as e:
        QMessageBox.critical(None, "错误", f"程序运行出错：{e}")
        sys.exit(1)