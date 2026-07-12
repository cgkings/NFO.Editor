import os
import sys
import re
import logging
from datetime import datetime
from xml.etree import ElementTree as ET
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QTextEdit, QProgressBar,
    QFileDialog, QMessageBox,
)
from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass
from nfo_utils import (
    normalize_catalog_number,
    parse_xml_file,
    preferred_nfo_from_names,
    read_series_text,
    same_path,
    write_xml_root_atomic,
)

# 配置常量
class Config:
    DEFAULT_FOLDER_FORMAT = "filename smart_actor"
    SUPPORTED_NFO_EXTENSIONS = ['.nfo']
    INVALID_FILENAME_CHARS = r'[\\/:*?"<>|]'
    APP_VERSION = "v9.8.1"
    WINDOW_MIN_SIZE = (900, 900)
    # 日志配置
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

# 公共工具类
class PathUtils:
    """路径处理工具类"""
    
    @staticmethod
    def get_application_paths() -> Tuple[Path, Path]:
        """获取应用程序路径"""
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).parent
            base_path = Path(sys._MEIPASS)
        else:
            exe_dir = base_path = Path(__file__).parent
        return exe_dir, base_path

class XMLUtils:
    """XML处理工具类"""
    
    @staticmethod
    def find_first_valid_text(root: ET.Element, xpath_list: List[str]) -> str:
        """从xpath列表中查找第一个有效的文本值"""
        for xpath in xpath_list:
            element = root.find(xpath)
            if element is not None and element.text:
                return element.text.strip()
        return ""
    
    @staticmethod
    def insert_element_before_reference(root: ET.Element, new_element: ET.Element, 
                                      reference_tags: List[str]) -> bool:
        """
        在参考标签前插入元素 - 简化策略
        
        Args:
            root: 根元素
            new_element: 要插入的新元素
            reference_tags: 参考标签列表（按优先级排序）
        
        Returns:
            bool: 是否成功插入到指定位置
        """
        # 按优先级查找参考元素
        for ref_tag in reference_tags:
            ref_elements = root.findall(f'.//{ref_tag}')
            if ref_elements:
                # 找到第一个参考元素，在其前插入
                first_ref = ref_elements[0]
                insert_position = list(root).index(first_ref)
                root.insert(insert_position, new_element)
                return True
        
        # 没有找到任何参考元素，添加到末尾
        root.append(new_element)
        return False

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
            exe_dir, base_path = PathUtils.get_application_paths()
            log_dir = exe_dir / Config.LOG_FOLDER
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
            
            for handler in list(self.logger.handlers):
                handler.close()
                self.logger.removeHandler(handler)

@dataclass
class NFOFields:
    """NFO文件字段数据类"""
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

# 映射加载器基类
class BaseMappingLoader:
    """映射加载器基类"""
    
    def __init__(self, filename: str, display_name: str):
        self.filename = filename
        self.display_name = display_name
        self._mapping_cache = None
        self._file_path_cache = None
    
    def find_mapping_file(self) -> Optional[str]:
        """查找映射文件"""
        if self._file_path_cache is not None:
            return self._file_path_cache
        
        exe_dir, base_path = PathUtils.get_application_paths()
        
        # 优先查找外部配置文件
        external_mapping = exe_dir / self.filename
        if external_mapping.exists():
            self._file_path_cache = str(external_mapping)
            return self._file_path_cache
        
        # 查找内置配置文件
        internal_mapping = base_path / self.filename
        if internal_mapping.exists():
            self._file_path_cache = str(internal_mapping)
            return self._file_path_cache
        
        self._file_path_cache = None
        return None
    
    def load_mapping(self, mapping_file: str = None) -> Dict[str, str]:
        """从XML文件加载映射"""
        if self._mapping_cache is not None and mapping_file is None:
            return self._mapping_cache
        
        if mapping_file is None:
            mapping_file = self.find_mapping_file()
            if not mapping_file:
                return {}
        
        try:
            self._mapping_cache = self._parse_mapping_file(mapping_file)
            return self._mapping_cache
        except Exception as e:
            raise Exception(f"加载{self.display_name}映射文件失败: {e}")
    
    def _parse_mapping_file(self, mapping_file: str) -> Dict[str, str]:
        """解析映射文件 - 子类需要实现"""
        raise NotImplementedError("子类需要实现此方法")
    
    def get_file_type(self) -> str:
        """获取文件类型标识"""
        file_path = self.find_mapping_file()
        if not file_path:
            return "未找到"
        
        exe_dir, _ = PathUtils.get_application_paths()
        return "外部" if file_path.startswith(str(exe_dir)) and self.filename in file_path else "内置"

class ActorMappingLoader(BaseMappingLoader):
    """演员映射加载器"""
    
    def __init__(self):
        super().__init__("mapping_actor.xml", "演员")
    
    def _parse_mapping_file(self, mapping_file: str) -> Dict[str, str]:
        """解析演员映射文件"""
        mapping = {}
        context = ET.iterparse(mapping_file, events=("start",))
        for event, elem in context:
            if elem.tag == "a":
                zh_cn = elem.get("zh_cn")
                if zh_cn:
                    keywords = elem.get("keyword", "").strip(",").split(",")
                    for keyword in (k.strip() for k in keywords if k.strip()):
                        mapping[keyword] = zh_cn
            elem.clear()
        return mapping

class SeriesMappingLoader(BaseMappingLoader):
    """Load series mappings with catalog-number normalization and conflict checks."""

    def __init__(self):
        super().__init__("series_mapping.xml", "系列")

    def _parse_mapping_file(self, mapping_file: str) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        conflicts: List[str] = []
        context = ET.iterparse(mapping_file, events=("start",))
        for _event, elem in context:
            if elem.tag == "map":
                raw_code = (elem.get("code") or "").strip()
                series = (elem.get("series") or "").strip()
                key = normalize_catalog_number(raw_code)
                if key and series:
                    existing = mapping.get(key)
                    if existing and existing != series:
                        conflicts.append(
                            f"{raw_code}: {existing} / {series}"
                        )
                    else:
                        mapping[key] = series
            elem.clear()
        if conflicts:
            preview = "；".join(conflicts[:8])
            more = f"，另有 {len(conflicts) - 8} 项" if len(conflicts) > 8 else ""
            raise ValueError(f"系列映射存在番号冲突：{preview}{more}")
        return mapping


class NFOParser:
    """Parse NFO fields from a file or an already parsed XML root."""

    FIELD_MAPPINGS = {
        "title": ["title"],
        "number": ["num", "id", "number"],
        "director": ["director"],
        "studio": ["studio"],
        "publisher": ["publisher"],
        "year": ["year"],
        "runtime": ["runtime"],
        "rating": ["rating"],
        "mosaic": ["mosaic"],
        "definition": ["definition", "resolution"],
    }

    def __init__(self, actor_mapping: Optional[Dict[str, str]] = None):
        self.actor_mapping = actor_mapping or {}

    def parse_nfo_file(self, nfo_path: str) -> NFOFields:
        try:
            tree = parse_xml_file(nfo_path)
            return self.parse_root(tree.getroot(), nfo_path)
        except Exception as exc:
            raise Exception(f"解析NFO文件失败 {nfo_path}: {exc}") from exc

    def parse_root(self, root: ET.Element, nfo_path: str) -> NFOFields:
        fields = NFOFields()
        fields.filename = Path(nfo_path).stem
        for field_name, xpath_list in self.FIELD_MAPPINGS.items():
            setattr(
                fields,
                field_name,
                XMLUtils.find_first_valid_text(root, xpath_list),
            )
        fields.series = read_series_text(root)
        self._process_special_fields(fields)
        self._parse_actors(root, fields)
        return fields

    @staticmethod
    def _process_special_fields(fields: NFOFields):
        if fields.rating:
            try:
                fields.rating = f"{float(fields.rating):.1f}"
            except ValueError:
                fields.rating = ""
        fields.four_k = "4K" if any(
            keyword in fields.definition.casefold() for keyword in ("4k", "2160")
        ) else ""

    def _parse_actors(self, root: ET.Element, fields: NFOFields):
        actors: List[str] = []
        for actor in root.findall("actor"):
            name_element = actor.find("name")
            if name_element is not None and name_element.text:
                original_name = name_element.text.strip()
                actors.append(self.actor_mapping.get(original_name, original_name))
        fields.actor = ",".join(actors)
        fields.smart_actor = self._generate_smart_actor(actors)

    @staticmethod
    def _generate_smart_actor(actors: List[str]) -> str:
        if len(actors) <= 3:
            return ",".join(actors)
        return f"{','.join(actors[:3])}等演员"


class NFOModifier:
    """Apply selected actor/series changes to an already parsed XML root."""

    SERIES_PREFIX = re.compile(r"^\s*系列\s*[:：]", re.IGNORECASE)

    def __init__(
        self,
        actor_mapping: Dict[str, str],
        series_mapping: Optional[Dict[str, str]] = None,
    ):
        self.actor_mapping = actor_mapping or {}
        self.series_mapping = {
            normalize_catalog_number(code): value
            for code, value in (series_mapping or {}).items()
            if normalize_catalog_number(code) and value
        }
        self.position_references = {
            "series": ["studio", "maker", "publisher", "label", "tag", "genre"],
            "set": ["studio", "maker", "publisher", "label", "tag", "genre"],
            "tag": ["genre", "poster", "cover"],
            "genre": ["poster", "cover", "trailer"],
        }

    def modify_nfo_file(
        self,
        nfo_path: str,
        *,
        modify_actors: bool = True,
        modify_series: bool = True,
        normalize_structure: bool = False,
    ) -> Tuple[bool, List[str], Dict[str, int], Dict[str, Any]]:
        tree = parse_xml_file(nfo_path)
        modified, actors, stats, logs = self.modify_root(
            tree.getroot(),
            modify_actors=modify_actors,
            modify_series=modify_series,
            normalize_structure=normalize_structure,
        )
        if modified:
            write_xml_root_atomic(tree.getroot(), nfo_path)
        return modified, actors, stats, logs

    def modify_root(
        self,
        root: ET.Element,
        *,
        modify_actors: bool,
        modify_series: bool,
        normalize_structure: bool = False,
    ) -> Tuple[bool, List[str], Dict[str, int], Dict[str, Any]]:
        stats = {"actor": 0, "tag": 0, "genre": 0, "series": 0, "set": 0}
        logs: Dict[str, Any] = {}
        modified = False
        actors: List[str] = []

        if modify_actors and self.actor_mapping:
            for element_type, tag_name in (
                ("actor", "actor"), ("tag", "tag"), ("genre", "genre")
            ):
                changed, found_actors, count, changes = self._modify_elements_with_log(
                    root, tag_name, element_type == "actor"
                )
                actors.extend(found_actors)
                if changed:
                    modified = True
                    stats[element_type] += count
                    logs[f"{element_type}_changes"] = changes

        if modify_series and self.series_mapping:
            changed, series_stats, series_logs = self._modify_series_with_log(root)
            if changed:
                modified = True
                for key, count in series_stats.items():
                    stats[key] = stats.get(key, 0) + count
                logs.update(series_logs)

        if normalize_structure:
            structure_changed, structure_logs = self._normalize_nfo_structure_with_log(root)
            if structure_changed:
                modified = True
                logs["structure_changes"] = structure_logs

        return modified, actors, stats, logs

    def _modify_elements_with_log(
        self, root: ET.Element, tag_name: str, is_actor: bool
    ) -> Tuple[bool, List[str], int, List[str]]:
        modified = False
        actors: List[str] = []
        count = 0
        changes: List[str] = []
        for element in root.findall(tag_name):
            if is_actor:
                name_element = element.find("name")
                if name_element is None or not name_element.text:
                    continue
                original = name_element.text.strip()
                mapped = self.actor_mapping.get(original, original)
                actors.append(mapped)
                if mapped != original:
                    name_element.text = mapped
                    modified = True
                    count += 1
                    changes.append(f"{original} → {mapped}")
            elif element.text:
                original = element.text.strip()
                mapped = self.actor_mapping.get(original, original)
                if mapped != original:
                    element.text = mapped
                    modified = True
                    count += 1
                    changes.append(f"{original} → {mapped}")
        return modified, actors, count, changes

    def _modify_series_with_log(
        self, root: ET.Element
    ) -> Tuple[bool, Dict[str, int], Dict[str, str]]:
        stats = {"series": 0, "set": 0, "tag": 0, "genre": 0}
        logs: Dict[str, str] = {}
        number = XMLUtils.find_first_valid_text(root, ["num", "id", "number"])
        expected = self.series_mapping.get(normalize_catalog_number(number))
        if not expected:
            return False, stats, logs

        operations = (
            ("series", self._update_series_field_with_log),
            ("set", self._update_set_field_with_log),
            ("tag", lambda r, value: self._update_series_text_nodes(r, "tag", value)),
            ("genre", lambda r, value: self._update_series_text_nodes(r, "genre", value)),
        )
        modified = False
        for key, func in operations:
            change = func(root, expected)
            if change:
                modified = True
                stats[key] += 1
                logs[f"{key}_change"] = change
        return modified, stats, logs

    def _update_series_field_with_log(
        self, root: ET.Element, expected: str
    ) -> Optional[str]:
        element = root.find("series")
        if element is None:
            element = ET.Element("series")
            element.text = expected
            positioned = XMLUtils.insert_element_before_reference(
                root, element, self.position_references["series"]
            )
            return f"空 → {expected} ({'参考节点前' if positioned else '末尾'})"
        old = (element.text or "").strip()
        if old != expected:
            element.text = expected
            return f"{old or '空'} → {expected}"
        return None

    def _update_set_field_with_log(
        self, root: ET.Element, expected: str
    ) -> Optional[str]:
        element = root.find("set")
        if element is None:
            element = ET.Element("set")
            ET.SubElement(element, "name").text = expected
            positioned = XMLUtils.insert_element_before_reference(
                root, element, self.position_references["set"]
            )
            return f"空 → {expected} ({'参考节点前' if positioned else '末尾'})"

        name = element.find("name")
        legacy = (element.text or "").strip()
        if name is None:
            element.text = None
            name = ET.SubElement(element, "name")
            name.text = expected
            return f"{legacy or '空'} → {expected} (结构修复)"
        old = (name.text or "").strip()
        if old != expected or legacy:
            element.text = None
            name.text = expected
            return f"{old or legacy or '空'} → {expected}"
        return None

    def _update_series_text_nodes(
        self, root: ET.Element, tag_name: str, expected: str
    ) -> Optional[str]:
        target = f"系列: {expected}"
        matches = [
            elem for elem in root.findall(tag_name)
            if elem.text and self.SERIES_PREFIX.match(elem.text)
        ]
        if not matches:
            element = ET.Element(tag_name)
            element.text = target
            positioned = XMLUtils.insert_element_before_reference(
                root, element, self.position_references[tag_name]
            )
            return f"空 → {target} ({'参考节点前' if positioned else '末尾'})"

        old_values = [(elem.text or "").strip() for elem in matches]
        matches[0].text = target
        for duplicate in matches[1:]:
            root.remove(duplicate)
        if old_values != [target]:
            suffix = f"，移除重复 {len(matches) - 1} 个" if len(matches) > 1 else ""
            return f"{' | '.join(old_values)} → {target}{suffix}"
        return None

    def _normalize_nfo_structure_with_log(
        self, root: ET.Element
    ) -> Tuple[bool, List[str]]:
        logs: List[str] = []
        logs.extend(self._fix_actor_elements_with_log(root))
        logs.extend(self._fix_set_elements_with_log(root))
        reorder = self._simple_reorder_with_log(root)
        if reorder:
            logs.append(reorder)
        return bool(logs), logs

    @staticmethod
    def _fix_actor_elements_with_log(root: ET.Element) -> List[str]:
        fixed = 0
        for actor in root.findall("actor"):
            name = actor.find("name")
            if name is None and actor.text and actor.text.strip():
                name = ET.SubElement(actor, "name")
                name.text = actor.text.strip()
                actor.text = None
                fixed += 1
        return [f"修复actor元素结构: {fixed}个"] if fixed else []

    @staticmethod
    def _fix_set_elements_with_log(root: ET.Element) -> List[str]:
        fixed = 0
        for element in root.findall("set"):
            if element.find("name") is None and element.text and element.text.strip():
                text = element.text.strip()
                element.text = None
                ET.SubElement(element, "name").text = text
                fixed += 1
        return [f"修复set元素结构: {fixed}个"] if fixed else []

    @staticmethod
    def _simple_reorder_with_log(root: ET.Element) -> Optional[str]:
        moved = 0
        series = root.find("series")
        studio = root.find("studio")
        if series is not None and studio is not None:
            children = list(root)
            series_pos = children.index(series)
            studio_pos = children.index(studio)
            if series_pos > studio_pos:
                root.remove(series)
                root.insert(studio_pos, series)
                moved += 1

        set_elem = root.find("set")
        series = root.find("series")
        if set_elem is not None and series is not None:
            children = list(root)
            series_pos = children.index(series)
            set_pos = children.index(set_elem)
            if set_pos != series_pos + 1:
                root.remove(set_elem)
                children = list(root)
                root.insert(children.index(series) + 1, set_elem)
                moved += 1
        return f"关键元素重排序: {moved}个" if moved else None


class FolderRenamer:
    """文件夹重命名器"""
    
    def __init__(self, format_string: str = ""):
        self.format_string = format_string.strip() or Config.DEFAULT_FOLDER_FORMAT
    
    def generate_folder_name(self, fields: NFOFields) -> str:
        """根据字段和格式生成文件夹名称"""
        if not self.format_string:
            return fields.filename
        
        result = self.format_string
        
        # 获取所有字段
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
    """Batch worker: one XML parse and at most one XML write per NFO."""

    progressUpdated = Signal(int, int)
    logUpdated = Signal(str)
    completed = Signal(dict)
    error = Signal(str)
    nfoChanged = Signal(str)
    folderRenamed = Signal(str, str)

    def __init__(
        self,
        directory: str,
        actor_mapping: Dict[str, str],
        rename_folders: bool,
        folder_format: str = "",
        series_mapping: Optional[Dict[str, str]] = None,
        *,
        modify_actors: bool = True,
        modify_series: bool = True,
    ):
        super().__init__()
        self.directory = os.path.normpath(directory)
        self.actor_mapping = actor_mapping or {}
        self.series_mapping = series_mapping or {}
        self.rename_folders = rename_folders
        self.modify_actors = bool(modify_actors and self.actor_mapping)
        self.modify_series = bool(modify_series and self.series_mapping)
        self.nfo_parser = NFOParser(self.actor_mapping if self.modify_actors else {})
        self.nfo_modifier = NFOModifier(self.actor_mapping, self.series_mapping)
        self.folder_renamer = FolderRenamer(folder_format)
        self.log_manager = LogManager()
        self.result = {
            "total": 0,
            "processed": 0,
            "nfo_modified": 0,
            "renamed": 0,
            "failed": 0,
            "skipped": 0,
            "canceled": False,
        }

    def run(self):
        try:
            self.log_manager.log_info(f"开始处理目录: {self.directory}")
            self.log_manager.log_info(f"修改演员: {'是' if self.modify_actors else '否'}")
            self.log_manager.log_info(f"修改系列: {'是' if self.modify_series else '否'}")
            self.log_manager.log_info(f"重命名文件夹: {'是' if self.rename_folders else '否'}")
            self._process_directory()
            self.result["canceled"] = self.isInterruptionRequested()
            if self.result["canceled"]:
                self.log_manager.log_info("任务已取消")
            else:
                self.log_manager.log_info("批量任务处理完成")
            self.completed.emit(dict(self.result))
        except Exception as exc:
            self.log_manager.log_error(f"处理过程出错: {exc}")
            self.error.emit(str(exc))
        finally:
            self.log_manager.close()

    def _process_directory(self):
        folders = self._collect_folders_with_nfo()
        total = len(folders)
        self.result["total"] = total
        self.log_manager.log_info(f"找到 {total} 个包含NFO文件的文件夹")
        for current, (folder_path, nfo_path) in enumerate(folders, 1):
            if self.isInterruptionRequested():
                break
            try:
                outcome = self._process_single_folder(folder_path, nfo_path)
                self.result["processed"] += 1
                self.result["nfo_modified"] += int(outcome["nfo_modified"])
                self.result["renamed"] += int(outcome["renamed"])
                self.result["skipped"] += int(outcome["skipped"])
            except Exception as exc:
                self.result["failed"] += 1
                message = f"处理文件夹 {folder_path} 时出错: {exc}"
                self.log_manager.log_error(message)
                self.logUpdated.emit(message)
            finally:
                self.progressUpdated.emit(current, total)

    def _collect_folders_with_nfo(self) -> List[Tuple[str, str]]:
        folders: List[Tuple[str, str]] = []
        for root, dirs, files in os.walk(self.directory):
            if self.isInterruptionRequested():
                break
            dirs.sort(key=str.casefold)
            nfo_path = preferred_nfo_from_names(root, files, Path(root).name)
            if nfo_path:
                folders.append((root, nfo_path))
        folders.sort(
            key=lambda item: (len(Path(item[0]).parts), item[0].casefold()),
            reverse=True,
        )
        return folders

    def _process_single_folder(self, folder_path: str, nfo_path: str) -> Dict[str, bool]:
        folder_name = Path(folder_path).name
        nfo_name = Path(nfo_path).name
        self.log_manager.log_info(f"开始处理: {folder_path}")

        tree = parse_xml_file(nfo_path)
        root = tree.getroot()
        fields = self.nfo_parser.parse_root(root, nfo_path)

        nfo_modified = False
        modified_fields: List[str] = []
        if self.modify_actors or self.modify_series:
            nfo_modified, _actors, stats, logs = self.nfo_modifier.modify_root(
                root,
                modify_actors=self.modify_actors,
                modify_series=self.modify_series,
                normalize_structure=False,
            )
            if nfo_modified:
                write_xml_root_atomic(root, nfo_path)
                fields = self.nfo_parser.parse_root(root, nfo_path)
                modified_fields = self._log_modifications(nfo_name, stats, logs)

        # NFO 已经成功落盘时立即上报。即使后续文件夹重命名失败，
        # 主编辑器也不会漏掉这次内容变化；若重命名成功，路径重定向会接管旧路径。
        if nfo_modified:
            self.nfoChanged.emit(nfo_path)

        renamed_path: Optional[str] = None
        if self.rename_folders:
            if same_path(folder_path, self.directory):
                self.log_manager.log_info(f"跳过所选根目录重命名: {folder_path}")
            else:
                renamed_path = self._rename_folder_if_needed(
                    folder_path, fields, folder_name
                )

        if renamed_path:
            self.folderRenamed.emit(folder_path, renamed_path)

        ui_messages = list(modified_fields)
        if renamed_path:
            ui_messages.append("文件夹已重命名")
        if ui_messages:
            self.logUpdated.emit(f"{nfo_name} - {', '.join(ui_messages)}")

        return {
            "nfo_modified": nfo_modified,
            "renamed": bool(renamed_path),
            "skipped": not nfo_modified and not renamed_path,
        }

    def _log_modifications(
        self, nfo_name: str, stats: Dict[str, int], logs: Dict[str, Any]
    ) -> List[str]:
        fields: List[str] = []
        labels = {"actor": "演员", "tag": "标签", "genre": "类型", "series": "系列"}
        for key, label in labels.items():
            if stats.get(key, 0):
                fields.append(label)
        for key in ("actor_changes", "tag_changes", "genre_changes"):
            for change in logs.get(key, []) or []:
                self.log_manager.log_success(f"{change}")
        for key in ("series_change", "set_change", "tag_change", "genre_change"):
            if logs.get(key):
                self.log_manager.log_success(f"{key}: {logs[key]}")
        self.log_manager.log_success(f"NFO文件修改完成: {nfo_name}")
        return [f"{label}已修改" for label in fields]

    def _rename_folder_if_needed(
        self, folder_path: str, fields: NFOFields, folder_name: str
    ) -> Optional[str]:
        expected = self.folder_renamer.generate_folder_name(fields)
        if folder_name == expected:
            self.log_manager.log_info(f"文件夹名称符合规范: {folder_name}")
            return None
        new_path = os.path.normpath(os.path.join(str(Path(folder_path).parent), expected))
        self.folder_renamer.rename_folder(folder_path, expected)
        self.log_manager.log_success(f"文件夹重命名: {folder_name} → {expected}")
        return new_path


class RenameToolGUI(QMainWindow):
    operationStarted = Signal(str)
    nfoChanged = Signal(str)
    folderRenamed = Signal(str, str)
    operationFinished = Signal(bool)

    def __init__(self, parent=None):
        # Qt6/PySide6 版本：parent 可以安全接收 NFOEditorQt6。
        super().__init__(parent)
        self.parent_window = parent
        # 使用新的加载器
        self.actor_loader = ActorMappingLoader()
        self.series_loader = SeriesMappingLoader()
        
        self.actor_mapping = {}
        self.series_mapping = {}
        self.worker = None
        self._operation_active = False
        
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
        screen = QApplication.primaryScreen()
        if not screen:
            return
        screen_geometry = screen.availableGeometry()
        x = screen_geometry.x() + (screen_geometry.width() - self.width()) // 2
        y = screen_geometry.y() + (screen_geometry.height() - self.height()) // 2
        self.move(x, y)
    
    def _setup_icon(self):
        """设置窗口图标"""
        try:
            exe_dir, base_path = PathUtils.get_application_paths()
            
            icon_path = base_path / "chuizi.ico"
            if icon_path.exists():
                icon = QIcon(str(icon_path))
                for size in [16, 32, 64, 128]:
                    icon.addFile(str(icon_path), QSize(size, size))
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
        self.modify_actors_cb.setChecked(True)
        first_row.addWidget(self.modify_actors_cb)
        first_row.addStretch()
        layout.addLayout(first_row)
        
        # 第二行：系列映射选项
        second_row = QHBoxLayout()
        self.modify_series_cb = QCheckBox("修改系列信息（应用系列映射）")
        self.modify_series_cb.setChecked(True)
        second_row.addWidget(self.modify_series_cb)
        second_row.addStretch()
        layout.addLayout(second_row)
        
        # 第三行：重命名文件夹选项
        third_row = QHBoxLayout()
        self.rename_folders_cb = QCheckBox("同时重命名文件夹")
        self.rename_folders_cb.setChecked(True)
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
        self.execute_btn = QPushButton("执行")
        self.execute_btn.setMinimumHeight(45)
        self.execute_btn.setStyleSheet(Config.PRIMARY_BUTTON_STYLE)
        self.execute_btn.clicked.connect(self.execute_rename)
        layout.addWidget(self.execute_btn)
        
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
    
    def _load_mapping_with_ui_update(self, loader: BaseMappingLoader, 
                                   mapping_attr: str, label_attr: str, 
                                   item_name: str) -> bool:
        """通用映射加载方法"""
        try:
            mapping_file_path = loader.find_mapping_file()
            if mapping_file_path:
                mapping = loader.load_mapping(mapping_file_path)
                setattr(self, mapping_attr, mapping)
                
                success_msg = f"成功加载 {len(mapping)} 个{item_name}映射关系"
                self.log_text.append(success_msg)
                
                # 更新路径显示
                file_type = loader.get_file_type()
                label_text = f"{loader.display_name}映射 ({file_type}): {mapping_file_path}"
                getattr(self, label_attr).setText(label_text)
                
                return True
            else:
                not_found_msg = f"未找到{item_name}映射配置文件"
                getattr(self, label_attr).setText(not_found_msg)
                self.log_text.append(f"警告：{not_found_msg}")
                return False
                
        except Exception as e:
            error_msg = f"{loader.display_name}映射: 配置文件加载失败"
            getattr(self, label_attr).setText(error_msg)
            self.log_text.append(f"加载{item_name}映射文件出错: {e}")
            return False
    
    def load_actor_mapping(self):
        """加载演员映射"""
        self._load_mapping_with_ui_update(
            self.actor_loader, "actor_mapping", "mapping_label", "演员"
        )
    
    def load_series_mapping(self):
        """加载系列映射"""
        self._load_mapping_with_ui_update(
            self.series_loader, "series_mapping", "series_mapping_label", "系列"
        )
   
    def browse_folder(self):
        """浏览文件夹"""
        folder = QFileDialog.getExistingDirectory(self, "选择工作目录")
        if folder:
            self.path_entry.setText(folder)
    

    def execute_rename(self):
        """执行重命名操作，并通过信号向编辑器报告精确变化。"""
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.execute_btn.setText("正在停止...")
            self.execute_btn.setEnabled(False)
            self.log_text.append("已请求停止，将在当前文件处理完成后结束。")
            return

        directory = self.path_entry.text().strip()
        if not directory or not os.path.isdir(directory):
            QMessageBox.critical(self, "错误", f"路径 '{directory}' 不是一个有效的目录")
            return

        has_actor_mapping = (
            self.modify_actors_cb.isChecked() and bool(self.actor_mapping)
        )
        has_series_mapping = (
            self.modify_series_cb.isChecked() and bool(self.series_mapping)
        )

        if (
            not has_actor_mapping
            and not has_series_mapping
            and not self.rename_folders_cb.isChecked()
        ):
            QMessageBox.critical(
                self,
                "错误",
                "请至少选择一项操作：修改演员信息、修改系列信息或重命名文件夹",
            )
            return

        try:
            self.log_text.clear()
            self.progress_bar.setValue(0)
            self.log_text.append("开始处理，只显示有修改的文件...")
            self.log_text.append("")

            folder_format = (
                self.folder_format_entry.text().strip()
                or Config.DEFAULT_FOLDER_FORMAT
            )
            actor_mapping = self.actor_mapping if has_actor_mapping else {}
            series_mapping = self.series_mapping if has_series_mapping else {}

            self.worker = RenameWorker(
                directory,
                actor_mapping,
                self.rename_folders_cb.isChecked(),
                folder_format,
                series_mapping,
                modify_actors=has_actor_mapping,
                modify_series=has_series_mapping,
            )

            self.worker.progressUpdated.connect(self.update_progress)
            self.worker.logUpdated.connect(self.update_ui_log)
            self.worker.nfoChanged.connect(self.nfoChanged.emit)
            self.worker.folderRenamed.connect(self.folderRenamed.emit)
            self.worker.completed.connect(self.on_worker_finished)
            self.worker.error.connect(self.handle_error)
            self.worker.finished.connect(self._on_worker_thread_stopped)

            self.execute_btn.setText("停止")
            self.execute_btn.setEnabled(True)
            self._operation_active = True
            self.operationStarted.emit(os.path.normpath(directory))
            self.worker.start()

        except Exception as e:
            self.execute_btn.setEnabled(True)
            if self._operation_active:
                self._operation_active = False
                self.operationFinished.emit(False)
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


    def on_worker_finished(self, stats):
        """Show truthful success/failure/cancel statistics."""
        self.log_text.append("")
        summary = (
            f"处理 {stats.get('processed', 0)}/{stats.get('total', 0)}，"
            f"修改NFO {stats.get('nfo_modified', 0)}，"
            f"重命名 {stats.get('renamed', 0)}，"
            f"失败 {stats.get('failed', 0)}，跳过 {stats.get('skipped', 0)}"
        )
        if stats.get("canceled"):
            self.log_text.append(f"任务已取消：{summary}")
            self.progress_bar.setFormat("已取消")
        elif stats.get("failed"):
            self.log_text.append(f"任务完成但存在失败：{summary}")
            self.progress_bar.setFormat("部分完成")
        else:
            self.log_text.append(f"🎉 处理完成：{summary}")
            self.progress_bar.setFormat("完成")

        success = not stats.get("canceled") and not stats.get("failed")
        if self._operation_active:
            self._operation_active = False
            self.operationFinished.emit(success)

        if getattr(self.worker, "log_manager", None) and self.worker.log_manager.log_file_path:
            self.log_text.append(f"详细日志: {self.worker.log_manager.log_file_path}")


    def _notify_parent_finished(self):
        """保留旧接口；新版通过 operationFinished/变更信号通知主窗口。"""
        if self._operation_active:
            self._operation_active = False
            self.operationFinished.emit(True)


    def handle_error(self, error_message: str):
        """处理工作线程错误。"""
        if self._operation_active:
            self._operation_active = False
            self.operationFinished.emit(False)
        QMessageBox.critical(
            self, "错误", f"处理过程中出现错误: {error_message}"
        )
        self.log_text.append(f"处理出错: {error_message}")


    def _on_worker_thread_stopped(self):
        worker = self.worker
        self.worker = None
        self.execute_btn.setText("执行")
        self.execute_btn.setEnabled(True)

        # 被用户中断或线程异常退出但未走 completed/error 时，也必须解除主程序操作锁。
        if self._operation_active:
            self._operation_active = False
            self.operationFinished.emit(False)

        if worker is not None:
            worker.deleteLater()

    def update_ui_log(self, message: str):
        """更新UI日志"""
        if message:
            self.log_text.append(message)
            
            # 确保最新日志可见
            scroll_bar = self.log_text.verticalScrollBar()
            scroll_bar.setValue(scroll_bar.maximum())

    def closeEvent(self, event):
        """防止运行中的重命名线程随窗口一起被销毁。"""
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            if not self.worker.wait(5000):
                QMessageBox.warning(
                    self, "任务仍在运行",
                    "批量处理尚未安全停止，请等待当前文件处理完成后再关闭。",
                )
                event.ignore()
                return
        super().closeEvent(event)


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
    sys.exit(app.exec())

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