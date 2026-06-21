"""Validate required repository files with exact filename casing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = (
    "nfo_editor.py",
    "nfo_editor_ui.py",
    "nfo_editor_events.py",
    "nfo_utils.py",
    "cg_crop.py",
    "cg_dedupe.py",
    "cg_photo_wall.py",
    "cg_rename.py",
    "nfo_editor.spec",
    "requirements.txt",
    "requirements-build.txt",
    "scripts/qt_source_smoke.py",
    "scripts/build_windows.ps1",
    "scripts/check_windows_artifact.ps1",
)

REQUIRED_DIRECTORIES = (
    ".github",
    ".github/workflows",
    "scripts",
    "tests",
    "img",
)


def exact_child_exists(relative: str, *, directory: bool) -> bool:
    target = REPO_ROOT / relative
    parent = target.parent
    if not parent.is_dir():
        return False

    for child in parent.iterdir():
        if child.name != target.name:
            continue
        return child.is_dir() if directory else child.is_file()
    return False


def tracked_files() -> set[str] | None:
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return {line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()}


def main() -> int:
    missing_files = [
        item for item in REQUIRED_FILES
        if not exact_child_exists(item, directory=False)
    ]
    missing_directories = [
        item for item in REQUIRED_DIRECTORIES
        if not exact_child_exists(item, directory=True)
    ]

    tracked = tracked_files()
    wrong_git_case: list[str] = []
    if tracked is not None:
        wrong_git_case = [item for item in REQUIRED_FILES if item not in tracked]

    if missing_files or missing_directories or wrong_git_case:
        print("Repository source layout validation failed.", file=sys.stderr)

        if missing_files:
            print("Missing files or wrong filename casing:", file=sys.stderr)
            for item in missing_files:
                print(f"  - expected exactly: {item}", file=sys.stderr)

        if missing_directories:
            print("Missing directories or wrong directory casing:", file=sys.stderr)
            for item in missing_directories:
                print(f"  - expected exactly: {item}/", file=sys.stderr)

        if wrong_git_case:
            print("Git index does not contain these exact-case paths:", file=sys.stderr)
            for item in wrong_git_case:
                print(f"  - {item}", file=sys.stderr)

        print(
            "\nOn Windows, use scripts/fix_source_filename_case.ps1, "
            "then commit and push the case-only renames.",
            file=sys.stderr,
        )
        return 1

    print("Repository source layout and exact filename casing passed.")
    for item in REQUIRED_FILES:
        print(f"[OK] {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
