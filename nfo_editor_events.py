"""
nfo_editor_events.py
====================

给未重构的组件脚本使用的轻量事件通知工具。

主程序会每秒读取 NFO 根目录下的 .nfo_editor_events.jsonl。
组件脚本在修改 NFO、裁剪图片、移动/删除文件夹后调用 notify_editor_event，
主程序即可增量刷新或刷新当前项，不需要监听整棵库。
"""

import json
import os
import time


def notify_editor_event(root_path, event_type, path="", message=""):
    """向 NFO Editor 发送一个轻量事件。

    event_type 常用值:
      - task_started   外部组件任务开始，只更新状态栏
      - task_finished  外部组件任务结束，只更新状态栏
      - nfo_changed    某个 NFO 被修改，path 填 nfo 路径
      - image_changed  某张图或某个文件夹图片被修改，path 填图片或文件夹路径
      - folder_moved   文件夹移动完成，触发列表增量刷新
      - folder_deleted 文件夹删除完成，触发列表增量刷新
      - refresh_list   明确要求主程序增量刷新列表
      - library_dirty  通用列表已变更事件
    """
    if not root_path:
        return False

    event_file = os.path.join(root_path, ".nfo_editor_events.jsonl")
    event = {
        "type": event_type,
        "path": os.path.abspath(path) if path else "",
        "message": message,
        "time": time.time(),
    }

    try:
        with open(event_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def notify_task_started(root_path, message="外部组件正在处理..."):
    return notify_editor_event(root_path, "task_started", message=message)


def notify_task_finished(root_path, message="外部组件处理完成"):
    return notify_editor_event(root_path, "task_finished", message=message)


def notify_nfo_changed(root_path, nfo_path):
    return notify_editor_event(root_path, "nfo_changed", path=nfo_path)


def notify_image_changed(root_path, image_or_folder_path):
    return notify_editor_event(root_path, "image_changed", path=image_or_folder_path)


def notify_refresh_list(root_path):
    return notify_editor_event(root_path, "refresh_list", path=root_path)
