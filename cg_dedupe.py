# -*- coding: utf-8 -*-
"""大锤 NFO/STRM 多版本查重。版本号统一管理于 nfo_version.py。
Windows / Python 3.10+ / PySide6。
直接运行 cg_dedupe.py，或双击 构建exe.bat 打包。
GUI 与查重核心均位于单个 Python 文件中。
"""
from __future__ import annotations

import json
import datetime
import faulthandler
import hashlib
import secrets
import stat
import queue
import ntpath
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import nullcontext
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from fractions import Fraction
from pathlib import Path
from typing import Callable

DATA_DIR_NAME = "CGDedupe_Data"

# 每次 115 / CloudDrive2 真实视频删除完成后，至少间隔三秒才处理下一条。
BULK_DELETE_DELAY_SECONDS = 3.0


APP_TITLE = "大锤 NFO / STRM 多版本查重"


# 外部版本函数和本地回退统一走一个签名，避免 Pylance 函数赋值类型冲突。
_external_get_app_version: Callable[[str], str] | None = None
try:
    from nfo_version import get_app_version as _imported_get_app_version
except ModuleNotFoundError as error:
    if error.name != "nfo_version":
        raise
else:
    _external_get_app_version = _imported_get_app_version


def get_app_version(app_name: str) -> str:
    if _external_get_app_version is None:
        return "9.8.5"
    return _external_get_app_version(app_name)


APP_VERSION = get_app_version("dedupe")


VIDEO_EXTS = frozenset({
    ".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".ts", ".m2ts",
    ".mts", ".mpg", ".mpeg", ".flv", ".webm", ".rm", ".rmvb",
    ".vob", ".3gp", ".divx", ".iso",
})


MEDIA_EXTS = VIDEO_EXTS | {".strm"}


COLORS = {
    "navy": "#1B2A43", "bg": "#F4F7FC", "paper": "#FFFFFF",
    "ink": "#24324A", "muted": "#70819B", "line": "#DFE6F0",
    "blue": "#366CE9", "blue_hover": "#2459CE", "pale": "#E8F0FF",
    "green": "#177E64", "amber": "#AD6A1B", "red": "#B44153",
}


# 基于 Number-Tags-v2.2.6.user.js 的 canon/extract 规则，并保留原版本
# T-xx-xxx、6位日期番号和 CD1/CD2 多碟处理。只分析文件名/NFO 文本，
# 不访问 STRM 指向的 CD2/115 云盘文件。
FC2_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])FC2[-_. ]*(?:PPV[-_. ]*)?(\d{5,9})(?![A-Za-z0-9])", re.I
)

T_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])T[-_. ]?(\d{2,3})[-_. ]?(\d{3})(?![A-Za-z0-9])", re.I
)

DATE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(\d{6})[-_](\d{3,4})(?![A-Za-z0-9])"
)

NUMERIC_PAIR_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(\d{5,9})[-_](\d{2,5})(?![A-Za-z0-9])"
)

# MKBD-S94 等字母数字混合后缀；不能截取成 MKBD-94。
SPECIAL_MODEL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]*)[-_]([A-Za-z]\d{2,8})(?![A-Za-z0-9])",
    re.I,
)

# AB-CD-123、300NTK-670、ABC-123-A/B 等含明确连接符的编码。
SEPARATED_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"((?:\d{1,4})?[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z][A-Za-z0-9]*)*)"
    r"[-_. ](\d{1,8})(?:[-_]([A-Za-z0-9]{1,4}))?"
    r"(?![A-Za-z0-9])", re.I,
)

# SSIS001、300NTK670 等无连接符编码；至少两位字母以过滤 H265。
COMPACT_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])((?:\d{1,4})?[A-Za-z]{2,12})(\d{1,8})"
    r"(?:[-_]([A-Za-z0-9]{1,4}))?(?![A-Za-z0-9])", re.I,
)

# 已识别的分碟后缀只是文件分段，不应分裂为不同番号。
DISC_SUFFIX_PATTERN = re.compile(r"^(?:CD|DISC|DISK|PART)0?[1-9]$", re.I)

# 文件名中的 U/C 一般用作发行版本标记。仅在 NFO 配对时允许其
# 回退匹配同一基础番号，不将 A/B 或其他有区分意义的后缀混为一组。
RELEASE_SUFFIXES_FOR_NFO = frozenset({"U", "C"})


IGNORED_PREFIX = {
    "AV", "AAC", "HDR", "UHD", "FHD", "QHD", "WEB", "HEVC", "AVC",
    "BLURAY", "REMUX", "DVD", "H264", "H265", "X264", "X265",
    "XVID", "DIVX", "VIDEO", "FPS", "DISC", "DISK", "PART", "CD",
    "FC", "DOLBY", "FULLHD", "MPV", "CLOUDDRIVE",
}


DISC_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:CD|DISC|DISK|PART)[-_. ]*0?([1-9])(?!\d)"
    r"|第([一二三四五六七八九])(?:部|张|碟|盘)",
    re.I
)


WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[/\\]")


ANY_URI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def is_trailer_filename(path: str) -> bool:
    """文件名包含 trailer（大小写不敏感）即视为预告片；不检查父目录名。"""
    name = str(path)
    if ANY_URI.match(name):
        try:
            name = urllib.parse.unquote(urllib.parse.urlsplit(name).path)
        except ValueError:
            return False
    return "trailer" in ntpath.basename(name).casefold()


def application_dir() -> Path:
    """返回程序真实所在目录，不使用 PyInstaller onefile 的临时解压目录。

    .py 开发运行：cg_dedupe.py 所在目录。
    PyInstaller onefile / onedir：sys.executable（EXE）所在目录。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def program_data_dir() -> Path:
    """便携模式：日志、SQLite 缓存和配置随 .py/.exe 同目录存放。

    使用 application_dir 而非 sys._MEIPASS：onefile 的解压目录是临时目录。
    如安装于 Program Files 等受保护目录，须将程序移到可写位置。
    """
    return application_dir()


def legacy_program_data_dir() -> Path:
    """仅读取旧版 APPDATA 配置；新的可写文件不再存放在此。"""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / DATA_DIR_NAME


def ensure_program_data_dir() -> Path:
    """在后台线程验证可执行文件或源码同级目录是否可写。"""
    directory = program_data_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".write_test_{os.getpid()}_{secrets.token_hex(4)}"
        with open(probe, "xb") as stream:
            stream.write(b"ok")
        probe.unlink()
    except OSError as error:
        raise OSError(
            f"程序所在目录不可写：{directory}\n"
            "请将 .py/.exe 放在有写入权限的目录；缓存和日志需要保存在这里。"
        ) from error
    return directory



def resource_path(name: str) -> str:
    """分发资源路径：优先 PyInstaller bundle，其次 EXE 旁边；开发时取 .py 旁边。"""
    if getattr(sys, "frozen", False):
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            bundled = Path(bundle_root) / name
            if bundled.is_file():
                return str(bundled)
        return str(application_dir() / name)
    return str(Path(__file__).resolve().parent / name)


def ffprobe_binary() -> str | None:
    """兼容源码旁、EXE 旁及 PyInstaller _internal/bin 下的 ffprobe。"""
    candidates = (
        resource_path("ffprobe.exe"),
        resource_path("bin/ffprobe.exe"),
        resource_path("tools/ffprobe.exe"),
        str(application_dir() / "ffprobe.exe"),
        str(application_dir() / "tools" / "ffprobe.exe"),
        str(application_dir() / "bin" / "ffprobe.exe"),
    )
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("ffprobe.exe") or shutil.which("ffprobe")


def nt_key(path: str) -> str:
    """纯字符串 Windows 路径归一化；不触发 CD2 的网络/文件系统 I/O。"""
    return ntpath.normcase(ntpath.normpath(str(path)))


def is_nested_path(path: str, root: str, *, allow_equal=False) -> bool:
    try:
        normalized = nt_key(path)
        base = nt_key(root)
        common = ntpath.commonpath([normalized, base])
        return common == base and (allow_equal or normalized != base)
    except (OSError, ValueError):
        return False


def unique_scan_roots(roots: list[str]) -> list[str]:
    unique = []
    for root in roots:
        if not root:
            continue
        absolute = os.path.abspath(root)
        if nt_key(absolute) not in {nt_key(item) for item in unique}:
            unique.append(absolute)
    return [root for root in unique if not any(
        root != other and is_nested_path(root, other)
        for other in unique
    )]


def extract_number(value: str | None) -> str:
    """从 NFO/文件名提取规范番号；不同作品的 A/B 等后缀必须保留。

    参考 Number Tags 的 canon/extract，但不会用通用规则吞掉 FC2 前缀。
    保留先前的小于3位数字补零规则及 T / 日期识别。
    """
    if not value:
        return ""
    value = str(value).strip()
    value = value.translate(str.maketrans({
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
        "−": "-", "＿": "_",
    }))

    match = FC2_PATTERN.search(value)
    if match:
        return f"FC2-PPV-{match.group(1)}"

    match = T_PATTERN.search(value)
    if match:
        return f"T-{match.group(1)}-{match.group(2)}"

    match = DATE_PATTERN.search(value)
    if match:
        return f"{match.group(1)}-{match.group(2)}"

    # 部分系列使用单字母 N + 4 位序号，例如 N0780、N0946。
    # 对此类明确形式单独识别，不放宽其他厂牌的字母要求。
    match = re.search(r"(?<![A-Za-z0-9])N[-_. ]?(\d{4})(?![A-Za-z0-9])", value, re.I)
    if match:
        return f"N-{match.group(1)}"

    # 加勒比等纯数字厂牌的四位前缀：4159-001。
    match = re.search(r"(?<![A-Za-z0-9])(\d{4})[-_](\d{3})(?![A-Za-z0-9])", value)
    if match:
        return f"{match.group(1)}-{match.group(2)}"

    match = NUMERIC_PAIR_PATTERN.search(value)
    if match:
        return f"{match.group(1)}_{match.group(2)}"

    match = SPECIAL_MODEL_PATTERN.search(value)
    if match:
        prefix = match.group(1).upper()
        if prefix not in IGNORED_PREFIX:
            return f"{prefix}-{match.group(2).upper()}"

    for pattern in (SEPARATED_NUMBER_PATTERN, COMPACT_NUMBER_PATTERN):
        for match in pattern.finditer(value):
            prefix = match.group(1).upper().replace("_", "-")
            compact = prefix.replace("-", "")
            if sum(letter.isalpha() for letter in compact) < 2:
                continue
            if compact in IGNORED_PREFIX:
                continue
            digits = match.group(2)
            # 与旧版相同：少于三位的序号补足三位。
            number = digits.zfill(3) if len(digits) <= 3 else digits
            suffix = (match.group(3) or "").upper()
            if DISC_SUFFIX_PATTERN.fullmatch(suffix):
                suffix = ""
            return f"{prefix}-{number}" + (f"-{suffix}" if suffix else "")
    return ""


def number_equivalence_key(code: str) -> str:
    """参考 buildKeys 的明确等价关系；返回用于查重的稳定单键。

    - 数字厂牌前缀：300NTK-670 == NTK-670
    - 复合前缀连接符：AB-CD-123 == ABCD-123
    - 数字序号前导零：ABC-001 == ABC-1
    - 字母后缀必须参与匹配：ABC-123-A != ABC-123-B
    - FC2 和纯数字组合有独立命名空间，不与普通字母番号混合。
    """
    if not code:
        return code
    code = code.strip().upper()
    match = re.fullmatch(r"FC2-PPV-(\d{5,9})", code)
    if match:
        return f"FC2-PPV-{match.group(1)}"

    match = re.fullmatch(r"(\d{4,9})[-_](\d{2,5})", code)
    if match:
        # 保留两段数字的分界，避免不同分段的纯数字编号误碰撞。
        return f"NUM:{match.group(1)}_{match.group(2)}"

    match = re.fullmatch(r"T-(\d{2,3})-(\d{3})", code)
    if match:
        return code

    match = re.fullmatch(r"([A-Z][A-Z0-9]*)-([A-Z]\d{2,8})", code)
    if match:
        # MKBD-S94 与 MKBDS94 的紧凑形式在用户脚本中可以匹配。
        return match.group(1) + match.group(2)[0] + "-" + match.group(2)[1:]

    match = re.fullmatch(
        r"([A-Z0-9]+(?:-[A-Z][A-Z0-9]*)*)-(\d{1,8})(?:-([A-Z0-9]{1,4}))?",
        code,
    )
    if not match:
        return code

    compact_prefix = match.group(1).replace("-", "")
    studio_stripped = re.sub(
        r"^\d{1,4}(?=[A-Z]{2,})", "", compact_prefix
    )
    suffix = (match.group(3) or "")
    if DISC_SUFFIX_PATTERN.fullmatch(suffix):
        suffix = ""
    return f"{studio_stripped}-{int(match.group(2))}" + (
        f"-{suffix}" if suffix else ""
    )


def filename_release_base_key(code: str) -> str:
    """U/C 发行标记可回退到主番号；A/B 等真实区分后缀不合并。"""
    if not code:
        return ""
    base, delim, suffix = code.rpartition("-")
    if delim and suffix.upper() in RELEASE_SUFFIXES_FOR_NFO:
        return number_equivalence_key(base)
    return ""


def duplicate_number_key(code: str) -> str:
    """仅合并发行版 U/C 和无后缀番号；保留其他字母后缀的差异。"""
    return filename_release_base_key(code) or number_equivalence_key(code)


def preferred_number_label(entries: list[MediaEntry]) -> str:
    """分组标签优先采用已扫描到的无数字前缀番号，保留所有原始记录。"""
    codes = {entry.code for entry in entries if entry.code}
    return min(
        codes,
        key=lambda code: (
            bool(re.match(r"^\d{1,4}[A-Z]{2,12}-\d+$", code, re.I)),
            len(code),
            code.casefold(),
        ),
    )


def normalize_series(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def read_nfo(path: str, *, strict: bool = False) -> dict[str, str]:
    try:
        # 限制大小，避免异常文件拖慢 SSD 查重。
        with open(path, "rb") as handle:
            raw = handle.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("NFO 超过 2 MiB，已跳过")
    except (OSError, ValueError):
        if strict:
            raise
        return {}
    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError):
        root = None
        for encoding in ("utf-8-sig", "gb18030", "utf-16"):
            try:
                text = raw.decode(encoding)
                text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1)
                root = ET.fromstring(text)
                break
            except (ET.ParseError, UnicodeError, ValueError):
                continue
        if root is None:
            if strict:
                raise ValueError("NFO XML 或字符编码无效")
            return {}
    data = {}
    for tag in ("num", "title", "originaltitle", "sorttitle", "series"):
        data[tag] = (root.findtext(tag) or "").strip()
    if not data.get("series"):
        data["series"] = (root.findtext("./set/name") or "").strip()
    return data


def read_strm(path: str, *, strict: bool = False) -> str:
    """STRM 是文本指针，读取少量本地字节，不访问指向的云端视频。"""
    try:
        with open(path, "rb") as handle:
            raw = handle.read(16 * 1024 + 1)
        if len(raw) > 16 * 1024:
            raise ValueError("STRM 超过 16 KiB，已跳过")
    except (OSError, ValueError):
        if strict:
            raise
        return ""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ("utf-16",)
    elif b"\x00" in raw:
        encodings = ("utf-16-be",) if raw[::2].count(0) > raw[1::2].count(0) else ("utf-16-le",)
    else:
        encodings = ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            content = raw.decode(encoding)
            if "\x00" in content:
                continue
            for line in content.splitlines():
                text = line.strip().strip('"').strip("'")
                if text and not text.startswith("#"):
                    return text
        except UnicodeError:
            continue
    if strict:
        raise ValueError("STRM 为空或编码无效")
    return ""


def normalize_strm_target(
    strm_path: str, raw: str, from_prefix: str = "", to_prefix: str = ""
) -> tuple[str, str]:
    """返回 (地址, path/url/unmapped/invalid)；从不检查源文件是否存在。"""
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        return "", "invalid"
    if ANY_URI.match(raw):
        try:
            urllib.parse.urlsplit(raw)
        except ValueError:
            return raw, "invalid"
    if raw.lower().startswith("file://"):
        parts = urllib.parse.urlsplit(raw)
        if parts.netloc and parts.netloc.lower() != "localhost":
            raw = "\\\\" + parts.netloc + urllib.parse.unquote(parts.path).replace("/", "\\")
        else:
            raw = urllib.parse.unquote(parts.path)
            if re.match(r"^/[A-Za-z]:/", raw):
                raw = raw[1:]
    elif ANY_URI.match(raw):
        scheme = urllib.parse.urlsplit(raw).scheme.casefold()
        return (raw, "url") if scheme in {"http", "https", "rtsp", "ftp", "smb"} else (raw, "unmapped")

    # Linux 路径可手动映射到 CD2 本地盘符，例如 /115/视频 -> M:\视频
    source = from_prefix.strip().replace("\\", "/").rstrip("/")
    raw_unix = raw.replace("\\", "/")
    if source and to_prefix.strip() and (
        raw_unix.casefold() == source.casefold()
        or raw_unix.casefold().startswith(source.casefold() + "/")
    ):
        suffix = raw_unix[len(source):].lstrip("/")
        segments = [part for part in suffix.split("/") if part]
        raw = ntpath.join(to_prefix.strip(), *segments)
    if WINDOWS_DRIVE.match(raw) or raw.startswith("\\\\") or raw.startswith("//"):
        return ntpath.normpath(raw), "path"
    if ntpath.splitdrive(raw)[0] or raw.startswith("\\"):
        return raw, "unmapped"
    if raw.startswith("/"):
        return raw, "unmapped"
    return ntpath.normpath(ntpath.join(ntpath.dirname(strm_path), raw)), "path"


def get_disc_number(text: str) -> int | None:
    match = DISC_PATTERN.search(text)
    if not match:
        return None
    if match.group(1):
        return int(match.group(1))
    return "一二三四五六七八九".index(match.group(2)) + 1


def get_edition_group(path: str, disc: int | None) -> str:
    """同一套 CD1/CD2/CD3 作为一个版本，避免误算三部重复。"""
    if disc is None:
        return ""
    folder = ntpath.dirname(path)
    folder_name = ntpath.basename(folder)
    clean_stem = DISC_PATTERN.sub("", ntpath.splitext(ntpath.basename(path))[0])
    clean_stem = clean_stem.strip(" ._-").casefold()
    clean_folder = ""
    if get_disc_number(folder_name) is not None:
        clean_folder = DISC_PATTERN.sub("", folder_name).strip(" ._-").casefold()
        folder = ntpath.dirname(folder)
    return f"discs:{nt_key(folder)}|{clean_folder}|{clean_stem}"


def determine_key(nfo: dict, media_path: str, target: str, mode: str) -> str:
    if mode == "系列":
        return normalize_series(nfo.get("series"))
    candidates = (
        nfo.get("num", ""),
        ntpath.splitext(ntpath.basename(media_path))[0],
        ntpath.basename(ntpath.dirname(media_path)),
        nfo.get("title", ""),
        nfo.get("originaltitle", ""),
        nfo.get("sorttitle", ""),
        ntpath.basename(ntpath.dirname(ntpath.dirname(media_path))),
        ntpath.splitext(ntpath.basename(target))[0] if target else "",
    )
    for candidate in candidates:
        result = extract_number(candidate)
        if result:
            return result
    return ""


def paired_nfo(
    media_path: str, nfo_paths: list[str],
    parsed: dict[str, dict] | None = None,
    stem_index: dict[str, list[str]] | None = None,
    number_index: dict[str, list[str]] | None = None,
    *, allow_fallback: bool = True,
) -> str:
    """建立 O(1) 的同名/NFO 编码索引，避免同目录万级文件的 O(N²) 配对。"""
    if not nfo_paths:
        return ""
    parsed = parsed or {}
    stem = ntpath.splitext(ntpath.basename(media_path))[0].casefold()
    movie_code = extract_number(stem)

    def is_compatible(path: str) -> bool:
        info = parsed.get(path, {})
        known = extract_number(info.get("num")) or extract_number(ntpath.basename(path))
        if not movie_code or not known:
            return True
        return duplicate_number_key(movie_code) == duplicate_number_key(known)

    if stem_index is None:
        exact_candidates = [
            path for path in nfo_paths
            if ntpath.splitext(ntpath.basename(path))[0].casefold() == stem
        ]
    else:
        exact_candidates = stem_index.get(stem, [])
    for path in exact_candidates:
        if is_compatible(path):
            return path
    if movie_code:
        if number_index is None:
            number_candidates = [
                path for path in nfo_paths
                if duplicate_number_key(
                    extract_number(parsed.get(path, {}).get("num"))
                    or extract_number(ntpath.basename(path))
                ) == duplicate_number_key(movie_code)
            ]
        else:
            # 兼容由旧代码构建的原始番号索引及新版等价键索引。
            number_candidates = number_index.get(duplicate_number_key(movie_code), [])
            if not number_candidates:
                number_candidates = number_index.get(movie_code, [])
            if not number_candidates:
                number_candidates = number_index.get(number_equivalence_key(movie_code), [])
        if len(number_candidates) > 1:
            # 如果一个目录同时有 NTK-670 和 300NTK-670 的 NFO，
            # 优先选择与文件名原始番号完全一致的 NFO，避免配错元数据。
            exact = [
                path for path in number_candidates
                if (extract_number(parsed.get(path, {}).get("num"))
                    or extract_number(ntpath.basename(path))) == movie_code
            ]
            if len(exact) == 1:
                return exact[0]
        if len(number_candidates) == 1:
            return number_candidates[0]
    # movie.nfo 是目录元数据；仅在目录作品无歧义时允许兜底。
    generic = [path for path in nfo_paths
               if ntpath.basename(path).casefold() == "movie.nfo" and is_compatible(path)]
    if allow_fallback and len(generic) == 1:
        return generic[0]
    if allow_fallback and len(nfo_paths) == 1 and is_compatible(nfo_paths[0]):
        return nfo_paths[0]
    return ""


@dataclass
class MediaEntry:
    identifier: int
    code: str
    media_path: str       # 实际 .strm / .mp4 / .mkv 等本地扫描文件
    media_type: str       # STRM / 视频 / NFO
    nfo_path: str
    raw_target: str       # STRM 的原始首行，或实际视频路径
    target: str           # 用于播放/ffprobe 的地址
    target_kind: str      # path / url / unmapped / invalid / nfo
    folder: str
    edition_group: str    # 多碟的归并组
    disc: int | None = None
    info: dict = field(default_factory=dict)
    info_status: str = "未读取"
    nfo_num: str = ""     # 仅用于未识别番号日志；来自现有 NFO 解析或缓存

    def distinct_version_key(self) -> str:
        if self.target and self.target_kind in ("path", "url"):
            return target_identity(self.target, self.target_kind)
        if self.target and self.target_kind == "unmapped":
            return "unmapped:" + self.target
        # 共用 NFO 不代表共用视频；无效 STRM 也必须保留各自的记录身份。
        return "media:" + nt_key(self.media_path)


def target_identity(target: str, kind: str | None = None) -> str:
    """Windows 路径忽略大小写；URL 保留路径、查询及签名的大小写。"""
    kind = kind or ("url" if ANY_URI.match(target) else "path")
    return "url:" + target if kind == "url" else "path:" + nt_key(target)


def version_groups(entries: list[MediaEntry]) -> list[list[MediaEntry]]:
    """按实际本地文件计数；CD1/CD2 同套归并，不因共享视频源漏掉重复 STRM。"""
    parent = list(range(len(entries)))
    seen = {}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, entry in enumerate(entries):
        # 两个不同路径的 STRM 即使指向同一视频，也需要列为重复文件。
        # 但同套 CD1/CD2 仍只计一个版本。
        keys = ["file:" + nt_key(entry.media_path)]
        if entry.edition_group:
            keys.append(entry.edition_group)
        for key in keys:
            if key in seen:
                parent[find(i)] = find(seen[key])
            else:
                seen[key] = i
    groups = defaultdict(list)
    for i, entry in enumerate(entries):
        groups[find(i)].append(entry)
    return list(groups.values())


def version_count(entries: list[MediaEntry]) -> int:
    return len(version_groups(entries))


def group_matches_query(code: str, entries: list[MediaEntry], query: str) -> bool:
    return not query or query in code.casefold() or any(
        query in row.code.casefold() or query in row.folder.casefold()
        or query in row.target.casefold() or query in row.media_path.casefold()
        for row in entries
    )


def number_sort_key(code: str) -> tuple:
    """番号自然升序：ABC-2 排在 ABC-10 前，不改变原有查重分组。"""
    pieces = re.split(r"([0-9]+)", code.casefold())
    return tuple(
        (1, int(piece)) if piece.isascii() and piece.isdecimal() else (0, piece)
        for piece in pieces if piece
    )


def unique_group_folders(entries: list[MediaEntry]) -> list[str]:
    """按文件夹全路径（Windows 不区分大小写）去重；不访问 CD2。"""
    folders = []
    seen = set()
    for entry in entries:
        folder = entry.folder
        if not folder:
            continue
        key = nt_key(folder)
        if key not in seen:
            seen.add(key)
            folders.append(folder)
    return folders


def group_duplicates(
    entries: list[MediaEntry], *, mode: str, fuzzy=False,
    threshold=0.8, stop_event: threading.Event | None = None,
):
    buckets: dict[str, list[MediaEntry]] = defaultdict(list)
    for entry in entries:
        if entry.code:
            key = (duplicate_number_key(entry.code)
                   if mode != "系列" else entry.code)
            buckets[key].append(entry)

    # 只针对“番号”使用油猴脚本的明确等价键，不进行模糊匹配。
    # 保留每条 entry.code 的原始番号；显示名优先选无数字前缀的版本。
    if mode != "系列":
        return {
            preferred_number_label(rows): rows
            for rows in buckets.values()
            if version_count(rows) >= 2
        }
    if not fuzzy or len(buckets) < 2:
        return {key: rows for key, rows in buckets.items() if version_count(rows) >= 2}

    values = sorted(buckets)
    parent = list(range(len(values)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left, right):
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    # 相似匹配仅限系列；用长度过滤减少无意义的比较。
    for left in range(len(values)):
        if stop_event is not None and stop_event.is_set():
            return {}
        first = values[left]
        for right in range(left + 1, len(values)):
            if right % 128 == 0 and stop_event is not None and stop_event.is_set():
                return {}
            second = values[right]
            if 2 * min(len(first), len(second)) / (len(first) + len(second)) < threshold:
                continue
            matcher = SequenceMatcher(None, first, second, autojunk=False)
            if matcher.quick_ratio() >= threshold and matcher.ratio() >= threshold:
                union(left, right)
    combined: dict[int, list[MediaEntry]] = defaultdict(list)
    for index, key in enumerate(values):
        combined[find(index)].extend(buckets[key])
    result = {}
    for group in combined.values():
        if version_count(group) >= 2:
            key = min((entry.code for entry in group), key=str.casefold)
            result[key] = group
    return result


class LocalTextCache:
    """SQLite 仅由扫描管理线程写入；只读快照供本地小文件线程共享。"""

    def __init__(self, path: Path):
        self.connection = sqlite3.connect(str(path), timeout=8)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS local_text_cache ("
            "path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, "
            "size INTEGER NOT NULL, payload TEXT NOT NULL)"
        )
        self.connection.commit()
        # 一次顺序读，避免每个文件执行一次 SELECT。只缓存 NFO/STRM 元数据。
        self.snapshot = {row[0]: row[1:] for row in self.connection.execute(
            "SELECT path, mtime_ns, size, payload FROM local_text_cache")}
        self.pending = []

    def store_many(self, rows):
        self.pending.extend(rows)
        if len(self.pending) >= 512:
            self.flush()

    def flush(self):
        if self.pending:
            self.connection.executemany(
                "INSERT OR REPLACE INTO local_text_cache VALUES (?, ?, ?, ?)", self.pending)
            self.connection.commit()
            self.pending.clear()

    def close(self):
        try:
            self.flush()
        finally:
            self.connection.close()


def iter_scan_folders(roots, stop_event, errors):
    """单次 scandir 枚举；不跟随链接/junction，不访问 STRM 目标。"""
    for root in unique_scan_roots(roots):
        stack = [root]
        while stack and not stop_event.is_set():
            folder = stack.pop()
            nfos, media, children = [], [], []
            try:
                with os.scandir(folder) as items:
                    for item in items:
                        if stop_event.is_set():
                            return
                        try:
                            if item.is_symlink():
                                continue
                            if item.is_dir(follow_symlinks=False):
                                if os.name == "nt" and (
                                    item.stat(follow_symlinks=False).st_file_attributes & 0x400
                                ):
                                    continue
                                children.append(item.path)
                                continue
                            ext = ntpath.splitext(item.name)[1].casefold()
                            if ext != ".nfo" and ext not in MEDIA_EXTS:
                                continue
                            if not item.is_file(follow_symlinks=False):
                                continue
                            if ext == ".nfo":
                                nfos.append(item)
                            else:
                                media.append(item)
                        except OSError as error:
                            if len(errors) < 25:
                                errors.append(f"{item.path}：{error}")
            except OSError as error:
                if len(errors) < 25:
                    errors.append(f"{folder}：{error}")
                continue
            # 稳定的目录遍历顺序；文件在各目录任务内排序。
            stack.extend(sorted(children, key=str.casefold, reverse=True))
            yield folder, nfos, media


def read_scan_folder(job, snapshot, mode, source_prefix, mount_prefix, stop_event):
    folder, nfo_items, media_items = job
    # 先记住原目录是否存在媒体，避免“只有预告片 + 普通 NFO”
    # 被错误视为纯 NFO 目录，重新生成一条假版本。
    had_media_files = bool(media_items)
    nfo_items = [item for item in nfo_items if not is_trailer_filename(item.name)]
    media_items = [item for item in media_items if not is_trailer_filename(item.name)]
    nfo_items.sort(key=lambda item: item.name.casefold())
    media_items.sort(key=lambda item: item.name.casefold())
    changed, errors, records = [], [], []
    hits = {"nfo": 0, "strm": 0}
    reads = {"nfo": 0, "strm": 0}

    def load(item, kind):
        if stop_event.is_set():
            return {} if kind == "nfo" else ""
        try:
            st = item.stat(follow_symlinks=False)
            key = nt_key(item.path)
            cached = snapshot.get(key)
            if cached and cached[:2] == (st.st_mtime_ns, st.st_size):
                try:
                    value = json.loads(cached[2])
                    if ((kind == "nfo" and isinstance(value, dict)) or
                            (kind == "strm" and isinstance(value, str))):
                        hits[kind] += 1
                        return value
                except (ValueError, TypeError):
                    pass
            reads[kind] += 1
            value = (read_nfo(item.path, strict=True) if kind == "nfo"
                     else read_strm(item.path, strict=True))
            # DirEntry.stat 有缓存；此处有意 fresh stat，避免读取期间变动被缓存。
            after = os.stat(item.path, follow_symlinks=False)
            if (after.st_mtime_ns, after.st_size) == (st.st_mtime_ns, st.st_size):
                changed.append((key, st.st_mtime_ns, st.st_size,
                                json.dumps(value, ensure_ascii=False)))
            return value
        except (OSError, ValueError) as error:
            if len(errors) < 25:
                errors.append(f"{item.path}：{error}")
            return {} if kind == "nfo" else ""

    nfos = [item.path for item in nfo_items]
    parsed = {}
    for item in nfo_items:
        if stop_event.is_set():
            break
        parsed[item.path] = load(item, "nfo")
    stem_index, number_index = defaultdict(list), defaultdict(list)
    for path in nfos:
        stem_index[ntpath.splitext(ntpath.basename(path))[0].casefold()].append(path)
        number = extract_number(parsed.get(path, {}).get("num")) or extract_number(ntpath.basename(path))
        if number:
            number_index[duplicate_number_key(number)].append(path)
    # 番号提取一次即可复用：避免大目录上反复执行多轮正则表达式。
    codes = set()
    all_media_coded = True
    for item in media_items:
        raw_code = extract_number(item.name)
        if raw_code:
            codes.add(duplicate_number_key(raw_code))
        else:
            all_media_coded = False
    allow_fallback = len(media_items) == 1 or (
        len(codes) == 1 and all_media_coded
    )
    for item in media_items:
        if stop_event.is_set():
            break
        media_path = item.path
        is_strm = item.name.casefold().endswith(".strm")
        nfo_path = paired_nfo(media_path, nfos, parsed, stem_index, number_index,
                              allow_fallback=allow_fallback)
        raw = load(item, "strm") if is_strm else media_path
        target, kind = (normalize_strm_target(media_path, raw, source_prefix, mount_prefix)
                        if is_strm else (media_path, "path"))
        # STRM 自身可能没有 trailer，但指向的实际视频是预告片。
        if is_strm and is_trailer_filename(target):
            continue
        disc = get_disc_number(ntpath.splitext(item.name)[0])
        if disc is None:
            disc = get_disc_number(ntpath.basename(folder))
        records.append(MediaEntry(
            identifier=0, code=determine_key(parsed.get(nfo_path, {}), media_path, target, mode),
            media_path=media_path, media_type="STRM" if is_strm else "视频",
            nfo_path=nfo_path, raw_target=raw, target=target, target_kind=kind,
            folder=folder, edition_group=get_edition_group(media_path, disc), disc=disc,
            info_status={"unmapped": "待映射", "invalid": "STRM 无效"}.get(kind, "未读取"),
            nfo_num=str(parsed.get(nfo_path, {}).get("num") or ""),
        ))
    # 有媒体的目录内，未配对 NFO 只是元数据，不能凭空多算一个媒体版本。
    # 纯 NFO 目录继续支持旧版查重。
    if not had_media_files:
        for path in nfos:
            if stop_event.is_set():
                break
            disc = get_disc_number(ntpath.splitext(ntpath.basename(path))[0])
            if disc is None:
                disc = get_disc_number(ntpath.basename(folder))
            records.append(MediaEntry(
                identifier=0, code=determine_key(parsed.get(path, {}), path, "", mode),
                media_path=path, media_type="NFO", nfo_path=path, raw_target="", target="",
                target_kind="nfo", folder=folder, edition_group=get_edition_group(path, disc),
                disc=disc, info_status="仅 NFO",
                nfo_num=str(parsed.get(path, {}).get("num") or ""),
            ))
    return records, changed, hits, reads, errors


def scan_library(
    roots: list[str], mode: str, source_prefix: str, mount_prefix: str,
    stop_event: threading.Event, notify: Callable[[int, int], None],
    cache_path: Path | None = None, cache_report: dict | None = None,
    *, scan_workers: int = 1,
) -> tuple[list[MediaEntry], int, int, int, list[str]]:
    """本地文件有界并行；SQLite 在管理线程；目标视频零 I/O。"""
    entries, errors = [], []
    cache = None
    snapshot = {}
    if cache_path is not None:
        try:
            cache = LocalTextCache(cache_path)
            snapshot = cache.snapshot
        except (OSError, sqlite3.Error) as error:
            errors.append(f"本地文本缓存不可用，本次直接读取：{error}")
    nfo_total = strm_total = video_total = visited = processed = 0
    stats = {"hits": 0, "parsed": 0, "strm_hits": 0, "strm_read": 0}
    workers = max(1, min(int(scan_workers), 16))
    pending = []
    last_notify = time.monotonic()

    def consume(result):
        nonlocal processed, cache, last_notify, strm_total, video_total
        records, changed, hits, reads, notices = result
        entries.extend(records)
        # 统计实际保留下来的媒体，不把预告片计入扫描结果。
        strm_total += sum(entry.media_type == "STRM" for entry in records)
        video_total += sum(entry.media_type == "视频" for entry in records)
        processed += hits["nfo"] + reads["nfo"]
        stats["hits"] += hits["nfo"]
        stats["parsed"] += reads["nfo"]
        stats["strm_hits"] += hits["strm"]
        stats["strm_read"] += reads["strm"]
        errors.extend(notices[:max(0, 25 - len(errors))])
        if cache is not None:
            try:
                cache.store_many(changed)
            except sqlite3.Error as error:
                errors.append(f"缓存写入失败，本次继续扫描：{error}")
                try:
                    cache.connection.close()
                finally:
                    cache = None
        now = time.monotonic()
        if now - last_notify >= 0.15:
            notify(visited, processed)
            last_notify = now

    try:
        # 仅保留最多 2*workers 个目录任务，不把整库 Future 堆进内存。
        executor = (ThreadPoolExecutor(max_workers=workers, thread_name_prefix="CGDedupe-Local")
                    if workers > 1 else nullcontext())
        with executor as pool:
            try:
                for job in iter_scan_folders(roots, stop_event, errors):
                    if stop_event.is_set():
                        break
                    visited += 1
                    nfo_total += sum(
                        not is_trailer_filename(item.name) for item in job[1]
                    )
                    if pool is None:
                        consume(read_scan_folder(job, snapshot, mode, source_prefix, mount_prefix, stop_event))
                        continue
                    pending.append(pool.submit(read_scan_folder, job, snapshot, mode,
                                               source_prefix, mount_prefix, stop_event))
                    if len(pending) >= workers * 2:
                        # 按提交顺序归并，保证每次扫描 ID/显示顺序稳定。
                        consume(pending.pop(0).result())
                for future in pending:
                    if stop_event.is_set():
                        future.cancel()
                    if not future.cancelled():
                        consume(future.result())
            except BaseException:
                stop_event.set()
                for future in pending:
                    future.cancel()
                raise
    finally:
        if cache_report is not None:
            cache_report.update(stats)
        if cache is not None:
            try:
                cache.close()
            except sqlite3.Error as error:
                errors.append(f"缓存保存失败：{error}")
    for identifier, entry in enumerate(entries, 1):
        entry.identifier = identifier
    notify(visited, processed)
    return entries, nfo_total, strm_total, video_total, errors


def classify_resolution(width: int, height: int) -> str:
    if width >= 3800 or height >= 2100:
        return "4K"
    if width >= 2500 or height >= 1400:
        return "2K"
    if width >= 1900 or height >= 1000:
        return "1080p"
    if width >= 1200 or height >= 680:
        return "720p"
    return "SD" if width and height else "—"


def format_fps(value) -> str:
    try:
        rate = float(Fraction(str(value or "0/0")))
        if 0 < rate <= 500:
            return f"{rate:.2f}".rstrip("0").rstrip(".") + " fps"
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return "—"


def parse_ffprobe_result(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("ffprobe JSON 根节点不是对象")
    streams = [stream for stream in (data.get("streams") or []) if isinstance(stream, dict)]
    video = next((stream for stream in streams
                  if stream.get("codec_type") == "video"
                  and not (stream.get("disposition") or {}).get("attached_pic")), {})
    audio = sorted({
        str(stream.get("codec_name") or "").upper()
        for stream in streams if stream.get("codec_type") == "audio"
    })
    fmt = data.get("format") or {}
    try:
        width = int(video.get("width") or 0)
        height = int(video.get("height") or 0)
    except (TypeError, ValueError):
        width = height = 0

    def to_int(value):
        try:
            return int(value) or None
        except (ValueError, TypeError):
            return None

    def to_float(value):
        try:
            return float(value) or None
        except (ValueError, TypeError):
            return None

    fps = format_fps(video.get("avg_frame_rate"))
    if fps == "—":
        fps = format_fps(video.get("r_frame_rate"))
    return {
        "width": width,
        "height": height,
        "resolution": classify_resolution(width, height),
        "fps": fps,
        "codec": str(video.get("codec_name") or "—").upper(),
        "audio": "、".join(audio),
        "size": to_int(fmt.get("size")),
        "duration": to_float(fmt.get("duration")),
        "bitrate": to_int(video.get("bit_rate") or fmt.get("bit_rate")),
        "hdr": ("HDR" if str(video.get("color_transfer") or "").casefold()
                in {"smpte2084", "arib-std-b67"} else ""),
        "verified": bool(width and height),
    }


def filename_hint(path: str) -> dict:
    """无法实测时明确显示问号，绝不将文件名推测当作真实参数。"""
    name = ntpath.basename(path).casefold()
    quality = "—"
    for regex, label in (
        (r"(?<![A-Za-z0-9])(?:2160p|4k|uhd)(?![A-Za-z0-9])", "4K?"),
        (r"(?<![A-Za-z0-9])(?:1080p|fhd)(?![A-Za-z0-9])", "1080p?"),
        (r"(?<![A-Za-z0-9])720p(?![A-Za-z0-9])", "720p?"),
    ):
        if re.search(regex, name):
            quality = label
            break
    match = re.search(r"(?<!\d)(24|25|30|50|60|120)\s*(?:fps|帧)(?![A-Za-z0-9])", name)
    return {
        "resolution": quality,
        "fps": f"{match.group(1)} fps?" if match else "—",
        "verified": False,
    }


def human_size(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number < 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if number < 1024 or unit == "TB":
            return f"{number:.0f} B" if unit == "B" else f"{number:.1f} {unit}"
        number /= 1024
    return "—"


def human_duration(value) -> str:
    try:
        seconds = round(float(value))
    except (ValueError, TypeError):
        return "—"
    if seconds <= 0:
        return "—"
    hours, leftover = divmod(seconds, 3600)
    minutes, seconds = divmod(leftover, 60)
    return (f"{hours}:{minutes:02d}:{seconds:02d}" if hours
            else f"{minutes}:{seconds:02d}")


class Interrupted(Exception):
    """正常取消后台任务，不属于扫描/探测错误。"""


def run_ffprobe(
    binary: str, target: str, stop_event: threading.Event, timeout=15
) -> dict:
    """ffprobe 是外部子进程，超时/停止都杀掉它，避免 CD2 挂载 I/O 卡死应用。"""
    command = [
        binary, "-hide_banner", "-v", "error",
        "-probesize", "2000000", "-analyzeduration", "1000000",
        "-show_entries",
        ("stream=codec_type,codec_name,width,height,avg_frame_rate,"
         "r_frame_rate,bit_rate,color_transfer:stream_disposition=attached_pic:format=duration,size,bit_rate"),
        "-of", "json", "-i", target,
    ]
    proc = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    started = time.monotonic()
    try:
        while True:
            if stop_event.is_set():
                raise Interrupted()
            if time.monotonic() - started > timeout:
                raise TimeoutError(f"视频探测超过 {timeout} 秒")
            try:
                stdout, stderr = proc.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                continue
        if proc.returncode:
            raise OSError(
                stderr.decode("utf-8", errors="replace").strip()[:180]
                or f"ffprobe 返回错误 {proc.returncode}"
            )
        info = parse_ffprobe_result(json.loads(stdout.decode("utf-8", "replace")))
        if not info.get("verified"):
            raise ValueError("视频中未发现可识别的视频轨道")
        return info
    finally:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                # 不阻塞 GUI；ffprobe 已收到 kill。
                pass


class MediaIndex:
    """本地视频参数库；兼容旧版原始路径键，不检查云端源文件。"""

    def __init__(self, file_path: Path):
        self.connection = sqlite3.connect(str(file_path), timeout=8)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS video_cache ("
                                "target TEXT PRIMARY KEY, recorded_at REAL NOT NULL, details_json TEXT NOT NULL)")
        self.connection.commit()
        self.pending = 0

    def get(self, target: str, *, maximum_age_days=14) -> dict | None:
        return self.get_many([target], maximum_age_days=maximum_age_days).get(target)

    def get_many(self, targets: list[str], *, maximum_age_days=14) -> dict[str, dict]:
        unique = list(dict.fromkeys(targets))
        keys = list(dict.fromkeys(key for target in unique
                                 for key in (target_identity(target), target)))
        found = {}
        cutoff = time.time() - maximum_age_days * 86400
        for offset in range(0, len(keys), 400):
            batch = keys[offset:offset + 400]
            placeholders = ",".join("?" for _ in batch)
            for key, payload in self.connection.execute(
                f"SELECT target, details_json FROM video_cache WHERE recorded_at >= ? "
                f"AND target IN ({placeholders})", [cutoff, *batch],
            ):
                try:
                    data = json.loads(payload)
                    if isinstance(data, dict) and data.get("verified"):
                        found[key] = data
                except (TypeError, ValueError):
                    pass
        return {target: found.get(target_identity(target), found.get(target))
                for target in unique if target_identity(target) in found or target in found}

    def forget(self, targets: list[str]):
        keys = {key for target in targets for key in (target, target_identity(target))}
        self.connection.executemany("DELETE FROM video_cache WHERE target=?", [(key,) for key in keys])
        self.connection.commit()
        self.pending = 0

    def store(self, target: str, details: dict):
        if not details.get("verified"):
            return
        self.connection.execute(
            "INSERT OR REPLACE INTO video_cache VALUES (?, ?, ?)",
            (target_identity(target), time.time(), json.dumps(details, ensure_ascii=False)))
        self.pending += 1
        if self.pending >= 32:
            self.flush()

    def flush(self):
        self.connection.commit()
        self.pending = 0

    def close(self):
        try:
            self.flush()
        finally:
            self.connection.close()


def append_unrecognized_scan_log(
    records: list[MediaEntry], field_mode: str,
) -> str:
    """在扫描工作线程写入未识别记录，不访问云端源视频。

    只追加真正未识别的记录。每条包含当前扫描时间、文件/NFO 路径、
    已缓存的 NFO 原始 num，以及当前识别规则未命中的原因。
    完整扫描且有未识别记录时才创建日志。
    """
    unknown = [entry for entry in records if not entry.code]
    if not unknown:
        return ""

    log_file = program_data_dir() / "scan_unrecognized.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now(
        datetime.timezone.utc
    ).astimezone().isoformat()
    scan_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    with open(log_file, "a", encoding="utf-8", newline="\n") as handle:
        for entry in unknown:
            number = entry.nfo_num.strip()
            if field_mode == "系列":
                reason = "未能从关联 NFO 的 series 字段确定系列"
            elif number and not extract_number(number):
                reason = "NFO 的 num 字段未符合现有番号规则，其他候选名称也未命中"
            elif not entry.nfo_path:
                reason = "未关联 NFO，文件名及目录名等候选未识别出番号"
            else:
                reason = "关联 NFO 及文件名、目录名等候选均未识别出番号"
            event = {
                "time": timestamp,
                "scan_id": scan_id,
                "field_mode": field_mode,
                "media_type": entry.media_type,
                "media_path": entry.media_path,
                "nfo_path": entry.nfo_path,
                "nfo_num": number[:512],
                "reason": reason,
            }
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return str(log_file)


def json_load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def json_save(path: Path, value: dict):
    """原子替换配置文件；调用方应确保程序数据目录可写。"""
    temp = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(temp), str(path))
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


class ConfigWriter(threading.Thread):
    """后台写设置、记录人工取消日志，Qt 主线程不做磁盘写入。"""

    def __init__(self):
        super().__init__(name="CGDedupe-SettingsWriter", daemon=False)
        self.pending: "queue.Queue[tuple[str, Path | None, dict]]" = queue.Queue()
        self.stop_event = threading.Event()  # set() = 队列清空后退出

    def submit(self, path: Path, value: dict):
        self.pending.put(("json", path, dict(value)))

    def submit_merge_event(self, operation_id: str, stage: str, **details):
        self.pending.put(("merge_event", None, {
            "operation_id": operation_id, "stage": stage, "details": details
        }))

    def run(self):
        while not self.stop_event.is_set() or not self.pending.empty():
            try:
                kind, path, payload = self.pending.get(timeout=0.15)
            except queue.Empty:
                continue
            try:
                if kind == "json" and path is not None:
                    # 目录创建/写入在后台，不阻塞 Qt 界面线程。
                    ensure_program_data_dir()
                    json_save(path, payload)
                elif kind == "merge_event":
                    append_merge_delete_log(
                        payload["operation_id"], payload["stage"],
                        **payload["details"],
                    )
            except (OSError, ValueError) as error:
                if sys.stderr is not None:
                    print(f"无法保存设置/日志：{error}", file=sys.stderr)
            finally:
                self.pending.task_done()


def find_onecommander_executable() -> str | None:
    """尝试发现 OneCommander，用于多目录打开新标签。

    检测不到时回退旧版的 os.startfile，交由 Windows 注册的文件管理器接管。
    只在 FolderOpenWorker 子线程运行，避免 GUI 主线程访问注册表/磁盘。
    """
    for executable in ("OneCommander.exe", "onecommander.exe"):
        discovered = shutil.which(executable)
        if discovered:
            return str(discovered)

    if sys.platform != "win32":
        return None

    # App Paths 是 Windows 常见的 exe 注册方式，不假定用户安装在固定位置。
    try:
        import winreg
    except ImportError:
        winreg = None
    if winreg is not None:
        registry_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\OneCommander.exe"
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, registry_path) as key:
                    registered, _type = winreg.QueryValueEx(key, "")
                candidate = str(registered).strip().strip('"')
                if candidate.casefold().endswith("onecommander.exe") and os.path.isfile(candidate):
                    return candidate
            except (FileNotFoundError, PermissionError, OSError):
                continue

    # 普通 MSI / 用户级安装位置；没发现时仍使用系统关联打开。
    candidates = []
    program_files = os.environ.get("PROGRAMFILES")
    local_appdata = os.environ.get("LOCALAPPDATA")
    if program_files:
        candidates.append(os.path.join(program_files, "OneCommander", "OneCommander.exe"))
    if local_appdata:
        candidates.extend((
            os.path.join(local_appdata, "Programs", "OneCommander", "OneCommander.exe"),
            os.path.join(local_appdata, "OneCommander", "OneCommander.exe"),
            os.path.join(local_appdata, "Microsoft", "WindowsApps", "OneCommander.exe"),
        ))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


class MergeDeleteBlocked(Exception):
    """完整预检不通过，禁止任何删除。"""


class MergeDeletePartial(MergeDeleteBlocked):
    """删除了部分目标；批量任务必须立即停止，不可自动继续下一项。"""

    def __init__(self, message: str, completed_steps: list[str]):
        super().__init__(message)
        self.completed_steps = list(completed_steps)


def merge_delete_log_path() -> Path:
    """合并删除审计日志：脚本或 EXE 同级 merge_delete.jsonl。"""
    return program_data_dir() / "merge_delete.jsonl"


def append_merge_delete_log(operation_id: str, stage: str, **data):
    """删除前写入审计日志并落盘；日志写入失败则不能启动删除。

    JSON Lines 每行一条记录，记录每次预检、拦截、实际删除及部分完成。
    """
    path = merge_delete_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "time": datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(),
        "operation_id": operation_id,
        "stage": stage,
        **data,
    }
    with open(path, "a", encoding="utf-8", newline="\n") as out:
        out.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        out.flush()
        os.fsync(out.fileno())


def check_merge_stop(stop_event: threading.Event):
    if stop_event.is_set():  # 线程全局统一：set() = 请求停止
        raise Interrupted()


def guard_merge_path(path: str, *, directory: bool = False):
    """拒绝根目录、符号链接、junction，防止误删到其它位置。"""
    if not path or not ntpath.isabs(path):
        # Linux CI 的临时绝对路径也能用纯逻辑测试；正式程序仅运行 Windows。
        if os.name == "nt" or not os.path.isabs(path):
            raise MergeDeleteBlocked(f"目标不是绝对路径：{path}")
    full = os.path.normpath(path)
    drive, rest = ntpath.splitdrive(ntpath.normpath(path))
    if drive and rest in ("\\", "/", ""):
        raise MergeDeleteBlocked(f"拒绝删除磁盘根目录或网络共享根目录：{path}")
    if os.path.islink(full) or (
        hasattr(os.path, "isjunction") and os.path.isjunction(full)
    ):
        raise MergeDeleteBlocked(f"目标是链接或目录联接，拒绝删除：{path}")
    try:
        st = os.lstat(full)
    except OSError as error:
        raise MergeDeleteBlocked(f"目标不可访问：{path}，{error}") from error
    # 其他目录 reparse point 也必须拒绝；防止 rmtree 穿过重解析点。
    attrs = getattr(st, "st_file_attributes", 0)
    reparse_bit = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if attrs & reparse_bit:
        raise MergeDeleteBlocked(f"目标是重解析点，拒绝删除：{path}")
    if directory and not os.path.isdir(full):
        raise MergeDeleteBlocked(f"目标文件夹不存在：{path}")
    if not directory and not os.path.isfile(full):
        raise MergeDeleteBlocked(f"目标文件不是普通文件：{path}")
    return st


def guard_merge_ancestor_links(path: str, stop_event: threading.Event):
    """禁止目标路径经由上层目录的 symlink / junction 跳转到别处。

    跳过盘符根目录本身：M:\\ 可以是合法的 CD2 虚拟盘根。
    """
    current = os.path.dirname(os.path.abspath(path))
    while current and os.path.dirname(current) != current:
        check_merge_stop(stop_event)
        if os.path.islink(current) or (
            hasattr(os.path, "isjunction") and os.path.isjunction(current)
        ):
            raise MergeDeleteBlocked(f"目标的上级目录是符号链接/junction：{current}")
        try:
            attrs = getattr(os.lstat(current), "st_file_attributes", 0)
        except OSError as error:
            raise MergeDeleteBlocked(f"无法核查目标的上级目录：{current}，{error}") from error
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if attrs & reparse_flag:
            raise MergeDeleteBlocked(f"目标的上级目录是重解析点：{current}")
        current = os.path.dirname(current)


def check_folder_has_only_selected_media(
    folder: str, allowed_media_paths: list[str], stop_event: threading.Event,
    *, allow_other_media: bool = False,
) -> dict:
    """完整递归检查目录内的媒体，禁止在不知情时整目录删除。

    预检使用 allow_other_media=True：发现其它正片/STRM 时返回其数量，
    自动改为只删除指定文件。文件名含 trailer 的视频/STRM 不算其它媒体，
    但整目录删除仍会一并删除它们；必须在确认框告知用户。
    实际执行整目录删除时使用严格模式。
    所有扫描均在后台线程运行，拒绝目录链接及异常大的目录。
    """
    check_merge_stop(stop_event)
    guard_merge_path(folder, directory=True)
    allowed = {nt_key(path) for path in allowed_media_paths}
    discovered = set()
    file_total = 0
    folder_total = 0
    tree_digest = hashlib.sha256()
    other_media_count = 0
    other_media_example = ""
    trailer_count = 0
    trailer_example = ""
    nfo_count = 0

    def fail_walk(error):
        raise MergeDeleteBlocked(f"不能完整读取待删目录，拒绝删除：{error}")

    for current, directories, names in os.walk(
        folder, topdown=True, followlinks=False, onerror=fail_walk,
    ):
        check_merge_stop(stop_event)
        guard_merge_path(current, directory=True)
        # 遍历顺序稳定，快照包含目录和各文件的元数据，不读取媒体内容。
        directories.sort(key=str.casefold)
        names.sort(key=str.casefold)
        tree_digest.update(("D|" + nt_key(os.path.relpath(current, folder)) + "\n").encode("utf-8"))
        folder_total += 1
        if folder_total > 2000:
            raise MergeDeleteBlocked("待删文件夹下有超过2000个子目录，请人工检查")
        for name in directories:
            check_merge_stop(stop_event)
            child = os.path.join(current, name)
            guard_merge_path(child, directory=True)
        for name in names:
            check_merge_stop(stop_event)
            target = os.path.join(current, name)
            file_total += 1
            if file_total > 100000:
                raise MergeDeleteBlocked("待删文件夹包含超过10万个文件，拒绝整目录删除")
            if os.path.islink(target):
                raise MergeDeleteBlocked(f"文件夹含符号链接，拒绝递归删除：{target}")
            try:
                file_st = os.lstat(target)
                attributes = getattr(file_st, "st_file_attributes", 0)
            except OSError as error:
                raise MergeDeleteBlocked(f"无法核查目录中文件：{target}，{error}") from error
            tree_digest.update((
                f"F|{nt_key(os.path.relpath(target, folder))}|"
                f"{file_st.st_size}|{file_st.st_mtime_ns}|{file_st.st_ino}\n"
            ).encode("utf-8"))
            if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise MergeDeleteBlocked(f"目录中有重解析点文件，拒绝整目录删除：{target}")
            extension = os.path.splitext(name)[1].casefold()
            if extension == ".nfo" and not is_trailer_filename(name):
                nfo_count += 1
            if extension in MEDIA_EXTS:
                key = nt_key(target)
                if key in allowed:
                    discovered.add(key)
                elif (is_trailer_filename(name)
                      or (extension == ".strm"
                          and is_trailer_filename(read_strm(target)))):
                    # 预告片文件或指向预告片的 STRM 不算“其他视频”。
                    # 仅读取本地小 STRM 文本，绝不访问其指向的云端源文件。
                    # 仍纳入文件数量、链接检查及确认框的永久删除提醒。
                    trailer_count += 1
                    if not trailer_example:
                        trailer_example = target
                else:
                    if not allow_other_media:
                        raise MergeDeleteBlocked(f"目录里还有其他 STRM/视频文件：{target}")
                    other_media_count += 1
                    if not other_media_example:
                        other_media_example = target

    missing = allowed - discovered
    if missing:
        raise MergeDeleteBlocked(
            "待删文件夹中未找到目标视频/STRM，禁止删除：" + "；".join(sorted(missing))
        )
    return {
        "files": file_total, "directories": folder_total,
        "tree_fingerprint": tree_digest.hexdigest(),
        "other_media_count": other_media_count,
        "other_media_example": other_media_example,
        "trailer_count": trailer_count,
        "trailer_example": trailer_example,
        "nfo_count": nfo_count,
    }


def choose_cleanup_mode(
    folder: str, media_path: str, scan_roots: list[str],
    stop_event: threading.Event, *, force_file_reason: str = "",
) -> tuple[str, dict | None, str]:
    """只在确认可以安全删除整个目录时返回 folder，否则退回到单文件。

    不删除扫描根目录。权限不足、目录过大或遇到重解析点，均不能
    证明整目录安全，因此只删除明确选中的媒体文件。
    """
    check_merge_stop(stop_event)
    if force_file_reason:
        return "file", None, force_file_reason
    if any(nt_key(folder) == nt_key(root) for root in scan_roots):
        return "file", None, "所在目录就是扫描根目录，必须保留目录"
    try:
        stats = check_folder_has_only_selected_media(
            folder, [media_path], stop_event, allow_other_media=True,
        )
    except MergeDeleteBlocked as error:
        return "file", None, f"无法证明整目录可安全删除：{error}"
    count = stats["other_media_count"]
    if count:
        return (
            "file", stats,
            f"所在目录还有 {count} 个其他视频/STRM，"
            f"例如：{stats['other_media_example']}",
        )
    if stats["trailer_count"]:
        return (
            "folder", stats,
            f"目录内没有其他正片/STRM；{stats['trailer_count']} 个 trailer 预告片"
            "不阻止整目录删除，并会随目录一起永久删除",
        )
    return "folder", stats, "目录内没有其他视频/STRM，可删除整个目录"


def file_fingerprint(st) -> dict:
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "device": st.st_dev, "inode": st.st_ino}


def check_snapshot_matches(path: str, fingerprint: dict):
    st = guard_merge_path(path, directory=False)
    if file_fingerprint(st) != fingerprint:
        raise MergeDeleteBlocked(
            f"扫描后文件已经发生变化，禁止删除，请重新扫描：{path}"
        )


# 分享冲突 WinError 32 / 文件锁定 WinError 33：只针对已确认的单个视频源重试。
# 首次删除失败后稍作等待，让 mpvnet 或 CD2 有机会释放文件句柄。
VIDEO_FILE_LOCK_RETRY_DELAYS = (1.5, 2.0, 2.5)


def remove_video_file_with_retry(
    path: str, fingerprint: dict, stop_event: threading.Event, operation_id: str,
):
    """仅对 WinError 32/33 有限重试，绝不强制结束其他进程。

    每次尝试前重新检查文件快照，防止等待期间文件被替换。
    如果最后仍被占用，原样保留 STRM/目录，不执行后续清理。
    此函数只允许在删除工作线程运行，所有等待均可被 Event 取消。
    """
    for attempt in range(len(VIDEO_FILE_LOCK_RETRY_DELAYS) + 1):
        check_merge_stop(stop_event)
        check_snapshot_matches(path, fingerprint)
        try:
            os.remove(path)
            return
        except OSError as error:
            code = getattr(error, "winerror", None)
            if code not in (32, 33):
                raise
            if attempt == len(VIDEO_FILE_LOCK_RETRY_DELAYS):
                raise MergeDeleteBlocked(
                    f"真实视频仍被播放器、CD2 或其他进程占用（WinError {code}），"
                    f"已尝试 {attempt + 1} 次；未继续执行后续清理步骤。"
                    f"请确认相关播放器已完全退出、CD2 已释放句柄，再重试：{path}"
                ) from error
            pause = VIDEO_FILE_LOCK_RETRY_DELAYS[attempt]
            append_merge_delete_log(
                operation_id, "SOURCE_VIDEO_LOCK_RETRY",
                path=path, winerror=code, attempt=attempt + 1,
                next_retry_delay_seconds=pause,
            )
            if stop_event.wait(pause):
                raise Interrupted()


def build_merge_delete_plan(
    entry: MediaEntry,
    records: list[MediaEntry],
    scan_roots: list[str],
    old_prefix: str,
    mapped_prefix: str,
    stop_event: threading.Event,
    *,
    operation_id: str,
) -> dict:
    """一次性预检所有目标；成功前不修改任何文件。"""
    check_merge_stop(stop_event)
    kind = entry.media_type
    if kind not in ("STRM", "视频"):
        raise MergeDeleteBlocked("只允许合并删除 STRM 或普通视频条目")
    if is_trailer_filename(entry.media_path) or (
        kind == "STRM" and is_trailer_filename(entry.target)
    ):
        raise MergeDeleteBlocked("预告片不作为查重版本，不能单独执行合并删除；请重新扫描")
    if kind == "STRM" and (
        entry.target_kind != "path"
        or os.path.splitext(entry.target)[1].casefold() not in VIDEO_EXTS
    ):
        raise MergeDeleteBlocked("STRM 没有映射到明确的本地视频文件，已拒绝")
    if kind == "视频" and os.path.splitext(entry.media_path)[1].casefold() not in VIDEO_EXTS:
        raise MergeDeleteBlocked("普通视频文件扩展名无效")
    if not any(is_nested_path(entry.folder, root, allow_equal=True) for root in scan_roots):
        raise MergeDeleteBlocked("目标文件不在当前扫描目录内，禁止删除")

    guard_merge_ancestor_links(entry.media_path, stop_event)
    media_st = guard_merge_path(entry.media_path, directory=False)
    guard_merge_path(entry.folder, directory=True)
    if nt_key(os.path.dirname(entry.media_path)) != nt_key(entry.folder):
        raise MergeDeleteBlocked("记录中媒体路径和所在文件夹不一致")

    source_path = entry.target if kind == "STRM" else entry.media_path
    target_folder = os.path.dirname(source_path)
    check_merge_stop(stop_event)

    # 防止 STRM 内容被用户或其他程序修改后仍误删旧扫描结果。
    if kind == "STRM":
        actual_raw = read_strm(entry.media_path)
        actual_target, actual_type = normalize_strm_target(
            entry.media_path, actual_raw, old_prefix, mapped_prefix,
        )
        if actual_type != "path" or nt_key(actual_target) != nt_key(entry.target):
            raise MergeDeleteBlocked(
                f"STRM 指向内容已变化，必须重新扫描后再删除：{entry.media_path}"
            )
        # 源视频与 STRM 刮削目录存在父子关系时，强制只删除两个明确文件。
        # 不因目录关系拦截合法的同目录 STRM，但绝不递归删除其目录。

    # STRM: 仅校验指向的真实视频文件本身，绝不对其父目录进行
    # os.walk/scandir/其它媒体检查，也不删除源视频所在文件夹。
    # 云盘的挂载目录可能本身使用 Windows reparse point；
    # 对 STRM 源目录递归核查祖先会误拦截合法的挂载路径。
    # 刮削路径仍严格检查链接、扫描根及目录内其它媒体。
    video_st = guard_merge_path(source_path, directory=False) if kind == "STRM" else media_st

    # 同一源文件如果被其它已扫描媒体引用，仍要拒绝：
    # 删掉它会破坏其它 STRM。这和源目录有无其它视频是两回事。
    refs = [
        other.media_path for other in records
        if other.identifier != entry.identifier
        and other.target_kind == "path" and nt_key(other.target) == nt_key(source_path)
    ]
    if refs:
        raise MergeDeleteBlocked(
            f"另有 {len(refs)} 条扫描记录引用同一真实视频，禁止删除。例如：{refs[0]}"
        )

    if kind == "STRM":
        # STRM 指向的源视频一律仅删除其文件，不遍历、不清空源视频目录。
        video_folder_stats = None
        overlaps_source = (
            is_nested_path(target_folder, entry.folder, allow_equal=True)
            or is_nested_path(entry.folder, target_folder, allow_equal=True)
        )
        cleanup_mode, scrape_stats, cleanup_reason = choose_cleanup_mode(
            entry.folder, entry.media_path, scan_roots, stop_event,
            force_file_reason=(
                "STRM 与源视频目录有包含关系，仅删除两个明确文件"
                if overlaps_source else ""
            ),
        )
    else:
        cleanup_mode, video_folder_stats, cleanup_reason = choose_cleanup_mode(
            target_folder, source_path, scan_roots, stop_event,
        )
        scrape_stats = None

    return {
        "operation_id": operation_id,
        "identifier": entry.identifier,
        "code": entry.code,
        "media_type": kind,
        "media_path": entry.media_path,
        "source_video": source_path,
        "video_folder": target_folder,
        "scrape_folder": entry.folder if kind == "STRM" else "",
        "cleanup_mode": cleanup_mode,
        "cleanup_reason": cleanup_reason,
        "cleanup_folder": entry.folder if kind == "STRM" else target_folder,
        "media_fingerprint": file_fingerprint(media_st),
        "video_fingerprint": file_fingerprint(video_st),
        "video_folder_contents": video_folder_stats,
        "scrape_folder_contents": scrape_stats,
        "source_folder_enumerated": (
            kind != "STRM" and video_folder_stats is not None
        ),
        "source_folder_deletion": (
            "仅删除指定真实视频文件" if kind == "STRM" else
            ("删除视频所在文件夹" if cleanup_mode == "folder" else "仅删除指定视频文件")
        ),
        "scrape_folder_deletion": (
            ("删除整个刮削文件夹" if cleanup_mode == "folder" else "仅删除指定 STRM 文件")
            if kind == "STRM" else "不适用"
        ),
        "root_directories": list(scan_roots),
    }


def execute_merge_delete(
    plan: dict,
    entry: MediaEntry,
    records: list[MediaEntry],
    scan_roots: list[str],
    old_prefix: str,
    mapped_prefix: str,
    stop_event: threading.Event,
):
    """确认后再次预检。STRM 先删除源视频，再删除选中的 STRM 或目录。

    不遍历、更不删除 STRM 源视频的父目录；存在其它媒体时仅删文件。
    部分失败写入日志，绝不假报成功。
    """
    operation_id = plan["operation_id"]
    # 二次检查：用户确认过程中可能已经有新视频进入文件夹。
    fresh = build_merge_delete_plan(
        entry, records, scan_roots, old_prefix, mapped_prefix,
        stop_event, operation_id=operation_id,
    )
    # 第二次预检时还要核对用户在确认框中看到的*完整删除目标*；
    # 不能仅比文件大小/修改时间，否则同尺寸文件有机会被调换。
    for key in ("media_path", "source_video", "video_folder", "scrape_folder", "cleanup_folder"):
        if nt_key(fresh.get(key, "")) != nt_key(plan.get(key, "")):
            raise MergeDeleteBlocked(
                f"确认期间删除目标路径已变化（{key}），已取消；请重新预检"
            )
    if (fresh["identifier"] != plan["identifier"]
            or fresh["media_type"] != plan["media_type"]):
        raise MergeDeleteBlocked("确认期间媒体记录已变化，已取消")
    if fresh["cleanup_mode"] != plan["cleanup_mode"]:
        raise MergeDeleteBlocked(
            "确认期间目录内视频情况已变化，删除方式改变；已取消，请重新确认"
        )
    if fresh["cleanup_mode"] == "folder":
        # 预告片不是查重视频，但新增预告片仍属于新增的永久删除目标，
        # 因此不能在未经再次确认时悄悄一并删除。
        stats_key = ("scrape_folder_contents" if entry.media_type == "STRM"
                     else "video_folder_contents")
        old_stats = plan.get(stats_key) or {}
        new_stats = fresh.get(stats_key) or {}
        if (old_stats.get("trailer_count") != new_stats.get("trailer_count")
                or old_stats.get("trailer_example") != new_stats.get("trailer_example")):
            raise MergeDeleteBlocked(
                "确认期间预告片文件发生增减，删除范围已变化；请重新预检确认"
            )
    if fresh["video_fingerprint"] != plan["video_fingerprint"] or (
        fresh["media_fingerprint"] != plan["media_fingerprint"]
    ):
        raise MergeDeleteBlocked("预检之后目标文件已变化，操作已取消")
    if plan.get("bulk_source_root"):
        original = plan.get("scrape_folder_contents") or {}
        updated = fresh.get("scrape_folder_contents") or {}
        if original.get("tree_fingerprint") != updated.get("tree_fingerprint"):
            raise MergeDeleteBlocked("STRM 作品目录在批量预览后发生变化，拒绝删除")
    check_merge_stop(stop_event)
    if plan.get("bulk_source_root"):
        # 最终实际删除前再确认保留方，避免预检或限速等待后副本丢失。
        _bulk_ensure_keeper(
            plan, {record.identifier: record for record in records},
            old_prefix, mapped_prefix, stop_event,
        )
    append_merge_delete_log(
        operation_id, "EXECUTE_START", plan=fresh,
        note="开始不可逆操作；在此之前不做任何删除"
    )
    finished_steps = []
    try:
        if entry.media_type == "STRM":
            # 用户指定顺序：先删除真实视频文件，再删除 STRM 或其专属目录。
            remove_video_file_with_retry(
                fresh["source_video"], plan["video_fingerprint"],
                stop_event, operation_id,
            )
            finished_steps.append(f"真实视频单文件：{fresh['source_video']}")
            append_merge_delete_log(
                operation_id, "VIDEO_DELETED",
                path=fresh["source_video"], completed=finished_steps,
            )
            check_merge_stop(stop_event)
            if fresh["cleanup_mode"] == "folder":
                # 真实视频删完后也要保护本地目录：若出现任何新增文件，
                # 不得把它连同原目录一起误删；此时记录为部分完成并停止整批。
                latest_folder = check_folder_has_only_selected_media(
                    fresh["scrape_folder"], [fresh["media_path"]], stop_event,
                )
                if plan.get("bulk_source_root") and (
                    latest_folder.get("tree_fingerprint") !=
                    (plan.get("scrape_folder_contents") or {}).get("tree_fingerprint")
                ):
                    raise MergeDeleteBlocked(
                        "源视频已经删除，但 STRM 目录在操作中发生变化；"
                        "已保留目录，请人工检查审计日志"
                    )
            check_snapshot_matches(fresh["media_path"], plan["media_fingerprint"])
            check_merge_stop(stop_event)
            if fresh["cleanup_mode"] == "folder":
                shutil.rmtree(fresh["scrape_folder"])
                label = f"STRM 刮削目录：{fresh['scrape_folder']}"
                stage = "SCRAPE_FOLDER_DELETED"
                target = fresh["scrape_folder"]
            else:
                os.remove(fresh["media_path"])
                label = f"STRM 单文件：{fresh['media_path']}"
                stage = "STRM_FILE_DELETED"
                target = fresh["media_path"]
            finished_steps.append(label)
            append_merge_delete_log(
                operation_id, stage, path=target, completed=finished_steps,
            )
        else:
            if fresh["cleanup_mode"] == "folder":
                check_folder_has_only_selected_media(
                    fresh["video_folder"], [fresh["media_path"]], stop_event,
                )
            check_snapshot_matches(fresh["media_path"], plan["media_fingerprint"])
            check_merge_stop(stop_event)
            if fresh["cleanup_mode"] == "folder":
                shutil.rmtree(fresh["video_folder"])
                label = f"视频所在文件夹：{fresh['video_folder']}"
                stage = "VIDEO_FOLDER_DELETED"
                target = fresh["video_folder"]
            else:
                remove_video_file_with_retry(
                    fresh["media_path"], plan["media_fingerprint"],
                    stop_event, operation_id,
                )
                label = f"视频单文件：{fresh['media_path']}"
                stage = "VIDEO_FILE_DELETED"
                target = fresh["media_path"]
            finished_steps.append(label)
            append_merge_delete_log(
                operation_id, stage, path=target, completed=finished_steps,
            )
        # 删除已完成后将路径从视频技术参数缓存移除，避免显示过期信息。
        try:
            index = MediaIndex(program_data_dir() / "video_index.sqlite3")
            try:
                index.forget([fresh["source_video"]])
            finally:
                index.close()
        except (OSError, sqlite3.Error) as error:
            append_merge_delete_log(
                operation_id, "CACHE_INVALIDATION_FAILED", reason=str(error),
            )
        append_merge_delete_log(
            operation_id, "COMPLETE", completed=finished_steps,
        )
        return finished_steps
    except (Interrupted, OSError, ValueError, MergeDeleteBlocked) as error:
        stage = "PARTIAL" if finished_steps else "FAILED"
        append_merge_delete_log(
            operation_id, stage, completed=finished_steps,
            error=str(error),
        )
        message = (f"删除未完成：{error}；已完成步骤："
                   + ("；".join(finished_steps) if finished_steps else "无"))
        raise MergeDeletePartial(message, finished_steps) from error


def _bulk_candidates(group: list[MediaEntry], root: str) -> list[MediaEntry]:
    """仅选择指定根下的 STRM；整个目录必须严格位于根目录之下。"""
    return [
        entry for entry in group
        if entry.media_type == "STRM"
        and ntpath.splitext(entry.media_path)[1].casefold() == ".strm"
        and is_nested_path(entry.folder, root)
        and is_nested_path(entry.media_path, root)
        and nt_key(ntpath.dirname(entry.media_path)) == nt_key(entry.folder)
    ]


def _bulk_keepers(group: list[MediaEntry], root: str) -> list[MediaEntry]:
    """另一份必须在指定保留根内，且有独立的本地 STRM/视频文件。"""
    return [
        entry for entry in group
        if entry.media_type in ("STRM", "视频")
        and entry.target_kind in ("path", "url")
        and bool(entry.target)
        and is_nested_path(entry.media_path, root)
        and is_nested_path(entry.folder, root)
    ]


def _verify_bulk_keeper(
    entry: MediaEntry, source_video: str, from_prefix: str,
    to_prefix: str, stop_event: threading.Event,
) -> dict:
    """只触碰保留方的本地 STRM/媒体文件，不打开其指向的云盘视频。"""
    check_merge_stop(stop_event)
    st = guard_merge_path(entry.media_path, directory=False)
    if entry.media_type == "STRM":
        raw = read_strm(entry.media_path, strict=True)
        actual, kind = normalize_strm_target(entry.media_path, raw, from_prefix, to_prefix)
        if (kind != entry.target_kind
                or target_identity(actual, kind) != target_identity(entry.target, entry.target_kind)):
            raise MergeDeleteBlocked(f"保留方 STRM 内容已变化：{entry.media_path}")
    if (entry.target_kind == "path"
            and nt_key(entry.target) == nt_key(source_video)):
        raise MergeDeleteBlocked("保留方和待删 STRM 指向同一源视频，禁止删除源文件")
    return {
        "identifier": entry.identifier,
        "media_path": entry.media_path,
        "fingerprint": file_fingerprint(st),
    }


def _bulk_ensure_keeper(
    plan: dict, record_by_id: dict[int, MediaEntry],
    from_prefix: str, to_prefix: str, stop_event: threading.Event,
):
    """执行时至少仍有一份与预览完全相同的保留方文件。"""
    for snapshot in plan["bulk_keepers"]:
        check_merge_stop(stop_event)
        keeper = record_by_id.get(snapshot["identifier"])
        if keeper is None or nt_key(keeper.media_path) != nt_key(snapshot["media_path"]):
            continue
        try:
            current = _verify_bulk_keeper(
                keeper, plan["source_video"], from_prefix, to_prefix, stop_event,
            )
        except (MergeDeleteBlocked, OSError, ValueError):
            continue
        if current["fingerprint"] == snapshot["fingerprint"]:
            return
    raise MergeDeleteBlocked("全部保留方副本已缺失或发生变化，禁止删除源视频")


def _bulk_guard_source(entry: MediaEntry, root: str, retain_root: str):
    if entry.target_kind != "path" or not entry.target:
        raise MergeDeleteBlocked("STRM 不是已映射的本地视频路径，不能批量删源")
    if ntpath.splitext(entry.target)[1].casefold() not in VIDEO_EXTS:
        raise MergeDeleteBlocked("STRM 指向内容不是已识别的视频扩展名")
    if os.name == "nt" and not (
        WINDOWS_DRIVE.match(entry.target) or entry.target.startswith("\\\\")
    ):
        raise MergeDeleteBlocked("视频源不是 Windows 盘符或 UNC 绝对路径")
    if (is_nested_path(entry.target, root, allow_equal=True)
            or is_nested_path(entry.target, retain_root, allow_equal=True)):
        raise MergeDeleteBlocked("视频源位于待删 STRM 根目录或保留目录内，拒绝操作")



def validate_bulk_roots(
    source_root: str, keep_root: str, scan_roots: list[str],
) -> tuple[str, str]:
    """校验用户从本次扫描目录中选择的待删端和保留端；只做字符串检查。"""
    if not source_root or not keep_root:
        raise MergeDeleteBlocked("必须分别选择待删扫描目录和保留扫描目录")
    source_root = ntpath.normpath(str(source_root))
    keep_root = ntpath.normpath(str(keep_root))
    for label, root in (("待删", source_root), ("保留", keep_root)):
        if not ntpath.isabs(root):
            raise MergeDeleteBlocked(f"{label}目录不是 Windows 绝对路径：{root}")
        if nt_key(root) not in {nt_key(item) for item in scan_roots}:
            raise MergeDeleteBlocked(f"{label}目录不在本次扫描目录列表中：{root}")
        if ntpath.dirname(root) == root:
            raise MergeDeleteBlocked(f"不能把磁盘根目录或网络共享根目录作为批量清理目标：{root}")
    if (is_nested_path(source_root, keep_root, allow_equal=True)
            or is_nested_path(keep_root, source_root, allow_equal=True)):
        raise MergeDeleteBlocked("待删目录和保留目录不能相同，也不能互相包含")
    return source_root, keep_root


def format_bulk_preview(batch: dict) -> str:
    lines = [
        "大锤 NFO / STRM 批量清理 · 删除前预览（并非执行结果）",
        "=" * 74,
        f"生成时间：{datetime.datetime.now().astimezone().isoformat()}",
        f"扫描重复组总数：{batch['all_groups']}",
        f"涉及指定根目录的重复组：{batch['matched_groups']}",
        f"准备删除作品目录：{len(batch['plans'])}",
        f"被跳过的条目：{len(batch['skipped'])}",
        f"只允许删除 STRM 所在目录：{batch['source_root']}",
        f"必须保留匹配的副本：{batch['keep_root']}",
        f"源视频删除间隔：{BULK_DELETE_DELAY_SECONDS:.0f} 秒（两次删除之间）",
        "注意：CD2 挂载删除可能直接删除 115 网盘原文件，不进 Windows 回收站。",
        "注意：只检查当前已扫描的 STRM 引用；扫描范围外的引用无法自动排除。",
        "",
        "一、将执行的操作（每条：先删除真实视频文件，再删除整个 STRM 作品目录）",
        "-" * 74,
    ]
    for index, plan in enumerate(batch["plans"], 1):
        lines.extend([
            f"[{index}] 番号：{plan['code']}",
            f"    要删除的作品目录：{plan['scrape_folder']}",
            f"    要删除的 115/CD2 视频：{plan['source_video']}",
            f"    保留的 STRM/视频：{plan['bulk_keepers'][0]['media_path']}",
            f"    目录内文件数（含图片/NFO等）：{plan['scrape_folder_contents']['files']}",
            "",
        ])
    lines.extend(["", "二、跳过（不会删除）", "-" * 74])
    for index, item in enumerate(batch["skipped"], 1):
        lines.append(
            f"[{index}] {item['code']} | {item['media_path']}\n"
            f"    原因：{item['reason']}"
        )
    if not batch["plans"]:
        lines.append("没有任何符合安全条件的删除项。")
    return "\n".join(lines) + "\n"


def _save_bulk_preview(operation_id: str, content: str) -> str:
    path = program_data_dir() / f"bulk_preview_{operation_id}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    return str(path)


def precheck_bulk_cleanup(
    duplicates: dict[str, list[MediaEntry]], records: list[MediaEntry],
    scan_roots: list[str], from_prefix: str, to_prefix: str,
    stop_event: threading.Event, notify: Callable[[int, int, str], None],
    *, operation_id: str, source_root: str, keep_root: str,
) -> dict:
    """后台全量预检；不删任何数据，逐项安全拒绝并生成完整清单。"""
    source_root, keep_root = validate_bulk_roots(source_root, keep_root, scan_roots)
    append_merge_delete_log(
        operation_id, "BULK_PRECHECK_START", source_root=source_root,
        keep_root=keep_root, all_groups=len(duplicates),
    )
    folder_occurrences: dict[str, int] = defaultdict(int)
    candidates: list[tuple[str, MediaEntry, list[MediaEntry]]] = []
    matched_groups = 0
    for code, group in sorted(duplicates.items(), key=lambda x: number_sort_key(x[0])):
        if stop_event.is_set():
            raise Interrupted()
        targets = _bulk_candidates(group, source_root)
        if targets:
            matched_groups += 1
        for entry in targets:
            folder_occurrences[nt_key(entry.folder)] += 1
            candidates.append((code, entry, group))

    plans = []
    skipped = []
    for index, (code, entry, group) in enumerate(candidates, 1):
        check_merge_stop(stop_event)
        try:
            if folder_occurrences[nt_key(entry.folder)] != 1:
                raise MergeDeleteBlocked("同一作品目录匹配了多个 STRM，批量模式跳过整目录")
            keepers = _bulk_keepers(group, keep_root)
            if not keepers:
                raise MergeDeleteBlocked("未找到指定保留目录内的另一份 STRM/视频，不能确认有副本")
            _bulk_guard_source(entry, source_root, keep_root)
            safe_keepers = []
            for keeper in keepers:
                check_merge_stop(stop_event)
                try:
                    safe_keepers.append(_verify_bulk_keeper(
                        keeper, entry.target, from_prefix, to_prefix, stop_event,
                    ))
                except (OSError, ValueError, MergeDeleteBlocked):
                    continue
            if not safe_keepers:
                raise MergeDeleteBlocked("保留方文件缺失、改变或与待删项共用同一视频源")
            plan = build_merge_delete_plan(
                entry, records, scan_roots, from_prefix, to_prefix,
                stop_event, operation_id=operation_id,
            )
            if plan["cleanup_mode"] != "folder":
                raise MergeDeleteBlocked(
                    "目录不适合整删（有其他媒体/共享目录/权限问题）："
                    + plan["cleanup_reason"]
                )
            if nt_key(plan["cleanup_folder"]) != nt_key(entry.folder):
                raise MergeDeleteBlocked("预检得到的整删目录与扫描目录不一致")
            plan["bulk_keepers"] = safe_keepers
            plan["bulk_source_root"] = source_root
            plan["bulk_keep_root"] = keep_root
            plans.append(plan)
        except (MergeDeleteBlocked, OSError, ValueError) as error:
            skipped.append({
                "code": code, "media_path": entry.media_path,
                "reason": str(error),
            })
        if index % 5 == 0 or index == len(candidates):
            notify(index, max(len(candidates), 1),
                   f"批量预检 {index}/{len(candidates)}，可删 {len(plans)}，跳过 {len(skipped)}")
    batch = {
        "operation_id": operation_id,
        "source_root": source_root, "keep_root": keep_root,
        "all_groups": len(duplicates), "matched_groups": matched_groups,
        "plans": plans, "skipped": skipped,
    }
    check_merge_stop(stop_event)
    preview = format_bulk_preview(batch)
    batch["preview_path"] = _save_bulk_preview(operation_id, preview)
    check_merge_stop(stop_event)
    append_merge_delete_log(
        operation_id, "BULK_PRECHECK_DONE",
        ready=[{"code": p["code"], "folder": p["scrape_folder"],
                "source": p["source_video"]} for p in plans],
        skipped=skipped, preview_path=batch["preview_path"],
    )
    return batch


def execute_bulk_cleanup(
    batch: dict, records: list[MediaEntry], scan_roots: list[str],
    from_prefix: str, to_prefix: str,
    stop_event: threading.Event, notify: Callable[[int, int, str], None],
    *, delete_interval_seconds: float = BULK_DELETE_DELAY_SECONDS,
) -> dict:
    """逐一删除 CD2 源视频与 STRM 文件夹；三秒间隔，Event 可中断。"""
    operation_id = batch["operation_id"]
    plans = list(batch["plans"])
    by_id = {entry.identifier: entry for entry in records}
    outcome = {
        "deleted": [], "skipped_runtime": [], "error": "", "stopped": False,
        "total_plans": len(plans), "preview_path": batch["preview_path"],
        "log_path": str(merge_delete_log_path()),
    }
    # 冻结整批删除目标：不允许同一目录或同一真实视频重复入队。
    folder_keys = [nt_key(plan["scrape_folder"]) for plan in plans]
    video_keys = [nt_key(plan["source_video"]) for plan in plans]
    if len(folder_keys) != len(set(folder_keys)) or len(video_keys) != len(set(video_keys)):
        raise MergeDeleteBlocked("批量清单有重复的视频源或目标文件夹，已整批拦截")
    source_root, keep_root = validate_bulk_roots(
        batch["source_root"], batch["keep_root"], scan_roots,
    )
    # 预览时选定的范围在执行阶段保持不变；逐条核对已确认的计划。
    for plan in plans:
        if (nt_key(plan.get("bulk_source_root", "")) != nt_key(source_root)
                or nt_key(plan.get("bulk_keep_root", "")) != nt_key(keep_root)
                or not is_nested_path(plan.get("scrape_folder", ""), source_root)
                or not is_nested_path(plan.get("media_path", ""), source_root)
                or not plan.get("bulk_keepers")
                or not all(is_nested_path(snapshot.get("media_path", ""), keep_root)
                           for snapshot in plan["bulk_keepers"])):
            raise MergeDeleteBlocked("预览清单的目录范围与所选扫描目录不一致，已整批拦截")
    append_merge_delete_log(
        operation_id, "BULK_EXECUTE_START", planned=len(plans),
        source_root=source_root, keep_root=keep_root,
        delay_seconds=delete_interval_seconds, preview_path=batch["preview_path"],
    )
    last_attempt_finished = None
    for index, plan in enumerate(plans, 1):
        if stop_event.is_set():
            outcome["stopped"] = True
            break
        entry = by_id.get(plan["identifier"])
        if (entry is None or entry.media_type != "STRM"
                or nt_key(entry.media_path) != nt_key(plan["media_path"])
                or not is_nested_path(plan["scrape_folder"], source_root)):
            outcome["skipped_runtime"].append({
                "code": plan["code"], "reason": "记录/目标路径在预检后发生变化"
            })
            continue
        try:
            _bulk_guard_source(entry, source_root, keep_root)
            _bulk_ensure_keeper(plan, by_id, from_prefix, to_prefix, stop_event)
            # 与确认预览对比整个文件夹的快照，而非只比较 STRM 文件。
            current = check_folder_has_only_selected_media(
                plan["scrape_folder"], [plan["media_path"]], stop_event,
            )
            prior = plan.get("scrape_folder_contents") or {}
            if current.get("tree_fingerprint") != prior.get("tree_fingerprint"):
                raise MergeDeleteBlocked("作品目录内文件在预览后发生变化，请重新扫描")
        except Interrupted:
            outcome["stopped"] = True
            break
        except (MergeDeleteBlocked, OSError, ValueError) as error:
            skipped = {"code": plan["code"], "reason": str(error)}
            outcome["skipped_runtime"].append(skipped)
            append_merge_delete_log(operation_id, "BULK_ITEM_SKIPPED", **skipped)
            notify(index, len(plans), f"跳过 {plan['code']}：{error}")
            continue
        if last_attempt_finished is not None:
            remaining = delete_interval_seconds - (time.monotonic() - last_attempt_finished)
            if remaining > 0:
                notify(index - 1, len(plans),
                       f"115/CD2 限速等待 {remaining:.1f} 秒，下一条 {plan['code']}")
                if stop_event.wait(remaining):
                    outcome["stopped"] = True
                    break
        if stop_event.is_set():
            outcome["stopped"] = True
            break
        # 限速等待时可能出现新增文件或保留方变化，再做最后一次本地核查。
        try:
            _bulk_ensure_keeper(plan, by_id, from_prefix, to_prefix, stop_event)
            latest = check_folder_has_only_selected_media(
                plan["scrape_folder"], [plan["media_path"]], stop_event,
            )
            if latest.get("tree_fingerprint") != (
                (plan.get("scrape_folder_contents") or {}).get("tree_fingerprint")
            ):
                raise MergeDeleteBlocked("限速等待期间作品目录发生变化，已跳过")
        except Interrupted:
            outcome["stopped"] = True
            break
        except (MergeDeleteBlocked, OSError, ValueError) as error:
            skipped = {"code": plan["code"], "reason": str(error)}
            outcome["skipped_runtime"].append(skipped)
            append_merge_delete_log(operation_id, "BULK_ITEM_SKIPPED", **skipped)
            notify(index, len(plans), f"跳过 {plan['code']}：{error}")
            continue
        notify(index - 1, len(plans), f"正在删除 {index}/{len(plans)}：{plan['code']}")
        try:
            # 此函数再做一次完整预检，包括引用、STRM 内容和文件指纹，
            # 删除顺序为真实视频单文件 -> 本地专属 STRM 目录。
            completed = execute_merge_delete(
                plan, entry, records, scan_roots, from_prefix, to_prefix, stop_event,
            )
            last_attempt_finished = time.monotonic()
            done = {"code": plan["code"], "source_video": plan["source_video"],
                    "scrape_folder": plan["scrape_folder"], "steps": completed,
                    "nfo_deleted": ((plan.get("scrape_folder_contents") or {}).get("nfo_count", 0)
                                    if any(step.startswith("STRM 刮削目录：") for step in completed)
                                    else 0)}
            outcome["deleted"].append(done)
            append_merge_delete_log(
                operation_id, "BULK_ITEM_DONE", code=plan["code"],
                source_video=plan["source_video"], scrape_folder=plan["scrape_folder"],
            )
        except (MergeDeleteBlocked, OSError, ValueError) as error:
            last_attempt_finished = time.monotonic()
            # 出现任何真实视频删除失败或部分完成，都立即停下整批，
            # 避免继续请求 115 并掩盖已发生的不可逆变化。
            outcome["error"] = f"{plan['code']}：{error}"
            outcome["partial_steps"] = (
                error.completed_steps if isinstance(error, MergeDeletePartial) else []
            )
            outcome["partial_nfo_deleted"] = (
                (plan.get("scrape_folder_contents") or {}).get("nfo_count", 0)
                if any(step.startswith("STRM 刮削目录：")
                       for step in outcome["partial_steps"]) else 0
            )
            append_merge_delete_log(
                operation_id, "BULK_ABORTED", code=plan["code"],
                error=outcome["error"],
                partial_steps=(error.completed_steps
                               if isinstance(error, MergeDeletePartial) else []),
            )
            break
        notify(index, len(plans), f"已清理 {len(outcome['deleted'])}/{len(plans)}：{plan['code']}")
    if stop_event.is_set():
        outcome["stopped"] = True
    append_merge_delete_log(
        operation_id,
        "BULK_STOPPED" if outcome["stopped"] else
        ("BULK_INCOMPLETE" if outcome["error"] else "BULK_FINISHED"),
        deleted=outcome["deleted"], skipped_runtime=outcome["skipped_runtime"],
        error=outcome["error"], planned=len(plans),
    )
    return outcome


# 批量移动覆盖：源/目标均由用户选择的扫描根目录限定。
# 以重复番号匹配整套作品文件夹；整个目标作品目录由源目录替换。
# STRM 只是文本指针，绝不删除或访问其指向的 115/CD2 真实视频。
def validate_move_roots(source_root: str, destination_root: str,
                        scanned_roots: list[str]) -> tuple[str, str]:
    """源、目标只能选择上次扫描的两个独立根目录，不允许互相包含。"""
    known = {nt_key(root) for root in scanned_roots}
    choices = []
    for label, value in (("源", source_root), ("目标", destination_root)):
        root = os.path.normpath(str(value).strip())
        if not root or not (ntpath.isabs(root) or (os.name != "nt" and os.path.isabs(root))):
            raise MergeDeleteBlocked(f"请选择有效的{label}扫描目录")
        if nt_key(root) not in known:
            raise MergeDeleteBlocked(f"{label}目录不在最近一次扫描的根目录列表中：{root}")
        if nt_key(root) == nt_key(ntpath.dirname(root)):
            raise MergeDeleteBlocked(f"不能将盘符或共享根目录用作{label}目录：{root}")
        choices.append(root)
    source, destination = choices
    if (is_nested_path(source, destination, allow_equal=True)
            or is_nested_path(destination, source, allow_equal=True)):
        raise MergeDeleteBlocked("源扫描目录和目标扫描目录不能相同或互相包含")
    return source, destination


def _move_folder_snapshot(folder: str) -> dict:
    """记录完整文件树的路径及元数据；拒绝链接/junction，不打开视频内容。"""
    guard_merge_path(folder, directory=True)
    digest = hashlib.sha256()
    counts = {"files": 0, "directories": 0, "nfo_count": 0,
              "strm_count": 0, "video_count": 0, "bytes": 0}

    def fail_walk(error):
        raise MergeDeleteBlocked(f"无法完整访问作品目录：{error}")

    for current, directories, names in os.walk(
        folder, topdown=True, followlinks=False, onerror=fail_walk,
    ):
        guard_merge_path(current, directory=True)
        directories.sort(key=str.casefold)
        names.sort(key=str.casefold)
        relative = os.path.relpath(current, folder)
        digest.update(("D|" + nt_key(relative) + "\n").encode("utf-8"))
        counts["directories"] += 1
        if counts["directories"] > 2000:
            raise MergeDeleteBlocked("作品文件夹超过 2000 个子目录，拒绝批量覆盖")
        for name in directories:
            guard_merge_path(os.path.join(current, name), directory=True)
        for name in names:
            path = os.path.join(current, name)
            st = guard_merge_path(path, directory=False)
            counts["files"] += 1
            if counts["files"] > 100000:
                raise MergeDeleteBlocked("作品文件夹超过 10 万个文件，拒绝批量覆盖")
            counts["bytes"] += st.st_size
            relative = os.path.relpath(path, folder)
            digest.update((
                f"F|{nt_key(relative)}|{st.st_size}|{st.st_mtime_ns}|"
                f"{st.st_dev}|{st.st_ino}\n"
            ).encode("utf-8"))
            ext = os.path.splitext(name)[1].casefold()
            if ext == ".nfo" and not is_trailer_filename(name):
                counts["nfo_count"] += 1
            if ext == ".strm" and not is_trailer_filename(name):
                counts["strm_count"] += 1
            if ext in VIDEO_EXTS and not is_trailer_filename(name):
                counts["video_count"] += 1
    counts["fingerprint"] = digest.hexdigest()
    return counts


def _folder_code_index(records: list[MediaEntry], roots: tuple[str, str]):
    """线性建立每个作品目录含有哪些番号；防止误移动含其他作品的目录。"""
    index = defaultdict(set)
    folder_rows = defaultdict(list)
    for entry in records:
        if not entry.media_path:
            continue
        relevant_root = next((root for root in roots
                              if is_nested_path(entry.folder, root)), None)
        if relevant_root is None:
            continue
        key = duplicate_number_key(entry.code) if entry.code else "(未识别)"
        parent = entry.folder
        while is_nested_path(parent, relevant_root):
            folder_key = nt_key(parent)
            index[folder_key].add(key)
            folder_rows[folder_key].append(entry)
            ancestor = ntpath.dirname(parent)
            if nt_key(ancestor) == nt_key(parent):
                break
            parent = ancestor
    return index, folder_rows


def _move_source_relative_strm_safe(records: list[MediaEntry], folder: str) -> None:
    """相对 STRM 若指向目录外，移动后可能失效，宁可跳过。"""
    for entry in records:
        if (entry.media_type != "STRM"
                or not is_nested_path(entry.media_path, folder)):
            continue
        raw = entry.raw_target.strip().strip('"').strip("'")
        if not raw:
            continue
        is_absolute = (ANY_URI.match(raw) or WINDOWS_DRIVE.match(raw)
                       or raw.startswith(("/", "\\")))
        if not is_absolute and not is_nested_path(entry.target, folder):
            raise MergeDeleteBlocked(
                f"存在指向文件夹外的相对 STRM，移动后可能失效：{entry.media_path}"
            )


def precheck_bulk_move(
    duplicates: dict[str, list[MediaEntry]], records: list[MediaEntry],
    roots: list[str], from_prefix: str, to_prefix: str,
    stop_event: threading.Event, notify: Callable[[int, int, str], None],
    *, operation_id: str, source_root: str, destination_root: str,
) -> dict:
    """后台预检：指定两侧根目录，按重复番号唯一匹配作品文件夹。"""
    source_root, destination_root = validate_move_roots(
        source_root, destination_root, roots,
    )
    append_merge_delete_log(
        operation_id, "MOVE_FOLDER_PRECHECK_START", source_root=source_root,
        destination_root=destination_root, groups=len(duplicates),
    )
    code_index, folder_rows = _folder_code_index(records, (source_root, destination_root))
    groups = sorted(duplicates.items(), key=lambda pair: number_sort_key(pair[0]))
    plans, skipped = [], []
    matched = 0
    for index, (code, group) in enumerate(groups, 1):
        check_merge_stop(stop_event)
        source_rows = [e for e in group if e.media_type in ("STRM", "视频")
                       and is_nested_path(e.folder, source_root)]
        dest_rows = [e for e in group if e.media_type in ("STRM", "视频")
                     and is_nested_path(e.folder, destination_root)]
        if not source_rows or not dest_rows:
            if index % 50 == 0 or index == len(groups):
                notify(index, max(1, len(groups)),
                       f"作品文件夹预检 {index}/{len(groups)}；可选 {len(plans)}，跳过 {len(skipped)}")
            continue
        matched += 1
        try:
            source_folders = {nt_key(e.folder): e.folder for e in source_rows}
            dest_folders = {nt_key(e.folder): e.folder for e in dest_rows}
            if len(source_folders) != 1:
                raise MergeDeleteBlocked("源侧该番号存在多个作品文件夹，无法唯一确定来源")
            if len(dest_folders) != 1:
                raise MergeDeleteBlocked("目标侧该番号存在多个作品文件夹，无法唯一确定覆盖位置")
            source_folder = next(iter(source_folders.values()))
            dest_folder = next(iter(dest_folders.values()))
            code_key = duplicate_number_key(code)
            if code_index[nt_key(source_folder)] != {code_key}:
                raise MergeDeleteBlocked("源作品目录包含其他或无法识别的番号，不能整目录移动")
            if code_index[nt_key(dest_folder)] != {code_key}:
                raise MergeDeleteBlocked("目标作品目录包含其他或无法识别的番号，不能整目录覆盖")
            if is_nested_path(source_folder, dest_folder, allow_equal=True) or (
                is_nested_path(dest_folder, source_folder, allow_equal=True)
            ):
                raise MergeDeleteBlocked("源和目标作品目录有包含关系")
            guard_merge_ancestor_links(source_folder, stop_event)
            guard_merge_ancestor_links(dest_folder, stop_event)
            _move_source_relative_strm_safe(
                folder_rows[nt_key(source_folder)], source_folder,
            )
            source_snapshot = _move_folder_snapshot(source_folder)
            dest_snapshot = _move_folder_snapshot(dest_folder)
            if not source_snapshot["files"] or not dest_snapshot["files"]:
                raise MergeDeleteBlocked("源或目标作品文件夹为空，拒绝自动覆盖")
            plans.append({
                "code": code, "source_folder": source_folder,
                "destination_folder": dest_folder,
                "source_snapshot": source_snapshot,
                "destination_snapshot": dest_snapshot,
            })
        except (OSError, ValueError, MergeDeleteBlocked) as error:
            skipped.append({"code": code, "reason": str(error)})
        if index % 50 == 0 or index == len(groups):
            notify(index, max(1, len(groups)),
                   f"作品文件夹预检 {index}/{len(groups)}；可选 {len(plans)}，跳过 {len(skipped)}")
    # 一个作品目录在整批中只能作为一次源或目标；冲突组全部跳过。
    usage = defaultdict(int)
    for plan in plans:
        usage[nt_key(plan["source_folder"])] += 1
        usage[nt_key(plan["destination_folder"])] += 1
    valid = []
    for plan in plans:
        if any(usage[nt_key(plan[field])] != 1
               for field in ("source_folder", "destination_folder")):
            skipped.append({"code": plan["code"],
                            "reason": "作品文件夹被多个重复组引用，无法一对一覆盖"})
        else:
            valid.append(plan)
    batch = {"operation_id": operation_id, "source_root": source_root,
             "destination_root": destination_root, "group_count": len(groups),
             "matched_groups": matched, "plans": valid, "skipped": skipped}
    preview = format_bulk_move_preview(batch)
    path = program_data_dir() / f"move_preview_{operation_id}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8", newline="\n") as stream:
        stream.write(preview)
        stream.flush()
        os.fsync(stream.fileno())
    batch["preview_path"] = str(path)
    append_merge_delete_log(
        operation_id, "MOVE_FOLDER_PRECHECK_DONE",
        source_root=source_root, destination_root=destination_root,
        matched_groups=matched, ready=len(valid), skipped=len(skipped),
        preview_path=str(path),
    )
    return batch


def format_bulk_move_preview(batch: dict) -> str:
    lines = [
        "批量移动覆盖 · 作品文件夹预览（未执行）", "=" * 74,
        f"源扫描目录：{batch['source_root']}",
        f"目标扫描目录：{batch['destination_root']}",
        f"重复组 {batch['group_count']}，两侧匹配 {batch['matched_groups']}，",
        f"可选作品文件夹 {len(batch['plans'])}，跳过 {len(batch['skipped'])}。",
        "操作：将源作品文件夹移动到目标作品文件夹原来的完整路径。",
        "覆盖含义：整个目标文件夹原有内容都会被源文件夹内容替换，",
        "包括目标原有 NFO、封面、字幕及 STRM；不是合并文件，也不是只覆盖 STRM。",
        "源文件夹中的所有文件和子目录随文件夹一起移动。",
        "只操作扫描根目录内的本地作品文件夹；不碰 STRM 指向的 115/CD2 云端原视频。",
        "执行时旧目标目录暂存为备份，异常则尽量回滚；成功后清理备份。", "",
        "一、可执行的匹配作品文件夹", "-" * 74,
    ]
    for number, plan in enumerate(batch["plans"], 1):
        src = plan["source_snapshot"]
        dst = plan["destination_snapshot"]
        lines.extend([
            f"[{number}] {plan['code']}",
            f"  源目录：{plan['source_folder']}",
            f"  替换目标：{plan['destination_folder']}",
            f"  源文件：{src['files']} 个，源 NFO：{src['nfo_count']} 个",
            f"  目标旧文件：{dst['files']} 个，目标旧 NFO：{dst['nfo_count']} 个",
            "  目标旧目录中的全部内容都将被替换（并非按同名文件合并）。", "",
        ])
    lines.extend(["二、跳过项目", "-" * 74])
    for item in batch["skipped"]:
        lines.append(f"{item['code']} | {item['reason']}")
    return "\n".join(lines) + "\n"


def _verify_move_folder_plan(plan: dict, source_root: str, destination_root: str,
                             stop_event: threading.Event):
    source = plan["source_folder"]
    destination = plan["destination_folder"]
    check_merge_stop(stop_event)
    if not is_nested_path(source, source_root) or not is_nested_path(destination, destination_root):
        raise MergeDeleteBlocked("作品文件夹不在已选定的源/目标扫描根目录下")
    if is_nested_path(source, destination, allow_equal=True) or (
        is_nested_path(destination, source, allow_equal=True)
    ):
        raise MergeDeleteBlocked("源/目标作品文件夹有包含关系，拒绝覆盖")
    guard_merge_ancestor_links(source, stop_event)
    guard_merge_ancestor_links(destination, stop_event)
    for label, folder, snapshot_key in (
        ("源", source, "source_snapshot"),
        ("目标", destination, "destination_snapshot"),
    ):
        current = _move_folder_snapshot(folder)
        if current["fingerprint"] != plan[snapshot_key]["fingerprint"]:
            raise MergeDeleteBlocked(f"{label}作品文件夹在预览后发生变化，跳过并重新预检：{folder}")


class MoveReplacePartial(MergeDeleteBlocked):
    """目录交换发生变更但未彻底完成；必须停止整批并同步受影响目录。"""

    def __init__(self, message: str, *, changed: bool = False):
        super().__init__(message)
        self.changed = changed


def _copy_move_tree(source: str, stage: str, stop_event: threading.Event):
    """跨卷复制到目标所在卷临时目录，检查每一个子目录和文件的链接状态。"""
    os.mkdir(stage)
    def fail_walk(error):
        raise MergeDeleteBlocked(f"复制时无法完整读取来源：{error}")
    for current, directories, names in os.walk(
        source, topdown=True, followlinks=False, onerror=fail_walk,
    ):
        check_merge_stop(stop_event)
        guard_merge_path(current, directory=True)
        relative = os.path.relpath(current, source)
        new_parent = stage if relative == "." else os.path.join(stage, relative)
        os.makedirs(new_parent, exist_ok=True)
        for name in directories:
            check_merge_stop(stop_event)
            guard_merge_path(os.path.join(current, name), directory=True)
        for name in names:
            check_merge_stop(stop_event)
            current_file = os.path.join(current, name)
            guard_merge_path(current_file, directory=False)
            shutil.copy2(current_file, os.path.join(new_parent, name))


def _move_replace_folder(plan: dict, stop_event: threading.Event,
                         operation_id: str) -> dict:
    """目标先更名为同级备份；同卷更名，跨卷先复制后切换；失败尽可能恢复。"""
    source = plan["source_folder"]
    destination = plan["destination_folder"]
    parent = os.path.dirname(destination)
    token = secrets.token_hex(7)
    backup = os.path.join(parent, f".cg_backup_{token}")
    stage = os.path.join(parent, f".cg_stage_{token}")
    if os.path.lexists(backup) or os.path.lexists(stage):
        raise MergeDeleteBlocked("临时备份/暂存目录发生冲突，已取消")
    check_merge_stop(stop_event)
    source_stat = guard_merge_path(source, directory=True)
    destination_stat = guard_merge_path(destination, directory=True)
    source_drive, dest_drive = ntpath.splitdrive(source)[0], ntpath.splitdrive(destination)[0]
    cross_volume = (source_stat.st_dev != destination_stat.st_dev
                    or (source_drive and dest_drive and
                        source_drive.casefold() != dest_drive.casefold()))
    old_renamed = False
    promoted = False
    source_removed = False
    try:
        if cross_volume:
            _copy_move_tree(source, stage, stop_event)
        # 复制可能需要较长时间，真正替换前再次确认两边文件树没有变化。
        _verify_move_folder_plan(plan, plan["source_root"],
                                 plan["destination_root"], stop_event)
        check_merge_stop(stop_event)
        append_merge_delete_log(operation_id, "MOVE_FOLDER_SWAP_START",
                                source=source, destination=destination,
                                backup=backup, staging=stage if cross_volume else "",
                                cross_volume=cross_volume)
        os.rename(destination, backup)
        old_renamed = True
        try:
            os.rename(stage if cross_volume else source, destination)
            promoted = True
            source_removed = not cross_volume
        except OSError as swap_error:
            try:
                os.rename(backup, destination)
                old_renamed = False
            except OSError as restore_error:
                raise MoveReplacePartial(
                    f"新目录未能移动，旧目标目录回滚也失败；"
                    f"旧目标暂存在 {backup}，请人工处理：{restore_error}", changed=True,
                ) from swap_error
            raise MergeDeleteBlocked(f"移动文件夹失败，已恢复目标旧目录：{swap_error}") from swap_error
        # 交换完成后，不再受停止事件中断；确保删除/日志收尾有机会完成。
        try:
            append_merge_delete_log(operation_id, "MOVE_FOLDER_PROMOTED",
                                    source=source, destination=destination,
                                    backup=backup, cross_volume=cross_volume)
        except OSError as error:
            raise MoveReplacePartial(
                f"目标目录已被替换，但写入操作日志失败；旧目标暂存在 {backup}：{error}",
                changed=True,
            ) from error
        warnings = []
        if cross_volume:
            try:
                shutil.rmtree(source)
                source_removed = True
            except OSError as error:
                raise MoveReplacePartial(
                    f"目标已经替换为源文件夹副本，但跨卷删除源目录未完成：{source}；{error}。"
                    f"目标旧内容暂存在 {backup}。已停止后续项目。", changed=True,
                ) from error
        try:
            # 不再使用旧目标目录，确保只能删除此次明确预检过的目标备份。
            shutil.rmtree(backup)
            old_renamed = False
        except OSError as error:
            warnings.append(f"目标覆盖已完成，但旧目录备份清理失败：{backup}；{error}")
        return {"source_removed": source_removed, "warnings": warnings,
                "backup": backup if warnings else ""}
    finally:
        if os.path.lexists(stage):
            try:
                shutil.rmtree(stage)
            except OSError as error:
                # 最少写下残留位置，避免下一次扫描误把临时目录当作作品。
                try:
                    append_merge_delete_log(operation_id, "MOVE_STAGE_CLEANUP_FAILED",
                                            staging=stage, error=str(error))
                except OSError as audit_error:
                    if sys.stderr is not None:
                        print(f"暂存目录清理失败：{stage}；{error}；日志：{audit_error}",
                              file=sys.stderr)
        # 未交换成功的情况下，旧目标应仍位于其原来的路径；兜底回滚。
        if old_renamed and not promoted and not os.path.lexists(destination):
            try:
                os.rename(backup, destination)
            except OSError:
                pass


def _refresh_move_scopes(plans: list[dict], from_prefix: str, to_prefix: str,
                         notify: Callable[[int, int, str], None]) -> dict:
    """只重新读取本次操作实际触及的作品文件夹，不重扫两万个目录。"""
    all_records = []
    errors = []
    old_nfo_count = sum(
        plan["source_snapshot"]["nfo_count"] + plan["destination_snapshot"]["nfo_count"]
        for plan in plans
    )
    paths = list(dict.fromkeys(
        path for plan in plans
        for path in (plan["source_folder"], plan["destination_folder"])
    ))
    unchanged_event = threading.Event()  # 此阶段只是校正结果，不受已请求的 stop 干扰
    cache_rows = []
    new_nfo_count = 0
    for index, path in enumerate(paths, 1):
        if os.path.isdir(path):
            # 每个作品目录单独遍历，避免 unique_scan_roots 对大量路径进行 O(N²) 去重。
            for job in iter_scan_folders([path], unchanged_event, errors):
                new_nfo_count += sum(not is_trailer_filename(item.name) for item in job[1])
                fresh, changed, _hits, _reads, notices = read_scan_folder(
                    job, {}, "番号", from_prefix, to_prefix, unchanged_event,
                )
                all_records.extend(fresh)
                cache_rows.extend(changed)
                errors.extend(notices)
        elif os.path.lexists(path):
            errors.append(f"作品目录仍存在，但不是可扫描文件夹：{path}")
        if index % 5 == 0 or index == len(paths):
            notify(index, max(1, len(paths)),
                   f"正在更新已移动作品的列表 {index}/{len(paths)}（不重扫其他目录）")
    if cache_rows:
        try:
            cache = LocalTextCache(program_data_dir() / "nfo_cache.sqlite3")
            try:
                cache.store_many(cache_rows)
            finally:
                cache.close()
        except (OSError, sqlite3.Error) as error:
            errors.append(f"已移动文件的缓存写入失败：{error}")
    return {"scopes": paths, "records": all_records,
            "old_nfo_count": old_nfo_count, "new_nfo_count": new_nfo_count,
            "errors": errors[:25]}


def execute_bulk_move(
    batch: dict, records: list[MediaEntry], roots: list[str],
    from_prefix: str, to_prefix: str, stop_event: threading.Event,
    notify: Callable[[int, int, str], None],
) -> dict:
    """逐个交换完整作品文件夹；每项完成后才开始下一项。"""
    operation_id = batch["operation_id"]
    source_root, destination_root = validate_move_roots(
        batch["source_root"], batch["destination_root"], roots,
    )
    plans = list(batch["selected_plans"])
    all_folders = [nt_key(plan[key]) for plan in plans
                   for key in ("source_folder", "destination_folder")]
    if len(all_folders) != len(set(all_folders)):
        raise MergeDeleteBlocked("所选文件夹在多条移动计划之间冲突，整批禁止执行")
    for plan in plans:
        if not is_nested_path(plan["source_folder"], source_root) or (
            not is_nested_path(plan["destination_folder"], destination_root)
        ):
            raise MergeDeleteBlocked("预览清单中的源或目标目录超出了最终选定的根目录")
    outcome = {"moved": [], "skipped": [], "error": "", "stopped": False,
               "source_root": source_root, "destination_root": destination_root,
               "total": len(plans), "preview_path": batch["preview_path"],
               "refresh": {}, "warnings": []}
    affected = []
    append_merge_delete_log(
        operation_id, "MOVE_FOLDER_EXECUTE_START",
        source_root=source_root, destination_root=destination_root, selected=len(plans),
    )
    for number, plan in enumerate(plans, 1):
        if stop_event.is_set():
            outcome["stopped"] = True
            break
        current = dict(plan)
        current["source_root"] = source_root
        current["destination_root"] = destination_root
        try:
            _verify_move_folder_plan(current, source_root, destination_root, stop_event)
            notify(number - 1, len(plans), f"正在移动作品文件夹 {number}/{len(plans)}：{plan['code']}")
            result = _move_replace_folder(current, stop_event, operation_id)
            affected.append(plan)
            completed = {"code": plan["code"], "source_folder": plan["source_folder"],
                         "destination_folder": plan["destination_folder"]}
            outcome["moved"].append(completed)
            if result["warnings"]:
                outcome["warnings"].extend(result["warnings"])
                outcome["error"] = "；".join(result["warnings"])
            append_merge_delete_log(
                operation_id, "MOVE_FOLDER_ITEM_DONE", **completed,
                warnings=result["warnings"],
            )
            notify(number, len(plans),
                   f"已移动 {len(outcome['moved'])}/{len(plans)} 个作品文件夹")
            if result["warnings"]:
                break  # 旧备份清理失败时停止后续项
        except Interrupted:
            outcome["stopped"] = True
            break
        except MoveReplacePartial as error:
            if error.changed:
                affected.append(plan)
            outcome["error"] = f"{plan['code']}：{error}"
            try:
                append_merge_delete_log(operation_id, "MOVE_FOLDER_PARTIAL",
                                        code=plan["code"], reason=str(error))
            except OSError as log_error:
                outcome["error"] += f"；无法写入部分完成日志：{log_error}"
            break
        except (MergeDeleteBlocked, OSError, ValueError) as error:
            outcome["error"] = f"{plan['code']}：{error}"
            try:
                append_merge_delete_log(operation_id, "MOVE_FOLDER_ITEM_FAILED",
                                        code=plan["code"], reason=str(error))
            except OSError as log_error:
                outcome["error"] += f"；审计日志写入失败：{log_error}"
            break
    # 即使用户在途中按停止，也要把已完成的文件夹更新到 UI，避免显示过期路径。
    if affected:
        try:
            outcome["refresh"] = _refresh_move_scopes(
                affected, from_prefix, to_prefix, notify,
            )
        except (MergeDeleteBlocked, OSError, ValueError) as error:
            outcome["error"] += f"；局部刷新失败，须人工检查受影响目录：{error}"
    outcome["stopped"] = outcome["stopped"] or stop_event.is_set()
    try:
        append_merge_delete_log(
            operation_id,
            "MOVE_FOLDER_STOPPED" if outcome["stopped"] else
            ("MOVE_FOLDER_INCOMPLETE" if outcome["error"] else "MOVE_FOLDER_FINISHED"),
            moved=outcome["moved"], error=outcome["error"],
            warnings=outcome["warnings"],
        )
    except OSError as log_error:
        outcome["error"] += f"；最终审计日志写入失败：{log_error}"
    return outcome

from PySide6 import QtCore, QtGui, QtWidgets


from PySide6.QtCore import Qt


class ScanWorker(QtCore.QThread):
    progress = QtCore.Signal(int, int)
    result = QtCore.Signal(object)
    error = QtCore.Signal(str)

    def __init__(self, roots, field_mode, from_prefix, to_prefix, fuzzy=False, threshold=0.8,
                 scan_workers=1):
        super().__init__()
        self.roots = list(roots)
        self.field_mode = field_mode
        self.from_prefix = from_prefix
        self.to_prefix = to_prefix
        self.fuzzy = fuzzy
        self.threshold = threshold
        self.scan_workers = scan_workers
        self.stop_event = threading.Event()  # set() = 请求停止

    def stop(self):
        self.stop_event.set()

    def run(self):
        try:
            # 验证脚本或 EXE 所在目录可写：GUI 线程不执行磁盘写入。
            ensure_program_data_dir()
            if self.stop_event.is_set():
                return
            cache_stats = {}
            records, nfo, strm, video, errors = scan_library(
                self.roots, self.field_mode, self.from_prefix, self.to_prefix,
                self.stop_event, self.progress.emit,
                cache_path=program_data_dir() / "nfo_cache.sqlite3",
                cache_report=cache_stats, scan_workers=self.scan_workers,
            )
            if not self.stop_event.is_set():
                if self.field_mode == "系列" and self.fuzzy:
                    distinct = len({entry.code for entry in records if entry.code})
                    if distinct > 600:
                        errors.append(
                            f"系列名有 {distinct} 种，为避免近似匹配 O(N²) 耗时过长，本次改用精确匹配。"
                        )
                        self.fuzzy = False
                matches = group_duplicates(
                    records, mode=self.field_mode,
                    fuzzy=self.fuzzy, threshold=self.threshold,
                    stop_event=self.stop_event,
                )
                restored = 0
                index = None
                try:
                    indexed = {
                        row.target for group in matches.values() for row in group
                        if row.target and row.target_kind in ("path", "url")
                    }
                    index = MediaIndex(program_data_dir() / "video_index.sqlite3")
                    saved = index.get_many(sorted(indexed))
                    for row in records:
                        if self.stop_event.is_set():
                            break
                        info = saved.get(row.target)
                        if info is not None:
                            row.info = dict(info)
                            row.info_status = "缓存命中（14天）"
                            restored += 1
                except (OSError, sqlite3.Error) as exc:
                    errors.append(f"视频参数缓存读取失败：{exc}")
                finally:
                    if index is not None:
                        index.close()
                if not self.stop_event.is_set():
                    # 扫描完成后仅对 code 为空的条目追加诊断日志；在 QThread 内写盘。
                    unknown_log_path = ""
                    try:
                        unknown_log_path = append_unrecognized_scan_log(
                            records, self.field_mode
                        )
                    except OSError as error:
                        # 诊断日志故障不应该破坏既有的查重扫描结果。
                        errors.append(f"未识别番号日志写入失败：{error}")
                    self.result.emit({
                        "records": records, "matches": matches,
                        "counts": (nfo, strm, video), "errors": errors,
                        "text_cache": cache_stats, "video_cache_restored": restored,
                        "unrecognized_log_path": unknown_log_path,
                    })
        except Exception as error:
            self.error.emit(f"{type(error).__name__}: {error}")


class ProbeWorker(QtCore.QThread):
    """有界视频探测队列。

    Qt 信号只从管理 QThread 发出，GUI 更新留给主线程。
    外层 QThread 负责 SQLite 缓存；线程池仅调用 ffprobe 子进程。
    同时存在的 ffprobe 最大 1～2 个，不一次提交所有任务。
    所有停止事件都是 set() 表示请求停止。
    """
    progress = QtCore.Signal(int, int, str)
    batch_ready = QtCore.Signal(object)  # [(target, info, state)]
    error = QtCore.Signal(str)
    statistics = QtCore.Signal(object)

    def __init__(
        self, jobs: list[tuple[str, str]], cache_path: Path, *,
        refresh: bool = False, timeout: float = 15, concurrency: int = 1,
    ):
        super().__init__()
        self.jobs = list(jobs)
        self.cache_path = cache_path
        self.refresh = refresh
        self.timeout = timeout
        self.concurrency = max(1, min(int(concurrency), 2))
        self.stop_event = threading.Event()  # set -> 请求取消待运行及在运行的 ffprobe

    def stop(self):
        self.stop_event.set()

    def _probe_one(self, binary, target):
        if self.stop_event.is_set():
            return {}, "已停止"
        try:
            info = run_ffprobe(binary, target, self.stop_event, timeout=self.timeout)
            return info, "视频实测"
        except Interrupted:
            return {}, "已停止"
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return filename_hint(target), f"探测失败：{str(exc)[:120]}"

    def run(self):
        cache = None
        results = []
        pending = {}
        counts = {"total": len(self.jobs), "finished": 0, "cached": 0,
                  "measured": 0, "failed": 0, "stopped": False}
        sent_progress_at = 0.0
        last_batch = time.monotonic()

        def deliver(target, info, state):
            nonlocal sent_progress_at, last_batch
            counts["finished"] += 1
            if state.startswith("缓存命中"):
                counts["cached"] += 1
            elif info.get("verified"):
                counts["measured"] += 1
            else:
                counts["failed"] += 1
            results.append((target, info, state))
            now = time.monotonic()
            if len(results) >= 32 or now - last_batch >= 0.25:
                self.batch_ready.emit(list(results))
                results.clear()
                last_batch = now
            if now - sent_progress_at > 0.15 or counts["finished"] == counts["total"]:
                self.progress.emit(
                    counts["finished"], counts["total"],
                    ntpath.basename(target),
                )
                sent_progress_at = now

        try:
            binary = ffprobe_binary()
            saved = {}
            try:
                cache = MediaIndex(self.cache_path)
                if not self.refresh:
                    saved = cache.get_many([target for target, kind in self.jobs])
            except (OSError, sqlite3.Error) as exc:
                # 缓存故障不能让实际探测一同失效。
                self.error.emit(f"缓存不可用，将继续直接探测：{exc}")
            index = 0
            total = len(self.jobs)

            # 无 ffprobe 时也优先读取本地 SQLite 缓存。
            if not binary:
                for target, kind in self.jobs:
                    if self.stop_event.is_set():
                        break
                    stored = saved.get(target)
                    if stored is not None and stored.get("verified"):
                        deliver(target, stored, "缓存命中（14天）")
                    else:
                        deliver(target, filename_hint(target), "未找到 ffprobe.exe")
            else:
                with ThreadPoolExecutor(
                    max_workers=self.concurrency, thread_name_prefix="CGDedupe-CD2Probe"
                ) as pool:
                    while index < total or pending:
                        if self.stop_event.is_set():
                            for future in pending:
                                future.cancel()
                            # 仍须等待运行中的 ffprobe 收到 stop_event 并结束。
                            if not pending:
                                break

                        while (not self.stop_event.is_set()
                               and len(pending) < self.concurrency and index < total):
                            target, kind = self.jobs[index]
                            index += 1

                            if kind not in ("path", "url"):
                                deliver(target, filename_hint(target), "路径待映射")
                                continue
                            stored = saved.get(target)
                            if stored is not None:
                                deliver(target, stored, "缓存命中（14天）")
                                continue

                            future = pool.submit(self._probe_one, binary, target)
                            pending[future] = target

                        if not pending:
                            if index >= total or self.stop_event.is_set():
                                break
                            continue

                        done, _ = wait(
                            tuple(pending), timeout=0.25, return_when=FIRST_COMPLETED
                        )
                        for future in done:
                            target = pending.pop(future)
                            if future.cancelled():
                                continue
                            try:
                                info, state = future.result()
                            except (OSError, RuntimeError, ValueError) as exc:
                                info, state = filename_hint(target), f"探测失败：{exc}"
                            if state == "已停止" or self.stop_event.is_set():
                                continue
                            if state == "视频实测":
                                try:
                                    if cache is not None:
                                        cache.store(target, info)
                                except sqlite3.Error:
                                    state = "实测成功（缓存写入失败）"
                            deliver(target, info, state)
                            # 连续高频请求容易触发 115 风控；允许 stop 打断短间隔。
                            if state.startswith("探测失败"):
                                if self.stop_event.wait(0.8):
                                    break

            counts["stopped"] = self.stop_event.is_set()
        except (OSError, sqlite3.Error, RuntimeError) as exc:
            self.error.emit(f"视频参数读取失败：{exc}")
        finally:
            if results:
                self.batch_ready.emit(list(results))
            if cache is not None:
                try:
                    cache.close()
                except sqlite3.Error as exc:
                    self.error.emit(f"参数缓存保存失败：{exc}")
            counts["stopped"] = self.stop_event.is_set()
            self.statistics.emit(dict(counts))


class FolderOpenWorker(QtCore.QThread):
    """在后台打开目录，不强制 Windows Explorer，恢复旧版系统关联方式。"""

    errors = QtCore.Signal(str)
    opened = QtCore.Signal(int, str)

    def __init__(self, folders: list[str]):
        super().__init__()
        self.folders = list(folders)
        self.stop_event = threading.Event()  # set() 代表请求停止后续打开操作

    def stop(self):
        self.stop_event.set()

    def run(self):
        opened_count = 0
        failures = []
        # OneCommander 官方支持：OneCommander.exe -o "完整路径" -newtab
        # 多目录时优先用此命令保证打开为不同标签；单目录走系统注册关联，
        # 与旧版 NFO 查重脚本的 os.startfile(folder) 完全一致。
        onecommander = (
            find_onecommander_executable() if len(self.folders) > 1 else None
        )
        manager = "OneCommander 新标签" if onecommander else "Windows 文件夹默认关联"
        for folder in self.folders:
            if self.stop_event.is_set():
                break
            # folder 来自本地扫描时保存的父目录，不能错误拼入 Explorer 的 /n 参数。
            folder_path = ntpath.normpath(str(folder).strip())
            if not ntpath.isabs(folder_path):
                failures.append(f"非绝对路径，已拒绝打开：{folder}")
                continue
            try:
                if not os.path.isdir(folder_path):
                    raise FileNotFoundError(f"目标目录不存在或暂不可访问：{folder_path}")
                if onecommander:
                    try:
                        subprocess.Popen(
                            [onecommander, "-o", folder_path, "-newtab"],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            close_fds=True,
                        )
                    except OSError:
                        # 启动别名失效时，恢复原版系统文件夹关联处理。
                        os.startfile(folder_path)
                        manager = "Windows 文件夹默认关联（OneCommander 启动失败后回退）"
                else:
                    # 原版实现：保留 Windows 当前的文件夹关联/OneCommander 接管。
                    os.startfile(folder_path)
                opened_count += 1
            except (OSError, ValueError) as exc:
                failures.append(f"{folder_path}：{exc}")
            if self.stop_event.wait(0.15):
                break

        if failures:
            self.errors.emit("\n".join(failures[:8]))
        self.opened.emit(opened_count, manager)


class BulkMoveSelectionModel(QtCore.QAbstractTableModel):
    """可筛选、可勾选的整个作品文件夹移动计划。"""

    count_changed = QtCore.Signal(int)
    HEADERS = ("执行", "番号", "源作品文件夹（移走）", "目标作品文件夹（整体覆盖）", "文件数量：源 / 目标旧")

    def __init__(self, plans, parent=None):
        super().__init__(parent)
        self.plans = list(plans)
        self.checked = [True] * len(plans)

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.plans)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.plans)):
            return None
        column = index.column()
        if column == 0 and role == Qt.ItemDataRole.CheckStateRole:
            return Qt.CheckState.Checked if self.checked[index.row()] else Qt.CheckState.Unchecked
        plan = self.plans[index.row()]
        values = (
            "", plan["code"], plan["source_folder"], plan["destination_folder"],
            f"{plan['source_snapshot']['files']} / {plan['destination_snapshot']['files']}",
        )
        if role == Qt.ItemDataRole.DisplayRole:
            return values[column]
        if role == Qt.ItemDataRole.ToolTipRole and column > 0:
            return values[column]
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if index.column() == 0:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid() or index.column() != 0 or role != Qt.ItemDataRole.CheckStateRole:
            return False
        checked = value == Qt.CheckState.Checked or value == Qt.CheckState.Checked.value
        if self.checked[index.row()] == checked:
            return False
        self.checked[index.row()] = checked
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
        self.count_changed.emit(sum(self.checked))
        return True

    def select_rows(self, rows, checked):
        changed = False
        for row in rows:
            if 0 <= row < len(self.checked) and self.checked[row] != checked:
                self.checked[row] = checked
                changed = True
        if changed and self.checked:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.checked) - 1, 0),
                                  [Qt.ItemDataRole.CheckStateRole])
            self.count_changed.emit(sum(self.checked))

    def selected_plans(self):
        return [plan for plan, checked in zip(self.plans, self.checked) if checked]

class NfoDuplicateFinder(QtWidgets.QWidget):
    """主窗口：继续使用原 cg_dedupe.py 的 PySide6，保留番号/系列、目录配置、查重。"""

    MAX_DIRECTORIES = 9
    COL = {
        "title": 0, "count": 1, "path": 2, "quality": 3, "fps": 4,
        "codec": 5, "size": 6, "duration": 7,
        "play": 8, "open_folder": 9, "actions": 10,
        "source": 11, "type": 12,
    }

    def __init__(self, initial_directory: str | None = None):
        super().__init__()
        # 只计算路径并尝试读取旧便携配置；首次扫描前不创建数据目录。
        self.data_dir = program_data_dir()
        self._storage_active = self.data_dir.is_dir()
        self.settings_file = self.data_dir / "settings.json"
        self.cache_file = self.data_dir / "video_index.sqlite3"
        self.merge_log_file = self.data_dir / "merge_delete.jsonl"
        # 读取旧位置的设置，不改动旧文件；保存时统一写到 .py/.exe 同级。
        legacy_settings = application_dir() / DATA_DIR_NAME / "settings.json"
        appdata_settings = legacy_program_data_dir() / "settings.json"
        settings_source = next(
            (candidate for candidate in (self.settings_file, legacy_settings, appdata_settings)
             if candidate.is_file()), self.settings_file,
        )
        self.settings = json_load(settings_source)
        # 从旧版本继承已有的纯路径映射，不发起任何网络属性请求。
        legacy_mapping = self.settings.pop("webdav", {})
        legacy_mapping = legacy_mapping if isinstance(legacy_mapping, dict) else {}
        mapping = self.settings.get("path_mapping")
        mapping = mapping if isinstance(mapping, dict) else legacy_mapping
        self.path_mapping = {key: str(mapping.get(key) or "")
                             for key in ("source_prefix", "target_prefix")}
        saved_roots = self.settings.get("directories", [])
        if not isinstance(saved_roots, list):
            saved_roots = []
        self.directories = [
            item for item in saved_roots if isinstance(item, str)
        ][:self.MAX_DIRECTORIES]
        self.last_directory = str(self.settings.get("last_directory") or Path.home())
        self.writer = ConfigWriter()
        self.writer.start()
        self.worker = None
        self.closing_requested = False
        self.close_save_requested = False
        self.records = []
        self.duplicates = {}
        self.records_by_id: dict[int, MediaEntry] = {}
        self.rows_by_target: dict[str, list[MediaEntry]] = defaultdict(list)
        self.tree_items: dict[int, QtWidgets.QTreeWidgetItem] = {}
        self.group_items: dict[str, QtWidgets.QTreeWidgetItem] = {}
        self.group_for_entry: dict[int, str] = {}
        self.scan_counts = (0, 0, 0)
        self.scan_errors = []
        self._last_scan_mode = ""
        self._completed_scan_roots: tuple[str, ...] = ()
        self._scan_started_at = time.monotonic()
        self.sort_column = self.COL["title"]  # 默认按番号，而非版本数
        self.sort_reverse = False                # A-Z / 数字升序
        self._render_token = 0
        self._render_offset = 0
        self._render_groups = []
        self._loaded_groups = set()
        self._render_saved_id = None
        # 分组仍默认全部展开。子项通过单个 Qt 定时器分批生成，
        # 不为数千个分组同时创建数千个独立定时任务。
        self._child_jobs = deque()
        self._child_pending_codes = set()
        self._child_render_timer = QtCore.QTimer(self)
        self._child_render_timer.setSingleShot(True)
        self._child_render_timer.timeout.connect(self._render_children_queue)
        self._group_header_brush = QtGui.QBrush(QtGui.QColor(COLORS["pale"]))
        self.folder_worker = None
        self._current_probe_stats = {}

        self._init_ui()
        self._connect()
        self._load_options()
        if initial_directory:
            self.add_directory(initial_directory)
        self._refresh_roots()
        self._update_buttons()
        # 后面的 _set_status 同时更新文字及悬停提示，无需设置两次。
        self._set_status(
            f"数据目录：{self.data_dir}"
            + ("" if self._storage_active else "（首次扫描后自动生成）")
        )
        # 仅 Qt 主线程负责心跳；后台监视器只在 GUI 连续 12 秒无法响应
        # 时写入 Python 各线程堆栈，便于区分 Qt 绘制和磁盘/网络阻塞。
        # 所有线程停止 Event 的语义统一为 set() = 请求停止。
        self._last_gui_heartbeat = time.monotonic()
        self._gui_watchdog_stop = threading.Event()
        self._gui_heartbeat_timer = QtCore.QTimer(self)
        self._gui_heartbeat_timer.setInterval(400)
        self._gui_heartbeat_timer.timeout.connect(self._gui_heartbeat)
        self._gui_heartbeat_timer.start()
        self._gui_watchdog = threading.Thread(
            target=self._watch_gui_responsiveness,
            name="CGDedupe-GuiWatchdog", daemon=False,
        )
        self._gui_watchdog.start()
        QtCore.QTimer.singleShot(0, self._center_window)

    def _gui_heartbeat(self):
        """此方法由 Qt 主事件循环调用；只更新时间戳，不做任何磁盘 I/O。"""
        self._last_gui_heartbeat = time.monotonic()

    def _watch_gui_responsiveness(self):
        """监视 GUI 事件循环是否长时间不运行，只在后台写卡顿诊断。"""
        reported = False
        while not self._gui_watchdog_stop.wait(1.0):
            blocked_for = time.monotonic() - self._last_gui_heartbeat
            if blocked_for < 12.0:
                reported = False
                continue
            if reported:
                continue
            reported = True
            path = program_data_dir() / "gui_freeze_trace.log"
            try:
                with open(path, "a", encoding="utf-8", newline="\n") as out:
                    out.write(
                        f"\n{'=' * 72}\n"
                        f"{datetime.datetime.now().astimezone().isoformat()} "
                        f"GUI 无响应约 {blocked_for:.1f} 秒；版本 {APP_VERSION}\n"
                        f"Python: {sys.version.splitlines()[0]}\n"
                    )
                    faulthandler.dump_traceback(file=out, all_threads=True)
                    out.flush()
                if sys.stderr is not None:
                    print(f"[CGDedupe] 检测到 GUI 停顿，线程堆栈已写入 {path}",
                          file=sys.stderr, flush=True)
            except (OSError, RuntimeError) as error:
                if sys.stderr is not None:
                    print(f"[CGDedupe] GUI 停顿日志写入失败：{error}",
                          file=sys.stderr, flush=True)

    def _load_app_icon(self):
        """同一 ICO 用于 EXE、主窗口、任务栏；无需生成 PNG 文件。

        我们的原始多尺寸 ICO 内含 PNG 帧。直接从 ICO 的目录表取出
        PNG 字节，再让 Qt 加载，避免 Windows 环境缺少 Qt ICO 插件。
        若换成没有 PNG 帧的 ICO，则尝试 Qt 原生 ICO 解码。
        """
        icon_path = Path(resource_path("cg_dedupe.ico"))
        icon = QtGui.QIcon()
        try:
            raw = icon_path.read_bytes()
            if len(raw) >= 6 and raw[:4] == b"\x00\x00\x01\x00":
                count = int.from_bytes(raw[4:6], "little")
                for index in range(min(count, 256)):
                    item = 6 + index * 16
                    if item + 16 > len(raw):
                        break
                    size = int.from_bytes(raw[item + 8:item + 12], "little")
                    offset = int.from_bytes(raw[item + 12:item + 16], "little")
                    if not size or offset + size > len(raw):
                        continue
                    image = raw[offset:offset + size]
                    if image.startswith(b"\x89PNG\r\n\x1a\n"):
                        pixmap = QtGui.QPixmap()
                        if pixmap.loadFromData(image, "PNG") and not pixmap.isNull():
                            icon.addPixmap(pixmap)
        except OSError as error:
            if sys.stderr is not None:
                print(f"[CGDedupe] 无法读取图标：{error}", file=sys.stderr)

        if icon.isNull() and icon_path.is_file():
            icon = QtGui.QIcon(str(icon_path))
        if icon.isNull() and getattr(sys, "frozen", False):
            icon = QtWidgets.QFileIconProvider().icon(
                QtCore.QFileInfo(str(sys.executable))
            )
        if not icon.isNull():
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.setWindowIcon(icon)
            self.setWindowIcon(icon)
        elif sys.stderr is not None:
            print("[CGDedupe] 找不到可用的窗口图标。", file=sys.stderr)

    def _init_ui(self):
        """紧凑工作台：设置/统计/工具栏占可用窗口高度约 1/3 以内。"""
        self.setWindowTitle(f"{APP_TITLE}  v{APP_VERSION}")
        self.resize(1520, 860)
        self.setMinimumSize(1090, 650)
        # 不在 Windows 运行时向 Qt 注入 ICO/QPixmap：设置窗口图标会进入
        # qt_imageToWinHBITMAP/CreateDIBSection，部分系统返回无效窗口句柄。
        # 打包图标交给 PyInstaller --icon 处理，避免运行时的 GDI 转换。
        self.setStyleSheet(f"""
            QWidget {{
                font-family: "Microsoft YaHei UI", "Segoe UI";
                font-size: 12px; background: {COLORS['bg']}; color: {COLORS['ink']};
            }}
            QFrame#hero {{background:{COLORS['navy']}; border-radius:8px;}}
            QLabel#hero_title {{
                color:#FFFFFF; background:transparent; font-size:20px; font-weight:700;
            }}
            QLabel#hero_subtitle {{color:#B8CAE7; background:transparent;}}
            QFrame#panel, QFrame#stat {{
                background:white; border:1px solid {COLORS['line']}; border-radius:7px;
            }}
            QLabel#section, QLabel#muted, QLabel#stat_value {{
                background:transparent;
            }}
            QLabel#section {{font-weight:700; font-size:13px;}}
            QLabel#stat_value {{font-size:21px; font-weight:700;}}
            QLabel#muted {{color:{COLORS['muted']}; font-size:11px;}}
            QPushButton {{
                background:#EAF0FA; border:0; border-radius:6px;
                padding:5px 10px; min-height:19px; font-weight:600;
            }}
            QPushButton:hover {{background:#DDE9FC;}}
            QPushButton:disabled {{background:#EFF2F7; color:#A3ADBB;}}
            QPushButton#primary {{background:{COLORS['blue']}; color:white;}}
            QPushButton#primary:hover {{background:{COLORS['blue_hover']};}}
            QPushButton#primary:disabled {{background:#B9CBEF; color:#F7F9FE;}}
            QPushButton#toggle {{
                background:#EAF0FA; border:1px solid #CFDAE9; min-width:76px;
            }}
            QPushButton#toggle:checked {{
                background:#2C68D6; border:1px solid #2456B7; color:white;
            }}
            QLineEdit, QComboBox, QSpinBox {{
                border:1px solid {COLORS['line']}; border-radius:5px;
                padding:5px 7px; background:white;
            }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
                border:1px solid {COLORS['blue']};
            }}
            QTreeWidget {{
                background:white; alternate-background-color:#F8FAFE;
                border:1px solid {COLORS['line']}; border-radius:6px;
                selection-background-color:#DCE9FF; selection-color:{COLORS['ink']};
            }}
            QTreeWidget::item {{min-height:27px; padding:3px;}}
            QTreeWidget::item:selected {{
                background:#DCE9FF; color:{COLORS['ink']};
            }}
            QHeaderView::section {{
                background:#EAF1FA; border:0; border-right:1px solid #DFE6F0;
                padding:8px 5px; font-weight:700;
            }}
            QProgressBar {{
                background:#EAF0FA; border:1px solid {COLORS['line']};
                min-height:15px; border-radius:4px;
            }}
            QProgressBar::chunk {{background:{COLORS['blue']};}}
            QToolTip {{background:white; color:{COLORS['ink']};}}
        """)

        main = QtWidgets.QVBoxLayout(self)
        main.setContentsMargins(12, 10, 12, 10)
        main.setSpacing(6)

        hero = QtWidgets.QFrame()
        hero.setObjectName("hero")
        hero.setFixedHeight(50)
        banner = QtWidgets.QHBoxLayout(hero)
        banner.setContentsMargins(16, 5, 16, 5)
        title = QtWidgets.QLabel("大锤  /  NFO · STRM 多版本查重")
        title.setObjectName("hero_title")
        subtitle = QtWidgets.QLabel("Windows  ·  本地快速查重  ·  CD2 按需探测")
        subtitle.setObjectName("hero_subtitle")
        banner.addWidget(title)
        banner.addStretch()
        banner.addWidget(subtitle)
        main.addWidget(hero)

        summary = QtWidgets.QHBoxLayout()
        summary.setSpacing(7)
        self.stat_labels = []
        for name in ("扫描条目", "重复作品", "重复文件/套"):
            card = QtWidgets.QFrame()
            card.setObjectName("stat")
            card.setFixedHeight(44)
            line = QtWidgets.QHBoxLayout(card)
            line.setContentsMargins(13, 3, 13, 3)
            caption = QtWidgets.QLabel(name)
            caption.setObjectName("muted")
            value = QtWidgets.QLabel("—")
            value.setObjectName("stat_value")
            line.addWidget(caption)
            line.addStretch()
            line.addWidget(value)
            self.stat_labels.append(value)
            summary.addWidget(card, 1)
        main.addLayout(summary)

        scan_panel = QtWidgets.QFrame()
        scan_panel.setObjectName("panel")
        root = QtWidgets.QVBoxLayout(scan_panel)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(7)

        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(8)
        directory_label = QtWidgets.QLabel("扫描目录")
        directory_label.setObjectName("section")
        row1.addWidget(directory_label)
        self.directory_list = QtWidgets.QComboBox()
        self.directory_list.setMinimumWidth(270)
        self.directory_list.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.directory_list.setMinimumContentsLength(23)
        self.directory_list.setToolTip("支持添加最多9个本地目录，依次扫描全部已添加目录。")
        row1.addWidget(self.directory_list, 3)
        self.add_btn = QtWidgets.QPushButton("＋")
        self.add_btn.setToolTip("添加扫描目录")
        self.add_btn.setFixedWidth(37)
        self.remove_btn = QtWidgets.QPushButton("－")
        self.remove_btn.setToolTip("移除当前选中的扫描目录")
        self.remove_btn.setFixedWidth(37)
        row1.addWidget(self.add_btn)
        row1.addWidget(self.remove_btn)
        row1.addSpacing(12)
        row1.addWidget(QtWidgets.QLabel("依据"))
        self.field_combo = QtWidgets.QComboBox()
        self.field_combo.addItems(("番号", "系列"))
        row1.addWidget(self.field_combo)
        row1.addWidget(QtWidgets.QLabel("至少"))
        self.minimum_spin = QtWidgets.QSpinBox()
        self.minimum_spin.setRange(2, 99)
        self.minimum_spin.setValue(2)
        self.minimum_spin.setFixedWidth(65)
        row1.addWidget(self.minimum_spin)
        self.fuzzy_check = QtWidgets.QCheckBox("系列近似")
        self.fuzzy_check.setToolTip("仅适用于系列查重；番号始终精确匹配")
        row1.addWidget(self.fuzzy_check)
        self.threshold_spin = QtWidgets.QSpinBox()
        self.threshold_spin.setRange(50, 100)
        self.threshold_spin.setValue(80)
        self.threshold_spin.setSuffix("%")
        self.threshold_spin.setFixedWidth(70)
        row1.addWidget(self.threshold_spin)
        root.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(8)
        self.scan_btn = QtWidgets.QPushButton("开始快速查重")
        self.scan_btn.setObjectName("primary")
        self.stop_btn = QtWidgets.QPushButton("停止任务")
        self.stop_btn.setToolTip("停止扫描或读取队列；不会强制销毁线程")
        self.scan_tip = QtWidgets.QLabel("只读本地 STRM / NFO；CD1/CD2 计作一套版本")
        self.scan_tip.setObjectName("muted")
        row2.addWidget(self.scan_btn)
        row2.addWidget(self.stop_btn)
        row2.addWidget(self.scan_tip)
        row2.addStretch()
        row2.addWidget(QtWidgets.QLabel("本地扫描线程"))
        self.scan_workers_spin = QtWidgets.QSpinBox()
        self.scan_workers_spin.setRange(1, 16)
        self.scan_workers_spin.setValue(1)
        self.scan_workers_spin.setToolTip("仅用于本地 NFO/STRM 读取；默认1；可比较4/8的实际耗时；与CD2探测并发独立")
        row2.addWidget(self.scan_workers_spin)
        self.mapping_btn = QtWidgets.QPushButton("路径映射")
        row2.addWidget(self.mapping_btn)
        root.addLayout(row2)
        main.addWidget(scan_panel)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setSpacing(7)
        self.info_btn = QtWidgets.QPushButton("读取选中 / 整组参数")
        self.info_btn.setObjectName("primary")
        self.info_btn.setToolTip("支持 Ctrl 多选、Shift 多选和选中番号组")
        self.bulk_btn = QtWidgets.QPushButton("批量读取当前重复视频")
        self.bulk_btn.setToolTip(
            "读取当前筛选出的所有重复组的视频参数（不依赖分组是否展开）。"
            "从本地缓存恢复无需访问 CD2；结果显示在番号组的「已读 X/Y」、分辨率和帧率列。"
        )
        self.bulk_delete_btn = QtWidgets.QPushButton("⚠ 批量清理重复项")
        self.bulk_move_btn = QtWidgets.QPushButton("批量移动覆盖")
        self.bulk_move_btn.setToolTip(
            "从本次扫描根目录中选择源和目标；按重复番号匹配唯一的作品文件夹。"
            "可筛选勾选，将源整个作品文件夹移动到目标旧路径并整体替换原目录。"
            "目标旧 NFO/封面/STRM 也将被替换；不会访问 115/CD2 视频源。"
        )
        self.bulk_delete_btn.setToolTip(
            "从已扫描的目录中分别选择「待删目录」和「保留目录」。"
            "仅处理待删根下重复 STRM 所在的安全作品目录；"
            "同时删除指向的真实 115/CD2 视频，删除间隔至少 3 秒。"
            "执行前完整预检、预览、输入确认词；不安全的项目跳过。"
        )
        self.concurrent_spin = QtWidgets.QSpinBox()
        self.concurrent_spin.setRange(1, 2)
        self.concurrent_spin.setValue(1)
        self.concurrent_spin.setFixedWidth(56)
        self.concurrent_spin.setToolTip(
            "CloudDrive2 / 115：推荐 1；允许最多 2 条同时探测。"
            "不受 CD2 上传/预处理任务数量设置控制。"
        )
        self.refresh_check = QtWidgets.QPushButton("重测：关闭")
        self.refresh_check.setObjectName("toggle")
        self.refresh_check.setCheckable(True)
        self.refresh_check.setToolTip(
            f"参数缓存位置：{self.data_dir}\\video_index.sqlite3；"
            "默认复用14天内的结果。开启后，下次读取会绕过缓存重新访问 CD2。"
        )
        self.fastplay_check = QtWidgets.QPushButton("首帧：默认")
        self.fastplay_check.setObjectName("toggle")
        self.fastplay_check.setCheckable(True)
        self.fastplay_check.setToolTip(
            "仅 mpvnet：关闭预缓冲等待；对 MKV/MP4 限制容器探测大小。"
            "某些视频可能无法完整识别，取消勾选可恢复默认。"
        )
        self.filter_input = QtWidgets.QLineEdit()
        self.filter_input.setPlaceholderText("筛选番号、视频路径、文件夹…")
        self.filter_input.setClearButtonEnabled(True)
        self.filter_input.setMinimumWidth(165)
        toolbar.addWidget(self.info_btn)
        toolbar.addWidget(self.bulk_btn)
        toolbar.addWidget(self.bulk_delete_btn)
        toolbar.addWidget(self.bulk_move_btn)
        toolbar.addWidget(QtWidgets.QLabel("探测并发"))
        toolbar.addWidget(self.concurrent_spin)
        toolbar.addWidget(self.refresh_check)
        toolbar.addWidget(self.fastplay_check)
        toolbar.addStretch()
        toolbar.addWidget(self.filter_input, 1)
        main.addLayout(toolbar)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(13)
        self.tree.setHeaderLabels([
            "番号 / 版本", "文件/套数", "重复文件路径", "分辨率", "帧率",
            "编码", "大小", "时长",
            "播放", "文件夹", "操作", "视频源路径", "类型",
        ])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(20)
        self.tree.setAnimated(False)
        self.tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.tree.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.tree.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.setSortingEnabled(False)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.header().setSectionsClickable(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.setToolTip(
            "单击「文件夹」打开此目录（番号组则打开整组）；"
            "单击「播放」启动 mpvnet；Ctrl+A 选中已加载行，"
            "也可用「读取全部重复视频」批量处理当前结果。"
        )
        widths = [190, 58, 400, 105, 95, 78, 83, 74, 76, 86, 68, 340, 123]
        for index, width in enumerate(widths):
            self.tree.setColumnWidth(index, width)
        # Stretch 模式会锁定该列宽度；统一改为可拖拽的 Interactive。
        # 初始仍使用上方配置的 400px，用户之后可以自由拖动表头分隔线。
        self.tree.header().setSectionResizeMode(
            self.COL["path"], QtWidgets.QHeaderView.ResizeMode.Interactive
        )
        # 不调用 Windows 原生 QStyle.standardIcon 作为每条记录的图标；
        # 在海量树节点中反复绘制 Windows HBITMAP 可能造成句柄/绘制压力。
        # 播放、打开、更多按钮仍由文字列和点击事件实现。
        main.addWidget(self.tree, 1)

        footer = QtWidgets.QHBoxLayout()
        self.progress = QtWidgets.QProgressBar()
        self.progress.setMaximumWidth(320)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.status_label = QtWidgets.QLabel("准备就绪 · 选择本地刮削目录开始查重")
        self.status_label.setObjectName("muted")
        footer.addWidget(self.progress)
        footer.addWidget(self.status_label, 1)
        main.addLayout(footer)

    def _connect(self):
        self.add_btn.clicked.connect(self.choose_directory)
        self.remove_btn.clicked.connect(self.remove_directory)
        self.scan_btn.clicked.connect(self.start_scan)
        self.mapping_btn.clicked.connect(self.edit_path_mapping)
        self.scan_workers_spin.valueChanged.connect(self._save_settings)
        self.stop_btn.clicked.connect(self.stop_task)
        self.field_combo.currentTextChanged.connect(self._mode_changed)
        self.minimum_spin.valueChanged.connect(self.render_results)
        self.fuzzy_check.toggled.connect(self._fuzzy_changed)
        self.threshold_spin.valueChanged.connect(self._threshold_changed)
        self.filter_timer = QtCore.QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(180)
        self.filter_timer.timeout.connect(self.render_results)
        self.filter_input.textChanged.connect(lambda _text: self.filter_timer.start())
        self.info_btn.clicked.connect(self.get_video_info)
        self.bulk_btn.clicked.connect(self.get_all_video_info)
        self.bulk_delete_btn.clicked.connect(self.start_bulk_cleanup_precheck)
        self.bulk_move_btn.clicked.connect(self.start_bulk_move_precheck)
        self.concurrent_spin.valueChanged.connect(self._save_settings)
        self.refresh_check.toggled.connect(self._refresh_toggle_changed)
        self.fastplay_check.toggled.connect(self._fastplay_toggle_changed)
        self.tree.itemSelectionChanged.connect(self._selection_changed)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.itemClicked.connect(self._cell_clicked)
        self.tree.itemExpanded.connect(self._group_expanded)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.header().sectionClicked.connect(self._header_clicked)

    def _refresh_toggle_changed(self, active: bool):
        self.refresh_check.setText("重测：开启" if active else "重测：关闭")
        self._set_status(
            "下次读取将忽略缓存并重新探测 CD2 视频"
            if active else "下次读取优先使用14天内的本地视频参数缓存"
        )

    def _fastplay_toggle_changed(self, active: bool):
        self.fastplay_check.setText("首帧：加速" if active else "首帧：默认")
        self._save_settings()

    def _load_options(self):
        # 信号连接已建立；加载期间必须阻止保存，避免覆盖尚未加载的字段。
        blockers = [QtCore.QSignalBlocker(widget) for widget in (
            self.field_combo, self.minimum_spin, self.fuzzy_check, self.threshold_spin,
            self.concurrent_spin, self.fastplay_check, self.scan_workers_spin)]
        try:
            workers = int(self.settings.get("scan_workers", 1))
        except (TypeError, ValueError):
            workers = 1
        self.scan_workers_spin.setValue(max(1, min(16, workers)))
        self.field_combo.setCurrentText(self.settings.get("field", "番号"))
        try:
            minimum = int(self.settings.get("minimum", 2))
        except (TypeError, ValueError):
            minimum = 2
        self.minimum_spin.setValue(max(2, min(99, minimum)))
        self.fuzzy_check.setChecked(bool(self.settings.get("fuzzy", False)))
        try:
            threshold = int(self.settings.get("threshold", 80))
        except (TypeError, ValueError):
            threshold = 80
        self.threshold_spin.setValue(max(50, min(100, threshold)))
        self.concurrent_spin.setValue(1 if self.settings.get("probe_concurrency") not in (1, 2) else self.settings["probe_concurrency"])
        self.fastplay_check.setChecked(bool(self.settings.get("fast_play", False)))
        self.fastplay_check.setText("首帧：加速" if self.fastplay_check.isChecked() else "首帧：默认")
        del blockers
        self._update_fuzzy_ability()

    def edit_path_mapping(self):
        if self._task_active() or self.closing_requested:
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("STRM 路径前缀映射")
        layout = QtWidgets.QFormLayout(dialog)
        source = QtWidgets.QLineEdit(self.path_mapping.get("source_prefix", ""))
        target = QtWidgets.QLineEdit(self.path_mapping.get("target_prefix", ""))
        source.setPlaceholderText("例如 /115/视频；STRM 已是本地盘符时可留空")
        target.setPlaceholderText("例如 X:\\视频")
        source.setMinimumWidth(420)
        layout.addRow("STRM 原始前缀", source)
        layout.addRow("CD2 本地前缀", target)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            old, new = source.text().strip(), target.text().strip()
            if bool(old) != bool(new) or (new and not (WINDOWS_DRIVE.match(new) or new.startswith("\\\\"))):
                QtWidgets.QMessageBox.warning(self, "映射无效", "两项应同时填写或留空；本地前缀须为盘符绝对路径或 UNC 路径。")
                return
            self.path_mapping = {"source_prefix": old, "target_prefix": new}
            self._save_settings()
            self._set_status("路径映射已保存；重新扫描后应用")

    def _center_window(self):
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return
        # Qt6 的 availableGeometry() 和 QWidget frameGeometry() 都是逻辑像素。
        available = screen.availableGeometry()
        geometry = self.frameGeometry()
        geometry.moveCenter(available.center())
        self.move(geometry.topLeft())

    def _set_status(self, text: str):
        self.status_label.setText(text)
        self.status_label.setToolTip(text)

    def _save_settings(self):
        self.settings.update({
            "directories": list(self.directories),
            "last_directory": self.last_directory,
            "field": self.field_combo.currentText(),
            "minimum": self.minimum_spin.value(),
            "fuzzy": self.fuzzy_check.isChecked(),
            "threshold": self.threshold_spin.value(),
            "path_mapping": dict(self.path_mapping),
            "probe_concurrency": self.concurrent_spin.value(),
            "scan_workers": self.scan_workers_spin.value(),
            "fast_play": self.fastplay_check.isChecked(),
        })
        # 首次扫描前仅更新内存；扫描后由后台线程落盘。
        if self._storage_active:
            self.writer.submit(self.settings_file, self.settings)

    def _refresh_roots(self):
        previous = self.directory_list.currentText() if self.directory_list.count() else ""
        self.directory_list.blockSignals(True)
        self.directory_list.clear()
        for directory in self.directories:
            self.directory_list.addItem(directory)
            self.directory_list.setItemData(
                self.directory_list.count() - 1, directory, Qt.ItemDataRole.ToolTipRole
            )
        self.directory_list.setCurrentIndex(
            self.directories.index(previous) if previous in self.directories else
            (len(self.directories) - 1 if self.directories else -1)
        )
        self.directory_list.blockSignals(False)
        self.directory_list.setToolTip(
            f"当前有 {len(self.directories)} 个扫描目录，开始查重会扫描所有目录。"
        )

    def choose_directory(self):
        # 首次扫描之前选择仅保存在内存；扫描后保存到程序旁 settings.json。
        # 让原生对话框处理路径有效性；不要先在 GUI 线程 stat 可能离线的挂载盘。
        initial = self.last_directory or str(Path.home())
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self, "选择 NFO / STRM 扫描目录", initial
        )
        if selected:
            self.last_directory = selected
            self.add_directory(selected)

    def add_directory(self, directory: str):
        directory = os.path.abspath(directory)
        if nt_key(directory) in {nt_key(root) for root in self.directories}:
            self._set_status(f"目录已在扫描范围：{directory}")
            return
        if len(self.directories) >= self.MAX_DIRECTORIES:
            QtWidgets.QMessageBox.warning(self, "目录数量限制", "最多允许九个扫描目录。")
            return
        self.directories.append(directory)
        self._completed_scan_roots = ()  # 目录改变后必须重新扫描，才能批量删除
        self._refresh_roots()
        self._save_settings()
        self._update_buttons()

    def remove_directory(self):
        index = self.directory_list.currentIndex()
        if index < 0 or index >= len(self.directories):
            return
        removed = self.directories.pop(index)
        self._completed_scan_roots = ()  # 目录改变后必须重新扫描，才能批量删除
        self._refresh_roots()
        self._save_settings()
        self._update_buttons()
        self._set_status(f"已移除目录：{removed}")

    def _update_fuzzy_ability(self):
        is_series = self.field_combo.currentText() == "系列"
        self.fuzzy_check.setEnabled(is_series)
        self.threshold_spin.setEnabled(is_series and self.fuzzy_check.isChecked())

    def _mode_changed(self, _value):
        self._update_fuzzy_ability()
        self._save_settings()
        if self.records:
            self._set_status("匹配依据已改变：请重新扫描才会应用")

    def _fuzzy_changed(self, _value):
        self._update_fuzzy_ability()
        self._save_settings()
        if self.records and self.field_combo.currentText() == "系列":
            self._set_status("系列相似匹配已改变：重新快速查重后生效")

    def _threshold_changed(self, _value):
        self._save_settings()
        if self.records and self.fuzzy_check.isChecked() and self.field_combo.currentText() == "系列":
            self._set_status("系列相似度已改变：重新快速查重后生效")


    def _task_active(self):
        # finished 信号排队期间仍保留任务所有权，防止新任务覆盖旧任务。
        return self.worker is not None

    def _update_buttons(self):
        busy = self._task_active() or self.closing_requested
        self.scan_btn.setEnabled(not busy)
        self.mapping_btn.setEnabled(not busy)
        self.scan_workers_spin.setEnabled(not busy)
        self.info_btn.setEnabled(not busy and bool(self.records))
        self.bulk_btn.setEnabled(not busy and bool(self.duplicates))
        self.bulk_delete_btn.setEnabled(
            not busy and bool(self.duplicates) and self._last_scan_mode == "番号"
            and bool(self._completed_scan_roots)
        )
        self.bulk_move_btn.setEnabled(
            not busy and bool(self.duplicates) and self._last_scan_mode == "番号"
            and self.field_combo.currentText() == "番号" and bool(self._completed_scan_roots)
        )
        self.concurrent_spin.setEnabled(not busy)
        self.refresh_check.setEnabled(not busy)
        self.fastplay_check.setEnabled(not self.closing_requested)
        self.add_btn.setEnabled(not busy)
        self.remove_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(self._task_active())
        self.field_combo.setEnabled(not busy)
        self.fuzzy_check.setEnabled(not busy and self.field_combo.currentText() == "系列")
        self.threshold_spin.setEnabled(
            not busy and self.field_combo.currentText() == "系列"
            and self.fuzzy_check.isChecked()
        )
        self.minimum_spin.setEnabled(not busy)
        self.directory_list.setEnabled(not busy)

    def _begin_task(self, worker, message):
        if self._task_active():
            return False
        self.worker = worker
        worker.progress.connect(self._task_progress)
        if hasattr(worker, "error"):
            worker.error.connect(self._task_error)
        worker.finished.connect(self._task_finished)
        self.progress.setRange(0, 0)
        self._set_status(message)
        worker.start()
        self._update_buttons()
        return True

    def start_scan(self):
        if self._task_active() or self.closing_requested:
            return
        if not self.directories:
            QtWidgets.QMessageBox.warning(self, "缺少目录", "请至少添加一个本地刮削目录。")
            return
        # 避免在 GUI 主线程对 CD2 根目录做 isdir/stat 等阻塞 I/O。
        # 在启动之前冻结设置；只有明确填写的路径前缀才映射 STRM。
        old = self.path_mapping.get("source_prefix", "")
        new = self.path_mapping.get("target_prefix", "")
        self._scan_started_at = time.monotonic()
        self._last_scan_mode = ""
        self._completed_scan_roots = ()
        # 此时用户明确发起扫描，才允许配置写入、创建缓存与日志目录。
        self._storage_active = True
        self._save_settings()
        self._render_token += 1
        self._render_groups = []
        self._loaded_groups.clear()
        self.filter_timer.stop()
        # 新一轮全库扫描不沿用旧搜索词，避免结果已找到但被旧过滤条件隐藏。
        filter_blocker = QtCore.QSignalBlocker(self.filter_input)
        self.filter_input.clear()
        del filter_blocker
        self._child_render_timer.stop()
        self._child_jobs.clear()
        self._child_pending_codes.clear()
        self.records = []
        self.duplicates = {}
        self.records_by_id = {}
        self.tree_items = {}
        self.group_items = {}
        self.group_for_entry = {}
        self.rows_by_target = defaultdict(list)
        self.scan_counts = (0, 0, 0)
        self.tree.clear()
        worker = ScanWorker(
            self.directories, self.field_combo.currentText(), old, new,
            fuzzy=self.fuzzy_check.isChecked(),
            threshold=self.threshold_spin.value() / 100.0,
            scan_workers=self.scan_workers_spin.value(),
        )
        worker.result.connect(self._scan_complete)
        self._begin_task(worker, "快速扫描中：不会读取 CD2 指向的视频")

    def _scan_complete(self, result):
        self.records = list(result["records"])
        self.duplicates = dict(result["matches"])
        self._last_scan_mode = self.field_combo.currentText()
        self._completed_scan_roots = tuple(self.directories)
        self.records_by_id = {entry.identifier: entry for entry in self.records}
        nfo_count, strm_count, video_count = result["counts"]
        self.scan_counts = (nfo_count, strm_count, video_count)
        stats = result["text_cache"]
        cache_hits, cache_parsed = stats.get("hits", 0), stats.get("parsed", 0)
        strm_hits, strm_read = stats.get("strm_hits", 0), stats.get("strm_read", 0)
        restored_video_rows = result["video_cache_restored"]
        visible_errors = list(result["errors"])
        self.scan_errors = visible_errors
        self.rows_by_target = defaultdict(list)
        for entry in self.records:
            if entry.target and entry.target_kind in ("path", "url"):
                key = target_identity(entry.target, entry.target_kind)
                self.rows_by_target[key].append(entry)
        self.render_results()
        unknown = sum(not e.code for e in self.records)
        total_duplicate_groups = len(self.duplicates)
        visible_groups = len(self._render_groups)
        visibility = (
            f"（当前显示 {visible_groups} 组；检查‘至少’与筛选条件）"
            if visible_groups != total_duplicate_groups else ""
        )
        message = (
            f"扫描完成（{time.monotonic() - self._scan_started_at:.2f}秒）："
            f"NFO {nfo_count}，STRM {strm_count}，视频 {video_count}，"
            f"重复 {total_duplicate_groups} 组{visibility}，番号未识别 {unknown}"
        )
        message += (
            f"；NFO 缓存命中 {cache_hits} / 本次解析 {cache_parsed}"
            f"；STRM 缓存命中 {strm_hits} / 本次读取 {strm_read}"
            f"；已恢复视频参数 {restored_video_rows} 条（14天内）"
        )
        if visible_errors:
            message += f"；{len(visible_errors)} 条扫描提示"
        self._set_status(message)
        unknown_log_path = result.get("unrecognized_log_path", "")
        if visible_errors or unknown_log_path:
            hints = [message]
            if unknown_log_path:
                hints.append(f"未识别番号详情日志：{unknown_log_path}")
            if visible_errors:
                hints.extend(map(str, visible_errors))
            self.status_label.setToolTip("\n\n".join(hints))

    def stop_task(self):
        if not self._task_active():
            return
        self.worker.stop()
        self.stop_btn.setEnabled(False)
        self._set_status("已请求停止，等待当前文件访问 / 网络请求结束…")

    def _task_progress(self, current, total, label=""):
        if isinstance(self.worker, ScanWorker):
            self.progress.setRange(0, 0)
            self._set_status(
                f"快速查重中：已遍历 {current:,} 个目录，已读取 {total:,} 个 NFO"
            )
            return
        if isinstance(self.worker, (BulkDeleteWorker, BulkMoveWorker)):
            self.progress.setRange(0, max(1, total))
            self.progress.setValue(min(current, max(1, total)))
            if label:
                self._set_status(label)
            return
        if total <= 0:
            self.progress.setRange(0, 0)
            return
        self.progress.setRange(0, total)
        self.progress.setValue(min(current, total))

    def _task_error(self, message):
        self._set_status(f"任务失败：{message}")
        QtWidgets.QMessageBox.warning(self, "任务失败", message)

    def _task_finished(self):
        source = self.sender()
        if source is not self.worker:
            return
        stopped = source.stop_event.is_set()
        was_probe = isinstance(source, ProbeWorker)
        was_merge = isinstance(source, MergeDeleteWorker)
        was_bulk_delete = isinstance(source, BulkDeleteWorker)
        was_bulk_move = isinstance(source, BulkMoveWorker)
        merge_outcome = source.outcome if was_merge else None
        bulk_outcome = source.outcome if was_bulk_delete else None
        move_outcome = source.outcome if was_bulk_move else None
        source.stop_event.clear()  # worker ended; clear Event for any later task
        source.deleteLater()
        self.worker = None
        self.progress.setRange(0, 1)
        self.progress.setValue(0 if stopped else 1)
        if was_probe:
            summary = self._current_probe_stats
            self._set_status(
                f"{'已停止' if stopped else '探测完成'}："
                f"{summary.get('finished', 0)}/{summary.get('total', 0)}，"
                f"缓存 {summary.get('cached', 0)}，实测 {summary.get('measured', 0)}，"
                f"失败/跳过 {summary.get('failed', 0)}"
            )
        elif stopped:
            self._set_status("任务已停止")
        self._update_buttons()
        if self.closing_requested:
            QtCore.QTimer.singleShot(0, self.close)
        elif was_merge and merge_outcome is not None:
            QtCore.QTimer.singleShot(
                0, lambda response=merge_outcome: self._handle_merge_stage(response)
            )
        elif was_bulk_delete and bulk_outcome is not None:
            QtCore.QTimer.singleShot(
                0, lambda response=bulk_outcome: self._handle_bulk_stage(response)
            )
        elif was_bulk_move and move_outcome is not None:
            QtCore.QTimer.singleShot(
                0, lambda response=move_outcome: self._handle_bulk_move_stage(response)
            )

    def render_results(self):
        """所有重复组默认展开；分组和媒体子行均分批插入，避免阻塞 UI。"""
        if not hasattr(self, "tree") or self.closing_requested:
            return
        selected = self.tree.currentItem()
        saved_id = None
        if selected is not None:
            stored = selected.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(stored, dict):
                saved_id = stored.get("id")
        minimum = self.minimum_spin.value()
        query = self.filter_input.text().strip().casefold()
        groups = []
        versions_total = 0
        for code, items in self.duplicates.items():
            count = version_count(items)
            if count < minimum:
                continue
            if not group_matches_query(code, items, query):
                continue
            groups.append((code, items, count))
            versions_total += count
        if self.sort_column == self.COL["count"]:
            groups.sort(
                key=lambda row: (row[2], number_sort_key(row[0]), row[0].casefold()),
                reverse=self.sort_reverse,
            )
        else:
            groups.sort(
                key=lambda row: (number_sort_key(row[0]), row[0].casefold()),
                reverse=self.sort_reverse,
            )

        self._render_token += 1
        token = self._render_token
        self._child_render_timer.stop()
        self._child_jobs.clear()
        self._child_pending_codes.clear()
        self._render_offset = 0
        self._render_groups = groups
        self._loaded_groups = set()
        self._render_saved_id = saved_id
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        self.tree_items = {}
        self.group_items = {}
        self.group_for_entry = {
            row.identifier: code for code, group, _count in groups for row in group
        }
        self.tree.setUpdatesEnabled(True)
        self.stat_labels[0].setText(
            f"{self.scan_counts[0]} / {self.scan_counts[1]} / {self.scan_counts[2]}"
        )
        total_groups = len(self.duplicates)
        visible_groups = len(groups)
        self.stat_labels[1].setText(
            str(visible_groups) if visible_groups == total_groups
            else f"{visible_groups} / {total_groups}"
        )
        self.stat_labels[1].setToolTip(
            f"已识别重复组 {total_groups}；当前筛选显示 {visible_groups}。"
            f"\n至少重复数：{minimum}；搜索词：{query or '无'}"
        )
        self.stat_labels[2].setText(str(versions_total))
        if not groups and self.duplicates:
            notice = QtWidgets.QTreeWidgetItem(self.tree)
            notice.setText(0, "已发现重复，但当前筛选未显示任何组")
            notice.setText(
                self.COL["path"],
                f"总计 {total_groups} 组；当前至少 {minimum} 套，"
                f"搜索词：{query or '无'}。请清空搜索词或把‘至少’调至 2。",
            )
            notice.setData(0, Qt.ItemDataRole.UserRole, {"kind": "notice"})
        else:
            # 当前可见组（特别是 5 组这种小结果）立即开始显示，
            # 剩余大量结果仍由后续时间片处理，不等待后台回调。
            self._render_group_batch(token)

    def _render_group_batch(self, token: int):
        if token != self._render_token or self.closing_requested:
            return
        # 单次最多 35 组、约 9ms；分组一创建就默认展开，
        # 子行进入统一队列分批插入，而不是一次性构建上万行。
        start = self._render_offset
        end = min(start + 35, len(self._render_groups))
        deadline = time.monotonic() + 0.009
        actual_end = start
        self.tree.setUpdatesEnabled(False)
        try:
            for code, entries, count in self._render_groups[start:end]:
                root = QtWidgets.QTreeWidgetItem(self.tree)
                root.setText(0, code)
                root.setText(1, str(count))
                aliases = sorted(
                    {entry.code for entry in entries if entry.code},
                    key=number_sort_key,
                )
                if len(aliases) > 1:
                    root.setToolTip(
                        0, "等价番号（原始番号均保留）：\n"
                        + "\n".join(aliases[:20])
                        + (f"\n……另有 {len(aliases) - 20} 个" if len(aliases) > 20 else "")
                        + "\n\n仅供查重参考；永久删除前请确认实际视频内容。"
                    )
                unique_dirs = unique_group_folders(entries)
                root.setText(self.COL["open_folder"], "全部")
                root.setToolTip(
                    self.COL["open_folder"],
                    f"单击打开本组 {len(unique_dirs)} 个不同文件夹"
                )
                root.setText(self.COL["actions"], "更多")
                root.setData(
                    0, Qt.ItemDataRole.UserRole, {"kind": "group", "code": code}
                )
                self.group_items[code] = root
                self._update_group_summary(code)
                font = root.font(0)
                font.setBold(True)
                root.setFont(0, font)
                for col in range(self.tree.columnCount()):
                    root.setBackground(col, self._group_header_brush)
                placeholder = QtWidgets.QTreeWidgetItem(root)
                placeholder.setText(0, "正在分批加载本组版本…")
                placeholder.setData(
                    0, Qt.ItemDataRole.UserRole, {"kind": "placeholder"}
                )
                # 用户原本的设计：所有重复组默认展开，无需逐组点击。
                # 信号回调可能同步触发；_child_pending_codes 防止重复入队。
                root.setExpanded(True)
                self._group_expanded(root)
                actual_end += 1
                if time.monotonic() >= deadline:
                    break
        finally:
            self.tree.setUpdatesEnabled(True)
        self._render_offset = actual_end
        if actual_end < len(self._render_groups):
            QtCore.QTimer.singleShot(12, lambda: self._render_group_batch(token))
        else:
            self._selection_changed()

    def _update_group_summary(self, code: str):
        """更新分组的扫描文件路径和已读取的视频参数摘要。"""
        root = self.group_items.get(code)
        if root is None:
            return
        entries = self.duplicates.get(code, [])
        sources = [next((row for row in group if row.info.get("verified")), group[0])
                   for group in version_groups(entries)]
        total = len(sources)
        checked = [row for row in sources if row.info.get("verified")]
        cached = sum("缓存" in row.info_status for row in checked)
        # 本列显示扫描目录中真实存在的文件路径，不是视频探测状态，
        # 也不是 STRM 首行指向的远程/本地视频源地址。
        media_paths = [
            entry.media_path for entry in entries
            if entry.media_type in ("视频", "STRM")
        ]
        if media_paths:
            visible_paths = "；".join(media_paths[:2])
            if len(media_paths) > 2:
                visible_paths += f"；…（其余 {len(media_paths) - 2} 个，展开查看）"
            root.setText(2, visible_paths)
            root.setToolTip(
                2,
                "本组扫描到的重复视频/STRM 文件路径：\n"
                + "\n".join(media_paths[:25])
                + (f"\n……其余 {len(media_paths) - 25} 条请展开查看" if len(media_paths) > 25 else "")
                + f"\n\n探测进度：已读 {len(checked)}/{total}"
                + (f"；缓存 {cached}" if cached else ""),
            )
        else:
            root.setText(2, "—（仅 NFO，无视频文件）")
            root.setToolTip(2, "此重复组只有 NFO 元数据，没有视频或 STRM 文件。")
        resolutions = sorted({
            str(row.info.get("resolution")) for row in checked if row.info.get("resolution")
            and row.info.get("resolution") != "—"
        })
        fps_values = sorted({
            str(row.info.get("fps")) for row in checked if row.info.get("fps")
            and row.info.get("fps") != "—"
        })
        root.setText(3, " / ".join(resolutions[:3]) + ("…" if len(resolutions) > 3 else ""))
        root.setText(4, " / ".join(fps_values[:3]) + ("…" if len(fps_values) > 3 else ""))
        # 探测信息已放在路径列的悬停提示中，不覆盖上述扫描路径。
        color = COLORS["green"] if checked and len(checked) == total else COLORS["muted"]
        root.setForeground(2, QtGui.QBrush(QtGui.QColor(color)))

    def _group_expanded(self, group_item: QtWidgets.QTreeWidgetItem):
        """自动/手动展开同一路径：子项统一入队，避免数千个 QTimer 竞争。"""
        data = group_item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict) or data.get("kind") != "group":
            return
        code = data.get("code", "")
        if code in self._loaded_groups or code in self._child_pending_codes:
            return
        # 在首条真实子行插入前保留占位项，避免 QTreeWidget 在短暂的
        # "0 个子项" 状态下收回展开箭头，导致自动展开状态意外丢失。
        has_placeholder = False
        if group_item.childCount():
            first_data = group_item.child(0).data(0, Qt.ItemDataRole.UserRole)
            has_placeholder = (
                isinstance(first_data, dict) and first_data.get("kind") == "placeholder"
            )
        resume_offset = group_item.childCount() - (1 if has_placeholder else 0)
        entries = sorted(
            self.duplicates.get(code, []),
            key=lambda entry: (
                -(entry.info.get("height") or 0),
                -(entry.info.get("size") or 0),
                ntpath.basename(entry.media_path).casefold(),
            ),
        )
        if resume_offset >= len(entries):
            self._loaded_groups.add(code)
            return
        self._child_pending_codes.add(code)
        self._child_jobs.append((self._render_token, group_item, code, entries, resume_offset))
        if not self._child_render_timer.isActive():
            self._child_render_timer.start(0)

    def _render_children_queue(self):
        """单个定时器公平地分片填充所有已展开的组，Qt 绘制仍在主线程。"""
        if self.closing_requested:
            return
        deadline = time.monotonic() + 0.008
        rows_added = 0
        self.tree.setUpdatesEnabled(False)
        try:
            while self._child_jobs and rows_added < 60:
                token, root, code, entries, offset = self._child_jobs.popleft()
                if token != self._render_token or not root.isExpanded():
                    self._child_pending_codes.discard(code)
                    continue
                # 每次仅服务少量子行后轮换到另一组，防止大组独占界面。
                limit = min(offset + 16, len(entries))
                while offset < limit and rows_added < 60:
                    entry = entries[offset]
                    child = QtWidgets.QTreeWidgetItem(root)
                    # 确保任何时刻父组至少有一个子项；首条媒体就位后
                    # 再移除占位项，后续追加不会引起树结构塌缩。
                    if root.childCount() >= 2:
                        first_data = root.child(0).data(0, Qt.ItemDataRole.UserRole)
                        if (isinstance(first_data, dict)
                                and first_data.get("kind") == "placeholder"):
                            root.takeChild(0)
                    child.setData(
                        0, Qt.ItemDataRole.UserRole,
                        {"kind": "media", "id": entry.identifier}
                    )
                    child.setText(
                        0, ntpath.basename(entry.folder) or ntpath.basename(entry.media_path)
                    )
                    if entry.disc is not None:
                        child.setText(0, child.text(0) + f" · CD{entry.disc}")
                    self.tree_items[entry.identifier] = child
                    self._set_entry_cells(child, entry)
                    if self._render_saved_id == entry.identifier:
                        self.tree.setCurrentItem(child)
                    offset += 1
                    rows_added += 1
                    if time.monotonic() >= deadline:
                        break
                if offset < len(entries):
                    self._child_jobs.append((token, root, code, entries, offset))
                else:
                    self._child_pending_codes.discard(code)
                    self._loaded_groups.add(code)
                if time.monotonic() >= deadline:
                    break
        finally:
            self.tree.setUpdatesEnabled(True)
        if self._child_jobs and not self.closing_requested:
            self._child_render_timer.start(12)

    def _set_entry_cells(self, item: QtWidgets.QTreeWidgetItem, entry: MediaEntry):
        info = entry.info
        # media_path 是扫描结果里的文件（STRM 时为 .strm 本身）。
        # target 是其指向/播放的视频源，此处绝不能使用 target。
        if entry.media_type in ("视频", "STRM"):
            item.setText(2, entry.media_path)
            item.setToolTip(
                2, f"扫描文件路径：{entry.media_path}\n探测状态：{entry.info_status}"
            )
        else:
            item.setText(2, "—（仅 NFO，无视频文件）")
            item.setToolTip(
                2, f"仅有 NFO 元数据：{entry.media_path}\n状态：{entry.info_status}"
            )
        media_label = entry.media_type
        if entry.media_type in ("STRM", "视频"):
            media_label += " · 有NFO" if entry.nfo_path else " · 单文件"
        item.setText(self.COL["type"], media_label)
        item.setText(self.COL["quality"], str(info.get("resolution") or "—"))
        item.setText(self.COL["fps"], str(info.get("fps") or "—"))
        item.setText(self.COL["codec"], str(info.get("codec") or "—"))
        item.setText(self.COL["size"], human_size(info.get("size")))
        item.setText(self.COL["duration"], human_duration(info.get("duration")))
        if entry.target_kind in ("path", "url"):
            item.setText(self.COL["play"], "播放")
        else:
            item.setText(self.COL["play"], "—")
        item.setText(self.COL["open_folder"], "打开")
        item.setText(self.COL["actions"], "更多")
        item.setText(self.COL["source"], entry.target or entry.media_path)
        item.setToolTip(
            self.COL["source"], f"{entry.target or entry.media_path}\n{entry.info_status}"
        )
        item.setToolTip(self.COL["play"], "单击播放，不会额外扫描 CD2 视频")
        item.setToolTip(
            self.COL["open_folder"], f"单击打开本文件所在文件夹：{entry.folder}"
        )
        item.setToolTip(self.COL["actions"], "单击打开本版本的其他操作")
        if entry.target_kind in ("unmapped", "invalid"):
            item.setForeground(
                self.COL["source"], QtGui.QBrush(QtGui.QColor(COLORS["red"]))
            )
        elif entry.info.get("verified"):
            item.setForeground(
                self.COL["quality"], QtGui.QBrush(QtGui.QColor(COLORS["green"]))
            )

    def _selected_entry(self) -> MediaEntry | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("kind") == "media":
            return self.records_by_id.get(data.get("id"))
        return None

    def _selected_entries(self) -> list[MediaEntry]:
        """Ctrl、Shift、Ctrl+A 多选；分组与子项重复选中时只入队一次。"""
        selection = self.tree.selectedItems()
        if not selection and self.tree.currentItem():
            selection = [self.tree.currentItem()]
        entries = []
        seen = set()
        for item in selection:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not isinstance(data, dict):
                continue
            if data.get("kind") == "media":
                candidates = [self.records_by_id.get(data.get("id"))]
            elif data.get("kind") == "group":
                candidates = self.duplicates.get(data.get("code", ""), [])
            else:
                candidates = []
            for entry in candidates:
                if entry is not None and entry.identifier not in seen:
                    seen.add(entry.identifier)
                    entries.append(entry)
        return entries

    def _selection_changed(self):
        """不再跳到下方详情面板：只在表格状态栏提示最短操作说明。"""
        if self._task_active():
            return
        entry = self._selected_entry()
        if entry is not None:
            self._set_status(
                f"{entry.code}  ·  {entry.media_type}  ·  {entry.info_status}  |  "
                "单击播放或文件夹图标直接操作；播放检查完即可手动清理"
            )
        else:
            selected = self.tree.currentItem()
            if selected is None:
                return
            data = selected.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get("kind") == "group":
                self._set_status(
                    f"{data.get('code')}: 单击「文件夹」一次打开整组目录；"
                    "点击展开箭头查看版本"
                )

    def _cell_clicked(self, item, column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict) or data.get("kind") == "placeholder":
            return
        if column == self.COL["play"] and data.get("kind") == "media":
            self.play_selected()
        elif column == self.COL["open_folder"]:
            if data.get("kind") == "group":
                self.open_group_folders(data.get("code", ""))
            elif data.get("kind") == "media":
                self.open_scrape_folder()
        elif column == self.COL["actions"]:
            cell = self.tree.visualItemRect(item)
            position = QtCore.QPoint(
                max(10, min(self.tree.viewport().width() - 10,
                            self.tree.columnViewportPosition(self.COL["actions"]) + 26)),
                cell.center().y(),
            )
            self._context_menu(position)

    def _double_clicked(self, item, column):
        # 操作按钮单击已执行；双击不重复打开播放器或文件夹。
        if column in (self.COL["play"], self.COL["open_folder"], self.COL["actions"]):
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        if data.get("kind") == "group":
            item.setExpanded(not item.isExpanded())
        elif data.get("kind") == "media":
            self.play_selected()

    def open_group_folders(self, code: str):
        # 不依赖树节点是否展开，使用完整分组资料；一次发出所有不同目录。
        folders = unique_group_folders(self.duplicates.get(code, []))
        self._open_folders(folders, code)

    def _open_folders(self, folders: list[str], label=""):
        if self.closing_requested:
            return
        if self.folder_worker is not None:
            if self.folder_worker.isRunning():
                self._set_status("文件管理器正在处理上次的目录打开请求")
                return
            self.folder_worker.deleteLater()
            self.folder_worker = None
        if not folders:
            return
        if len(folders) > 30:
            QtWidgets.QMessageBox.warning(
                self, "避免打开过多文件管理器标签",
                f"{label} 涉及 {len(folders)} 个目录，超过本工具单次打开上限 30。"
                "为避免文件管理器瞬间打开太多标签，请按版本分批操作。"
            )
            return
        if len(folders) > 5:
            reply = QtWidgets.QMessageBox.question(
                self, "确认打开多目录",
                f"{label} 包含 {len(folders)} 个不同文件夹，是否全部打开？",
                QtWidgets.QMessageBox.StandardButton.Yes |
                QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if reply != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self.folder_worker = FolderOpenWorker(folders)
        self.folder_worker.opened.connect(
            lambda count, manager: self._set_status(
                f"已通过{manager}请求打开 {count}/{len(folders)} 个独立文件夹"
            )
        )
        self.folder_worker.errors.connect(
            lambda text: QtWidgets.QMessageBox.warning(
                self, "部分文件夹无法打开", text
            )
        )
        self.folder_worker.finished.connect(self._folders_finished)
        self.folder_worker.start()

    def _folders_finished(self):
        worker = self.sender()
        if worker is self.folder_worker:
            worker.stop_event.clear()
            worker.deleteLater()
            self.folder_worker = None
        if self.closing_requested:
            QtCore.QTimer.singleShot(0, self.close)

    def _header_clicked(self, index):
        if index not in (self.COL["title"], self.COL["count"]):
            return
        if index == self.sort_column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = index
            self.sort_reverse = index == self.COL["count"]
        self.render_results()


    def _jobs_from_entries(self, entries: list[MediaEntry]) -> list[tuple[str, str]]:
        """同一个 CD2 视频无论被多少 STRM 引用，都只读取一次。"""
        jobs = []
        seen = set()
        for entry in entries:
            if entry.target_kind not in ("path", "url") or not entry.target:
                continue
            key = target_identity(entry.target, entry.target_kind)
            if key not in seen:
                seen.add(key)
                jobs.append((entry.target, entry.target_kind))
        return jobs

    def _visible_duplicated_entries(self) -> list[MediaEntry]:
        """直接从内存中的重复组获得全量数据，不依赖懒加载树节点。"""
        minimum = self.minimum_spin.value()
        keyword = self.filter_input.text().strip().casefold()
        entries = []
        for code, rows in self.duplicates.items():
            if version_count(rows) < minimum:
                continue
            if not group_matches_query(code, rows, keyword):
                continue
            entries.extend(rows)
        return entries

    def _start_info_jobs(self, jobs: list[tuple[str, str]], scope: str):
        if self._task_active() or self.closing_requested:
            return
        if not jobs:
            QtWidgets.QMessageBox.information(
                self, "没有可读取的视频",
                "当前范围中没有已映射的 STRM / 本地视频；"
                "若 STRM 内是 /mnt/... 请先处理本地盘符映射。"
            )
            return
        count = len(jobs)
        if count > 10:
            confirm = QtWidgets.QMessageBox.question(
                self, "批量探测 CD2 视频",
                f"{scope}共有 {count} 个不同的视频源。\n\n"
                f"并发：{self.concurrent_spin.value()}，单源超时：20秒。\n"
                "读取容器/轨道元数据，优先复用14天缓存；"
                "批量探测可能仍会触发115服务端限流。\n\n继续吗？",
                QtWidgets.QMessageBox.StandardButton.Yes |
                QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self._current_probe_stats = {}
        worker = ProbeWorker(
            jobs, self.cache_file,
            refresh=self.refresh_check.isChecked(),
            timeout=20,
            concurrency=self.concurrent_spin.value(),
        )
        worker.batch_ready.connect(self._receive_probe_batch)
        worker.statistics.connect(self._receive_probe_statistics)
        # _begin_task 自动连接 progress/error/finished。
        self._begin_task(
            worker, f"已创建 {count} 条探测任务；CD2 同时最多 {self.concurrent_spin.value()} 条"
        )

    def get_video_info(self):
        self._start_info_jobs(
            self._jobs_from_entries(self._selected_entries()),
            "选中范围",
        )

    def get_all_video_info(self):
        # “全部重复视频”按当前筛选结果，不受树节点按需加载的影响。
        self._start_info_jobs(
            self._jobs_from_entries(self._visible_duplicated_entries()),
            "当前全部重复组",
        )

    def _receive_probe_batch(self, batch):
        # 只更新已显示的行，折叠组只更新分组摘要，避免批量读取时重建整棵树。
        affected_groups = set()
        self.tree.setUpdatesEnabled(False)
        try:
            for target, info, state in batch:
                key = target_identity(target)
                for entry in self.rows_by_target.get(key, []):
                    if info.get("verified"):
                        entry.info = dict(info)
                        entry.info_status = state
                    elif entry.info.get("verified"):
                        # 主动重测失败也不能清除已成功的历史实测数据。
                        entry.info_status = "本次探测失败（保留旧参数）：" + state
                    else:
                        entry.info = dict(info)
                        entry.info_status = state
                    item = self.tree_items.get(entry.identifier)
                    if item is not None:
                        self._set_entry_cells(item, entry)
                    group_name = self.group_for_entry.get(entry.identifier)
                    if group_name is not None:
                        affected_groups.add(group_name)
            for group_name in affected_groups:
                self._update_group_summary(group_name)
        finally:
            self.tree.setUpdatesEnabled(True)

    def _receive_probe_statistics(self, result):
        self._current_probe_stats = dict(result)

    def _require_single_media(self) -> MediaEntry | None:
        entry = self._selected_entry()
        if entry is None:
            QtWidgets.QMessageBox.information(
                self, "请选中具体版本", "请选中番号分组下面的一条实际 STRM/视频记录。"
            )
        return entry

    def play_selected(self):
        """以独立 mpvnet 进程播放，关闭播放器窗口后更易释放文件占用。

        不强制结束播放器或云盘驱动，避免误伤用户另开的播放任务。
        加速试验仍可在工具栏关闭，不读取 CD2 目标文件做额外 stat。
        """
        entry = self._require_single_media()
        if entry is None:
            return
        if entry.target_kind not in ("path", "url") or not entry.target:
            QtWidgets.QMessageBox.warning(
                self, "不能播放", "STRM 视频地址未映射或无效，请检查 STRM 内容。"
            )
            return
        player = resource_path("mpvnet.exe")
        if not os.path.isfile(player):
            player = "mpvnet"
        # mpv.net single 模式会复用正在运行的播放器，关闭窗口后
        # 旧进程可能仍持有媒体；multi 让本次播放使用独立进程。
        # 仅发送启动请求；不调用 taskkill、不关闭别的程序句柄。
        args = [player, "--process-instance=multi"]
        if self.fastplay_check.isChecked():
            args.extend([
                "--cache-pause-initial=no",
                "--cache-pause-wait=0.5",
            ])
            extension = ntpath.splitext(
                urllib.parse.urlsplit(entry.target).path if ANY_URI.match(entry.target)
                else entry.target
            )[1].casefold()
            if extension in (".mkv", ".mp4", ".m4v", ".mov"):
                # ⚠️ 请确认此用法：部分特殊 MKV/MP4 可能需要更大探测量。
                # 如果出现音轨/字幕识别不全，关闭工具栏加速选项即可恢复默认。
                args.extend([
                    "--demuxer-lavf-probesize=2MiB",
                    "--demuxer-lavf-analyzeduration=0.5",
                ])
        args.append(entry.target)
        try:
            subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )
            self._set_status(
                f"{entry.code} 已交给 mpvnet 独立播放进程"
                + ("（首帧加速试验已开启）" if self.fastplay_check.isChecked() else "")
                + "；关闭播放器窗口后再删除，若 CD2 仍占用会安全重试"
            )
        except FileNotFoundError:
            try:
                os.startfile(entry.target)
                self._set_status(
                    "没有找到 mpvnet.exe，已交给 Windows 默认播放器"
                )
            except OSError as exc:
                QtWidgets.QMessageBox.warning(
                    self, "播放器未找到",
                    f"无法启动 mpvnet / 默认播放器：{exc}\n"
                    "建议在 mpv.net 的 Setup 中将 mpvnet 加入 PATH。"
                )
        except OSError as exc:
            QtWidgets.QMessageBox.warning(
                self, "启动失败", f"mpvnet 启动失败：{exc}"
            )

    def open_scrape_folder(self):
        entry = self._require_single_media()
        if entry is not None:
            self._open_folders([entry.folder], entry.code)

    def copy_video_path(self):
        entry = self._require_single_media()
        if entry is None:
            return
        QtWidgets.QApplication.clipboard().setText(
            entry.target or entry.raw_target or entry.media_path
        )
        self._set_status("视频源地址已复制到剪贴板")

    def _context_menu(self, point):
        item = self.tree.itemAt(point)
        if item is None:
            return
        self.tree.setCurrentItem(item)
        data = item.data(0, Qt.ItemDataRole.UserRole)
        selected = (
            self.records_by_id.get(data.get("id"))
            if isinstance(data, dict) and data.get("kind") == "media" else None
        )
        menu = QtWidgets.QMenu(self)
        if selected is None:
            code = data.get("code", "") if isinstance(data, dict) else ""
            menu.addAction(
                "打开这一组的全部文件夹",
                lambda: self.open_group_folders(code),
            )
            menu.addAction(
                "读取这一组的真实视频信息",
                self.get_video_info,
            )
            menu.addAction("读取当前筛选中的全部重复视频", self.get_all_video_info)
            menu.addSeparator()
            hint = menu.addAction("删除请先展开分组，右键具体视频 / STRM")
            hint.setEnabled(False)
            menu.addAction(
                "展开 / 折叠",
                lambda: item.setExpanded(not item.isExpanded())
            )
        else:
            menu.addAction("▶ 播放视频", self.play_selected)
            menu.addAction("获取真实视频分辨率 / 帧率", self.get_video_info)

            menu.addAction("打开本 STRM / NFO 文件夹", self.open_scrape_folder)
            menu.addAction("复制真实视频地址", self.copy_video_path)
            menu.addSeparator()
            if self._task_active():
                action = menu.addAction("请先停止当前任务，再进行合并删除")
                action.setEnabled(False)
            elif selected.media_type in ("STRM", "视频"):
                action = menu.addAction(
                    "⚠ 删除重复项（先预检，自动选择单文件 / 整目录）"
                )
                action.triggered.connect(
                    lambda _checked=False, entry=selected:
                    self.start_merge_delete_precheck(entry)
                )
            menu.addAction(
                "查看合并删除日志目录",
                lambda: self._open_folders([str(self.merge_log_file.parent)], "删除日志"),
            )
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def start_merge_delete_precheck(self, entry: MediaEntry | None = None):
        """右键动作绑定具体条目；预检前绝不修改文件。"""
        if entry is None:
            entry = self._require_single_media()
        if entry is None or self._task_active() or self.closing_requested:
            return
        if self.records_by_id.get(entry.identifier) is not entry:
            QtWidgets.QMessageBox.warning(
                self, "记录已变化", "所选条目已变化，请重新扫描后再删除。"
            )
            return
        if entry.media_type not in ("STRM", "视频"):
            QtWidgets.QMessageBox.information(
                self, "不能合并删除", "请先选择一个具体的 STRM 或普通视频。"
            )
            return
        operation_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(4)
        worker = MergeDeleteWorker(
            "precheck", entry, self.records, self.directories,
            self.path_mapping.get("source_prefix", ""),
            self.path_mapping.get("target_prefix", ""),
            operation_id,
        )
        self._begin_task(
            worker,
            (
                f"删除预检：{entry.code}；先核查 STRM 指向的真实视频，"
                "再决定是删除 STRM 文件还是整个专属目录"
                if entry.media_type == "STRM" else
                f"删除预检：检查 {entry.code} 目录，存在其他视频时仅删除目标文件"
            ),
        )

    def _apply_incremental_changes(
        self, *, completed_steps=(), moved_paths=(), replacements=(), nfo_deleted_count=0,
    ) -> int:
        """只调整本次扫描的内存记录及树视图，绝不再次遍历两万个目录。"""
        from bisect import bisect_right
        removed_files = {nt_key(path) for path in moved_paths}
        removed_folders = []
        for step in completed_steps:
            if "：" not in step:
                continue
            label, path = step.split("：", 1)
            if label in ("真实视频单文件", "STRM 单文件", "视频单文件"):
                removed_files.add(nt_key(path))
            elif label in ("STRM 刮削目录", "视频所在文件夹"):
                removed_folders.append(path)
        updates = {item["destination_id"]: item for item in replacements}
        if not removed_files and not removed_folders and not updates:
            return 0
        folder_prefixes = sorted({nt_key(folder).rstrip("\\/") + "\\"
                                  for folder in removed_folders})

        def folder_was_removed(path_key):
            if not folder_prefixes:
                return False
            index = bisect_right(folder_prefixes, path_key) - 1
            return index >= 0 and path_key.startswith(folder_prefixes[index])

        retained = []
        removed_ids = set()
        removed_rows = []
        for entry in self.records:
            key = nt_key(entry.media_path)
            deleted = key in removed_files or folder_was_removed(key)
            if deleted:
                removed_rows.append(entry)
                removed_ids.add(entry.identifier)
                continue
            updated = updates.get(entry.identifier)
            if updated:
                old_identity = target_identity(entry.target, entry.target_kind)
                entry.raw_target = updated["raw"]
                entry.target = updated["target"]
                entry.target_kind = updated["kind"]
                if old_identity != target_identity(entry.target, entry.target_kind):
                    entry.info = {}
                entry.info_status = "移动覆盖后已更新（未重新扫描）"
            elif entry.target_kind == "path" and nt_key(entry.target) in removed_files:
                entry.info = {}
                entry.info_status = "源视频已被删除；STRM 仍在，须人工核查"
            retained.append(entry)
        self.records = retained
        self.records_by_id = {entry.identifier: entry for entry in retained}
        # 保留之前的精确/系列匹配组合；只剔除已成功删除的条目。
        updated_groups = {}
        for code, group in self.duplicates.items():
            current = [row for row in group if row.identifier not in removed_ids]
            if version_count(current) >= 2:
                updated_groups[code] = current
        self.duplicates = updated_groups
        self.rows_by_target = defaultdict(list)
        for entry in retained:
            if entry.target and entry.target_kind in ("path", "url"):
                self.rows_by_target[target_identity(entry.target, entry.target_kind)].append(entry)
        self.scan_counts = (
            max(0, self.scan_counts[0] - int(nfo_deleted_count)),
            max(0, self.scan_counts[1] - sum(e.media_type == "STRM" for e in removed_rows)),
            max(0, self.scan_counts[2] - sum(e.media_type == "视频" for e in removed_rows)),
        )
        # render_results 只更新 QTreeWidget，不执行 scan_library 或磁盘 I/O。
        self.render_results()
        self._update_buttons()
        return len(removed_rows)

    def _handle_merge_stage(self, result: dict):
        """QThread 已结束；才在主线程弹出确认对话框。"""
        if self.closing_requested:
            return
        phase = result.get("phase")
        success = result.get("success", False)
        log_file = str(self.merge_log_file)
        if not success:
            error = result.get("error", "未知错误")
            title = "合并删除已阻止" if phase == "precheck" else "合并删除未完成"
            self._set_status(f"{title}：{error}")
            QtWidgets.QMessageBox.warning(
                self, title,
                f"{error}\n\n审计日志：\n{log_file}\n\n"
                "如果是 WinError 32/33，请先完全退出播放器，"
                "等待 CD2/云盘驱动释放文件占用，再重新尝试。\n"
                "程序不会强行结束其他进程，也不会冒险继续清理 STRM。\n"
                "若出现部分删除，请按日志人工核对。"
            )
            if phase == "execute":
                plan = result.get("plan") or {}
                finished = result.get("completed", [])
                nfo_deleted = (
                    ((plan.get("scrape_folder_contents") or {}).get("nfo_count", 0)
                     if plan.get("media_type") == "STRM" else
                     (plan.get("video_folder_contents") or {}).get("nfo_count", 0))
                    if any("目录：" in item for item in finished) else 0
                )
                self._apply_incremental_changes(
                    completed_steps=finished, nfo_deleted_count=nfo_deleted,
                )
            return
        if phase == "precheck":
            plan = result.get("plan")
            if not isinstance(plan, dict):
                return
            self._confirm_merge_plan(plan)
        elif phase == "execute":
            completed = result.get("completed", [])
            self._set_status(
                f"合并删除完成：{result.get('plan', {}).get('code', '')}；"
                f"共处理 {len(completed)} 个目标"
            )
            QtWidgets.QMessageBox.information(
                self, "合并删除完成",
                "操作步骤：\n" + "\n".join(completed)
                + f"\n\n日志：\n{log_file}"
                + "\n\n查重列表已经同步更新，无需重新扫描。"
            )
            plan = result.get("plan") or {}
            nfo_deleted = (
                ((plan.get("scrape_folder_contents") or {}).get("nfo_count", 0)
                 if plan.get("media_type") == "STRM" else
                 (plan.get("video_folder_contents") or {}).get("nfo_count", 0))
                if any("目录：" in item for item in completed) else 0
            )
            self._apply_incremental_changes(
                completed_steps=completed, nfo_deleted_count=nfo_deleted,
            )

    def _confirm_merge_plan(self, plan: dict):
        """预检通过后仅弹出一次是/否确认，不要求键入字符。"""
        if self._task_active() or self.closing_requested:
            return
        kind = plan["media_type"]
        entire_folder = plan["cleanup_mode"] == "folder"
        if kind == "STRM":
            second_target = (
                plan["scrape_folder"] if entire_folder else plan["media_path"]
            )
            details = (
                "执行顺序（均为永久删除）：\n"
                f"1. STRM 指向的真实视频文件：\n{plan['source_video']}\n\n"
                f"2. {'整个 STRM 所在目录' if entire_folder else '仅 STRM 文件'}：\n"
                f"{second_target}\n"
            )
            summary = (
                "不会遍历或清空 STRM 真实视频的父目录。\n"
                "源视频若被其他扫描记录引用，预检会拒绝。"
            )
        else:
            target = plan["video_folder"] if entire_folder else plan["media_path"]
            details = (
                f"执行操作（永久删除{'整个文件夹' if entire_folder else '单个视频文件'}）：\n"
                f"{target}\n"
            )
            summary = ""
        summary += "\n目录处理依据：" + plan["cleanup_reason"]
        if entire_folder:
            summary += "\n目录中的 NFO、图片、字幕等其它文件也会被删除。"
            folder_stats = (plan.get("scrape_folder_contents") if kind == "STRM"
                            else plan.get("video_folder_contents")) or {}
            if folder_stats.get("trailer_count", 0):
                summary += (
                    f"\n⚠ 本目录还有 {folder_stats['trailer_count']} 个 trailer 预告片，"
                    "它们不算重复视频，但也会随整个目录永久删除。"
                    f"\n例如：{folder_stats.get('trailer_example', '')}"
                )
        else:
            summary += "\n所在目录和其中其它文件均保留。"
        summary += (
            "\n\n⚠ 115 / CD2 的源文件删除可能直接影响云端原件！"
            "\n所有删除都是永久删除，不进入回收站，不保证可恢复。"
            "\n执行前会再次完整预检，防止确认期间新增其它媒体文件。"
        )
        prompt = QtWidgets.QMessageBox.question(
            self, f"确认永久合并删除 {plan['code']}",
            details + "\n" + summary + "\n\n是否继续？",
            QtWidgets.QMessageBox.StandardButton.Yes |
            QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if prompt != QtWidgets.QMessageBox.StandardButton.Yes:
            self.writer.submit_merge_event(
                plan["operation_id"], "CONFIRM_CANCELLED",
                plan=plan, reason="用户在删除确认框中选择否"
            )
            self._set_status("已取消合并删除；所有文件保持不变")
            return
        entry = self.records_by_id.get(plan["identifier"])
        if entry is None:
            self.writer.submit_merge_event(
                plan["operation_id"], "EXECUTE_ABORTED",
                plan=plan, reason="条目在预检后消失"
            )
            return
        worker = MergeDeleteWorker(
            "execute", entry, self.records, self.directories,
            self.path_mapping.get("source_prefix", ""),
            self.path_mapping.get("target_prefix", ""),
            plan["operation_id"], confirmed_plan=plan,
        )
        self._begin_task(
            worker,
            f"执行合并删除：{plan['code']}；正在重新核查后永久删除，日志保存在 {self.merge_log_file}",
        )


    def start_bulk_cleanup_precheck(self):
        """从本次扫描的根目录中选择两侧，再对全部番号重复组预检。"""
        if self._task_active() or self.closing_requested:
            return
        if self._last_scan_mode != "番号" or self.field_combo.currentText() != "番号":
            QtWidgets.QMessageBox.warning(
                self, "请按番号重新扫描",
                "批量永久删除仅支持按番号查重，避免系列近似匹配误删不同作品。"
            )
            return
        if not self.duplicates:
            QtWidgets.QMessageBox.information(self, "没有重复组", "请先完成快速查重。")
            return
        if (not self._completed_scan_roots or
                tuple(map(nt_key, self.directories)) !=
                tuple(map(nt_key, self._completed_scan_roots))):
            QtWidgets.QMessageBox.warning(
                self, "扫描目录已变化",
                "扫描目录在上次查重后有调整，请重新执行快速查重再批量清理。"
            )
            return
        scan_roots = list(self._completed_scan_roots)
        if len(scan_roots) < 2:
            QtWidgets.QMessageBox.warning(
                self, "需要两个扫描目录",
                "请至少添加两个相互独立的扫描目录，"
                "一个用于删除重复项，另一个用于保留对应副本，然后重新扫描。"
            )
            return

        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("批量清理 - 从扫描目录中选择")
        dialog.setMinimumWidth(750)
        layout = QtWidgets.QVBoxLayout(dialog)
        message = QtWidgets.QLabel(
            f"当前扫描得到 {len(self.duplicates)} 个重复番号组（不受筛选影响）。\n"
            "请选择「要删除的一侧」及「必须保留的一侧」；两边不能相同或嵌套。\n"
            "下一步只进行预检；最终确认后将删除待删侧 STRM 作品目录，"
            "以及 STRM 指向的 115/CD2 真实视频（两次源视频删除间隔至少 3 秒）。"
        )
        message.setWordWrap(True)
        layout.addWidget(message)
        form = QtWidgets.QFormLayout()
        delete_combo = QtWidgets.QComboBox(dialog)
        keep_combo = QtWidgets.QComboBox(dialog)
        delete_combo.addItem("— 请选择待清理的扫描目录 —", "")
        keep_combo.addItem("— 请选择要保留的扫描目录 —", "")
        for root in scan_roots:
            delete_combo.addItem(root, root)
            keep_combo.addItem(root, root)
        delete_combo.setMinimumWidth(530)
        keep_combo.setMinimumWidth(530)
        form.addRow("删除重复项所在目录：", delete_combo)
        form.addRow("保留另一份所在目录：", keep_combo)
        layout.addLayout(form)
        caution = QtWidgets.QLabel(
            "⚠ 115/CD2 挂载文件删除可能直接影响网盘原件；"
            "预检会跳过共享源视频、目录混有其他媒体以及保留副本无效的项目。"
        )
        caution.setWordWrap(True)
        layout.addWidget(caution)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        ok_button = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        ok_button.setText("开始安全预检")
        def check_user_choice():
            try:
                validate_bulk_roots(
                    delete_combo.currentData() or "",
                    keep_combo.currentData() or "",
                    scan_roots,
                )
            except MergeDeleteBlocked as error:
                QtWidgets.QMessageBox.warning(dialog, "目录选择无效", str(error))
                return
            dialog.accept()
        buttons.accepted.connect(check_user_choice)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        source_root = str(delete_combo.currentData())
        keep_root = str(keep_combo.currentData())
        operation_id = (
            datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            + "-" + secrets.token_hex(4)
        )
        worker = BulkDeleteWorker(
            "precheck", self.duplicates, self.records, scan_roots,
            self.path_mapping.get("source_prefix", ""),
            self.path_mapping.get("target_prefix", ""), operation_id,
            source_root=source_root, keep_root=keep_root,
        )
        self._begin_task(
            worker,
            f"预检全部 {len(self.duplicates)} 个重复组：删除侧 {source_root}；保留侧 {keep_root}",
        )

    def _handle_bulk_stage(self, result: dict):
        if self.closing_requested:
            return
        phase = result.get("phase")
        if phase == "precheck":
            if not result.get("success"):
                QtWidgets.QMessageBox.warning(
                    self, "批量预检未完成",
                    str(result.get("error", "预检被中断"))
                    + f"\n\n审计日志：{self.merge_log_file}",
                )
                return
            batch = result.get("batch")
            if batch:
                self._confirm_bulk_cleanup(batch)
            return
        if phase == "execute":
            outcome = result.get("outcome") or {}
            error = result.get("error") or outcome.get("error", "")
            deleted = outcome.get("deleted", [])
            skipped = outcome.get("skipped_runtime", [])
            stopped = result.get("stopped", False) or outcome.get("stopped", False)
            message = (
                f"计划 {outcome.get('total_plans', 0)} 项，成功删除 {len(deleted)} 项，"
                f"执行中跳过 {len(skipped)} 项。"
                + ("\n用户已请求停止。" if stopped else "")
                + (f"\n异常：{error}" if error else "")
                + f"\n\n预览：{outcome.get('preview_path', '')}"
                + f"\n审计日志：{self.merge_log_file}"
                + "\n\n请核对日志；删除 115/CD2 挂载视频不保证可恢复。"
            )
            self._set_status(message.replace("\n", "  "))
            if error or stopped:
                QtWidgets.QMessageBox.warning(self, "批量清理中止 / 部分完成", message)
            else:
                QtWidgets.QMessageBox.information(self, "批量清理完成", message)
            # 批量完成或部分完成都根据实际成功步骤更新内存，不重扫硬盘。
            steps = [step for done in deleted for step in done.get("steps", [])]
            steps.extend(outcome.get("partial_steps", []))
            nfo_deleted = sum(done.get("nfo_deleted", 0) for done in deleted)
            nfo_deleted += outcome.get("partial_nfo_deleted", 0)
            if steps:
                self._apply_incremental_changes(
                    completed_steps=steps, nfo_deleted_count=nfo_deleted,
                )

    def _confirm_bulk_cleanup(self, batch: dict):
        """两级确认：全量文本清单 -> 手工输入确认词。"""
        if self._task_active() or self.closing_requested:
            return
        ready = len(batch["plans"])
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("批量清理预览 - 真实 115/CD2 视频将被永久删除")
        dialog.resize(1050, 700)
        layout = QtWidgets.QVBoxLayout(dialog)
        explanation = QtWidgets.QLabel(
            f"全部重复组 {batch['all_groups']}；命中待删侧 {batch['matched_groups']} 组；"
            f"可整删 {ready} 项，跳过 {len(batch['skipped'])} 项。\n"
            f"待删扫描目录：{batch['source_root']}\n"
            f"必须保留目录：{batch['keep_root']}\n"
            "将删除待删侧作品目录及对应 115/CD2 真实视频；保留另一侧副本。"
            f"源视频删除间隔至少 {BULK_DELETE_DELAY_SECONDS:g} 秒。"
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        preview = QtWidgets.QPlainTextEdit(dialog)
        preview.setReadOnly(True)
        preview.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        preview.setPlainText(format_bulk_preview(batch))
        layout.addWidget(preview, 1)
        path_label = QtWidgets.QLabel(f"完整预检清单已保存到：{batch['preview_path']}")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        if ready:
            proceed = buttons.addButton(
                "已核对清单，进入最终确认",
                QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole,
            )
            proceed.setObjectName("primary")
            buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted or not ready:
            self.writer.submit_merge_event(
                batch["operation_id"], "BULK_CONFIRM_CANCELLED",
                preview_path=batch["preview_path"], planned=ready,
            )
            self._set_status("批量清理已取消：预检未执行任何删除")
            return
        word, accepted = QtWidgets.QInputDialog.getText(
            self, "最终确认：删除 115 网盘真实视频",
            f"将永久删除 {ready} 个 STRM 作品目录及其真实源视频。\n"
            f"待删目录：{batch['source_root']}\n"
            f"保留目录：{batch['keep_root']}\n"
            f"每次源视频删除之间间隔至少 {BULK_DELETE_DELAY_SECONDS:g} 秒。\n"
            "115/CD2 删除可能直接影响云端原件，不进入 Windows 回收站。\n\n"
            "如已核对所有真实视频路径，请输入：删除115源视频",
            QtWidgets.QLineEdit.EchoMode.Normal,
        )
        if not accepted or word.strip() != "删除115源视频":
            self.writer.submit_merge_event(
                batch["operation_id"], "BULK_CONFIRM_CANCELLED",
                reason="最终确认词未匹配或用户取消", planned=ready,
            )
            self._set_status("批量删除取消：最终确认词未匹配")
            return
        worker = BulkDeleteWorker(
            "execute", self.duplicates, self.records, self.directories,
            self.path_mapping.get("source_prefix", ""),
            self.path_mapping.get("target_prefix", ""),
            batch["operation_id"],
            source_root=batch["source_root"], keep_root=batch["keep_root"],
            confirmed_batch=batch,
        )
        self._begin_task(
            worker, f"批量执行：计划删除 {ready} 项；115/CD2 每条间隔至少 3 秒，按停止可中断",
        )

    def start_bulk_move_precheck(self):
        """先指定已扫描的源/目标根目录，再按重复番号核查整个作品文件夹。"""
        if self._task_active() or self.closing_requested:
            return
        if (self._last_scan_mode != "番号" or self.field_combo.currentText() != "番号"
                or not self._completed_scan_roots
                or tuple(map(nt_key, self.directories)) !=
                tuple(map(nt_key, self._completed_scan_roots))):
            QtWidgets.QMessageBox.warning(
                self, "请先按番号完成扫描",
                "移动覆盖只支持本次按番号扫描所得的重复组；目录更改后需重新扫描。",
            )
            return
        roots = list(self._completed_scan_roots)
        if len(roots) < 2:
            QtWidgets.QMessageBox.warning(
                self, "至少需要两个扫描目录",
                "请先扫描至少两个相互独立的根目录，分别选择来源和覆盖目标。",
            )
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("批量移动覆盖 - 选择源目录和目标目录")
        dialog.setMinimumWidth(770)
        layout = QtWidgets.QVBoxLayout(dialog)
        message = QtWidgets.QLabel(
            f"本次扫描发现 {len(self.duplicates)} 个重复番号组。\n"
            "从已扫描的根目录中选择「源」与「目标」，两个目录必须不同且不嵌套。\n"
            "每个番号只在两侧分别唯一定位到一个作品文件夹时才会进入预览。"
        )
        message.setWordWrap(True)
        layout.addWidget(message)
        form = QtWidgets.QFormLayout()
        source_combo = QtWidgets.QComboBox(dialog)
        dest_combo = QtWidgets.QComboBox(dialog)
        source_combo.addItem("— 请选择要移动的源扫描目录 —", "")
        dest_combo.addItem("— 请选择接收覆盖的目标扫描目录 —", "")
        for root in roots:
            source_combo.addItem(root, root)
            dest_combo.addItem(root, root)
        source_combo.setMinimumWidth(540)
        dest_combo.setMinimumWidth(540)
        form.addRow("源目录（要移走的作品）：", source_combo)
        form.addRow("目标目录（被覆盖的作品）：", dest_combo)
        layout.addLayout(form)
        note = QtWidgets.QLabel(
            "⚠ 覆盖是整个作品文件夹替换：目标旧目录内的 STRM、NFO、封面、字幕等"
            "都会被源目录的内容替换，而不是两边合并。源文件夹移到目标旧路径。\n"
            "不会删除或修改 STRM 所指向的 115/CD2 真实视频；先预检、勾选、最终确认才执行。"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("开始作品文件夹预检")
        def accept_roots():
            try:
                validate_move_roots(
                    source_combo.currentData() or "",
                    dest_combo.currentData() or "", roots,
                )
            except MergeDeleteBlocked as error:
                QtWidgets.QMessageBox.warning(dialog, "目录选择无效", str(error))
                return
            dialog.accept()
        buttons.accepted.connect(accept_roots)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        source_root = str(source_combo.currentData())
        destination_root = str(dest_combo.currentData())
        operation_id = (datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                        + "-" + secrets.token_hex(4))
        worker = BulkMoveWorker(
            "precheck", self.duplicates, self.records, roots,
            self.path_mapping.get("source_prefix", ""),
            self.path_mapping.get("target_prefix", ""), operation_id,
            source_root=source_root, destination_root=destination_root,
        )
        self._begin_task(
            worker,
            f"预检作品文件夹：源 {source_root} → 目标 {destination_root}",
        )

    def _apply_scoped_move_refresh(self, refresh: dict) -> int:
        """仅用后台读出的受影响作品目录结果替换内存记录，绝不重新扫描全库。"""
        from bisect import bisect_right
        paths = refresh.get("scopes", [])
        if not paths:
            return 0
        prefixes = sorted({nt_key(path).rstrip("\\/") + "\\" for path in paths})
        def affected(path: str) -> bool:
            normalized = nt_key(path)
            index = bisect_right(prefixes, normalized) - 1
            return index >= 0 and normalized.startswith(prefixes[index])
        old_infos = {}
        retained = []
        removed = []
        for entry in self.records:
            if affected(entry.media_path):
                removed.append(entry)
                if entry.info.get("verified"):
                    old_infos[target_identity(entry.target, entry.target_kind)] = (
                        dict(entry.info), entry.info_status,
                    )
            else:
                retained.append(entry)
        last_id = max((item.identifier for item in self.records), default=0)
        fresh = list(refresh.get("records", []))
        for entry in fresh:
            last_id += 1
            entry.identifier = last_id
            previous = old_infos.get(target_identity(entry.target, entry.target_kind))
            if previous:
                entry.info = dict(previous[0])
                entry.info_status = previous[1]
        self.records = retained + fresh
        self.records_by_id = {entry.identifier: entry for entry in self.records}
        # 仅在内存中重新匹配番号，复杂度随记录数线性增长，不进行全目录扫描。
        self.duplicates = group_duplicates(self.records, mode="番号")
        self.rows_by_target = defaultdict(list)
        for entry in self.records:
            if entry.target and entry.target_kind in ("path", "url"):
                self.rows_by_target[target_identity(entry.target, entry.target_kind)].append(entry)
        self.scan_counts = (
            max(0, self.scan_counts[0] +
                int(refresh["new_nfo_count"]) - int(refresh["old_nfo_count"])),
            max(0, self.scan_counts[1] - sum(e.media_type == "STRM" for e in removed)
                + sum(e.media_type == "STRM" for e in fresh)),
            max(0, self.scan_counts[2] - sum(e.media_type == "视频" for e in removed)
                + sum(e.media_type == "视频" for e in fresh)),
        )
        self.render_results()
        self._update_buttons()
        return len(removed)

    def _handle_bulk_move_stage(self, result: dict):
        if self.closing_requested:
            return
        phase = result.get("phase")
        if not result.get("success"):
            error = result.get("error") or "移动任务已中止"
            QtWidgets.QMessageBox.warning(
                self, "移动覆盖未完成", f"{error}\n\n审计日志：{self.merge_log_file}",
            )
            self._set_status(f"移动覆盖未完成：{error}")
            return
        if phase == "precheck":
            batch = result.get("batch")
            if batch:
                self._confirm_bulk_move(batch)
        elif phase == "execute":
            outcome = result.get("outcome") or {}
            refresh = outcome.get("refresh") or {}
            if refresh:
                self._apply_scoped_move_refresh(refresh)
            moved = outcome.get("moved", [])
            issues = outcome.get("warnings", []) + refresh.get("errors", [])
            error = outcome.get("error", "")
            message = (
                f"源：{outcome.get('source_root', '') or '已选源目录'}\n"
                f"计划 {outcome.get('total', 0)} 个作品文件夹，"
                f"已成功移动并整体覆盖 {len(moved)} 个。"
                + ("\n已按请求停止。" if outcome.get("stopped") else "")
                + (f"\n异常：{error}" if error else "")
                + ("\n提示：" + "；".join(issues[:5]) if issues else "")
                + "\n\n源作品目录已移动到目标目录原路径；目标旧目录内容整体被替换。"
                + "\n不会访问或删除 STRM 指向的 115/CD2 视频。"
                + "\n查重列表仅更新受影响作品文件夹，无需重新扫描整个库。"
                + f"\n预览：{outcome.get('preview_path', '')}"
                + f"\n审计日志：{self.merge_log_file}"
            )
            self._set_status(message.replace("\n", "  "))
            if error or issues or outcome.get("stopped"):
                QtWidgets.QMessageBox.warning(self, "文件夹移动覆盖部分完成 / 已停止", message)
            else:
                QtWidgets.QMessageBox.information(self, "文件夹批量移动覆盖完成", message)

    def _confirm_bulk_move(self, batch: dict):
        if self._task_active() or self.closing_requested:
            return
        plans = batch["plans"]
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("批量移动覆盖 · 勾选作品文件夹")
        dialog.resize(1160, 700)
        layout = QtWidgets.QVBoxLayout(dialog)
        hint = QtWidgets.QLabel(
            f"源：{batch['source_root']}\n目标：{batch['destination_root']}\n"
            f"全部重复组 {batch['group_count']}，两侧匹配 {batch['matched_groups']}，"
            f"可选 {len(plans)} 个作品文件夹，跳过 {len(batch['skipped'])}。\n"
            "每项操作会把源作品文件夹移动到目标作品文件夹原路径，"
            "用源目录整个替换目标旧目录（包括 NFO、封面、字幕及 STRM）。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        search = QtWidgets.QLineEdit(dialog)
        search.setPlaceholderText("筛选番号或文件夹路径；下方选择操作只对当前筛选结果生效")
        layout.addWidget(search)
        model = BulkMoveSelectionModel(plans, dialog)
        proxy = QtCore.QSortFilterProxyModel(dialog)
        proxy.setSourceModel(model)
        proxy.setFilterKeyColumn(-1)
        proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        search.textChanged.connect(proxy.setFilterFixedString)
        view = QtWidgets.QTableView(dialog)
        view.setModel(proxy)
        view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        view.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.AllEditTriggers)
        view.setAlternatingRowColors(True)
        view.setSortingEnabled(False)
        view.verticalHeader().hide()
        view.setColumnWidth(0, 52)
        view.setColumnWidth(1, 128)
        view.setColumnWidth(2, 345)
        view.setColumnWidth(3, 345)
        view.setColumnWidth(4, 210)
        layout.addWidget(view, 1)
        bar = QtWidgets.QHBoxLayout()
        selected_label = QtWidgets.QLabel(f"已勾选 {len(plans)} / {len(plans)}")
        model.count_changed.connect(
            lambda count: selected_label.setText(f"已勾选 {count} / {len(plans)}")
        )
        select_btn = QtWidgets.QPushButton("勾选筛选结果")
        clear_btn = QtWidgets.QPushButton("取消筛选结果")
        def set_visible(checked):
            rows = (proxy.mapToSource(proxy.index(i, 0)).row()
                    for i in range(proxy.rowCount()))
            model.select_rows(rows, checked)
        select_btn.clicked.connect(lambda: set_visible(True))
        clear_btn.clicked.connect(lambda: set_visible(False))
        bar.addWidget(select_btn)
        bar.addWidget(clear_btn)
        bar.addWidget(selected_label)
        bar.addStretch()
        layout.addLayout(bar)
        warning = QtWidgets.QLabel(
            "⚠ 目标文件夹中独有的文件也会被替换删除，绝非简单的文件合并。"
            "成功后不保留旧目标备份；失败会尽可能回滚，并在审计日志记录备份路径。"
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)
        path_label = QtWidgets.QLabel(f"完整预检及跳过原因：{batch['preview_path']}")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        proceed = buttons.addButton("确认勾选作品文件夹", QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        proceed.setEnabled(bool(plans))
        def proceed_checked():
            if not model.selected_plans():
                QtWidgets.QMessageBox.warning(dialog, "尚未勾选", "请选择至少一个作品文件夹。")
                return
            dialog.accept()
        buttons.accepted.connect(proceed_checked)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            self.writer.submit_merge_event(batch["operation_id"], "MOVE_FOLDER_CANCELLED",
                                           reason="取消勾选预览")
            return
        selected_plans = model.selected_plans()
        word, accepted = QtWidgets.QInputDialog.getText(
            self, "最终确认：移动并整体覆盖作品文件夹",
            f"来源根目录：{batch['source_root']}\n"
            f"目标根目录：{batch['destination_root']}\n"
            f"已选择 {len(selected_plans)} 个作品文件夹。\n\n"
            "源文件夹将移动至目标旧路径；目标文件夹原有的所有文件"
            "（NFO、STRM、图片等）都将被整体替换，且成功后不自动保留备份。\n"
            "不会删除 STRM 所指向的 115/CD2 视频。\n\n"
            "确认已核对路径后，请输入：覆盖整个作品文件夹",
            QtWidgets.QLineEdit.EchoMode.Normal,
        )
        if not accepted or word.strip() != "覆盖整个作品文件夹":
            self.writer.submit_merge_event(batch["operation_id"], "MOVE_FOLDER_CANCELLED",
                                           reason="最终确认词不匹配或用户取消")
            return
        selected_batch = dict(batch)
        selected_batch["selected_plans"] = selected_plans
        worker = BulkMoveWorker(
            "execute", self.duplicates, self.records, self._completed_scan_roots,
            self.path_mapping.get("source_prefix", ""),
            self.path_mapping.get("target_prefix", ""), batch["operation_id"],
            source_root=batch["source_root"],
            destination_root=batch["destination_root"],
            confirmed_batch=selected_batch,
        )
        self._begin_task(worker,
                         f"执行目录整体移动覆盖：{len(selected_plans)} 个作品文件夹")

    def closeEvent(self, event: QtGui.QCloseEvent):
        if not self.close_save_requested:
            self.close_save_requested = True
            self._save_settings()
            self.writer.stop_event.set()
            self._gui_watchdog_stop.set()
            self._gui_heartbeat_timer.stop()
        self.closing_requested = True
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            self._set_status("退出中：等待后台任务安全结束，不会强制结束删除线程")
            self._update_buttons()
            event.ignore()
            return
        if self.folder_worker is not None and self.folder_worker.isRunning():
            self.folder_worker.stop()
            self._set_status("退出中：等待文件管理器打开任务结束")
            event.ignore()
            return
        if self.writer.is_alive() or self._gui_watchdog.is_alive():
            event.ignore()
            self._set_status(f"退出中：正在完成后台日志/设置写入 {self.settings_file}")
            QtCore.QTimer.singleShot(150, self.close)
            return
        # 所有 Event 的语义均为 set 请求停止；线程退出后清理状态。
        self.writer.stop_event.clear()
        self._gui_watchdog_stop.clear()
        event.accept()


class MergeDeleteWorker(QtCore.QThread):
    """两阶段安全合并删除：后台预检 -> GUI确认 -> 后台二次预检与执行。"""

    progress = QtCore.Signal(int, int, str)
    result = QtCore.Signal(object)

    def __init__(
        self, phase: str, entry: MediaEntry, records: list[MediaEntry],
        roots: list[str], from_prefix: str, to_prefix: str,
        operation_id: str, confirmed_plan: dict | None = None,
    ):
        super().__init__()
        self.phase = phase
        self.entry = entry
        self.records = list(records)
        self.roots = list(roots)
        self.from_prefix = from_prefix
        self.to_prefix = to_prefix
        self.operation_id = operation_id
        self.confirmed_plan = dict(confirmed_plan) if confirmed_plan else None
        self.outcome = None
        self.stop_event = threading.Event()  # set() 表示请求停止

    def stop(self):
        self.stop_event.set()

    def run(self):
        result = {
            "phase": self.phase, "success": False, "error": "",
            "plan": dict(self.confirmed_plan) if self.confirmed_plan else None,
            "completed": [],
        }
        try:
            if self.phase == "precheck":
                append_merge_delete_log(
                    self.operation_id, "PRECHECK_START",
                    media_type=self.entry.media_type,
                    code=self.entry.code,
                    scrape_or_video=self.entry.media_path,
                    source_video=self.entry.target,
                )
                self.progress.emit(
                    0, 1,
                    "仅检查刮削文件夹里有无其它视频/STRM，不遍历源视频目录"
                    if self.entry.media_type == "STRM"
                    else "检查普通视频所在文件夹里有无其它视频/STRM",
                )
                plan = build_merge_delete_plan(
                    self.entry, self.records, self.roots,
                    self.from_prefix, self.to_prefix,
                    self.stop_event, operation_id=self.operation_id,
                )
                append_merge_delete_log(
                    self.operation_id, "PRECHECK_PASSED", plan=plan,
                )
                result["success"] = True
                result["plan"] = plan
                self.progress.emit(1, 1, "预检通过，等待人工确认")
            elif self.phase == "execute":
                if self.confirmed_plan is None:
                    raise MergeDeleteBlocked("不存在有效的合并删除预检方案")
                self.progress.emit(0, 2, "重新检查目标文件夹，尚未删除")
                completed = execute_merge_delete(
                    self.confirmed_plan, self.entry, self.records,
                    self.roots, self.from_prefix, self.to_prefix,
                    self.stop_event,
                )
                result["completed"] = completed
                result["success"] = True
                self.progress.emit(2, 2, "合并删除完成")
            else:
                raise ValueError("无效的合并删除阶段")
        except (Interrupted, MergeDeleteBlocked, OSError, ValueError) as error:
            result["error"] = str(error) or "用户停止或取消操作"
            if isinstance(error, MergeDeletePartial):
                result["completed"] = list(error.completed_steps)
            stage = "PRECHECK_BLOCKED" if self.phase == "precheck" else "EXECUTE_ABORTED"
            try:
                append_merge_delete_log(
                    self.operation_id, stage,
                    reason=str(error),
                    media_type=self.entry.media_type,
                    media_path=self.entry.media_path,
                    source_video=self.entry.target,
                )
            except OSError as log_error:
                result["error"] += f"；审计日志无法写入：{log_error}"
        self.outcome = result
        self.result.emit(result)


class BulkDeleteWorker(QtCore.QThread):
    """后台批量预检或逐条删除；set(stop_event)=请求停止。"""

    progress = QtCore.Signal(int, int, str)
    result = QtCore.Signal(object)

    def __init__(
        self, phase: str, duplicates: dict[str, list[MediaEntry]],
        records: list[MediaEntry], roots: list[str], from_prefix: str,
        to_prefix: str, operation_id: str, *,
        source_root: str, keep_root: str, confirmed_batch: dict | None = None,
    ):
        super().__init__()
        self.phase = phase
        self.duplicates = dict(duplicates)
        self.records = list(records)
        self.roots = list(roots)
        self.from_prefix = from_prefix
        self.to_prefix = to_prefix
        self.operation_id = operation_id
        self.source_root = source_root
        self.keep_root = keep_root
        self.confirmed_batch = confirmed_batch
        self.stop_event = threading.Event()  # set() = 请求停止，退出后主线程清理
        self.outcome = None

    def stop(self):
        self.stop_event.set()

    def run(self):
        result = {"phase": self.phase, "success": False, "error": "", "stopped": False}
        try:
            if self.phase == "precheck":
                result["batch"] = precheck_bulk_cleanup(
                    self.duplicates, self.records, self.roots,
                    self.from_prefix, self.to_prefix, self.stop_event,
                    self.progress.emit, operation_id=self.operation_id,
                    source_root=self.source_root, keep_root=self.keep_root,
                )
            elif self.phase == "execute":
                if not self.confirmed_batch:
                    raise MergeDeleteBlocked("缺少最终确认的批量删除计划")
                if (nt_key(self.confirmed_batch["source_root"]) != nt_key(self.source_root)
                        or nt_key(self.confirmed_batch["keep_root"]) != nt_key(self.keep_root)):
                    raise MergeDeleteBlocked("执行目录与用户预检时选择的目录不一致")
                result["outcome"] = execute_bulk_cleanup(
                    self.confirmed_batch, self.records, self.roots,
                    self.from_prefix, self.to_prefix, self.stop_event,
                    self.progress.emit,
                )
            else:
                raise ValueError(f"不支持的批量任务阶段：{self.phase}")
            result["success"] = True
        except Interrupted:
            result["stopped"] = True
            result["error"] = "用户停止操作；未开始下一条删除"
        except (MergeDeleteBlocked, OSError, ValueError) as error:
            result["error"] = str(error)
            try:
                append_merge_delete_log(
                    self.operation_id, "BULK_WORKER_FAILED", phase=self.phase,
                    error=str(error),
                )
            except OSError as log_error:
                result["error"] += f"；日志写入失败：{log_error}"
        result["stopped"] = bool(result["stopped"] or self.stop_event.is_set())
        self.outcome = result
        self.result.emit(result)


class BulkMoveWorker(QtCore.QThread):
    """移动预检/执行均在后台线程；stop_event.set() 表示请求停止。"""

    progress = QtCore.Signal(int, int, str)
    result = QtCore.Signal(object)

    def __init__(self, phase, duplicates, records, roots, from_prefix, to_prefix,
                 operation_id, *, source_root, destination_root, confirmed_batch=None):
        super().__init__()
        self.phase = phase
        self.duplicates = dict(duplicates)
        self.records = list(records)
        self.roots = list(roots)
        self.from_prefix = from_prefix
        self.to_prefix = to_prefix
        self.operation_id = operation_id
        self.source_root = source_root
        self.destination_root = destination_root
        self.confirmed_batch = confirmed_batch
        self.stop_event = threading.Event()  # set() = 请求停止；finished 后 GUI 清理
        self.outcome = None

    def stop(self):
        self.stop_event.set()

    def run(self):
        result = {"phase": self.phase, "success": False, "error": ""}
        try:
            if self.phase == "precheck":
                result["batch"] = precheck_bulk_move(
                    self.duplicates, self.records, self.roots,
                    self.from_prefix, self.to_prefix, self.stop_event,
                    self.progress.emit, operation_id=self.operation_id,
                    source_root=self.source_root,
                    destination_root=self.destination_root,
                )
            elif self.phase == "execute":
                if not self.confirmed_batch:
                    raise MergeDeleteBlocked("缺少用户勾选并确认的作品文件夹移动计划")
                batch = self.confirmed_batch
                if (batch["operation_id"] != self.operation_id
                        or nt_key(batch["source_root"]) != nt_key(self.source_root)
                        or nt_key(batch["destination_root"]) != nt_key(self.destination_root)):
                    raise MergeDeleteBlocked("执行阶段的根目录与用户预检确认内容不一致")
                result["outcome"] = execute_bulk_move(
                    batch, self.records, self.roots,
                    self.from_prefix, self.to_prefix,
                    self.stop_event, self.progress.emit,
                )
            else:
                raise ValueError("无效的移动任务阶段")
            result["success"] = True
        except Interrupted:
            result["error"] = "用户请求停止移动任务"
        except (MergeDeleteBlocked, OSError, ValueError, sqlite3.Error) as error:
            result["error"] = str(error)
        self.outcome = result
        self.result.emit(result)

if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("本程序专为 Windows 编写。")
    if sys.version_info < (3, 10):
        raise SystemExit("需要 Python 3.10 或更新版本。")
    import ctypes
    try:
        set_app_id = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        set_app_id.argtypes = [ctypes.c_wchar_p]
        set_app_id.restype = ctypes.c_long
        set_app_id("CGDedupe.Desktop")
    except (OSError, AttributeError):
        pass
    app = QtWidgets.QApplication(sys.argv)
    # Windows 使用 Qt 当前平台的原生 Style，避免强制 Fusion 的图标绘制路径。
    start_directory = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        window = NfoDuplicateFinder(initial_directory=start_directory)
    except OSError as error:
        QtWidgets.QMessageBox.critical(
            None, "程序数据目录不可写",
            str(error)
        )
        raise SystemExit(1)
    running_file = (
        Path(sys.executable).resolve()
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve()
    )
    if sys.stdout is not None:
        print(f"[CGDedupe v{APP_VERSION}] Running: {running_file}", flush=True)
        print(f"[CGDedupe] Data directory: {window.data_dir}", flush=True)
    window.show()
    sys.exit(app.exec())
