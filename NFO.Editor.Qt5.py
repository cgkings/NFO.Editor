import os
import shutil
import sys
import threading
import webbrowser
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
from datetime import datetime
from PIL import Image
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QLineEdit,
    QFileDialog,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QShortcut,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QDialog,
    QScrollArea,
    QWidget,
    QVBoxLayout,
    QGridLayout,
    QHBoxLayout,
    QGroupBox,
    QCheckBox,
    QProgressBar,
)
from PyQt5.QtCore import (
    Qt,
    QThread,
    pyqtSignal,
    QSettings,
    QFileSystemWatcher,
    QTimer,
)
from PyQt5.QtGui import QIcon, QPixmap, QKeySequence
import subprocess
import winshell
import json
import requests
from bs4 import BeautifulSoup


# ================ 异步加载和缓存机制 ================

class NFOCache:
    """NFO文件缓存管理器"""

    def __init__(self):
        self.cache = {}
        self.file_paths = []

    def get(self, path):
        return self.cache.get(path)

    def set(self, path, data):
        self.cache[path] = data
        if path not in self.file_paths:
            self.file_paths.append(path)

    def remove(self, path):
        if path in self.cache:
            del self.cache[path]
        if path in self.file_paths:
            self.file_paths.remove(path)

    def clear(self):
        self.cache.clear()
        self.file_paths.clear()

    def get_all_paths(self):
        return self.file_paths.copy()

    def size(self):
        return len(self.cache)


class LoadFilesThread(QThread):
    """异步加载NFO文件的线程"""

    progress = pyqtSignal(int, int, str)
    item_ready = pyqtSignal(object, dict)
    finished_signal = pyqtSignal(int)
    error = pyqtSignal(str)

    def __init__(self, folder_path, batch_size=100):
        super().__init__()
        self.folder_path = folder_path
        self.batch_size = batch_size
        self.is_running = True

    def run(self):
        try:
            nfo_files = []
            for root, dirs, files in os.walk(self.folder_path):
                if not self.is_running:
                    return
                for file in files:
                    if file.endswith(".nfo"):
                        nfo_files.append(os.path.join(root, file))

            total = len(nfo_files)
            if total == 0:
                self.finished_signal.emit(0)
                return

            for i, nfo_path in enumerate(nfo_files, 1):
                if not self.is_running:
                    return

                try:
                    cache_data = self._parse_nfo(nfo_path)
                    relative_path = os.path.relpath(nfo_path, self.folder_path)
                    parts = relative_path.split(os.sep)

                    if len(parts) > 1:
                        first_level = os.sep.join(parts[:-2]) if len(parts) > 2 else ""
                        second_level = parts[-2]
                        nfo_file = parts[-1]
                    else:
                        first_level = ""
                        second_level = ""
                        nfo_file = parts[-1]

                    tree_item = QTreeWidgetItem([first_level, second_level, nfo_file])
                    self.item_ready.emit(tree_item, {nfo_path: cache_data})
                    self.progress.emit(i, total, os.path.basename(nfo_path))

                except Exception as e:
                    print(f"解析文件失败 {nfo_path}: {str(e)}")
                    continue

            self.finished_signal.emit(total)

        except Exception as e:
            self.error.emit(f"加载过程出错: {str(e)}")

    def _parse_nfo(self, nfo_path):
        tree = ET.parse(nfo_path)
        root = tree.getroot()

        data = {
            'path': nfo_path,
            'num': '',
            'title': '',
            'plot': '',
            'series': '',
            'rating': 0.0,
            'release': '',
            'actors': [],
            'tags': [],
        }

        for field in ['num', 'title', 'plot', 'series']:
            elem = root.find(field)
            if elem is not None and elem.text:
                data[field] = elem.text.strip()

        rating_elem = root.find('rating')
        if rating_elem is not None and rating_elem.text:
            try:
                data['rating'] = float(rating_elem.text.strip())
            except ValueError:
                data['rating'] = 0.0

        release_elem = root.find('release')
        if release_elem is not None and release_elem.text:
            data['release'] = release_elem.text.strip()

        actors = []
        for actor in root.findall('actor'):
            name_elem = actor.find('name')
            if name_elem is not None and name_elem.text:
                actors.append(name_elem.text.strip())
        data['actors'] = actors

        tags = []
        for tag in root.findall('tag'):
            if tag is not None and tag.text:
                tags.append(tag.text.strip())
        data['tags'] = tags

        return data

    def stop(self):
        self.is_running = False


def parse_single_nfo(nfo_path):
    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()

        data = {
            'path': nfo_path,
            'num': '',
            'title': '',
            'plot': '',
            'series': '',
            'rating': 0.0,
            'release': '',
            'actors': [],
            'tags': [],
        }

        for field in ['num', 'title', 'plot', 'series']:
            elem = root.find(field)
            if elem is not None and elem.text:
                data[field] = elem.text.strip()

        rating_elem = root.find('rating')
        if rating_elem is not None and rating_elem.text:
            try:
                data['rating'] = float(rating_elem.text.strip())
            except ValueError:
                data['rating'] = 0.0

        release_elem = root.find('release')
        if release_elem is not None and release_elem.text:
            data['release'] = release_elem.text.strip()

        actors = []
        for actor in root.findall('actor'):
            name_elem = actor.find('name')
            if name_elem is not None and name_elem.text:
                actors.append(name_elem.text.strip())
        data['actors'] = actors

        tags = []
        for tag in root.findall('tag'):
            if tag is not None and tag.text:
                tags.append(tag.text.strip())
        data['tags'] = tags

        return data

    except Exception as e:
        print(f"解析NFO文件失败 {nfo_path}: {str(e)}")
        return None

# ================ 结束 ================


from NFO_Editor_ui import NFOEditorQt


class ConfigManager:
    def __init__(self):
        self.config_file = "settings.json"
        self.default_config = {
            "search_sites": {
                "predefined_sites": {
                    "supjav": True,
                    "subtitlecat": True,
                    "javdb": True,
                },
                "custom_sites": [
                    {"name": "", "url_template": "", "enabled": False},
                    {"name": "", "url_template": "", "enabled": False},
                    {"name": "", "url_template": "", "enabled": False}
                ]
            }
        }

    def load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                return self._merge_config(self.default_config, config)
            else:
                return self.default_config.copy()
        except Exception as e:
            print(f"加载配置文件失败: {e}")
            return self.default_config.copy()

    def save_config(self, config):
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"保存配置文件失败: {e}")
            return False

    def _merge_config(self, default, user_config):
        result = default.copy()
        for key, value in user_config.items():
            if key in result:
                if isinstance(value, dict) and isinstance(result[key], dict):
                    result[key] = self._merge_config(result[key], value)
                else:
                    result[key] = value
        return result


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        self.config_manager = ConfigManager()
        self.config = self.config_manager.load_config()

        self.setWindowTitle("NFO Editor - 设置")
        self.setFixedSize(600, 500)
        self.setWindowModality(Qt.ApplicationModal)

        self.setup_ui()
        self.load_current_settings()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)

        search_group = self.create_search_sites_group()
        scroll_layout.addWidget(search_group)
        scroll_layout.addStretch()

        scroll.setWidget(scroll_widget)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)

        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()

        self.apply_btn = QPushButton("应用")
        self.ok_btn = QPushButton("确定")
        self.cancel_btn = QPushButton("取消")

        buttons_layout.addWidget(self.apply_btn)
        buttons_layout.addWidget(self.ok_btn)
        buttons_layout.addWidget(self.cancel_btn)

        layout.addLayout(buttons_layout)

        self.apply_btn.clicked.connect(self.apply_settings)
        self.ok_btn.clicked.connect(self.accept_settings)
        self.cancel_btn.clicked.connect(self.reject)

    def create_search_sites_group(self):
        group = QGroupBox("番号搜索网站设置")
        layout = QVBoxLayout(group)

        predefined_label = QLabel("预设网站 (智能跳转详情页):")
        predefined_label.setStyleSheet("font-weight: bold; margin-top: 5px;")
        layout.addWidget(predefined_label)

        self.predefined_checkboxes = {}
        predefined_sites = {
            'supjav': 'SupJAV (立即打开)',
            'subtitlecat': 'SubtitleCat (立即打开)',
            'javdb': 'JAVDB (智能跳转)'
        }

        for site_id, site_name in predefined_sites.items():
            checkbox = QCheckBox(site_name)
            self.predefined_checkboxes[site_id] = checkbox
            layout.addWidget(checkbox)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        layout.addWidget(line)

        custom_label = QLabel("自定义网站 (打开搜索页面):")
        custom_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(custom_label)

        help_label = QLabel("URL模板示例: https://example.com/search/{number}")
        help_label.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(help_label)

        self.custom_site_widgets = []

        for i in range(3):
            site_layout = QHBoxLayout()

            enabled_cb = QCheckBox(f"自定义网站{i+1}")
            enabled_cb.setFixedWidth(120)

            name_edit = QLineEdit()
            name_edit.setPlaceholderText("网站名称")
            name_edit.setFixedWidth(100)

            url_edit = QLineEdit()
            url_edit.setPlaceholderText("https://example.com/search/{number}")

            site_layout.addWidget(enabled_cb)
            site_layout.addWidget(name_edit)
            site_layout.addWidget(url_edit)

            layout.addLayout(site_layout)

            self.custom_site_widgets.append({
                'enabled': enabled_cb,
                'name': name_edit,
                'url': url_edit
            })

            enabled_cb.stateChanged.connect(
                lambda state, widgets=(name_edit, url_edit): self.toggle_custom_site_inputs(state, widgets)
            )

        return group

    def toggle_custom_site_inputs(self, state, widgets):
        enabled = state == Qt.Checked
        for widget in widgets:
            widget.setEnabled(enabled)

    def load_current_settings(self):
        predefined_sites = self.config.get('search_sites', {}).get('predefined_sites', {})
        for site_id, checkbox in self.predefined_checkboxes.items():
            checkbox.setChecked(predefined_sites.get(site_id, False))

        custom_sites = self.config.get('search_sites', {}).get('custom_sites', [])
        for i, site_config in enumerate(custom_sites[:3]):
            if i < len(self.custom_site_widgets):
                widgets = self.custom_site_widgets[i]
                enabled = site_config.get('enabled', False)
                name = site_config.get('name', '')
                url = site_config.get('url_template', '')

                widgets['enabled'].setChecked(enabled)
                widgets['name'].setText(name)
                widgets['url'].setText(url)

                widgets['name'].setEnabled(enabled)
                widgets['url'].setEnabled(enabled)

    def get_current_settings(self):
        config = self.config.copy()

        predefined_sites = {}
        for site_id, checkbox in self.predefined_checkboxes.items():
            predefined_sites[site_id] = checkbox.isChecked()

        custom_sites = []
        for widgets in self.custom_site_widgets:
            custom_sites.append({
                'enabled': widgets['enabled'].isChecked(),
                'name': widgets['name'].text().strip(),
                'url_template': widgets['url'].text().strip()
            })

        config['search_sites'] = {
            'predefined_sites': predefined_sites,
            'custom_sites': custom_sites
        }

        return config

    def apply_settings(self):
        try:
            new_config = self.get_current_settings()

            for i, site in enumerate(new_config['search_sites']['custom_sites']):
                if site['enabled']:
                    if not site['name'] or not site['url_template']:
                        QMessageBox.warning(
                            self, "设置错误",
                            f"自定义网站{i+1}已启用但缺少网站名称或URL模板"
                        )
                        return
                    if '{number}' not in site['url_template']:
                        QMessageBox.warning(
                            self, "设置错误",
                            f"自定义网站{i+1}的URL模板必须包含 {{number}} 占位符"
                        )
                        return

            if self.config_manager.save_config(new_config):
                self.config = new_config
                if hasattr(self.parent, 'on_settings_changed'):
                    self.parent.on_settings_changed()
                QMessageBox.information(self, "成功", "设置已保存")
            else:
                QMessageBox.critical(self, "错误", "保存设置失败")

        except Exception as e:
            QMessageBox.critical(self, "错误", f"应用设置时出错: {str(e)}")

    def accept_settings(self):
        self.apply_settings()
        self.accept()


class FileOperationThread(QThread):
    """文件操作线程类"""

    progress = pyqtSignal(int, int)
    finished = pyqtSignal()
    error = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, operation_type, **kwargs):
        super().__init__()
        self.operation_type = operation_type
        self.kwargs = kwargs
        self.is_running = True

    def run(self):
        if self.operation_type == "move":
            self.move_files()

    def stop(self):
        self.is_running = False

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
                    if dest_path is None or folder_name is None:
                        raise Exception("目标路径或文件夹名称无效")
                    dest_folder_path = os.path.join(str(dest_path), str(folder_name))

                    if not os.path.exists(dest_path):
                        raise Exception(f"目标目录不存在: {dest_path}")

                    if (
                        os.path.splitdrive(src_path)[0]
                        == os.path.splitdrive(dest_path)[0]
                    ):
                        if os.path.exists(dest_folder_path):
                            try:
                                shutil.rmtree(dest_folder_path)
                            except Exception as e:
                                raise Exception(f"删除已存在的目标文件夹失败: {str(e)}")

                        try:
                            shutil.move(src_path, dest_folder_path)
                        except Exception as e:
                            raise Exception(f"移动文件夹失败: {str(e)}")
                    else:
                        if os.path.exists(dest_folder_path):
                            try:
                                shutil.rmtree(dest_folder_path)
                            except Exception as e:
                                raise Exception(f"删除已存在的目标文件夹失败: {str(e)}")

                        try:
                            shutil.copytree(src_path, dest_folder_path)
                            shutil.rmtree(src_path)
                        except Exception as e:
                            if os.path.exists(dest_folder_path):
                                shutil.rmtree(dest_folder_path)
                            raise Exception(f"复制并删除文件夹失败: {str(e)}")

                    self.progress.emit(i, total)
                    self.status.emit(f"正在处理: {folder_name}")

                except Exception as e:
                    self.error.emit(f"移动文件夹失败: {str(e)}")
                    continue

            self.finished.emit()

        except Exception as e:
            self.error.emit(f"操作过程中发生错误: {str(e)}")


class SearchEngine:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

    def search_javdb(self, num_text):
        try:
            search_url = f"https://javdb.com/search?q={num_text}&f=all"
            response = requests.get(search_url, headers=self.headers, timeout=10)

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'lxml')

                empty_message = soup.find('div', class_='empty-message')
                if empty_message:
                    print(f"JavDB: 没有找到 {num_text} 的搜索结果")
                    return None

                movie_list = soup.find('div', class_='movie-list')
                if movie_list:
                    items = movie_list.find_all('div', class_='item')

                    for item in items:
                        strong_tag = item.find('strong')
                        if strong_tag and strong_tag.text.strip().upper() == num_text.upper():
                            link_tag = item.find('a', class_='box')
                            if link_tag and link_tag.get('href'):
                                detail_url = f"https://javdb.com{link_tag['href']}"
                                print(f"JavDB: 找到详情页 {detail_url}")
                                return detail_url

                    print(f"JavDB: 没有找到完全匹配 {num_text} 的番号")
                else:
                    print(f"JavDB: 搜索页面格式可能已变更")
            else:
                print(f"JavDB: 访问失败，状态码: {response.status_code}")
            return None
        except Exception as e:
            print(f"JavDB搜索失败: {str(e)}")
            return None

    def search_javtrailers(self, num_text):
        try:
            search_url = f"https://javtrailers.com/search/{num_text}"
            response = requests.get(search_url, headers=self.headers, timeout=10)

            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'lxml')

                videos_section = soup.find('section', class_='videos-list')
                if videos_section:
                    video_links = videos_section.find_all('a', class_='video-link')

                    for link in video_links:
                        title_element = link.find('p', class_='vid-title')
                        if title_element:
                            title_text = title_element.text.strip()
                            if title_text.upper().startswith(num_text.upper() + ' '):
                                href = link.get('href')
                                if href:
                                    detail_url = f"https://javtrailers.com{href}"
                                    print(f"JavTrailers: 找到详情页 {detail_url}")
                                    return detail_url

                    print(f"JavTrailers: 没有找到完全匹配 {num_text} 的番号")
                else:
                    print(f"JavTrailers: 搜索页面格式可能已变更")
            else:
                print(f"JavTrailers: 访问失败，状态码: {response.status_code}")
            return None
        except Exception as e:
            print(f"JavTrailers搜索失败: {str(e)}")
            return None


class SearchSiteManager:
    """搜索网站管理器"""

    def handle_custom_site(self, url_template, num_text):
        try:
            search_url = url_template.replace('{number}', num_text)
            webbrowser.open(search_url)
            return True
        except Exception as e:
            print(f"打开自定义网站失败: {str(e)}")
            return False


class NFOEditorQt5(NFOEditorQt):
    def __init__(self):
        super().__init__()

        # 成员变量初始化
        self.nfo_files = []
        self.selected_index_cache = None
        self.move_thread = None
        self.file_watcher = QFileSystemWatcher()
        self._pending_select_folder = None   # 命令行 --select-folder 的延迟选择路径

        # 缓存和异步加载
        self.nfo_cache = NFOCache()
        self.load_thread = None
        self._show_progress = True  # 控制进度条显示

        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(300)
        self.progress_bar.hide()
        self.status_bar.addPermanentWidget(self.progress_bar)

        self.reload_timer = QTimer()
        self.reload_timer.setSingleShot(True)
        self.reload_timer.timeout.connect(self._delayed_reload)

        # 配置和搜索管理器
        self.config_manager = ConfigManager()
        self.search_site_manager = SearchSiteManager()

        # 默认勾选显示图片选项
        self.show_images_checkbox.setChecked(True)

        # 启用拖拽功能
        self.setAcceptDrops(True)

        # 连接信号槽
        self.setup_signals()

        # 恢复上次窗口状态
        self.restore_window_state()

        # 初始隐藏目标文件夹树
        self.sorted_tree.hide()

        # 获取主网格布局并保存原始列伸缩因子
        main_grid = self.centralWidget().layout()
        self.original_stretches = {
            0: main_grid.columnStretch(0),
            1: main_grid.columnStretch(1),
            2: main_grid.columnStretch(2),
        }
        main_grid.setColumnStretch(1, 0)

        # 删除快捷键
        QShortcut(QKeySequence("Delete"), self, self.delete_selected_folders)

    def setup_signals(self):
        """设置信号槽连接"""
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
                btn.clicked.connect(lambda: self.load_files_in_folder())
            elif text == "🖼":
                btn.clicked.connect(self.show_photo_wall)
            elif text == "🔜":
                btn.clicked.connect(self.start_move_thread)
            elif text == "⚙️":
                btn.clicked.connect(self.open_settings)
            elif text == "批量填充 (Batch Filling)":
                btn.clicked.connect(self.batch_filling)
            elif text == "批量新增 (Batch Add)":
                btn.clicked.connect(self.batch_add)

        self.show_images_checkbox.stateChanged.connect(self.toggle_image_display)

        self.file_tree.itemSelectionChanged.connect(self.on_file_select)
        self.file_tree.itemDoubleClicked.connect(self.on_file_double_click)

        self.file_tree.setStyleSheet("""
            QTreeWidget {
                selection-background-color: #3daee9;
                selection-color: white;
            }
            QTreeWidget::item:selected {
                background-color: #3daee9;
                color: white;
            }
            QTreeWidget::item:selected:!focus {
                background-color: #bfbfbf;
                color: black;
            }
        """)

        self.file_watcher.fileChanged.connect(self.on_file_changed)
        self.file_watcher.directoryChanged.connect(self.on_directory_changed)

        self.sorting_group.buttonClicked.connect(self.sort_files)

        self.setup_shortcuts()

        self.sorted_tree.itemDoubleClicked.connect(self.on_target_tree_double_click)

        if "rating" in self.fields_entries:
            self.fields_entries["rating"].installEventFilter(self)
            rating_widget = self.fields_entries["rating"]
            rating_widget.keyReleaseEvent = lambda event: self.on_rating_key_release(
                rating_widget, event
            )

        for field_name, widget in self.fields_entries.items():
            if isinstance(widget, QTextEdit):
                original_keyPressEvent = widget.keyPressEvent
                def make_keyPressEvent(original_func):
                    def new_keyPressEvent(event):
                        if event.key() == Qt.Key_Enter:
                            self.save_changes()
                            return
                        original_func(event)
                    return new_keyPressEvent
                widget.keyPressEvent = make_keyPressEvent(original_keyPressEvent)

        for btn in self.findChildren(QPushButton):
            if "保存更改" in btn.text():
                btn.clicked.connect(self.save_changes)
                break

        for btn in self.findChildren(QPushButton):
            if btn.text() == "筛选":
                btn.clicked.connect(self.apply_filter)
                break

        self.filter_entry.returnPressed.connect(self.apply_filter)

        if "num" in self.fields_entries:
            self.fields_entries["num"].mousePressEvent = lambda event: self.open_number_search(event)

        if hasattr(self, 'copy_num_button'):
            self.copy_num_button.clicked.connect(self.copy_number_to_clipboard)

        if hasattr(self, 'play_trailer_button'):
            self.play_trailer_button.clicked.connect(self.play_trailer)

    def eventFilter(self, obj, event):
        if (
            event.type() == event.KeyPress
            and isinstance(obj, QTextEdit)
            and obj == self.fields_entries.get("rating")
        ):
            if event.key() == Qt.Key_Left:
                self.focus_file_list()
                return True
            elif event.key() == Qt.Key_Right:
                obj.selectAll()
                return True

        return super().eventFilter(obj, event)

    def setup_shortcuts(self):
        QShortcut(QKeySequence("F5"), self, self.load_files_in_folder)
        QShortcut(QKeySequence("Ctrl+Right"), self, self.start_move_thread)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Left:
            focus_widget = self.focusWidget()
            if (
                isinstance(focus_widget, QTextEdit)
                and "rating" in self.fields_entries
                and self.fields_entries["rating"] == focus_widget
            ):
                event.accept()
                self.focus_file_list()
                return
        elif event.key() == Qt.Key_Right:
            focus_widget = self.focusWidget()
            if isinstance(focus_widget, QTreeWidget):
                event.accept()
                self.focus_rating()
                return

        super().keyPressEvent(event)

    def on_rating_key_release(self, widget, event):
        try:
            current_text = widget.toPlainText().strip()

            if not current_text:
                return

            key_text = event.text()

            if key_text.isdigit():
                if "." in current_text:
                    main_num = current_text.split(".")[0]
                    formatted_rating = f"{main_num}.{key_text}"

                    if float(formatted_rating) <= 9.9:
                        widget.setPlainText(formatted_rating)
                    else:
                        widget.setPlainText("9.9")
                elif current_text.isdigit():
                    formatted_rating = f"{float(current_text):.1f}"
                    widget.setPlainText(formatted_rating)

                cursor = widget.textCursor()
                cursor.movePosition(cursor.End)
                widget.setTextCursor(cursor)

        except Exception as e:
            print(f"处理评分输入时出错: {str(e)}")

        QTextEdit.keyReleaseEvent(widget, event)

    def open_settings(self):
        try:
            dialog = SettingsDialog(self)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.exec_()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开设置失败: {str(e)}")

    def on_settings_changed(self):
        pass

    def set_nfo_folder(self, folder_path):
        self.folder_path = folder_path
        settings = QSettings("NFOEditor", "Directories")
        settings.setValue("last_nfo_dir", folder_path)
        self.load_files_in_folder()

        if self.folder_path in self.file_watcher.directories():
            self.file_watcher.removePath(self.folder_path)
        self.file_watcher.addPath(self.folder_path)

    def open_folder(self):
        settings = QSettings("NFOEditor", "Directories")
        last_dir = settings.value("last_nfo_dir", "")

        folder_selected = QFileDialog.getExistingDirectory(
            self, "选择NFO文件夹", last_dir
        )

        if folder_selected:
            self.set_nfo_folder(folder_selected)

    def select_target_folder(self):
        settings = QSettings("NFOEditor", "Directories")
        last_target_dir = settings.value("last_target_dir", "")

        target_folder = QFileDialog.getExistingDirectory(
            self, "选择目标文件夹", last_target_dir
        )

        if target_folder:
            self.current_target_path = target_folder
            settings.setValue("last_target_dir", target_folder)
            self.load_target_files(target_folder)

            self.sorted_tree.show()
            main_grid = self.centralWidget().layout()
            main_grid.setColumnStretch(1, self.original_stretches[1])
        else:
            self.sorted_tree.hide()
            main_grid = self.centralWidget().layout()
            main_grid.setColumnStretch(1, 0)

    def clear_target_folder(self):
        self.current_target_path = None
        self.sorted_tree.clear()
        self.sorted_tree.hide()

        main_grid = self.centralWidget().layout()
        main_grid.setColumnStretch(1, 0)

        self.status_bar.showMessage("目标目录已清除")

    # ================================================================
    #  核心加载逻辑 - 修复进度条闪烁 & 旧线程信号残留
    # ================================================================

    def load_files_in_folder(self, auto_select=True, show_progress=True):
        """加载文件夹中的NFO文件 - 异步版本

        show_progress=False 时静默重载，不显示/隐藏进度条（用于移动后的自动刷新）
        """
        if not self.folder_path:
            return

        current_selection_path = None
        if not auto_select:
            selected_items = self.file_tree.selectedItems()
            if selected_items and self.current_file_path:
                current_selection_path = self.current_file_path

        # 停止旧线程并断开所有信号，防止残留回调污染新数据
        if self.load_thread is not None and self.load_thread.isRunning():
            self.load_thread.stop()
            try:
                self.load_thread.item_ready.disconnect()
                self.load_thread.progress.disconnect()
                self.load_thread.finished_signal.disconnect()
                self.load_thread.error.disconnect()
            except Exception:
                pass
            self.load_thread.wait()

        self.file_tree.clear()
        self.nfo_files = []
        self.nfo_cache.clear()  # 清空旧缓存，避免已删除文件残留

        self._show_progress = show_progress

        if show_progress:
            self.progress_bar.setValue(0)
            self.progress_bar.show()
            self.status_bar.showMessage("正在加载文件...")

        self.load_thread = LoadFilesThread(self.folder_path, batch_size=100)
        self.load_thread.progress.connect(self._on_load_progress)
        self.load_thread.item_ready.connect(self._on_item_ready)
        self.load_thread.finished_signal.connect(
            lambda count: self._on_load_finished(count, auto_select, current_selection_path)
        )
        self.load_thread.error.connect(self._on_load_error)
        self.load_thread.start()

    def _on_load_progress(self, current, total, filename):
        if not self._show_progress:
            return
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        if current % 100 == 0 or current == total:
            self.status_bar.showMessage(f"正在加载: {current}/{total} - {filename}")

    def _on_item_ready(self, tree_item, cache_data):
        self.file_tree.addTopLevelItem(tree_item)
        for path, data in cache_data.items():
            self.nfo_cache.set(path, data)
            self.nfo_files.append(path)

    def _on_load_finished(self, count, auto_select, selection_path):
        if self._show_progress:
            self.progress_bar.hide()

        total_folders = len(set(os.path.dirname(f) for f in self.nfo_files))
        self.status_bar.showMessage(
            f"加载完成: {count} 个NFO文件 ({total_folders} 个文件夹) - 目录: {self.folder_path}"
        )

        if auto_select and self.file_tree.topLevelItemCount() > 0:
            first_item = self.file_tree.topLevelItem(0)
            self.file_tree.setCurrentItem(first_item)
            self.on_file_select()
        elif selection_path:
            self._restore_selection(selection_path)

        # 处理命令行 --select-folder 的延迟选择（加载完成后再定位，避免异步时序问题）
        if self._pending_select_folder:
            pending = self._pending_select_folder
            self._pending_select_folder = None
            self.select_folder_in_tree(pending)

        if self.load_thread:
            self.load_thread.deleteLater()
            self.load_thread = None

    def _restore_selection(self, target_path):
        for i in range(self.file_tree.topLevelItemCount()):
            item = self.file_tree.topLevelItem(i)
            values = [item.text(j) for j in range(3)]

            if values[2]:
                item_path = (
                    os.path.join(self.folder_path, values[0], values[1], values[2])
                    if values[1]
                    else os.path.join(self.folder_path, values[0], values[2])
                )

                if os.path.normpath(item_path) == os.path.normpath(target_path):
                    self.file_tree.setCurrentItem(item)
                    self.file_tree.scrollToItem(item)
                    return

        self.file_tree.clearSelection()

    def _on_load_error(self, error_msg):
        if self._show_progress:
            self.progress_bar.hide()
        self.status_bar.showMessage(f"加载失败: {error_msg}")
        QMessageBox.critical(self, "错误", error_msg)

    # ================================================================
    #  文件监控
    # ================================================================

    def on_file_changed(self, path):
        """文件变化响应 - 更新缓存（外部修改触发，保存时已屏蔽）"""
        if path == self.current_file_path:
            cache_data = parse_single_nfo(path)
            if cache_data:
                self.nfo_cache.set(path, cache_data)
            self.load_nfo_fields()

    def on_directory_changed(self, path):
        """目录变化响应 - 防抖动延迟重载"""
        if path == self.folder_path:
            self.reload_timer.start(500)

    def _delayed_reload(self):
        """延迟重新加载（防抖动，静默模式）"""
        if self.folder_path:
            self.load_files_in_folder(auto_select=False, show_progress=False)

    # ================================================================
    #  文件选择与字段加载
    # ================================================================

    def on_file_select(self):
        selected_items = self.file_tree.selectedItems()
        if not selected_items:
            return

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

        item = selected_items[0]
        values = [item.text(i) for i in range(3)]

        if values[2]:
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
        for entry in self.fields_entries.values():
            if isinstance(entry, QTextEdit):
                entry.clear()
            elif isinstance(entry, QLabel):
                entry.setText("")

        try:
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            for field in ["title", "plot", "series", "rating", "num"]:
                elem = root.find(field)
                if elem is not None and elem.text:
                    widget = self.fields_entries.get(field)
                    if widget:
                        if isinstance(widget, QLabel):
                            widget.setText(elem.text)
                        else:
                            widget.setPlainText(elem.text)

            actors = [
                actor.find("name").text.strip()
                for actor in root.findall("actor")
                if actor.find("name") is not None and actor.find("name").text
            ]
            self.fields_entries["actors"].setPlainText(", ".join(actors))

            tags = [
                tag.text.strip()
                for tag in root.findall("tag")
                if tag is not None and tag.text
            ]
            self.fields_entries["tags"].setPlainText(", ".join(tags))

            release_elem = root.find("release")
            if release_elem is not None and release_elem.text:
                self.release_label.setText(release_elem.text.strip())

        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载NFO文件失败: {str(e)}")

    # ================================================================
    #  保存 - 屏蔽监控避免回调干扰
    # ================================================================

    def save_changes(self):
        if not self.current_file_path:
            return

        try:
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            title = self.fields_entries["title"].toPlainText().strip()
            plot = self.fields_entries["plot"].toPlainText().strip()
            actors_text = self.fields_entries["actors"].toPlainText().strip()
            series = self.fields_entries["series"].toPlainText().strip()
            tags_text = self.fields_entries["tags"].toPlainText().strip()
            rating = self.fields_entries["rating"].toPlainText().strip()

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

            try:
                rating_value = float(rating)
                critic_rating = int(rating_value * 10)
                critic_elem = root.find("criticrating")
                if critic_elem is None:
                    critic_elem = ET.SubElement(root, "criticrating")
                critic_elem.text = str(critic_rating)
            except ValueError:
                pass

            for actor_elem in root.findall("actor"):
                root.remove(actor_elem)
            for actor in actors_text.split(","):
                actor = actor.strip()
                if actor:
                    actor_elem = ET.SubElement(root, "actor")
                    name_elem = ET.SubElement(actor_elem, "name")
                    name_elem.text = actor

            for tag_elem in root.findall("tag"):
                root.remove(tag_elem)
            for genre_elem in root.findall("genre"):
                root.remove(genre_elem)

            for tag in tags_text.split(","):
                tag = tag.strip()
                if tag:
                    tag_elem = ET.SubElement(root, "tag")
                    tag_elem.text = tag
                    genre_elem = ET.SubElement(root, "genre")
                    genre_elem.text = tag

            xml_str = ET.tostring(root, encoding="utf-8")
            parsed_str = minidom.parseString(xml_str)
            pretty_str = parsed_str.toprettyxml(indent="  ", encoding="utf-8")
            pretty_str = "\n".join(
                line for line in pretty_str.decode("utf-8").split("\n") if line.strip()
            )

            # 保存前临时断开文件变化信号，防止 on_file_changed 在保存过程中干扰 UI
            try:
                self.file_watcher.fileChanged.disconnect(self.on_file_changed)
            except Exception:
                pass

            with open(self.current_file_path, "w", encoding="utf-8") as file:
                file.write(pretty_str)

            # 保存后立即更新缓存
            cache_data = parse_single_nfo(self.current_file_path)
            if cache_data:
                self.nfo_cache.set(self.current_file_path, cache_data)

            # 恢复文件变化信号
            self.file_watcher.fileChanged.connect(self.on_file_changed)

            save_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.save_time_label.setText(f"保存时间: {save_time}")

            if self.selected_index_cache:
                for item_id in self.selected_index_cache:
                    self.file_tree.setCurrentItem(item_id)

        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存NFO文件失败: {str(e)}")

    # ================================================================
    #  移动文件 - 修复进度条闪烁
    # ================================================================

    def start_move_thread(self):
        try:
            selected_items = self.file_tree.selectedItems()
            if not selected_items:
                QMessageBox.warning(self, "警告", "请先选择要移动的文件夹")
                return

            if not self.current_target_path:
                QMessageBox.critical(self, "错误", "请先选择目标目录")
                return

            src_paths = []
            for item in selected_items:
                try:
                    values = [item.text(i) for i in range(3)]
                    if values[1]:
                        src_path = os.path.join(self.folder_path, values[0], values[1])
                    else:
                        src_path = os.path.join(self.folder_path, values[0])

                    if not os.path.exists(src_path):
                        raise FileNotFoundError(f"源文件夹不存在: {src_path}")

                    src_paths.append(src_path)
                except Exception as e:
                    QMessageBox.warning(self, "警告", f"处理路径时出错: {str(e)}")
                    continue

            if not src_paths:
                QMessageBox.warning(self, "警告", "没有有效的源文件夹可以移动")
                return

            # 移动前暂停目录监控，防止移动过程中触发自动重载
            if self.folder_path and self.folder_path in self.file_watcher.directories():
                self.file_watcher.removePath(self.folder_path)

            progress = QProgressDialog("准备移动...", "取消", 0, len(src_paths), self)
            progress.setWindowModality(Qt.WindowModal)
            progress.setAutoClose(True)
            progress.setAutoReset(True)

            if self.move_thread is not None and self.move_thread.isRunning():
                self.move_thread.stop()
                self.move_thread.wait()

            self.move_thread = FileOperationThread(
                operation_type="move",
                src_paths=src_paths,
                dest_path=self.current_target_path,
            )

            self.move_thread.progress.connect(progress.setValue)
            self.move_thread.status.connect(progress.setLabelText)
            self.move_thread.error.connect(
                lambda msg: QMessageBox.critical(self, "错误", msg)
            )
            self.move_thread.finished.connect(self.on_move_finished)
            progress.canceled.connect(self.move_thread.stop)

            self.move_thread.start()

        except Exception as e:
            # 异常时恢复监控
            if self.folder_path:
                self.file_watcher.addPath(self.folder_path)
            QMessageBox.critical(self, "错误", f"启动移动操作时出错: {str(e)}")

    def on_move_finished(self):
        """文件移动完成回调 - 静默重载，不显示进度条"""
        # 取消可能挂起的延迟重载（防止与主动重载重复）
        self.reload_timer.stop()

        # 静默重载文件列表
        self.load_files_in_folder(auto_select=False, show_progress=False)

        # 刷新目标目录
        if self.current_target_path:
            self.load_target_files(self.current_target_path)

        # 恢复目录监控
        if self.folder_path:
            self.file_watcher.addPath(self.folder_path)

        # 清理线程
        if self.move_thread:
            self.move_thread.deleteLater()
            self.move_thread = None

    # ================================================================
    #  目标目录
    # ================================================================

    def load_target_files(self, target_path):
        self.sorted_tree.clear()
        try:
            if os.path.dirname(target_path) != target_path:
                parent_item = QTreeWidgetItem([".."])
                parent_item.setIcon(
                    0, self.style().standardIcon(self.style().SP_ArrowUp)
                )
                self.sorted_tree.addTopLevelItem(parent_item)

            for entry in os.scandir(target_path):
                if entry.is_dir():
                    item = QTreeWidgetItem([entry.name])
                    item.setIcon(0, self.style().standardIcon(self.style().SP_DirIcon))
                    self.sorted_tree.addTopLevelItem(item)

            folder_count = self.sorted_tree.topLevelItemCount()
            if ".." in [
                self.sorted_tree.topLevelItem(i).text(0) for i in range(folder_count)
            ]:
                folder_count -= 1

            self.status_bar.showMessage(f"目标目录: {target_path} (共{folder_count}个文件夹)")

        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载目标目录失败: {str(e)}")

    # ================================================================
    #  未保存检测
    # ================================================================

    def has_unsaved_changes(self):
        if not self.current_file_path or not os.path.exists(self.current_file_path):
            return False

        try:
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            for field in ["title", "plot", "series", "rating"]:
                current_value = self.fields_entries[field].toPlainText().strip()
                elem = root.find(field)
                original_value = (
                    elem.text.strip() if elem is not None and elem.text else ""
                )
                if current_value != original_value:
                    return True

            current_actors = set(
                actor.strip()
                for actor in self.fields_entries["actors"].toPlainText().strip().split(",")
                if actor.strip()
            )
            original_actors = {
                actor.find("name").text.strip()
                for actor in root.findall("actor")
                if actor.find("name") is not None and actor.find("name").text
            }
            if current_actors != original_actors:
                return True

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
                return True

            return False

        except Exception as e:
            print(f"检查更改状态时出错: {str(e)}")
            return False

    # ================================================================
    #  图片显示
    # ================================================================

    def toggle_image_display(self):
        if self.show_images_checkbox.isChecked():
            self.display_image()
        else:
            self.clear_images()

    def clear_images(self):
        if hasattr(self, "poster_label"):
            self.poster_label.clear()
            self.poster_label.setText("封面图 (poster)")
        if hasattr(self, "thumb_label"):
            self.thumb_label.clear()
            self.thumb_label.setText("缩略图 (thumb)")
        if hasattr(self, "poster_resolution_label"):
            self.poster_resolution_label.setText("分辨率: 未知")
        if hasattr(self, "thumb_resolution_label"):
            self.thumb_resolution_label.setText("分辨率: 未知")

    def display_image(self):
        if not self.current_file_path:
            return

        folder = os.path.dirname(self.current_file_path)

        poster_files = []
        thumb_files = []
        fanart_files = []
        for entry in os.scandir(folder):
            name = entry.name.lower()
            if name.endswith(".jpg"):
                if "poster" in name:
                    poster_files.append(entry.name)
                elif "thumb" in name:
                    thumb_files.append(entry.name)
                elif "fanart" in name:
                    fanart_files.append(entry.name)

        if poster_files:
            poster_path = os.path.join(folder, poster_files[0])
            self.load_image(poster_path, self.poster_label, self.poster_resolution_label)
        else:
            self.poster_label.setText("文件夹内无poster图片")
            self.poster_resolution_label.setText("分辨率: 未知")

        if thumb_files:
            thumb_path = os.path.join(folder, thumb_files[0])
            self.load_image(thumb_path, self.thumb_label, self.thumb_resolution_label)
        elif fanart_files:
            fanart_path = os.path.join(folder, fanart_files[0])
            self.load_image(fanart_path, self.thumb_label, self.thumb_resolution_label)
        else:
            self.thumb_label.setText("文件夹内无thumb或fanart图片")
            self.thumb_resolution_label.setText("分辨率: 未知")

    def load_image(self, image_path, label, resolution_label=None):
        try:
            with Image.open(image_path) as img:
                original_width, original_height = img.size

            if resolution_label:
                resolution_label.setText(f"分辨率: {original_width} × {original_height}")

            pixmap = QPixmap(image_path)
            if pixmap.isNull():
                label.setText("加载图片失败")
                if resolution_label:
                    resolution_label.setText("分辨率: 加载失败")
                return

            scaled_pixmap = pixmap.scaled(
                label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            label.setPixmap(scaled_pixmap)

        except Exception as e:
            label.setText(f"加载图片失败: {str(e)}")
            if resolution_label:
                resolution_label.setText("分辨率: 加载失败")

    # ================================================================
    #  排序 & 筛选
    # ================================================================

    def sort_files(self):
        if not self.sorting_group.checkedButton():
            return

        if self.nfo_cache.size() == 0:
            return

        sort_by = self.sorting_group.checkedButton().text()

        items_with_data = []
        for i in range(self.file_tree.topLevelItemCount()):
            item = self.file_tree.topLevelItem(i)
            values = [item.text(j) for j in range(3)]

            if values[2]:
                nfo_path = (
                    os.path.join(self.folder_path, values[0], values[1], values[2])
                    if values[1]
                    else os.path.join(self.folder_path, values[0], values[2])
                )

                cache_data = self.nfo_cache.get(nfo_path)
                if cache_data:
                    items_with_data.append((values, item, cache_data))

        def get_sort_key(item_tuple):
            values, tree_item, cache_data = item_tuple

            if "演员" in sort_by:
                return (", ".join(sorted(cache_data.get('actors', []) or [])),)
            elif "系列" in sort_by:
                series = cache_data.get('series', '') or ''
                # (-count, series_name) 使作品多的系列排前面，空系列排末尾
                return (1 if not series else 0,
                        -series_counts.get(series, 0),
                        series)
            elif "评分" in sort_by:
                try:
                    rating = cache_data.get('rating', 0.0)
                    return (-float(rating) if rating else 0.0,)
                except (ValueError, TypeError):
                    return (0.0,)
            else:
                release = cache_data.get('release', '') or '0000-00-00'
                return (release,)

        # 系列排序需要预先统计每个系列的影片数
        series_counts: dict = {}
        if "系列" in sort_by:
            for _, _, cd in items_with_data:
                s = cd.get('series', '') or ''
                series_counts[s] = series_counts.get(s, 0) + 1

        try:
            # 评分/日期已在 get_sort_key 内取负，统一升序排即可
            reverse = False
            if "日期" in sort_by:
                # 日期需要降序（新日期在前），key 没取负，用 reverse=True
                reverse = True
            items_with_data.sort(key=get_sort_key, reverse=reverse)
        except Exception as e:
            print(f"排序出错: {str(e)}")
            QMessageBox.warning(self, "警告", f"排序失败: {str(e)}")
            return

        self.file_tree.clear()
        for values, item, cache_data in items_with_data:
            new_item = QTreeWidgetItem(values)
            self.file_tree.addTopLevelItem(new_item)

        self.status_bar.showMessage(f"已按 {sort_by} 排序", 3000)

    def apply_filter(self):
        if not self.folder_path:
            return

        field = self.field_combo.currentText()
        condition = self.condition_combo.currentText()
        filter_text = self.filter_entry.text().strip()

        if not filter_text:
            self.load_files_in_folder()
            return

        self.file_tree.clear()
        matched_paths = []

        for nfo_path in self.nfo_files:
            cache_data = self.nfo_cache.get(nfo_path)
            if not cache_data:
                continue

            try:
                value = ""
                if field == "标题":
                    value = cache_data.get('title', '')
                elif field == "标签":
                    value = ", ".join(cache_data.get('tags', []))
                elif field == "演员":
                    value = ", ".join(cache_data.get('actors', []))
                elif field == "系列":
                    value = cache_data.get('series', '')
                elif field == "评分":
                    rating = cache_data.get('rating', 0.0)
                    try:
                        value = str(float(rating) if rating else 0.0)
                    except (ValueError, TypeError):
                        value = "0.0"

                match = False
                if field == "评分":
                    try:
                        current_value = float(value)
                        filter_value = float(filter_text)
                        if condition == "大于":
                            match = current_value > filter_value
                        elif condition == "小于":
                            match = current_value < filter_value
                    except ValueError:
                        continue
                else:
                    if condition == "包含":
                        match = filter_text.lower() in value.lower()
                    elif condition == "不包含":
                        match = filter_text.lower() not in value.lower()

                if match:
                    matched_paths.append(nfo_path)

            except Exception as e:
                print(f"筛选文件 {nfo_path} 时出错: {str(e)}")
                continue

        for nfo_path in matched_paths:
            relative_path = os.path.relpath(nfo_path, self.folder_path)
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

        self.status_bar.showMessage(
            f"筛选结果: 匹配 {len(matched_paths)} / 总计 {len(self.nfo_files)}"
        )

    # ================================================================
    #  批量操作 - 操作后同步更新缓存
    # ================================================================

    def batch_filling(self):
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.setWindowTitle("批量填充")
        dialog.resize(400, 600)

        layout = QVBoxLayout()
        dialog.setLayout(layout)

        from PyQt5.QtWidgets import QRadioButton
        layout.addWidget(QLabel("选择填充替换字段:"))
        field_buttons = []
        for field in ["series", "rating", "actor"]:
            rb = QRadioButton(field)
            if not field_buttons:
                rb.setChecked(True)
            field_buttons.append(rb)
            layout.addWidget(rb)

        layout.addWidget(QLabel("填充替换值:"))
        value_entry = QLineEdit()
        layout.addWidget(value_entry)

        log_text = QTextEdit()
        layout.addWidget(log_text)

        original_key_release = value_entry.keyReleaseEvent

        def format_rating_input(widget, event):
            try:
                current_text = widget.text().strip()
                if not current_text:
                    original_key_release(event)
                    return

                key_text = event.text()
                if key_text.isdigit():
                    if "." in current_text:
                        main_num = current_text.split(".")[0]
                        formatted_rating = f"{main_num}.{key_text}"
                        try:
                            if float(formatted_rating) <= 9.9:
                                widget.setText(formatted_rating)
                            else:
                                widget.setText("9.9")
                        except ValueError:
                            pass
                    elif current_text.isdigit():
                        try:
                            formatted_rating = f"{float(current_text):.1f}"
                            widget.setText(formatted_rating)
                        except ValueError:
                            pass
                    widget.setCursorPosition(len(widget.text()))
            except Exception as e:
                print(f"处理评分输入时出错: {str(e)}")
            original_key_release(event)

        def on_field_changed():
            selected_field = None
            for rb in field_buttons:
                if rb.isChecked():
                    selected_field = rb.text()
                    break

            if selected_field == "rating":
                value_entry.keyReleaseEvent = lambda event: format_rating_input(value_entry, event)
                value_entry.setPlaceholderText("输入评分 (如: 8.5)")
            else:
                value_entry.keyReleaseEvent = original_key_release
                if selected_field == "actor":
                    value_entry.setPlaceholderText("输入演员名，多个用逗号分隔")
                else:
                    value_entry.setPlaceholderText("输入填充值")
            value_entry.setFocus()

        def apply_fill():
            field = None
            for rb in field_buttons:
                if rb.isChecked():
                    field = rb.text()
                    break

            if not field:
                return

            fill_value = value_entry.text().strip()
            if not fill_value:
                return

            selected_items = self.file_tree.selectedItems()
            if not selected_items:
                QMessageBox.warning(dialog, "警告", "请先选择要填充的文件")
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

                    if field == "actor":
                        for actor_elem in root.findall("actor"):
                            root.remove(actor_elem)
                        for actor_name in fill_value.split(","):
                            actor_name = actor_name.strip()
                            if actor_name:
                                actor_elem = ET.SubElement(root, "actor")
                                name_elem = ET.SubElement(actor_elem, "name")
                                name_elem.text = actor_name
                        operation_log.append(f"{nfo_path}: actor字段填充成功")

                    elif field == "rating":
                        rating_elem = root.find("rating")
                        if rating_elem is None:
                            rating_elem = ET.SubElement(root, "rating")
                        rating_elem.text = fill_value

                        try:
                            rating_value = float(fill_value)
                            critic_rating = int(rating_value * 10)
                            critic_elem = root.find("criticrating")
                            if critic_elem is None:
                                critic_elem = ET.SubElement(root, "criticrating")
                            critic_elem.text = str(critic_rating)
                            operation_log.append(
                                f"{nfo_path}: rating填充成功 (rating: {fill_value}, criticrating: {critic_rating})"
                            )
                        except ValueError:
                            operation_log.append(f"{nfo_path}: rating填充成功，但criticrating转换失败")

                    else:
                        elem = root.find(field)
                        if elem is None:
                            elem = ET.SubElement(root, field)
                        elem.text = fill_value
                        operation_log.append(f"{nfo_path}: {field}字段填充成功")

                    xml_str = ET.tostring(root, encoding="utf-8")
                    parsed_str = minidom.parseString(xml_str)
                    pretty_str = parsed_str.toprettyxml(indent="  ", encoding="utf-8")
                    pretty_str = "\n".join(
                        line for line in pretty_str.decode("utf-8").split("\n") if line.strip()
                    )

                    with open(nfo_path, "w", encoding="utf-8") as f:
                        f.write(pretty_str)

                    # 同步更新缓存
                    cache_data = parse_single_nfo(nfo_path)
                    if cache_data:
                        self.nfo_cache.set(nfo_path, cache_data)

                except Exception as e:
                    operation_log.append(f"{nfo_path}: {field}字段填充失败 - {str(e)}")

            log_text.setText("\n".join(operation_log))

            if self.current_file_path:
                self.load_nfo_fields()

        for rb in field_buttons:
            rb.toggled.connect(on_field_changed)

        apply_button = QPushButton("应用填充")
        apply_button.clicked.connect(apply_fill)
        layout.addWidget(apply_button)

        on_field_changed()
        value_entry.returnPressed.connect(apply_fill)

        dialog.exec_()

    def batch_add(self):
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.setWindowTitle("批量新增标签")
        dialog.resize(400, 500)

        layout = QVBoxLayout()
        dialog.setLayout(layout)

        layout.addWidget(QLabel("为选中的NFO文件批量新增标签:"))
        layout.addWidget(QLabel("(将同时添加到tag和genre字段)"))
        layout.addWidget(QLabel("多个标签请用逗号分隔"))

        layout.addWidget(QLabel("输入新增标签:"))
        value_entry = QLineEdit()
        value_entry.setPlaceholderText("例如: 新标签1, 新标签2, 新标签3")
        layout.addWidget(value_entry)

        log_text = QTextEdit()
        layout.addWidget(log_text)

        def apply_add():
            add_value = value_entry.text().strip()
            if not add_value:
                QMessageBox.warning(dialog, "警告", "请输入标签内容")
                return

            selected_items = self.file_tree.selectedItems()
            if not selected_items:
                QMessageBox.warning(dialog, "警告", "请先选择要新增的文件")
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

                    existing_tags = []
                    for tag in root.findall("tag"):
                        if tag is not None and tag.text:
                            tag_text = tag.text.strip()
                            if "," in tag_text:
                                for sub_tag in tag_text.split(","):
                                    sub_tag = sub_tag.strip()
                                    if sub_tag:
                                        existing_tags.append(sub_tag)
                            else:
                                existing_tags.append(tag_text)

                    new_tags = []
                    for tag in add_value.split(","):
                        tag = tag.strip()
                        if tag and tag not in existing_tags:
                            new_tags.append(tag)

                    if not new_tags:
                        operation_log.append(f"{nfo_path}: 所有标签已存在，跳过")
                        continue

                    all_tags = existing_tags + new_tags

                    for tag_elem in root.findall("tag"):
                        root.remove(tag_elem)
                    for genre_elem in root.findall("genre"):
                        root.remove(genre_elem)

                    for tag in all_tags:
                        tag_elem = ET.SubElement(root, "tag")
                        tag_elem.text = tag

                    for tag in all_tags:
                        genre_elem = ET.SubElement(root, "genre")
                        genre_elem.text = tag

                    xml_str = ET.tostring(root, encoding="utf-8")
                    parsed_str = minidom.parseString(xml_str)
                    pretty_str = parsed_str.toprettyxml(indent="  ", encoding="utf-8")
                    pretty_str = "\n".join(
                        line for line in pretty_str.decode("utf-8").split("\n") if line.strip()
                    )

                    with open(nfo_path, "w", encoding="utf-8") as f:
                        f.write(pretty_str)

                    # 同步更新缓存
                    cache_data = parse_single_nfo(nfo_path)
                    if cache_data:
                        self.nfo_cache.set(nfo_path, cache_data)

                    operation_log.append(f"{nfo_path}: 成功新增{len(new_tags)}个标签")

                except Exception as e:
                    operation_log.append(f"{nfo_path}: 标签新增失败 - {str(e)}")

            log_text.setText("\n".join(operation_log))

            if self.current_file_path:
                self.load_nfo_fields()

        button_layout = QHBoxLayout()
        apply_button = QPushButton("应用新增")
        apply_button.clicked.connect(apply_add)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(dialog.close)
        button_layout.addWidget(apply_button)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)

        value_entry.returnPressed.connect(apply_add)
        value_entry.setFocus()

        dialog.exec_()

    # ================================================================
    #  文件操作
    # ================================================================

    def open_selected_nfo(self):
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
                    os.startfile(nfo_path)
                else:
                    QMessageBox.critical(self, "错误", f"NFO文件不存在: {nfo_path}")

    def open_selected_folder(self):
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
                    os.startfile(os.path.dirname(nfo_path))
                else:
                    QMessageBox.critical(self, "错误", f"文件夹不存在: {os.path.dirname(nfo_path)}")

    def open_selected_video(self):
        video_extensions = [".mp4", ".mkv", ".avi", ".mov", ".rm", ".mpeg", ".ts", ".strm"]
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
                                self._play_strm(video_path)
                            else:
                                try:
                                    subprocess.Popen(["mpvnet", video_path])
                                except OSError as e:
                                    QMessageBox.critical(self, "错误", f"启动 mpvnet 失败: {e}")
                            return

                    QMessageBox.warning(self, "警告", "未找到匹配的视频文件")
                else:
                    QMessageBox.critical(self, "错误", f"NFO文件不存在: {nfo_path}")

    def play_trailer(self):
        if not self.current_file_path:
            QMessageBox.warning(self, "警告", "请先选择NFO文件")
            return

        try:
            folder = os.path.dirname(self.current_file_path)
            num_text = self.fields_entries["num"].text().strip()
            if not num_text:
                QMessageBox.warning(self, "警告", "番号为空")
                return

            trailer_extensions = [".mp4", ".mkv", ".avi", ".mov", ".rm", ".mpeg", ".ts", ".strm"]
            trailer_files = []

            for file in os.listdir(folder):
                file_lower = file.lower()
                if "trailer" in file_lower:
                    for ext in trailer_extensions:
                        if file_lower.endswith(ext):
                            trailer_files.append(os.path.join(folder, file))
                            break

            if trailer_files:
                trailer_path = trailer_files[0]
                if trailer_path.lower().endswith(".strm"):
                    if self._play_strm(trailer_path):
                        self.status_bar.showMessage(
                            f"正在播放预告片: {os.path.basename(trailer_path)}", 3000
                        )
                else:
                    try:
                        subprocess.Popen(["mpvnet", trailer_path])
                        self.status_bar.showMessage(
                            f"正在播放预告片: {os.path.basename(trailer_path)}", 3000
                        )
                    except OSError as e:
                        QMessageBox.critical(self, "错误", f"启动 mpvnet 失败: {e}")
            else:
                search_engine = SearchEngine()

                try:
                    print("本地未找到预告片，搜索JavTrailers...")
                    javtrailers_url = search_engine.search_javtrailers(num_text)
                    if javtrailers_url:
                        webbrowser.open(javtrailers_url)
                        self.status_bar.showMessage("已打开JavTrailers详情页", 3000)
                        return
                except Exception as e:
                    print(f"JavTrailers搜索出错: {str(e)}")

                try:
                    print("JavTrailers未找到详情页，尝试JavDB...")
                    javdb_url = search_engine.search_javdb(num_text)
                    if javdb_url:
                        webbrowser.open(javdb_url)
                        self.status_bar.showMessage("已打开JavDB详情页", 3000)
                        return
                except Exception as e:
                    print(f"JavDB搜索出错: {str(e)}")

                try:
                    fallback_url = f"https://javdb.com/search?q={num_text}&f=all"
                    webbrowser.open(fallback_url)
                    self.status_bar.showMessage("已打开JavDB搜索页面", 3000)
                except Exception as e:
                    print(f"打开JavDB搜索页面失败: {str(e)}")
                    self.status_bar.showMessage("预告片搜索失败", 3000)

        except Exception as e:
            QMessageBox.critical(self, "错误", f"播放预告片失败: {str(e)}")

    # ================================================================
    #  STRM 播放辅助 - 自动附加同名字幕
    # ================================================================

    def _play_strm(self, strm_path: str) -> bool:
        """读取 STRM 并调用 mpvnet 播放，自动附加同目录下的同名字幕文件。

        字幕匹配规则：文件名以 <strm_stem> 开头，且紧跟 "." 或到达末尾，
        扩展名为常见字幕格式。
        例如 JUR-687-破解-C.strm 可匹配：
            JUR-687-破解-C.srt
            JUR-687-破解-C.chs.srt
            JUR-687-破解-C.zh.ass
        不会匹配：
            JUR-687-破解-CD.srt（紧跟字符不是 "."）
            JUR-687-破解-C-commentary.srt（紧跟字符不是 "."）

        返回 True 表示成功启动播放，False 表示出错已弹窗提示。
        """
        SUBTITLE_EXTS = (".srt", ".ass", ".ssa", ".vtt", ".sup")

        try:
            with open(strm_path, "r", encoding="utf-8") as f:
                strm_url = f.readline().strip()
        except OSError as e:
            QMessageBox.critical(self, "错误", f"读取 STRM 文件失败: {e}")
            return False

        if not strm_url:
            QMessageBox.critical(self, "错误", "STRM 文件内容为空或无效")
            return False

        folder = os.path.dirname(strm_path)
        # base_name 不含任何扩展名，如 "JUR-687-破解-C"
        base_name = os.path.splitext(os.path.basename(strm_path))[0]

        cmd = ["mpvnet", strm_url]
        try:
            for entry in os.scandir(folder):
                name = entry.name
                # 扩展名必须是字幕格式（大小写不敏感）
                if not name.lower().endswith(SUBTITLE_EXTS):
                    continue
                # base_name 后紧跟 "." 才算匹配，防止前缀相同但文件名不同的文件误入
                # 例如 "JUR-687-破解-C." 开头才匹配，"JUR-687-破解-CD." 不匹配
                rest = name[len(base_name):]
                if name.startswith(base_name) and rest.startswith("."):
                    cmd.append(f"--sub-file={entry.path}")
        except OSError as e:
            # 扫描失败不影响播放，仅打印日志
            print(f"扫描字幕文件失败: {e}")

        try:
            subprocess.Popen(cmd)
            return True
        except OSError as e:
            QMessageBox.critical(self, "错误", f"启动 mpvnet 失败: {e}")
            return False

    # ================================================================
    #  番号搜索
    # ================================================================

    def open_number_search(self, event):
        if event.button() == Qt.LeftButton:
            num_text = self.fields_entries["num"].text().strip()
            if not num_text:
                return

            try:
                QApplication.clipboard().setText(num_text)

                config = self.config_manager.load_config()
                predefined_sites = config.get('search_sites', {}).get('predefined_sites', {})
                custom_sites = config.get('search_sites', {}).get('custom_sites', [])

                opened_count = 0

                if predefined_sites.get('supjav', False):
                    webbrowser.open(f"https://supjav.com/zh/?s={num_text}")
                    opened_count += 1

                if predefined_sites.get('subtitlecat', False):
                    webbrowser.open(f"https://www.subtitlecat.com/index.php?search={num_text}")
                    opened_count += 1

                for custom_site in custom_sites:
                    if (custom_site.get('enabled', False) and
                        custom_site.get('name') and
                        custom_site.get('url_template')):
                        try:
                            if self.search_site_manager.handle_custom_site(
                                custom_site['url_template'], num_text):
                                opened_count += 1
                        except Exception as e:
                            print(f"打开自定义网站 {custom_site['name']} 时出错: {e}")

                if predefined_sites.get('javdb', False):
                    self._start_javdb_search(num_text, opened_count)

                total_sites = opened_count + (1 if predefined_sites.get('javdb', False) else 0)
                if total_sites == 0:
                    self.status_bar.showMessage(f"已复制番号: {num_text}，但未配置搜索网站", 3000)
                else:
                    self.status_bar.showMessage(f"已复制番号: {num_text}，正在处理 {total_sites} 个搜索网站", 3000)

            except Exception as e:
                QMessageBox.warning(self, "警告", f"操作失败: {str(e)}")

    def _start_javdb_search(self, num_text, initial_count):
        def search_javdb():
            search_engine = SearchEngine()
            opened_count = initial_count

            try:
                detail_url = search_engine.search_javdb(num_text)
                if detail_url:
                    webbrowser.open(detail_url)
                    opened_count += 1
                else:
                    search_url = f"https://javdb.com/search?q={num_text}&f=all"
                    webbrowser.open(search_url)
                    opened_count += 1
            except Exception as e:
                print(f"JavDB处理失败: {str(e)}")

            QTimer.singleShot(0, lambda: self.status_bar.showMessage(
                f"已处理 {opened_count} 个搜索网站", 3000
            ))

        threading.Thread(target=search_javdb, daemon=True).start()

    def batch_search_numbers(self):
        selected_items = self.file_tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "警告", "请先选择要搜索的NFO文件")
            return

        numbers = []
        for item in selected_items:
            try:
                values = [item.text(i) for i in range(3)]
                if values[2]:
                    nfo_path = (
                        os.path.join(self.folder_path, values[0], values[1], values[2])
                        if values[1]
                        else os.path.join(self.folder_path, values[0], values[2])
                    )
                    if os.path.exists(nfo_path):
                        tree = ET.parse(nfo_path)
                        root = tree.getroot()
                        num_elem = root.find("num")
                        if num_elem is not None and num_elem.text:
                            numbers.append(num_elem.text.strip())
            except Exception as e:
                print(f"处理NFO文件失败: {str(e)}")
                continue

        if not numbers:
            QMessageBox.warning(self, "警告", "选中的NFO文件中没有找到有效的番号")
            return

        QApplication.clipboard().setText(", ".join(numbers))

        try:
            config = self.config_manager.load_config()
            predefined_sites = config.get('search_sites', {}).get('predefined_sites', {})
            custom_sites = config.get('search_sites', {}).get('custom_sites', [])

            total_opened = 0

            for num_text in numbers:
                if predefined_sites.get('supjav', False):
                    webbrowser.open(f"https://supjav.com/zh/?s={num_text}")
                    total_opened += 1

                if predefined_sites.get('subtitlecat', False):
                    webbrowser.open(f"https://www.subtitlecat.com/index.php?search={num_text}")
                    total_opened += 1

                for custom_site in custom_sites:
                    if (custom_site.get('enabled', False) and
                        custom_site.get('name') and
                        custom_site.get('url_template')):
                        try:
                            if self.search_site_manager.handle_custom_site(
                                custom_site['url_template'], num_text):
                                total_opened += 1
                        except Exception as e:
                            print(f"打开自定义网站 {custom_site['name']} 时出错: {e}")

            if predefined_sites.get('javdb', False):
                def search_javdb_batch():
                    search_engine = SearchEngine()
                    for num_text in numbers:
                        try:
                            detail_url = search_engine.search_javdb(num_text)
                            if detail_url:
                                webbrowser.open(detail_url)
                            else:
                                webbrowser.open(f"https://javdb.com/search?q={num_text}&f=all")
                        except Exception as e:
                            print(f"JavDB批量搜索 {num_text} 失败: {str(e)}")

                threading.Thread(target=search_javdb_batch, daemon=True).start()

            self.status_bar.showMessage(
                f"已复制 {len(numbers)} 个番号到剪贴板，共打开 {total_opened} 个搜索页面", 5000
            )

        except Exception as e:
            QMessageBox.critical(self, "错误", f"批量搜索失败: {str(e)}")

    def copy_number_to_clipboard(self):
        try:
            if "num" in self.fields_entries:
                num_text = self.fields_entries["num"].text().strip()
                if num_text:
                    QApplication.clipboard().setText(num_text)
                    self.copy_num_button.setText("✅")
                    self.copy_num_button.setToolTip("已复制")
                    self.status_bar.showMessage(f"番号已复制: {num_text}", 2000)
                    QTimer.singleShot(2000, self.restore_copy_button)
                else:
                    self.status_bar.showMessage("番号为空，无法复制", 2000)
        except Exception as e:
            QMessageBox.warning(self, "警告", f"复制番号失败: {str(e)}")

    def restore_copy_button(self):
        if hasattr(self, 'copy_num_button'):
            self.copy_num_button.setText("📋")
            self.copy_num_button.setToolTip("复制番号")

    # ================================================================
    #  双击 & 焦点
    # ================================================================

    def on_file_double_click(self, item, column):
        values = [item.text(i) for i in range(3)]
        if values[2]:
            nfo_path = (
                os.path.join(self.folder_path, values[0], values[1], values[2])
                if values[1]
                else os.path.join(self.folder_path, values[0], values[2])
            )
            if os.path.exists(nfo_path):
                os.startfile(os.path.dirname(nfo_path))
            else:
                QMessageBox.critical(self, "错误", f"文件夹不存在: {os.path.dirname(nfo_path)}")

    def on_target_tree_double_click(self, item, column):
        if not self.current_target_path:
            return

        item_text = item.text(0)
        if item_text == "..":
            parent_path = os.path.dirname(self.current_target_path)
            if parent_path != self.current_target_path:
                self.current_target_path = parent_path
                self.load_target_files(parent_path)
        else:
            new_path = os.path.join(self.current_target_path, item_text)
            if os.path.isdir(new_path):
                self.current_target_path = new_path
                self.load_target_files(new_path)

    def focus_file_list(self):
        if hasattr(self, "file_tree"):
            self.file_tree.setFocus(Qt.OtherFocusReason)
            if not self.file_tree.selectedItems():
                if self.file_tree.topLevelItemCount() > 0:
                    self.file_tree.setCurrentItem(self.file_tree.topLevelItem(0))

    def focus_rating(self):
        if "rating" in self.fields_entries:
            rating_widget = self.fields_entries["rating"]
            rating_widget.setFocus(Qt.OtherFocusReason)
            rating_widget.selectAll()

    # ================================================================
    #  图片裁剪 & 删除 & 其他工具
    # ================================================================

    def open_image_and_crop(self, image_type):
        if not self.current_file_path:
            return

        folder = os.path.dirname(self.current_file_path)
        image_files = [
            f for f in os.listdir(folder)
            if f.lower().endswith(".jpg") and image_type in f.lower()
        ]

        if not image_files:
            QMessageBox.critical(self, "错误", f"未找到{image_type}图片")
            return

        try:
            from cg_crop import EmbyPosterCrop

            image_path = os.path.join(folder, image_files[0])
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            has_subtitle = False
            mark_type = "none"

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

            nfo_base_name = os.path.splitext(os.path.basename(self.current_file_path))[0]
            crop_tool = EmbyPosterCrop(nfo_base_name=nfo_base_name)
            crop_tool.load_initial_image(image_path)
            crop_tool.set_watermark_options(has_subtitle, mark_type)
            crop_tool.exec_()

            if self.show_images_checkbox.isChecked():
                self.display_image()

        except ImportError:
            QMessageBox.critical(self, "错误", "找不到 cg_crop.py 文件")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"裁剪工具出错: {str(e)}")

    def delete_selected_folders(self):
        selected_items = self.file_tree.selectedItems()
        if not selected_items:
            return

        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除选中的 {len(selected_items)} 个文件夹吗？",
            QMessageBox.Yes | QMessageBox.No,
        )

        if reply == QMessageBox.No:
            return

        deleted_count = 0
        for item in selected_items:
            try:
                values = [item.text(i) for i in range(3)]
                if values[2]:
                    nfo_path = (
                        os.path.join(self.folder_path, values[0], values[1], values[2])
                        if values[1]
                        else os.path.join(self.folder_path, values[0], values[2])
                    )
                    folder_path = os.path.dirname(nfo_path)

                    if os.path.exists(folder_path):
                        winshell.delete_file(folder_path)
                        deleted_count += 1

                        self.nfo_cache.remove(nfo_path)
                        if nfo_path in self.nfo_files:
                            self.nfo_files.remove(nfo_path)

                    root_item = self.file_tree.invisibleRootItem()
                    root_item.takeChild(root_item.indexOfChild(item))

            except Exception as e:
                QMessageBox.warning(self, "警告", f"删除文件夹失败: {str(e)}")

        if deleted_count > 0:
            self.status_bar.showMessage(f"成功删除 {deleted_count} 个文件夹")
            self.on_file_select()

    def open_batch_rename_tool(self):
        if not self.folder_path:
            QMessageBox.critical(self, "错误", "请先选择NFO目录")
            return

        try:
            if not os.path.isdir(self.folder_path):
                QMessageBox.critical(self, "错误", f"目录不存在: {self.folder_path}")
                return

            from cg_rename import RenameToolGUI

            rename_tool = RenameToolGUI(parent=self)
            rename_tool.path_entry.setText(self.folder_path)
            rename_tool.show()

        except ImportError:
            QMessageBox.critical(self, "错误", "找不到重命名工具模块(cg_rename.py)")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"启动重命名工具时出错: {str(e)}")

    def show_photo_wall(self):
        try:
            from cg_photo_wall import PhotoWallDialog

            if not self.folder_path:
                QMessageBox.warning(self, "警告", "请先选择NFO目录")
                return

            # parent 传 None：照片墙成为独立顶级窗口，与编辑器平级，
            # 互相 activateWindow/raise_ 均可正常置顶，不受 Qt 父子层级限制。
            dialog = PhotoWallDialog(self.folder_path, None)
            dialog.set_editor(self)      # 单独注入编辑器引用，仅用于回调定位
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.show()

        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开照片墙失败: {str(e)}")

    def select_folder_in_tree(self, folder_path):
        try:
            if not self.folder_path:
                base_path = os.path.dirname(folder_path)
                self.folder_path = base_path
                self.load_files_in_folder()

            for i in range(self.file_tree.topLevelItemCount()):
                item = self.file_tree.topLevelItem(i)
                first_level = item.text(0)
                second_level = item.text(1)

                if second_level:
                    item_path = os.path.join(self.folder_path, first_level, second_level)
                else:
                    item_path = os.path.join(self.folder_path, first_level)

                if os.path.normpath(item_path) == os.path.normpath(folder_path):
                    self.file_tree.setCurrentItem(item)
                    self.file_tree.scrollToItem(item)
                    self.file_tree.itemSelectionChanged.emit()
                    self.on_file_select()
                    return

            QMessageBox.warning(self, "警告", f"未找到文件夹: {folder_path}")

        except Exception as e:
            QMessageBox.critical(self, "错误", f"选择文件夹失败: {str(e)}")

    # ================================================================
    #  右键菜单 & 拖拽
    # ================================================================

    def contextMenuEvent(self, event):
        menu = QMenu(self)

        refresh_action = menu.addAction("刷新")
        refresh_action.triggered.connect(self.load_files_in_folder)

        if self.file_tree.selectedItems():
            menu.addSeparator()

            menu.addAction("打开NFO").triggered.connect(self.open_selected_nfo)
            menu.addAction("打开文件夹").triggered.connect(self.open_selected_folder)
            menu.addAction("播放视频").triggered.connect(self.open_selected_video)

            menu.addSeparator()
            if len(self.file_tree.selectedItems()) > 1:
                batch_search_action = menu.addAction(
                    f"批量搜索番号 ({len(self.file_tree.selectedItems())}个)"
                )
                batch_search_action.triggered.connect(self.batch_search_numbers)
            else:
                menu.addAction("搜索番号").triggered.connect(
                    lambda: self.open_number_search(
                        type('Event', (), {'button': lambda: Qt.LeftButton})()
                    )
                )

            menu.addSeparator()
            menu.addAction("删除文件夹").triggered.connect(self.delete_selected_folders)

            if self.current_target_path:
                menu.addSeparator()
                menu.addAction("移动到目标目录").triggered.connect(self.start_move_thread)

        menu.exec_(event.globalPos())

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isdir(path):
                self.set_nfo_folder(path)

    # ================================================================
    #  关闭清理
    # ================================================================

    def closeEvent(self, event):
        try:
            if hasattr(self, 'load_thread') and self.load_thread and self.load_thread.isRunning():
                self.load_thread.stop()
                self.load_thread.wait(2000)

            if hasattr(self, 'file_watcher'):
                directories = self.file_watcher.directories()
                files = self.file_watcher.files()
                if directories:
                    self.file_watcher.removePaths(directories)
                if files:
                    self.file_watcher.removePaths(files)

            if hasattr(self, 'move_thread') and self.move_thread and self.move_thread.isRunning():
                self.move_thread.stop()
                self.move_thread.wait(2000)

            if hasattr(self, 'nfo_cache'):
                self.nfo_cache.clear()

        except Exception as e:
            print(f"清理资源时出错: {e}")

        event.accept()


def main():
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = NFOEditorQt5()

    import argparse
    parser = argparse.ArgumentParser(description="NFO Editor")
    parser.add_argument("--base-path", help="基础目录路径")
    parser.add_argument("--select-folder", help="要选择的文件夹路径")
    args = parser.parse_args()

    if args.base_path and os.path.exists(args.base_path):
        window.folder_path = args.base_path
        # 先把待选路径存入 _pending_select_folder，
        # load_files_in_folder 是异步的，实际选择在 _on_load_finished 回调里执行，
        # 避免文件树尚未加载完毕时就调用 select_folder_in_tree 导致找不到目标项。
        if args.select_folder:
            window._pending_select_folder = args.select_folder
        window.load_files_in_folder()

    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
