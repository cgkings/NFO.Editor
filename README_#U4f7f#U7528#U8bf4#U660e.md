# NFO Editor Qt6 增量索引版

本包基于你上传的 `nfo_editor.py` / `nfo_editor_ui.py` 修改。

## 已改动重点

1. `LoadFilesThread` 改为 SQLite 持久化索引。
   - 索引文件位置：Windows `%APPDATA%\NFOEditor\nfo_index.sqlite3`
   - 第二次加载同一库时，只要 NFO 的 `mtime_ns` 和 `size` 没变，就不重新 XML 解析。
   - 状态栏会显示：本次解析多少个、缓存命中多少个。

2. 文件列表改为批量渲染。
   - 原来每个 NFO 发一次 Qt 信号、主线程 add 一次 item。
   - 现在每 300 个一批发给 UI，降低 15000 个 NFO 时的 UI 压力。

3. 文件监控收敛。
   - 不再监听 NFO 根目录并在目录变化时整库 reload。
   - 只监听当前选中的 NFO 文件和当前文件夹，用于即时刷新当前字段/图片。
   - 外部组件推荐用事件文件通知主程序。

4. 筛选清空不再重载。
   - 清空筛选时直接从当前内存列表重建树，不重新扫盘。

5. 外部组件事件桥。
   - 新增 `nfo_editor_events.py`
   - 主程序每秒读取库根目录下 `.nfo_editor_events.jsonl`
   - 未 Qt6 重构的组件也可以通过这个文件通知主程序刷新当前项或列表。

## 组件脚本如何接入事件通知

在组件脚本里加：

```python
from nfo_editor_events import (
    notify_task_started,
    notify_task_finished,
    notify_nfo_changed,
    notify_image_changed,
    notify_refresh_list,
)
```

示例：

```python
notify_task_started(root_path, "正在批量裁剪图片...")

# 修改某个 nfo 后：
notify_nfo_changed(root_path, nfo_path)

# 裁剪或替换图片后：
notify_image_changed(root_path, image_path)

# 移动/删除/重命名文件夹后：
notify_refresh_list(root_path)

notify_task_finished(root_path, "批量裁剪完成")
```

## 本地运行

```powershell
pip install -r requirements.txt
python nfo_editor.py
```

## Windows 打包 exe

请把这些文件放在同一目录：

- `nfo_editor.py`
- `nfo_editor_ui.py`
- `nfo_editor_events.py`
- `cg_crop.py`，如需图片裁剪
- `cg_rename.py`，如需演员统一/重命名工具
- `cg_photo_wall.py`，如需照片墙
- `chuizi.ico`，如需窗口图标

然后 PowerShell 执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build_exe.ps1
```

输出：

```text
dist\NFOEditor\NFOEditor.exe
```

## 还需要你上传哪些组件脚本

如果你想让我继续把外部组件也改成事件通知 + Qt6 兼容，建议上传：

1. `cg_crop.py`：图片裁剪工具
2. `cg_rename.py`：统一演员 / 重命名工具
3. `cg_photo_wall.py`：照片墙
4. 任何被以上脚本 import 的自定义 `.py` 文件
5. `chuizi.ico`，如果你希望最终 exe 带图标

目前这个包已经能替换主程序和 UI 源码；但完整 exe 是否可打包，取决于这些动态导入组件是否齐全。


## SQLite 旧记录清理机制

会清理。每次扫描会生成一个新的 `scan_id`：

1. 本次扫描看到的 NFO，会写入/刷新 `last_seen_scan = scan_id`。
2. 完整扫描结束后执行：
   `DELETE FROM nfo_index WHERE root_path = ? AND last_seen_scan != ?`
3. 因此，同一个 NFO 根目录下已经不存在的旧记录会被删除。
4. 如果扫描被中断、用户切换目录、程序关闭或线程 stop，中途直接 return，不会执行最终删除，避免半扫描误删缓存。
5. 清理只限定当前 `root_path`，不会误删其他库目录的索引。

## 本包已纳入的组件

- `cg_crop.py`：已转 PySide6，并保留裁剪功能。
- `cg_rename.py`：已转 PySide6，并接入事件通知；修改 NFO 后通知主程序刷新缓存，重命名文件夹后通知列表增量刷新。
- `cg_photo_wall.py`：已转 PySide6。
- `cg_dedupe.py`：已转 PySide6，作为独立组件打包纳入。
- 图标：`chuizi.ico`、`cg_crop.ico`、`cg_dedupe.ico`、`cg_photo_wall.ico`

注意：裁剪工具如果使用水印，还需要你本地保留 `img/sub.png`、`img/youma.png`、`img/wuma.png`、`img/leak.png`、`img/umr.png`。本包没有这些图片资源；本地打包时只要 `img` 文件夹存在，spec 会自动收进去。

## v2 修复：照片墙不再重复加载 NFO

从主窗口打开照片墙时，`nfo_editor.py` 现在不再把 `folder_path` 传给 `PhotoWallDialog` 触发全库 `os.walk()`。
流程改成：

1. 主窗口已完成 `nfo_files + nfo_cache` 加载。
2. 照片墙调用 `load_from_editor_cache(editor)`。
3. 照片墙只根据每个已加载 NFO 的所在文件夹快速找 poster 图。
4. NFO 元数据直接来自主窗口缓存，不重新解析 15000 个 NFO。

独立运行 `python cg_photo_wall.py <目录>` 时仍保留原来的扫描模式。

同时修复：
- 照片墙筛选只对当前渲染页生效的问题，改为对完整内存数据源筛选。
- 滚动窗口模式下 resize 后图片索引错位的问题。
- 图片加载线程池被取消后再次加载不可用的问题。
- SQLite 索引删除更保守：文件存在但解析失败时只标记 seen，不会把旧索引当作已删除记录清掉。
