"""Pure filesystem/XML safety helpers shared by the Qt tools."""

from __future__ import annotations

import json
import hashlib
import ntpath
import sqlite3
import os
import re
import stat
import sys
import time
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Optional, Sequence



def application_dir() -> Path:
    """开发时取 .py 所在目录；PyInstaller 打包后取真实 EXE 所在目录。

    不能用当前工作目录，也不能把 _MEIPASS 临时解包目录作为写入基准。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def program_config_dir() -> Path:
    """配置与持久化缓存统一放在程序同级 config 目录。"""
    return application_dir() / "config"


def program_log_dir() -> Path:
    """审计、事件等日志统一放在程序同级 log 目录。"""
    return application_dir() / "log"




# Shared, directory-scoped manifest cache for all three scan applications.
# Unlike NFO metadata, directory membership changes on directory timestamps;
# NFO/STRM contents continue to be checked with real file fingerprints.
DIRECTORY_MANIFEST_SCHEMA = 1
DIRECTORY_MANIFEST_RECHECK_SECONDS = 15 * 60
DIRECTORY_MANIFEST_RETENTION_SECONDS = 180 * 24 * 3600
DIRECTORY_MANIFEST_MAX_FOLDERS = 300000


def directory_manifest_db_path() -> Path:
    """A single APPDATA database for all .py and bundled .exe applications.

    Uses the real Windows roaming profile; never _MEIPASS, CWD or program folder.
    """
    home = os.environ.get("APPDATA")
    if home:
        base = Path(home)
    elif os.name == "nt":
        base = Path.home() / "AppData" / "Roaming"
    else:
        base = Path.home() / ".local" / "share"
    return base / "NFO Tools" / "directory_manifests.sqlite3"


class SnapshotFile:
    """A small, immutable DirEntry-like object used only inside worker threads."""

    __slots__ = ("name", "path", "_symlink")

    def __init__(self, folder: str, name: str, is_symlink: bool):
        self.name = name
        self.path = os.path.join(folder, name)
        self._symlink = bool(is_symlink)

    def is_symlink(self) -> bool:
        return self._symlink

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        if self._symlink and not follow_symlinks:
            return False
        try:
            return os.path.isdir(self.path)
        except OSError:
            return False

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        try:
            mode = os.stat(
                self.path, follow_symlinks=follow_symlinks
            ).st_mode
            return stat.S_ISREG(mode)
        except OSError:
            return False

    def stat(self, *, follow_symlinks: bool = True):
        # Must always re-stat the actual NFO/STRM when checking content caches.
        return os.stat(self.path, follow_symlinks=follow_symlinks)


class DirectorySnapshotCache:
    """One shared, bounded SQLite manifest table, keyed by physical folder path.

    On every scan, stat each directory. Reuse its children/filenames only if the
    directory fingerprint matches and it was enumerated recently. Re-enumerate
    changed/aged directories. Never reuse NFO file metadata without its own stat.

    The SQLite database is best-effort; if it becomes unavailable, use normal
    scandir and preserve scan correctness. No user settings or opt-in UI.
    """

    WRITE_BATCH = 250

    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path is not None else directory_manifest_db_path()
        self.connection = None
        self._rows = {}
        self._pending = []
        self.cache_hits = 0
        self.scanned_folders = 0
        self.failed_folders = 0
        self.changed_folders = 0
        self._now = int(time.time())
        self._last_touch = set()  # 每隔 30 天才更新一次活跃目录 used_at
        self._loaded_roots = set()
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(self.db_path), timeout=3.0)
            self.connection = connection
            connection.execute("PRAGMA busy_timeout=3000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA cache_size=-8192")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS directory_manifests ("
                "path_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
                "subdirs_json TEXT NOT NULL, files_json TEXT NOT NULL, "
                "checked_at INTEGER NOT NULL, used_at INTEGER NOT NULL, "
                "schema_version INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_directory_manifests_used "
                "ON directory_manifests(used_at)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS manifest_meta "
                "(name TEXT PRIMARY KEY, value INTEGER NOT NULL)"
            )
            connection.commit()
            # Do not eagerly deserialize all four libraries when the user opens
            # only a single movie subfolder. Fetch one subtree on demand.
        except (OSError, sqlite3.Error) as error:
            print(f"[目录快照] 缓存不可用，回退正常目录扫描：{error}")
            if self.connection is not None:
                self.connection.close()
            self.connection = None
            self._rows.clear()

    @staticmethod
    def _valid_cached_name(value):
        return (
            isinstance(value, str) and value not in (".", "..", "")
            and "/" not in value and "\\\\" not in value
        )

    def _load_subtree(self, root: str):
        """One indexed range query for the chosen subtree, never the full DB."""
        if self.connection is None:
            return
        key = self._key(root)
        if key in self._loaded_roots:
            return
        self._loaded_roots.add(key)
        prefix = key.rstrip("\\") + "\\"
        upper_bound = prefix + chr(0x10FFFF)
        try:
            cursor = self.connection.execute(
                "SELECT path_key,fingerprint,subdirs_json,files_json,checked_at,used_at "
                "FROM directory_manifests WHERE schema_version=? AND "
                "(path_key=? OR (path_key>=? AND path_key<?))",
                (DIRECTORY_MANIFEST_SCHEMA,key,prefix,upper_bound),
            )
            for row_key, signature, subdirs, files, checked, used in cursor:
                try:
                    folders = json.loads(subdirs)
                    names = json.loads(files)
                    stamp = json.loads(signature)
                    if (
                        isinstance(folders, list)
                        and all(self._valid_cached_name(n) for n in folders)
                        and isinstance(names, list)
                        and all(
                            isinstance(item, list) and len(item) == 2
                            and self._valid_cached_name(item[0])
                            and item[1] in (0,1) for item in names
                        )
                        and isinstance(stamp, list) and len(stamp)==2
                        and all(isinstance(x,int) for x in stamp)
                    ):
                        self._rows[row_key] = (
                            tuple(stamp),folders,names,int(checked),int(used)
                        )
                except (TypeError, ValueError):
                    continue
        except sqlite3.Error as error:
            # Keep scanning normally, even when SQLite is locked/corrupted.
            print(f"[目录快照] 子目录索引读取失败，回退实时扫描：{error}")
            self.connection.close()
            self.connection = None
            self._rows.clear()

    @staticmethod
    def _key(folder: str) -> str:
        return ntpath.normcase(ntpath.normpath(os.path.abspath(folder)))

    @staticmethod
    def _fingerprint(st):
        # Directory stamp checks membership, not content of existing NFO files.
        return (int(st.st_mtime_ns), int(st.st_ctime_ns))

    @staticmethod
    def _enumerate(folder: str, stop_event):
        subdirs, files = [], []
        errors_seen = False
        with os.scandir(folder) as entries:
            for entry in entries:
                if stop_event.is_set():
                    return None
                try:
                    if entry.is_symlink():
                        # Do not follow symlink/junction directories into cycles.
                        if not entry.is_dir(follow_symlinks=False):
                            files.append([entry.name, 1])
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if (os.name == "nt"
                                and getattr(entry.stat(follow_symlinks=False),
                                            "st_file_attributes", 0) & 0x400):
                            continue
                        subdirs.append(entry.name)
                    else:
                        files.append([entry.name, 0])
                except OSError:
                    # Never persist a manifest that silently omitted an entry.
                    errors_seen = True
                    continue
        subdirs.sort(key=str.casefold)
        files.sort(key=lambda item: item[0].casefold())
        return subdirs, files, errors_seen

    def _flush(self):
        if not self._pending or self.connection is None:
            self._pending.clear()
            return
        rows = self._pending
        self._pending = []
        try:
            self.connection.executemany(
                "INSERT INTO directory_manifests "
                "(path_key,fingerprint,subdirs_json,files_json,checked_at,used_at,schema_version) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(path_key) DO UPDATE SET "
                "fingerprint=excluded.fingerprint,subdirs_json=excluded.subdirs_json,"
                "files_json=excluded.files_json,checked_at=excluded.checked_at,"
                "used_at=excluded.used_at,schema_version=excluded.schema_version",
                rows,
            )
            self.connection.commit()
        except sqlite3.Error as error:
            self.connection.rollback()
            print(f"[目录快照] 缓存更新失败，保持正常扫描：{error}")
            self.connection.close()
            self.connection = None

    def _store(self, folder, signature, subdirs, files):
        key = self._key(folder)
        self._rows[key] = (signature, subdirs, files, self._now, self._now)
        if self.connection is None:
            return
        self._pending.append((
            key,json.dumps(list(signature),separators=(",",":")),
            json.dumps(subdirs,ensure_ascii=False,separators=(",",":")),
            json.dumps(files,ensure_ascii=False,separators=(",",":")),
            self._now,self._now,DIRECTORY_MANIFEST_SCHEMA,
        ))
        if len(self._pending) >= self.WRITE_BATCH:
            self._flush()

    def walk(self, root: str, stop_event, errors=None):
        """Yield (folder, list[SnapshotFile]); traverse only under selected root.

        An independently selected subfolder reuses the same key from its parent
        library scan. Missing/inaccessible folders never cause cache deletion.
        """
        stack = [os.path.normpath(os.path.abspath(os.fspath(root)))]
        self._load_subtree(stack[0])
        seen = set()
        while stack and not stop_event.is_set():
            folder = stack.pop()
            key = self._key(folder)
            if key in seen:
                continue
            seen.add(key)
            try:
                stat_before = os.stat(folder, follow_symlinks=False)
                if not stat.S_ISDIR(stat_before.st_mode):
                    raise NotADirectoryError(folder)
                if (os.name == "nt" and key not in (
                        self._key(os.path.normpath(root)),
                    ) and getattr(stat_before,"st_file_attributes",0) & 0x400):
                    # Do not traverse cached directories that became junctions.
                    continue
                fingerprint = self._fingerprint(stat_before)
            except OSError as error:
                self.failed_folders += 1
                if errors is not None and len(errors) < 25:
                    errors.append(f"{folder}：{error}")
                continue

            previous = self._rows.get(key)
            if (
                previous is not None and previous[0] == fingerprint
                and self._now - previous[3] < DIRECTORY_MANIFEST_RECHECK_SECONDS
            ):
                subdirs, files = previous[1], previous[2]
                self.cache_hits += 1
                if (self.connection is not None and
                        self._now - previous[4] >= 30 * 24 * 3600):
                    self._last_touch.add(key)
            else:
                try:
                    listing = self._enumerate(folder, stop_event)
                    if listing is None:
                        return
                    subdirs, files, entry_errors = listing
                    if entry_errors:
                        self.failed_folders += 1
                        if errors is not None and len(errors) < 25:
                            errors.append(f"{folder}：部分文件无法读取，下次将重新核对")
                    # Directory may change during enumeration; never persist
                    # an unstable listing as if it were valid.
                    stat_after = os.stat(folder, follow_symlinks=False)
                    if (not entry_errors and
                            self._fingerprint(stat_after) == fingerprint):
                        self._store(folder, fingerprint, subdirs, files)
                    else:
                        self.failed_folders += 1
                        if errors is not None and len(errors) < 25:
                            errors.append(f"{folder}：枚举期间目录发生变化，下次将重新核对")
                    self.scanned_folders += 1
                    if previous is not None:
                        self.changed_folders += 1
                except OSError as error:
                    self.failed_folders += 1
                    if errors is not None and len(errors) < 25:
                        errors.append(f"{folder}：{error}")
                    continue

            for name in reversed(subdirs):
                stack.append(os.path.join(folder, name))
            yield folder, [
                SnapshotFile(folder, pair[0], bool(pair[1])) for pair in files
            ]

    def close(self):
        self._flush()
        if self.connection is not None:
            try:
                if self._last_touch:
                    self.connection.executemany(
                        "UPDATE directory_manifests SET used_at=? WHERE path_key=?",
                        ((self._now, key) for key in self._last_touch),
                    )
                    self.connection.commit()
                self._last_touch.clear()
                # Clean stale manifests rarely; one shared DB rather than a DB
                # per library. Avoid expensive maintenance on every scan.
                row = self.connection.execute(
                    "SELECT value FROM manifest_meta WHERE name='last_cleanup'"
                ).fetchone()
                if (row is None or int(row[0]) < self._now - 7*24*3600
                        or self.scanned_folders >= 1000):
                    cutoff = self._now - DIRECTORY_MANIFEST_RETENTION_SECONDS
                    self.connection.execute(
                        "DELETE FROM directory_manifests WHERE used_at < ?",
                        (cutoff,),
                    )
                    count = self.connection.execute(
                        "SELECT COUNT(*) FROM directory_manifests"
                    ).fetchone()[0]
                    if count > DIRECTORY_MANIFEST_MAX_FOLDERS:
                        self.connection.execute(
                            "DELETE FROM directory_manifests WHERE path_key IN ("
                            "SELECT path_key FROM directory_manifests "
                            "ORDER BY used_at ASC LIMIT ?)",
                            (count - DIRECTORY_MANIFEST_MAX_FOLDERS,),
                        )
                    self.connection.execute(
                        "INSERT INTO manifest_meta(name,value) VALUES('last_cleanup',?) "
                        "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                        (self._now,),
                    )
                    self.connection.commit()
            except sqlite3.Error as error:
                print(f"[目录快照] 清理失败（不影响扫描）：{error}")
            finally:
                self.connection.close()
                self.connection = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False


SHARED_NFO_SCHEMA = 1
SHARED_NFO_REQUIRED = frozenset({
    "num", "title", "originaltitle", "sorttitle", "series",
    "plot", "release", "rating", "actors", "tags",
})


def shared_nfo_key(path: str) -> str:
    """Windows 同一路径忽略大小写；Linux 测试中也使用相同的纯字符串格式。"""
    return ntpath.normcase(ntpath.normpath(os.fspath(path)))


def shared_nfo_valid(data) -> bool:
    """仅信任全字段 NFO 元数据；早期版本的精简缓存不能当成完整数据。"""
    return (
        isinstance(data, dict)
        and SHARED_NFO_REQUIRED.issubset(data)
        and isinstance(data.get("actors"), list)
        and isinstance(data.get("tags"), list)
    )


class SharedNFOCache:
    """三个程序共享的 NFO XML 元数据缓存，不存 STRM、视频参数或图片像素。

    SQLite WAL + busy_timeout 允许多个独立进程读写；每个工作线程独占连接。
    缓存读取必须用文件的 mtime_ns + size 检验，任何一项变化都要重新解析。
    """

    BATCH_SIZE = 300

    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path is not None else program_config_dir() / "shared_nfo_cache.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.db_path), timeout=10.0)
        try:
            self.connection.execute("PRAGMA busy_timeout=10000")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=NORMAL")
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS shared_nfo_cache ("
                "path_key TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, "
                "size INTEGER NOT NULL, payload TEXT NOT NULL, "
                "schema_version INTEGER NOT NULL)"
            )
            self.connection.commit()
        except (sqlite3.Error, OSError):
            self.connection.close()
            raise

    def get_many(self, paths):
        """返回 {原始路径: (mtime_ns, size, 完整 dict)}；不触碰影片文件。"""
        originals = {}
        for path in paths:
            if path:
                originals.setdefault(shared_nfo_key(path), []).append(os.fspath(path))
        results = {}
        keys = list(originals)
        for start in range(0, len(keys), self.BATCH_SIZE):
            batch = keys[start:start + self.BATCH_SIZE]
            placeholders = ",".join("?" for _ in batch)
            sql = (
                "SELECT path_key, mtime_ns, size, payload FROM shared_nfo_cache "
                "WHERE schema_version = ? AND path_key IN (" + placeholders + ")"
            )
            for key, mtime, size, payload in self.connection.execute(
                sql, (SHARED_NFO_SCHEMA, *batch)
            ):
                try:
                    metadata = json.loads(payload)
                except (TypeError, ValueError):
                    continue
                if not shared_nfo_valid(metadata):
                    continue
                for path in originals[key]:
                    results[path] = (mtime, size, metadata)
        return results

    def store_many(self, rows):
        """rows: [(path, mtime_ns, size, 全字段 NFO dict)]；调用方须已复核 stat。"""
        payloads = [
            (shared_nfo_key(path), int(mtime), int(size),
             json.dumps(data, ensure_ascii=False), SHARED_NFO_SCHEMA)
            for path, mtime, size, data in rows
            if shared_nfo_valid(data)
        ]
        if not payloads:
            return
        try:
            self.connection.executemany(
                "INSERT INTO shared_nfo_cache "
                "(path_key, mtime_ns, size, payload, schema_version) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(path_key) DO UPDATE SET "
                "mtime_ns=excluded.mtime_ns, size=excluded.size, "
                "payload=excluded.payload, schema_version=excluded.schema_version",
                payloads,
            )
            self.connection.commit()
        except sqlite3.Error:
            self.connection.rollback()
            raise

    def close(self):
        self.connection.close()


def editor_event_file(library_root: str) -> str:
    """按 NFO 库根目录区分事件文件，防止多个媒体库互相串事件。"""
    if not library_root:
        raise ValueError("library_root 不能为空")
    canonical = os.path.normcase(os.path.abspath(os.fspath(library_root)))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return str(program_log_dir() / f"nfo_editor_events_{digest}.jsonl")


def portable_qsettings(scope: str):
    """Qt 设置写入程序同级 config/*.ini；首次使用时迁移旧 QSettings。

    PySide6 采用延迟导入，nfo_utils 的纯文件操作函数不依赖 GUI 模块。
    """
    from PySide6.QtCore import QSettings

    files = {
        "Directories": "nfo_editor_directories.ini",
        "WindowState": "nfo_editor_window_state.ini",
        "PhotoWall": "cg_photo_wall.ini",
    }
    if scope not in files:
        raise ValueError(f"未知 Qt 设置范围：{scope}")

    settings_path = program_config_dir() / files[scope]
    if not settings_path.is_file():
        # 迁移 Windows 注册表 / 旧 Qt 用户设置，仅复制有值的项。
        old_settings = QSettings("NFOEditor", scope)
        legacy_keys = old_settings.allKeys()
        if legacy_keys:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            new_settings = QSettings(str(settings_path), QSettings.IniFormat)
            for key in legacy_keys:
                new_settings.setValue(key, old_settings.value(key))
            new_settings.sync()
            if new_settings.status() != QSettings.NoError:
                raise OSError(f"无法迁移旧 Qt 设置至：{settings_path}")
            return new_settings

    # QFileDialog/QSettings 创建操作均由现有界面调用；此处只定位和创建小目录。
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    return QSettings(str(settings_path), QSettings.IniFormat)


IMAGE_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

VIDEO_EXTENSIONS: tuple[str, ...] = (
    ".mp4", ".mkv", ".avi", ".mov", ".rm", ".rmvb", ".mpeg", ".mpg",
    ".ts", ".m4v", ".webm", ".strm",
)


def normalize_catalog_number(value: str) -> str:
    """Normalize a catalog number for safe filename matching."""
    return re.sub(r"[^0-9a-z]+", "", (value or "").casefold())


def find_numbered_trailer(directory: str, number: str) -> Optional[str]:
    """Find a trailer named after *number* in one configured directory.

    Ranking is deterministic: exact stem, exact stem with a trailer suffix, then
    separator-insensitive catalog-number equality. A longer catalog number never
    matches merely because it contains the requested one.
    """
    directory = os.path.expandvars(os.path.expanduser(directory or ""))
    wanted_raw = (number or "").strip()
    wanted_norm = normalize_catalog_number(wanted_raw)
    if not directory or not wanted_norm or not os.path.isdir(directory):
        return None

    candidates: list[tuple[int, int, str, str]] = []
    ext_priority = {ext: i for i, ext in enumerate(VIDEO_EXTENSIONS)}
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return None

    for entry in entries:
        if not entry.is_file():
            continue
        stem, ext = os.path.splitext(entry.name)
        ext = ext.casefold()
        if ext not in ext_priority:
            continue
        stem_fold = stem.casefold().strip()
        rank: Optional[int] = None
        if stem_fold == wanted_raw.casefold():
            rank = 0
        else:
            stripped = re.sub(r"[ ._-]+(?:trailer|preview)$", "", stem_fold).strip()
            if stripped == wanted_raw.casefold():
                rank = 1
            elif normalize_catalog_number(stripped) == wanted_norm:
                rank = 2
        if rank is not None:
            candidates.append((rank, ext_priority[ext], entry.name.casefold(), entry.path))

    return min(candidates)[3] if candidates else None


def find_trailer_in_movie_folder(directory: str) -> Optional[str]:
    """Return a deterministic trailer file from a movie folder."""
    if not directory or not os.path.isdir(directory):
        return None
    ext_priority = {ext: i for i, ext in enumerate(VIDEO_EXTENSIONS)}
    candidates: list[tuple[int, str, str]] = []
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return None
    for entry in entries:
        if not entry.is_file():
            continue
        stem, ext = os.path.splitext(entry.name)
        ext = ext.casefold()
        if ext in ext_priority and "trailer" in stem.casefold():
            candidates.append((ext_priority[ext], entry.name.casefold(), entry.path))
    return min(candidates)[2] if candidates else None


def canonical_path(path: str) -> str:
    """Return an absolute, real, normalized path suitable for comparisons."""
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


def same_path(path_a: str, path_b: str) -> bool:
    return bool(path_a and path_b) and canonical_path(path_a) == canonical_path(path_b)


def is_path_within(path: str, parent: str, *, include_equal: bool = True) -> bool:
    """Return whether *path* is inside *parent* without unsafe startswith checks."""
    try:
        child_real = canonical_path(path)
        parent_real = canonical_path(parent)
        common = os.path.commonpath([child_real, parent_real])
    except (OSError, ValueError, TypeError):
        return False
    if common != parent_real:
        return False
    return include_equal or child_real != parent_real


def unique_paths(paths: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for path in paths:
        key = canonical_path(path)
        if key not in seen:
            seen.add(key)
            result.append(os.path.normpath(path))
    return result


def unique_paths_lexical(paths: Iterable[str]) -> list[str]:
    """Deduplicate selection paths without touching disk (GUI hot path).

    Security-sensitive checks still run inside the moving worker using
    move_directory_fast_or_shell, including canonical/symlink validation.
    """
    result: list[str] = []
    seen: set[str] = set()
    for raw_path in paths:
        if not raw_path:
            continue
        path = os.path.abspath(os.path.normpath(os.fspath(raw_path)))
        key = os.path.normcase(path)
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def disc_part_index(path: str) -> Optional[int]:
    """Return 1/2 for explicit CD/disc part markers in a path, otherwise None."""
    text = os.path.normpath(os.fspath(path)).lower()
    patterns = (
        r"(?<![a-z0-9])cd[ ._-]*([12])(?![a-z0-9])",
        r"(?<![a-z0-9])disc[ ._-]*([12])(?![a-z0-9])",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))

    if re.search(r"(?:^|[\\/_. -])(?:第)?一(?:张|碟|盘)(?:$|[\\/_. -])", text):
        return 1
    if re.search(r"(?:^|[\\/_. -])(?:第)?二(?:张|碟|盘)(?:$|[\\/_. -])", text):
        return 2
    return None


def paths_form_multi_disc_set(paths: Sequence[str]) -> bool:
    """True only when every path has a part marker and at least two parts exist."""
    if len(paths) <= 1:
        return False
    indexes = [disc_part_index(path) for path in paths]
    return all(index is not None for index in indexes) and len(set(indexes)) >= 2


def normalize_scan_roots(paths: Iterable[str]) -> list[str]:
    """Deduplicate roots and remove descendants already covered by a parent root."""
    existing = [os.path.normpath(p) for p in paths if p and os.path.isdir(p)]
    existing = unique_paths(existing)
    existing.sort(key=lambda p: (len(Path(canonical_path(p)).parts), canonical_path(p)))

    roots: list[str] = []
    for path in existing:
        if any(is_path_within(path, root, include_equal=True) for root in roots):
            continue
        roots.append(path)
    return roots




def append_editor_event(
    library_root: str,
    event_type: str,
    *,
    path: Optional[str] = None,
    old_path: Optional[str] = None,
    message: Optional[str] = None,
    source: Optional[str] = None,
    **extra,
) -> str:
    """Append one structured event for a separately running editor tool.

    Tools running inside the NFO Editor process should prefer Qt signals.
    Independent processes can call this helper after their filesystem write
    succeeds.  One ``os.write`` call is used with ``O_APPEND`` so concurrent
    tools cannot overwrite each other's event records.
    """
    if not library_root or not os.path.isdir(library_root):
        raise OSError(f"NFO 根目录不存在: {library_root}")
    event_type = (event_type or "").strip()
    if not event_type:
        raise ValueError("event_type 不能为空")

    payload = {
        "type": event_type,
        "timestamp": time.time(),
    }
    if path:
        payload["path"] = os.path.normpath(os.fspath(path))
    if old_path:
        payload["old_path"] = os.path.normpath(os.fspath(old_path))
    if message:
        payload["message"] = str(message)
    if source:
        payload["source"] = str(source)
    payload.update(extra)

    # 外部组件事件也是日志；新版本不再往 NFO 媒体库根目录写隐藏文件。
    event_file = editor_event_file(library_root)
    os.makedirs(os.path.dirname(event_file), exist_ok=True)
    encoded = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")

    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(event_file, flags, 0o600)
    try:
        written = os.write(fd, encoded)
        if written != len(encoded):
            raise OSError("外部事件写入不完整")
        os.fsync(fd)
    finally:
        os.close(fd)
    return event_file



def read_series_text(root: ET.Element) -> str:
    """Read series text from common NFO layouts.

    Preferred order is a direct ``<series>`` node, then the structured
    ``<set><name>`` form, and finally legacy text stored directly in ``<set>``.
    """
    series = root.find("series")
    if series is not None and series.text and series.text.strip():
        return series.text.strip()

    set_elem = root.find("set")
    if set_elem is None:
        return ""
    name_elem = set_elem.find("name")
    if name_elem is not None and name_elem.text and name_elem.text.strip():
        return name_elem.text.strip()
    return (set_elem.text or "").strip()


def preferred_nfo_from_names(
    folder_path: str, file_names: Iterable[str], preferred_stem: Optional[str] = None
) -> Optional[str]:
    """Select a deterministic NFO from an already enumerated filename list."""
    candidates = sorted(
        (name for name in file_names if str(name).lower().endswith(".nfo")),
        key=lambda name: str(name).casefold(),
    )
    if not candidates:
        return None
    if preferred_stem:
        wanted = preferred_stem.casefold()
        for name in candidates:
            if Path(name).stem.casefold() == wanted:
                return os.path.join(folder_path, name)
    return os.path.join(folder_path, candidates[0])


def preferred_image_from_names(
    folder_path: str,
    file_names: Iterable[str],
    image_type: str,
    preferred_stem: Optional[str] = None,
) -> Optional[str]:
    """Select a deterministic image from an already enumerated filename list."""
    image_type = (image_type or "").casefold()
    candidates = [
        str(name) for name in file_names
        if str(name).casefold().endswith(IMAGE_EXTENSIONS)
        and image_type in str(name).casefold()
    ]
    if not candidates:
        return None

    preferred_stem = (preferred_stem or "").casefold()

    def sort_key(name: str):
        lower = name.casefold()
        stem = Path(name).stem.casefold()
        exact = bool(preferred_stem and stem == f"{preferred_stem}-{image_type}")
        canonical_suffix = stem.endswith(f"-{image_type}")
        return (0 if exact else 1, 0 if canonical_suffix else 1, lower)

    return os.path.join(folder_path, sorted(candidates, key=sort_key)[0])

def parse_xml_file(path: str) -> ET.ElementTree:
    """Parse XML while retaining comments and processing instructions where supported."""
    builder = ET.TreeBuilder(insert_comments=True, insert_pis=True)
    parser = ET.XMLParser(target=builder)
    return ET.parse(path, parser=parser)

def split_csv_values(text: str) -> list[str]:
    """Split a comma-delimited UI field while preserving order and removing duplicates."""
    values: list[str] = []
    seen: set[str] = set()
    for value in (text or "").split(","):
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values


def sync_actor_nodes(root: ET.Element, actor_names: Sequence[str]) -> None:
    """Synchronize actor names while retaining metadata for actors that remain.

    Named actor blocks are reordered to match the UI field.  For a retained actor,
    the first existing block is reused intact, so role/thumb/type/order children stay.
    Malformed actor blocks without a usable <name> are left untouched.
    """
    desired: list[str] = []
    seen_desired: set[str] = set()
    for raw_name in actor_names:
        name = (raw_name or "").strip()
        if name and name not in seen_desired:
            seen_desired.add(name)
            desired.append(name)

    named_nodes: dict[str, ET.Element] = {}
    named_positions: list[int] = []
    for index, actor in enumerate(list(root)):
        if actor.tag != "actor":
            continue
        name_elem = actor.find("name")
        name = (name_elem.text or "").strip() if name_elem is not None else ""
        if not name:
            continue
        named_positions.append(index)
        named_nodes.setdefault(name, actor)
        root.remove(actor)

    insert_at = min(named_positions) if named_positions else len(root)
    for offset, name in enumerate(desired):
        actor = named_nodes.get(name)
        if actor is None:
            actor = ET.Element("actor")
            name_elem = ET.SubElement(actor, "name")
            name_elem.text = name
        root.insert(insert_at + offset, actor)


def sync_series_nodes(root: ET.Element, series_value: str) -> None:
    """Keep direct ``series`` and structured ``set/name`` values consistent."""
    value = (series_value or "").strip()
    series = root.find("series")
    if series is None:
        series = ET.Element("series")
        # Put series before studio-like metadata when possible.
        children = list(root)
        insert_at = len(children)
        for index, child in enumerate(children):
            if child.tag in {"studio", "maker", "publisher", "label", "tag", "genre"}:
                insert_at = index
                break
        root.insert(insert_at, series)
    series.text = value

    set_elem = root.find("set")
    if set_elem is None and value:
        set_elem = ET.Element("set")
        children = list(root)
        try:
            insert_at = children.index(series) + 1
        except ValueError:
            insert_at = len(children)
        root.insert(insert_at, set_elem)
    if set_elem is not None:
        set_elem.text = None
        name = set_elem.find("name")
        if name is None:
            name = ET.SubElement(set_elem, "name")
        name.text = value


def replace_text_nodes(root: ET.Element, tag_name: str, values: Sequence[str]) -> None:
    """Replace simple text nodes in place; unrelated XML nodes stay intact."""
    children = list(root)
    matching = [(index, elem) for index, elem in enumerate(children) if elem.tag == tag_name]
    insert_at = matching[0][0] if matching else len(children)
    for _, elem in matching:
        root.remove(elem)

    offset = 0
    for value in values:
        value = (value or "").strip()
        if not value:
            continue
        elem = ET.Element(tag_name)
        elem.text = value
        root.insert(insert_at + offset, elem)
        offset += 1


def atomic_write_text(path: str, text: str, *, encoding: str = "utf-8") -> None:
    """Write a text file through a same-directory temporary file and os.replace()."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    prefix = f".{os.path.basename(path)}."
    fd, temp_path = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=directory)
    old_mode: Optional[int] = None
    try:
        if os.path.exists(path):
            try:
                old_mode = os.stat(path).st_mode
            except OSError:
                old_mode = None
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if old_mode is not None:
            try:
                os.chmod(temp_path, old_mode)
            except OSError:
                pass
        os.replace(temp_path, path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def serialize_pretty_xml(root: ET.Element) -> str:
    """Serialize XML without constructing a second DOM tree.

    ``ElementTree.indent`` is substantially lighter than ``minidom`` for large
    batch operations and preserves comments/processing instructions retained by
    :func:`parse_xml_file`. Callers discard the in-memory tree after writing, so
    applying indentation in place is safe here.
    """
    if hasattr(ET, "indent"):
        ET.indent(root, space="  ")
    xml_bytes = ET.tostring(
        root, encoding="utf-8", xml_declaration=True, short_empty_elements=True
    )
    return xml_bytes.decode("utf-8").rstrip() + "\n"


def write_xml_root_atomic(root: ET.Element, path: str) -> None:
    atomic_write_text(path, serialize_pretty_xml(root))


class WindowsShellMoveInterrupted(OSError):
    """Windows Shell reports that the operation was canceled or interrupted."""



def move_directory_fast_or_shell(
    src_path: str,
    dest_parent: str,
    *,
    owner_hwnd: int = 0,
    stop_event=None,
    on_cross_volume=None,
) -> str:
    """同卷直接调用 Windows 系统目录重命名；仅跨卷时使用 IFileOperation。

    同卷不扫描文件、不读取影片；跨卷必须经调用方明确授权后，
    才使用 Windows Shell 的复制-删除移动流程。
    os.rename() 返回非跨卷错误时直接失败，绝不误入复制流程。
    """
    import errno

    # Event.set() = 请求停止后续目录；当前系统调用不做强制中断。
    if stop_event is not None and stop_event.is_set():
        raise WindowsShellMoveInterrupted("用户已请求停止后续移动")
    if not src_path or not os.path.isdir(src_path):
        raise OSError(f"源文件夹不存在或不是目录：{src_path}")
    if not dest_parent or not os.path.isdir(dest_parent):
        raise OSError(f"目标目录不存在或不是目录：{dest_parent}")

    src_path = os.path.abspath(os.path.normpath(os.fspath(src_path)))
    dest_parent = os.path.abspath(os.path.normpath(os.fspath(dest_parent)))
    folder_name = os.path.basename(src_path)
    if not folder_name:
        raise OSError("无法确定源文件夹名称")

    dest_path = os.path.join(dest_parent, folder_name)
    if same_path(src_path, dest_path):
        raise OSError("源目录和目标目录相同，未移动")
    if is_path_within(dest_parent, src_path, include_equal=True):
        raise OSError("不能移动到源文件夹自身或其子目录")
    if os.path.lexists(dest_path):
        raise FileExistsError(f"目标已有同名文件夹或链接，未覆盖：{dest_path}")

    try:
        os.rename(src_path, dest_path)
    except OSError as exc:
        # Windows ERROR_NOT_SAME_DEVICE (17) / errno.EXDEV 才代表跨卷。
        # 占用、权限、重名等错误不得自动改走复制。
        if exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17:
            # 安全默认：未经调用方明确确认，绝不发生跨卷复制。
            # on_cross_volume 必须先得到用户授权；拒绝时应抛出 OSError。
            if on_cross_volume is None:
                raise OSError(
                    "检测到跨卷移动，已阻止自动复制；源文件夹保持不变。"
                    "请通过主界面移动并确认跨卷复制。"
                ) from exc
            on_cross_volume(src_path, dest_path)
            if stop_event is not None and stop_event.is_set():
                raise WindowsShellMoveInterrupted("用户已取消跨卷移动")
            return move_directory_windows_shell(
                src_path, dest_parent,
                owner_hwnd=owner_hwnd,
                stop_event=stop_event,
            )
        raise OSError(f"同卷目录移动失败（未复制）：{exc}") from exc

    return dest_path


def move_directory_windows_shell(
    src_path: str,
    dest_parent: str,
    *,
    owner_hwnd: int = 0,
    stop_event=None,
) -> str:
    """Move one folder through native Windows IFileOperation (same/cross-volume).

    The shell is responsible for the actual rename/copy/delete work and its
    own progress dialog. The caller can request that the NEXT folder be
    skipped using stop_event; while PerformOperations() is running, the user
    cancels the CURRENT folder through the Windows progress dialog.

    This function never calls shutil.move(), copytree(), or rmtree().
    """
    if os.name != "nt":
        raise OSError("Windows IFileOperation 仅支持 Windows 系统")

    # Event.set() requests stopping the queue; Event.clear() permits work.
    if stop_event is not None and stop_event.is_set():
        raise WindowsShellMoveInterrupted("用户已请求停止后续移动")

    if not src_path or not os.path.isdir(src_path):
        raise OSError(f"源文件夹不存在或不是目录：{src_path}")
    if not dest_parent or not os.path.isdir(dest_parent):
        raise OSError(f"目标目录不存在或不是目录：{dest_parent}")

    src_path = os.path.abspath(os.path.normpath(os.fspath(src_path)))
    dest_parent = os.path.abspath(os.path.normpath(os.fspath(dest_parent)))
    folder_name = os.path.basename(src_path)
    if not folder_name:
        raise OSError("无法确定源文件夹名称")

    dest_path = os.path.join(dest_parent, folder_name)
    if same_path(src_path, dest_path):
        raise OSError("源目录与目标目录相同")
    if is_path_within(dest_parent, src_path, include_equal=True):
        raise OSError("不能将文件夹移动到自身或自身子目录")
    if os.path.lexists(dest_path):
        raise FileExistsError(f"目标已存在同名文件夹，不执行覆盖：{dest_path}")

    # Avoid additional COM dependencies; use the documented Windows ABI.
    # COM IID and vtable ordering are from Microsoft's shobjidl_core.h:
    # https://learn.microsoft.com/windows/win32/api/shobjidl_core/nn-shobjidl_core-ifileoperation
    import ctypes
    from ctypes import wintypes
    import uuid

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_ubyte * 8),
        ]

        @classmethod
        def from_string(cls, value: str):
            return cls.from_buffer_copy(uuid.UUID(value).bytes_le)

    # GUIDs from the official Windows SDK (not Python package-generated IDs).
    CLSID_FileOperation = GUID.from_string(
        "3AD05575-8857-4850-9277-11B85BDB8E09"
    )
    IID_IFileOperation = GUID.from_string(
        "947AAB5F-0A5C-4C13-B4D6-4BF7836FC9F8"
    )
    IID_IShellItem = GUID.from_string(
        "43826D1E-E718-42EE-BC55-A1E261C37BFE"
    )

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)

    ole_initialize = ole32.OleInitialize
    ole_initialize.argtypes = [ctypes.c_void_p]
    ole_initialize.restype = ctypes.c_long

    ole_uninitialize = ole32.OleUninitialize
    ole_uninitialize.argtypes = []
    ole_uninitialize.restype = None

    co_create_instance = ole32.CoCreateInstance
    co_create_instance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    co_create_instance.restype = ctypes.c_long

    sh_create_item = shell32.SHCreateItemFromParsingName
    sh_create_item.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    sh_create_item.restype = ctypes.c_long

    def check_hr(hr: int, action: str) -> None:
        if hr < 0:
            code = ctypes.c_uint32(hr).value
            raise OSError(f"{action}失败，HRESULT=0x{code:08X}")

    def call_com(interface, slot: int, restype, argtypes: tuple, *args):
        """Call a documented COM vtable method (IUnknown = slots 0,1,2)."""
        vtable = ctypes.cast(
            interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        addr = vtable[slot]
        if not addr:
            raise OSError(f"Windows COM 接口方法不可用：slot={slot}")
        prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return prototype(addr)(interface, *args)

    def release_com(interface) -> None:
        if interface.value:
            call_com(interface, 2, wintypes.ULONG, ())
            interface.value = None

    com_initialized = False
    operation = ctypes.c_void_p()
    source_item = ctypes.c_void_p()
    destination_item = ctypes.c_void_p()

    try:
        # IFileOperation requires an STA. OleInitialize initializes STA+OLE
        # within the worker thread; do NOT initialize it in the GUI thread.
        check_hr(ole_initialize(None), "初始化 Windows Shell")
        com_initialized = True

        CLSCTX_ALL = 0x17
        check_hr(
            co_create_instance(
                ctypes.byref(CLSID_FileOperation),
                None,
                CLSCTX_ALL,
                ctypes.byref(IID_IFileOperation),
                ctypes.byref(operation),
            ),
            "创建 IFileOperation",
        )

        # FOF_NOERRORUI + FOFX_EARLYFAILURE: stop on error, do not silently
        # skip an item; FOFX_SHOWELEVATIONPROMPT allows a UAC request.
        # Deliberately omit FOF_NOCONFIRMATION, FOF_RENAMEONCOLLISION,
        # FOF_SILENT, and FOF_NORECURSION. The Windows progress UI remains.
        FOF_NOERRORUI = 0x0400
        FOFX_EARLYFAILURE = 0x00100000
        FOFX_SHOWELEVATIONPROMPT = 0x00040000
        flags = FOF_NOERRORUI | FOFX_EARLYFAILURE | FOFX_SHOWELEVATIONPROMPT
        check_hr(
            call_com(operation, 5, ctypes.c_long, (wintypes.DWORD,), flags),
            "设置 Windows 移动选项",
        )

        if owner_hwnd:
            check_hr(
                call_com(
                    operation, 9, ctypes.c_long,
                    (wintypes.HWND,), int(owner_hwnd),
                ),
                "设置 Windows 移动窗口",
            )

        check_hr(
            sh_create_item(
                src_path, None, ctypes.byref(IID_IShellItem),
                ctypes.byref(source_item),
            ),
            "获取源文件夹 Shell 项",
        )
        check_hr(
            sh_create_item(
                dest_parent, None, ctypes.byref(IID_IShellItem),
                ctypes.byref(destination_item),
            ),
            "获取目标文件夹 Shell 项",
        )

        if stop_event is not None and stop_event.is_set():
            raise WindowsShellMoveInterrupted("用户已请求停止后续移动")
        if os.path.lexists(dest_path):
            raise FileExistsError(f"移动开始前目标出现同名文件夹：{dest_path}")

        # IFileOperation: MoveItem slot 14, PerformOperations slot 21,
        # GetAnyOperationsAborted slot 22. MoveItem only QUEUES the move.
        check_hr(
            call_com(
                operation, 14, ctypes.c_long,
                (
                    ctypes.c_void_p, ctypes.c_void_p,
                    wintypes.LPCWSTR, ctypes.c_void_p,
                ),
                source_item, destination_item, None, None,
            ),
            "提交移动请求",
        )

        perform_hr = call_com(operation, 21, ctypes.c_long, ())
        aborted = wintypes.BOOL(0)
        # Must be called even when PerformOperations returns an error.
        aborted_hr = call_com(
            operation, 22, ctypes.c_long,
            (ctypes.POINTER(wintypes.BOOL),), ctypes.byref(aborted),
        )
        check_hr(aborted_hr, "检查 Windows 移动结果")
        if aborted.value:
            result_code = ctypes.c_uint32(perform_hr).value
            raise WindowsShellMoveInterrupted(
                f"Windows 已取消或中断此项移动（HRESULT=0x{result_code:08X}）；"
                f"源目录存在={os.path.lexists(src_path)}，"
                f"目标目录存在={os.path.lexists(dest_path)}；"
                "请检查两个位置，不会将其计为成功"
            )
        check_hr(perform_hr, "执行 Windows 移动")

        # The exact source folder must disappear and the exact destination
        # folder must exist. Anything else is NOT reported as a success.
        source_exists = os.path.lexists(src_path)
        target_exists = os.path.isdir(dest_path)
        if source_exists or not target_exists:
            raise OSError(
                "Windows 返回完成，但移动结果不符合预期："
                f"源目录存在={source_exists}，目标目录存在={target_exists}。"
                "已判定为移动失败，请检查源和目标（不会自动清理文件）"
            )

        return dest_path

    finally:
        # COM objects must be released within the SAME STA thread in which
        # they were created, before OleUninitialize.
        try:
            release_com(destination_item)
        finally:
            try:
                release_com(source_item)
            finally:
                try:
                    release_com(operation)
                finally:
                    if com_initialized:
                        ole_uninitialize()


def safe_move_directory(src_path: str, dest_parent: str) -> str:
    """兼容旧入口：只允许真正的同卷目录重命名，不允许隐式复制。

    保留函数名以兼容已有调用方；若检测到跨卷，则安全失败。
    GUI 入口需要经过用户确认后才能使用 Windows Shell 跨卷移动。
    """
    return move_directory_fast_or_shell(src_path, dest_parent)



def preferred_nfo_file(folder_path: str, preferred_stem: Optional[str] = None) -> Optional[str]:
    try:
        names = [entry.name for entry in os.scandir(folder_path) if entry.is_file()]
    except OSError:
        return None
    return preferred_nfo_from_names(folder_path, names, preferred_stem)


def preferred_image_file(
    folder_path: str, image_type: str, preferred_stem: Optional[str] = None
) -> Optional[str]:
    try:
        names = [entry.name for entry in os.scandir(folder_path) if entry.is_file()]
    except OSError:
        return None
    return preferred_image_from_names(
        folder_path, names, image_type, preferred_stem
    )
