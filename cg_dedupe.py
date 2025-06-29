import os
import re
import sys
import json
import subprocess
from PyQt5 import QtWidgets, QtGui, QtCore
from PyQt5.QtCore import Qt
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET
from multiprocessing import cpu_count, freeze_support
import signal
from threading import Lock
from difflib import SequenceMatcher


# 应用常量
class AppConstants:
    """应用常量定义"""
    MAX_DIRECTORIES = 9
    DEFAULT_BATCH_SIZE = 1000
    PROGRESS_UPDATE_INTERVAL = 200  # 毫秒
    BUTTON_HEIGHT = 20
    SIMILARITY_THRESHOLD_MIN = 50
    SIMILARITY_THRESHOLD_MAX = 100
    DEFAULT_SIMILARITY_THRESHOLD = 80
    MAX_OPEN_FOLDERS_WARNING = 5
    
    # 窗口设置
    WINDOW_MIN_WIDTH = 700
    WINDOW_MIN_HEIGHT = 500
    WINDOW_DEFAULT_WIDTH = 900
    WINDOW_DEFAULT_HEIGHT = 650


class AppTheme:
    """集中管理应用主题颜色"""

    def __init__(self):
        # 商务风配色常量 - 扩展版
        self.colors = {
            "primary": "#2c3e50",  # 深蓝灰色(主色)
            "secondary": "#3498db",  # 中蓝色(辅助色)
            "light": "#ecf0f1",  # 浅灰色(背景)
            "border": "#d5d8dc",  # 边框颜色
            "success": "#27ae60",  # 成功色
            "warning": "#e74c3c",  # 警告色
            "text": "#2c3e50",  # 文本颜色
            "light_text": "#7f8c8d",  # 浅色文本
            "group_bg": "#d6eaf8",  # 淡蓝色组背景
            "item1_bg": "#e8f8f5",  # 浅绿色第一项背景
            "item2_bg": "#fef9e7",  # 浅黄色其他项背景
            "row_alt": "#ffffff",  # 交替行色
        }

    def get_button_style(self, is_action=False):
        """获取按钮样式"""
        return f"""
            QPushButton {{
                background-color: {self.colors["secondary"]};
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #2980b9;
            }}
            QPushButton:pressed {{
                background-color: #1f6da8;
            }}
        """

    def get_directory_button_style(self):
        """获取目录按钮样式"""
        return f"""
            QPushButton {{
                text-align: left;
                padding: 8px 10px;
                background-color: white;
                border: 1px solid {self.colors["border"]};
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: {self.colors["light"]};
                border: 1px solid {self.colors["secondary"]};
            }}
        """

    def get_tree_widget_style(self):
        """获取树形控件样式"""
        return f"""
            QTreeWidget {{
                border: 1px solid {self.colors["border"]};
                border-radius: 4px;
                background-color: white;
                alternate-background-color: {self.colors["light"]};
                gridline-color: {self.colors["border"]};
            }}
            QTreeWidget::item {{
                padding: 6px;
                border-bottom: 1px solid {self.colors["border"]};
                min-height: 24px;
            }}
            QTreeWidget::item:selected {{
                background-color: {self.colors["secondary"]};
                color: white;
            }}
            QHeaderView::section {{
                background-color: {self.colors["primary"]};
                color: white;
                padding: 8px;
                border: none;
                font-weight: bold;
                cursor: pointer;
            }}
            QHeaderView::section:hover {{
                background-color: #34495e;
            }}
        """

    def get_progress_bar_style(self):
        """获取进度条样式"""
        return f"""
            QProgressBar {{
                border: none;
                border-radius: 4px;
                background-color: {self.colors["light"]};
                text-align: center;
                height: 18px;
                font-size: 12px;
            }}
            QProgressBar::chunk {{
                background-color: {self.colors["success"]};
                border-radius: 4px;
            }}
        """

    def get_label_style(self, is_tip=False, is_stats=False):
        """获取标签样式"""
        if is_tip:
            return f"""
                QLabel {{
                    color: {self.colors["light_text"]};
                    padding: 4px;
                    font-size: 14px;
                    border-bottom: 1px solid {self.colors["border"]};
                    margin-bottom: 5px;
                }}
            """
        elif is_stats:
            return f"""
                QLabel {{
                    color: {self.colors["primary"]};
                    font-weight: bold;
                    padding: 4px;
                    margin-top: 5px;
                }}
            """
        else:
            return f"""
                QLabel {{
                    color: {self.colors["text"]};
                }}
            """


class CustomSpinner(QtWidgets.QWidget):
    valueChanged = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_index = 0
        self.options = ["番号", "系列"]
        self.initUI()

    def initUI(self):
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 文本显示区域
        self.display = QtWidgets.QLineEdit()
        self.display.setReadOnly(True)
        self.display.setText(self.options[0])
        self.display.setAlignment(Qt.AlignCenter)

        # 按钮容器
        button_container = QtWidgets.QWidget()
        button_layout = QtWidgets.QVBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(0)

        # 上下箭头按钮
        self.up_button = QtWidgets.QPushButton("▲")
        self.down_button = QtWidgets.QPushButton("▼")

        # 设置按钮大小
        self.up_button.setFixedHeight(AppConstants.BUTTON_HEIGHT)
        self.down_button.setFixedHeight(AppConstants.BUTTON_HEIGHT)

        # 添加按钮到布局
        button_layout.addWidget(self.up_button)
        button_layout.addWidget(self.down_button)
        button_container.setLayout(button_layout)
        button_container.setFixedWidth(24)

        # 添加到主布局
        layout.addWidget(self.display)
        layout.addWidget(button_container)
        self.setLayout(layout)

        # 连接信号
        self.up_button.clicked.connect(self.previous_item)
        self.down_button.clicked.connect(self.next_item)

        # 统一使用样式表
        self.setStyleSheet(
            """
            QLineEdit {
                border: 1px solid #e2e8f0;
                border-right: none;
                border-top-left-radius: 4px;
                border-bottom-left-radius: 4px;
                padding: 5px;
                background: white;
                min-width: 60px;
            }
            QPushButton {
                border: 1px solid #e2e8f0;
                background: white;
                font-size: 8px;
                padding: 0px;
            }
            QPushButton:hover {
                background: #f8fafc;
            }
            QPushButton:pressed {
                background: #f1f5f9;
            }
            """
        )

    def previous_item(self):
        self.current_index = (self.current_index - 1) % len(self.options)
        self.display.setText(self.options[self.current_index])
        self.valueChanged.emit()

    def next_item(self):
        self.current_index = (self.current_index + 1) % len(self.options)
        self.display.setText(self.options[self.current_index])
        self.valueChanged.emit()

    def get_current_value(self):
        return self.options[self.current_index]


class MatchModeWidget(QtWidgets.QWidget):
    """匹配模式选择控件"""
    modeChanged = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.initUI()

    def initUI(self):
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        # 匹配模式选择
        self.exact_radio = QtWidgets.QRadioButton("完全匹配")
        self.partial_radio = QtWidgets.QRadioButton("部分匹配")
        self.exact_radio.setChecked(True)

        # 部分匹配阈值设置
        threshold_layout = QtWidgets.QHBoxLayout()
        self.threshold_label = QtWidgets.QLabel("相似度:")
        self.threshold_spin = QtWidgets.QSpinBox()
        self.threshold_spin.setRange(AppConstants.SIMILARITY_THRESHOLD_MIN, 
                                   AppConstants.SIMILARITY_THRESHOLD_MAX)
        self.threshold_spin.setValue(AppConstants.DEFAULT_SIMILARITY_THRESHOLD)
        self.threshold_spin.setSuffix("%")
        self.threshold_spin.setEnabled(False)

        threshold_layout.addWidget(self.threshold_label)
        threshold_layout.addWidget(self.threshold_spin)
        threshold_layout.addStretch()

        layout.addWidget(self.exact_radio)
        layout.addWidget(self.partial_radio)
        layout.addLayout(threshold_layout)

        self.setLayout(layout)

        # 连接信号
        self.exact_radio.toggled.connect(self.on_mode_changed)
        self.partial_radio.toggled.connect(self.on_mode_changed)
        self.threshold_spin.valueChanged.connect(self.modeChanged.emit)

    def on_mode_changed(self):
        self.threshold_spin.setEnabled(self.partial_radio.isChecked())
        self.modeChanged.emit()

    def is_exact_match(self):
        return self.exact_radio.isChecked()

    def get_threshold(self):
        return self.threshold_spin.value() / 100.0


class DirectoryButton(QtWidgets.QPushButton):
    rightClicked = QtCore.pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )

    def setText(self, text):
        """设置按钮文本，优化显示"""
        if text:
            display_text = self._get_display_text(text)
            super().setText(display_text)
            self.setToolTip(text)
        else:
            super().setText("")
            self.setToolTip("")

    def _get_display_text(self, text):
        """获取显示文本，处理路径显示"""
        try:
            # 去除末尾的路径分隔符
            cleaned_text = text.rstrip("/\\")
            # 获取目录名
            display_text = os.path.basename(cleaned_text)
            # 如果为空（如根目录），返回原文本
            return display_text or text
        except Exception:
            return text

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.rightClicked.emit()
        else:
            super().mousePressEvent(event)


class NfoFile:
    """NFO文件处理类"""

    # 编码尝试顺序
    ENCODINGS = ["utf-8", "gbk", "cp936", "latin1"]

    # 番号匹配正则表达式
    CODE_PATTERNS = [
        (r"([A-Za-z]{2,15})-?(\d{2,5})", False),  # 标准番号如 MIDE-954 或 MY-948
        (r"([A-Za-z]{2,15})(\d{2,5})", False),  # 无连字符格式如 MIDE954
        (r"FC2-?PPV-?(\d{6,7})", True),  # FC2格式
        (r"T-?(\d{2,3})-?(\d{3})", True),  # T28系列特殊格式
        (r"(\d{6})-(\d{3})", True),  # 全数字格式如 050525-001
    ]

    # CD标识模式，用于排除重复检测
    CD_PATTERNS = [
        r"[-_\s]*cd[12]?[-_\s]*",  # 匹配 cd1, cd2, -cd1, _cd2 等
        r"[-_\s]*disc[12]?[-_\s]*",  # 匹配 disc1, disc2 等
        r"[-_\s]*[第]?[一二1-2][张碟盘][-_\s]*",  # 匹配中文 第一张、第二盘 等
    ]

    @staticmethod
    def safe_read_file(file_path):
        """安全地读取文件，尝试多种编码，增强异常处理"""
        if not os.path.exists(file_path):
            return None, None
        
        if not os.access(file_path, os.R_OK):
            print(f"文件无读取权限 {file_path}")
            return None, None

        for encoding in NfoFile.ENCODINGS:
            try:
                with open(file_path, "r", encoding=encoding) as f:
                    return f.read(), encoding
            except UnicodeDecodeError:
                continue
            except PermissionError:
                print(f"文件权限错误 {file_path}")
                return None, None
            except FileNotFoundError:
                print(f"文件不存在 {file_path}")
                return None, None
            except Exception as e:
                print(f"读取文件出错 {file_path}: {str(e)}")
                return None, None

        print(f"无法以任何编码打开文件 {file_path}")
        return None, None

    @staticmethod
    def extract_code(text):
        """从文本中提取番号"""
        if not text:
            return None

        for pattern, is_special in NfoFile.CODE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                if is_special:
                    if "FC2" in pattern:
                        return match.group(0).upper()
                    elif "T-?" in pattern:
                        return f"T-{match.group(1)}-{match.group(2)}".upper()
                    elif r"(\d{6})-(\d{3})" == pattern:
                        return f"{match.group(1)}-{match.group(2)}"
                    else:
                        return match.group(0).upper()
                else:
                    return f"{match.group(1)}-{match.group(2)}".upper()
        return None

    @staticmethod
    def similarity(str1, str2):
        """计算两个字符串的相似度，修复缺失的方法"""
        if not str1 or not str2:
            return 0.0
        
        # 使用SequenceMatcher计算相似度
        return SequenceMatcher(None, str1.lower(), str2.lower()).ratio()

    @staticmethod
    def should_exclude_cd_duplicate(file_paths):
        """检查文件路径列表中是否包含CD标识，如果是则应排除重复检测"""
        if len(file_paths) <= 1:
            return False
        
        return NfoFile._check_all_files_have_cd_markers(file_paths)

    @staticmethod
    def _check_all_files_have_cd_markers(file_paths):
        """检查所有文件是否都包含CD标识"""
        cd_files = []
        for path in file_paths:
            filename = os.path.basename(path).lower()
            for pattern in NfoFile.CD_PATTERNS:
                if re.search(pattern, filename, re.IGNORECASE):
                    cd_files.append(path)
                    break
        
        # 如果所有文件都包含CD标识，则排除
        return len(cd_files) == len(file_paths)


class NfoDuplicateLogic:
    """处理NFO文件查重的核心逻辑类"""

    # 字段常量
    FIELD_NUM = "番号"
    FIELD_SERIES = "系列"

    def get_nfo_files_generator(self, directories):
        """使用生成器获取所有NFO文件路径"""
        for directory in directories:
            if not os.path.exists(directory):
                continue
            try:
                for root, _, files in os.walk(directory):
                    for file in files:
                        if file.lower().endswith(".nfo"):
                            yield os.path.join(root, file)
            except PermissionError:
                print(f"无权限访问目录: {directory}")
                continue
            except Exception as e:
                print(f"遍历目录出错 {directory}: {str(e)}")
                continue

    def process_nfo_file(self, args):
        """
        处理单个NFO文件，提取指定字段值

        Args:
            args (tuple): (文件路径, 要查找的字段)

        Returns:
            tuple: (提取的值, 文件路径) 或 (None, 文件路径)表示未找到
        """
        nfo_file, field = args

        # 检查文件是否存在
        if not os.path.exists(nfo_file):
            return None, nfo_file

        # 读取文件内容
        content, encoding = NfoFile.safe_read_file(nfo_file)
        if content is None:
            return None, nfo_file

        # 解析XML
        try:
            tree = ET.fromstring(content)
            result = self._extract_field_value(tree, field, nfo_file)
            return result, nfo_file

        except ET.ParseError as e:
            print(f"XML解析错误 {nfo_file}: {e}")
        except Exception as e:
            print(f"处理文件错误 {nfo_file}: {e}")

        return None, nfo_file

    def _extract_field_value(self, tree, field, nfo_file):
        """从XML树中提取字段值"""
        if field == self.FIELD_NUM:
            return self._extract_num_field(tree, nfo_file)
        elif field == self.FIELD_SERIES:
            return self._extract_series_field(tree)
        return None

    def _extract_num_field(self, tree, nfo_file):
        """提取番号字段"""
        # 优先级1: <num>标签
        num_elem = tree.find("num")
        if num_elem is not None and num_elem.text:
            return num_elem.text.strip()

        # 优先级2: 标题字段
        for tag_name in ["title", "originaltitle", "sorttitle"]:
            elem = tree.find(tag_name)
            if elem is not None and elem.text:
                code = NfoFile.extract_code(elem.text)
                if code:
                    return code

        # 优先级3: 文件名
        filename = os.path.basename(nfo_file)
        code = NfoFile.extract_code(filename)
        return code

    def _extract_series_field(self, tree):
        """提取系列字段"""
        series_elem = tree.find("series")
        if series_elem is not None and series_elem.text:
            return series_elem.text.strip()
        return None

    def find_duplicates_with_similarity(self, field_value_map, is_exact_match, threshold):
        """根据匹配模式查找重复项，重构减少重复代码"""
        if is_exact_match:
            return self._find_exact_duplicates(field_value_map)
        else:
            return self._find_partial_duplicates(field_value_map, threshold)

    def _find_exact_duplicates(self, field_value_map):
        """查找完全匹配的重复项"""
        duplicates = {}
        for value, paths in field_value_map.items():
            if len(paths) > 1 and not self._should_exclude_duplicate(paths):
                duplicates[value] = paths
        return duplicates

    def _find_partial_duplicates(self, field_value_map, threshold):
        """查找部分匹配的重复项"""
        duplicates = {}
        processed = set()
        values_list = list(field_value_map.keys())
        
        for i, value1 in enumerate(values_list):
            if value1 in processed:
                continue
            
            similar_group = [value1]
            processed.add(value1)
            
            # 查找相似项
            for j in range(i + 1, len(values_list)):
                value2 = values_list[j]
                if value2 in processed:
                    continue
                
                if NfoFile.similarity(value1, value2) >= threshold:
                    similar_group.append(value2)
                    processed.add(value2)
            
            # 处理相似组
            self._process_similar_group(similar_group, field_value_map, duplicates)
        
        return duplicates

    def _process_similar_group(self, similar_group, field_value_map, duplicates):
        """处理相似组"""
        if len(similar_group) > 1:
            # 合并相似项的路径
            all_paths = []
            for val in similar_group:
                all_paths.extend(field_value_map[val])
            
            if not self._should_exclude_duplicate(all_paths):
                duplicates[similar_group[0]] = all_paths
        elif len(field_value_map[similar_group[0]]) > 1:
            # 单个值但有多个文件
            if not self._should_exclude_duplicate(field_value_map[similar_group[0]]):
                duplicates[similar_group[0]] = field_value_map[similar_group[0]]

    def _should_exclude_duplicate(self, paths):
        """统一的重复检测排除逻辑"""
        return NfoFile.should_exclude_cd_duplicate(paths)


class NfoDuplicateOperations:
    def __init__(self, ui_instance):
        self.ui = ui_instance
        self.result_lock = Lock()
        self.progress_lock = Lock()
        self.processed_files = 0
        self.batch_size = AppConstants.DEFAULT_BATCH_SIZE
        self.current_sort_column = 0
        self.current_sort_order = Qt.AscendingOrder

    def select_directories(self, index=-1):
        """选择目录，index=-1表示新增，否则表示替换指定位置"""
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self.ui,
            "选择目录",
            ".",
            QtWidgets.QFileDialog.ShowDirsOnly
            | QtWidgets.QFileDialog.DontUseNativeDialog,
        )
        if directory and os.path.exists(directory):
            if index >= 0:
                if index < len(self.ui.selected_directories):
                    self.ui.selected_directories[index] = directory
                    self.ui.update_directory_display()
            else:
                self.ui.add_directory(directory)

    def find_duplicates(self):
        """查找重复项的核心方法，增强线程安全"""
        if not self.ui.selected_directories:
            QtWidgets.QMessageBox.warning(self.ui, "错误", "请先选择至少一个目录！")
            return

        # 线程安全地重置状态
        with self.progress_lock:
            self.processed_files = 0
        
        self.ui.start_button.setEnabled(False)
        self.ui.start_button.setText("正在查找...")

        selected_field = self.ui.field_spinner.get_current_value()
        is_exact_match = self.ui.match_mode_widget.is_exact_match()
        threshold = self.ui.match_mode_widget.get_threshold()

        self.ui.progress_bar.setValue(0)
        self.ui.progress_bar.setFormat("处理中: %p%")
        self.ui.result_stats_label.setText("")

        # 获取文件列表
        nfo_files = list(
            self.ui.logic.get_nfo_files_generator(self.ui.selected_directories)
        )
        total_files = len(nfo_files)
        self.ui.progress_bar.setMaximum(total_files)

        if total_files == 0:
            self._handle_no_files_found()
            return

        field_value_map = {}

        # 创建更新定时器，降低更新频率
        self.update_timer = QtCore.QTimer()
        self.update_timer.setInterval(AppConstants.PROGRESS_UPDATE_INTERVAL)
        self.update_timer.timeout.connect(self._update_progress_ui)
        self.update_timer.start()

        # 处理文件
        self._process_files_in_batches(nfo_files, selected_field, field_value_map)

        # 停止定时器
        self.update_timer.stop()
        self.ui.progress_bar.setValue(total_files)

        # 根据匹配模式查找重复项
        duplicates = self.ui.logic.find_duplicates_with_similarity(
            field_value_map, is_exact_match, threshold
        )

        self.display_duplicates(duplicates)
        self._reset_ui_state()

    def _handle_no_files_found(self):
        """处理未找到文件的情况"""
        QtWidgets.QMessageBox.information(self.ui, "提示", "在所选目录中未找到NFO文件。")
        self._reset_ui_state()

    def _reset_ui_state(self):
        """重置UI状态"""
        self.ui.start_button.setEnabled(True)
        self.ui.start_button.setText("开始查重")

    def _process_files_in_batches(self, nfo_files, selected_field, field_value_map):
        """批量处理文件"""
        # 划分批次
        batches = [
            nfo_files[i : i + self.batch_size]
            for i in range(0, len(nfo_files), self.batch_size)
        ]

        # 使用线程池处理批次
        with ThreadPoolExecutor(max_workers=cpu_count()) as executor:
            future_to_batch = {
                executor.submit(self._process_batch, batch, selected_field): batch
                for batch in batches
            }

            for future in as_completed(future_to_batch):
                try:
                    batch_results = future.result()
                    with self.result_lock:
                        self._merge_batch_results(batch_results, field_value_map)
                except Exception as e:
                    print(f"处理批次时出错: {str(e)}")

    def _merge_batch_results(self, batch_results, field_value_map):
        """合并批次结果"""
        for key, values in batch_results.items():
            if key in field_value_map:
                field_value_map[key].extend(values)
            else:
                field_value_map[key] = values

    def _update_progress_ui(self):
        """更新UI进度条，由定时器调用"""
        with self.progress_lock:
            current_progress = self.processed_files
            self.ui.progress_bar.setValue(current_progress)

    def _process_batch(self, file_batch, selected_field):
        """处理一批文件，由线程池调用"""
        batch_results = {}
        local_processed = 0

        for nfo_file in file_batch:
            result = self.ui.logic.process_nfo_file((nfo_file, selected_field))
            if result[0]:
                field_value = result[0]
                if field_value in batch_results:
                    batch_results[field_value].append(result[1])
                else:
                    batch_results[field_value] = [result[1]]

            local_processed += 1

        with self.progress_lock:
            self.processed_files += local_processed

        return batch_results

    def display_duplicates(self, duplicates):
        """显示重复项结果"""
        self.ui.result_list.clear()

        # 准备排序数据
        sort_data = []
        for field_value, paths in duplicates.items():
            if len(paths) > 1:
                sort_data.append((field_value, paths, len(paths)))

        # 根据当前排序设置进行排序
        self._sort_data(sort_data)

        group_count = 0
        total_files = 0

        for field_value, paths, file_count in sort_data:
            group_count += 1
            total_files += file_count
            self._create_duplicate_group_item(field_value, paths, file_count)

        self._update_result_stats(group_count, total_files)

    def _create_duplicate_group_item(self, field_value, paths, file_count):
        """创建重复组项目"""
        first_directory = (
            self.ui.selected_directories[0] if self.ui.selected_directories else None
        )

        # 对路径排序
        sorted_paths = sorted(
            paths,
            key=lambda x: (
                0 if first_directory and x.startswith(first_directory) else 1,
                x,
            ),
        )

        # 创建根项
        root_item = self._create_root_item(field_value, file_count, sorted_paths)
        
        # 添加子项
        self._add_child_items(root_item, sorted_paths[1:], first_directory)
        
        root_item.setExpanded(True)

    def _create_root_item(self, field_value, file_count, sorted_paths):
        """创建根项目"""
        root_item = QtWidgets.QTreeWidgetItem(self.ui.result_list)
        root_item.setText(0, field_value)
        root_item.setText(1, str(file_count))
        root_item.setText(2, sorted_paths[0] if sorted_paths else "")
        root_item.setFont(0, QtGui.QFont("", weight=QtGui.QFont.Bold))

        # 设置背景色
        root_item.setBackground(0, QtGui.QColor(self.ui.theme.colors["group_bg"]))
        root_item.setBackground(1, QtGui.QColor(self.ui.theme.colors["group_bg"]))
        root_item.setBackground(2, QtGui.QColor(self.ui.theme.colors["item1_bg"]))

        # 存储数据用于排序
        root_item.setData(0, QtCore.Qt.UserRole, 
                         {"value": field_value, "is_group": True, "paths": sorted_paths})
        root_item.setData(1, QtCore.Qt.UserRole, {"count": file_count})
        root_item.setData(2, QtCore.Qt.UserRole, 
                         {"is_file": True, "path": sorted_paths[0] if sorted_paths else ""})

        return root_item

    def _add_child_items(self, root_item, remaining_paths, first_directory):
        """添加子项目"""
        use_alt_bg = True
        for path in remaining_paths:
            child_item = QtWidgets.QTreeWidgetItem(root_item)
            child_item.setText(2, path)

            bg_color = (
                QtGui.QColor(self.ui.theme.colors["item2_bg"])
                if use_alt_bg
                else QtGui.QColor(self.ui.theme.colors["row_alt"])
            )
            child_item.setBackground(2, bg_color)
            use_alt_bg = not use_alt_bg

            if first_directory and path.startswith(first_directory):
                child_item.setForeground(
                    2, QtGui.QColor(self.ui.theme.colors["secondary"])
                )

            child_item.setData(
                2, QtCore.Qt.UserRole, {"is_file": True, "path": path}
            )

    def _update_result_stats(self, group_count, total_files):
        """更新结果统计"""
        if group_count == 0:
            QtWidgets.QMessageBox.information(self.ui, "结果", "未找到重复项。")
            self.ui.result_stats_label.setText("")
        else:
            match_mode = ("完全匹配" if self.ui.match_mode_widget.is_exact_match() 
                         else f"部分匹配({self.ui.match_mode_widget.get_threshold()*100:.0f}%)")
            self.ui.result_stats_label.setText(
                f"找到 {group_count} 组重复项，共 {total_files} 个文件 ({match_mode})"
            )

        self.ui.progress_bar.setFormat(
            f"完成：找到 {group_count} 组重复项" if group_count > 0 else "完成"
        )

    def _sort_data(self, sort_data):
        """根据当前排序设置对数据进行排序"""
        reverse = self.current_sort_order == Qt.DescendingOrder
        
        sort_key = self._get_sort_key()
        sort_data.sort(key=sort_key, reverse=reverse)

    def _get_sort_key(self):
        """获取排序键函数"""
        if self.current_sort_column == 0:  # 按字段值排序
            return lambda x: x[0].lower()
        elif self.current_sort_column == 1:  # 按文件数量排序
            return lambda x: x[2]
        elif self.current_sort_column == 2:  # 按第一个路径排序
            return lambda x: x[1][0] if x[1] else ""
        else:
            return lambda x: x[0].lower()  # 默认排序

    def on_header_clicked(self, logical_index):
        """处理表头点击事件"""
        if logical_index == self.current_sort_column:
            # 切换排序顺序
            self.current_sort_order = (
                Qt.DescendingOrder if self.current_sort_order == Qt.AscendingOrder 
                else Qt.AscendingOrder
            )
        else:
            # 更改排序列
            self.current_sort_column = logical_index
            self.current_sort_order = Qt.AscendingOrder

        # 重新显示结果（如果有数据）
        if self.ui.result_list.topLevelItemCount() > 0:
            self._re_sort_current_results()

    def _re_sort_current_results(self):
        """重新排序当前结果"""
        # 收集当前所有数据
        sort_data = []
        for i in range(self.ui.result_list.topLevelItemCount()):
            item = self.ui.result_list.topLevelItem(i)
            
            # 从UserRole数据中获取信息
            value_data = item.data(0, QtCore.Qt.UserRole)
            count_data = item.data(1, QtCore.Qt.UserRole)
            
            if value_data and value_data.get("is_group"):
                field_value = value_data.get("value", item.text(0))
                paths = value_data.get("paths", [])
                file_count = count_data.get("count", len(paths)) if count_data else len(paths)
                sort_data.append((field_value, paths, file_count))

        # 重新排序并显示
        if sort_data:
            duplicates = {item[0]: item[1] for item in sort_data}
            self.display_duplicates(duplicates)

    def open_folder(self, item, column):
        """处理项目双击事件，打开对应文件夹"""
        data = item.data(column, QtCore.Qt.UserRole)
        if not data:
            return

        if column == 0 and data.get("is_group"):
            self._open_group_folders(data.get("paths", []))
        elif data.get("is_file"):
            path = data.get("path")
            if path:
                self._open_single_folder(os.path.dirname(path))

    def _open_single_folder(self, folder_path):
        """打开单个文件夹"""
        try:
            if sys.platform == "win32":
                os.startfile(folder_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder_path])
            else:
                subprocess.Popen(["xdg-open", folder_path])
        except Exception as e:
            QtWidgets.QMessageBox.warning(self.ui, "错误", f"无法打开文件夹: {str(e)}")

    def _open_group_folders(self, paths):
        """打开一组文件夹"""
        if not paths:
            return

        unique_folders = set()
        for path in paths:
            folder = os.path.dirname(path)
            unique_folders.add(folder)

        if len(unique_folders) > AppConstants.MAX_OPEN_FOLDERS_WARNING:
            reply = QtWidgets.QMessageBox.question(
                self.ui,
                "确认",
                f"将要打开 {len(unique_folders)} 个文件夹，是否继续？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if reply == QtWidgets.QMessageBox.No:
                return

        for folder in unique_folders:
            self._open_single_folder(folder)

    def clear_results_on_change(self):
        """当选择器变化时清空结果"""
        self.ui.result_list.clear()
        self.ui.result_stats_label.setText("")


class DirectoryManager:
    """目录管理类，优化目录操作逻辑"""
    
    def __init__(self, ui_instance):
        self.ui = ui_instance

    def update_directory_button(self, index, directory):
        """更新指定位置的目录按钮"""
        if 0 <= index < len(self.ui.dir_buttons):
            self.ui.dir_buttons[index].setText(directory)

    def clear_all_buttons(self):
        """清空所有目录按钮"""
        for btn in self.ui.dir_buttons:
            btn.setText("")

    def update_all_buttons(self, directories):
        """更新所有目录按钮显示"""
        self.clear_all_buttons()
        for i, directory in enumerate(directories[:AppConstants.MAX_DIRECTORIES]):
            self.update_directory_button(i, directory)


class NfoDuplicateFinder(QtWidgets.QWidget):
    def __init__(self, initial_directory=None):
        super().__init__()
        self.theme = AppTheme()
        self.logic = NfoDuplicateLogic()
        self.selected_directories = []
        self.dir_buttons = []
        self.directory_manager = DirectoryManager(self)
        self.init_ui()
        self.operations = NfoDuplicateOperations(self)
        self.connect_signals()
        self.load_directories()
        if initial_directory and os.path.exists(initial_directory):
            self.add_directory(initial_directory)

    def init_ui(self):
        self.setWindowTitle("大锤 NFO查重工具 v10.1.0")
        self.setGeometry(100, 100, 
                        AppConstants.WINDOW_DEFAULT_WIDTH, 
                        AppConstants.WINDOW_DEFAULT_HEIGHT)
        self.setMinimumSize(AppConstants.WINDOW_MIN_WIDTH, 
                           AppConstants.WINDOW_MIN_HEIGHT)

        self._load_app_icon()

        # 主布局
        main_layout = QtWidgets.QVBoxLayout()
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # 顶部区域布局
        top_container = self._create_top_container()

        # 提示文本标签
        self.tip_label = QtWidgets.QLabel(
            "使用说明：九宫格内已选目录，左键重选，右键删除；点击表头调整排序；自动排除CD1/CD2重复"
        )

        # 结果列表
        self.result_list = self._create_result_list()

        # 进度条
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setValue(0)

        # 结果统计标签
        self.result_stats_label = QtWidgets.QLabel("")
        self.result_stats_label.setAlignment(Qt.AlignRight)

        # 添加所有组件到主布局
        main_layout.addWidget(top_container)
        main_layout.addWidget(self.tip_label)
        main_layout.addWidget(self.result_list, 1)
        main_layout.addWidget(self.result_stats_label)
        main_layout.addWidget(self.progress_bar)

        self.setLayout(main_layout)
        self.apply_basic_styles()

    def _create_top_container(self):
        """创建顶部容器"""
        top_container = QtWidgets.QWidget()
        top_layout = QtWidgets.QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        # 左侧九宫格布局
        self.grid_widget = self._create_directory_grid()

        # 右侧控制区域
        right_container = self._create_control_panel()

        # 添加到顶部布局
        top_layout.addWidget(self.grid_widget, 4)
        top_layout.addWidget(right_container, 1)
        top_container.setLayout(top_layout)

        return top_container

    def _create_directory_grid(self):
        """创建目录九宫格"""
        grid_widget = QtWidgets.QWidget()
        self.grid_layout = QtWidgets.QGridLayout()
        self.grid_layout.setSpacing(1)
        self.grid_layout.setContentsMargins(1, 1, 1, 1)

        for i in range(3):
            self.grid_layout.setColumnStretch(i, 1)
            self.grid_layout.setRowStretch(i, 1)

        for i in range(AppConstants.MAX_DIRECTORIES):
            btn = DirectoryButton()
            self.dir_buttons.append(btn)
            self.grid_layout.addWidget(btn, i // 3, i % 3)

        grid_widget.setLayout(self.grid_layout)
        return grid_widget

    def _create_control_panel(self):
        """创建控制面板"""
        right_container = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout()
        right_layout.setContentsMargins(0, 1, 0, 1)
        right_layout.setSpacing(8)

        # 选择目录按钮
        self.select_dir_button = QtWidgets.QPushButton("选择目录")
        self.select_dir_button.setFixedSize(140, 40)

        # 查重字段选择
        self.field_spinner = CustomSpinner()
        self.field_spinner.setFixedSize(140, 40)

        # 匹配模式控件
        self.match_mode_widget = MatchModeWidget()
        self.match_mode_widget.setFixedWidth(140)

        # 查找按钮
        self.start_button = QtWidgets.QPushButton("开始查重")
        self.start_button.setFixedSize(140, 40)

        # 添加到右侧布局
        right_layout.addWidget(self.select_dir_button, alignment=Qt.AlignTop)
        right_layout.addWidget(self.field_spinner, alignment=Qt.AlignTop)
        right_layout.addWidget(self.match_mode_widget, alignment=Qt.AlignTop)
        right_layout.addStretch(1)
        right_layout.addWidget(self.start_button, alignment=Qt.AlignBottom)

        right_container.setLayout(right_layout)
        return right_container

    def _create_result_list(self):
        """创建结果列表"""
        result_list = QtWidgets.QTreeWidget()
        result_list.setColumnCount(3)
        result_list.setHeaderLabels(["重复番号/系列", "文件数", "文件路径"])
        result_list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        result_list.header().setStretchLastSection(True)
        result_list.setIndentation(20)
        result_list.setAnimated(True)
        
        # 设置列宽
        result_list.setColumnWidth(0, 200)
        result_list.setColumnWidth(1, 80)

        return result_list

    def _load_app_icon(self):
        """加载应用图标"""
        try:
            if getattr(sys, "frozen", False):
                application_path = sys._MEIPASS
            else:
                application_path = os.path.dirname(os.path.abspath(__file__))

            icon_path = os.path.join(application_path, "cg_dedupe.ico")
            if os.path.exists(icon_path):
                self.setWindowIcon(QtGui.QIcon(icon_path))
        except Exception as e:
            print(f"图标设置失败: {str(e)}")

    def apply_basic_styles(self):
        """应用基本样式"""
        self.setStyleSheet(
            f"""
            QWidget {{
                background-color: white;
                color: {self.theme.colors["text"]};
                font-family: 'Segoe UI', Arial, sans-serif;
            }}
        """
        )

        # 应用按钮样式
        self.select_dir_button.setStyleSheet(self.theme.get_button_style())
        self.start_button.setStyleSheet(self.theme.get_button_style())

        # 目录按钮样式
        for btn in self.dir_buttons:
            btn.setStyleSheet(self.theme.get_directory_button_style())

        # 提示标签样式
        self.tip_label.setStyleSheet(self.theme.get_label_style(is_tip=True))

        # 表格样式
        self.result_list.setStyleSheet(self.theme.get_tree_widget_style())
        self.result_list.setAlternatingRowColors(True)
        self.result_list.setUniformRowHeights(True)

        # 结果统计标签样式
        self.result_stats_label.setStyleSheet(self.theme.get_label_style(is_stats=True))

        # 进度条样式
        self.progress_bar.setStyleSheet(self.theme.get_progress_bar_style())

    def connect_signals(self):
        """连接所有信号"""
        # 目录按钮信号
        for i, btn in enumerate(self.dir_buttons):
            btn.clicked.connect(
                lambda checked, idx=i: self.operations.select_directories(idx)
            )
            btn.rightClicked.connect(lambda idx=i: self.remove_directory(idx))

        # 其他信号
        self.select_dir_button.clicked.connect(
            lambda: self.operations.select_directories()
        )
        self.field_spinner.valueChanged.connect(self.operations.clear_results_on_change)
        self.match_mode_widget.modeChanged.connect(self.operations.clear_results_on_change)
        self.start_button.clicked.connect(self.operations.find_duplicates)
        self.result_list.itemDoubleClicked.connect(
            lambda item, column: self.operations.open_folder(item, column)
        )
        
        # 表头点击信号
        self.result_list.header().sectionClicked.connect(
            self.operations.on_header_clicked
        )

    def add_directory(self, directory):
        """添加新目录到网格"""
        if (
            directory not in self.selected_directories
            and len(self.selected_directories) < AppConstants.MAX_DIRECTORIES
        ):
            self.selected_directories.append(directory)
            self.update_directory_display()
            self.save_directories()

    def remove_directory(self, index):
        """移除指定位置的目录"""
        if 0 <= index < len(self.selected_directories):
            self.selected_directories.pop(index)
            self.update_directory_display()
            self.save_directories()

    def update_directory_display(self):
        """更新目录显示，使用目录管理器"""
        self.directory_manager.update_all_buttons(self.selected_directories)

    def load_directories(self):
        """加载保存的目录"""
        settings = QtCore.QSettings("NfoDuplicateFinder", "Directories")
        saved_directories = settings.value("directories", [])
        self.selected_directories = (
            [d for d in saved_directories if os.path.exists(d)]
            if saved_directories
            else []
        )
        self.update_directory_display()

    def save_directories(self):
        """保存目录列表"""
        settings = QtCore.QSettings("NfoDuplicateFinder", "Directories")
        settings.setValue("directories", self.selected_directories)

    def closeEvent(self, event):
        """窗口关闭事件处理"""
        self.save_directories()
        event.accept()


if __name__ == "__main__":
    freeze_support()
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    initial_directory = None
    if len(sys.argv) > 1:
        initial_directory = sys.argv[1]

    # Enable High DPI support
    if hasattr(QtCore.Qt, "AA_EnableHighDpiScaling"):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    if hasattr(QtCore.Qt, "AA_UseHighDpiPixmaps"):
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    window = NfoDuplicateFinder(initial_directory=initial_directory)

    # 窗口居中显示
    screen = app.primaryScreen().geometry()
    window_geometry = window.geometry()
    x = (screen.width() - window_geometry.width()) // 2
    y = (screen.height() - window_geometry.height()) // 2
    window.move(x, y)

    window.show()
    sys.exit(app.exec_())