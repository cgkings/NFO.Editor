# 修改摘要

## 性能
- 新增 `NFODiskIndex`，使用 SQLite 保存 NFO 解析结果。
- `LoadFilesThread` 不再每次解析全部 NFO；只解析新增或修改过的文件。
- `LoadFilesThread` 不再在线程中创建 `QTreeWidgetItem`，改为发普通数据到主线程。
- UI 列表改为 `addTopLevelItems()` 批量添加。

## 文件监控
- 移除根目录实时监控触发整库刷新。
- 只监听当前选中的 NFO 和当前文件夹。
- 新增 `.nfo_editor_events.jsonl` 事件队列，用于和外部组件通信。

## 交互
- 清空筛选不再重新加载文件夹。
- 状态栏显示解析数和缓存命中数。


## 组件整合
- 纳入 `cg_crop.py`、`cg_rename.py`、`cg_photo_wall.py`、`cg_dedupe.py`。
- 四个组件从 PyQt5 import 改为 PySide6 import。
- `cg_rename.py` 接入 `nfo_editor_events.py`：
  - 任务开始/结束更新主程序状态栏。
  - 修改 NFO 后发送 `nfo_changed`。
  - 重命名文件夹后发送 `refresh_list`。
- PyInstaller spec 已加入组件和图标。

## v2
- 修复从主窗口打开照片墙时重复 `os.walk + parse_nfo` 的性能问题。
- `nfo_editor.show_photo_wall()` 改为先创建空照片墙窗口，再调用 `load_from_editor_cache(self)`。
- `cg_photo_wall.py` 新增 `load_from_editor_cache()`，直接复用主窗口 `nfo_files` 和 `nfo_cache`。
- 修复照片墙筛选只作用于当前渲染窗口的问题。
- 修复照片墙 resize 时滑动窗口索引错位导致图片加载错的问题。
- SQLite 索引清理更保守：已存在但解析失败的文件会保留旧索引，不会被误删。
