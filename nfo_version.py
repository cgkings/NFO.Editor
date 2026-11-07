# -*- coding: utf-8 -*-
"""NFO Tools 版本唯一入口（Python .py 与 PyInstaller EXE 通用）。

修改方式：
- 日常独立运行时，默认保留各工具原来的版本。
- 如需所有工具统一版本，只修改下面 UNIFIED_VERSION 一行。
- GitHub Actions 使用 vX.Y.Z 标签发布时，构建生成的版本优先。
"""

# 示例：改成 "9.8.4" 即可让所有工具显示 v9.8.4；留空则保留独立版本。
UNIFIED_VERSION = ""

# 保留之前各个程序原有的版本号；需要独立发布时只在本文件修改。
_DEFAULT_VERSIONS = {
    "editor": "9.8.3",
    "crop": "9.8.1",
    "rename": "9.8.1",
    "photo_wall": "9.8.1",
    "dedupe": "11.3.11",
}

# GitHub CI 在源码分析之前生成此模块；本地直接运行 .py 时可以不存在。
try:
    from _nfo_ci_build_version import BUILD_VERSION as _BUILD_VERSION
except ModuleNotFoundError as exc:
    if exc.name != "_nfo_ci_build_version":
        raise
    _BUILD_VERSION = ""


def get_app_version(app_name: str, *, prefixed: bool = False) -> str:
    """返回工具版本；prefixed=True 时在版本前加 v。

    优先级：GitHub 构建标签 > UNIFIED_VERSION > 对应工具原版本。
    """
    if app_name not in _DEFAULT_VERSIONS:
        raise ValueError(f"未知的 NFO Tools 程序标识: {app_name}")
    version = (
        str(_BUILD_VERSION or "").strip()
        or str(UNIFIED_VERSION or "").strip()
        or _DEFAULT_VERSIONS[app_name]
    ).removeprefix("v").removeprefix("V")
    return "v" + version if prefixed else version
