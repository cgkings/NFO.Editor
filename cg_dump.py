import os
import re
import sys
import subprocess
from PyQt5 import QtWidgets, QtGui, QtCore
from multiprocessing import Pool, cpu_count, freeze_support
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
        self.setWindowTitle("NFO重复查找工具  v1.0.0")
        self.setGeometry(100, 100, 800, 600)
        self.setWindowIcon(QtGui.QIcon("chuizi.ico"))

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
            self.ui.selected_directories.append(directories)
            self.ui.last_directory = directories
            self.ui.update_selected_directories_display()

    def find_duplicates(self):
        if not self.ui.selected_directories:
            QtWidgets.QMessageBox.warning(self.ui, "错误", "请先选择至少一个目录！")
            return

        selected_field = self.ui.field_combo_box.currentText()
        self.ui.progress_bar.setValue(0)
        duplicates = self.ui.logic.get_duplicates(
            self.ui.selected_directories, selected_field, self.ui.progress_bar
        )
        self.display_duplicates(duplicates)

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

    def remove_selected_directory(self):
        directories, ok = QtWidgets.QInputDialog.getItem(
            self.ui,
            "取消选择目录",
            "请选择要取消的目录:",
            self.ui.selected_directories,
            0,
            False,
        )
        if ok and directories:
            self.ui.selected_directories.remove(directories)
            self.ui.update_selected_directories_display()

    def clear_results_on_change(self):
        # 当更换字段选择时，清空结果列表区内容
        self.ui.result_list.setRowCount(0)


class NfoDuplicateLogic:
    def get_duplicates(self, directories, field, progress_bar):
        # 获取所有的 .nfo 文件路径
        nfo_files = []
        for directory in directories:
            for root, _, files in os.walk(directory):
                for file in files:
                    if file.endswith(".nfo"):
                        nfo_files.append(os.path.join(root, file))

        total_files = len(nfo_files)
        progress_bar.setMaximum(total_files)

        # 使用多进程池来并行处理文件内容的读取和匹配
        with Pool(processes=cpu_count()) as pool:
            try:
                results = []
                for i, result in enumerate(
                    pool.imap(
                        self.process_nfo_file,
                        [(nfo_file, field) for nfo_file in nfo_files],
                    ),
                    1,
                ):
                    results.append(result)
                    progress_bar.setValue(i)
            except KeyboardInterrupt:
                pool.terminate()
                pool.join()
                raise

        # 收集重复项
        field_dict = {}
        for field_value, nfo_file in results:
            if field_value:
                if field_value in field_dict:
                    field_dict[field_value].append(nfo_file)
                else:
                    field_dict[field_value] = [nfo_file]

        duplicates = {key: value for key, value in field_dict.items() if len(value) > 1}
        return duplicates

    def process_nfo_file(self, args):
        nfo_file, field = args
        try:
            with open(nfo_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                if field == "番号":
                    match = re.search(r"<num>(.*?)</num>", content)
                elif field == "系列":
                    match = re.search(r"<series>(.*?)</series>", content)
                else:
                    match = None

                if match:
                    field_value = match.group(1).strip()
                    return field_value, nfo_file
                else:
                    return None, nfo_file
        except Exception as e:
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
