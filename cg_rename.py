import os
import sys
import re
from xml.etree import ElementTree as ET
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QTextEdit,
    QProgressBar,
    QFileDialog,
    QMessageBox,
)
from PyQt5.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass, field

# 配置常量
class Config:
    DEFAULT_FOLDER_FORMAT = "number smart_actor"
    SUPPORTED_NFO_EXTENSIONS = ['.nfo']
    INVALID_FILENAME_CHARS = r'[\\/:*?"<>|]'
    APP_VERSION = "v9.6.5"
    WINDOW_MIN_SIZE = (900, 850)


@dataclass
class NFOFields:
    """NFO文件字段数据类"""
    title: str = ""
    originaltitle: str = ""
    number: str = ""
    letters: str = ""
    first_letter: str = ""
    filename: str = ""
    
    # 演员相关（原始数据，未应用映射）
    original_actors: List[str] = field(default_factory=list)
    # 演员相关（已应用映射）
    actors: List[str] = field(default_factory=list)
    actor: str = ""
    all_actor: str = ""
    first_actor: str = ""
    smart_actor: str = ""
    actor_count: str = ""
    
    # 制作信息
    director: str = ""
    series: str = ""
    studio: str = ""
    publisher: str = ""
    release: str = ""
    year: str = ""
    
    # 技术信息
    runtime: str = ""
    rating: str = ""
    score: str = ""
    mosaic: str = ""
    definition: str = ""
    four_k: str = ""
    
    # 其他
    wanted: str = ""
    outline: str = ""


class NFOParser:
    """NFO文件解析器"""
    
    # XML字段映射配置
    FIELD_MAPPINGS = {
        'title': ['.//title'],
        'originaltitle': ['.//originaltitle'],
        'number': ['.//num', './/id', './/number'],
        'director': ['.//director'],
        'series': ['.//series', './/set'],
        'studio': ['.//studio'],
        'publisher': ['.//publisher'],
        'release': ['.//releasedate', './/release', './/premiered'],
        'year': ['.//year'],
        'runtime': ['.//runtime'],
        'rating': ['.//rating'],
        'mosaic': ['.//mosaic'],
        'definition': ['.//definition', './/resolution'],
        'wanted': ['.//wanted'],
        'outline': ['.//plot', './/outline'],
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
            self._parse_basic_fields(root, fields)
            
            # 解析演员信息
            self._parse_actor_fields(root, fields)
            
            # 解析派生字段
            self._parse_derived_fields(fields)
            
            return fields
            
        except Exception as e:
            raise Exception(f"解析NFO文件失败 {nfo_path}: {e}")
    
    def _parse_basic_fields(self, root: ET.Element, fields: NFOFields):
        """解析基本字段"""
        for field_name, xpath_list in self.FIELD_MAPPINGS.items():
            value = self._find_first_valid_text(root, xpath_list)
            setattr(fields, field_name, value)
        
        # 特殊处理评分字段
        if fields.rating:
            try:
                rating_value = float(fields.rating)
                fields.rating = f"{rating_value:.1f}"
                fields.score = fields.rating
            except ValueError:
                fields.rating = ""
                fields.score = ""
        
        # 处理番号相关字段
        if fields.number:
            match = re.match(r'^([A-Za-z]+)', fields.number)
            fields.letters = match.group(1) if match else ""
            fields.first_letter = fields.letters[:1] if fields.letters else ""
        
        # 处理4K标识
        fields.four_k = "4K" if any(keyword in fields.definition.lower() 
                                  for keyword in ['4k', '2160']) else ""
    
    def _parse_actor_fields(self, root: ET.Element, fields: NFOFields):
        """解析演员信息"""
        actor_elements = root.findall(".//actor")
        
        for actor in actor_elements:
            name_element = actor.find("name")
            if name_element is not None and name_element.text:
                original_name = name_element.text.strip()
                fields.original_actors.append(original_name)
                
                # 应用映射关系
                mapped_name = self.actor_mapping.get(original_name, original_name)
                fields.actors.append(mapped_name)
        
        # 设置演员相关字段
        self._set_actor_related_fields(fields)
    
    def _set_actor_related_fields(self, fields: NFOFields):
        """设置演员相关的派生字段"""
        if not fields.actors:
            return
        
        fields.actor = ",".join(fields.actors)
        fields.all_actor = fields.actor
        fields.first_actor = fields.actors[0]
        fields.actor_count = str(len(fields.actors))
        
        # 智能演员显示逻辑
        actor_count = len(fields.actors)
        if actor_count == 1:
            fields.smart_actor = fields.actors[0]
        elif actor_count == 2:
            fields.smart_actor = f"{fields.actors[0]},{fields.actors[1]}"
        elif actor_count >= 3:
            fields.smart_actor = f"{fields.actors[0]}等演员"
    
    def _parse_derived_fields(self, fields: NFOFields):
        """解析派生字段（基于其他字段计算得出的字段）"""
        # 目前主要是演员相关字段，已在 _parse_actor_fields 中处理
        pass
    
    def _find_first_valid_text(self, root: ET.Element, xpath_list: List[str]) -> str:
        """从xpath列表中查找第一个有效的文本值"""
        for xpath in xpath_list:
            element = root.find(xpath)
            if element is not None and element.text:
                return element.text.strip()
        return ""


class NFOModifier:
    """NFO文件修改器"""
    
    def __init__(self, actor_mapping: Dict[str, str]):
        self.actor_mapping = actor_mapping
    
    def modify_actor_names(self, nfo_path: str) -> Tuple[bool, List[str], Dict[str, int]]:
        """修改NFO文件中的演员名称"""
        try:
            tree = ET.parse(nfo_path)
            root = tree.getroot()
            
            modified = False
            all_actors = []
            modification_stats = {
                'actor': 0,
                'tag': 0, 
                'genre': 0
            }
            
            # 处理 <actor><name> 标签
            actor_modified, actors = self._modify_actor_elements(root)
            if actor_modified:
                modified = True
                modification_stats['actor'] = len([a for a in actors if a])
            all_actors.extend(actors)
            
            # 处理 <tag> 标签
            tag_modified, tag_count = self._modify_text_elements(root, './/tag')
            if tag_modified:
                modified = True
                modification_stats['tag'] = tag_count
            
            # 处理 <genre> 标签
            genre_modified, genre_count = self._modify_text_elements(root, './/genre')
            if genre_modified:
                modified = True
                modification_stats['genre'] = genre_count
            
            if modified:
                tree.write(nfo_path, encoding="utf-8", xml_declaration=True)
            
            return modified, all_actors, modification_stats
            
        except Exception as e:
            raise Exception(f"修改NFO文件失败 {nfo_path}: {e}")
    
    def _modify_actor_elements(self, root: ET.Element) -> Tuple[bool, List[str]]:
        """修改 <actor><name> 元素"""
        modified = False
        actors = []
        
        actor_elements = root.findall(".//actor")
        for actor in actor_elements:
            name_element = actor.find("name")
            if name_element is not None and name_element.text:
                original_name = name_element.text.strip()
                mapped_name = self.actor_mapping.get(original_name, original_name)
                
                if mapped_name != original_name:
                    name_element.text = mapped_name
                    modified = True
                
                actors.append(mapped_name)
        
        return modified, actors
    
    def _modify_text_elements(self, root: ET.Element, xpath: str) -> Tuple[bool, int]:
        """修改文本元素（如tag、genre）"""
        modified = False
        modification_count = 0
        
        elements = root.findall(xpath)
        for element in elements:
            if element.text:
                original_text = element.text.strip()
                
                # 检查是否是演员名（在映射表中存在）
                if original_text in self.actor_mapping:
                    mapped_name = self.actor_mapping[original_text]
                    if mapped_name != original_text:
                        element.text = mapped_name
                        modified = True
                        modification_count += 1
        
        return modified, modification_count


class FolderRenamer:
    """文件夹重命名器"""
    
    def __init__(self, format_string: str = ""):
        self.format_string = format_string.strip() or Config.DEFAULT_FOLDER_FORMAT
    
    def generate_folder_name(self, fields: NFOFields) -> str:
        """根据字段和格式生成文件夹名称"""
        if not self.format_string:
            return fields.filename
        
        result = self.format_string
        field_dict = self._fields_to_dict(fields)
        
        # 按字段名长度降序排列，避免短字段名影响长字段名
        sorted_fields = sorted(field_dict.items(), key=lambda x: len(x[0]), reverse=True)
        
        for field_name, field_value in sorted_fields:
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
    
    def _fields_to_dict(self, fields: NFOFields) -> Dict[str, str]:
        """将字段对象转换为字典"""
        field_dict = {}
        for field_name in dir(fields):
            if not field_name.startswith('_') and field_name not in ['original_actors', 'actors']:
                value = getattr(fields, field_name)
                if not callable(value):
                    field_dict[field_name] = value
        
        # 处理特殊字段名
        field_dict['4k'] = fields.four_k
        
        return field_dict
    
    def _clean_filename(self, filename: str) -> str:
        """清理文件名中的非法字符"""
        return re.sub(Config.INVALID_FILENAME_CHARS, "_", filename)


class RenameWorker(QThread):
    """重命名工作线程"""
    
    progressUpdated = pyqtSignal(int, int)
    logUpdated = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)
    
    def __init__(self, directory: str, actor_mapping: Dict[str, str], 
                 rename_folders: bool, folder_format: str = ""):
        super().__init__()
        self.directory = directory
        self.actor_mapping = actor_mapping
        self.rename_folders = rename_folders
        
        # 初始化组件
        self.nfo_parser = NFOParser(actor_mapping)
        self.nfo_modifier = NFOModifier(actor_mapping)
        self.folder_renamer = FolderRenamer(folder_format)
    
    def run(self):
        try:
            self._process_directory()
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))
    
    def _process_directory(self):
        """处理目录"""
        # 收集所有需要处理的文件夹
        folders_to_process = self._collect_folders_with_nfo()
        total_folders = len(folders_to_process)
        
        self.logUpdated.emit(f"找到 {total_folders} 个包含NFO文件的文件夹")
        
        for i, (folder_path, nfo_path) in enumerate(folders_to_process, 1):
            try:
                self._process_single_folder(folder_path, nfo_path, i, total_folders)
            except Exception as e:
                self.logUpdated.emit(f"处理文件夹 {folder_path} 时出错: {e}")
                continue
        
        self.logUpdated.emit("\n所有处理完成！")
    
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
    
    def _process_single_folder(self, folder_path: str, nfo_path: str, 
                             current: int, total: int):
        """处理单个文件夹"""
        folder_name = Path(folder_path).name
        self.logUpdated.emit(f"\n[{current}/{total}] 处理文件夹: {folder_name}")
        
        # 1. 解析NFO文件
        try:
            nfo_fields = self.nfo_parser.parse_nfo_file(nfo_path)
        except Exception as e:
            self.logUpdated.emit(f"解析NFO文件失败: {e}")
            return
        
        # 2. 修改演员信息
        try:
            modified, new_actors, stats = self.nfo_modifier.modify_actor_names(nfo_path)
            if modified:
                # 详细显示修改统计
                details = []
                if stats['actor'] > 0:
                    details.append(f"演员标签: {stats['actor']}个")
                if stats['tag'] > 0:
                    details.append(f"标签: {stats['tag']}个")
                if stats['genre'] > 0:
                    details.append(f"类型: {stats['genre']}个")
                
                if details:
                    self.logUpdated.emit(f"演员信息已更新 - {', '.join(details)}")
                    if new_actors:
                        self.logUpdated.emit(f"更新后的演员: {', '.join(new_actors)}")
                else:
                    self.logUpdated.emit("演员信息已更新")
            else:
                self.logUpdated.emit("演员信息无需修改")
        except Exception as e:
            self.logUpdated.emit(f"修改演员信息失败: {e}")
        
        # 3. 重命名文件夹（如果启用）
        if self.rename_folders:
            try:
                expected_name = self.folder_renamer.generate_folder_name(nfo_fields)
                current_name = Path(folder_path).name
                
                if current_name != expected_name:
                    self.folder_renamer.rename_folder(folder_path, expected_name)
                    self.logUpdated.emit(f"文件夹已重命名: {current_name} → {expected_name}")
                else:
                    self.logUpdated.emit("文件夹名称符合规范，无需重命名")
            except Exception as e:
                self.logUpdated.emit(f"重命名文件夹失败: {e}")
        
        # 4. 更新进度
        self.progressUpdated.emit(current, total)
    
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
        # 优先查找外部配置文件
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).parent
        else:
            exe_dir = Path(__file__).parent
        
        external_mapping = exe_dir / "mapping_actor.xml"
        if external_mapping.exists():
            return str(external_mapping)
        
        # 查找内置配置文件
        if getattr(sys, "frozen", False):
            base_path = Path(sys._MEIPASS)
        else:
            base_path = Path(__file__).parent
        
        internal_mapping = base_path / "mapping_actor.xml"
        if internal_mapping.exists():
            return str(internal_mapping)
        
        return None
    
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


class RenameToolGUI(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.actor_mapping = {}
        self.mapping_file_path = None
        self.worker = None
        self.init_ui()
        self.load_actor_mapping()

    def init_ui(self):
        """初始化用户界面"""
        self.setWindowTitle(f"大锤 批量改名工具 {Config.APP_VERSION}")
        self.setMinimumSize(*Config.WINDOW_MIN_SIZE)
        
        # 设置样式
        self._setup_styles()
        
        # 居中窗口
        self._center_window()
        
        # 设置图标
        self._setup_icon()
        
        # 创建界面
        self._create_widgets()
    
    def _setup_styles(self):
        """设置界面样式"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f5f5f5;
            }
            QWidget {
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
            }
            QLineEdit {
                padding: 8px;
                border: 1px solid #dcdcdc;
                border-radius: 6px;
                background-color: white;
            }
            QLineEdit:focus {
                border: 1px solid #2196F3;
            }
            QPushButton {
                padding: 8px 16px;
                border-radius: 6px;
                font-weight: bold;
                background-color: #f0f0f0;
                border: none;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
            QPushButton:pressed {
                background-color: #d0d0d0;
            }
            QTextEdit {
                border: 1px solid #dcdcdc;
                border-radius: 6px;
                background-color: white;
                padding: 8px;
            }
            QProgressBar {
                border: none;
                border-radius: 6px;
                background-color: #f0f0f0;
                text-align: center;
                min-height: 12px;
            }
            QProgressBar::chunk {
                border-radius: 6px;
                background-color: #2196F3;
            }
            QCheckBox {
                padding: 5px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 2px solid #dcdcdc;
            }
            QCheckBox::indicator:checked {
                background-color: #2196F3;
                border-color: #2196F3;
            }
        """)
    
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
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # 路径选择区域
        main_layout.addWidget(self._create_path_container())
        
        # 选项区域
        main_layout.addWidget(self._create_options_container())
        
        # 映射文件路径显示
        self.mapping_label = QLabel()
        self.mapping_label.setStyleSheet("""
            color: #2196F3;
            background-color: white;
            padding: 10px;
            border-radius: 8px;
        """)
        main_layout.addWidget(self.mapping_label)
        
        # 日志区域
        main_layout.addWidget(self._create_log_container(), stretch=1)
        
        # 底部区域
        main_layout.addWidget(self._create_bottom_container())
    
    def _create_path_container(self):
        """创建路径选择容器"""
        container = QWidget()
        container.setObjectName("pathContainer")
        container.setStyleSheet("""
            QWidget#pathContainer {
                background-color: white;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        
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
        container = QWidget()
        container.setObjectName("optionsContainer")
        container.setStyleSheet("""
            QWidget#optionsContainer {
                background-color: white;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        
        layout = QVBoxLayout(container)
        
        # 重命名文件夹选项
        first_row = QHBoxLayout()
        self.rename_folders_cb = QCheckBox("同时重命名文件夹")
        first_row.addWidget(self.rename_folders_cb)
        first_row.addStretch()
        layout.addLayout(first_row)
        
        # 文件夹命名格式
        format_row = QHBoxLayout()
        format_row.addWidget(QLabel("文件夹命名格式："))
        
        self.folder_format_entry = QLineEdit()
        self.folder_format_entry.setText(Config.DEFAULT_FOLDER_FORMAT)
        self.folder_format_entry.setPlaceholderText("例如: number smart_actor 或 smart_actor title rating")
        format_row.addWidget(self.folder_format_entry)
        
        help_btn = QPushButton("说明")
        help_btn.setMaximumWidth(80)
        help_btn.clicked.connect(self.show_field_help)
        format_row.addWidget(help_btn)
        
        layout.addLayout(format_row)
        
        return container
    
    def _create_log_container(self):
        """创建日志容器"""
        container = QWidget()
        container.setObjectName("logContainer")
        container.setStyleSheet("""
            QWidget#logContainer {
                background-color: white;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 10, 10, 10)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        
        return container
    
    def _create_bottom_container(self):
        """创建底部容器"""
        container = QWidget()
        container.setObjectName("bottomContainer")
        container.setStyleSheet("""
            QWidget#bottomContainer {
                background-color: white;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        
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
        execute_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #0D47A1;
            }
        """)
        execute_btn.clicked.connect(self.execute_rename)
        layout.addWidget(execute_btn)
        
        return container
        
    def show_field_help(self):
        """显示字段帮助"""
        help_text = """
        支持的字段名称：

        基础字段：
        • title - 标题
        • originaltitle - 原标题  
        • number - 番号
        • letters - 番号前缀
        • first_letter - 首位字母
        • filename - nfo文件名

        演员相关：
        • actor - 女演员（多个用逗号分隔）
        • all_actor - 所有演员
        • first_actor - 首位演员（已映射）
        • smart_actor - 智能演员显示（推荐使用）
        ├─ 1个演员：直接显示演员名
        ├─ 2个演员：演员1,演员2
        └─ 3个及以上：演员1等演员
        • actor_count - 演员数量

        制作信息：
        • director - 导演
        • series - 系列
        • studio - 片商
        • publisher - 发行商
        • release - 发行日期
        • year - 年份

        技术信息：
        • runtime - 时长
        • rating - 评分
        • score - 评分（同rating）
        • mosaic - 有码/无码
        • definition - 分辨率
        • 4k - 4K标识

        其他：
        • wanted - 想看人数
        • outline - 剧情简介

        使用示例：
        • "number smart_actor" → "PRED-001 桥本有菜"
        • "smart_actor title rating" → "桥本有菜等演员 完全主观 9.2"
        • "studio number smart_actor" → "Premium PRED-001 桥本有菜等演员"

        推荐格式：
        • 单演员：number smart_actor
        • 多演员：number smart_actor（会自动显示为"主演等演员"）

        注意：
        • smart_actor字段会根据演员数量智能调整显示方式
        • 所有演员名都会自动应用映射关系
        • 字段之间用空格分隔，程序会自动替换为对应的值
                """
                
        QMessageBox.information(self, "字段说明", help_text)
    
    def load_actor_mapping(self):
        """加载演员映射"""
        try:
            self.mapping_file_path = ActorMappingLoader.find_mapping_file()
            if self.mapping_file_path:
                self.actor_mapping = ActorMappingLoader.load_mapping(self.mapping_file_path)
                self.log_text.append(f"成功加载 {len(self.actor_mapping)} 个演员映射关系")
                
                # 更新映射文件路径显示
                if "mapping_actor.xml" in self.mapping_file_path and sys.executable not in self.mapping_file_path:
                    self.mapping_label.setText(f"外部配置: {self.mapping_file_path}")
                else:
                    self.mapping_label.setText(f"内置配置: {self.mapping_file_path}")
            else:
                self.mapping_label.setText("未找到配置文件")
                self.log_text.append("警告：未找到演员映射配置文件")
        except Exception as e:
            self.mapping_label.setText("配置文件加载失败")
            self.log_text.append(f"加载映射文件出错: {e}")
   
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
        
        if not self.mapping_file_path or not os.path.exists(self.mapping_file_path):
            QMessageBox.critical(self, "错误", "未找到有效的配置文件")
            return
        
        try:
            self.log_text.clear()
            self.progress_bar.setValue(0)
            self.log_text.append("开始处理...")
            
            folder_format = self.folder_format_entry.text().strip()
            if not folder_format:
                folder_format = Config.DEFAULT_FOLDER_FORMAT
            
            self.worker = RenameWorker(
                directory,
                self.actor_mapping,
                self.rename_folders_cb.isChecked(),
                folder_format
            )
            
            self.worker.progressUpdated.connect(self.update_progress)
            self.worker.logUpdated.connect(self.update_log)
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
    
    def update_log(self, message: str):
        """更新日志"""
        self.log_text.append(message)
        # 确保最新日志可见
        scroll_bar = self.log_text.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())
    
    def on_worker_finished(self):
        """工作线程完成"""
        self.log_text.append("\n处理完成！")
        self.progress_bar.setFormat("完成")
    
    def handle_error(self, error_message: str):
        """处理工作线程错误"""
        QMessageBox.critical(self, "错误", f"处理过程中出现错误: {error_message}")
        self.log_text.append(f"处理出错: {error_message}")


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
                        rename_folders: bool, folder_format: str = "") -> RenameWorker:
    """便利函数：创建RenameWorker实例，保持向后兼容性"""
    return RenameWorker(directory, actor_mapping, rename_folders, folder_format)


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