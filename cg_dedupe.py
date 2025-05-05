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


class CustomSpinner(QtWidgets.QWidget):
    valueChanged = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.initUI()
        self.current_index = 0
        self.options = ["番号", "系列"]

    def initUI(self):
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 文本显示区域
        self.display = QtWidgets.QLineEdit()
        self.display.setReadOnly(True)
        self.display.setText("番号")
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
        button_container.setFixedWidth(24)  # 设置按钮容器宽度

        # 添加到主布局
        layout.addWidget(self.display)
        layout.addWidget(button_container)
        self.setLayout(layout)

        # 连接信号
        self.up_button.clicked.connect(self.previous_item)
        self.down_button.clicked.connect(self.next_item)

        # 设置样式
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
        self.setStyleSheet(
            """
            QPushButton {
                text-align: left;
                padding: 5px 8px;
                background-color: white;
                border: 1px solid #c0c0c0;
                border-radius: 0px;
            }
            QPushButton:hover {
                background-color: #f0f0f0;
                border: 1px solid #2563eb;
            }
        """
        )

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


class NfoDuplicateFinder(QtWidgets.QWidget):
    def __init__(self, initial_directory=None):
        super().__init__()
        self.logic = NfoDuplicateLogic()
        self.operations = NfoDuplicateOperations(self)
        self.selected_directories = []
        self.dir_buttons = []
        self.init_ui()
        self.load_directories()
        if initial_directory and os.path.exists(initial_directory):
            self.add_directory(initial_directory)

    def init_ui(self):
        self.setWindowTitle("大锤 NFO查重工具 v9.6.0")
        self.setGeometry(100, 100, 800, 600)
        self.setMinimumSize(600, 400)

        # 加载窗口图标
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
            btn.clicked.connect(
                lambda checked, idx=i: self.operations.select_directories(idx)
            )
            btn.rightClicked.connect(lambda idx=i: self.remove_directory(idx))
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
        self.tip_label.setStyleSheet(
            """
            QLabel {
                color: #666666;
                padding: 4px;
                font-size: 16px;
            }
        """
        )

        # 结果列表
        self.result_list = QtWidgets.QTreeWidget()
        self.result_list.setColumnCount(2)
        self.result_list.setHeaderLabels(["重复内容", "NFO路径"])
        self.result_list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.result_list.header().setStretchLastSection(True)
        self.result_list.setAlternatingRowColors(True)

        # 进度条
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setValue(0)

        # 添加所有组件到主布局
        main_layout.addWidget(top_container)
        main_layout.addWidget(self.tip_label)  # 添加提示标签
        main_layout.addWidget(self.result_list, 1)
        main_layout.addWidget(self.progress_bar)

        self.setLayout(main_layout)

        # 连接信号
        self.select_dir_button.clicked.connect(
            lambda: self.operations.select_directories()
        )
        self.field_spinner.valueChanged.connect(self.operations.clear_results_on_change)
        self.start_button.clicked.connect(self.operations.find_duplicates)
        self.result_list.itemDoubleClicked.connect(
            lambda item, column: self.operations.open_folder(item, column)
        )

        # 应用样式
        self.apply_styles()

    def apply_styles(self):
        # 设置主窗口背景色
        self.setStyleSheet(
            """
            QWidget {
                background-color: white;
            }
        """
        )

        # 选择目录按钮样式
        self.select_dir_button.setStyleSheet(
            """
            QPushButton {
                background-color: #2563eb;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
            QPushButton:pressed {
                background-color: #1e40af;
            }
        """
        )

        # 开始查找按钮样式
        self.start_button.setStyleSheet(
            """
            QPushButton {
                background-color: #2563eb;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
        """
        )

        # 表格样式
        self.result_list.setStyleSheet(
            """
            QTableWidget {
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                background-color: white;
                gridline-color: #f1f5f9;
            }
            QTableWidget::item {
                padding: 4px;
            }
            QHeaderView::section {
                background-color: #f8fafc;
                padding: 6px;
                border: none;
                font-weight: bold;
            }
        """
        )

        # 进度条样式
        self.progress_bar.setStyleSheet(
            """
            QProgressBar {
                border: none;
                border-radius: 6px;
                background-color: #f1f5f9;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #2563eb;
                border-radius: 6px;
            }
        """
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
        self.save_directories()
        event.accept()


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

    def remove_selected_directory(self):
        if not self.ui.selected_directories:
            QtWidgets.QMessageBox.warning(self.ui, "提示", "没有可以取消的目录")
            return

        directory, ok = QtWidgets.QInputDialog.getItem(
            self.ui,
            "取消选择目录",
            "请选择要取消的目录:",
            self.ui.selected_directories,
            0,
            False,
        )
        if ok and directory:
            self.ui.selected_directories.remove(directory)
            self.ui.update_selected_directories_display()

    def find_duplicates(self):
        if not self.ui.selected_directories:
            QtWidgets.QMessageBox.warning(self.ui, "错误", "请先选择至少一个目录！")
            return

        self.ui.start_button.setEnabled(False)
        self.ui.start_button.setText("正在查找...")
        self.processed_files = 0

        selected_field = self.ui.field_spinner.get_current_value()
        self.ui.progress_bar.setValue(0)

        # 使用生成器获取文件列表
        nfo_files = list(
            self.ui.logic.get_nfo_files_generator(self.ui.selected_directories)
        )
        total_files = len(nfo_files)
        self.ui.progress_bar.setMaximum(total_files)

        duplicates = {}

        def process_batch(file_batch):
            batch_results = {}
            for nfo_file in file_batch:
                result = self.ui.logic.process_nfo_file((nfo_file, selected_field))
                if result[0]:  # 如果找到了字段值
                    field_value = result[0]
                    if field_value in batch_results:
                        batch_results[field_value].append(result[1])
                    else:
                        batch_results[field_value] = [result[1]]

                with self.progress_lock:
                    self.processed_files += 1
                    self.ui.progress_bar.setValue(self.processed_files)

            return batch_results

        # 划分批次
        batches = [
            nfo_files[i : i + self.batch_size]
            for i in range(0, len(nfo_files), self.batch_size)
        ]

        # 使用线程池处理批次
        with ThreadPoolExecutor(max_workers=cpu_count()) as executor:
            future_to_batch = {
                executor.submit(process_batch, batch): batch for batch in batches
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

        # 过滤掉没有重复的项
        filtered_duplicates = {
            key: paths for key, paths in duplicates.items() if len(paths) > 1
        }

        self.display_duplicates(filtered_duplicates)
        self.ui.start_button.setEnabled(True)
        self.ui.start_button.setText("查找重复项")

    def display_duplicates(self, duplicates):
        # 清空现有结果
        self.ui.result_list.clear()

        # 排序重复项，按重复项数量从多到少排序
        sorted_duplicates = sorted(
            duplicates.items(), key=lambda x: len(x[1]), reverse=True
        )

        # 计算找到的重复组数量
        group_count = 0

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

            # 创建组标题项
            group_item = QtWidgets.QTreeWidgetItem()
            group_item.setText(0, f"{field_value} ({len(paths)}个文件)")
            group_item.setFont(0, QtGui.QFont("", weight=QtGui.QFont.Bold))
            group_item.setBackground(0, QtGui.QColor("#e2e8f0"))  # 设置背景色

            # 储存组信息用于双击处理
            group_item.setData(
                0, QtCore.Qt.UserRole, {"is_group": True, "paths": paths}
            )

            # 添加到树控件
            self.ui.result_list.addTopLevelItem(group_item)

            # 对路径进行排序，优先显示第一个文件夹内的文件
            sorted_paths = sorted(
                paths,
                key=lambda x: (
                    0 if first_directory and x.startswith(first_directory) else 1,
                    x,
                ),
            )

            # 添加子项
            for path in sorted_paths:
                child_item = QtWidgets.QTreeWidgetItem()
                child_item.setText(1, path)

                # 如果是第一个文件夹的文件，设置背景色以突出显示
                if first_directory and path.startswith(first_directory):
                    child_item.setBackground(1, QtGui.QColor("#e6f3ff"))  # 浅蓝色背景

                # 储存路径信息用于双击处理
                child_item.setData(
                    1, QtCore.Qt.UserRole, {"is_file": True, "path": path}
                )

                # 添加到组项下
                group_item.addChild(child_item)

            # 默认展开组
            group_item.setExpanded(True)

        # 如果没有找到重复项，显示信息
        if group_count == 0:
            QtWidgets.QMessageBox.information(self.ui, "结果", "未找到重复项。")
        else:
            # 自动调整列宽
            self.ui.result_list.resizeColumnToContents(0)

    def open_folder(self, item, column):
        # 获取项的用户数据
        data = item.data(column, QtCore.Qt.UserRole)
        if not data:
            return

        if data.get("is_group"):
            # 如果是组项，打开该组所有文件夹
            self._open_group_folders(data.get("paths", []))
        elif data.get("is_file"):
            # 如果是文件项，打开单个文件夹
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
        self.ui.result_list.setRowCount(0)


class NfoDuplicateLogic:
    def get_nfo_files_generator(self, directories):
        """使用生成器获取所有NFO文件路径"""
        for directory in directories:
            if os.path.exists(directory):
                for root, _, files in os.walk(directory):
                    for file in files:
                        if file.lower().endswith(".nfo"):
                            yield os.path.join(root, file)

    def process_nfo_file(self, args):
        """处理单个NFO文件，只读取不修改"""
        nfo_file, field = args
        try:
            if not os.path.exists(nfo_file):
                return None, nfo_file

            # 使用read-only模式打开文件
            with open(nfo_file, "r", encoding="utf-8") as f:
                try:
                    tree = ET.parse(f)
                    root = tree.getroot()

                    if field == "番号":
                        field_value = root.find("num")
                    elif field == "系列":
                        field_value = root.find("series")
                    else:
                        field_value = None

                    if field_value is not None and field_value.text:
                        return field_value.text.strip(), nfo_file
                except ET.ParseError as e:
                    print(f"XML解析错误 {nfo_file}: {e}")
                except Exception as e:
                    print(f"处理文件错误 {nfo_file}: {e}")

            return None, nfo_file
        except Exception as e:
            print(f"读取文件错误 {nfo_file}: {e}")
            return None, nfo_file


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

    # Center window on screen
    screen = app.primaryScreen().geometry()
    window_geometry = window.geometry()
    x = (screen.width() - window_geometry.width()) // 2
    y = (screen.height() - window_geometry.height()) // 2
    window.move(x, y)

    window.show()
    sys.exit(app.exec_())
