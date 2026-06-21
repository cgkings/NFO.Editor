"""Pure filesystem/XML safety helpers shared by the Qt tools."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Optional, Sequence


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
    xml_bytes = ET.tostring(root, encoding="utf-8")
    parsed = minidom.parseString(xml_bytes)
    pretty = parsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
    return "\n".join(line for line in pretty.splitlines() if line.strip()) + "\n"


def write_xml_root_atomic(root: ET.Element, path: str) -> None:
    atomic_write_text(path, serialize_pretty_xml(root))


def safe_move_directory(src_path: str, dest_parent: str) -> str:
    """Move one directory without deleting/overwriting an existing destination."""
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
    if same_path(src_path, dest_path):
        raise OSError("源目录和目标目录相同，已取消移动")
    if is_path_within(dest_parent, src_path, include_equal=True):
        raise OSError("不能把文件夹移动到自身或自身子目录")
    if os.path.exists(dest_path):
        raise FileExistsError(f"目标已存在同名文件夹，未执行覆盖: {dest_path}")

    src_drive = os.path.splitdrive(canonical_path(src_path))[0]
    dest_drive = os.path.splitdrive(canonical_path(dest_parent))[0]
    if src_drive == dest_drive:
        shutil.move(src_path, dest_path)
        return dest_path

    try:
        shutil.copytree(src_path, dest_path)
    except Exception:
        if os.path.exists(dest_path):
            shutil.rmtree(dest_path, ignore_errors=True)
        raise

    try:
        shutil.rmtree(src_path)
    except Exception as exc:
        # Keep the verified destination copy; deleting it here could turn a recoverable
        # duplicate into data loss if the source is only partially removable.
        raise OSError(
            f"文件已复制到目标，但源目录删除失败；为安全起见保留两份: {exc}"
        ) from exc
    return dest_path


def preferred_nfo_file(folder_path: str, preferred_stem: Optional[str] = None) -> Optional[str]:
    try:
        candidates = sorted(
            (
                entry.path
                for entry in os.scandir(folder_path)
                if entry.is_file() and entry.name.lower().endswith(".nfo")
            ),
            key=lambda p: os.path.basename(p).lower(),
        )
    except OSError:
        return None
    if not candidates:
        return None
    if preferred_stem:
        wanted = preferred_stem.lower()
        for candidate in candidates:
            if Path(candidate).stem.lower() == wanted:
                return candidate
    return candidates[0]


def preferred_image_file(
    folder_path: str, image_type: str, preferred_stem: Optional[str] = None
) -> Optional[str]:
    image_type = (image_type or "").lower()
    try:
        candidates = [
            entry.path
            for entry in os.scandir(folder_path)
            if entry.is_file()
            and entry.name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
            and image_type in entry.name.lower()
        ]
    except OSError:
        return None
    if not candidates:
        return None

    preferred_stem = (preferred_stem or "").lower()

    def sort_key(path: str):
        name = os.path.basename(path).lower()
        stem = Path(path).stem.lower()
        exact = bool(preferred_stem and stem == f"{preferred_stem}-{image_type}")
        canonical_suffix = stem.endswith(f"-{image_type}")
        return (0 if exact else 1, 0 if canonical_suffix else 1, name)

    return sorted(candidates, key=sort_key)[0]
