# GitHub Actions PySide6 构建说明

`/.github/workflows/build.yml` 已改为纯 PySide6 Windows x64 构建流程。

## 构建内容

- 根目录：`NFOEditor.exe` 及其 `_internal` 运行库。
- `tools/cg_crop/`：独立图片裁剪工具。
- `tools/cg_rename/`：独立批量改名工具。
- `tools/cg_dedupe/`：独立查重工具。
- `tools/cg_photo_wall/`：独立照片墙工具。

## CI 验证

1. 拒绝安装或打包 PyQt5、PyQt6、PySide2。
2. 编译全部 Python 文件并运行单元测试、pyflakes。
3. 使用 Qt offscreen 平台实际创建并关闭五个顶层窗口。
4. 使用 PyInstaller 6.21.0 构建 Windows x64 onedir 应用。
5. 检查每个应用的 exe、PySide6 内容、`qwindows.dll`、offscreen/minimal 插件及 XML/图标资源。
6. 启动每个冻结后的 exe，确认 6 秒内不会因缺 DLL、Qt 插件或导入异常而退出。
7. 生成 ZIP 和 SHA-256 文件。
8. 普通分支、PR 和手动运行只上传 Actions Artifact；推送 `v*` 标签时发布或更新 GitHub Release。

## 本地复现

```powershell
python -m venv .venv
. .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-build.txt
python scripts/qt_source_smoke.py
.\scripts\build_windows.ps1
.\scripts\check_windows_artifact.ps1 -Root dist/NFOTools -RunSmokeTests
```
