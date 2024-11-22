import os
import re
import sys
import subprocess
from PyQt5 import QtWidgets, QtGui, QtCore
from concurrent.futures import ThreadPoolExecutor
import xml.etree.ElementTree as ET
from multiprocessing import cpu_count, freeze_support
import signal


class NfoDuplicateFinder(QtWidgets.QWidget):
    def __init__(self, initial_directory=None):
        super().__init__()
        self.logic = NfoDuplicateLogic()
        self.operations = NfoDuplicateOperations(self)
        self.init_ui()
        self.selected_directories = []
        self.last_directory = None
        self.load_last_directory()
        if initial_directory:
            self.selected_directories.append(initial_directory)
            self.update_selected_directories_display()

    def init_ui(self):
        # 设置窗口
        self.setWindowTitle("NFO重复查找工具  v9.2.6")
        self.setGeometry(100, 100, 800, 600)

        try:
            # 获取应用程序路径
            if getattr(sys, "frozen", False):
                # 如果是打包后的exe
                application_path = sys._MEIPASS
            else:
                # 如果是直接运行的py脚本
                application_path = os.path.dirname(os.path.abspath(__file__))

            # 图标路径
            icon_path = os.path.join(application_path, "chuizi.ico")

            if os.path.exists(icon_path):
                # 设置窗口图标
                self.setWindowIcon(QtGui.QIcon(icon_path))
            else:
                print(f"图标文件未找到: {icon_path}")

        except Exception as e:
            print(f"图标设置失败: {str(e)}")

        # 布局
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
        self.dir_label.setFixedHeight(100)
        layout.addWidget(self.dir_label)

        # 请选择查重依据文本
        self.criteria_label = QtWidgets.QLabel("请选择查重依据")
        layout.addWidget(self.criteria_label)

        # 字段选择下拉菜单
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

        # 结果列表（两列）
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

    def load_last_directory(self):
        # 尝试从环境变量或上次的打开记录中加载最后的目录
        self.last_directory = QtCore.QSettings(
            "NfoDuplicateFinder", "last_directory"
        ).value("last_directory", "")

    def save_last_directory(self):
        if self.last_directory:
            QtCore.QSettings("NfoDuplicateFinder", "last_directory").setValue(
                "last_directory", self.last_directory
            )

    def update_selected_directories_display(self):
        if self.selected_directories:
            self.dir_label.setText("\n".join(self.selected_directories))
        else:
            self.dir_label.setText("未选择目录")

    def closeEvent(self, event):
        self.save_last_directory()
        event.accept()


class NfoDuplicateOperations:
    def __init__(self, ui_instance):
        self.ui = ui_instance

    def update_directories(self, directory, remove=False):
        if remove:
            if directory in self.ui.selected_directories:
                self.ui.selected_directories.remove(directory)
        else:
            self.ui.selected_directories.append(directory)
            self.ui.last_directory = directory
        self.ui.update_selected_directories_display()

    def select_directories(self):
        directories = QtWidgets.QFileDialog.getExistingDirectory(
            self.ui,
            "选择目录（可多选）",
            self.ui.last_directory or ".",
            options=QtWidgets.QFileDialog.DontUseNativeDialog
            | QtWidgets.QFileDialog.ShowDirsOnly
            | QtWidgets.QFileDialog.ReadOnly,
        )
        if directories:
            self.update_directories(directories)

    def remove_selected_directory(self):
        directory, ok = QtWidgets.QInputDialog.getItem(
            self.ui,
            "取消选择目录",
            "请选择要取消的目录:",
            self.ui.selected_directories,
            0,
            False,
        )
        if ok and directory:
            self.update_directories(directory, remove=True)

    def find_duplicates(self):
        if not self.ui.selected_directories:
            QtWidgets.QMessageBox.warning(self.ui, "错误", "请先选择至少一个目录！")
            return

        self.ui.start_button.setEnabled(False)
        self.ui.start_button.setText("正在查找...")

        selected_field = self.ui.field_combo_box.currentText()
        self.ui.progress_bar.setValue(0)

        nfo_files = self.ui.logic.get_all_nfo_files(self.ui.selected_directories)
        total_files = len(nfo_files)
        self.ui.progress_bar.setMaximum(total_files)

        duplicates = {}
        with ThreadPoolExecutor(max_workers=cpu_count()) as executor:
            futures = [
                executor.submit(
                    self.ui.logic.process_nfo_file, (nfo_file, selected_field)
                )
                for nfo_file in nfo_files
            ]
            for i, future in enumerate(futures, 1):
                result = future.result()
                if result[0]:
                    if result[0] in duplicates:
                        duplicates[result[0]].append(result[1])
                    else:
                        duplicates[result[0]] = [result[1]]
                self.ui.progress_bar.setValue(i)

        # 过滤掉没有重复项的记录
        filtered_duplicates = {
            key: value for key, value in duplicates.items() if len(value) > 1
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
        if item.column() == 1:
            path = os.path.dirname(item.text())
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])

    def clear_results_on_change(self):
        # 当更换字段选择时，清空结果列表区内容
        self.ui.result_list.setRowCount(0)


class NfoDuplicateLogic:
    def get_all_nfo_files(self, directories):
        # 获取所有的 .nfo 文件路径
        nfo_files = []
        for directory in directories:
            for root, _, files in os.walk(directory):
                for file in files:
                    if file.endswith(".nfo"):
                        nfo_files.append(os.path.join(root, file))
        return nfo_files

    def process_nfo_file(self, args):
        nfo_file, field = args
        try:
            tree = ET.parse(nfo_file)
            root = tree.getroot()
            if field == "番号":
                field_value = root.find("num")
            elif field == "系列":
                field_value = root.find("series")
            else:
                field_value = None

            if field_value is not None and field_value.text:
                return field_value.text.strip(), nfo_file
            else:
                return None, nfo_file
        except ET.ParseError as e:
            print(f"Error processing file {nfo_file}: {e}")
            return None, nfo_file


if __name__ == "__main__":
    freeze_support()  # 为 Windows 系统上支持安全的多进程处理
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # 使得程序可以正常响应 Ctrl+C

    # 获取启动参数
    initial_directory = None
    if len(sys.argv) > 1:
        initial_directory = sys.argv[1]

    app = QtWidgets.QApplication(sys.argv)
    window = NfoDuplicateFinder(initial_directory=initial_directory)
    window.selected_directories = []  # 程序启动时清空选择目录
    window.update_selected_directories_display()
    window.show()
    sys.exit(app.exec_())
