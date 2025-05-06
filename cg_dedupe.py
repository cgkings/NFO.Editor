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
        button_height = 20
        self.up_button.setFixedHeight(button_height)
        self.down_button.setFixedHeight(button_height)

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


class DirectoryButton(QtWidgets.QPushButton):
    rightClicked = QtCore.pyqtSignal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        # 样式将通过theme统一应用

    def setText(self, text):
        if text:
            display_text = os.path.basename(text.rstrip("/\\")) or text
            super().setText(display_text)
            self.setToolTip(text)
        else:
            super().setText("")
            self.setToolTip("")

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

    @staticmethod
    def safe_read_file(file_path):
        """安全地读取文件，尝试多种编码"""
        for encoding in NfoFile.ENCODINGS:
            try:
                with open(file_path, "r", encoding=encoding) as f:
                    return f.read(), encoding
            except UnicodeDecodeError:
                continue
            except Exception as e:
                print(f"打开文件出错 {file_path}: {str(e)}")
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
                    # 特殊格式处理
                    if "FC2" in pattern:
                        return match.group(0).upper()
                    elif "T-?" in pattern:
                        return f"T-{match.group(1)}-{match.group(2)}".upper()
                    elif r"(\d{6})-(\d{3})" == pattern:
                        # 保持全数字格式原样
                        return f"{match.group(1)}-{match.group(2)}"
                    else:
                        return match.group(0).upper()
                else:
                    # 标准格式，确保使用连字符
                    return f"{match.group(1)}-{match.group(2)}".upper()
        return None


class NfoDuplicateLogic:
    """处理NFO文件查重的核心逻辑类"""

    # 字段常量
    FIELD_NUM = "番号"
    FIELD_SERIES = "系列"

    def get_nfo_files_generator(self, directories):
        """使用生成器获取所有NFO文件路径"""
        for directory in directories:
            if os.path.exists(directory):
                for root, _, files in os.walk(directory):
                    for file in files:
                        if file.lower().endswith(".nfo"):
                            yield os.path.join(root, file)

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

            if field == self.FIELD_NUM:
                # 优先级1: <num>标签
                num_elem = tree.find("num")
                if num_elem is not None and num_elem.text:
                    return num_elem.text.strip(), nfo_file

                # 优先级2: 标题字段
                for tag_name in ["title", "originaltitle", "sorttitle"]:
                    elem = tree.find(tag_name)
                    if elem is not None and elem.text:
                        code = NfoFile.extract_code(elem.text)
                        if code:
                            return code, nfo_file

                # 优先级3: 文件名
                filename = os.path.basename(nfo_file)
                code = NfoFile.extract_code(filename)
                if code:
                    return code, nfo_file

            elif field == self.FIELD_SERIES:
                series_elem = tree.find("series")
                if series_elem is not None and series_elem.text:
                    return series_elem.text.strip(), nfo_file

            # 未找到任何匹配
            return None, nfo_file

        except ET.ParseError as e:
            print(f"XML解析错误 {nfo_file}: {e}")
        except Exception as e:
            print(f"处理文件错误 {nfo_file}: {e}")

        return None, nfo_file


class NfoDuplicateOperations:
    def __init__(self, ui_instance):
        self.ui = ui_instance
        self.result_lock = Lock()
        self.progress_lock = Lock()
        self.processed_files = 0
        self.batch_size = 1000  # 固定批处理大小

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
            if index >= 0:  # 替换模式
                if index < len(self.ui.selected_directories):
                    self.ui.selected_directories[index] = directory
                    self.ui.update_directory_display()
            else:  # 新增模式
                self.ui.add_directory(directory)

    def find_duplicates(self):
        """查找重复项的核心方法"""
        if not self.ui.selected_directories:
            QtWidgets.QMessageBox.warning(self.ui, "错误", "请先选择至少一个目录！")
            return

        self.ui.start_button.setEnabled(False)
        self.ui.start_button.setText("正在查找...")
        self.processed_files = 0

        selected_field = self.ui.field_spinner.get_current_value()
        self.ui.progress_bar.setValue(0)
        self.ui.progress_bar.setFormat("处理中: %p%")
        self.ui.result_stats_label.setText("")

        # 使用生成器获取文件列表
        nfo_files = list(
            self.ui.logic.get_nfo_files_generator(self.ui.selected_directories)
        )
        total_files = len(nfo_files)
        self.ui.progress_bar.setMaximum(total_files)

        duplicates = {}

        # 创建一个定时器用于更新UI，避免从工作线程直接更新UI
        self.update_timer = QtCore.QTimer()
        self.update_timer.setInterval(100)  # 100毫秒更新一次
        self.update_timer.timeout.connect(self._update_progress_ui)
        self.update_timer.start()

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
                        for key, values in batch_results.items():
                            if key in duplicates:
                                duplicates[key].extend(values)
                            else:
                                duplicates[key] = values
                except Exception as e:
                    print(f"处理批次时出错: {str(e)}")

        # 停止定时器
        self.update_timer.stop()

        # 确保进度条显示100%
        self.ui.progress_bar.setValue(total_files)

        # 过滤掉没有重复的项
        filtered_duplicates = {
            key: paths for key, paths in duplicates.items() if len(paths) > 1
        }

        self.display_duplicates(filtered_duplicates)
        self.ui.start_button.setEnabled(True)
        self.ui.start_button.setText("开始查重")

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
            if result[0]:  # 如果找到了字段值
                field_value = result[0]
                if field_value in batch_results:
                    batch_results[field_value].append(result[1])
                else:
                    batch_results[field_value] = [result[1]]

            local_processed += 1

        # 批量更新进度，减少锁竞争
        with self.progress_lock:
            self.processed_files += local_processed

        return batch_results

    def display_duplicates(self, duplicates):
        """显示重复项结果"""
        # 清空现有结果
        self.ui.result_list.clear()

        # 排序重复项，按重复项数量从多到少排序
        sorted_duplicates = sorted(
            duplicates.items(), key=lambda x: len(x[1]), reverse=True
        )

        # 计算找到的重复组数量
        group_count = 0
        total_files = 0

        # 第一个选择的目录（用于突出显示）
        first_directory = (
            self.ui.selected_directories[0] if self.ui.selected_directories else None
        )

        # 遍历所有重复项
        for field_value, paths in sorted_duplicates:
            # 仅处理确实有重复的项（路径数量大于1）
            if len(paths) <= 1:
                continue

            group_count += 1
            total_files += len(paths)

            # 对路径进行排序，优先显示第一个文件夹内的文件
            sorted_paths = sorted(
                paths,
                key=lambda x: (
                    0 if first_directory and x.startswith(first_directory) else 1,
                    x,
                ),
            )

            # 创建根项（第一行：番号+第一个文件）
            root_item = QtWidgets.QTreeWidgetItem(self.ui.result_list)
            root_item.setText(0, f"{field_value} ({len(paths)}个文件)")
            root_item.setText(1, sorted_paths[0] if sorted_paths else "")
            root_item.setFont(0, QtGui.QFont("", weight=QtGui.QFont.Bold))

            # 设置第一行背景色 - 使用theme中的颜色
            root_item.setBackground(0, QtGui.QColor(self.ui.theme.colors["group_bg"]))
            root_item.setBackground(1, QtGui.QColor(self.ui.theme.colors["item1_bg"]))

            # 存储数据用于双击事件
            root_item.setData(0, QtCore.Qt.UserRole, {"is_group": True, "paths": paths})
            root_item.setData(
                1,
                QtCore.Qt.UserRole,
                {"is_file": True, "path": sorted_paths[0] if sorted_paths else ""},
            )

            # 添加剩余项（从第二个开始）
            use_alt_bg = True  # 交替背景色标志
            for i, path in enumerate(sorted_paths[1:], 1):
                child_item = QtWidgets.QTreeWidgetItem(root_item)
                child_item.setText(1, path)  # 只设置第二列

                # 交替背景色 - 使用theme中的颜色
                bg_color = (
                    QtGui.QColor(self.ui.theme.colors["item2_bg"])
                    if use_alt_bg
                    else QtGui.QColor(self.ui.theme.colors["row_alt"])
                )
                child_item.setBackground(1, bg_color)
                use_alt_bg = not use_alt_bg  # 切换标志

                # 如果是第一个文件夹的文件，添加前景色高亮
                if first_directory and path.startswith(first_directory):
                    child_item.setForeground(
                        1, QtGui.QColor(self.ui.theme.colors["secondary"])
                    )

                # 储存路径信息用于双击处理
                child_item.setData(
                    1, QtCore.Qt.UserRole, {"is_file": True, "path": path}
                )

            # 默认展开所有组
            root_item.setExpanded(True)

        # 如果没有找到重复项，显示信息
        if group_count == 0:
            QtWidgets.QMessageBox.information(self.ui, "结果", "未找到重复项。")
            self.ui.result_stats_label.setText("")
        else:
            # 更新结果统计
            self.ui.result_stats_label.setText(
                f"找到 {group_count} 组重复项，共 {total_files} 个文件"
            )

        # 重置进度条文本
        self.ui.progress_bar.setFormat(
            f"完成：找到 {group_count} 组重复项" if group_count > 0 else "完成"
        )

    def open_folder(self, item, column):
        """处理项目双击事件，打开对应文件夹"""
        # 获取项的用户数据
        data = item.data(column, QtCore.Qt.UserRole)
        if not data:
            return

        if column == 0 and data.get("is_group"):
            # 点击番号列，打开所有相关文件夹
            self._open_group_folders(data.get("paths", []))
        elif data.get("is_file"):
            # 点击文件路径，只打开该文件所在文件夹
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

        # 提取所有不同的文件夹路径
        unique_folders = set()
        for path in paths:
            folder = os.path.dirname(path)
            unique_folders.add(folder)

        # 如果文件夹过多，询问用户是否继续
        if len(unique_folders) > 5:
            reply = QtWidgets.QMessageBox.question(
                self.ui,
                "确认",
                f"将要打开 {len(unique_folders)} 个文件夹，是否继续？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if reply == QtWidgets.QMessageBox.No:
                return

        # 打开所有文件夹
        for folder in unique_folders:
            self._open_single_folder(folder)

    def clear_results_on_change(self):
        """当选择器变化时清空结果"""
        self.ui.result_list.clear()
        self.ui.result_stats_label.setText("")


class NfoDuplicateFinder(QtWidgets.QWidget):
    def __init__(self, initial_directory=None):
        super().__init__()
        self.theme = AppTheme()  # 使用集中管理的主题
        self.logic = NfoDuplicateLogic()
        self.selected_directories = []
        self.dir_buttons = []
        self.init_ui()
        # 在初始化UI后创建操作类实例
        self.operations = NfoDuplicateOperations(self)
        # 在operations实例化后应用需要使用operations的样式和连接
        self.connect_signals()
        # 加载目录
        self.load_directories()
        if initial_directory and os.path.exists(initial_directory):
            self.add_directory(initial_directory)

    def init_ui(self):
        # 初始化UI组件，但不连接信号
        self.setWindowTitle("大锤 NFO查重工具 v9.6.0")
        self.setGeometry(100, 100, 800, 600)
        self.setMinimumSize(600, 400)

        # 加载窗口图标
        self._load_app_icon()

        # 主布局
        main_layout = QtWidgets.QVBoxLayout()
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(8, 8, 8, 8)

        # 顶部区域布局
        top_container = QtWidgets.QWidget()
        top_layout = QtWidgets.QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(8)

        # 左侧九宫格布局初始化...
        self.grid_widget = QtWidgets.QWidget()
        self.grid_layout = QtWidgets.QGridLayout()
        self.grid_layout.setSpacing(1)
        self.grid_layout.setContentsMargins(1, 1, 1, 1)

        # 设置列和行等宽等高
        for i in range(3):
            self.grid_layout.setColumnStretch(i, 1)
            self.grid_layout.setRowStretch(i, 1)

        # 初始化按钮
        for i in range(9):
            btn = DirectoryButton()
            self.dir_buttons.append(btn)
            self.grid_layout.addWidget(btn, i // 3, i % 3)

        self.grid_widget.setLayout(self.grid_layout)

        # 右侧按钮区域
        right_container = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout()
        right_layout.setContentsMargins(0, 1, 0, 1)
        right_layout.setSpacing(1)

        # 选择目录按钮
        self.select_dir_button = QtWidgets.QPushButton("选择目录")
        self.select_dir_button.setFixedSize(120, 40)

        # 创建Spinner控件
        self.field_spinner = CustomSpinner()
        self.field_spinner.setFixedSize(120, 40)

        # 查找按钮
        self.start_button = QtWidgets.QPushButton("开始查重")
        self.start_button.setFixedSize(120, 40)

        # 添加到右侧布局
        right_layout.addWidget(self.select_dir_button, alignment=Qt.AlignTop)
        right_layout.addStretch(1)
        right_layout.addWidget(self.field_spinner, alignment=Qt.AlignVCenter)
        right_layout.addStretch(1)
        right_layout.addWidget(self.start_button, alignment=Qt.AlignBottom)

        right_container.setLayout(right_layout)

        # 添加到顶部布局
        top_layout.addWidget(self.grid_widget, 4)
        top_layout.addWidget(right_container, 1)
        top_container.setLayout(top_layout)

        # 添加提示文本标签
        self.tip_label = QtWidgets.QLabel(
            "使用说明：九宫格内已选目录，左键重选，右键删除"
        )

        # 结果列表
        self.result_list = QtWidgets.QTreeWidget()
        self.result_list.setColumnCount(2)
        self.result_list.setHeaderLabels(["重复番号/系列", "文件路径"])
        self.result_list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.result_list.header().setStretchLastSection(True)
        self.result_list.setIndentation(20)
        self.result_list.setAnimated(True)

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

        # 应用样式
        self.apply_basic_styles()

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
        """应用基本样式，不涉及operations"""
        # 设置主窗口样式
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

        # 应用目录按钮样式
        for btn in self.dir_buttons:
            btn.setStyleSheet(self.theme.get_directory_button_style())

        # 提示标签样式
        self.tip_label.setStyleSheet(self.theme.get_label_style(is_tip=True))

        # 表格样式
        self.result_list.setStyleSheet(self.theme.get_tree_widget_style())
        self.result_list.setAlternatingRowColors(True)
        self.result_list.setUniformRowHeights(True)
        self.result_list.setColumnWidth(0, 200)  # 设置第一列宽度

        # 结果统计标签样式
        self.result_stats_label.setStyleSheet(self.theme.get_label_style(is_stats=True))

        # 进度条样式
        self.progress_bar.setStyleSheet(self.theme.get_progress_bar_style())

    def connect_signals(self):
        """连接所有信号，需要operations对象已初始化"""
        # 连接目录按钮信号
        for i, btn in enumerate(self.dir_buttons):
            btn.clicked.connect(
                lambda checked, idx=i: self.operations.select_directories(idx)
            )
            btn.rightClicked.connect(lambda idx=i: self.remove_directory(idx))

        # 连接其他信号
        self.select_dir_button.clicked.connect(
            lambda: self.operations.select_directories()
        )
        self.field_spinner.valueChanged.connect(self.operations.clear_results_on_change)
        self.start_button.clicked.connect(self.operations.find_duplicates)
        self.result_list.itemDoubleClicked.connect(
            lambda item, column: self.operations.open_folder(item, column)
        )

    def add_directory(self, directory):
        """添加新目录到网格"""
        if (
            directory not in self.selected_directories
            and len(self.selected_directories) < 9
        ):
            self.selected_directories.append(directory)
            idx = len(self.selected_directories) - 1
            self.dir_buttons[idx].setText(directory)
            self.dir_buttons[idx].setVisible(True)
            self.save_directories()

    def remove_directory(self, index):
        """移除指定位置的目录"""
        if 0 <= index < len(self.selected_directories):
            self.selected_directories.pop(index)
            self.update_directory_display()
            self.save_directories()

    def update_directory_display(self):
        """更新目录显示"""
        # 清空所有按钮的文本
        for btn in self.dir_buttons:
            btn.setText("")

        # 显示现有目录
        for i, directory in enumerate(self.selected_directories):
            self.dir_buttons[i].setText(directory)

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
