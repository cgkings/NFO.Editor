# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

import os

def add_if_exists(items, source, destination="."):
    if os.path.exists(source):
        items.append((source, destination))


datas = [
    ("nfo_editor_ui.py", "."),
    ("nfo_utils.py", "."),
]

for optional_file in (
    "chuizi.ico",
    "cg_crop.py",
    "cg_crop.ico",
    "cg_rename.py",
    "cg_photo_wall.py",
    "cg_photo_wall.ico",
    "cg_dedupe.py",
    "cg_dedupe.ico",
    "mapping_actor.xml",
    "series_mapping.xml",
):
    add_if_exists(datas, optional_file)

if os.path.isdir("img"):
    datas.append(("img", "img"))

# The PySide6-Fluent-Widgets wheel embeds its Qt resources in Python modules.
# Importing qfluentwidgets statically lets PyInstaller follow only the modules
# used by this application instead of collecting optional heavy image helpers.
hiddenimports = [
    "qfluentwidgets",
    "PIL",
    "PIL.Image",
    "PIL._imaging",
    "bs4",
    "lxml",
    "requests",
    "winshell",
    "win32api",
    "win32com",
    "win32com.client",
    "win32com.shell",
    "win32com.shell.shellcon",
    "pythoncom",
    "pywintypes",
    "win32wnet",
    "nfo_utils",
    "cg_crop",
    "cg_rename",
    "cg_photo_wall",
    "cg_dedupe",
]

excluded_modules = [
    "PyQt5",
    "PyQt6",
    "PySide2",
    # Optional packages referenced by qfluentwidgets/Pillow but not used here.
    "numpy",
    "scipy",
    "matplotlib",
    "IPython",
    "pytest",
    "tkinter",
    "jedi",
]

a = Analysis(
    ["nfo_editor.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NFOEditor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="chuizi.ico" if os.path.exists("chuizi.ico") else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NFOEditor",
)
