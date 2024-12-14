import os
import sys
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
from datetime import datetime
from PIL import Image
from PyQt5.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QFileDialog,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QShortcut,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
)
from PyQt5.QtCore import (
    Qt,
    QThread,
    pyqtSignal,
    QSettings,
    QFileSystemWatcher,
)
from PyQt5.QtGui import QIcon, QPixmap, QKeySequence
import subprocess
import winshell

from NFO_Editor_ui import NFOEditorQt


class FileOperationThread(QThread):
    """文件操作线程类"""

    progress = pyqtSignal(int, int)  # 当前进度,总数
    finished = pyqtSignal()  # 完成信号
    error = pyqtSignal(str)  # 错误信号
    status = pyqtSignal(str)  # 状态信息信号

    def __init__(self, operation_type, **kwargs):
        super().__init__()
        self.operation_type = operation_type
        self.kwargs = kwargs
        self.is_running = True
        # 启用拖放
        self.setAcceptDrops(True)

    def run(self):
        if self.operation_type == "move":
            self.move_files()

    def move_files(self):
        try:
            src_paths = self.kwargs.get("src_paths", [])
            dest_path = self.kwargs.get("dest_path")
            total = len(src_paths)

            for i, src_path in enumerate(src_paths, 1):
                if not self.is_running:
                    break

                try:
                    folder_name = os.path.basename(src_path)
                    dest_folder_path = os.path.join(dest_path, folder_name)

                    # 同盘符移动判断逻辑
                    if (
                        os.path.splitdrive(src_path)[0]
                        == os.path.splitdrive(dest_path)[0]
                    ):
                        if os.path.exists(dest_folder_path):
                            # 通过信号发送显示确认对话框的请求
                            # 实际实现需要处理用户响应
                            os.remove(dest_folder_path)
                        os.rename(src_path, dest_folder_path)
                    else:
                        # 跨盘符复制后删除
                        if os.path.exists(dest_folder_path):
                            subprocess.run(
                                f'rd /s /q "{dest_folder_path}"', shell=True, check=True
                            )

                        # 复制文件
                        subprocess.run(
                            f'xcopy "{src_path}" "{dest_folder_path}" /E /I /H /R /Y',
                            shell=True,
                            check=True,
                        )

                        # 删除源文件
                        subprocess.run(f'rd /s /q "{src_path}"', shell=True, check=True)

                    self.progress.emit(i, total)
                    self.status.emit(f"正在处理: {folder_name}")

                except Exception as e:
                    self.error.emit(f"移动文件夹失败: {str(e)}")
                    continue

            self.finished.emit()

        except Exception as e:
            self.error.emit(f"操作过程中发生错误: {str(e)}")

    def stop(self):
        """停止线程"""
        self.is_running = False


class NFOEditorQt5(NFOEditorQt):
    def __init__(self):
        super().__init__()
        # 成员变量初始化
        self.current_file_path = None
        self.folder_path = None
        self.current_target_path = None
        self.nfo_files = []
        self.selected_index_cache = None
        self.move_thread = None
        self.file_watcher = QFileSystemWatcher()

        # 连接信号槽
        self.setup_signals()

        # 恢复上次窗口状态
        self.restore_window_state()

    def setup_signals(self):
        """设置信号槽连接"""
        # 为每个按钮设置处理函数
        buttons = self.findChildren(QPushButton)
        for btn in buttons:
            text = btn.text()
            if text == "选择nfo目录":
                btn.clicked.connect(self.open_folder)
            elif text == "选择整理目录":
                btn.clicked.connect(self.select_target_folder)
            elif text == "🖊":
                btn.clicked.connect(self.open_selected_nfo)
            elif text == "📁":
                btn.clicked.connect(self.open_selected_folder)
            elif text == "⏯":
                btn.clicked.connect(self.open_selected_video)
            elif text == "🔗":
                btn.clicked.connect(self.open_batch_rename_tool)
            elif text == "🔁":
                btn.clicked.connect(self.load_files_in_folder)
            elif text == "=>":
                btn.clicked.connect(self.start_move_thread)

        # 显示图片复选框信号
        self.show_images_checkbox.stateChanged.connect(self.toggle_image_display)

        # 文件树信号
        self.file_tree.itemSelectionChanged.connect(self.on_file_select)
        self.file_tree.itemDoubleClicked.connect(self.on_file_double_click)

        # 文件系统监控信号
        self.file_watcher.fileChanged.connect(self.on_file_changed)
        self.file_watcher.directoryChanged.connect(self.on_directory_changed)

        # 排序按钮组信号
        self.sorting_group.buttonClicked.connect(self.sort_files)

        # 快捷键
        self.setup_shortcuts()

        # 添加目标目录树的双击事件处理
        self.sorted_tree.itemDoubleClicked.connect(self.on_target_tree_double_click)

        # 为评分框添加事件过滤器
        if "rating" in self.fields_entries:
            self.fields_entries["rating"].installEventFilter(self)
            # 评分格式化
            rating_widget = self.fields_entries["rating"]
            # 移除 textChanged 连接
            # rating_widget.textChanged.connect(self.on_rating_text_changed)
            # 添加键盘事件处理
            rating_widget.keyReleaseEvent = lambda event: self.on_rating_key_release(
                rating_widget, event
            )

        # 连接保存按钮
        save_button = None
        for btn in self.findChildren(QPushButton):
            if "保存更改" in btn.text():
                save_button = btn
                break
        if save_button:
            save_button.clicked.connect(self.save_changes)

    def eventFilter(self, obj, event):
        """事件过滤器"""
        if (
            event.type() == event.KeyPress
            and isinstance(obj, QTextEdit)
            and obj == self.fields_entries.get("rating")
        ):

            if event.key() == Qt.Key_Left:
                # 处理向左键
                self.focus_file_list()
                return True
            elif event.key() == Qt.Key_Right:
                # 全选评分框文本
                obj.selectAll()
                return True

        return super().eventFilter(obj, event)

    def setup_shortcuts(self):
        """设置快捷键"""
        QShortcut(QKeySequence("F5"), self, self.load_files_in_folder)
        QShortcut(QKeySequence("Ctrl+S"), self, self.save_changes)
        QShortcut(QKeySequence("Ctrl+Right"), self, self.start_move_thread)

    def keyPressEvent(self, event):
        """键盘事件处理"""
        if event.key() == Qt.Key_Left:
            # 获取当前焦点控件
            focus_widget = self.focusWidget()
            # 检查是否是评分框
            if (
                isinstance(focus_widget, QTextEdit)
                and "rating" in self.fields_entries
                and self.fields_entries["rating"] == focus_widget
            ):
                # 阻止事件传递并移动焦点到文件列表
                event.accept()
                self.focus_file_list()
                return
        elif event.key() == Qt.Key_Right:
            focus_widget = self.focusWidget()
            if isinstance(focus_widget, QTreeWidget):
                event.accept()
                self.focus_rating()
                return

        # 如果不是特殊处理的情况，调用父类的事件处理
        super().keyPressEvent(event)

    def on_rating_key_release(self, widget, event):
        """处理评分输入的格式化"""
        try:
            # 获取当前文本
            current_text = widget.toPlainText().strip()

            # 空值不处理
            if not current_text:
                return

            # 获取输入的字符
            key_text = event.text()

            # 打印调试信息，帮助排查问题
            print(f"当前文本: {current_text}, 输入字符: {key_text}")

            # 如果输入的是数字
            if key_text.isdigit():
                # 如果当前文本包含小数点（即已经格式化过）
                if "." in current_text:
                    main_num = current_text.split(".")[0]
                    formatted_rating = f"{main_num}.{key_text}"

                    # 检查是否超过9.9
                    if float(formatted_rating) <= 9.9:
                        widget.setPlainText(formatted_rating)
                    else:
                        widget.setPlainText("9.9")
                # 如果是单个数字，格式化为 x.0
                elif current_text.isdigit():
                    formatted_rating = f"{float(current_text):.1f}"
                    widget.setPlainText(formatted_rating)

                # 移动光标到末尾
                cursor = widget.textCursor()
                cursor.movePosition(cursor.End)
                widget.setTextCursor(cursor)

        except Exception as e:
            print(f"处理评分输入时出错: {str(e)}")

        # 调用原始的事件处理
        QTextEdit.keyReleaseEvent(widget, event)

    def open_folder(self):
        """选择并打开NFO文件夹"""
        folder_selected = QFileDialog.getExistingDirectory(self, "选择NFO文件夹")
        if folder_selected:
            self.folder_path = folder_selected
            # 直接加载文件而不更新label
            self.load_files_in_folder()

            # 添加文件夹监控
            if self.folder_path in self.file_watcher.directories():
                self.file_watcher.removePath(self.folder_path)
            self.file_watcher.addPath(self.folder_path)

    def select_target_folder(self):
        """选择目标文件夹"""
        target_folder = QFileDialog.getExistingDirectory(self, "选择目标文件夹")
        if target_folder:
            self.current_target_path = target_folder
            self.load_target_files(target_folder)

    def load_files_in_folder(self):
        """加载文件夹中的NFO文件"""
        if not self.folder_path:
            return

        self.file_tree.clear()
        self.nfo_files = []

        try:
            for root, dirs, files in os.walk(self.folder_path):
                for file in files:
                    if file.endswith(".nfo"):
                        nfo_path = os.path.join(root, file)
                        self.nfo_files.append(nfo_path)

                        relative_path = os.path.relpath(nfo_path, self.folder_path)
                        parts = relative_path.split(os.sep)

                        if len(parts) > 1:
                            first_level = (
                                os.sep.join(parts[:-2]) if len(parts) > 2 else ""
                            )
                            second_level = parts[-2]
                            nfo_file = parts[-1]
                        else:
                            first_level = ""
                            second_level = ""
                            nfo_file = parts[-1]

                        item = QTreeWidgetItem([first_level, second_level, nfo_file])
                        self.file_tree.addTopLevelItem(item)

            # 选中第一项
            if self.file_tree.topLevelItemCount() > 0:
                first_item = self.file_tree.topLevelItem(0)
                self.file_tree.setCurrentItem(first_item)
                self.on_file_select()

        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载文件失败: {str(e)}")

    def on_file_select(self):
        """文件选择响应函数"""
        selected_items = self.file_tree.selectedItems()
        if not selected_items:
            return

        # 检查是否有未保存的更改
        if self.current_file_path and self.has_unsaved_changes():
            reply = QMessageBox.question(
                self,
                "保存更改",
                "当前有未保存的更改，是否保存？",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )

            if reply == QMessageBox.Cancel:
                return
            elif reply == QMessageBox.Yes:
                self.save_changes()

        # 处理新选中的文件
        item = selected_items[0]
        values = [item.text(i) for i in range(3)]

        if values[2]:  # 如果有NFO文件名
            self.current_file_path = (
                os.path.join(self.folder_path, values[0], values[1], values[2])
                if values[1]
                else os.path.join(self.folder_path, values[0], values[2])
            )

            if not os.path.exists(self.current_file_path):
                self.file_tree.takeTopLevelItem(
                    self.file_tree.indexOfTopLevelItem(item)
                )
                return

            self.load_nfo_fields()
            if self.show_images_checkbox.isChecked():
                self.display_image()

    def load_nfo_fields(self):
        """加载NFO文件字段"""
        # 清空所有字段
        for entry in self.fields_entries.values():
            if isinstance(entry, QTextEdit):
                entry.clear()
            elif isinstance(entry, QLabel):
                entry.setText("")

        try:
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            # 基本字段
            fields_to_load = ["title", "plot", "series", "rating", "num"]
            for field in fields_to_load:
                elem = root.find(field)
                if elem is not None and elem.text:
                    widget = self.fields_entries.get(field)
                    if widget:
                        if isinstance(widget, QLabel):
                            widget.setText(elem.text)
                        else:
                            widget.setPlainText(elem.text)

            # 演员列表
            actors = [
                actor.find("name").text.strip()
                for actor in root.findall("actor")
                if actor.find("name") is not None and actor.find("name").text
            ]
            self.fields_entries["actors"].setPlainText(", ".join(actors))

            # 标签
            tags = [
                tag.text.strip()
                for tag in root.findall("tag")
                if tag is not None and tag.text
            ]
            self.fields_entries["tags"].setPlainText(", ".join(tags))

            # 发行日期
            release_elem = root.find("release")
            if release_elem is not None and release_elem.text:
                self.release_label.setText(release_elem.text.strip())

        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载NFO文件失败: {str(e)}")

    def load_target_files(self, target_path):
        """加载目标文件夹内容"""
        self.sorted_tree.clear()
        try:
            # 添加返回上级目录项
            if os.path.dirname(target_path) != target_path:  # 不是根目录
                parent_item = QTreeWidgetItem([".."])
                parent_item.setIcon(
                    0, self.style().standardIcon(self.style().SP_ArrowUp)
                )
                self.sorted_tree.addTopLevelItem(parent_item)

            # 添加文件夹
            for entry in os.scandir(target_path):
                if entry.is_dir():
                    item = QTreeWidgetItem([entry.name])
                    item.setIcon(0, self.style().standardIcon(self.style().SP_DirIcon))
                    self.sorted_tree.addTopLevelItem(item)

            # 更新状态信息
            folder_count = self.sorted_tree.topLevelItemCount()
            if ".." in [
                self.sorted_tree.topLevelItem(i).text(0) for i in range(folder_count)
            ]:
                folder_count -= 1  # 不计算返回上级目录项

            status_text = f"目标目录: {target_path} (共{folder_count}个文件夹)"
            self.statusBar().showMessage(status_text)

        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载目标目录失败: {str(e)}")

    def save_changes(self):
        """保存更改"""
        if not self.current_file_path:
            return

        try:
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            # 获取字段值
            title = self.fields_entries["title"].toPlainText().strip()
            plot = self.fields_entries["plot"].toPlainText().strip()
            actors_text = self.fields_entries["actors"].toPlainText().strip()
            series = self.fields_entries["series"].toPlainText().strip()
            tags_text = self.fields_entries["tags"].toPlainText().strip()
            rating = self.fields_entries["rating"].toPlainText().strip()

            # 更新基本字段
            for field, value in {
                "title": title,
                "plot": plot,
                "series": series,
                "rating": rating,
            }.items():
                elem = root.find(field)
                if elem is None:
                    elem = ET.SubElement(root, field)
                elem.text = value

            # 更新 criticrating 字段
            try:
                rating_value = float(rating)
                critic_rating = int(rating_value * 10)  # 将 rating 转换为 criticrating
                critic_elem = root.find("criticrating")
                if critic_elem is None:
                    critic_elem = ET.SubElement(root, "criticrating")
                critic_elem.text = str(critic_rating)
            except ValueError:
                pass

            # 更新演员信息
            for actor_elem in root.findall("actor"):
                root.remove(actor_elem)
            for actor in actors_text.split(","):
                actor = actor.strip()
                if actor:
                    actor_elem = ET.SubElement(root, "actor")
                    name_elem = ET.SubElement(actor_elem, "name")
                    name_elem.text = actor

            # 更新标签和类型（联动更新）
            # 删除现有的标签和类型
            for tag_elem in root.findall("tag"):
                root.remove(tag_elem)
            for genre_elem in root.findall("genre"):
                root.remove(genre_elem)

            # 从 tags 字段获取值，同时添加到 tag 和 genre 节点
            for tag in tags_text.split(","):
                tag = tag.strip()
                if tag:
                    # 添加标签
                    tag_elem = ET.SubElement(root, "tag")
                    tag_elem.text = tag
                    # 添加类型
                    genre_elem = ET.SubElement(root, "genre")
                    genre_elem.text = tag

            # 保存文件
            xml_str = ET.tostring(root, encoding="utf-8")
            parsed_str = minidom.parseString(xml_str)
            pretty_str = parsed_str.toprettyxml(indent="  ", encoding="utf-8")

            pretty_str = "\n".join(
                line for line in pretty_str.decode("utf-8").split("\n") if line.strip()
            )

            with open(self.current_file_path, "w", encoding="utf-8") as file:
                file.write(pretty_str)

            # 更新保存时间
            save_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.save_time_label.setText(f"保存时间: {save_time}")

            # 保持选中状态
            if self.selected_index_cache:
                for item_id in self.selected_index_cache:
                    self.file_tree.setCurrentItem(item_id)

        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存NFO文件失败: {str(e)}")

    def start_move_thread(self):
        """启动移动文件的线程"""
        # 检查选择
        selected_items = self.file_tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "警告", "请先选择要移动的文件夹")
            return

        if not self.current_target_path:
            QMessageBox.critical(self, "错误", "请先选择目标目录")
            return

        # 收集源路径
        src_paths = []
        for item in selected_items:
            values = [item.text(i) for i in range(3)]
            if values[1]:  # 有二级目录
                src_path = os.path.join(self.folder_path, values[0], values[1])
            else:  # 只有一级目录
                src_path = os.path.join(self.folder_path, values[0])
            src_paths.append(src_path)

        # 创建并配置进度对话框
        progress = QProgressDialog("准备移动...", "取消", 0, len(src_paths), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(True)
        progress.setAutoReset(True)

        # 创建移动线程
        self.move_thread = FileOperationThread(
            operation_type="move",
            src_paths=src_paths,
            dest_path=self.current_target_path,
        )

        # 连接信号
        self.move_thread.progress.connect(progress.setValue)
        self.move_thread.status.connect(progress.setLabelText)
        self.move_thread.error.connect(
            lambda msg: QMessageBox.critical(self, "错误", msg)
        )
        self.move_thread.finished.connect(self.on_move_finished)

        # 连接取消按钮
        progress.canceled.connect(self.move_thread.stop)

        # 启动线程
        self.move_thread.start()

    def on_move_finished(self):
        """文件移动完成回调"""
        # 刷新文件列表
        self.load_files_in_folder()

        # 刷新目标目录
        if self.current_target_path:
            self.load_target_files(self.current_target_path)

        # 清理线程
        if self.move_thread:
            self.move_thread.deleteLater()
            self.move_thread = None

    def open_selected_nfo(self):
        """打开选中的NFO文件"""
        selected_items = self.file_tree.selectedItems()
        for item in selected_items:
            values = [item.text(i) for i in range(3)]
            if values[2]:  # 有NFO文件
                nfo_path = (
                    os.path.join(self.folder_path, values[0], values[1], values[2])
                    if values[1]
                    else os.path.join(self.folder_path, values[0], values[2])
                )

                if os.path.exists(nfo_path):
                    os.startfile(nfo_path)
                else:
                    QMessageBox.critical(self, "错误", f"NFO文件不存在: {nfo_path}")

    def open_selected_folder(self):
        """打开选中的文件夹"""
        selected_items = self.file_tree.selectedItems()
        for item in selected_items:
            values = [item.text(i) for i in range(3)]
            if values[2]:
                nfo_path = (
                    os.path.join(self.folder_path, values[0], values[1], values[2])
                    if values[1]
                    else os.path.join(self.folder_path, values[0], values[2])
                )

                if os.path.exists(nfo_path):
                    folder_path = os.path.dirname(nfo_path)
                    os.startfile(folder_path)
                else:
                    QMessageBox.critical(
                        self, "错误", f"文件夹不存在: {os.path.dirname(nfo_path)}"
                    )

    def open_selected_video(self):
        """打开选中的视频文件"""
        video_extensions = [
            ".mp4",
            ".mkv",
            ".avi",
            ".mov",
            ".rm",
            ".mpeg",
            ".ts",
            ".strm",
        ]
        selected_items = self.file_tree.selectedItems()

        for item in selected_items:
            values = [item.text(i) for i in range(3)]
            if values[2]:
                nfo_path = (
                    os.path.join(self.folder_path, values[0], values[1], values[2])
                    if values[1]
                    else os.path.join(self.folder_path, values[0], values[2])
                )

                if os.path.exists(nfo_path):
                    video_base = os.path.splitext(nfo_path)[0]
                    for ext in video_extensions:
                        video_path = video_base + ext
                        if os.path.exists(video_path):
                            if ext == ".strm":
                                try:
                                    with open(video_path, "r", encoding="utf-8") as f:
                                        strm_url = f.readline().strip()
                                    if strm_url:
                                        subprocess.Popen(["mpvnet", strm_url])
                                    else:
                                        QMessageBox.critical(
                                            self, "错误", "STRM文件内容为空或无效"
                                        )
                                except Exception as e:
                                    QMessageBox.critical(
                                        self, "错误", f"读取STRM文件失败: {str(e)}"
                                    )
                            else:
                                subprocess.Popen(["mpvnet", video_path])
                            return

                    QMessageBox.warning(self, "警告", "未找到匹配的视频文件")
                else:
                    QMessageBox.critical(self, "错误", f"NFO文件不存在: {nfo_path}")

    def has_unsaved_changes(self):
        """检查是否有未保存的更改"""
        if not self.current_file_path or not os.path.exists(self.current_file_path):
            return False

        try:
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            # 检查基本字段
            for field in ["title", "plot", "series", "rating"]:
                current_value = self.fields_entries[field].toPlainText().strip()
                elem = root.find(field)
                original_value = (
                    elem.text.strip() if elem is not None and elem.text else ""
                )
                if current_value != original_value:
                    print(f"字段 {field} 发生更改:")
                    print(f"原值: '{original_value}'")
                    print(f"新值: '{current_value}'")
                    return True

            # 检查演员列表
            current_actors = set(
                actor.strip()
                for actor in self.fields_entries["actors"]
                .toPlainText()
                .strip()
                .split(",")
                if actor.strip()
            )
            original_actors = {
                actor.find("name").text.strip()
                for actor in root.findall("actor")
                if actor.find("name") is not None and actor.find("name").text
            }
            if current_actors != original_actors:
                print("演员列表发生更改:")
                print(f"原列表: {original_actors}")
                print(f"新列表: {current_actors}")
                return True

            # 检查标签
            current_tags = set(
                tag.strip()
                for tag in self.fields_entries["tags"].toPlainText().strip().split(",")
                if tag.strip()
            )
            original_tags = {
                tag.text.strip()
                for tag in root.findall("tag")
                if tag is not None and tag.text
            }
            if current_tags != original_tags:
                print("标签列表发生更改:")
                print(f"原标签: {original_tags}")
                print(f"新标签: {current_tags}")
                return True

            return False

        except Exception as e:
            print(f"检查更改状态时出错: {str(e)}")
            return False

    def on_file_changed(self, path):
        """文件变化响应"""
        if path == self.current_file_path:
            self.load_nfo_fields()

    def on_directory_changed(self, path):
        """目录变化响应"""
        if path == self.folder_path:
            self.load_files_in_folder()

    def toggle_image_display(self):
        """切换图片显示状态"""
        if self.show_images_checkbox.isChecked():
            self.display_image()
        else:
            self.clear_images()

    def clear_images(self):
        """清除图片显示"""
        if hasattr(self, "poster_label"):
            self.poster_label.clear()
            self.poster_label.setText("封面图 (poster)")
        if hasattr(self, "thumb_label"):
            self.thumb_label.clear()
            self.thumb_label.setText("缩略图 (thumb)")

    def display_image(self):
        """显示图片"""
        if not self.current_file_path:
            return

        folder = os.path.dirname(self.current_file_path)

        # 查找图片文件
        poster_files = []
        thumb_files = []
        for entry in os.scandir(folder):
            name = entry.name.lower()
            if name.endswith(".jpg"):
                if "poster" in name:
                    poster_files.append(entry.name)
                elif "thumb" in name:
                    thumb_files.append(entry.name)

        # 显示poster图片
        if poster_files:
            self.load_image(os.path.join(folder, poster_files[0]), self.poster_label)
        else:
            self.poster_label.setText("文件夹内无poster图片")

        # 显示thumb图片
        if thumb_files:
            self.load_image(os.path.join(folder, thumb_files[0]), self.thumb_label)
        else:
            self.thumb_label.setText("文件夹内无thumb图片")

    def load_image(self, image_path, label):
        """加载图片到label"""
        try:
            pixmap = QPixmap(image_path)
            if pixmap.isNull():
                label.setText("加载图片失败")
                return

            # 根据label大小调整图片
            scaled_pixmap = pixmap.scaled(
                label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            label.setPixmap(scaled_pixmap)

        except Exception as e:
            label.setText(f"加载图片失败: {str(e)}")

    def sort_files(self):
        """排序文件列表"""
        if not self.sorting_group.checkedButton():
            return

        sort_by = self.sorting_group.checkedButton().text()
        items = []

        # 收集所有项目
        for i in range(self.file_tree.topLevelItemCount()):
            item = self.file_tree.topLevelItem(i)
            values = [item.text(j) for j in range(3)]
            items.append((values, item))

        # 定义排序键函数
        def get_sort_key(item_tuple):
            values, _ = item_tuple
            if values[2]:  # 如果有NFO文件
                try:
                    nfo_path = (
                        os.path.join(self.folder_path, values[0], values[1], values[2])
                        if values[1]
                        else os.path.join(self.folder_path, values[0], values[2])
                    )

                    tree = ET.parse(nfo_path)
                    root = tree.getroot()

                    if "演员" in sort_by:
                        actors = [
                            actor.find("name").text.strip()
                            for actor in root.findall("actor")
                            if actor.find("name") is not None
                        ]
                        return ", ".join(sorted(actors))
                    elif "系列" in sort_by:
                        series = root.find("series")
                        return series.text.strip() if series is not None else ""
                    elif "评分" in sort_by:
                        rating = root.find("rating")
                        return float(rating.text) if rating is not None else 0
                    else:  # 文件名
                        return values[2].lower()
                except:
                    return ""
            return ""

        # 排序
        items.sort(key=get_sort_key)

        # 重新添加到树
        self.file_tree.clear()
        for values, item in items:
            new_item = QTreeWidgetItem(values)
            self.file_tree.addTopLevelItem(new_item)

    def apply_filter(self):
        """应用筛选"""
        field = self.field_combo.currentText()
        condition = self.condition_combo.currentText()
        filter_text = self.filter_entry.text().strip()

        if not filter_text:
            self.load_files_in_folder()
            return

        self.file_tree.clear()

        for nfo_file in self.nfo_files:
            try:
                tree = ET.parse(nfo_file)
                root = tree.getroot()

                # 获取字段值
                if field == "标题":
                    elem = root.find("title")
                    value = elem.text.strip() if elem is not None else ""
                elif field == "标签":
                    value = ", ".join(
                        tag.text.strip()
                        for tag in root.findall("tag")
                        if tag is not None and tag.text
                    )
                elif field == "演员":
                    value = ", ".join(
                        actor.find("name").text.strip()
                        for actor in root.findall("actor")
                        if actor.find("name") is not None
                    )
                elif field == "系列":
                    elem = root.find("series")
                    value = elem.text.strip() if elem is not None else ""
                elif field == "评分":
                    elem = root.find("rating")
                    value = elem.text.strip() if elem is not None else "0"

                # 判断是否匹配
                match = False
                if field == "评分":
                    try:
                        current_value = float(value)
                        filter_value = float(filter_text)
                        if condition == "包含":
                            match = str(filter_value) in str(current_value)
                        elif condition == "大于":
                            match = current_value > filter_value
                        elif condition == "小于":
                            match = current_value < filter_value
                    except ValueError:
                        continue
                else:
                    match = filter_text.lower() in value.lower()

                # 如果匹配,添加到树中
                if match:
                    relative_path = os.path.relpath(nfo_file, self.folder_path)
                    parts = relative_path.split(os.sep)

                    if len(parts) > 1:
                        first_level = os.sep.join(parts[:-2]) if len(parts) > 2 else ""
                        second_level = parts[-2]
                        nfo_name = parts[-1]
                    else:
                        first_level = ""
                        second_level = ""
                        nfo_name = parts[-1]

                    item = QTreeWidgetItem([first_level, second_level, nfo_name])
                    self.file_tree.addTopLevelItem(item)

            except ET.ParseError:
                continue
            except Exception as e:
                print(f"Error processing {nfo_file}: {str(e)}")
                continue

    def batch_filling(self):
        """批量填充"""
        from PyQt5.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QRadioButton,
            QLineEdit,
            QPushButton,
            QTextEdit,
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("批量填充")
        dialog.resize(400, 600)

        layout = QVBoxLayout()

        # 字段选择
        layout.addWidget(QLabel("选择填充替换字段:"))
        field_var = None
        for field in ["series", "rating"]:
            rb = QRadioButton(field)
            if field_var is None:
                field_var = rb
                rb.setChecked(True)
            layout.addWidget(rb)

        # 填充值输入
        layout.addWidget(QLabel("填充替换值:"))
        value_entry = QLineEdit()
        layout.addWidget(value_entry)

        # 日志显示
        log_text = QTextEdit()
        layout.addWidget(log_text)

        def apply_fill():
            field = [rb for rb in dialog.findChildren(QRadioButton) if rb.isChecked()][
                0
            ].text()
            fill_value = value_entry.text().strip()

            if not fill_value:
                return

            selected_items = self.file_tree.selectedItems()
            if not selected_items:
                QMessageBox.warning(self, "警告", "请先选择要填充的文件")
                return

            operation_log = []

            for item in selected_items:
                values = [item.text(i) for i in range(3)]
                nfo_path = (
                    os.path.join(self.folder_path, values[0], values[1], values[2])
                    if values[1]
                    else os.path.join(self.folder_path, values[0], values[2])
                )

                try:
                    tree = ET.parse(nfo_path)
                    root = tree.getroot()

                    elem = root.find(field)
                    if elem is None:
                        elem = ET.SubElement(root, field)
                    elem.text = fill_value

                    xml_str = ET.tostring(root, encoding="utf-8")
                    parsed_str = minidom.parseString(xml_str)
                    pretty_str = parsed_str.toprettyxml(indent="  ", encoding="utf-8")

                    pretty_str = "\n".join(
                        line
                        for line in pretty_str.decode("utf-8").split("\n")
                        if line.strip()
                    )

                    with open(nfo_path, "w", encoding="utf-8") as f:
                        f.write(pretty_str)

                    operation_log.append(f"{nfo_path}: {field}字段填充成功")

                except Exception as e:
                    operation_log.append(f"{nfo_path}: {field}字段填充失败 - {str(e)}")

            log_text.setText("\n".join(operation_log))

        apply_button = QPushButton("应用填充")
        apply_button.clicked.connect(apply_fill)
        layout.addWidget(apply_button)

        dialog.setLayout(layout)
        dialog.exec_()

    def batch_add(self):
        """批量新增"""
        from PyQt5.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QRadioButton,
            QLineEdit,
            QPushButton,
            QTextEdit,
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("批量新增")
        dialog.resize(400, 600)

        layout = QVBoxLayout()

        # 字段选择
        layout.addWidget(QLabel("选择字段新增一个值:"))
        field_var = None
        for field in ["tag", "genre"]:
            rb = QRadioButton(field)
            if field_var is None:
                field_var = rb
                rb.setChecked(True)
            layout.addWidget(rb)

        # 新增值输入
        layout.addWidget(QLabel("输入新增值:"))
        value_entry = QLineEdit()
        layout.addWidget(value_entry)

        # 日志显示
        log_text = QTextEdit()
        layout.addWidget(log_text)

        def apply_add():
            field = [rb for rb in dialog.findChildren(QRadioButton) if rb.isChecked()][
                0
            ].text()
            add_value = value_entry.text().strip()

            if not add_value:
                return

            selected_items = self.file_tree.selectedItems()
            if not selected_items:
                QMessageBox.warning(self, "警告", "请先选择要新增的文件")
                return

            operation_log = []

            for item in selected_items:
                values = [item.text(i) for i in range(3)]
                nfo_path = (
                    os.path.join(self.folder_path, values[0], values[1], values[2])
                    if values[1]
                    else os.path.join(self.folder_path, values[0], values[2])
                )

                try:
                    tree = ET.parse(nfo_path)
                    root = tree.getroot()

                    new_elem = ET.SubElement(root, field)
                    new_elem.text = add_value

                    xml_str = ET.tostring(root, encoding="utf-8")
                    parsed_str = minidom.parseString(xml_str)
                    pretty_str = parsed_str.toprettyxml(indent="  ", encoding="utf-8")

                    pretty_str = "\n".join(
                        line
                        for line in pretty_str.decode("utf-8").split("\n")
                        if line.strip()
                    )

                    with open(nfo_path, "w", encoding="utf-8") as f:
                        f.write(pretty_str)

                    operation_log.append(f"{nfo_path}: {field}字段新增成功")

                except Exception as e:
                    operation_log.append(f"{nfo_path}: {field}字段新增失败 - {str(e)}")

            log_text.setText("\n".join(operation_log))

        apply_button = QPushButton("应用新增")
        apply_button.clicked.connect(apply_add)
        layout.addWidget(apply_button)

        dialog.setLayout(layout)
        dialog.exec_()

    def open_batch_rename_tool(self):
        """打开重命名工具"""
        if not self.folder_path:
            QMessageBox.critical(self, "错误", "请先选择NFO目录")
            return

        try:
            from cg_rename import start_rename_process

            # 启动重命名进程
            rename_window = start_rename_process(self.folder_path, self)

            if rename_window:
                # 设置窗口大小和位置
                window_width = 900
                window_height = 500
                screen = QApplication.primaryScreen()
                screen_geometry = screen.availableGeometry()
                x = (screen_geometry.width() - window_width) // 2
                y = (screen_geometry.height() - window_height) // 2
                rename_window.window.setGeometry(x, y, window_width, window_height)

                # 设置关闭回调
                def on_rename_close():
                    rename_window.window.close()
                    self.load_files_in_folder()

                # 设置窗口关闭事件
                rename_window.window.closeEvent = lambda e: on_rename_close()

        except ImportError:
            QMessageBox.critical(
                self, "错误", "找不到 cg_rename.py 文件,请确保它与主程序在同一目录。"
            )
        except Exception as e:
            QMessageBox.critical(self, "错误", f"启动重命名工具时出错: {str(e)}")

    def on_file_select(self):
        """文件选择回调"""
        selected_items = self.file_tree.selectedItems()
        if not selected_items:
            return

        # 检查是否有未保存的更改
        if self.current_file_path and self.has_unsaved_changes():
            reply = QMessageBox.question(
                self,
                "保存更改",
                "当前有未保存的更改，是否保存?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )

            if reply == QMessageBox.Cancel:
                return
            elif reply == QMessageBox.Yes:
                self.save_changes()

        # 处理新选中的文件
        item = selected_items[0]
        values = [item.text(i) for i in range(3)]

        if values[2]:  # 如果有NFO文件名
            self.current_file_path = (
                os.path.join(self.folder_path, values[0], values[1], values[2])
                if values[1]
                else os.path.join(self.folder_path, values[0], values[2])
            )

            if not os.path.exists(self.current_file_path):
                self.file_tree.takeTopLevelItem(
                    self.file_tree.indexOfTopLevelItem(item)
                )
                return

            self.load_nfo_fields()
            if self.show_images_checkbox.isChecked():
                self.display_image()

    def on_file_double_click(self, item, column):
        """双击文件列表项处理"""
        values = [item.text(i) for i in range(3)]
        if values[2]:  # 有NFO文件
            nfo_path = (
                os.path.join(self.folder_path, values[0], values[1], values[2])
                if values[1]
                else os.path.join(self.folder_path, values[0], values[2])
            )

            if os.path.exists(nfo_path):
                # 打开NFO所在文件夹
                folder_path = os.path.dirname(nfo_path)
                os.startfile(folder_path)
            else:
                QMessageBox.critical(
                    self, "错误", f"文件夹不存在: {os.path.dirname(nfo_path)}"
                )

    def on_target_tree_double_click(self, item, column):
        """目标目录树双击处理"""
        if not self.current_target_path:
            return

        item_text = item.text(0)
        if item_text == "..":  # 返回上级目录
            parent_path = os.path.dirname(self.current_target_path)
            if parent_path != self.current_target_path:  # 确保不是根目录
                self.current_target_path = parent_path
                self.load_target_files(parent_path)
        else:  # 进入子目录
            new_path = os.path.join(self.current_target_path, item_text)
            if os.path.isdir(new_path):
                self.current_target_path = new_path
                self.load_target_files(new_path)

    def focus_file_list(self):
        """焦点回到文件列表"""
        if hasattr(self, "file_tree"):
            self.file_tree.setFocus(Qt.OtherFocusReason)  # 使用明确的焦点原因
            if not self.file_tree.selectedItems():
                items = self.file_tree.topLevelItemCount()
                if items > 0:
                    first_item = self.file_tree.topLevelItem(0)
                    self.file_tree.setCurrentItem(first_item)

    def focus_rating(self):
        """焦点到评分框"""
        if "rating" in self.fields_entries:
            rating_widget = self.fields_entries["rating"]
            rating_widget.setFocus(Qt.OtherFocusReason)  # 使用明确的焦点原因
            rating_widget.selectAll()

    def open_image_and_crop(self, image_type):
        """打开图片裁剪工具"""
        if not self.current_file_path:
            return

        folder = os.path.dirname(self.current_file_path)
        image_files = [
            f
            for f in os.listdir(folder)
            if f.lower().endswith(".jpg") and image_type in f.lower()
        ]

        if not image_files:
            QMessageBox.critical(self, "错误", f"未找到{image_type}图片")
            return

        try:
            from cg_crop import EmbyPosterCrop

            # 获取图片路径
            image_path = os.path.join(folder, image_files[0])

            # 获取NFO文件内容确定水印配置
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            has_subtitle = False
            mark_type = "none"  # 默认无水印

            # 检查tag标签内容
            for tag in root.findall("tag"):
                tag_text = tag.text.lower() if tag.text else ""
                if "中文字幕" in tag_text:
                    has_subtitle = True
                elif "无码破解" in tag_text:
                    mark_type = "umr"
                elif "无码流出" in tag_text:
                    mark_type = "leak"
                elif "无码" in tag_text:
                    mark_type = "wuma"
                if mark_type != "none":
                    break

            # 获取NFO文件的基础名称
            nfo_base_name = os.path.splitext(os.path.basename(self.current_file_path))[
                0
            ]

            # 创建裁剪工具窗口
            crop_tool = EmbyPosterCrop(nfo_base_name=nfo_base_name)

            # 加载图片
            crop_tool.load_initial_image(image_path)

            # 设置水印选项
            if has_subtitle:
                crop_tool.sub_check.setChecked(True)
            for button in crop_tool.mark_group.buttons():
                if button.property("value") == mark_type:
                    button.setChecked(True)
                    break

            # 运行窗口并等待其完成
            crop_tool.exec_()

            # 如果显示图片选项是打开的，刷新图片显示
            if self.show_images_checkbox.isChecked():
                self.display_image()

        except ImportError:
            QMessageBox.critical(self, "错误", "找不到 cg_crop.py 文件")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"裁剪工具出错: {str(e)}")

    def contextMenuEvent(self, event):
        """右键菜单"""
        menu = QMenu(self)

        refresh_action = menu.addAction("刷新")
        refresh_action.triggered.connect(self.load_files_in_folder)

        if self.file_tree.selectedItems():
            menu.addSeparator()

            open_action = menu.addAction("打开NFO")
            open_action.triggered.connect(self.open_selected_nfo)

            folder_action = menu.addAction("打开文件夹")
            folder_action.triggered.connect(self.open_selected_folder)

            video_action = menu.addAction("播放视频")
            video_action.triggered.connect(self.open_selected_video)

            if self.current_target_path:
                menu.addSeparator()
                move_action = menu.addAction("移动到目标目录")
                move_action.triggered.connect(self.start_move_thread)

        menu.exec_(event.globalPos())

    def dragEnterEvent(self, event):
        """拖拽进入事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """拖拽放下事件"""
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isdir(path):
                self.folder_path = path
                self.folder_path_label.setText(path)
                self.load_files_in_folder()


def main():
    # 设置高DPI支持
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = NFOEditorQt5()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
