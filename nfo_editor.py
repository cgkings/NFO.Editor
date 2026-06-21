"""
nfo_editor.py
=============
NFO Editor 主程序 - PySide6 业务逻辑层

从 NFO.Editor.Qt5.py 迁移而来,改动:
  1. PyQt5 -> PySide6
  2. pyqtSignal -> Signal (使用 as 别名,业务代码免改)
  3. QShortcut 从 QtWidgets 移到 QtGui
  4. 移除 Qt5 时代的 AA_EnableHighDpiScaling / AA_UseHighDpiPixmaps
  5. setup_signals 不再使用 findChildren(QPushButton) + 按钮文字匹配,
     改为直接绑定 UI 暴露的命名属性 (btn_open_folder 等)
  6. 目标目录面板的折叠改用 set_target_panel_visible(),
     不再操作 GridLayout columnStretch
  7. load_image 直接传原图 QPixmap,由 AdaptiveImageLabel 自动缩放
  8. import 路径: from nfo_editor_ui import NFOEditorQt
"""

import copy
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime

import requests
try:
    import winshell
except (ImportError, OSError):
    winshell = None
from bs4 import BeautifulSoup
from PIL import Image

# ============================================================
#  PySide6 imports
# ============================================================
from PySide6.QtCore import (
    Qt,
    QEvent,
    QFileSystemWatcher,
    QSettings,
    QSignalBlocker,
    QtMsgType,
    QThread,
    QTimer,
    Signal as pyqtSignal,
    qInstallMessageHandler,
)
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QStyle,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nfo_editor_ui import NFOEditorQt
from nfo_utils import (
    atomic_write_text,
    find_numbered_trailer,
    find_trailer_in_movie_folder,
    parse_xml_file,
    preferred_image_file,
    safe_move_directory,
    same_path,
    split_csv_values,
    sync_actor_nodes,
    unique_paths,
    write_xml_root_atomic,
    replace_text_nodes,
)


# ================ 缓存 & 异步加载 ================

class NFOCache:
    """NFO 文件缓存管理器"""

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


class NFODiskIndex:
    """SQLite 持久化 NFO 索引,避免每次启动都重新解析全部 XML。"""

    def __init__(self):
        self.db_path = self._resolve_db_path()

    @staticmethod
    def _resolve_db_path():
        """返回 nfo_index.sqlite3 的可写路径。"""
        appdata = os.environ.get("APPDATA")
        if appdata:
            base_dir = os.path.join(appdata, "NFOEditor")
        else:
            base_dir = os.path.join(os.path.expanduser("~"), ".nfoeditor")
        os.makedirs(base_dir, exist_ok=True)
        return os.path.join(base_dir, "nfo_index.sqlite3")

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nfo_index (
                root_path TEXT NOT NULL,
                nfo_path TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL,
                data_json TEXT NOT NULL,
                last_seen_scan INTEGER NOT NULL,
                PRIMARY KEY (root_path, nfo_path)
            )
            """
        )
        return conn

    def get_if_fresh(self, conn, root_path, nfo_path, stat_result):
        row = conn.execute(
            """
            SELECT mtime_ns, size, data_json
            FROM nfo_index
            WHERE root_path = ? AND nfo_path = ?
            """,
            (root_path, nfo_path),
        ).fetchone()
        if not row:
            return None

        old_mtime_ns, old_size, data_json = row
        if old_mtime_ns != stat_result.st_mtime_ns or old_size != stat_result.st_size:
            return None

        try:
            data = json.loads(data_json)
            data["path"] = nfo_path
            return data
        except (json.JSONDecodeError, TypeError):
            return None

    def upsert_many(self, conn, root_path, rows, scan_id):
        if not rows:
            return
        conn.executemany(
            """
            INSERT OR REPLACE INTO nfo_index
                (root_path, nfo_path, mtime_ns, size, data_json, last_seen_scan)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    root_path,
                    path,
                    mtime_ns,
                    size,
                    json.dumps(data, ensure_ascii=False),
                    scan_id,
                )
                for path, mtime_ns, size, data in rows
            ],
        )

    def mark_seen(self, conn, root_path, nfo_path, scan_id):
        """文件仍存在但本次解析失败时,只刷新 seen 标记,避免误删旧索引。"""
        conn.execute(
            """
            UPDATE nfo_index
            SET last_seen_scan = ?
            WHERE root_path = ? AND nfo_path = ?
            """,
            (scan_id, root_path, nfo_path),
        )

    def delete_not_seen(self, conn, root_path, scan_id):
        conn.execute(
            """
            DELETE FROM nfo_index
            WHERE root_path = ? AND last_seen_scan != ?
            """,
            (root_path, scan_id),
        )


class LoadFilesThread(QThread):
    """异步加载 NFO 文件的线程。使用 SQLite 索引做增量解析,并批量回传 UI 数据。"""

    progress = pyqtSignal(int, int, str)
    batch_ready = pyqtSignal(list)
    finished_signal = pyqtSignal(int, int)  # total, parsed_count
    error = pyqtSignal(str)

    def __init__(self, folder_path, batch_size=300):
        super().__init__()
        self.folder_path = os.path.normpath(folder_path)
        self.batch_size = batch_size
        self.disk_index = NFODiskIndex()
        # 工程规范: 停止用 threading.Event, 不用裸布尔标志
        self._stop_event = threading.Event()

    @property
    def is_stopped(self):
        return self._stop_event.is_set()

    def run(self):
        conn = None
        try:
            scan_id = time.time_ns()
            conn = self.disk_index.connect()

            nfo_files = []
            for root, dirs, files in os.walk(self.folder_path):
                if self._stop_event.is_set():
                    return
                dirs.sort(key=str.lower)
                for file in sorted(files, key=str.lower):
                    if file.lower().endswith(".nfo"):
                        nfo_files.append(os.path.join(root, file))

            total = len(nfo_files)
            if total == 0:
                self.disk_index.delete_not_seen(conn, self.folder_path, scan_id)
                conn.commit()
                self.finished_signal.emit(0, 0)
                return

            parsed_count = 0
            batch = []
            index_rows = []

            for i, nfo_path in enumerate(nfo_files, 1):
                if self._stop_event.is_set():
                    return

                try:
                    stat_result = os.stat(nfo_path)
                    cache_data = self.disk_index.get_if_fresh(
                        conn, self.folder_path, nfo_path, stat_result
                    )
                    if cache_data is None:
                        cache_data = self._parse_nfo(nfo_path)
                        parsed_count += 1

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

                    batch.append((nfo_path, first_level, second_level, nfo_file, cache_data))
                    index_rows.append((
                        nfo_path,
                        stat_result.st_mtime_ns,
                        stat_result.st_size,
                        cache_data,
                    ))

                    if len(batch) >= self.batch_size:
                        self.disk_index.upsert_many(conn, self.folder_path, index_rows, scan_id)
                        conn.commit()
                        self.batch_ready.emit(batch)
                        batch = []
                        index_rows = []

                    if i % 200 == 0 or i == total:
                        self.progress.emit(i, total, os.path.basename(nfo_path))

                except (ET.ParseError, OSError) as e:
                    # 文件仍然存在但可能正被外部组件写入/替换,保留旧索引,不要当成已删除。
                    try:
                        self.disk_index.mark_seen(conn, self.folder_path, nfo_path, scan_id)
                    except sqlite3.Error:
                        pass
                    print(f"解析文件失败 {nfo_path}: {str(e)}")
                    continue

            if batch:
                self.disk_index.upsert_many(conn, self.folder_path, index_rows, scan_id)
                conn.commit()
                self.batch_ready.emit(batch)

            self.disk_index.delete_not_seen(conn, self.folder_path, scan_id)
            conn.commit()
            self.finished_signal.emit(total, parsed_count)

        except (OSError, sqlite3.Error) as e:
            self.error.emit(f"加载过程出错: {str(e)}")
        finally:
            if conn is not None:
                conn.close()

    def _parse_nfo(self, nfo_path):
        tree = ET.parse(nfo_path)
        root = tree.getroot()

        data = {
            'path': nfo_path,
            'num': '', 'title': '', 'plot': '', 'series': '',
            'rating': 0.0, 'release': '', 'actors': [], 'tags': [],
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
        self._stop_event.set()

def parse_single_nfo(nfo_path):
    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()
        data = {
            'path': nfo_path,
            'num': '', 'title': '', 'plot': '', 'series': '',
            'rating': 0.0, 'release': '', 'actors': [], 'tags': [],
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
    except (ET.ParseError, OSError) as e:
        print(f"解析 NFO 文件失败 {nfo_path}: {str(e)}")
        return None


# ================ 配置 & 设置对话框 ================

class ConfigManager:
    def __init__(self):
        # 工程规范: 可写文件必须存 APPDATA, 不能写程序目录(CWD)
        self.config_file = self._resolve_config_path()
        self.default_config = {
            "search_sites": {
                "predefined_sites": {
                    "supjav": True, "subtitlecat": True, "javdb": True,
                },
                "custom_sites": [
                    {"name": "", "url_template": "", "enabled": False},
                    {"name": "", "url_template": "", "enabled": False},
                    {"name": "", "url_template": "", "enabled": False}
                ]
            },
            "trailer": {
                "local_directory": ""
            }
        }

    @staticmethod
    def _resolve_config_path():
        """返回 settings.json 的可写路径。

        Windows: %APPDATA%\\NFOEditor\\settings.json
        其它平台 / APPDATA 缺失: ~/.nfoeditor/settings.json (兜底)

        若旧版本曾在程序目录(CWD)写过 settings.json, 自动迁移一次,
        保证老用户升级后配置不丢。
        """
        appdata = os.environ.get("APPDATA")
        if appdata:
            config_dir = os.path.join(appdata, "NFOEditor")
        else:
            config_dir = os.path.join(os.path.expanduser("~"), ".nfoeditor")

        try:
            os.makedirs(config_dir, exist_ok=True)
        except OSError as e:
            print(f"创建配置目录失败, 回退到当前目录: {e}")
            return "settings.json"

        config_path = os.path.join(config_dir, "settings.json")

        # 一次性迁移旧的 CWD 配置
        legacy_path = "settings.json"
        if os.path.exists(legacy_path) and not os.path.exists(config_path):
            try:
                shutil.copy2(legacy_path, config_path)
                print(f"已迁移旧配置 {legacy_path} -> {config_path}")
            except OSError as e:
                print(f"迁移旧配置失败(忽略): {e}")

        return config_path

    def load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                return self._merge_config(self.default_config, config)
            return copy.deepcopy(self.default_config)
        except (OSError, json.JSONDecodeError) as e:
            print(f"加载配置文件失败: {e}")
            return copy.deepcopy(self.default_config)

    def save_config(self, config):
        try:
            text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
            atomic_write_text(self.config_file, text)
            return True
        except OSError as e:
            print(f"保存配置文件失败: {e}")
            return False

    def _merge_config(self, default, user_config):
        result = copy.deepcopy(default)
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
        self.parent_ref = parent
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
        trailer_group = self.create_trailer_group()
        scroll_layout.addWidget(trailer_group)
        scroll_layout.addStretch()

        scroll.setWidget(scroll_widget)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.apply_btn = QPushButton("应用")
        self.ok_btn = QPushButton("确定")
        self.cancel_btn = QPushButton("取消")
        btn_row.addWidget(self.apply_btn)
        btn_row.addWidget(self.ok_btn)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

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
            cb = QCheckBox(site_name)
            self.predefined_checkboxes[site_id] = cb
            layout.addWidget(cb)

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
                'enabled': enabled_cb, 'name': name_edit, 'url': url_edit
            })
            enabled_cb.stateChanged.connect(
                lambda state, widgets=(name_edit, url_edit):
                    self.toggle_custom_site_inputs(state, widgets)
            )
        return group

    def create_trailer_group(self):
        group = QGroupBox("本地预告片设置")
        layout = QVBoxLayout(group)
        help_label = QLabel("优先查找以番号命名的视频；找不到时再查影片目录和网络预告片。")
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: gray;")
        layout.addWidget(help_label)

        row = QHBoxLayout()
        self.trailer_directory_edit = QLineEdit()
        self.trailer_directory_edit.setPlaceholderText("例如：D:\\Trailers")
        browse_btn = QPushButton("浏览…")
        clear_btn = QPushButton("清空")
        browse_btn.clicked.connect(self.choose_trailer_directory)
        clear_btn.clicked.connect(self.trailer_directory_edit.clear)
        row.addWidget(self.trailer_directory_edit, 1)
        row.addWidget(browse_btn)
        row.addWidget(clear_btn)
        layout.addLayout(row)
        return group

    def choose_trailer_directory(self):
        current = self.trailer_directory_edit.text().strip()
        folder = QFileDialog.getExistingDirectory(self, "选择本地预告片目录", current)
        if folder:
            self.trailer_directory_edit.setText(os.path.normpath(folder))

    def toggle_custom_site_inputs(self, state, widgets):
        # PySide6: Qt.Checked 整数值仍可比较,也兼容短写法
        enabled = state == Qt.Checked.value if hasattr(Qt.Checked, 'value') else state == Qt.Checked
        for widget in widgets:
            widget.setEnabled(enabled)

    def load_current_settings(self):
        predefined = self.config.get('search_sites', {}).get('predefined_sites', {})
        for site_id, cb in self.predefined_checkboxes.items():
            cb.setChecked(predefined.get(site_id, False))

        custom_sites = self.config.get('search_sites', {}).get('custom_sites', [])
        for i, sc in enumerate(custom_sites[:3]):
            if i < len(self.custom_site_widgets):
                w = self.custom_site_widgets[i]
                enabled = sc.get('enabled', False)
                w['enabled'].setChecked(enabled)
                w['name'].setText(sc.get('name', ''))
                w['url'].setText(sc.get('url_template', ''))
                w['name'].setEnabled(enabled)
                w['url'].setEnabled(enabled)

        trailer_dir = self.config.get('trailer', {}).get('local_directory', '')
        self.trailer_directory_edit.setText(trailer_dir)

    def get_current_settings(self):
        config = copy.deepcopy(self.config)
        predefined = {sid: cb.isChecked() for sid, cb in self.predefined_checkboxes.items()}
        custom = [{
            'enabled': w['enabled'].isChecked(),
            'name': w['name'].text().strip(),
            'url_template': w['url'].text().strip()
        } for w in self.custom_site_widgets]
        config['search_sites'] = {'predefined_sites': predefined, 'custom_sites': custom}
        config['trailer'] = {'local_directory': self.trailer_directory_edit.text().strip()}
        return config

    def apply_settings(self):
        try:
            new_config = self.get_current_settings()
            for i, site in enumerate(new_config['search_sites']['custom_sites']):
                if site['enabled']:
                    if not site['name'] or not site['url_template']:
                        QMessageBox.warning(
                            self, "设置错误",
                            f"自定义网站{i+1}已启用但缺少网站名称或URL模板",
                        )
                        return False
                    if '{number}' not in site['url_template']:
                        QMessageBox.warning(
                            self, "设置错误",
                            f"自定义网站{i+1}的URL模板必须包含 {{number}} 占位符",
                        )
                        return False
            trailer_dir = os.path.expandvars(os.path.expanduser(
                new_config.get('trailer', {}).get('local_directory', '')
            ))
            if trailer_dir and not os.path.isdir(trailer_dir):
                QMessageBox.warning(self, "设置错误", f"本地预告片目录不存在：\n{trailer_dir}")
                return False
            if trailer_dir:
                new_config['trailer']['local_directory'] = os.path.normpath(trailer_dir)
            if not self.config_manager.save_config(new_config):
                QMessageBox.critical(self, "错误", "保存设置失败")
                return False
            self.config = new_config
            if self.parent_ref and hasattr(self.parent_ref, 'on_settings_changed'):
                self.parent_ref.on_settings_changed()
            QMessageBox.information(self, "成功", "设置已保存")
            return True
        except OSError as exc:
            QMessageBox.critical(self, "错误", f"应用设置时出错: {exc}")
            return False

    def accept_settings(self):
        if self.apply_settings():
            self.accept()


# ================ 文件移动线程 ================

class FileOperationThread(QThread):
    progress = pyqtSignal(int, int)
    error = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, operation_type, **kwargs):
        super().__init__()
        self.operation_type = operation_type
        self.kwargs = kwargs
        # 工程规范: 停止用 threading.Event, 不用裸布尔标志
        self._stop_event = threading.Event()

    @property
    def is_stopped(self):
        return self._stop_event.is_set()

    def run(self):
        if self.operation_type == "move":
            self.move_files()

    def stop(self):
        self._stop_event.set()

    def move_files(self):
        src_paths = unique_paths(self.kwargs.get("src_paths", []))
        dest_path = self.kwargs.get("dest_path")
        total = len(src_paths)

        for i, src_path in enumerate(src_paths, 1):
            if self._stop_event.is_set():
                break
            folder_name = os.path.basename(os.path.normpath(src_path))
            self.status.emit(f"正在处理: {folder_name}")
            try:
                safe_move_directory(src_path, dest_path)
            except (OSError, shutil.Error) as exc:
                self.error.emit(f"移动文件夹失败: {exc}")
            finally:
                self.progress.emit(i, total)



# ================ 搜索引擎 ================

class SearchEngine:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }

    def search_javdb(self, num_text):
        try:
            search_url = f"https://javdb.com/search?q={num_text}&f=all"
            response = requests.get(search_url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'lxml')
                if soup.find('div', class_='empty-message'):
                    return None
                movie_list = soup.find('div', class_='movie-list')
                if movie_list:
                    for item in movie_list.find_all('div', class_='item'):
                        strong_tag = item.find('strong')
                        if strong_tag and strong_tag.text.strip().upper() == num_text.upper():
                            link_tag = item.find('a', class_='box')
                            if link_tag and link_tag.get('href'):
                                return f"https://javdb.com{link_tag['href']}"
            return None
        except requests.RequestException as e:
            print(f"JavDB 搜索失败: {str(e)}")
            return None


class SearchSiteManager:
    def handle_custom_site(self, url_template, num_text):
        try:
            search_url = url_template.replace('{number}', num_text)
            webbrowser.open(search_url)
            return True
        except OSError as e:
            print(f"打开自定义网站失败: {str(e)}")
            return False


# ================ 主类 ================

class NFOEditorQt6(NFOEditorQt):
    # 跨线程信号
    _trailer_play_signal = pyqtSignal(str, str)
    _trailer_error_signal = pyqtSignal(str, str)
    _status_message_signal = pyqtSignal(str, int)

    def __init__(self):
        super().__init__()

        # 业务状态
        self.nfo_files = []
        self.selected_index_cache = None
        self.move_thread = None
        self.file_watcher = QFileSystemWatcher()
        self._pending_select_folder = None
        self._event_file_path = None
        self._event_file_offset = 0
        self.library_dirty = False
        self._last_selected_item = None
        self._selection_change_guard = False

        # 缓存 & 异步加载
        self.nfo_cache = NFOCache()
        self.load_thread = None
        self._show_progress = True

        # QApplication 已由入口创建；此处再延迟导入 Fluent 控件，避免过早构造 QPixmap。
        from qfluentwidgets import ProgressBar as FluentProgressBar
        self.progress_bar = FluentProgressBar()
        self.progress_bar.setMaximumWidth(300)
        self.progress_bar.hide()
        self.status_bar.addPermanentWidget(self.progress_bar)

        # 防抖动重载
        self.reload_timer = QTimer()
        self.reload_timer.setSingleShot(True)
        self.reload_timer.timeout.connect(self._delayed_reload)

        # 外部组件事件轮询: 组件可向 .nfo_editor_events.jsonl 追加事件, 主程序轻量消费
        self.event_timer = QTimer()
        self.event_timer.setInterval(1000)
        self.event_timer.timeout.connect(self._poll_external_events)
        self.event_timer.start()

        # 配置 & 搜索
        self.config_manager = ConfigManager()
        self.search_site_manager = SearchSiteManager()

        # 默认勾选显示图片
        self.show_images_checkbox.setChecked(True)

        # 拖拽
        self.setAcceptDrops(True)

        # 信号绑定
        self.setup_signals()

        # 恢复上次窗口状态
        self.restore_window_state()

        # 删除快捷键
        QShortcut(QKeySequence("Delete"), self, self.delete_selected_folders)

    # ================================================================
    #  信号绑定 - 直接绑定 UI 命名属性,不再 findChildren
    # ================================================================

    def setup_signals(self):
        # 工具栏
        self.btn_open_folder.clicked.connect(self.open_folder)
        self.btn_select_target.clicked.connect(self.select_target_folder)
        self.btn_open_nfo.clicked.connect(self.open_selected_nfo)
        self.btn_open_dir.clicked.connect(self.open_selected_folder)
        self.btn_play_video.clicked.connect(self.open_selected_video)
        self.btn_unify_actor.clicked.connect(self.open_batch_rename_tool)
        self.btn_refresh.clicked.connect(lambda: self.load_files_in_folder())
        self.btn_photo_wall.clicked.connect(self.show_photo_wall)
        self.btn_move.clicked.connect(self.start_move_thread)
        self.btn_settings.clicked.connect(self.open_settings)

        # 操作区
        self.btn_save.clicked.connect(self.save_changes)
        self.btn_batch_fill.clicked.connect(self.batch_filling)
        self.btn_batch_add.clicked.connect(self.batch_add)
        self.btn_filter.clicked.connect(self.apply_filter)
        self.filter_entry.returnPressed.connect(self.apply_filter)

        # 番号区
        self.copy_num_button.clicked.connect(self.copy_number_to_clipboard)
        self.play_trailer_button.clicked.connect(self.play_trailer)
        # 番号 QLabel 点击
        self.fields_entries["num"].mousePressEvent = self.open_number_search

        # 显示图片切换
        self.show_images_checkbox.stateChanged.connect(self.toggle_image_display)

        # 文件树
        self.file_tree.itemSelectionChanged.connect(self.on_file_select)
        self.file_tree.itemDoubleClicked.connect(self.on_file_double_click)
        self.sorted_tree.itemDoubleClicked.connect(self.on_target_tree_double_click)

        # 文件监控
        self.file_watcher.fileChanged.connect(self.on_file_changed)
        self.file_watcher.directoryChanged.connect(self.on_directory_changed)

        # 排序
        self.sorting_group.buttonClicked.connect(self.sort_files)

        # 图片裁剪
        # bug 修复: poster 关键字传 "poster",thumb 传 "fanart"(原文件查 fanart.jpg)
        self.poster_label.mousePressEvent = lambda e: self.open_image_and_crop("poster")
        self.thumb_label.mousePressEvent = lambda e: self.open_image_and_crop("fanart")

        # 快捷键
        self.setup_shortcuts()

        # 评分输入特殊处理
        if "rating" in self.fields_entries:
            self.fields_entries["rating"].installEventFilter(self)
            rating_widget = self.fields_entries["rating"]
            rating_widget.keyReleaseEvent = (
                lambda event: self.on_rating_key_release(rating_widget, event)
            )

        # TextEdit 回车保存(Enter 键)
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

    def eventFilter(self, obj, event):
        if (
            event.type() == QEvent.KeyPress
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
        # 跨线程预告片信号
        self._trailer_play_signal.connect(self._play_online_trailer)
        self._trailer_error_signal.connect(self._on_trailer_error)
        self._status_message_signal.connect(self.status_bar.showMessage)

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
                    formatted = f"{main_num}.{key_text}"
                    if float(formatted) <= 9.9:
                        widget.setPlainText(formatted)
                    else:
                        widget.setPlainText("9.9")
                elif current_text.isdigit():
                    widget.setPlainText(f"{float(current_text):.1f}")
                cursor = widget.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                widget.setTextCursor(cursor)
        except ValueError as e:
            print(f"处理评分输入时出错: {str(e)}")
        QTextEdit.keyReleaseEvent(widget, event)

    # ================================================================
    #  设置
    # ================================================================

    def open_settings(self):
        try:
            dialog = SettingsDialog(self)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.exec()  # PySide6: exec_() -> exec()
        except OSError as e:
            QMessageBox.critical(self, "错误", f"打开设置失败: {str(e)}")

    def on_settings_changed(self):
        self.status_bar.showMessage("设置已更新", 3000)

    # ================================================================
    #  NFO 目录管理
    # ================================================================

    def set_nfo_folder(self, folder_path):
        self.folder_path = os.path.normpath(folder_path)
        settings = QSettings("NFOEditor", "Directories")
        settings.setValue("last_nfo_dir", self.folder_path)
        self._clear_file_watches()
        self._set_event_file_for_folder(self.folder_path)
        self.load_files_in_folder()

    def open_folder(self):
        settings = QSettings("NFOEditor", "Directories")
        last_dir = settings.value("last_nfo_dir", "")
        folder = QFileDialog.getExistingDirectory(self, "选择NFO文件夹", last_dir)
        if folder:
            self.set_nfo_folder(folder)

    def select_target_folder(self):
        settings = QSettings("NFOEditor", "Directories")
        last_target = settings.value("last_target_dir", "")
        target = QFileDialog.getExistingDirectory(self, "选择目标文件夹", last_target)
        if target:
            self.current_target_path = target
            settings.setValue("last_target_dir", target)
            self.load_target_files(target)
            # 用新 UI 提供的方法显示中间面板
            self.set_target_panel_visible(True)
        else:
            self.set_target_panel_visible(False)

    def clear_target_folder(self):
        self.current_target_path = None
        self.sorted_tree.clear()
        self.set_target_panel_visible(False)
        self.status_bar.showMessage("目标目录已清除")

    # ================================================================
    #  异步加载
    # ================================================================

    def _set_loading_busy(self, busy: bool):
        """加载线程运行期间禁用会冲突/重入的按钮, 完成后恢复。

        防止用户在加载中再次点击触发重复线程, 同时给出明确的视觉反馈。
        用 getattr 容错: 即使某个按钮在 UI 改动后被移除也不会崩。
        """
        for name in (
            "btn_open_folder", "btn_select_target", "btn_refresh",
            "btn_move", "btn_filter", "btn_photo_wall", "btn_unify_actor",
        ):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.setEnabled(not busy)
        if hasattr(self, "filter_entry") and self.filter_entry is not None:
            self.filter_entry.setEnabled(not busy)

    def _set_moving_busy(self, busy: bool):
        """移动线程运行期间禁用移动相关按钮。"""
        for name in ("btn_move", "btn_open_folder", "btn_refresh"):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.setEnabled(not busy)

    def load_files_in_folder(self, auto_select=True, show_progress=True):
        if not self.folder_path:
            return

        # 防重复启动: 若已有加载线程在跑, 先停掉再重开(下方已 stop+wait),
        # 此处仅做按钮态的兜底, 避免 UI 与线程状态不一致。
        current_selection_path = None
        if not auto_select:
            selected = self.file_tree.selectedItems()
            if selected and self.current_file_path:
                current_selection_path = self.current_file_path

        # 停止旧线程并断开所有信号
        if self.load_thread is not None and self.load_thread.isRunning():
            self.load_thread.stop()
            try:
                self.load_thread.batch_ready.disconnect()
                self.load_thread.progress.disconnect()
                self.load_thread.finished_signal.disconnect()
                self.load_thread.error.disconnect()
            except RuntimeError:
                pass
            self.load_thread.wait()

        self._last_selected_item = None
        self.file_tree.clear()
        self.nfo_files = []
        self.nfo_cache.clear()

        self._show_progress = show_progress
        if show_progress:
            self.progress_bar.setValue(0)
            self.progress_bar.show()
            self.status_bar.showMessage("正在加载文件...")

        self.load_thread = LoadFilesThread(self.folder_path, batch_size=300)
        self.load_thread.progress.connect(self._on_load_progress)
        self.load_thread.batch_ready.connect(self._on_batch_ready)
        self.load_thread.finished_signal.connect(
            lambda count, parsed: self._on_load_finished(
                count, auto_select, current_selection_path, parsed
            )
        )
        self.load_thread.error.connect(self._on_load_error)
        self._set_loading_busy(True)
        self.load_thread.start()

    def _on_load_progress(self, current, total, filename):
        if not self._show_progress:
            return
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        if current % 100 == 0 or current == total:
            self.status_bar.showMessage(f"正在加载: {current}/{total} - {filename}")

    def _on_batch_ready(self, batch):
        items = []
        for nfo_path, first, second, nfo_name, cache_data in batch:
            items.append(QTreeWidgetItem([first, second, nfo_name]))
            self.nfo_cache.set(nfo_path, cache_data)
            self.nfo_files.append(nfo_path)
        if items:
            self.file_tree.addTopLevelItems(items)

    def _on_load_finished(self, count, auto_select, selection_path, parsed_count=0):
        if self._show_progress:
            self.progress_bar.hide()

        self.library_dirty = False
        loaded_count = len(self.nfo_files)
        failed_count = max(count - loaded_count, 0)
        total_folders = len(set(os.path.dirname(f) for f in self.nfo_files))
        cache_hits = max(loaded_count - parsed_count, 0)
        failure_text = f"，失败 {failed_count} 个" if failed_count else ""
        self.status_bar.showMessage(
            f"加载完成: 成功 {loaded_count}/{count} 个NFO ({total_folders} 个文件夹)，"
            f"本次解析 {parsed_count} 个，缓存命中 {cache_hits} 个{failure_text} - "
            f"目录: {self.folder_path}"
        )

        if auto_select and self.file_tree.topLevelItemCount() > 0:
            first_item = self.file_tree.topLevelItem(0)
            self.file_tree.setCurrentItem(first_item)
        elif selection_path:
            self._restore_selection(selection_path)

        if self._pending_select_folder:
            pending = self._pending_select_folder
            self._pending_select_folder = None
            self.select_folder_in_tree(pending)

        if self.load_thread:
            self.load_thread.deleteLater()
            self.load_thread = None

        self._set_loading_busy(False)

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
        self._set_loading_busy(False)
        QMessageBox.critical(self, "错误", error_msg)

    # ================================================================
    #  文件监控
    # ================================================================

    def _same_path(self, a, b):
        return bool(a and b) and os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))

    def _clear_file_watches(self):
        try:
            files = self.file_watcher.files()
            directories = self.file_watcher.directories()
            if files:
                self.file_watcher.removePaths(files)
            if directories:
                self.file_watcher.removePaths(directories)
        except RuntimeError:
            pass

    def _watch_current_item(self):
        self._clear_file_watches()
        if not self.current_file_path:
            return

        watch_paths = []
        if os.path.exists(self.current_file_path):
            watch_paths.append(self.current_file_path)
        current_dir = os.path.dirname(self.current_file_path)
        if os.path.isdir(current_dir):
            watch_paths.append(current_dir)

        if watch_paths:
            try:
                self.file_watcher.addPaths(watch_paths)
            except RuntimeError:
                pass

    def _set_event_file_for_folder(self, folder_path):
        self._event_file_path = os.path.join(folder_path, ".nfo_editor_events.jsonl")
        try:
            self._event_file_offset = os.path.getsize(self._event_file_path)
        except OSError:
            self._event_file_offset = 0

    def _poll_external_events(self):
        if not self._event_file_path or not os.path.exists(self._event_file_path):
            return
        try:
            size = os.path.getsize(self._event_file_path)
            if size < self._event_file_offset:
                self._event_file_offset = 0
            if size == self._event_file_offset:
                return
            with open(self._event_file_path, "r", encoding="utf-8") as f:
                f.seek(self._event_file_offset)
                lines = f.readlines()
                self._event_file_offset = f.tell()
        except OSError:
            return

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._handle_external_event(event)

    def _handle_external_event(self, event):
        event_type = event.get("type", "")
        event_path = event.get("path", "")
        if event_path and not os.path.isabs(event_path) and self.folder_path:
            event_path = os.path.join(self.folder_path, event_path)

        if event_type == "task_started":
            self.status_bar.showMessage(event.get("message", "外部组件正在处理..."), 5000)
            return
        if event_type == "task_finished":
            self.status_bar.showMessage(event.get("message", "外部组件处理完成"), 5000)
            return

        if event_type == "nfo_changed" and event_path:
            cache_data = parse_single_nfo(event_path)
            if cache_data:
                self.nfo_cache.set(event_path, cache_data)
                if event_path not in self.nfo_files:
                    self.library_dirty = True
                    self.reload_timer.start(800)
            if self._same_path(event_path, self.current_file_path):
                self.load_nfo_fields()
            return

        if event_type == "image_changed" and event_path:
            current_dir = os.path.dirname(self.current_file_path) if self.current_file_path else ""
            event_dir = event_path if os.path.isdir(event_path) else os.path.dirname(event_path)
            if self._same_path(event_dir, current_dir) and self.show_images_checkbox.isChecked():
                self.display_image()
            return

        if event_type in {"folder_moved", "folder_deleted", "library_dirty", "refresh_list"}:
            self.library_dirty = True
            self.reload_timer.start(1000)
            self.status_bar.showMessage("检测到外部组件修改列表，稍后执行增量刷新", 5000)

    def on_file_changed(self, path):
        if self._same_path(path, self.current_file_path):
            cache_data = parse_single_nfo(path)
            if cache_data:
                self.nfo_cache.set(path, cache_data)
            self.load_nfo_fields()
            self.status_bar.showMessage("当前 NFO 已刷新", 3000)

            # 某些编辑器会以“删除旧文件 + 新建文件”的方式保存, QFileSystemWatcher 会丢失 watch。
            self._watch_current_item()

    def on_directory_changed(self, path):
        current_dir = os.path.dirname(self.current_file_path) if self.current_file_path else ""
        if self._same_path(path, current_dir):
            if self.show_images_checkbox.isChecked():
                self.display_image()
            self.status_bar.showMessage("当前文件夹图片已刷新", 3000)
            self._watch_current_item()

    def _delayed_reload(self):
        if self.folder_path:
            self.load_files_in_folder(auto_select=False, show_progress=False)

    # ================================================================
    #  文件选中
    # ================================================================

    def _nfo_path_from_item(self, item):
        if item is None or not self.folder_path:
            return None
        first, second, filename = (item.text(i) for i in range(3))
        if not filename:
            return None
        parts = [self.folder_path]
        if first:
            parts.append(first)
        if second:
            parts.append(second)
        parts.append(filename)
        return os.path.normpath(os.path.join(*parts))

    def _restore_tree_selection(self, item):
        self._selection_change_guard = True
        try:
            blocker = QSignalBlocker(self.file_tree)
            if item is not None:
                self.file_tree.setCurrentItem(item)
                self.file_tree.scrollToItem(item)
            else:
                self.file_tree.clearSelection()
            del blocker
        finally:
            self._selection_change_guard = False

    def on_file_select(self):
        if self._selection_change_guard:
            return
        selected = self.file_tree.selectedItems()
        if not selected:
            return

        item = selected[0]
        new_path = self._nfo_path_from_item(item)
        if not new_path:
            return
        if same_path(new_path, self.current_file_path):
            self._last_selected_item = item
            return

        previous_item = self._last_selected_item
        if self.current_file_path and self.has_unsaved_changes():
            reply = QMessageBox.question(
                self, "保存更改", "当前有未保存的更改，是否保存？",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if reply == QMessageBox.Cancel:
                self._restore_tree_selection(previous_item)
                return
            if reply == QMessageBox.Yes and not self.save_changes():
                self._restore_tree_selection(previous_item)
                return

        if not os.path.exists(new_path):
            self.file_tree.takeTopLevelItem(self.file_tree.indexOfTopLevelItem(item))
            self.current_file_path = None
            self._last_selected_item = None
            self._watch_current_item()
            return

        self.current_file_path = new_path
        self._last_selected_item = item
        self._watch_current_item()
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
            tree = parse_xml_file(self.current_file_path)
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
                tag.text.strip() for tag in root.findall("tag")
                if tag is not None and tag.text
            ]
            self.fields_entries["tags"].setPlainText(", ".join(tags))

            release_elem = root.find("release")
            if release_elem is not None and release_elem.text:
                self.release_label.setText(release_elem.text.strip())
            else:
                self.release_label.setText("")
        except (ET.ParseError, OSError) as e:
            QMessageBox.critical(self, "错误", f"加载NFO文件失败: {str(e)}")

    # ================================================================
    #  保存
    # ================================================================

    def save_changes(self):
        if not self.current_file_path:
            return False
        try:
            tree = parse_xml_file(self.current_file_path)
            root = tree.getroot()

            title = self.fields_entries["title"].toPlainText().strip()
            plot = self.fields_entries["plot"].toPlainText().strip()
            actors = split_csv_values(self.fields_entries["actors"].toPlainText())
            series = self.fields_entries["series"].toPlainText().strip()
            tags = split_csv_values(self.fields_entries["tags"].toPlainText())
            rating = self.fields_entries["rating"].toPlainText().strip()

            for field, value in {
                "title": title, "plot": plot, "series": series, "rating": rating,
            }.items():
                elem = root.find(field)
                if elem is None:
                    elem = ET.SubElement(root, field)
                elem.text = value

            critic_elem = root.find("criticrating")
            if rating:
                try:
                    rating_value = float(rating)
                except ValueError as exc:
                    raise ValueError("评分必须是有效数字") from exc
                if critic_elem is None:
                    critic_elem = ET.SubElement(root, "criticrating")
                critic_elem.text = str(int(rating_value * 10))
            elif critic_elem is not None:
                root.remove(critic_elem)

            # 只同步可编辑字段：保留匹配 actor 下的 role/thumb/type 等扩展信息，
            # tag 与 genre 分离，普通保存不再覆盖 genre。
            sync_actor_nodes(root, actors)
            replace_text_nodes(root, "tag", tags)

            self._clear_file_watches()
            write_xml_root_atomic(root, self.current_file_path)

            cache_data = parse_single_nfo(self.current_file_path)
            if cache_data:
                self.nfo_cache.set(self.current_file_path, cache_data)

            self._watch_current_item()
            save_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.save_time_label.setText(f"保存时间: {save_time}")
            return True
        except (ET.ParseError, OSError, ValueError) as exc:
            self._watch_current_item()
            QMessageBox.critical(self, "错误", f"保存NFO文件失败: {exc}")
            return False

    # ================================================================
    #  移动文件
    # ================================================================

    def start_move_thread(self):
        try:
            selected = self.file_tree.selectedItems()
            if not selected:
                QMessageBox.warning(self, "警告", "请先选择要移动的文件夹")
                return
            if not self.current_target_path:
                QMessageBox.critical(self, "错误", "请先选择目标目录")
                return

            src_paths = []
            skipped_root = False
            for item in selected:
                nfo_path = self._nfo_path_from_item(item)
                if not nfo_path:
                    continue
                src_path = os.path.dirname(nfo_path)
                if same_path(src_path, self.folder_path):
                    skipped_root = True
                    continue
                if not os.path.isdir(src_path):
                    QMessageBox.warning(self, "警告", f"源文件夹不存在: {src_path}")
                    continue
                src_paths.append(src_path)

            src_paths = unique_paths(src_paths)
            if skipped_root:
                QMessageBox.warning(
                    self, "根目录保护",
                    "根目录中的 NFO 不能按文件夹移动，已自动跳过。",
                )

            if not src_paths:
                QMessageBox.warning(self, "警告", "没有有效的源文件夹可以移动")
                return

            self._clear_file_watches()

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
            self._set_moving_busy(True)
            self.move_thread.start()
        except OSError as e:
            self._set_moving_busy(False)
            self._watch_current_item()
            QMessageBox.critical(self, "错误", f"启动移动操作时出错: {str(e)}")

    def on_move_finished(self):
        self.reload_timer.stop()
        current_was_moved = bool(
            self.current_file_path and not os.path.exists(self.current_file_path)
        )
        if current_was_moved:
            self.current_file_path = None
            self._last_selected_item = None
            self.clear_images()
            for entry in self.fields_entries.values():
                if isinstance(entry, QTextEdit):
                    entry.clear()
                elif isinstance(entry, QLabel):
                    entry.setText("")

        # 先解除移动态, 再触发加载(加载会自行接管按钮禁用), 避免两套状态打架
        self._set_moving_busy(False)
        self.load_files_in_folder(
            auto_select=current_was_moved, show_progress=False
        )
        if self.current_target_path:
            self.load_target_files(self.current_target_path)
        self._watch_current_item()
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
                # PySide6: 必须用全限定枚举 QStyle.StandardPixmap.SP_*
                # 不能用 PyQt5 的实例属性短写法 self.style().SP_ArrowUp
                parent_item.setIcon(
                    0,
                    self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp),
                )
                self.sorted_tree.addTopLevelItem(parent_item)

            for entry in os.scandir(target_path):
                if entry.is_dir():
                    item = QTreeWidgetItem([entry.name])
                    item.setIcon(
                        0,
                        self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon),
                    )
                    self.sorted_tree.addTopLevelItem(item)

            folder_count = self.sorted_tree.topLevelItemCount()
            top_texts = [
                self.sorted_tree.topLevelItem(i).text(0) for i in range(folder_count)
            ]
            if ".." in top_texts:
                folder_count -= 1
            self.status_bar.showMessage(f"目标目录: {target_path} (共{folder_count}个文件夹)")
        except OSError as e:
            QMessageBox.critical(self, "错误", f"加载目标目录失败: {str(e)}")

    # ================================================================
    #  未保存检测
    # ================================================================

    def has_unsaved_changes(self):
        if not self.current_file_path or not os.path.exists(self.current_file_path):
            return False
        try:
            tree = parse_xml_file(self.current_file_path)
            root = tree.getroot()
            for field in ["title", "plot", "series", "rating"]:
                current_value = self.fields_entries[field].toPlainText().strip()
                elem = root.find(field)
                original = elem.text.strip() if elem is not None and elem.text else ""
                if current_value != original:
                    return True

            current_actors = set(
                a.strip() for a in self.fields_entries["actors"].toPlainText().strip().split(",")
                if a.strip()
            )
            original_actors = {
                actor.find("name").text.strip()
                for actor in root.findall("actor")
                if actor.find("name") is not None and actor.find("name").text
            }
            if current_actors != original_actors:
                return True

            current_tags = set(
                t.strip() for t in self.fields_entries["tags"].toPlainText().strip().split(",")
                if t.strip()
            )
            original_tags = {
                tag.text.strip() for tag in root.findall("tag")
                if tag is not None and tag.text
            }
            if current_tags != original_tags:
                return True
            return False
        except (ET.ParseError, OSError) as e:
            print(f"检查更改状态时出错: {str(e)}")
            return False

    # ================================================================
    #  图片显示 - 使用 AdaptiveImageLabel,直接传原图
    # ================================================================

    def toggle_image_display(self):
        if self.show_images_checkbox.isChecked():
            self.display_image()
        else:
            self.clear_images()

    def clear_images(self):
        self.poster_label.setText("封面图 (poster)")
        self.thumb_label.setText("缩略图 (thumb)")
        self.poster_resolution_label.setText("分辨率: 未知")
        self.thumb_resolution_label.setText("分辨率: 未知")

    def display_image(self):
        if not self.current_file_path:
            return
        folder = os.path.dirname(self.current_file_path)
        nfo_base = os.path.splitext(os.path.basename(self.current_file_path))[0]
        poster_path = preferred_image_file(folder, "poster", nfo_base)
        thumb_path = preferred_image_file(folder, "thumb", nfo_base)
        if not thumb_path:
            thumb_path = preferred_image_file(folder, "fanart", nfo_base)

        if poster_path:
            self.load_image(poster_path, self.poster_label, self.poster_resolution_label)
        else:
            self.poster_label.setText("文件夹内无poster图片")
            self.poster_resolution_label.setText("分辨率: 未知")

        if thumb_path:
            self.load_image(thumb_path, self.thumb_label, self.thumb_resolution_label)
        else:
            self.thumb_label.setText("文件夹内无thumb或fanart图片")
            self.thumb_resolution_label.setText("分辨率: 未知")

    def load_image(self, image_path, label, resolution_label=None):
        """加载图片。AdaptiveImageLabel 会自动缩放,这里只传原图。"""
        try:
            with Image.open(image_path) as img:
                w, h = img.size
            if resolution_label:
                resolution_label.setText(f"分辨率: {w} × {h}")

            pixmap = QPixmap(image_path)
            if pixmap.isNull():
                label.setText("加载图片失败")
                if resolution_label:
                    resolution_label.setText("分辨率: 加载失败")
                return
            # 直接传原图,AdaptiveImageLabel.resizeEvent 会自动缩放
            label.setPixmap(pixmap)
        except (OSError, Image.UnidentifiedImageError) as e:
            label.setText(f"加载图片失败: {str(e)}")
            if resolution_label:
                resolution_label.setText("分辨率: 加载失败")

    # ================================================================
    #  列表构建工具
    # ================================================================

    def _path_to_tree_values(self, nfo_path):
        relative = os.path.relpath(nfo_path, self.folder_path)
        parts = relative.split(os.sep)
        if len(parts) > 1:
            first = os.sep.join(parts[:-2]) if len(parts) > 2 else ""
            second = parts[-2]
            nfo_name = parts[-1]
        else:
            first, second, nfo_name = "", "", parts[-1]
        return [first, second, nfo_name]

    def rebuild_tree_from_paths(self, paths):
        self._last_selected_item = None
        self.file_tree.clear()
        items = []
        for nfo_path in paths:
            if os.path.exists(nfo_path):
                items.append(QTreeWidgetItem(self._path_to_tree_values(nfo_path)))
        if items:
            self.file_tree.addTopLevelItems(items)

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

        series_counts = {}
        if "系列" in sort_by:
            for _, _, cd in items_with_data:
                s = cd.get('series', '') or ''
                series_counts[s] = series_counts.get(s, 0) + 1

        def get_sort_key(t):
            values, _, cd = t
            if "演员" in sort_by:
                return (", ".join(sorted(cd.get('actors', []) or [])),)
            elif "系列" in sort_by:
                series = cd.get('series', '') or ''
                return (1 if not series else 0, -series_counts.get(series, 0), series)
            elif "评分" in sort_by:
                try:
                    r = cd.get('rating', 0.0)
                    return (-float(r) if r else 0.0,)
                except (ValueError, TypeError):
                    return (0.0,)
            else:
                return (cd.get('release', '') or '0000-00-00',)

        try:
            reverse = "日期" in sort_by
            items_with_data.sort(key=get_sort_key, reverse=reverse)
        except (TypeError, ValueError) as e:
            print(f"排序出错: {str(e)}")
            QMessageBox.warning(self, "警告", f"排序失败: {str(e)}")
            return

        self._last_selected_item = None
        self.file_tree.clear()
        for values, _, _ in items_with_data:
            self.file_tree.addTopLevelItem(QTreeWidgetItem(values))
        self.status_bar.showMessage(f"已按 {sort_by} 排序", 3000)

    def apply_filter(self):
        if not self.folder_path:
            return
        field = self.field_combo.currentText()
        condition = self.condition_combo.currentText()
        filter_text = self.filter_entry.text().strip()

        if not filter_text:
            self.rebuild_tree_from_paths(self.nfo_files)
            self.status_bar.showMessage(f"已清除筛选: 共 {len(self.nfo_files)} 个NFO")
            return

        matched = []
        for nfo_path in self.nfo_files:
            cd = self.nfo_cache.get(nfo_path)
            if not cd:
                continue
            try:
                value = ""
                if field == "标题":
                    value = cd.get('title', '')
                elif field == "标签":
                    value = ", ".join(cd.get('tags', []))
                elif field == "演员":
                    value = ", ".join(cd.get('actors', []))
                elif field == "系列":
                    value = cd.get('series', '')
                elif field == "评分":
                    r = cd.get('rating', 0.0)
                    try:
                        value = str(float(r) if r else 0.0)
                    except (ValueError, TypeError):
                        value = "0.0"

                match = False
                if field == "评分":
                    try:
                        cv = float(value); fv = float(filter_text)
                        match = (cv > fv) if condition == "大于" else (cv < fv)
                    except ValueError:
                        continue
                else:
                    if condition == "包含":
                        match = filter_text.lower() in value.lower()
                    elif condition == "不包含":
                        match = filter_text.lower() not in value.lower()
                if match:
                    matched.append(nfo_path)
            except (ValueError, TypeError) as e:
                print(f"筛选文件 {nfo_path} 时出错: {str(e)}")
                continue

        self.rebuild_tree_from_paths(matched)
        self.status_bar.showMessage(
            f"筛选结果: 匹配 {len(matched)} / 总计 {len(self.nfo_files)}"
        )

    # ================================================================
    #  批量填充 & 新增
    # ================================================================

    def batch_filling(self):
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.setWindowTitle("批量填充")
        dialog.resize(400, 600)
        layout = QVBoxLayout(dialog)

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
                current = widget.text().strip()
                if not current:
                    original_key_release(event); return
                key_text = event.text()
                if key_text.isdigit():
                    if "." in current:
                        main = current.split(".")[0]
                        formatted = f"{main}.{key_text}"
                        try:
                            widget.setText(formatted if float(formatted) <= 9.9 else "9.9")
                        except ValueError:
                            pass
                    elif current.isdigit():
                        try:
                            widget.setText(f"{float(current):.1f}")
                        except ValueError:
                            pass
                    widget.setCursorPosition(len(widget.text()))
            except ValueError as e:
                print(f"评分输入错误: {e}")
            original_key_release(event)

        def on_field_changed():
            sel = None
            for rb in field_buttons:
                if rb.isChecked():
                    sel = rb.text(); break
            if sel == "rating":
                value_entry.keyReleaseEvent = lambda e: format_rating_input(value_entry, e)
                value_entry.setPlaceholderText("输入评分 (如: 8.5)")
            else:
                value_entry.keyReleaseEvent = original_key_release
                value_entry.setPlaceholderText(
                    "输入演员名，多个用逗号分隔" if sel == "actor" else "输入填充值"
                )
            value_entry.setFocus()

        def apply_fill():
            field = None
            for rb in field_buttons:
                if rb.isChecked():
                    field = rb.text(); break
            if not field:
                return
            fill_value = value_entry.text().strip()
            if not fill_value:
                return
            if field == "rating":
                try:
                    float(fill_value)
                except ValueError:
                    QMessageBox.warning(dialog, "评分无效", "评分必须是有效数字")
                    return
            selected = self.file_tree.selectedItems()
            if not selected:
                QMessageBox.warning(dialog, "警告", "请先选择要填充的文件")
                return

            log = []
            for item in selected:
                values = [item.text(i) for i in range(3)]
                nfo_path = (
                    os.path.join(self.folder_path, values[0], values[1], values[2])
                    if values[1]
                    else os.path.join(self.folder_path, values[0], values[2])
                )
                try:
                    tree = parse_xml_file(nfo_path)
                    root = tree.getroot()

                    if field == "actor":
                        sync_actor_nodes(root, split_csv_values(fill_value))
                        log.append(f"{nfo_path}: actor字段填充成功（已保留匹配演员的扩展信息）")
                    elif field == "rating":
                        re_ = root.find("rating")
                        if re_ is None:
                            re_ = ET.SubElement(root, "rating")
                        re_.text = fill_value
                        cr = int(float(fill_value) * 10)
                        ce = root.find("criticrating")
                        if ce is None:
                            ce = ET.SubElement(root, "criticrating")
                        ce.text = str(cr)
                        log.append(f"{nfo_path}: rating填充成功 ({fill_value}, criticrating: {cr})")
                    else:
                        elem = root.find(field)
                        if elem is None:
                            elem = ET.SubElement(root, field)
                        elem.text = fill_value
                        log.append(f"{nfo_path}: {field}字段填充成功")

                    write_xml_root_atomic(root, nfo_path)
                    cd = parse_single_nfo(nfo_path)
                    if cd:
                        self.nfo_cache.set(nfo_path, cd)
                except (ET.ParseError, OSError) as e:
                    log.append(f"{nfo_path}: {field}字段填充失败 - {str(e)}")

            log_text.setText("\n".join(log))
            if self.current_file_path:
                self.load_nfo_fields()

        for rb in field_buttons:
            rb.toggled.connect(on_field_changed)

        apply_btn = QPushButton("应用填充")
        apply_btn.clicked.connect(apply_fill)
        layout.addWidget(apply_btn)

        on_field_changed()
        value_entry.returnPressed.connect(apply_fill)
        dialog.exec()

    def batch_add(self):
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.setWindowTitle("批量新增标签")
        dialog.resize(400, 500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("为选中的NFO文件批量新增标签:"))
        layout.addWidget(QLabel("(仅新增 tag；不会覆盖原有 genre)"))
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
                QMessageBox.warning(dialog, "警告", "请输入标签内容"); return
            selected = self.file_tree.selectedItems()
            if not selected:
                QMessageBox.warning(dialog, "警告", "请先选择要新增的文件"); return

            log = []
            for item in selected:
                values = [item.text(i) for i in range(3)]
                nfo_path = (
                    os.path.join(self.folder_path, values[0], values[1], values[2])
                    if values[1]
                    else os.path.join(self.folder_path, values[0], values[2])
                )
                try:
                    tree = parse_xml_file(nfo_path)
                    root = tree.getroot()
                    existing = []
                    for tag in root.findall("tag"):
                        if tag is not None and tag.text:
                            tt = tag.text.strip()
                            if "," in tt:
                                for st in tt.split(","):
                                    st = st.strip()
                                    if st:
                                        existing.append(st)
                            else:
                                existing.append(tt)
                    existing = list(dict.fromkeys(existing))
                    requested_tags = split_csv_values(add_value)
                    new_tags = [tag for tag in requested_tags if tag not in existing]
                    if not new_tags:
                        log.append(f"{nfo_path}: 所有标签已存在，跳过"); continue
                    all_tags = existing + new_tags
                    replace_text_nodes(root, "tag", all_tags)
                    write_xml_root_atomic(root, nfo_path)
                    cd = parse_single_nfo(nfo_path)
                    if cd:
                        self.nfo_cache.set(nfo_path, cd)
                    log.append(f"{nfo_path}: 成功新增{len(new_tags)}个标签")
                except (ET.ParseError, OSError) as e:
                    log.append(f"{nfo_path}: 标签新增失败 - {str(e)}")

            log_text.setText("\n".join(log))
            if self.current_file_path:
                self.load_nfo_fields()

        btn_row = QHBoxLayout()
        apply_btn = QPushButton("应用新增"); apply_btn.clicked.connect(apply_add)
        close_btn = QPushButton("关闭"); close_btn.clicked.connect(dialog.close)
        btn_row.addWidget(apply_btn); btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        value_entry.returnPressed.connect(apply_add)
        value_entry.setFocus()
        dialog.exec()

    # ================================================================
    #  打开 NFO / 文件夹 / 视频
    # ================================================================

    def open_selected_nfo(self):
        for item in self.file_tree.selectedItems():
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
        for item in self.file_tree.selectedItems():
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
        exts = [".mp4", ".mkv", ".avi", ".mov", ".rm", ".mpeg", ".ts", ".strm"]
        for item in self.file_tree.selectedItems():
            values = [item.text(i) for i in range(3)]
            if values[2]:
                nfo_path = (
                    os.path.join(self.folder_path, values[0], values[1], values[2])
                    if values[1]
                    else os.path.join(self.folder_path, values[0], values[2])
                )
                if os.path.exists(nfo_path):
                    base = os.path.splitext(nfo_path)[0]
                    for ext in exts:
                        video = base + ext
                        if os.path.exists(video):
                            if ext == ".strm":
                                self._play_strm(video)
                            else:
                                try:
                                    subprocess.Popen(["mpvnet", video])
                                except OSError as e:
                                    QMessageBox.critical(self, "错误", f"启动 mpvnet 失败: {e}")
                            return
                    QMessageBox.warning(self, "警告", "未找到匹配的视频文件")
                else:
                    QMessageBox.critical(self, "错误", f"NFO文件不存在: {nfo_path}")

    # ================================================================
    #  预告片
    # ================================================================

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

            config = self.config_manager.load_config()
            configured_dir = config.get("trailer", {}).get("local_directory", "")
            trailer_path = find_numbered_trailer(configured_dir, num_text)
            source_label = "预告片目录"
            if not trailer_path:
                trailer_path = find_trailer_in_movie_folder(folder)
                source_label = "影片目录"

            if trailer_path:
                if self._play_local_media(trailer_path):
                    self.status_bar.showMessage(
                        f"正在播放本地预告片（{source_label}）: {os.path.basename(trailer_path)}",
                        5000,
                    )
                return

            self.status_bar.showMessage(f"本地无预告片，正在查询网络预告片: {num_text}", 0)
            self._fetch_online_trailer(num_text)
        except OSError as exc:
            QMessageBox.critical(self, "错误", f"播放预告片失败: {exc}")

    def _play_local_media(self, media_path: str) -> bool:
        if media_path.lower().endswith(".strm"):
            return self._play_strm(media_path)
        try:
            subprocess.Popen(["mpvnet", media_path])
            return True
        except OSError as exc:
            QMessageBox.critical(self, "错误", f"启动 mpvnet 失败: {exc}")
            return False

    def _fetch_online_trailer(self, num_text):
        def worker():
            api_url = f"https://javp.cc.cd/trailers/{num_text}"
            headers = {
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/120.0.0.0 Safari/537.36"),
                "Accept": "application/json, text/plain, */*",
            }
            trailer_url = None
            err = None
            try:
                response = requests.get(api_url, headers=headers, timeout=10)
                if response.status_code == 200:
                    try:
                        data = response.json()
                        trailer_url = (data.get("trailer") or "").strip()
                        if not trailer_url:
                            err = "API 返回的 trailer 字段为空"
                    except ValueError as e:
                        err = f"解析 API JSON 失败: {str(e)}"
                else:
                    err = f"API 请求失败，状态码: {response.status_code}"
            except requests.exceptions.Timeout:
                err = "API 请求超时"
            except requests.exceptions.RequestException as e:
                err = f"网络请求失败: {str(e)}"

            if trailer_url:
                self._trailer_play_signal.emit(trailer_url, num_text)
            else:
                self._trailer_error_signal.emit(num_text, err or "未知错误")

        threading.Thread(target=worker, daemon=True).start()

    def _play_online_trailer(self, trailer_url, num_text):
        try:
            cmd = ["mpvnet"]
            if "dmm.co.jp" in trailer_url:
                cmd.append("--referrer=https://www.dmm.co.jp/")
            cmd.append(trailer_url)
            subprocess.Popen(cmd)
            self.status_bar.showMessage(f"正在播放网络预告片: {num_text}", 3000)
        except OSError as e:
            QMessageBox.critical(self, "错误", f"启动 mpvnet 失败: {e}")

    def _on_trailer_error(self, num_text, err):
        self.status_bar.showMessage(f"未找到预告片: {num_text} ({err})", 8000)

    def _play_strm(self, strm_path: str) -> bool:
        SUBTITLE_EXTS = (".srt", ".ass", ".ssa", ".vtt", ".sup")
        try:
            with open(strm_path, "r", encoding="utf-8") as f:
                url = f.readline().strip()
        except OSError as e:
            QMessageBox.critical(self, "错误", f"读取 STRM 文件失败: {e}"); return False
        if not url:
            QMessageBox.critical(self, "错误", "STRM 文件内容为空或无效"); return False

        folder = os.path.dirname(strm_path)
        base = os.path.splitext(os.path.basename(strm_path))[0]
        cmd = ["mpvnet", url]
        try:
            for entry in os.scandir(folder):
                name = entry.name
                if not name.lower().endswith(SUBTITLE_EXTS):
                    continue
                rest = name[len(base):]
                if name.startswith(base) and rest.startswith("."):
                    cmd.append(f"--sub-file={entry.path}")
        except OSError as e:
            print(f"扫描字幕文件失败: {e}")
        try:
            subprocess.Popen(cmd); return True
        except OSError as e:
            QMessageBox.critical(self, "错误", f"启动 mpvnet 失败: {e}"); return False

    # ================================================================
    #  番号搜索
    # ================================================================

    def open_number_search(self, event):
        # 注意:event 是 QMouseEvent,需要 .button()
        try:
            if event.button() != Qt.LeftButton:
                return
        except AttributeError:
            return

        num_text = self.fields_entries["num"].text().strip()
        if not num_text:
            return
        try:
            QApplication.clipboard().setText(num_text)
            config = self.config_manager.load_config()
            predefined = config.get('search_sites', {}).get('predefined_sites', {})
            custom = config.get('search_sites', {}).get('custom_sites', [])

            opened = 0
            if predefined.get('supjav', False):
                webbrowser.open(f"https://supjav.com/zh/?s={num_text}"); opened += 1
            if predefined.get('subtitlecat', False):
                webbrowser.open(f"https://www.subtitlecat.com/index.php?search={num_text}"); opened += 1
            for cs in custom:
                if cs.get('enabled', False) and cs.get('name') and cs.get('url_template'):
                    try:
                        if self.search_site_manager.handle_custom_site(cs['url_template'], num_text):
                            opened += 1
                    except OSError as e:
                        print(f"打开自定义网站 {cs['name']} 时出错: {e}")
            if predefined.get('javdb', False):
                self._start_javdb_search(num_text, opened)

            total = opened + (1 if predefined.get('javdb', False) else 0)
            if total == 0:
                self.status_bar.showMessage(f"已复制番号: {num_text}，但未配置搜索网站", 3000)
            else:
                self.status_bar.showMessage(f"已复制番号: {num_text}，正在处理 {total} 个搜索网站", 3000)
        except OSError as e:
            QMessageBox.warning(self, "警告", f"操作失败: {str(e)}")

    def _start_javdb_search(self, num_text, initial_count):
        def search():
            engine = SearchEngine()
            opened = initial_count
            try:
                detail_url = engine.search_javdb(num_text)
                if detail_url:
                    webbrowser.open(detail_url)
                else:
                    webbrowser.open(f"https://javdb.com/search?q={num_text}&f=all")
                opened += 1
            except (requests.RequestException, OSError) as e:
                print(f"JavDB处理失败: {str(e)}")
            self._status_message_signal.emit(f"已处理 {opened} 个搜索网站", 3000)
        threading.Thread(target=search, daemon=True).start()

    def batch_search_numbers(self):
        selected = self.file_tree.selectedItems()
        if not selected:
            QMessageBox.warning(self, "警告", "请先选择要搜索的NFO文件"); return
        numbers = []
        for item in selected:
            values = [item.text(i) for i in range(3)]
            if values[2]:
                nfo_path = (
                    os.path.join(self.folder_path, values[0], values[1], values[2])
                    if values[1]
                    else os.path.join(self.folder_path, values[0], values[2])
                )
                if os.path.exists(nfo_path):
                    try:
                        tree = ET.parse(nfo_path); root = tree.getroot()
                        ne = root.find("num")
                        if ne is not None and ne.text:
                            numbers.append(ne.text.strip())
                    except (ET.ParseError, OSError) as e:
                        print(f"处理NFO失败: {e}")
        if not numbers:
            QMessageBox.warning(self, "警告", "选中的NFO文件中没有找到有效的番号"); return

        QApplication.clipboard().setText(", ".join(numbers))
        try:
            config = self.config_manager.load_config()
            predefined = config.get('search_sites', {}).get('predefined_sites', {})
            custom = config.get('search_sites', {}).get('custom_sites', [])

            total_opened = 0
            for num in numbers:
                if predefined.get('supjav', False):
                    webbrowser.open(f"https://supjav.com/zh/?s={num}"); total_opened += 1
                if predefined.get('subtitlecat', False):
                    webbrowser.open(f"https://www.subtitlecat.com/index.php?search={num}"); total_opened += 1
                for cs in custom:
                    if cs.get('enabled', False) and cs.get('name') and cs.get('url_template'):
                        try:
                            if self.search_site_manager.handle_custom_site(cs['url_template'], num):
                                total_opened += 1
                        except OSError as e:
                            print(f"打开自定义网站 {cs['name']} 时出错: {e}")

            if predefined.get('javdb', False):
                def batch_search():
                    engine = SearchEngine()
                    for num in numbers:
                        try:
                            detail = engine.search_javdb(num)
                            webbrowser.open(detail if detail else f"https://javdb.com/search?q={num}&f=all")
                        except (requests.RequestException, OSError) as e:
                            print(f"JavDB批量搜索 {num} 失败: {str(e)}")
                threading.Thread(target=batch_search, daemon=True).start()

            self.status_bar.showMessage(
                f"已复制 {len(numbers)} 个番号到剪贴板，共打开 {total_opened} 个搜索页面", 5000
            )
        except OSError as e:
            QMessageBox.critical(self, "错误", f"批量搜索失败: {str(e)}")

    def copy_number_to_clipboard(self):
        try:
            num = self.fields_entries["num"].text().strip()
            if num:
                QApplication.clipboard().setText(num)
                self.copy_num_button.setText("✅")
                self.copy_num_button.setToolTip("已复制")
                self.status_bar.showMessage(f"番号已复制: {num}", 2000)
                QTimer.singleShot(2000, self.restore_copy_button)
            else:
                self.status_bar.showMessage("番号为空，无法复制", 2000)
        except OSError as e:
            QMessageBox.warning(self, "警告", f"复制番号失败: {str(e)}")

    def restore_copy_button(self):
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
        text = item.text(0)
        if text == "..":
            parent = os.path.dirname(self.current_target_path)
            if parent != self.current_target_path:
                self.current_target_path = parent
                self.load_target_files(parent)
        else:
            new_path = os.path.join(self.current_target_path, text)
            if os.path.isdir(new_path):
                self.current_target_path = new_path
                self.load_target_files(new_path)

    def focus_file_list(self):
        self.file_tree.setFocus(Qt.OtherFocusReason)
        if not self.file_tree.selectedItems():
            if self.file_tree.topLevelItemCount() > 0:
                self.file_tree.setCurrentItem(self.file_tree.topLevelItem(0))

    def focus_rating(self):
        if "rating" in self.fields_entries:
            w = self.fields_entries["rating"]
            w.setFocus(Qt.OtherFocusReason)
            w.selectAll()

    # ================================================================
    #  图片裁剪 / 删除 / 其他工具
    # ================================================================

    def open_image_and_crop(self, image_type):
        if not self.current_file_path:
            return
        folder = os.path.dirname(self.current_file_path)
        try:
            image_files = [
                f for f in os.listdir(folder)
                if f.lower().endswith(".jpg") and image_type in f.lower()
            ]
        except OSError:
            image_files = []
        if not image_files:
            QMessageBox.critical(self, "错误", f"未找到{image_type}图片"); return

        try:
            from cg_crop import EmbyPosterCrop
            nfo_base = os.path.splitext(os.path.basename(self.current_file_path))[0]
            image_path = preferred_image_file(folder, image_type, nfo_base)
            if not image_path:
                QMessageBox.critical(self, "错误", f"未找到{image_type}图片")
                return
            tree = ET.parse(self.current_file_path); root = tree.getroot()
            has_subtitle = False; mark_type = "none"
            for tag in root.findall("tag"):
                tt = tag.text.lower() if tag.text else ""
                if "中文字幕" in tt:
                    has_subtitle = True
                elif "无码破解" in tt:
                    mark_type = "umr"
                elif "无码流出" in tt:
                    mark_type = "leak"
                elif "无码" in tt:
                    mark_type = "wuma"
                if mark_type != "none":
                    break
            crop_tool = EmbyPosterCrop(nfo_base_name=nfo_base)
            crop_tool.load_initial_image(image_path)
            crop_tool.set_watermark_options(has_subtitle, mark_type)
            crop_tool.exec()
            self._watch_current_item()
            if self.show_images_checkbox.isChecked():
                self.display_image()
        except ImportError:
            QMessageBox.critical(self, "错误", "找不到 cg_crop.py 文件")
        except (ET.ParseError, OSError) as e:
            QMessageBox.critical(self, "错误", f"裁剪工具出错: {str(e)}")

    def delete_selected_folders(self):
        selected = self.file_tree.selectedItems()
        if not selected:
            return

        folders = []
        skipped_root = False
        for item in selected:
            nfo_path = self._nfo_path_from_item(item)
            if not nfo_path:
                continue
            folder = os.path.dirname(nfo_path)
            if same_path(folder, self.folder_path):
                skipped_root = True
                continue
            if os.path.isdir(folder):
                folders.append(folder)
        folders = unique_paths(folders)

        if skipped_root:
            QMessageBox.warning(
                self, "根目录保护",
                "根目录中的 NFO 不能按“删除文件夹”处理，已自动跳过。",
            )
        if not folders:
            return

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要将选中的 {len(folders)} 个文件夹移入回收站吗？",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        deleted = 0
        for folder in folders:
            try:
                if winshell is None:
                    raise OSError("当前系统缺少 Windows 回收站支持（winshell/pywin32）")
                winshell.delete_file(folder)
                deleted += 1
            except OSError as exc:
                QMessageBox.warning(self, "警告", f"删除文件夹失败: {exc}")

        if deleted:
            if self.current_file_path and not os.path.exists(self.current_file_path):
                self.current_file_path = None
                self._last_selected_item = None
                self._watch_current_item()
            self.status_bar.showMessage(f"成功将 {deleted} 个文件夹移入回收站")
            self.load_files_in_folder(auto_select=True, show_progress=False)

    def open_batch_rename_tool(self):
        if not self.folder_path:
            QMessageBox.critical(self, "错误", "请先选择NFO目录"); return
        if not os.path.isdir(self.folder_path):
            QMessageBox.critical(self, "错误", f"目录不存在: {self.folder_path}"); return
        try:
            from cg_rename import RenameToolGUI
            rename_tool = RenameToolGUI(parent=self)
            rename_tool.path_entry.setText(self.folder_path)
            rename_tool.show()
        except ImportError:
            QMessageBox.critical(self, "错误", "找不到重命名工具模块(cg_rename.py)")
        except OSError as e:
            QMessageBox.critical(self, "错误", f"启动重命名工具时出错: {str(e)}")

    def show_photo_wall(self):
        if not self.folder_path:
            QMessageBox.warning(self, "警告", "请先选择NFO目录"); return
        try:
            from cg_photo_wall import PhotoWallDialog

            existing = getattr(self, "photo_wall_dialog", None)
            if existing is not None and existing.isVisible():
                existing.activateWindow()
                existing.raise_()
                return

            # 不再把 folder_path 传进构造函数,避免 PhotoWallDialog.__init__ 自动 os.walk 全库重扫。
            dialog = PhotoWallDialog(None, None)
            dialog.set_editor(self)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.destroyed.connect(lambda *_: setattr(self, "photo_wall_dialog", None))
            self.photo_wall_dialog = dialog
            dialog.show()

            # 等窗口完成初次布局后,直接使用主窗口已加载的 nfo_files + nfo_cache 构建照片墙。
            QTimer.singleShot(0, lambda d=dialog: d.load_from_editor_cache(self))
        except ImportError:
            QMessageBox.critical(self, "错误", "找不到照片墙模块(cg_photo_wall.py)")
        except OSError as e:
            QMessageBox.critical(self, "错误", f"打开照片墙失败: {str(e)}")

    def select_folder_in_tree(self, folder_path):
        try:
            if not self.folder_path:
                self.folder_path = os.path.dirname(folder_path)
                self.load_files_in_folder()
            for i in range(self.file_tree.topLevelItemCount()):
                item = self.file_tree.topLevelItem(i)
                first = item.text(0); second = item.text(1)
                item_path = (os.path.join(self.folder_path, first, second)
                             if second else os.path.join(self.folder_path, first))
                if os.path.normpath(item_path) == os.path.normpath(folder_path):
                    self.file_tree.setCurrentItem(item)
                    self.file_tree.scrollToItem(item)
                    return
            QMessageBox.warning(self, "警告", f"未找到文件夹: {folder_path}")
        except OSError as e:
            QMessageBox.critical(self, "错误", f"选择文件夹失败: {str(e)}")

    # ================================================================
    #  右键 & 拖拽
    # ================================================================

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("刷新").triggered.connect(self.load_files_in_folder)
        if self.file_tree.selectedItems():
            menu.addSeparator()
            menu.addAction("打开NFO").triggered.connect(self.open_selected_nfo)
            menu.addAction("打开文件夹").triggered.connect(self.open_selected_folder)
            menu.addAction("播放视频").triggered.connect(self.open_selected_video)
            menu.addSeparator()
            if len(self.file_tree.selectedItems()) > 1:
                menu.addAction(
                    f"批量搜索番号 ({len(self.file_tree.selectedItems())}个)"
                ).triggered.connect(self.batch_search_numbers)
            else:
                # 模拟左键事件传给 open_number_search
                menu.addAction("搜索番号").triggered.connect(
                    lambda: self.open_number_search(
                        type('Event', (), {'button': lambda _self=None: Qt.LeftButton})()
                    )
                )
            menu.addSeparator()
            menu.addAction("删除文件夹").triggered.connect(self.delete_selected_folders)
            if self.current_target_path:
                menu.addSeparator()
                menu.addAction("移动到目标目录").triggered.connect(self.start_move_thread)
        menu.exec(event.globalPos())

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
            running_threads = []
            if self.load_thread is not None and self.load_thread.isRunning():
                self.load_thread.stop()
                if not self.load_thread.wait(5000):
                    running_threads.append("文件加载")
            if self.move_thread is not None and self.move_thread.isRunning():
                self.move_thread.stop()
                if not self.move_thread.wait(5000):
                    running_threads.append("文件移动")

            if running_threads:
                QMessageBox.warning(
                    self, "操作仍在进行",
                    f"{', '.join(running_threads)}尚未安全停止，请等待操作完成后再关闭。",
                )
                event.ignore()
                return

            if hasattr(self, "event_timer"):
                self.event_timer.stop()
            directories = self.file_watcher.directories()
            files = self.file_watcher.files()
            if directories:
                self.file_watcher.removePaths(directories)
            if files:
                self.file_watcher.removePaths(files)
            self.nfo_cache.clear()
        except (OSError, RuntimeError) as exc:
            print(f"清理资源时出错: {exc}")
        super().closeEvent(event)



# 兼容旧脚本可能导入的类名。
NFOEditorQt5 = NFOEditorQt6


# ================================================================
#  Qt 消息过滤器 - 屏蔽 qfluentwidgets 的 QPainter 警告噪音
# ================================================================

# qfluentwidgets + PySide6 组合下,某些 widget 在 expose / repaint 时会
# 输出 "QPainter::begin: Paint device returned engine == 0, type: 3"
# 系列警告。这是 qfluentwidgets 自绘机制的已知问题,非致命,
# 但会污染终端输出。这里安装消息过滤器,只屏蔽这一类消息,
# 其他真正的错误(critical/fatal)照常显示。
_PAINTER_NOISE_KEYWORDS = (
    "QPainter::begin",
    "Painter not active",
    "QPainter::setCompositionMode",
    "QPainter::fillRect",
    "QPainter::setBrush",
    "QPainter::setPen",
    "QPainter::drawPath",
    "QPainter::setFont",
    "QPainter::end",
)


def _qt_message_handler(mode, context, message):
    # 只在 Warning 级别且匹配关键字时屏蔽,其他消息照常输出到 stderr
    if mode == QtMsgType.QtWarningMsg:
        for keyword in _PAINTER_NOISE_KEYWORDS:
            if keyword in message:
                return
    # 其余消息按 Qt 默认输出
    print(message, file=sys.stderr)


# ================================================================
#  入口
# ================================================================

def main():
    # 安装消息过滤器(必须在 QApplication 之前)
    qInstallMessageHandler(_qt_message_handler)

    # Qt6 默认开启高 DPI 缩放,不再需要 AA_EnableHighDpiScaling
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = NFOEditorQt6()

    import argparse
    parser = argparse.ArgumentParser(description="NFO Editor")
    parser.add_argument("--base-path", help="基础目录路径")
    parser.add_argument("--select-folder", help="要选择的文件夹路径")
    args = parser.parse_args()

    if args.base_path and os.path.exists(args.base_path):
        window.folder_path = os.path.normpath(args.base_path)
        window._set_event_file_for_folder(window.folder_path)
        if args.select_folder:
            window._pending_select_folder = args.select_folder
        window.load_files_in_folder()

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
