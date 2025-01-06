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


class RenameWorker(QThread):
    """Worker thread for handling the rename process"""

    progressUpdated = pyqtSignal(int, int)
    logUpdated = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, directory, actor_mapping, rename_folders):
        super().__init__()
        self.directory = directory
        self.actor_mapping = actor_mapping
        self.rename_folders = rename_folders

    def run(self):
        try:
            self.process_directory(self.directory, self.actor_mapping)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

    def process_directory(self, directory, actor_mapping):
        """Process the directory"""
        subfolder_count = sum([len(dirs) for _, dirs, _ in os.walk(directory)])
        progress_count = 0

        for root, dirs, _ in os.walk(directory):
            for folder in dirs:
                folder_path = os.path.join(root, folder)
                nfo_path = self.find_nfo_file(folder_path)

                if nfo_path is None or os.path.dirname(nfo_path) != folder_path:
                    self.logUpdated.emit(
                        f"跳过文件夹 '{folder_path}'，未找到对应的 .nfo 文件或 .nfo 文件不在当前文件夹。"
                    )
                    continue

                self.logUpdated.emit(f"\n开始处理文件夹: {folder_path}")

                # Modify actor names in NFO file
                actors_modified = self.modify_nfo_actor(nfo_path, actor_mapping)

                # Update progress
                progress_count += 1
                self.progressUpdated.emit(progress_count, subfolder_count)

                self.logUpdated.emit(f"处理进度：{progress_count}/{subfolder_count}")
                if actors_modified["modified"]:
                    self.logUpdated.emit(
                        f"actor 字段已修改为: {', '.join(actors_modified['actors'])}"
                    )
                else:
                    self.logUpdated.emit("actor 字段未做修改")

                # Rename folder if option is enabled
                if self.rename_folders:
                    self.rename_folder_if_needed(
                        folder_path, nfo_path, actors_modified["actors"]
                    )

        self.logUpdated.emit("\n演员信息更新完成！")

    @staticmethod
    def find_nfo_file(folder_path):
        """Find .nfo file in the folder"""
        nfo_files = []
        for root, _, files in os.walk(folder_path):
            for file in files:
                if file.lower().endswith(".nfo"):
                    nfo_files.append(os.path.join(root, file))
        return nfo_files[0] if nfo_files else None

    def modify_nfo_actor(self, nfo_path, actor_mapping):
        """Modify actor field in nfo file"""
        modified = False
        actors = []
        try:
            tree = ET.parse(nfo_path)
            root = tree.getroot()
            actor_elements = root.findall(".//actor")

            if not actor_elements:
                self.logUpdated.emit(f"在文件 '{nfo_path}' 中未找到任何 <actor> 元素。")
                return {"modified": modified, "actors": actors}

            for actor in actor_elements:
                name_element = actor.find("name")
                if name_element is not None and name_element.text:
                    current_actor = name_element.text.strip()
                    if current_actor in actor_mapping:
                        new_actor = actor_mapping[current_actor]
                        if new_actor != current_actor:
                            self.logUpdated.emit(
                                f"将演员 '{current_actor}' 替换为 '{new_actor}'"
                            )
                            name_element.text = new_actor
                            modified = True
                            actors.append(new_actor)
                        else:
                            actors.append(current_actor)
                    else:
                        actors.append(current_actor)
                else:
                    self.logUpdated.emit(
                        f"在文件 '{nfo_path}' 中发现一个 <actor> 元素，但缺少 <name> 标签或内容为空。"
                    )
                    actors.append("未知演员")

            if modified:
                tree.write(nfo_path, encoding="utf-8", xml_declaration=True)
                self.logUpdated.emit(f"已保存修改后的 .nfo 文件: {nfo_path}")

        except Exception as e:
            self.logUpdated.emit(f"修改 nfo 文件 {nfo_path} 时出错: {e}")

        return {"modified": modified, "actors": actors}

    def rename_folder_if_needed(self, folder_path, nfo_path, current_actors):
        """Rename folder if needed"""
        nfo_filename = os.path.splitext(os.path.basename(nfo_path))[0]
        nfo_rating = self.extract_rating(nfo_path)

        if not current_actors:
            current_actors = ["未知演员"]
            self.logUpdated.emit("未找到任何演员信息，使用默认值 '未知演员'。")

        expected_folder_name = self.handle_folder_with_rating(
            current_actors, nfo_filename, nfo_rating
        )
        current_folder_name = os.path.basename(folder_path)

        if current_folder_name != expected_folder_name:
            self.logUpdated.emit(
                f"当前文件夹名称 '{current_folder_name}' 不符合命名规则，准备重命名为 '{expected_folder_name}'"
            )
            if self.replace_folder_name(folder_path, expected_folder_name):
                self.logUpdated.emit(f"成功重命名文件夹为 '{expected_folder_name}'")
            else:
                self.logUpdated.emit(f"重命名文件夹 '{folder_path}' 失败。")
        else:
            self.logUpdated.emit("文件夹名称符合规范，跳过修改。")

    @staticmethod
    def extract_rating(nfo_path):
        """Extract rating from nfo file"""
        try:
            tree = ET.parse(nfo_path)
            root = tree.getroot()
            rating_element = root.find(".//rating")
            if rating_element is not None and rating_element.text:
                return float(rating_element.text.strip())
        except Exception as e:
            print(f"提取 rating 时出错: {e}")
        return 0.0

    @staticmethod
    def handle_folder_with_rating(actors, nfo_filename, rating):
        """Generate folder name according to naming rules"""
        sanitized_actors = [re.sub(r'[\\/:*?"<>|]', "_", actor) for actor in actors]
        actors_str = ",".join(sanitized_actors)
        sanitized_nfo = re.sub(r'[\\/:*?"<>|]', "_", nfo_filename)
        return f"{actors_str} {sanitized_nfo} {rating:.1f}"

    @staticmethod
    def replace_folder_name(folder_path, new_name):
        """Rename folder"""
        parent_path, _ = os.path.split(folder_path)
        new_path = os.path.join(parent_path, new_name)
        try:
            os.rename(folder_path, new_path)
            return True
        except OSError as e:
            print(f"重命名文件夹时出错: {folder_path}")
            print(f"错误信息: {str(e)}")
            return False


class RenameToolGUI(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        # 启用高DPI支持
        if hasattr(Qt, "AA_EnableHighDpiScaling"):
            QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        if hasattr(Qt, "AA_UseHighDpiPixmaps"):
            QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        self.init_ui()

    def init_ui(self):
        """Initialize the UI"""
        self.setWindowTitle("大锤 批量改名工具 v9.5.9")
        self.setMinimumSize(900, 800)

        # 设置窗口样式
        self.setStyleSheet(
            """
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
        """
        )

        # Center the window
        screen_geometry = QApplication.desktop().availableGeometry()
        x = (screen_geometry.width() - self.width()) // 2
        y = (screen_geometry.height() - self.height()) // 2
        self.move(x, y)

        # Set icon with high DPI support
        try:
            if getattr(sys, "frozen", False):
                application_path = sys._MEIPASS
            else:
                application_path = os.path.dirname(os.path.abspath(__file__))
            icon_path = os.path.join(application_path, "chuizi.ico")
            if os.path.exists(icon_path):
                icon = QIcon(icon_path)
                # 为高DPI设置多个尺寸
                icon.addFile(icon_path, QSize(16, 16))
                icon.addFile(icon_path, QSize(32, 32))
                icon.addFile(icon_path, QSize(64, 64))
                icon.addFile(icon_path, QSize(128, 128))
                self.setWindowIcon(icon)
        except Exception as e:
            print(f"图标设置失败: {str(e)}")

        # Create central widget with shadow effect
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        # Path selection area
        path_container = QWidget()
        path_container.setObjectName("pathContainer")
        path_container.setStyleSheet(
            """
            QWidget#pathContainer {
                background-color: white;
                border-radius: 8px;
                padding: 10px;
            }
        """
        )
        path_layout = QHBoxLayout(path_container)
        path_layout.setContentsMargins(10, 10, 10, 10)

        path_label = QLabel("工作目录：")
        self.path_entry = QLineEdit()
        self.path_entry.setText(os.path.dirname(os.path.abspath(__file__)))
        browse_btn = QPushButton("浏览")
        browse_btn.setProperty("class", "secondary")
        browse_btn.clicked.connect(self.browse_folder)

        path_layout.addWidget(path_label)
        path_layout.addWidget(self.path_entry)
        path_layout.addWidget(browse_btn)
        main_layout.addWidget(path_container)

        # Options layout
        options_container = QWidget()
        options_container.setObjectName("optionsContainer")
        options_container.setStyleSheet(
            """
            QWidget#optionsContainer {
                background-color: white;
                border-radius: 8px;
                padding: 10px;
            }
        """
        )
        options_layout = QHBoxLayout(options_container)
        self.rename_folders_cb = QCheckBox("同时重命名文件夹")
        options_layout.addWidget(self.rename_folders_cb)
        options_layout.addStretch()
        main_layout.addWidget(options_container)

        # Mapping file path display
        self.mapping_label = QLabel()
        self.mapping_label.setStyleSheet(
            """
            color: #2196F3;
            background-color: white;
            padding: 10px;
            border-radius: 8px;
        """
        )
        self.update_mapping_path()
        main_layout.addWidget(self.mapping_label)

        # Log area
        log_container = QWidget()
        log_container.setObjectName("logContainer")
        log_container.setStyleSheet(
            """
            QWidget#logContainer {
                background-color: white;
                border-radius: 8px;
                padding: 10px;
            }
        """
        )
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(10, 10, 10, 10)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        main_layout.addWidget(log_container, stretch=1)

        # Bottom container
        bottom_container = QWidget()
        bottom_container.setObjectName("bottomContainer")
        bottom_container.setStyleSheet(
            """
            QWidget#bottomContainer {
                background-color: white;
                border-radius: 8px;
                padding: 10px;
            }
        """
        )
        bottom_layout = QVBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(10, 10, 10, 10)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setAlignment(Qt.AlignCenter)
        self.progress_bar.setMinimumHeight(20)
        bottom_layout.addWidget(self.progress_bar)

        # Execute button
        execute_btn = QPushButton("执行")
        execute_btn.setMinimumHeight(45)
        execute_btn.setStyleSheet(
            """
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
        """
        )
        execute_btn.clicked.connect(self.execute_rename)
        bottom_layout.addWidget(execute_btn)

        main_layout.addWidget(bottom_container)

        try:
            self.actor_mapping = self.load_actor_mapping(self.mapping_file_path)
        except Exception as e:
            self.log_text.append(f"加载映射文件出错: {str(e)}")

    def update_mapping_path(self):
        """Update mapping file path display"""
        try:
            if getattr(sys, "frozen", False):
                base_path = sys._MEIPASS
            else:
                base_path = os.path.dirname(os.path.abspath(__file__))

            external_mapping_file = os.path.join(
                os.path.dirname(
                    sys.executable if getattr(sys, "frozen", False) else __file__
                ),
                "mapping_actor.xml",
            )
            if os.path.exists(external_mapping_file):
                self.mapping_file_path = external_mapping_file
                self.mapping_label.setText(f"外部配置: {external_mapping_file}")
                return

            internal_mapping_file = os.path.join(base_path, "mapping_actor.xml")
            if os.path.exists(internal_mapping_file):
                self.mapping_file_path = internal_mapping_file
                self.mapping_label.setText(f"内置配置: {internal_mapping_file}")
                return

            self.mapping_label.setText("未找到配置文件")
            self.mapping_file_path = None
        except Exception as e:
            print(f"获取配置文件路径时出错: {str(e)}")
            self.mapping_label.setText("配置文件路径错误")
            self.mapping_file_path = None

    def browse_folder(self):
        """Open folder selection dialog"""
        folder = QFileDialog.getExistingDirectory(self, "选择工作目录")
        if folder:
            self.path_entry.setText(folder)

    def execute_rename(self):
        """Execute the rename process"""
        if hasattr(self, "worker") and self.worker.isRunning():
            return

        directory = self.path_entry.text()
        if not os.path.isdir(directory):
            QMessageBox.critical(
                self, "错误", f"路径 '{directory}' 不是一个有效的目录。"
            )
            return

        if not self.mapping_file_path or not os.path.exists(self.mapping_file_path):
            QMessageBox.critical(self, "错误", "未找到有效的配置文件")
            return

        try:
            self.log_text.clear()
            self.progress_bar.setValue(0)
            self.log_text.append("开始处理...")

            self.worker = RenameWorker(
                directory,
                self.actor_mapping,  # 使用已加载的映射
                self.rename_folders_cb.isChecked(),
            )
            self.worker.progressUpdated.connect(self.update_progress)
            self.worker.logUpdated.connect(self.update_log)
            self.worker.finished.connect(
                lambda: self.log_text.append("文件夹名称更新完成！")
            )
            self.worker.error.connect(self.handle_error)
            self.worker.start()

        except Exception as e:
            QMessageBox.critical(self, "错误", f"处理过程中出现错误: {str(e)}")
            self.log_text.append("处理出错")

    def load_actor_mapping(self, mapping_file):
        """Load actor mapping from XML file"""
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

            self.log_text.append(f"成功加载 {len(mapping)} 个演员映射关系。")

        except Exception as e:
            self.log_text.append(f"加载映射文件出错: {str(e)}")
            raise

        return mapping

    def update_progress(self, current, total):
        """Update progress bar"""
        progress = int((current / total) * 100)
        self.progress_bar.setValue(progress)
        self.progress_bar.setFormat(f"{current}/{total} ({progress}%)")  # 简化显示文本

    def update_log(self, message):
        """Update log text"""
        self.log_text.append(message)
        # Ensure the latest log message is visible
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum()
        )

    def handle_error(self, error_message):
        """Handle worker thread errors"""
        QMessageBox.critical(self, "错误", f"处理过程中出现错误: {error_message}")
        self.status_label.setText("处理出错")


def start_rename_process(directory=None):
    """Start the rename process"""
    app = QApplication(sys.argv)
    window = RenameToolGUI()

    if directory:
        window.path_entry.setText(directory)

    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1:
            directory_path = sys.argv[1]
            if not os.path.isdir(directory_path):
                QMessageBox.critical(
                    None, "错误", f"路径 '{directory_path}' 不是一个有效的目录。"
                )
                sys.exit(1)
            start_rename_process(directory_path)
        else:
            start_rename_process()
    except Exception as e:
        QMessageBox.critical(None, "错误", f"程序运行出错：{str(e)}")
        sys.exit(1)
