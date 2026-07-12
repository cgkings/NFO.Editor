"""Create and close every top-level PySide6 window without user interaction."""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

# Isolate QSettings and application files from the runner account.
_temp_root = tempfile.TemporaryDirectory(prefix="nfo-tools-smoke-")
os.environ["APPDATA"] = _temp_root.name
os.environ["LOCALAPPDATA"] = _temp_root.name

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

_required_source_files = (
    "nfo_editor.py",
    "nfo_editor_ui.py",
    "nfo_utils.py",
    "cg_crop.py",
    "cg_dedupe.py",
    "cg_photo_wall.py",
    "cg_rename.py",
)


def _exact_file_exists(relative: str) -> bool:
    target = repo_root / relative
    parent = target.parent
    if not parent.is_dir():
        return False
    return any(child.name == target.name and child.is_file() for child in parent.iterdir())


_missing_source_files = [
    relative for relative in _required_source_files
    if not _exact_file_exists(relative)
]
if _missing_source_files:
    raise SystemExit(
        "Qt source smoke test cannot start because files are missing or use "
        "the wrong filename casing: "
        + ", ".join(_missing_source_files)
        + ". Run scripts/fix_source_filename_case.ps1, commit, and push."
    )

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication(["qt-source-smoke"])
    QCoreApplication.setOrganizationName("NFOToolsSmoke")
    QCoreApplication.setApplicationName("NFOToolsSmoke")

    windows = []
    try:
        from nfo_editor import NFOEditorQt6
        from cg_crop import EmbyPosterCrop
        from cg_dedupe import NfoDuplicateFinder
        from cg_photo_wall import PhotoWallDialog
        from cg_rename import RenameToolGUI

        factories = (
            ("NFOEditorQt6", NFOEditorQt6),
            ("EmbyPosterCrop", EmbyPosterCrop),
            ("NfoDuplicateFinder", NfoDuplicateFinder),
            ("PhotoWallDialog", PhotoWallDialog),
            ("RenameToolGUI", RenameToolGUI),
        )

        for name, factory in factories:
            window = factory()
            windows.append(window)
            window.show()
            app.processEvents()
            if not window.isVisible():
                raise RuntimeError(f"{name} did not become visible")
            window.close()
            app.processEvents()
            print(f"[OK] {name}")

        print("All PySide6 source startup smoke tests passed.")
        return 0
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        for window in reversed(windows):
            try:
                window.close()
                window.deleteLater()
            except RuntimeError:
                pass
        app.processEvents()
        _temp_root.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
