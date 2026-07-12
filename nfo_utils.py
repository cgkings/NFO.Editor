"""Pure filesystem/XML safety helpers shared by the Qt tools."""

from __future__ import annotations

import json
import os
import re
import time
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Optional, Sequence


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

    event_file = os.path.join(
        os.path.normpath(library_root),
        ".nfo_editor_events.jsonl",
    )
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
