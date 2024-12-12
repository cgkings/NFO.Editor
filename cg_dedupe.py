import os
import re
import sys
import json
import subprocess
from PyQt5 import QtWidgets, QtGui, QtCore
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET
from multiprocessing import cpu_count, freeze_support
import signal
from threading import Lock


class NfoDuplicateFinder(QtWidgets.QWidget):
    def __init__(self, initial_directory=None):
        super().__init__()
        self.logic = NfoDuplicateLogic()
        self.operations = NfoDuplicateOperations(self)
        self.selected_directories = []
        self.init_ui()
        self.load_directories()
        if initial_directory and os.path.exists(initial_directory):
            if initial_directory not in self.selected_directories:
                self.selected_directories.append(initial_directory)
                self.save_directories()
            self.update_selected_directories_display()

    def init_ui(self):
        self.setWindowTitle("NFO重复查找工具  v9.3.5")
        self.setGeometry(100, 100, 800, 600)

        try:
            if getattr(sys, "frozen", False):
                application_path = sys._MEIPASS
            else:
                application_path = os.path.dirname(os.path.abspath(__file__))

            icon_path = os.path.join(application_path, "chuizi.ico")
            if os.path.exists(icon_path):
                self.setWindowIcon(QtGui.QIcon(icon_path))
        except Exception as e:
            print(f"图标设置失败: {str(e)}")

        layout = QtWidgets.QVBoxLayout()

        # 选择目录按钮
        self.select_dir_button = QtWidgets.QPushButton("选择目录（可多选）")
        self.select_dir_button.clicked.connect(self.operations.select_directories)
        layout.addWidget(self.select_dir_button)

        # 取消选择按钮
        self.remove_dir_button = QtWidgets.QPushButton("取消选择的目录")
        self.remove_dir_button.clicked.connect(
            self.operations.remove_selected_directory
        )
        layout.addWidget(self.remove_dir_button)

        # 显示选定的目录
        self.dir_label = QtWidgets.QLabel("未选择目录")
        self.dir_label.setWordWrap(True)
        self.dir_label.setAlignment(QtCore.Qt.AlignTop)
        self.dir_scroll = QtWidgets.QScrollArea()
        self.dir_scroll.setWidget(self.dir_label)
        self.dir_scroll.setWidgetResizable(True)
        self.dir_scroll.setFixedHeight(100)
        layout.addWidget(self.dir_scroll)

        # 查重依据
        self.criteria_label = QtWidgets.QLabel("请选择查重依据")
        layout.addWidget(self.criteria_label)

        # 字段选择
        self.field_combo_box = QtWidgets.QComboBox()
        self.field_combo_box.addItems(["番号", "系列"])
        self.field_combo_box.currentIndexChanged.connect(
            self.operations.clear_results_on_change
        )
        layout.addWidget(self.field_combo_box)

        # 开始查找按钮
        self.start_button = QtWidgets.QPushButton("查找重复项")
        self.start_button.clicked.connect(self.operations.find_duplicates)
        layout.addWidget(self.start_button)

        # 结果列表
        self.result_list = QtWidgets.QTableWidget()
        self.result_list.setColumnCount(2)
        self.result_list.setHorizontalHeaderLabels(["重复内容", "NFO路径"])
        self.result_list.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.result_list.horizontalHeader().setStretchLastSection(True)
        self.result_list.itemDoubleClicked.connect(self.operations.open_folder)
        layout.addWidget(self.result_list)

        # 进度条
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.setLayout(layout)

    def load_directories(self):
        settings = QtCore.QSettings("NfoDuplicateFinder", "Directories")
        saved_directories = settings.value("directories", [])
        self.selected_directories = (
            [d for d in saved_directories if os.path.exists(d)]
            if saved_directories
            else []
        )
        self.update_selected_directories_display()

    def update_selected_directories_display(self):
        if self.selected_directories:
            self.dir_label.setText("\n".join(self.selected_directories))
        else:
            self.dir_label.setText("未选择目录")

    def closeEvent(self, event):
        settings = QtCore.QSettings("NfoDuplicateFinder", "Directories")
        settings.setValue("directories", self.selected_directories)
        event.accept()


class NfoDuplicateOperations:
    def __init__(self, ui_instance):
        self.ui = ui_instance
        self.result_lock = Lock()
        self.progress_lock = Lock()
        self.processed_files = 0
        self.batch_size = 1000  # 固定批处理大小

    def select_directories(self):
        last_dir = (
            self.ui.selected_directories[-1] if self.ui.selected_directories else "."
        )
        directory = QtWidgets.QFileDialog.getExistingDirectory(
            self.ui,
            "选择目录",
            last_dir,
            QtWidgets.QFileDialog.ShowDirsOnly
            | QtWidgets.QFileDialog.DontUseNativeDialog,
        )
        if directory and os.path.exists(directory):
            if directory not in self.ui.selected_directories:
                self.ui.selected_directories.append(directory)
                self.ui.update_selected_directories_display()

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

        selected_field = self.ui.field_combo_box.currentText()
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
        self.ui.result_list.setRowCount(0)
        for field_value, paths in duplicates.items():
            for path in paths:
                row_position = self.ui.result_list.rowCount()
                self.ui.result_list.insertRow(row_position)
                self.ui.result_list.setItem(
                    row_position, 0, QtWidgets.QTableWidgetItem(field_value)
                )
                self.ui.result_list.setItem(
                    row_position, 1, QtWidgets.QTableWidgetItem(path)
                )

        if self.ui.result_list.rowCount() == 0:
            QtWidgets.QMessageBox.information(self.ui, "结果", "未找到重复项。")

    def open_folder(self, item):
        if item.column() == 1:  # 只处理路径列
            path = os.path.dirname(item.text())
            try:
                if sys.platform == "win32":
                    os.startfile(path)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
            except Exception as e:
                QtWidgets.QMessageBox.warning(
                    self.ui, "错误", f"无法打开文件夹: {str(e)}"
                )

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

    app = QtWidgets.QApplication(sys.argv)
    window = NfoDuplicateFinder(initial_directory=initial_directory)
    window.show()
    sys.exit(app.exec_())
