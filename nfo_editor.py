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
  9. v9.8.3: 移动文件夹采用 Windows 本地磁盘单 worker 队列：
     同卷 rename 快速路径；跨卷 CopyFileExW/分块复制；移动后批量刷新 UI。
"""

import copy
import errno
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
from pathlib import Path

import requests
try:
    import winshell
except (ImportError, OSError):
    winshell = None
from bs4 import BeautifulSoup

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
    Slot as pyqtSlot,
    qInstallMessageHandler,
)
from PySide6.QtGui import QImageReader, QKeySequence, QPixmap, QShortcut, QTextCursor
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
    is_path_within,
    parse_xml_file,
    preferred_image_file,
    read_series_text,
    same_path,
    split_csv_values,
    sync_actor_nodes,
    sync_series_nodes,
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

    def remove_many(self, paths):
        """批量移除缓存项，避免逐项 list.remove 导致 O(n²) 卡顿。"""
        path_set = set(paths)
        if not path_set:
            return
        for path in path_set:
            self.cache.pop(path, None)
        self.file_paths = [
            path for path in self.file_paths
            if path not in path_set
        ]

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

    def get_stale(self, conn, root_path, nfo_path):
        """Return the last known data even when the file is temporarily unreadable."""
        row = conn.execute(
            """
            SELECT data_json
            FROM nfo_index
            WHERE root_path = ? AND nfo_path = ?
            """,
            (root_path, nfo_path),
        ).fetchone()
        if not row:
            return None
        try:
            data = json.loads(row[0])
            data["path"] = nfo_path
            data["stale"] = True
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
                    # 原子替换或外部写入期间可能短暂无法解析。优先展示旧索引，
                    # 避免项目从树中闪退消失；下一次精确事件会重新解析。
                    stale_data = None
                    try:
                        stale_data = self.disk_index.get_stale(
                            conn, self.folder_path, nfo_path
                        )
                        self.disk_index.mark_seen(
                            conn, self.folder_path, nfo_path, scan_id
                        )
                    except sqlite3.Error:
                        pass
                    if stale_data is not None:
                        relative_path = os.path.relpath(nfo_path, self.folder_path)
                        parts = relative_path.split(os.sep)
                        if len(parts) > 1:
                            first_level = os.sep.join(parts[:-2]) if len(parts) > 2 else ""
                            second_level = parts[-2]
                            nfo_file = parts[-1]
                        else:
                            first_level = second_level = ""
                            nfo_file = parts[-1]
                        batch.append((
                            nfo_path, first_level, second_level, nfo_file, stale_data
                        ))
                    if len(batch) >= self.batch_size:
                        if index_rows:
                            self.disk_index.upsert_many(
                                conn, self.folder_path, index_rows, scan_id
                            )
                        conn.commit()
                        self.batch_ready.emit(batch)
                        batch = []
                        index_rows = []
                    if i % 200 == 0 or i == total:
                        self.progress.emit(i, total, os.path.basename(nfo_path))
                    print(f"解析文件失败，已尝试使用旧索引 {nfo_path}: {e}")
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

        for field in ['num', 'title', 'plot']:
            elem = root.find(field)
            if elem is not None and elem.text:
                data[field] = elem.text.strip()
        data['series'] = read_series_text(root)

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
        for field in ['num', 'title', 'plot']:
            elem = root.find(field)
            if elem is not None and elem.text:
                data[field] = elem.text.strip()
        data['series'] = read_series_text(root)
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

class MoveCancelled(Exception):
    """Internal control-flow exception for a user-cancelled move."""


class FileOperationThread(QThread):
    """Single-worker move queue.

    Priority:
      1. Try os.rename() first.  Same-volume moves only update directory entries
         and are therefore ideal for SSD folders containing STRM/NFO/images.
      2. If rename is unavailable (normally cross-volume), copy one folder at a
         time into a staging directory, then publish it atomically and delete
         the source only after the copy is complete.
      3. Copy operations check the stop event frequently.  On Windows,
         CopyFileExW is used so a large video can be cancelled during the file
         copy instead of waiting for the whole file to finish.

    There is deliberately only ONE active folder operation.  Running several
    HDD cross-volume copies concurrently usually hurts throughput and stability.
    """

    progress = pyqtSignal(int)          # number of queue items consumed
    error = pyqtSignal(str)
    status = pyqtSignal(str)
    moved = pyqtSignal(str, str)        # old_folder, new_folder

    _COPY_STATUS_INTERVAL = 0.25
    _COPY_BUFFER_SIZE = 8 * 1024 * 1024

    def __init__(self, operation_type, **kwargs):
        super().__init__()
        self.operation_type = operation_type
        self.kwargs = kwargs
        self.moved_paths = []
        self._stop_event = threading.Event()
        self.fast_moved_count = 0
        self.copied_moved_count = 0
        self.failed_count = 0
        self.cancelled_during_item = False
        self._last_copy_status_at = 0.0

    @property
    def is_stopped(self):
        return self._stop_event.is_set()

    def run(self):
        try:
            if self.operation_type == "move":
                self.move_files()
        except Exception as exc:
            self.error.emit(f"移动线程异常: {exc}")

    def stop(self):
        self._stop_event.set()

    @staticmethod
    def _human_size(value):
        value = float(max(0, value or 0))
        units = ("B", "KB", "MB", "GB", "TB")
        for unit in units:
            if value < 1024.0 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024.0
        return f"{value:.1f} TB"

    def _emit_copy_status(
        self,
        queue_index,
        queue_total,
        folder_name,
        file_name,
        transferred,
        file_total,
        *,
        force=False,
    ):
        now = time.monotonic()
        if not force and now - self._last_copy_status_at < self._COPY_STATUS_INTERVAL:
            return
        self._last_copy_status_at = now

        if file_total > 0:
            detail = (
                f"{file_name}\n"
                f"{self._human_size(transferred)} / {self._human_size(file_total)}"
            )
        else:
            detail = file_name
        self.status.emit(
            f"队列 {queue_index}/{queue_total}：跨盘复制 {folder_name}\n{detail}"
        )

    def move_files(self):
        queue = unique_paths(self.kwargs.get("src_paths", []))
        dest_parent = self.kwargs.get("dest_path")
        total = len(queue)

        for index, src_path in enumerate(queue, 1):
            if self._stop_event.is_set():
                break

            folder_name = os.path.basename(os.path.normpath(src_path))
            self.status.emit(
                f"队列 {index}/{total}：{folder_name}\n"
                "正在尝试快速移动..."
            )

            consumed = False
            try:
                moved_path, mode = self._move_one(
                    src_path,
                    dest_parent,
                    queue_index=index,
                    queue_total=total,
                )
                if mode == "rename":
                    self.fast_moved_count += 1
                else:
                    self.copied_moved_count += 1

                self.moved_paths.append((src_path, moved_path))
                self.moved.emit(src_path, moved_path)
                consumed = True

            except MoveCancelled:
                self.cancelled_during_item = True
                self.status.emit(
                    f"已取消：{folder_name}\n"
                    "源文件夹保持不变；未继续处理后续队列。"
                )
                break

            except Exception as exc:
                self.failed_count += 1
                consumed = True
                self.error.emit(f"{folder_name}：移动失败：{exc}")

            finally:
                if consumed:
                    self.progress.emit(index)

    def _move_one(self, src_path, dest_parent, *, queue_index, queue_total):
        if self._stop_event.is_set():
            raise MoveCancelled()

        if not src_path or not os.path.isdir(src_path):
            raise OSError(f"源文件夹不存在或不是目录: {src_path}")
        if not dest_parent or not os.path.isdir(dest_parent):
            raise OSError(f"目标目录不存在或不是目录: {dest_parent}")

        src_path = os.path.normpath(src_path)
        dest_parent = os.path.normpath(dest_parent)
        folder_name = os.path.basename(src_path)
        if not folder_name:
            raise OSError("无法确定源文件夹名称")

        dest_path = os.path.join(dest_parent, folder_name)
        if os.path.exists(dest_path):
            raise FileExistsError(f"目标已存在同名文件夹: {dest_path}")

        # Windows 本地磁盘优先走 rename：同卷移动只改目录项，SSD/HDD 都很快。
        # 只有明确的“跨卷/跨设备”错误才降级到复制。权限不足、文件占用等
        # 同卷错误必须直接暴露，避免把一个本应瞬时完成的大视频目录误变成慢复制。
        try:
            os.rename(src_path, dest_path)
            self.status.emit(
                f"队列 {queue_index}/{queue_total}：{folder_name}\n"
                "快速移动完成"
            )
            return dest_path, "rename"
        except OSError as exc:
            is_cross_device = (
                exc.errno == errno.EXDEV
                or getattr(exc, "winerror", None) == 17  # ERROR_NOT_SAME_DEVICE
            )
            if not is_cross_device:
                raise OSError(f"快速移动失败: {exc}") from exc
            if os.path.exists(dest_path):
                raise FileExistsError(f"目标已存在同名文件夹: {dest_path}")

        if self._stop_event.is_set():
            raise MoveCancelled()

        stage_path = os.path.join(
            dest_parent,
            f".{folder_name}.nfoeditor-moving-{os.getpid()}-{time.time_ns()}",
        )

        self.status.emit(
            f"队列 {queue_index}/{queue_total}：跨盘复制 {folder_name}\n"
            "正在创建临时目标..."
        )

        try:
            self._copy_tree_cancelable(
                src_path,
                stage_path,
                queue_index=queue_index,
                queue_total=queue_total,
                folder_name=folder_name,
            )

            if self._stop_event.is_set():
                raise MoveCancelled()

            # Publish only after the whole folder has copied successfully.
            os.rename(stage_path, dest_path)

            # From this point the destination is complete.  Finish source
            # cleanup as one transaction; cancellation applies to the next
            # queue item, not halfway through source deletion.
            self.status.emit(
                f"队列 {queue_index}/{queue_total}：{folder_name}\n"
                "复制完成，正在删除源目录..."
            )
            try:
                shutil.rmtree(src_path)
            except Exception as exc:
                # Destination is already complete.  Never delete it here:
                # retaining both copies is safer than risking data loss.
                raise OSError(
                    "文件已完整复制到目标，但源目录删除失败；"
                    f"为安全起见保留两份。目标: {dest_path}；原因: {exc}"
                ) from exc

            self.status.emit(
                f"队列 {queue_index}/{queue_total}：{folder_name}\n"
                "跨盘移动完成"
            )
            return dest_path, "copy"

        except MoveCancelled:
            self._cleanup_stage_best_effort(stage_path)
            raise
        except Exception:
            self._cleanup_stage_best_effort(stage_path)
            raise

    def _cleanup_stage_best_effort(self, stage_path):
        if not stage_path or not os.path.exists(stage_path):
            return
        try:
            shutil.rmtree(stage_path)
        except OSError:
            # 临时目录清理失败不覆盖原始移动错误；源目录仍保持不变。
            pass

    def _copy_tree_cancelable(
        self,
        src_dir,
        dst_dir,
        *,
        queue_index,
        queue_total,
        folder_name,
    ):
        if self._stop_event.is_set():
            raise MoveCancelled()

        os.makedirs(dst_dir, exist_ok=False)
        try:
            with os.scandir(src_dir) as entries:
                for entry in entries:
                    if self._stop_event.is_set():
                        raise MoveCancelled()

                    src_entry = entry.path
                    dst_entry = os.path.join(dst_dir, entry.name)

                    try:
                        is_dir = entry.is_dir(follow_symlinks=True)
                    except OSError:
                        is_dir = os.path.isdir(src_entry)

                    if is_dir:
                        self._copy_tree_cancelable(
                            src_entry,
                            dst_entry,
                            queue_index=queue_index,
                            queue_total=queue_total,
                            folder_name=folder_name,
                        )
                        continue

                    try:
                        file_size = entry.stat(follow_symlinks=True).st_size
                    except OSError:
                        file_size = 0

                    self._copy_file_cancelable(
                        src_entry,
                        dst_entry,
                        queue_index=queue_index,
                        queue_total=queue_total,
                        folder_name=folder_name,
                        file_name=entry.name,
                        file_total=file_size,
                    )

                    # 轻量校验目标大小，不对 HDD 大视频做全文件哈希，
                    # 避免为安全检查额外读取几十 GB 数据。
                    try:
                        copied_size = os.path.getsize(dst_entry)
                    except OSError as exc:
                        raise OSError(f"无法校验目标文件: {dst_entry}: {exc}") from exc
                    if file_size >= 0 and copied_size != file_size:
                        raise OSError(
                            f"目标文件大小校验失败: {entry.name} "
                            f"({copied_size} != {file_size})"
                        )

            try:
                shutil.copystat(src_dir, dst_dir, follow_symlinks=True)
            except OSError:
                pass
        except Exception:
            raise

    def _copy_file_cancelable(
        self,
        src_file,
        dst_file,
        *,
        queue_index,
        queue_total,
        folder_name,
        file_name,
        file_total,
    ):
        if self._stop_event.is_set():
            raise MoveCancelled()

        os.makedirs(os.path.dirname(dst_file), exist_ok=True)

        if os.name == "nt":
            try:
                self._copy_file_windows(
                    src_file,
                    dst_file,
                    queue_index=queue_index,
                    queue_total=queue_total,
                    folder_name=folder_name,
                    file_name=file_name,
                    file_total=file_total,
                )
                return
            except MoveCancelled:
                raise
            except OSError:
                # CopyFileExW 不可用时退回普通分块复制；仍保持单 worker 串行。
                try:
                    if os.path.exists(dst_file):
                        os.remove(dst_file)
                except OSError:
                    pass

        self._copy_file_chunked(
            src_file,
            dst_file,
            queue_index=queue_index,
            queue_total=queue_total,
            folder_name=folder_name,
            file_name=file_name,
            file_total=file_total,
        )

    @staticmethod
    def _windows_extended_path(path):
        path = os.path.abspath(path)
        if path.startswith("\\\\?\\"):
            return path
        if path.startswith("\\\\"):
            return "\\\\?\\UNC\\" + path[2:]
        return "\\\\?\\" + path

    def _copy_file_windows(
        self,
        src_file,
        dst_file,
        *,
        queue_index,
        queue_total,
        folder_name,
        file_name,
        file_total,
    ):
        import ctypes
        from ctypes import wintypes

        PROGRESS_CONTINUE = 0
        PROGRESS_CANCEL = 1
        COPY_FILE_FAIL_IF_EXISTS = 0x00000001
        ERROR_REQUEST_ABORTED = 1235

        progress_routine_type = ctypes.WINFUNCTYPE(
            wintypes.DWORD,
            ctypes.c_longlong,  # TotalFileSize
            ctypes.c_longlong,  # TotalBytesTransferred
            ctypes.c_longlong,  # StreamSize
            ctypes.c_longlong,  # StreamBytesTransferred
            wintypes.DWORD,     # dwStreamNumber
            wintypes.DWORD,     # dwCallbackReason
            wintypes.HANDLE,    # hSourceFile
            wintypes.HANDLE,    # hDestinationFile
            wintypes.LPVOID,    # lpData
        )

        @progress_routine_type
        def progress_callback(
            total_size,
            transferred,
            _stream_size,
            _stream_transferred,
            _stream_number,
            _callback_reason,
            _source_handle,
            _dest_handle,
            _data,
        ):
            effective_total = int(total_size) if total_size else int(file_total or 0)
            self._emit_copy_status(
                queue_index,
                queue_total,
                folder_name,
                file_name,
                int(transferred),
                effective_total,
            )
            if self._stop_event.is_set():
                return PROGRESS_CANCEL
            return PROGRESS_CONTINUE

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        copy_file_ex = kernel32.CopyFileExW
        copy_file_ex.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            progress_routine_type,
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.BOOL),
            wintypes.DWORD,
        ]
        copy_file_ex.restype = wintypes.BOOL

        cancel_flag = wintypes.BOOL(False)
        ok = copy_file_ex(
            self._windows_extended_path(src_file),
            self._windows_extended_path(dst_file),
            progress_callback,
            None,
            ctypes.byref(cancel_flag),
            COPY_FILE_FAIL_IF_EXISTS,
        )

        if not ok:
            error_code = ctypes.get_last_error()
            if self._stop_event.is_set() or error_code == ERROR_REQUEST_ABORTED:
                try:
                    if os.path.exists(dst_file):
                        os.remove(dst_file)
                except OSError:
                    pass
                raise MoveCancelled()
            raise OSError(
                error_code,
                ctypes.FormatError(error_code),
                src_file,
            )

        self._emit_copy_status(
            queue_index,
            queue_total,
            folder_name,
            file_name,
            file_total,
            file_total,
            force=True,
        )
        try:
            shutil.copystat(src_file, dst_file, follow_symlinks=True)
        except OSError:
            pass

    def _copy_file_chunked(
        self,
        src_file,
        dst_file,
        *,
        queue_index,
        queue_total,
        folder_name,
        file_name,
        file_total,
    ):
        transferred = 0
        try:
            with open(src_file, "rb", buffering=0) as src_handle, open(
                dst_file, "xb", buffering=0
            ) as dst_handle:
                buffer = bytearray(self._COPY_BUFFER_SIZE)
                view = memoryview(buffer)
                while True:
                    if self._stop_event.is_set():
                        raise MoveCancelled()

                    read_count = src_handle.readinto(buffer)
                    if not read_count:
                        break

                    written = 0
                    while written < read_count:
                        if self._stop_event.is_set():
                            raise MoveCancelled()
                        count = dst_handle.write(view[written:read_count])
                        if not count:
                            raise OSError(f"写入目标文件失败: {dst_file}")
                        written += count

                    transferred += read_count
                    self._emit_copy_status(
                        queue_index,
                        queue_total,
                        folder_name,
                        file_name,
                        transferred,
                        file_total,
                    )

            self._emit_copy_status(
                queue_index,
                queue_total,
                folder_name,
                file_name,
                transferred,
                file_total,
                force=True,
            )
            try:
                shutil.copystat(src_file, dst_file, follow_symlinks=True)
            except OSError:
                pass

        except Exception:
            try:
                if os.path.exists(dst_file):
                    os.remove(dst_file)
            except OSError:
                pass
            raise


class BatchNFOOperationThread(QThread):
    """Run batch XML writes outside the GUI thread with exact per-file events."""

    progress = pyqtSignal(int, int, str)
    changed = pyqtSignal(str)
    error = pyqtSignal(str, str)
    completed = pyqtSignal(dict)

    def __init__(self, paths, operation, *, field="", value=""):
        super().__init__()
        self.paths = unique_paths(paths)
        self.operation = operation
        self.field = field
        self.value = value
        self._stop_event = threading.Event()

    @property
    def is_stopped(self):
        return self._stop_event.is_set()

    def stop(self):
        self._stop_event.set()

    def run(self):
        stats = {
            "total": len(self.paths),
            "processed": 0,
            "changed": 0,
            "failed": 0,
            "skipped": 0,
            "canceled": False,
        }
        total = len(self.paths)
        for current, nfo_path in enumerate(self.paths, 1):
            if self._stop_event.is_set():
                stats["canceled"] = True
                break
            try:
                changed = self._apply_one(nfo_path)
                stats["processed"] += 1
                if changed:
                    stats["changed"] += 1
                    self.changed.emit(nfo_path)
                else:
                    stats["skipped"] += 1
            except Exception as exc:
                stats["failed"] += 1
                self.error.emit(nfo_path, str(exc))
            finally:
                self.progress.emit(current, total, os.path.basename(nfo_path))
        self.completed.emit(stats)

    def _apply_one(self, nfo_path):
        tree = parse_xml_file(nfo_path)
        root = tree.getroot()
        changed = False

        if self.operation == "fill":
            if self.field == "actor":
                desired = split_csv_values(self.value)
                before = [
                    (actor.findtext("name") or "").strip()
                    for actor in root.findall("actor")
                    if (actor.findtext("name") or "").strip()
                ]
                if before != desired:
                    sync_actor_nodes(root, desired)
                    changed = True
            elif self.field == "rating":
                rating_value = float(self.value)
                rating_text = self.value.strip()
                rating = root.find("rating")
                if rating is None:
                    rating = ET.SubElement(root, "rating")
                if (rating.text or "").strip() != rating_text:
                    rating.text = rating_text
                    changed = True
                critic_text = str(int(rating_value * 10))
                critic = root.find("criticrating")
                if critic is None:
                    critic = ET.SubElement(root, "criticrating")
                if (critic.text or "").strip() != critic_text:
                    critic.text = critic_text
                    changed = True
            elif self.field == "series":
                before = read_series_text(root)
                if before != self.value.strip():
                    sync_series_nodes(root, self.value)
                    changed = True
            else:
                element = root.find(self.field)
                if element is None:
                    element = ET.SubElement(root, self.field)
                if (element.text or "").strip() != self.value.strip():
                    element.text = self.value.strip()
                    changed = True

        elif self.operation == "add_tags":
            existing = []
            for tag in root.findall("tag"):
                for value in split_csv_values(tag.text or ""):
                    if value not in existing:
                        existing.append(value)
            requested = split_csv_values(self.value)
            additions = [value for value in requested if value not in existing]
            if additions:
                replace_text_nodes(root, "tag", existing + additions)
                changed = True
        else:
            raise ValueError(f"未知批量操作: {self.operation}")

        if changed:
            write_xml_root_atomic(root, nfo_path)
        return changed


class TargetFolderLoadThread(QThread):
    """异步读取目标目录子文件夹，避免大目录双击进入时阻塞 UI。"""

    batch_ready = pyqtSignal(str, list)
    finished_signal = pyqtSignal(str, int)
    error = pyqtSignal(str, str)

    def __init__(self, target_path, batch_size=500):
        super().__init__()
        self.target_path = os.path.normpath(target_path)
        self.batch_size = batch_size
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    @property
    def is_stopped(self):
        return self._stop_event.is_set()

    def run(self):
        try:
            folder_names = []
            with os.scandir(self.target_path) as entries:
                for entry in entries:
                    if self._stop_event.is_set():
                        return
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            folder_names.append(entry.name)
                    except OSError:
                        # 个别目录无权限/瞬间消失时跳过，不影响整个目标目录展示。
                        continue

            if self._stop_event.is_set():
                return

            folder_names.sort(key=str.lower)
            total = len(folder_names)
            for start in range(0, total, self.batch_size):
                if self._stop_event.is_set():
                    return
                self.batch_ready.emit(
                    self.target_path,
                    folder_names[start:start + self.batch_size],
                )
            self.finished_signal.emit(self.target_path, total)
        except OSError as exc:
            self.error.emit(self.target_path, f"加载目标目录失败: {exc}")



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
        self.move_progress = None
        self.target_load_thread = None
        # 已请求停止、但底层目录 I/O 尚未返回的目标目录线程。
        # 必须保留引用，避免 "QThread: Destroyed while thread is still running"；
        # 同时绝不能在 GUI 线程里无期限 wait()。
        self._retired_target_load_threads = set()
        self._target_dir_icon = None
        self.file_watcher = QFileSystemWatcher()
        self._pending_select_folder = None
        self._event_file_path = None
        self._event_file_offset = 0
        self._event_partial_line = ""
        self.library_dirty = False
        self._last_selected_item = None
        self._selection_change_guard = False
        self._move_error_messages = []
        self._move_results = []
        self._target_reload_after_move = False
        self.batch_thread = None
        self.batch_progress = None
        self._batch_errors = []
        self._batch_stats = None
        self._loaded_snapshot = None
        self._nfo_path_index = {}
        self._tree_item_index = {}

        # 事件驱动刷新状态：
        # 定时器只用于合并短时间内的重复事件，不再周期性扫描整个媒体库。
        self._active_operations = set()
        self._pending_library_events = {}
        self._full_reconcile_required = False
        self._full_reconcile_reason = ""
        self._path_redirects = []
        self._refresh_pending = False
        self._rename_tool = None

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
        self.btn_refresh.clicked.connect(self.request_full_refresh)
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
        QShortcut(QKeySequence("F5"), self, self.request_full_refresh)
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
        folder_path = os.path.normpath(folder_path)
        changing_folder = bool(
            self.folder_path and not same_path(folder_path, self.folder_path)
        )
        if changing_folder and not self._confirm_unsaved_changes("切换目录"):
            return False
        if changing_folder:
            self._clear_current_editor_state()
        self.folder_path = folder_path
        settings = QSettings("NFOEditor", "Directories")
        settings.setValue("last_nfo_dir", self.folder_path)
        self._clear_file_watches()
        self._set_event_file_for_folder(self.folder_path)
        self.load_files_in_folder()
        return True

    def request_full_refresh(self):
        """User-requested full scan, guarded against losing unsaved edits."""
        if self._confirm_unsaved_changes("刷新列表"):
            self.load_files_in_folder(auto_select=False)

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
        self._stop_target_load_thread()
        self.current_target_path = None
        self.sorted_tree.clear()
        self.sorted_tree.setEnabled(True)
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
        """移动或批量改名期间锁定会写磁盘/切换根目录的控件。"""
        for name in (
            "btn_move",
            "btn_open_folder",
            "btn_refresh",
            "btn_select_target",
            "btn_save",
            "btn_batch_fill",
            "btn_batch_add",
            "btn_unify_actor",
        ):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.setEnabled(not busy)
        if getattr(self, "file_tree", None) is not None:
            self.file_tree.setEnabled(not busy)
        if getattr(self, "sorted_tree", None) is not None:
            self.sorted_tree.setEnabled(not busy)

    def load_files_in_folder(self, auto_select=True, show_progress=True):
        if not self.folder_path:
            return
        if self._active_operations:
            self._request_full_reconcile("文件操作完成后执行用户请求的列表校验")
            self.status_bar.showMessage(
                "当前正在修改文件，完整刷新已推迟到操作完成后",
                4000,
            )
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

        self._clear_current_editor_state()
        self.file_tree.clear()
        self.nfo_files = []
        self.nfo_cache.clear()
        self._nfo_path_index.clear()
        self._tree_item_index.clear()
        self._loaded_snapshot = None

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
        paths = []
        for nfo_path, first, second, nfo_name, cache_data in batch:
            item = QTreeWidgetItem([first, second, nfo_name])
            items.append(item)
            paths.append(nfo_path)
            self.nfo_cache.set(nfo_path, cache_data)
            self.nfo_files.append(nfo_path)
            self._nfo_path_index[self._event_path_key(nfo_path)] = nfo_path
        if items:
            self.file_tree.addTopLevelItems(items)
            for nfo_path, item in zip(paths, items):
                self._tree_item_index[self._event_path_key(nfo_path)] = item

    def _on_load_finished(self, count, auto_select, selection_path, parsed_count=0):
        if self._show_progress:
            self.progress_bar.hide()

        self.library_dirty = False
        loaded_count = len(self.nfo_files)
        failed_count = max(count - loaded_count, 0)
        total_folders = len(set(os.path.dirname(f) for f in self.nfo_files))
        cache_hits = max(loaded_count - parsed_count, 0)
        stale_count = sum(
            1 for path in self.nfo_files
            if (self.nfo_cache.get(path) or {}).get("stale")
        )
        failure_text = f"，失败 {failed_count} 个" if failed_count else ""
        stale_text = f"，临时使用旧索引 {stale_count} 个" if stale_count else ""
        self.status_bar.showMessage(
            f"加载完成: 成功 {loaded_count}/{count} 个NFO ({total_folders} 个文件夹)，"
            f"本次解析 {parsed_count} 个，缓存命中 {cache_hits} 个{stale_text}{failure_text} - "
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
        if self._pending_library_events or self._full_reconcile_required:
            QTimer.singleShot(0, self._process_pending_library_events)

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
                if same_path(item_path, target_path):
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
        self._event_file_path = os.path.join(
            folder_path, ".nfo_editor_events.jsonl"
        )
        self._event_partial_line = ""
        try:
            self._event_file_offset = os.path.getsize(
                self._event_file_path
            )
        except OSError:
            self._event_file_offset = 0


    def _begin_operation(self, name):
        """登记一个会修改文件系统的内部操作，并暂停事件消费。"""
        if not name:
            return
        self._active_operations.add(str(name))
        self.reload_timer.stop()

    def _end_operation(self, name):
        """结束内部操作；所有嵌套操作结束后再合并处理事件。"""
        if name:
            self._active_operations.discard(str(name))
        if not self._active_operations and (
            self._pending_library_events or self._full_reconcile_required
        ):
            QTimer.singleShot(0, self._process_pending_library_events)

    def _normalize_event_path(self, path):
        if not path:
            return ""
        path = os.path.expandvars(os.path.expanduser(os.fspath(path)))
        if not os.path.isabs(path) and self.folder_path:
            path = os.path.join(self.folder_path, path)
        return os.path.normpath(os.path.abspath(path))

    @staticmethod
    def _event_path_key(path):
        if not path:
            return ""
        return os.path.normcase(os.path.abspath(os.path.normpath(path)))

    @classmethod
    def _path_is_equal_or_child_fast(cls, path, parent):
        """仅做词法路径判断，不触发 realpath/磁盘访问，适合 GUI 热路径。"""
        child_key = cls._event_path_key(path)
        parent_key = cls._event_path_key(parent)
        if not child_key or not parent_key:
            return False
        if child_key == parent_key:
            return True
        try:
            return os.path.commonpath([child_key, parent_key]) == parent_key
        except (OSError, ValueError, TypeError):
            return False


    def _register_path_redirect(self, old_path, new_path):
        old_path = self._normalize_event_path(old_path)
        new_path = self._normalize_event_path(new_path)
        if not old_path or not new_path or same_path(old_path, new_path):
            return

        now = time.monotonic()
        old_key = self._event_path_key(old_path)
        self._path_redirects = [
            entry for entry in self._path_redirects
            if entry[2] > now and self._event_path_key(entry[0]) != old_key
        ]
        # 路径映射仅用于处理同一批延迟事件，避免未来重新创建同名目录时被误重定向。
        self._path_redirects.append((old_path, new_path, now + 15.0))
        self._path_redirects = self._path_redirects[-64:]



    def _redirect_event_path(self, path):
        """把同一批事件中的旧路径映射到重命名/移动后的新路径。"""
        result = self._normalize_event_path(path)
        if not result:
            return ""

        now = time.monotonic()
        self._path_redirects = [
            entry for entry in self._path_redirects if entry[2] > now
        ]
        redirects = sorted(
            self._path_redirects,
            key=lambda entry: len(Path(entry[0]).parts),
            reverse=True,
        )

        for _ in range(8):
            redirected = False
            for old_path, new_path, _expires_at in redirects:
                try:
                    if same_path(result, old_path):
                        result = new_path
                        redirected = True
                        break
                    if is_path_within(result, old_path, include_equal=False):
                        relative = os.path.relpath(result, old_path)
                        result = os.path.normpath(
                            os.path.join(new_path, relative)
                        )
                        redirected = True
                        break
                except (OSError, ValueError):
                    continue
            if not redirected:
                break
        return result

    def _queue_library_event(self, event_type, path="", old_path="", **extra):
        """合并文件系统事件。定时器只做防抖，不再直接全量扫描。"""
        event_type = (event_type or "").strip()
        if not event_type:
            return

        path = self._normalize_event_path(path)
        old_path = self._normalize_event_path(old_path)

        if event_type in {"folder_renamed", "folder_moved"} and old_path and path:
            self._register_path_redirect(old_path, path)

        key_path = old_path if event_type in {
            "folder_renamed", "folder_moved", "folder_deleted"
        } else path
        key = (event_type, self._event_path_key(key_path))
        event = {
            "type": event_type,
            "path": path,
            "old_path": old_path,
        }
        event.update(extra)
        self._pending_library_events[key] = event

        if not self._active_operations:
            self.reload_timer.start(300)

    def _request_full_reconcile(self, reason):
        """仅在无法精确定位变化时，安排一次兜底全量校验。"""
        self._full_reconcile_required = True
        if reason:
            self._full_reconcile_reason = str(reason)
        self.library_dirty = True
        if not self._active_operations:
            self.reload_timer.start(500)

    def _rebuild_path_indexes(self):
        self._nfo_path_index = {
            self._event_path_key(path): path for path in self.nfo_files
        }
        self._tree_item_index = {}
        for index in range(self.file_tree.topLevelItemCount()):
            item = self.file_tree.topLevelItem(index)
            path = self._nfo_path_from_item(item)
            if path:
                self._tree_item_index[self._event_path_key(path)] = item

    def _known_nfo_path(self, path):
        key = self._event_path_key(path)
        known = self._nfo_path_index.get(key)
        if known:
            return known
        for known_path in self.nfo_files:
            if same_path(known_path, path):
                self._nfo_path_index[key] = known_path
                return known_path
        return None

    def _find_tree_item_by_nfo_path(self, nfo_path):
        key = self._event_path_key(nfo_path)
        item = self._tree_item_index.get(key)
        if item is not None:
            return item
        for index in range(self.file_tree.topLevelItemCount()):
            item = self.file_tree.topLevelItem(index)
            item_path = self._nfo_path_from_item(item)
            if item_path and same_path(item_path, nfo_path):
                self._tree_item_index[key] = item
                return item
        return None

    def _insert_nfo_tree_item(self, nfo_path):
        if self._find_tree_item_by_nfo_path(nfo_path) is not None:
            return
        values = self._path_to_tree_values(nfo_path)
        sort_key = tuple(value.casefold() for value in values)
        insert_at = self.file_tree.topLevelItemCount()
        for index in range(self.file_tree.topLevelItemCount()):
            item = self.file_tree.topLevelItem(index)
            current_key = tuple(item.text(i).casefold() for i in range(3))
            if sort_key < current_key:
                insert_at = index
                break
        item = QTreeWidgetItem(values)
        self.file_tree.insertTopLevelItem(insert_at, item)
        key = self._event_path_key(nfo_path)
        self._nfo_path_index[key] = nfo_path
        self._tree_item_index[key] = item

    def _clear_current_editor_state(self):
        self.current_file_path = None
        self._last_selected_item = None
        self._loaded_snapshot = None
        self._clear_file_watches()
        self.clear_images()
        for entry in self.fields_entries.values():
            if isinstance(entry, QTextEdit):
                entry.clear()
            elif isinstance(entry, QLabel):
                entry.setText("")
        self.release_label.setText("")
        self.save_time_label.setText("")

    def _refresh_single_nfo(self, nfo_path, *, reload_current=True):
        """重新解析一个 NFO，并只更新对应缓存/树行。"""
        nfo_path = self._redirect_event_path(nfo_path)
        if (
            not nfo_path
            or not os.path.isfile(nfo_path)
            or not self.folder_path
            or not is_path_within(nfo_path, self.folder_path, include_equal=False)
        ):
            return False

        cache_data = parse_single_nfo(nfo_path)
        if not cache_data:
            return False

        known_path = self._known_nfo_path(nfo_path)
        tree_was_full = self.file_tree.topLevelItemCount() == len(self.nfo_files)

        if known_path and known_path != nfo_path:
            try:
                index = self.nfo_files.index(known_path)
                self.nfo_files[index] = nfo_path
            except ValueError:
                pass
            self.nfo_cache.remove(known_path)
            self._nfo_path_index.pop(self._event_path_key(known_path), None)
            old_item = self._tree_item_index.pop(
                self._event_path_key(known_path), None
            )
            if old_item is not None:
                self._tree_item_index[self._event_path_key(nfo_path)] = old_item
        elif not known_path:
            self.nfo_files.append(nfo_path)
            if tree_was_full:
                self._insert_nfo_tree_item(nfo_path)

        cache_data["path"] = nfo_path
        self.nfo_cache.set(nfo_path, cache_data)
        self._nfo_path_index[self._event_path_key(nfo_path)] = nfo_path

        if reload_current and self._same_path(nfo_path, self.current_file_path):
            # 外部修改不能静默覆盖用户尚未保存的输入。
            if self.has_unsaved_changes():
                self.status_bar.showMessage(
                    "当前 NFO 已被外部修改；请先保存或重新选择该项目以载入新内容",
                    6000,
                )
            else:
                self.load_nfo_fields()
                if self.show_images_checkbox.isChecked():
                    self.display_image()
        return True

    def _remove_folders_from_library(self, folder_paths):
        """一次性从内存模型/树中移除多个目录，避免每个目录都全表扫描。"""
        roots = []
        seen = set()
        for folder_path in folder_paths:
            normalized = self._normalize_event_path(folder_path)
            key = self._event_path_key(normalized)
            if not key or key in seen:
                continue
            seen.add(key)
            roots.append((normalized, key))

        if not roots:
            return 0

        root_specs = [
            (key, key if key.endswith(os.sep) else key + os.sep)
            for _path, key in roots
        ]

        def belongs_to_removed_root(path):
            key = self._event_path_key(path)
            return any(
                key == root_key or key.startswith(root_prefix)
                for root_key, root_prefix in root_specs
            )

        affected = [
            path for path in self.nfo_files
            if belongs_to_removed_root(path)
        ]
        if not affected:
            return 0

        affected_keys = {self._event_path_key(path) for path in affected}
        current_removed = bool(
            self.current_file_path
            and belongs_to_removed_root(self.current_file_path)
        )

        blocker = QSignalBlocker(self.file_tree)
        self.file_tree.setUpdatesEnabled(False)
        try:
            for index in range(self.file_tree.topLevelItemCount() - 1, -1, -1):
                item = self.file_tree.topLevelItem(index)
                item_path = self._nfo_path_from_item(item)
                if item_path and self._event_path_key(item_path) in affected_keys:
                    self.file_tree.takeTopLevelItem(index)
        finally:
            self.file_tree.setUpdatesEnabled(True)
            del blocker

        self.nfo_cache.remove_many(affected)
        self.nfo_files = [
            path for path in self.nfo_files
            if self._event_path_key(path) not in affected_keys
        ]
        self._rebuild_path_indexes()

        if current_removed:
            self._clear_current_editor_state()
            if self.file_tree.topLevelItemCount() > 0:
                first_item = self.file_tree.topLevelItem(0)
                QTimer.singleShot(
                    0,
                    lambda item=first_item: self.file_tree.setCurrentItem(item),
                )
        return len(affected)

    def _remove_folder_from_library(self, folder_path):
        """从内存模型和当前树中移除一个目录下的所有 NFO。"""
        return self._remove_folders_from_library([folder_path])


    def _remap_folder_in_library(self, old_folder, new_folder):
        """目录改名时原地改写路径、缓存键和树行，不重新扫描媒体库。"""
        old_folder = self._normalize_event_path(old_folder)
        new_folder = self._normalize_event_path(new_folder)
        if not old_folder or not new_folder:
            return 0

        remapped = []
        for old_nfo in list(self.nfo_files):
            if not (
                same_path(old_nfo, old_folder)
                or is_path_within(old_nfo, old_folder, include_equal=False)
            ):
                continue
            try:
                relative = os.path.relpath(old_nfo, old_folder)
            except ValueError:
                continue
            new_nfo = os.path.normpath(os.path.join(new_folder, relative))
            item = self._find_tree_item_by_nfo_path(old_nfo)
            data = self.nfo_cache.get(old_nfo)
            self.nfo_cache.remove(old_nfo)
            if data:
                data = dict(data)
                data["path"] = new_nfo
                self.nfo_cache.set(new_nfo, data)
            if item is not None:
                values = self._path_to_tree_values(new_nfo)
                for column, value in enumerate(values):
                    item.setText(column, value)
            remapped.append((old_nfo, new_nfo))

        if not remapped:
            return 0

        mapping = {
            self._event_path_key(old): new for old, new in remapped
        }
        self.nfo_files = [
            mapping.get(self._event_path_key(path), path)
            for path in self.nfo_files
        ]

        if self.current_file_path:
            current_key = self._event_path_key(self.current_file_path)
            if current_key in mapping:
                self.current_file_path = mapping[current_key]
                self._watch_current_item()
        self._rebuild_path_indexes()
        return len(remapped)

    def _scan_folder_into_library(self, folder_path):
        """只扫描新增目录，而不是重新扫描整个根目录。"""
        folder_path = self._normalize_event_path(folder_path)
        if (
            not folder_path
            or not os.path.isdir(folder_path)
            or not self.folder_path
            or not is_path_within(folder_path, self.folder_path, include_equal=True)
        ):
            return 0

        added = 0
        for root, dirs, files in os.walk(folder_path):
            dirs.sort(key=str.casefold)
            for filename in sorted(files, key=str.casefold):
                if not filename.lower().endswith(".nfo"):
                    continue
                nfo_path = os.path.join(root, filename)
                was_known = self._known_nfo_path(nfo_path) is not None
                if self._refresh_single_nfo(nfo_path, reload_current=False) and not was_known:
                    added += 1
        return added

    def _remove_target_folder_item(self, folder_path):
        if not self.current_target_path:
            return
        folder_path = self._normalize_event_path(folder_path)
        if not same_path(os.path.dirname(folder_path), self.current_target_path):
            return
        name = os.path.basename(folder_path)
        for index in range(self.sorted_tree.topLevelItemCount() - 1, -1, -1):
            item = self.sorted_tree.topLevelItem(index)
            if item.text(0) == name:
                self.sorted_tree.takeTopLevelItem(index)

    def _append_target_folder(self, folder_path):
        if not self.current_target_path:
            return
        folder_path = self._normalize_event_path(folder_path)
        if not same_path(os.path.dirname(folder_path), self.current_target_path):
            return
        name = os.path.basename(folder_path)
        if not name:
            return
        for index in range(self.sorted_tree.topLevelItemCount()):
            if self.sorted_tree.topLevelItem(index).text(0) == name:
                return
        if self._target_dir_icon is None:
            self._target_dir_icon = self.style().standardIcon(
                QStyle.StandardPixmap.SP_DirIcon
            )
        item = QTreeWidgetItem([name])
        item.setIcon(0, self._target_dir_icon)
        self.sorted_tree.addTopLevelItem(item)
        self.sorted_tree.sortItems(0, Qt.AscendingOrder)

    def _apply_folder_path_change(self, old_path, new_path, event_type):
        old_path = self._normalize_event_path(old_path)
        new_path = self._normalize_event_path(new_path)
        if not old_path or not new_path:
            self._request_full_reconcile(f"{event_type} 事件缺少旧路径或新路径")
            return

        old_in_library = bool(
            self.folder_path
            and is_path_within(old_path, self.folder_path, include_equal=True)
        )
        new_in_library = bool(
            self.folder_path
            and is_path_within(new_path, self.folder_path, include_equal=True)
        )

        if old_in_library and new_in_library:
            self._remap_folder_in_library(old_path, new_path)
        elif old_in_library:
            self._remove_folder_from_library(old_path)
        elif new_in_library:
            self._scan_folder_into_library(new_path)

        self._remove_target_folder_item(old_path)
        self._append_target_folder(new_path)

    def _process_pending_library_events(self):
        """消费合并后的事件；只有无法精确处理时才执行全量校验。"""
        load_busy = self.load_thread is not None and self.load_thread.isRunning()
        move_busy = self.move_thread is not None and self.move_thread.isRunning()
        if self._active_operations or load_busy or move_busy:
            self._refresh_pending = True
            return

        self.reload_timer.stop()
        self._refresh_pending = False
        events = list(self._pending_library_events.values())
        self._pending_library_events.clear()

        priority = {
            "folder_deleted": 0,
            "folder_renamed": 1,
            "folder_moved": 1,
            "folder_added": 2,
            "nfo_changed": 3,
            "image_changed": 4,
            "directory_changed": 5,
            "full_reconcile": 9,
        }
        events.sort(key=lambda event: priority.get(event.get("type"), 8))

        # “移动到整理目录”通常是从媒体库移出。多选时若逐个调用
        # _remove_folder_from_library，会对整棵树反复扫描，数量一大就会让
        # GUI 看起来像卡死。这里把这类事件合并成一次内存/树更新。
        outbound_moves = []
        remaining_events = []
        for event in events:
            if (
                event.get("type") == "folder_moved"
                and self.folder_path
                and self._path_is_equal_or_child_fast(
                    event.get("old_path", ""), self.folder_path
                )
                and not self._path_is_equal_or_child_fast(
                    event.get("path", ""), self.folder_path
                )
            ):
                outbound_moves.append(event)
            else:
                remaining_events.append(event)

        local_updates = 0
        if outbound_moves:
            local_updates += self._remove_folders_from_library(
                [event.get("old_path", "") for event in outbound_moves]
            )
            for event in outbound_moves:
                self._remove_target_folder_item(event.get("old_path", ""))
                self._append_target_folder(event.get("path", ""))

        for event in remaining_events:
            event_type = event.get("type", "")
            path = event.get("path", "")
            old_path = event.get("old_path", "")

            if event_type == "folder_deleted":
                local_updates += self._remove_folder_from_library(path or old_path)
            elif event_type in {"folder_renamed", "folder_moved"}:
                self._apply_folder_path_change(old_path, path, event_type)
                local_updates += 1
            elif event_type == "folder_added":
                local_updates += self._scan_folder_into_library(
                    self._redirect_event_path(path)
                )
            elif event_type == "nfo_changed":
                if self._refresh_single_nfo(
                    self._redirect_event_path(path),
                    reload_current=event.get("reload_current", True),
                ):
                    local_updates += 1
            elif event_type == "image_changed":
                event_path = self._redirect_event_path(path)
                current_dir = (
                    os.path.dirname(self.current_file_path)
                    if self.current_file_path else ""
                )
                event_dir = (
                    event_path if os.path.isdir(event_path)
                    else os.path.dirname(event_path)
                )
                if (
                    current_dir
                    and self._same_path(event_dir, current_dir)
                    and self.show_images_checkbox.isChecked()
                ):
                    self.display_image()
                    local_updates += 1
            elif event_type == "directory_changed":
                current_dir = (
                    os.path.dirname(self.current_file_path)
                    if self.current_file_path else ""
                )
                if current_dir and self._same_path(path, current_dir):
                    if not os.path.isdir(current_dir) or not os.path.isfile(
                        self.current_file_path
                    ):
                        self._request_full_reconcile(
                            "当前目录在未知外部操作中消失或 NFO 被删除"
                        )
                    elif self.show_images_checkbox.isChecked():
                        self.display_image()
                    self._watch_current_item()
            elif event_type in {"full_reconcile", "library_dirty", "refresh_list"}:
                self._request_full_reconcile(
                    event.get("reason") or "收到无法精确定位的外部变化"
                )

        if self._full_reconcile_required:
            self.reload_timer.stop()
            reason = self._full_reconcile_reason or "文件列表需要校验"
            self._full_reconcile_required = False
            self._full_reconcile_reason = ""
            self.library_dirty = False
            self.status_bar.showMessage(f"{reason}，正在执行一次完整校验...", 5000)
            if self.folder_path:
                self.load_files_in_folder(auto_select=False, show_progress=False)
            return

        self.library_dirty = False
        if local_updates:
            self.status_bar.showMessage(
                f"已局部同步 {local_updates} 项变化，无需扫描整个目录",
                4000,
            )


    def _poll_external_events(self):
        """增量读取外部事件文件，并保留尚未写完的最后一行。"""
        if not self._event_file_path or not os.path.exists(
            self._event_file_path
        ):
            return

        try:
            size = os.path.getsize(self._event_file_path)
            if size < self._event_file_offset:
                self._event_file_offset = 0
                self._event_partial_line = ""
            if size == self._event_file_offset:
                return

            with open(
                self._event_file_path, "r", encoding="utf-8"
            ) as handle:
                handle.seek(self._event_file_offset)
                chunk = handle.read()
                self._event_file_offset = handle.tell()
        except OSError:
            return

        buffer = self._event_partial_line + chunk
        if buffer and not buffer.endswith(("\n", "\r")):
            completed, separator, partial = buffer.rpartition("\n")
            if separator:
                lines = completed.splitlines()
                self._event_partial_line = partial
            else:
                self._event_partial_line = buffer
                lines = []
        else:
            lines = buffer.splitlines()
            self._event_partial_line = ""

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
        """接收独立外部程序写入的结构化事件。"""
        event_type = (event.get("type") or "").strip()
        event_path = event.get("path", "")
        old_path = event.get("old_path", "")

        if event_type == "task_started":
            self.status_bar.showMessage(
                event.get("message", "外部组件正在处理..."), 5000
            )
            return

        if event_type == "task_finished":
            self.status_bar.showMessage(
                event.get("message", "外部组件处理完成"), 5000
            )
            if self._pending_library_events:
                self.reload_timer.start(150)
            return

        if event_type == "nfo_changed" and event_path:
            self._queue_library_event("nfo_changed", event_path)
            return

        if event_type == "image_changed" and event_path:
            self._queue_library_event("image_changed", event_path)
            return

        if event_type in {"folder_renamed", "folder_moved"}:
            if old_path and event_path:
                self._queue_library_event(
                    event_type, event_path, old_path=old_path
                )
            else:
                self._request_full_reconcile(
                    f"{event_type} 事件没有同时提供 old_path 和 path"
                )
            return

        if event_type == "folder_deleted":
            deleted_path = event_path or old_path
            if deleted_path:
                self._queue_library_event("folder_deleted", deleted_path)
            else:
                self._request_full_reconcile("folder_deleted 事件缺少路径")
            return

        if event_type == "folder_added" and event_path:
            self._queue_library_event("folder_added", event_path)
            return

        if event_type in {"library_dirty", "refresh_list", "full_reconcile"}:
            self._request_full_reconcile(
                event.get("reason") or "外部组件无法提供精确变更路径"
            )


    def on_file_changed(self, path):
        # 内部保存、批量改名等操作会主动提交精确事件；
        # 此时忽略 QFileSystemWatcher 的重复通知。
        if self._active_operations:
            QTimer.singleShot(100, self._watch_current_item)
            return
        if self._same_path(path, self.current_file_path):
            self._queue_library_event("nfo_changed", path)
            self.status_bar.showMessage("检测到当前 NFO 外部修改", 3000)
            QTimer.singleShot(100, self._watch_current_item)


    def on_directory_changed(self, path):
        # 这里只监控当前影片文件夹；目录事件优先解释为图片变化。
        # 只有当前目录或 NFO 真正消失时，才请求一次完整校验。
        if self._active_operations:
            QTimer.singleShot(100, self._watch_current_item)
            return
        self._queue_library_event("directory_changed", path)


    def _delayed_reload(self):
        # 定时器仅负责合并事件，不再无条件调用 load_files_in_folder()。
        self._process_pending_library_events()

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
        if not self._confirm_unsaved_changes("切换文件"):
            self._restore_tree_selection(previous_item)
            return

        if not os.path.exists(new_path):
            self.file_tree.takeTopLevelItem(self.file_tree.indexOfTopLevelItem(item))
            known = self._known_nfo_path(new_path)
            if known:
                try:
                    self.nfo_files.remove(known)
                except ValueError:
                    pass
                self.nfo_cache.remove(known)
            key = self._event_path_key(new_path)
            self._nfo_path_index.pop(key, None)
            self._tree_item_index.pop(key, None)
            self.current_file_path = None
            self._last_selected_item = None
            self._loaded_snapshot = None
            self._watch_current_item()
            return

        self.current_file_path = new_path
        self._last_selected_item = item
        self._watch_current_item()
        self.load_nfo_fields()
        if self.show_images_checkbox.isChecked():
            self.display_image()

    @staticmethod
    def _snapshot_from_root(root):
        def text_of(tag):
            elem = root.find(tag)
            return (elem.text or "").strip() if elem is not None else ""

        return {
            "title": text_of("title"),
            "plot": text_of("plot"),
            "series": read_series_text(root),
            "rating": text_of("rating"),
            "actors": [
                (actor.findtext("name") or "").strip()
                for actor in root.findall("actor")
                if (actor.findtext("name") or "").strip()
            ],
            "tags": [
                (tag.text or "").strip()
                for tag in root.findall("tag")
                if (tag.text or "").strip()
            ],
        }

    def _capture_editor_snapshot(self):
        return {
            "title": self.fields_entries["title"].toPlainText().strip(),
            "plot": self.fields_entries["plot"].toPlainText().strip(),
            "series": self.fields_entries["series"].toPlainText().strip(),
            "rating": self.fields_entries["rating"].toPlainText().strip(),
            "actors": split_csv_values(
                self.fields_entries["actors"].toPlainText()
            ),
            "tags": split_csv_values(
                self.fields_entries["tags"].toPlainText()
            ),
        }

    def load_nfo_fields(self):
        for entry in self.fields_entries.values():
            if isinstance(entry, QTextEdit):
                entry.clear()
            elif isinstance(entry, QLabel):
                entry.setText("")

        if not self.current_file_path:
            self._loaded_snapshot = None
            return

        try:
            tree = parse_xml_file(self.current_file_path)
            root = tree.getroot()

            for field in ("title", "plot", "rating", "num"):
                elem = root.find(field)
                value = (elem.text or "").strip() if elem is not None else ""
                widget = self.fields_entries.get(field)
                if widget:
                    if isinstance(widget, QLabel):
                        widget.setText(value)
                    else:
                        widget.setPlainText(value)
            self.fields_entries["series"].setPlainText(read_series_text(root))

            actors = [
                (actor.findtext("name") or "").strip()
                for actor in root.findall("actor")
                if (actor.findtext("name") or "").strip()
            ]
            self.fields_entries["actors"].setPlainText(", ".join(actors))

            tags = [
                (tag.text or "").strip()
                for tag in root.findall("tag")
                if (tag.text or "").strip()
            ]
            self.fields_entries["tags"].setPlainText(", ".join(tags))

            release_elem = root.find("release")
            self.release_label.setText(
                (release_elem.text or "").strip()
                if release_elem is not None else ""
            )
            self._loaded_snapshot = self._snapshot_from_root(root)
        except (ET.ParseError, OSError) as exc:
            self._loaded_snapshot = None
            QMessageBox.critical(self, "错误", f"加载NFO文件失败: {exc}")

    # ================================================================
    #  保存
    # ================================================================


    def save_changes(self):
        if not self.current_file_path:
            return False
        if self._active_operations - {"save"}:
            self.status_bar.showMessage(
                "当前正在移动或重命名文件，暂不能保存 NFO",
                4000,
            )
            return False

        nfo_path = self.current_file_path
        self._begin_operation("save")
        try:
            tree = parse_xml_file(nfo_path)
            root = tree.getroot()

            title = self.fields_entries["title"].toPlainText().strip()
            plot = self.fields_entries["plot"].toPlainText().strip()
            actors = split_csv_values(self.fields_entries["actors"].toPlainText())
            series = self.fields_entries["series"].toPlainText().strip()
            tags = split_csv_values(self.fields_entries["tags"].toPlainText())
            rating = self.fields_entries["rating"].toPlainText().strip()

            for field, value in {
                "title": title, "plot": plot, "rating": rating,
            }.items():
                elem = root.find(field)
                if elem is None:
                    elem = ET.SubElement(root, field)
                elem.text = value

            sync_series_nodes(root, series)

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

            sync_actor_nodes(root, actors)
            replace_text_nodes(root, "tag", tags)

            self._clear_file_watches()
            write_xml_root_atomic(root, nfo_path)

            # 直接更新当前 NFO 的缓存，不触发整个目录扫描。
            self._refresh_single_nfo(nfo_path, reload_current=False)
            self._loaded_snapshot = self._capture_editor_snapshot()

            save_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.save_time_label.setText(f"保存时间: {save_time}")
            self.status_bar.showMessage("NFO 已保存并局部更新", 3000)
            return True
        except (ET.ParseError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "错误", f"保存NFO文件失败: {exc}")
            return False
        finally:
            self._watch_current_item()
            self._end_operation("save")

    # ================================================================
    #  移动文件
    # ================================================================


    def start_move_thread(self):
        if not self._confirm_unsaved_changes("移动文件夹"):
            return
        # 工具栏按钮被禁用时，Ctrl+Right 快捷键仍可能触发此方法。
        if self.move_thread is not None and self.move_thread.isRunning():
            self.status_bar.showMessage("文件移动正在进行，请勿重复操作", 3000)
            return

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

            dest_path = os.path.normpath(self.current_target_path)
            if not os.path.isdir(dest_path):
                QMessageBox.critical(self, "错误", f"目标目录不存在: {dest_path}")
                return

            invalid_moves = []
            valid_src_paths = []
            for src_path in src_paths:
                try:
                    common = os.path.commonpath([
                        os.path.abspath(src_path), os.path.abspath(dest_path)
                    ])
                except ValueError:
                    common = ""
                if same_path(src_path, dest_path) or same_path(common, src_path):
                    invalid_moves.append(src_path)
                else:
                    valid_src_paths.append(src_path)

            if invalid_moves:
                QMessageBox.warning(
                    self,
                    "目标目录无效",
                    "不能把文件夹移动到它自己或它的子目录中，已自动跳过这些项。",
                )

            src_paths = unique_paths(valid_src_paths)
            if not src_paths:
                QMessageBox.warning(self, "警告", "没有有效的源文件夹可以移动")
                return

            self.reload_timer.stop()
            self._target_reload_after_move = bool(
                self.target_load_thread is not None
                and self.target_load_thread.isRunning()
            )
            self._stop_target_load_thread()
            self._clear_file_watches()
            self._move_error_messages = []
            self._move_results = []

            if self.move_progress is not None:
                try:
                    self.move_progress.close()
                    self.move_progress.deleteLater()
                except RuntimeError:
                    pass
                self.move_progress = None

            self.move_progress = QProgressDialog(
                f"已建立移动队列，共 {len(src_paths)} 个文件夹",
                "取消",
                0,
                len(src_paths),
                self,
            )
            self.move_progress.setWindowTitle("移动队列")
            self.move_progress.setWindowModality(Qt.WindowModal)
            self.move_progress.setMinimumDuration(0)
            self.move_progress.setAutoClose(False)
            self.move_progress.setAutoReset(False)
            self.move_progress.setValue(0)

            self.move_thread = FileOperationThread(
                operation_type="move",
                src_paths=src_paths,
                dest_path=dest_path,
            )
            self.move_thread.progress.connect(self.move_progress.setValue)
            self.move_thread.status.connect(self.move_progress.setLabelText)
            self.move_thread.error.connect(self._on_move_error)
            self.move_thread.moved.connect(self._on_move_item_moved)
            self.move_thread.finished.connect(self.on_move_finished)
            self.move_progress.canceled.connect(self._cancel_move)

            self._begin_operation("move")
            self._set_moving_busy(True)
            self.move_thread.start()
        except Exception as exc:
            self._cleanup_move_objects()
            self._end_operation("move")
            self._watch_current_item()
            QMessageBox.critical(self, "错误", f"启动移动操作时出错: {exc}")

    @pyqtSlot(str)
    def _on_move_error(self, message):
        """在 GUI 线程记录工作线程错误，完成后统一显示。"""
        self._move_error_messages.append(message)
        self.status_bar.showMessage(message, 5000)


    @pyqtSlot(str, str)
    def _on_move_item_moved(self, old_path, new_path):
        """记录单个成功移动结果，完成后由事件队列局部更新。"""
        self._move_results.append((old_path, new_path))
        self._queue_library_event(
            "folder_moved", new_path, old_path=old_path
        )

    @pyqtSlot()
    def _cancel_move(self):
        thread = self.move_thread
        if thread is not None and thread.isRunning():
            thread.stop()
            if self.move_progress is not None:
                self.move_progress.setLabelText(
                    "正在取消当前任务...\n"
                    "跨盘复制会在当前复制块/系统回调处停止；"
                    "已完成的项目不会回滚。"
                )
            self.status_bar.showMessage("正在取消移动队列...", 3000)


    def _cleanup_move_objects(self):
        """只清理移动相关 Qt 对象；可从成功、失败和启动异常路径重复调用。"""
        progress = self.move_progress
        self.move_progress = None
        if progress is not None:
            try:
                progress.canceled.disconnect(self._cancel_move)
            except (RuntimeError, TypeError):
                pass
            try:
                progress.close()
                progress.deleteLater()
            except RuntimeError:
                pass

        thread = self.move_thread
        self.move_thread = None
        if thread is not None:
            for signal, slot in (
                (thread.error, self._on_move_error),
                (thread.moved, self._on_move_item_moved),
                (thread.finished, self.on_move_finished),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
            thread.deleteLater()

        self._set_moving_busy(False)




    @pyqtSlot()
    def on_move_finished(self):
        thread = self.move_thread
        canceled = bool(thread is not None and thread.is_stopped)
        errors = list(self._move_error_messages)

        # QThread.finished 到达时，线程内 moved_paths 已经完整。
        # 用它补齐可能尚未投递到 GUI 事件队列的 moved 信号，避免漏掉局部更新。
        successful_moves = []
        seen_moves = set()
        for old_path, new_path in (
            list(getattr(thread, "moved_paths", []) or [])
            + list(self._move_results)
        ):
            key = (
                self._event_path_key(old_path),
                self._event_path_key(new_path),
            )
            if key in seen_moves:
                continue
            seen_moves.add(key)
            successful_moves.append((old_path, new_path))
            self._queue_library_event(
                "folder_moved", new_path, old_path=old_path
            )

        moved_count = len(successful_moves)
        fast_count = int(getattr(thread, "fast_moved_count", 0) or 0)
        copied_count = int(getattr(thread, "copied_moved_count", 0) or 0)
        failed_count = int(getattr(thread, "failed_count", 0) or 0)
        reload_target = self._target_reload_after_move
        self._target_reload_after_move = False
        self._move_error_messages = []
        self._move_results = []

        self._cleanup_move_objects()
        self._end_operation("move")
        QTimer.singleShot(0, self._watch_current_item)

        # 只有移动开始时目标目录仍在异步加载，才重新读取目标目录的直属子文件夹。
        # 这不是 NFO 全库扫描；正常情况下由 moved 结果直接追加目标项。
        if reload_target and self.current_target_path:
            QTimer.singleShot(
                0,
                lambda path=self.current_target_path: self.load_target_files(path),
            )

        if errors:
            visible_errors = errors[:8]
            details = "\n\n".join(visible_errors)
            if len(errors) > len(visible_errors):
                details += f"\n\n其余 {len(errors) - len(visible_errors)} 个错误已省略。"
            QMessageBox.critical(self, "移动未完全成功", details)
        elif canceled:
            self.status_bar.showMessage(
                f"移动队列已取消：成功 {moved_count} 个"
                f"（快速 {fast_count}，复制 {copied_count}），"
                f"失败 {failed_count} 个",
                6000,
            )
        else:
            self.status_bar.showMessage(
                f"移动队列完成：成功 {moved_count} 个"
                f"（快速 {fast_count}，复制 {copied_count}），"
                f"失败 {failed_count} 个",
                6000,
            )

    # ================================================================
    #  目标目录
    # ================================================================

    @pyqtSlot()
    def _cleanup_retired_target_load_threads(self):
        """清理已经真正退出的目标目录线程；绝不阻塞 GUI 等待 I/O。"""
        for thread in list(self._retired_target_load_threads):
            if thread.isRunning():
                continue
            self._retired_target_load_threads.discard(thread)
            try:
                thread.finished.disconnect(
                    self._cleanup_retired_target_load_threads
                )
            except (RuntimeError, TypeError):
                pass
            try:
                thread.deleteLater()
            except RuntimeError:
                pass

    def _stop_target_load_thread(self):
        """请求停止目标目录读取，但不在 GUI 主线程无期限 wait()。

        os.scandir()/entry.is_dir() 在休眠机械盘或异常磁盘 I/O 时也可能长时间
        阻塞。旧实现直接 wait() 会把 Qt 事件循环一起堵住，Windows 随即显示
        “程序未响应”。这里把仍在退出的线程转入 retired 集合，等 finished
        信号到达后再清理。
        """
        thread = self.target_load_thread
        if thread is None:
            self._cleanup_retired_target_load_threads()
            return

        self.target_load_thread = None

        if thread.isRunning():
            thread.stop()
            for signal, slot in (
                (thread.batch_ready, self._on_target_batch_ready),
                (thread.finished_signal, self._on_target_load_finished),
                (thread.error, self._on_target_load_error),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass

            self._retired_target_load_threads.add(thread)
            try:
                thread.finished.connect(
                    self._cleanup_retired_target_load_threads
                )
            except (RuntimeError, TypeError):
                pass
            QTimer.singleShot(0, self._cleanup_retired_target_load_threads)
            return

        try:
            thread.deleteLater()
        except RuntimeError:
            pass
        self._cleanup_retired_target_load_threads()


    def _add_target_parent_item(self, target_path):
        if os.path.dirname(target_path) == target_path:
            return
        parent_item = QTreeWidgetItem([".."])
        parent_item.setIcon(
            0,
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp),
        )
        self.sorted_tree.addTopLevelItem(parent_item)

    def _target_tree_should_be_enabled(self):
        return not (self.move_thread is not None and self.move_thread.isRunning())

    def load_target_files(self, target_path):
        target_path = os.path.normpath(target_path)
        self.current_target_path = target_path
        self._stop_target_load_thread()
        self.sorted_tree.clear()
        self._add_target_parent_item(target_path)
        self.sorted_tree.setEnabled(False)
        self.status_bar.showMessage(f"正在读取目标目录: {target_path}")

        self.target_load_thread = TargetFolderLoadThread(target_path, batch_size=500)
        self.target_load_thread.batch_ready.connect(self._on_target_batch_ready)
        self.target_load_thread.finished_signal.connect(self._on_target_load_finished)
        self.target_load_thread.error.connect(self._on_target_load_error)
        self.target_load_thread.start()

    def _on_target_batch_ready(self, target_path, folder_names):
        sender_thread = self.sender()
        if (
            sender_thread is not None
            and sender_thread is not self.target_load_thread
        ):
            return
        if not same_path(target_path, self.current_target_path):
            return
        if self._target_dir_icon is None:
            self._target_dir_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)

        items = []
        for name in folder_names:
            item = QTreeWidgetItem([name])
            item.setIcon(0, self._target_dir_icon)
            items.append(item)

        if items:
            self.sorted_tree.setUpdatesEnabled(False)
            self.sorted_tree.addTopLevelItems(items)
            self.sorted_tree.setUpdatesEnabled(True)

    def _on_target_load_finished(self, target_path, folder_count):
        sender_thread = self.sender()
        if (
            sender_thread is not None
            and sender_thread is not self.target_load_thread
        ):
            return
        if not same_path(target_path, self.current_target_path):
            return
        self.sorted_tree.setEnabled(self._target_tree_should_be_enabled())
        self.status_bar.showMessage(f"目标目录: {target_path} (共{folder_count}个文件夹)")
        thread = self.target_load_thread
        self.target_load_thread = None
        if thread is not None:
            thread.deleteLater()

    def _on_target_load_error(self, target_path, error_msg):
        sender_thread = self.sender()
        if (
            sender_thread is not None
            and sender_thread is not self.target_load_thread
        ):
            return
        if not same_path(target_path, self.current_target_path):
            return
        self.sorted_tree.setEnabled(self._target_tree_should_be_enabled())
        thread = self.target_load_thread
        self.target_load_thread = None
        if thread is not None:
            thread.deleteLater()
        QMessageBox.critical(self, "错误", error_msg)

    # ================================================================
    #  未保存检测
    # ================================================================

    def has_unsaved_changes(self):
        if not self.current_file_path or not os.path.exists(self.current_file_path):
            return False
        if self._loaded_snapshot is None:
            try:
                root = parse_xml_file(self.current_file_path).getroot()
                self._loaded_snapshot = self._snapshot_from_root(root)
            except (ET.ParseError, OSError):
                return False
        return self._capture_editor_snapshot() != self._loaded_snapshot

    def _confirm_unsaved_changes(self, action_text="继续"):
        if not self.has_unsaved_changes():
            return True
        reply = QMessageBox.question(
            self,
            "保存更改",
            f"当前 NFO 有未保存的更改。是否先保存再{action_text}？",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
        )
        if reply == QMessageBox.Cancel:
            return False
        if reply == QMessageBox.Yes:
            return self.save_changes()
        return True

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
        """单次解码图片并应用 EXIF 方向，再交给自适应标签缩放。"""
        try:
            reader = QImageReader(image_path)
            reader.setAutoTransform(True)
            image = reader.read()
            if image.isNull():
                raise OSError(reader.errorString() or "无法读取图片")
            if resolution_label:
                resolution_label.setText(
                    f"分辨率: {image.width()} × {image.height()}"
                )
            pixmap = QPixmap.fromImage(image)
            if pixmap.isNull():
                raise OSError("无法创建图片预览")
            label.setPixmap(pixmap)
        except OSError as exc:
            label.setText(f"加载图片失败: {exc}")
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
        self._tree_item_index.clear()
        pairs = []
        for nfo_path in paths:
            if os.path.exists(nfo_path):
                pairs.append((nfo_path, QTreeWidgetItem(self._path_to_tree_values(nfo_path))))
        if pairs:
            self.file_tree.addTopLevelItems([item for _, item in pairs])
            for nfo_path, item in pairs:
                self._tree_item_index[self._event_path_key(nfo_path)] = item

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

    def _selected_nfo_paths(self):
        paths = []
        for item in self.file_tree.selectedItems():
            path = self._nfo_path_from_item(item)
            if path and os.path.isfile(path):
                paths.append(path)
        return unique_paths(paths)

    def _start_batch_nfo_operation(
        self, paths, operation, *, field="", value=""
    ):
        if self.batch_thread is not None and self.batch_thread.isRunning():
            QMessageBox.warning(self, "批量任务", "已有批量任务正在运行")
            return False
        if self._active_operations:
            QMessageBox.warning(self, "批量任务", "当前有其他文件操作正在进行")
            return False
        if not paths:
            QMessageBox.warning(self, "批量任务", "没有有效的 NFO 文件")
            return False
        if not self._confirm_unsaved_changes("执行批量操作"):
            return False

        self._batch_errors = []
        self._batch_stats = None
        self.batch_progress = QProgressDialog(
            "准备批量处理...", "取消", 0, len(paths), self
        )
        self.batch_progress.setWindowModality(Qt.WindowModal)
        self.batch_progress.setMinimumDuration(0)
        self.batch_progress.setAutoClose(False)
        self.batch_progress.setAutoReset(False)
        self.batch_progress.setValue(0)

        self.batch_thread = BatchNFOOperationThread(
            paths, operation, field=field, value=value
        )
        self.batch_thread.progress.connect(self._on_batch_operation_progress)
        self.batch_thread.changed.connect(self._on_batch_operation_changed)
        self.batch_thread.error.connect(self._on_batch_operation_error)
        self.batch_thread.completed.connect(self._on_batch_operation_completed)
        self.batch_thread.finished.connect(self._on_batch_operation_thread_finished)
        self.batch_progress.canceled.connect(self._cancel_batch_operation)

        self._begin_operation("batch")
        self._set_moving_busy(True)
        self._clear_file_watches()
        self.batch_thread.start()
        return True

    @pyqtSlot(int, int, str)
    def _on_batch_operation_progress(self, current, total, filename):
        if self.batch_progress is not None:
            self.batch_progress.setMaximum(total)
            self.batch_progress.setValue(current)
            self.batch_progress.setLabelText(
                f"正在处理 {current}/{total}: {filename}"
            )

    @pyqtSlot(str)
    def _on_batch_operation_changed(self, nfo_path):
        self._queue_library_event(
            "nfo_changed", nfo_path, reload_current=True
        )

    @pyqtSlot(str, str)
    def _on_batch_operation_error(self, nfo_path, message):
        self._batch_errors.append(f"{nfo_path}: {message}")

    @pyqtSlot(dict)
    def _on_batch_operation_completed(self, stats):
        self._batch_stats = dict(stats)

    @pyqtSlot()
    def _cancel_batch_operation(self):
        if self.batch_thread is not None and self.batch_thread.isRunning():
            self.batch_thread.stop()
            if self.batch_progress is not None:
                self.batch_progress.setLabelText(
                    "正在取消，等待当前 NFO 写入完成..."
                )

    @pyqtSlot()
    def _on_batch_operation_thread_finished(self):
        stats = self._batch_stats or {
            "total": 0, "processed": 0, "changed": 0,
            "failed": len(self._batch_errors), "skipped": 0,
            "canceled": True,
        }
        errors = list(self._batch_errors)

        progress = self.batch_progress
        self.batch_progress = None
        if progress is not None:
            try:
                progress.canceled.disconnect(self._cancel_batch_operation)
            except (RuntimeError, TypeError):
                pass
            progress.close()
            progress.deleteLater()

        thread = self.batch_thread
        self.batch_thread = None
        if thread is not None:
            thread.deleteLater()

        self._batch_errors = []
        self._batch_stats = None
        self._set_moving_busy(False)
        self._end_operation("batch")
        QTimer.singleShot(0, self._watch_current_item)

        summary = (
            f"处理 {stats.get('processed', 0)}/{stats.get('total', 0)}，"
            f"修改 {stats.get('changed', 0)}，"
            f"跳过 {stats.get('skipped', 0)}，"
            f"失败 {stats.get('failed', 0)}"
        )
        if errors:
            details = "\n".join(errors[:10])
            if len(errors) > 10:
                details += f"\n其余 {len(errors) - 10} 个错误已省略"
            QMessageBox.warning(
                self, "批量任务部分失败", f"{summary}\n\n{details}"
            )
        elif stats.get("canceled"):
            self.status_bar.showMessage(f"批量任务已取消：{summary}", 6000)
        else:
            self.status_bar.showMessage(f"批量任务完成：{summary}", 6000)

    def batch_filling(self):
        if not self._selected_nfo_paths():
            QMessageBox.warning(self, "警告", "请先选择要填充的 NFO")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("批量填充")
        dialog.resize(400, 320)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("选择填充替换字段:"))
        field_buttons = []
        for field in ("series", "rating", "actor"):
            button = QRadioButton(field)
            if not field_buttons:
                button.setChecked(True)
            field_buttons.append(button)
            layout.addWidget(button)

        layout.addWidget(QLabel("填充替换值:"))
        value_entry = QLineEdit()
        layout.addWidget(value_entry)

        def selected_field():
            for button in field_buttons:
                if button.isChecked():
                    return button.text()
            return "series"

        def update_placeholder():
            field = selected_field()
            if field == "rating":
                value_entry.setPlaceholderText("输入评分，例如 8.5")
            elif field == "actor":
                value_entry.setPlaceholderText("多个演员用逗号分隔")
            else:
                value_entry.setPlaceholderText("输入系列名称")

        def apply_fill():
            value = value_entry.text().strip()
            if not value:
                QMessageBox.warning(dialog, "警告", "请输入填充值")
                return
            field = selected_field()
            if field == "rating":
                try:
                    rating = float(value)
                except ValueError:
                    QMessageBox.warning(dialog, "评分无效", "评分必须是有效数字")
                    return
                if not 0 <= rating <= 10:
                    QMessageBox.warning(dialog, "评分无效", "评分应在 0 到 10 之间")
                    return
            paths = self._selected_nfo_paths()
            if self._start_batch_nfo_operation(
                paths, "fill", field=field, value=value
            ):
                dialog.accept()

        for button in field_buttons:
            button.toggled.connect(update_placeholder)
        apply_btn = QPushButton("开始批量填充")
        apply_btn.clicked.connect(apply_fill)
        layout.addWidget(apply_btn)
        value_entry.returnPressed.connect(apply_fill)
        update_placeholder()
        dialog.exec()

    def batch_add(self):
        if not self._selected_nfo_paths():
            QMessageBox.warning(self, "警告", "请先选择要新增标签的 NFO")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("批量新增标签")
        dialog.resize(400, 220)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("多个标签请用逗号分隔；仅新增 tag，不修改 genre。"))
        value_entry = QLineEdit()
        value_entry.setPlaceholderText("例如: 新标签1, 新标签2")
        layout.addWidget(value_entry)

        def apply_add():
            value = value_entry.text().strip()
            if not split_csv_values(value):
                QMessageBox.warning(dialog, "警告", "请输入有效标签")
                return
            paths = self._selected_nfo_paths()
            if self._start_batch_nfo_operation(
                paths, "add_tags", value=value
            ):
                dialog.accept()

        apply_btn = QPushButton("开始批量新增")
        apply_btn.clicked.connect(apply_add)
        layout.addWidget(apply_btn)
        value_entry.returnPressed.connect(apply_add)
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
        if not self.current_target_path or not self.sorted_tree.isEnabled():
            return
        text = item.text(0)
        if text == "..":
            parent = os.path.dirname(self.current_target_path)
            if parent != self.current_target_path:
                self.load_target_files(parent)
        else:
            new_path = os.path.join(self.current_target_path, text)
            if os.path.isdir(new_path):
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


    @pyqtSlot(str, str)
    def _on_crop_images_saved(self, poster_path, thumb_path):
        """裁剪工具已原子写入图片；只刷新当前影片图片区。"""
        changed_path = poster_path or thumb_path
        if changed_path:
            self._queue_library_event(
                "image_changed", os.path.dirname(changed_path)
            )


    def open_image_and_crop(self, image_type):
        if not self.current_file_path:
            return
        if self._active_operations:
            QMessageBox.warning(self, "图片裁剪", "当前有其他文件操作正在进行")
            return

        folder = os.path.dirname(self.current_file_path)
        nfo_base = os.path.splitext(
            os.path.basename(self.current_file_path)
        )[0]
        image_path = preferred_image_file(folder, image_type, nfo_base)
        if not image_path:
            QMessageBox.critical(self, "错误", f"未找到{image_type}图片")
            return

        self._begin_operation("crop")
        try:
            from cg_crop import EmbyPosterCrop

            root = parse_xml_file(self.current_file_path).getroot()
            has_subtitle = False
            mark_type = "none"
            priority = {"none": 0, "wuma": 1, "leak": 2, "umr": 3}
            for tag in root.findall("tag"):
                tag_text = (tag.text or "").casefold()
                if "中文字幕" in tag_text:
                    has_subtitle = True
                candidate = "none"
                if "无码破解" in tag_text:
                    candidate = "umr"
                elif "无码流出" in tag_text:
                    candidate = "leak"
                elif "无码" in tag_text:
                    candidate = "wuma"
                if priority[candidate] > priority[mark_type]:
                    mark_type = candidate

            crop_tool = EmbyPosterCrop(self, nfo_base_name=nfo_base)
            crop_tool.imagesSaved.connect(self._on_crop_images_saved)
            crop_tool.load_initial_image(image_path)
            crop_tool.set_watermark_options(has_subtitle, mark_type)
            crop_tool.exec()
        except ImportError:
            QMessageBox.critical(self, "错误", "找不到 cg_crop.py 文件")
        except (ET.ParseError, OSError) as exc:
            QMessageBox.critical(self, "错误", f"裁剪工具出错: {exc}")
        finally:
            self._end_operation("crop")
            QTimer.singleShot(0, self._watch_current_item)


    def delete_selected_folders(self):
        if not self._confirm_unsaved_changes("删除文件夹"):
            return
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
        self._begin_operation("delete")
        self._clear_file_watches()
        try:
            for folder in folders:
                try:
                    if winshell is None:
                        raise OSError(
                            "当前系统缺少 Windows 回收站支持（winshell/pywin32）"
                        )
                    winshell.delete_file(folder)
                    deleted += 1
                    self._queue_library_event("folder_deleted", folder)
                except OSError as exc:
                    QMessageBox.warning(self, "警告", f"删除文件夹失败: {exc}")
        finally:
            self._end_operation("delete")
            QTimer.singleShot(0, self._watch_current_item)

        if deleted:
            self.status_bar.showMessage(
                f"成功将 {deleted} 个文件夹移入回收站，正在局部更新列表",
                5000,
            )



    @pyqtSlot(str)
    def _on_rename_operation_started(self, directory):
        self._begin_operation("rename")
        self._set_moving_busy(True)
        self.status_bar.showMessage(
            f"批量改名正在处理: {directory}", 5000
        )

    @pyqtSlot(str)
    def _on_rename_nfo_changed(self, nfo_path):
        self._queue_library_event("nfo_changed", nfo_path)

    @pyqtSlot(str, str)
    def _on_rename_folder_renamed(self, old_path, new_path):
        self._queue_library_event(
            "folder_renamed", new_path, old_path=old_path
        )


    @pyqtSlot(bool)
    def _on_rename_operation_finished(self, success):
        self._set_moving_busy(False)
        self._end_operation("rename")
        if success:
            self.status_bar.showMessage(
                "批量改名完成，正在应用局部变更", 5000
            )
        else:
            self.status_bar.showMessage(
                "批量改名已取消或部分失败；已完成项目仍会局部同步", 5000
            )


    def _on_rename_tool_destroyed(self, *_):
        self._rename_tool = None
        self._set_moving_busy(False)
        # 窗口异常关闭时也不能让操作锁永久残留。
        self._end_operation("rename")


    def open_batch_rename_tool(self):
        if not self._confirm_unsaved_changes("打开批量改名工具"):
            return
        if not self.folder_path:
            QMessageBox.critical(self, "错误", "请先选择NFO目录")
            return
        if not os.path.isdir(self.folder_path):
            QMessageBox.critical(
                self, "错误", f"目录不存在: {self.folder_path}"
            )
            return

        existing = self._rename_tool
        if existing is not None and existing.isVisible():
            existing.activateWindow()
            existing.raise_()
            return

        try:
            from cg_rename import RenameToolGUI

            rename_tool = RenameToolGUI(parent=self)
            rename_tool.path_entry.setText(self.folder_path)
            rename_tool.setAttribute(Qt.WA_DeleteOnClose)
            rename_tool.operationStarted.connect(
                self._on_rename_operation_started
            )
            rename_tool.nfoChanged.connect(
                self._on_rename_nfo_changed
            )
            rename_tool.folderRenamed.connect(
                self._on_rename_folder_renamed
            )
            rename_tool.operationFinished.connect(
                self._on_rename_operation_finished
            )
            rename_tool.destroyed.connect(
                self._on_rename_tool_destroyed
            )
            self._rename_tool = rename_tool
            rename_tool.show()
        except ImportError:
            QMessageBox.critical(
                self, "错误", "找不到重命名工具模块(cg_rename.py)"
            )
        except OSError as e:
            QMessageBox.critical(
                self, "错误", f"启动重命名工具时出错: {str(e)}"
            )

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
        menu.addAction("刷新").triggered.connect(self.request_full_refresh)
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
        if not self._confirm_unsaved_changes("关闭程序"):
            event.ignore()
            return

        try:
            running_threads = []
            if self.load_thread is not None and self.load_thread.isRunning():
                self.load_thread.stop()
                if not self.load_thread.wait(5000):
                    running_threads.append("文件加载")
            if self.move_thread is not None and self.move_thread.isRunning():
                self.move_thread.stop()
                # 文件系统 I/O 可能暂时阻塞。关闭窗口时只做很短的等待，
                # 保持 GUI 可响应；线程未退则拒绝关闭而不是硬杀线程。
                if not self.move_thread.wait(250):
                    running_threads.append("文件移动")
            if self.batch_thread is not None and self.batch_thread.isRunning():
                self.batch_thread.stop()
                if not self.batch_thread.wait(5000):
                    running_threads.append("批量NFO修改")
            if self.target_load_thread is not None and self.target_load_thread.isRunning():
                self.target_load_thread.stop()
                if not self.target_load_thread.wait(5000):
                    running_threads.append("目标目录加载")

            # 非阻塞切换目标目录时可能仍有“退休”线程等待磁盘 I/O。
            # 关闭程序时每个只短暂等待，避免 GUI 长时间“未响应”。
            for retired_thread in list(self._retired_target_load_threads):
                if retired_thread.isRunning():
                    retired_thread.stop()
                    if not retired_thread.wait(250):
                        if "目标目录后台读取" not in running_threads:
                            running_threads.append("目标目录后台读取")
                if not retired_thread.isRunning():
                    self._retired_target_load_threads.discard(retired_thread)
                    try:
                        retired_thread.deleteLater()
                    except RuntimeError:
                        pass

            rename_tool = self._rename_tool
            rename_worker = getattr(rename_tool, "worker", None) if rename_tool else None
            if rename_worker is not None and rename_worker.isRunning():
                rename_worker.requestInterruption()
                if not rename_worker.wait(5000):
                    running_threads.append("批量改名")

            if running_threads:
                QMessageBox.warning(
                    self,
                    "操作仍在进行",
                    f"{', '.join(running_threads)}尚未安全停止，请稍后再关闭。",
                )
                event.ignore()
                return

            if hasattr(self, "event_timer"):
                self.event_timer.stop()
            self.reload_timer.stop()
            self._clear_file_watches()

            for progress_name in ("move_progress", "batch_progress"):
                progress = getattr(self, progress_name, None)
                if progress is not None:
                    try:
                        progress.close()
                        progress.deleteLater()
                    except RuntimeError:
                        pass
                    setattr(self, progress_name, None)

            if rename_tool is not None:
                try:
                    rename_tool.close()
                except RuntimeError:
                    pass
            self.nfo_cache.clear()
            self._nfo_path_index.clear()
            self._tree_item_index.clear()
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
