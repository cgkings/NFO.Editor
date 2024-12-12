import ctypes
import tkinter as tk
from tkinter import filedialog, messagebox, Toplevel, ttk
import os
import shutil
import threading
import xml.etree.cElementTree as ET
from datetime import datetime
from PIL import Image, ImageTk
from idlelib.tooltip import Hovertip
import xml.dom.minidom as minidom
import subprocess
import sys
import winshell
import tempfile  # 添加 tempfile 导入


def get_resource_path(relative_path):
    """获取资源文件的绝对路径"""
    if getattr(sys, "frozen", False):
        # 如果是打包后的exe
        base_path = sys._MEIPASS
    else:
        # 如果是直接运行的py脚本
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


class NFOEditorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("大锤 NFO Editor v9.3.2")

        # 在创建任何UI组件之前设置图标
        try:
            icon_path = get_resource_path("chuizi.ico")
            if os.path.exists(icon_path):
                self.root.iconbitmap(default=icon_path)
        except Exception:
            pass

        self.current_file_path = None
        self.fields_entries = {}
        self.show_images_var = tk.BooleanVar(value=False)

        self.setup_ui()
        self.center_window()
        self.root.mainloop()

    def center_window(self):
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def setup_ui(self):
        self.top_frame = tk.Frame(self.root)
        self.top_frame.pack(side=tk.TOP, fill=tk.X)
        self.create_top_buttons()

        self.sorting_frame = tk.Frame(self.root)
        self.sorting_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        self.create_sorting_options()
        self.create_filter_frame()  # 添加筛选框架

        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.create_file_list(self.main_frame)
        self.create_sorted_list(self.main_frame)
        self.create_fields_frame(self.main_frame)
        self.create_operations_panel()

        # 添加快捷键
        self.root.bind("<F5>", lambda e: self.load_files_in_folder())
        self.root.bind("<Return>", lambda e: self.save_changes())

        self.root.bind(
            "<Control-Right>", lambda e: self.start_move_thread()
        )  # Ctrl+→ 移动文件
        self.root.bind("<Left>", lambda e: self.focus_file_list())  # ← 焦点到文件列表
        self.root.bind("<Right>", lambda e: self.focus_rating())  # → 焦点到评分框

    def focus_file_list(self):
        """焦点回到文件列表"""
        self.file_treeview.focus_set()
        # 如果没有选中项，则选择第一项
        if not self.file_treeview.selection():
            items = self.file_treeview.get_children()
            if items:
                self.file_treeview.selection_set(items[0])
        return "break"

    def focus_rating(self):
        """焦点到评分框并全选"""
        if "rating" in self.fields_entries:
            rating_widget = self.fields_entries["rating"]
            rating_widget.focus_force()  # 强制焦点转移
            self.root.after(
                10, lambda: rating_widget.tag_add("sel", "1.0", "end-1c")
            )  # 延迟执行全选
        return "break"

    def create_top_buttons(self):
        buttons_info = [
            ("选择nfo目录", self.open_folder, "选择目录以加载NFO文件"),
            ("选择整理目录", self.select_target_folder, "选择整理目录"),
            ("🖊", self.open_selected_nfo, "打开选中的NFO文件"),
            ("📁", self.open_selected_folder, "打开选中的文件夹"),
            ("⏯", self.open_selected_video, "播放选中的视频文件"),
            ("🔗", self.open_batch_rename_tool, "统一演员名并重命名文件夹"),
            ("🔁", self.load_files_in_folder, "刷新文件列表,快捷键F5"),
            (
                "=>",
                self.start_move_thread,
                "移动nfo所在文件夹到目标目录,快捷键ctrl + →",
            ),
        ]

        for text, command, tooltip in buttons_info:
            button = tk.Button(
                self.top_frame, text=text, command=command, font=("DengXian", 12)
            )
            button.pack(side=tk.LEFT, padx=5)
            Hovertip(button, tooltip)

        self.folder_path_label = tk.Label(self.top_frame, text="")
        self.folder_path_label.pack(side=tk.RIGHT, padx=5)

        image_toggle = tk.Checkbutton(
            self.top_frame,
            text="显示图片",
            variable=self.show_images_var,
            command=self.toggle_image_display,
        )
        image_toggle.pack(side=tk.RIGHT, padx=5)
        Hovertip(image_toggle, "显示或隐藏图片")

    def create_sorting_options(self):
        tk.Label(self.sorting_frame, text="排序 (Sort by):").pack(side=tk.LEFT, padx=5)
        self.sorting_var = tk.StringVar(value="filename")
        sorting_options = [
            ("文件名 (Filename)", "filename"),
            ("演员 (Actors)", "actors"),
            ("系列 (Series)", "series"),
            ("评分 (Rating)", "rating"),
        ]
        for text, value in sorting_options:
            tk.Radiobutton(
                self.sorting_frame,
                text=text,
                variable=self.sorting_var,
                value=value,
                command=self.sort_files,
            ).pack(side=tk.LEFT, padx=5)

    def create_filter_frame(self):
        # 创建筛选框架
        filter_frame = tk.Frame(self.sorting_frame)
        filter_frame.pack(side=tk.RIGHT, padx=10)

        # 创建字段下拉菜单
        self.field_var = tk.StringVar(value="title")
        field_options = [
            ("标题", "title"),
            ("标签", "tag"),
            ("演员", "actor"),
            ("系列", "series"),
            ("评分", "rating"),
        ]
        field_menu = ttk.Combobox(
            filter_frame, textvariable=self.field_var, width=8, state="readonly"
        )
        field_menu["values"] = [opt[0] for opt in field_options]
        field_menu.current(0)
        field_menu.pack(side=tk.LEFT, padx=2)

        # 创建条件下拉菜单
        self.condition_var = tk.StringVar(value="contains")
        self.rating_conditions = [
            ("包含", "contains"),
            ("大于", "greater"),
            ("小于", "less"),
        ]
        self.normal_conditions = [("包含", "contains")]

        condition_menu = ttk.Combobox(
            filter_frame, textvariable=self.condition_var, width=8, state="readonly"
        )
        condition_menu["values"] = [opt[0] for opt in self.normal_conditions]
        condition_menu.current(0)
        condition_menu.pack(side=tk.LEFT, padx=2)

        # 修改后的代码：
        def on_field_change(event=None):
            if self.field_var.get() == "评分":
                condition_menu["values"] = [opt[0] for opt in self.rating_conditions]
            else:
                condition_menu["values"] = [opt[0] for opt in self.normal_conditions]
                self.condition_var.set("包含")

            # 清空筛选输入框
            self.filter_entry.delete(0, tk.END)

        field_menu.bind("<<ComboboxSelected>>", on_field_change)

        # 创建输入框
        self.filter_entry = tk.Entry(filter_frame, width=15)
        self.filter_entry.pack(side=tk.LEFT, padx=2)

        # 创建筛选按钮
        filter_button = tk.Button(filter_frame, text="筛选", command=self.apply_filter)
        filter_button.pack(side=tk.LEFT, padx=2)
        Hovertip(filter_button, "根据条件筛选文件列表")

    def apply_filter(self):
        # 获取筛选条件
        field = self.field_var.get()
        condition = self.condition_var.get()
        filter_text = self.filter_entry.get().strip()

        # 映射显示值到实际值
        condition_map = dict(self.rating_conditions + self.normal_conditions)
        condition_value = condition_map.get(condition, "contains")

        # 如果筛选条件为空，显示所有文件
        if not filter_text:
            self.load_files_in_folder()
            return

        # 清空当前文件列表
        self.file_treeview.delete(*self.file_treeview.get_children())

        # 遍历所有NFO文件进行筛选
        for root, dirs, files in os.walk(self.folder_path):
            for file in files:
                if file.endswith(".nfo"):
                    nfo_file_path = os.path.join(root, file)
                    try:
                        # 解析NFO文件
                        tree = ET.parse(nfo_file_path)
                        root_elem = tree.getroot()

                        # 获取字段值
                        if field == "标题":
                            elem = root_elem.find("title")
                            value = (
                                elem.text.strip()
                                if elem is not None and elem.text
                                else ""
                            )
                        elif field == "标签":
                            tags = [
                                tag.text.strip()
                                for tag in root_elem.findall("tag")
                                if tag.text and tag.text.strip()
                            ]
                            value = ", ".join(tags)
                        elif field == "演员":
                            actors = [
                                actor.find("name").text.strip()
                                for actor in root_elem.findall("actor")
                                if actor.find("name") is not None
                                and actor.find("name").text
                                and actor.find("name").text.strip()
                            ]
                            value = ", ".join(actors)
                        elif field == "系列":
                            elem = root_elem.find("series")
                            value = (
                                elem.text.strip()
                                if elem is not None and elem.text
                                else ""
                            )
                        elif field == "评分":
                            elem = root_elem.find("rating")
                            value = (
                                elem.text.strip()
                                if elem is not None and elem.text
                                else "0"
                            )

                        # 根据条件进行筛选
                        match = False
                        if field == "评分":
                            try:
                                current_value = float(value)
                                filter_value = float(filter_text)
                                if condition_value == "contains":
                                    match = str(filter_value) in str(current_value)
                                elif condition_value == "greater":
                                    match = current_value > filter_value
                                elif condition_value == "less":
                                    match = current_value < filter_value
                            except ValueError:
                                continue
                        else:
                            # 非评分字段只进行包含判断
                            match = filter_text.lower() in value.lower()

                        # 如果匹配，添加到列表中
                        if match:
                            relative_path = os.path.relpath(
                                nfo_file_path, self.folder_path
                            )
                            parts = relative_path.split(os.sep)

                            if len(parts) > 1:
                                first_level_dirs = (
                                    os.sep.join(parts[:-2]) if len(parts) > 2 else ""
                                )
                                second_level_dir = parts[-2]
                                nfo_file = parts[-1]
                            else:
                                first_level_dirs = ""
                                second_level_dir = ""
                                nfo_file = parts[-1]

                            self.file_treeview.insert(
                                "",
                                "end",
                                values=(first_level_dirs, second_level_dir, nfo_file),
                            )

                    except ET.ParseError:
                        continue
                    except Exception as e:
                        print(f"Error processing {nfo_file_path}: {str(e)}")
                        continue

    def create_file_list(self, parent):
        listbox_frame = tk.Frame(parent, width=150)
        listbox_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        columns = ("一级目录", "二级目录", "NFO文件")
        self.file_treeview = ttk.Treeview(
            listbox_frame, columns=columns, show="headings"
        )
        self.file_treeview.heading("一级目录", text="一级目录")
        self.file_treeview.heading("二级目录", text="二级目录")
        self.file_treeview.heading("NFO文件", text="NFO文件")
        self.file_treeview.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.file_treeview.column("NFO文件", width=0, minwidth=0)
        self.file_treeview.column("一级目录")
        self.file_treeview.column("二级目录")

        scrollbar = tk.Scrollbar(
            listbox_frame, orient=tk.VERTICAL, command=self.file_treeview.yview
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_treeview.config(yscrollcommand=scrollbar.set)

        self.file_treeview.bind("<<TreeviewSelect>>", self.on_file_select)
        self.file_treeview.bind("<Delete>", self.delete_selected_folders)  # 添加这一行

        # 设置焦点和选择绑定
        self.file_treeview.bind("<KeyRelease-Up>", lambda e: self.on_file_select(e))
        self.file_treeview.bind("<KeyRelease-Down>", lambda e: self.on_file_select(e))

        # 自动设置焦点到文件列表
        self.file_treeview.focus_force()

    def create_sorted_list(self, parent):
        sorted_list_frame = tk.Frame(parent, width=300)
        sorted_list_frame.pack(side=tk.LEFT, fill=tk.Y, expand=False)

        self.sorted_treeview = ttk.Treeview(
            sorted_list_frame, columns=("目标文件夹",), show="headings"
        )
        self.sorted_treeview.heading("目标文件夹", text="目标文件夹")
        self.sorted_treeview.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.sorted_treeview.column("目标文件夹", width=280)
        self.sorted_treeview.bind("<Button-1>", self.on_sorted_treeview_select)
        self.sorted_treeview.bind("<Double-1>", self.on_sorted_treeview_double_click)

        scrollbar = tk.Scrollbar(
            sorted_list_frame, orient=tk.VERTICAL, command=self.sorted_treeview.yview
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.sorted_treeview.config(yscrollcommand=scrollbar.set)

    def create_fields_frame(self, parent):
        self.fields_frame = tk.Frame(parent, padx=10, pady=10)
        self.fields_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        image_frame = tk.Frame(self.fields_frame)
        image_frame.pack(anchor=tk.W, pady=10)

        image_label = tk.Label(image_frame, text="图片:", font=("DengXian", 12, "bold"))
        image_label.pack(side=tk.LEFT, padx=5, pady=5)

        poster_frame = tk.Frame(
            image_frame,
            width=165,
            height=225,
            highlightthickness=1,
            highlightbackground="black",
        )
        poster_frame.pack(side=tk.LEFT, padx=5)
        poster_frame.pack_propagate(0)

        thumb_frame = tk.Frame(
            image_frame,
            width=333,
            height=225,
            highlightthickness=1,
            highlightbackground="black",
        )
        thumb_frame.pack(side=tk.LEFT, padx=5)
        thumb_frame.pack_propagate(0)

        self.poster_label = tk.Label(poster_frame, text="封面图 (poster)", fg="black")
        self.poster_label.pack(expand=True)
        self.poster_label.bind(
            "<Button-1>", lambda event: self.open_image_and_crop("fanart")
        )

        self.thumb_label = tk.Label(thumb_frame, text="缩略图 (thumb)", fg="black")
        self.thumb_label.pack(expand=True)
        self.thumb_label.bind(
            "<Button-1>", lambda event: self.open_image_and_crop("fanart")
        )

        self.create_field_labels()

    def create_field_labels(self):
        fields = {
            "num": ("番号", 1),
            "title": ("标题", 2),
            "plot": ("简介", 5),
            "tags": ("标签", 3),
            "genres": ("类别", 3),
            "actors": ("演员", 1),
            "series": ("系列", 1),
            "rating": ("评分", 1),
        }

        for field, (label_text, height) in fields.items():
            frame = tk.Frame(self.fields_frame)
            frame.pack(fill=tk.X)

            label = tk.Label(
                frame, text=label_text + ":", font=("DengXian", 12, "bold")
            )
            label.pack(side=tk.LEFT, padx=5, pady=5, anchor=tk.W)

            if field == "num":
                # 创建一个新的Frame来容纳番号和发行日期
                num_frame = tk.Frame(frame)
                num_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

                # 创建左侧Frame用于番号
                left_frame = tk.Frame(num_frame)
                left_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

                # 创建番号标签
                entry = tk.Label(
                    left_frame,
                    text="",
                    fg="blue",
                    cursor="hand2",
                    anchor="w",
                    font=("DengXian", 12, "bold"),
                )
                entry.pack(side=tk.LEFT, padx=5)
                entry.bind("<Button-1>", self.open_num_url)

                # 创建右侧Frame用于发行日期
                right_frame = tk.Frame(num_frame)
                right_frame.pack(side=tk.RIGHT)  # 第二个值50可以调大调小来控制右边距

                # 创建发行日期标签文字
                release_text = tk.Label(
                    right_frame,
                    text="<<发行日期",
                    anchor="e",
                    font=("DengXian", 12, "bold"),
                )
                release_text.pack(side=tk.RIGHT, padx=(0, 5))

                # 创建发行日期值标签
                self.release_label = tk.Label(
                    right_frame, text="", anchor="w", font=("DengXian", 12)
                )
                self.release_label.pack(side=tk.LEFT)

                self.fields_entries[field] = entry
            elif field == "rating":
                entry = tk.Text(frame, width=60, height=height)
                entry.pack(side=tk.LEFT, padx=5, pady=5, fill=tk.X, expand=True)
                entry.bind("<FocusIn>", self.on_rating_focus_in)
                entry.bind("<KeyRelease>", self.on_rating_key_release)
            else:
                entry = tk.Text(frame, width=60, height=height)
                entry.pack(side=tk.LEFT, padx=5, pady=5, fill=tk.X, expand=True)

            self.fields_entries[field] = entry

    def on_rating_focus_in(self, event):
        """当评分输入框获得焦点时，全选内容"""
        event.widget.tag_add("sel", "1.0", "end-1c")
        return "break"  # 防止默认行为

    def on_rating_key_release(self, event):
        """处理评分输入的格式化"""
        try:
            # 获取输入值并去除空格
            current_text = event.widget.get("1.0", "end").strip()

            # 空值不处理
            if not current_text:
                return

            # 获取按键输入的字符
            key_char = event.char

            # 如果按下的是数字键
            if key_char.isdigit():
                # 如果当前文本包含小数点（即已经格式化过）
                if "." in current_text:
                    # 获取小数点前的数字
                    main_num = current_text.split(".")[0]
                    # 新数字作为小数点后的值
                    formatted_rating = f"{main_num}.{key_char}"

                    # 检查是否超过9.9
                    if float(formatted_rating) <= 9.9:
                        event.widget.delete("1.0", "end")
                        event.widget.insert("1.0", formatted_rating)
                    else:
                        event.widget.delete("1.0", "end")
                        event.widget.insert("1.0", "9.9")
                # 如果是单个数字，格式化为 x.0
                elif current_text.isdigit():
                    formatted_rating = f"{float(current_text):.1f}"
                    event.widget.delete("1.0", "end")
                    event.widget.insert("1.0", formatted_rating)

            event.widget.mark_set(tk.INSERT, "end-1c")  # 光标移到末尾

        except Exception as e:
            print(f"处理评分输入时出错: {str(e)}")

    def create_operations_panel(self):
        operations_frame = tk.Frame(self.fields_frame, padx=10, pady=10)
        operations_frame.pack(fill=tk.X)

        save_button = tk.Button(
            operations_frame,
            text="保存更改 (Save Changes)",
            command=self.save_changes,
            width=25,
        )
        save_button.grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)

        batch_filling_button = tk.Button(
            operations_frame,
            text="批量填充 (Batch Filling)",
            command=self.batch_filling,
            width=25,
        )
        batch_filling_button.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)

        batch_add_button = tk.Button(
            operations_frame,
            text="批量新增 (Batch Add)",
            command=self.batch_add,
            width=25,
        )
        batch_add_button.grid(row=0, column=2, padx=5, pady=5, sticky=tk.W)

        self.save_time_label = tk.Label(operations_frame, text="")
        self.save_time_label.grid(
            row=1, column=0, columnspan=2, padx=5, pady=5, sticky=tk.W
        )

    def open_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.folder_path = folder_selected
            self.folder_path_label.config(text=self.folder_path)
            self.load_files_in_folder()

    def load_files_in_folder(self):
        self.file_treeview.delete(*self.file_treeview.get_children())
        self.nfo_files = []
        try:
            for root, dirs, files in os.walk(self.folder_path):
                for file in files:
                    if file.endswith(".nfo"):
                        self.nfo_files.append(os.path.join(root, file))
                        nfo_file_path = os.path.join(root, file)
                        relative_path = os.path.relpath(nfo_file_path, self.folder_path)
                        parts = relative_path.split(os.sep)

                        if len(parts) > 1:
                            nfo_file = parts[-1]
                            second_level_dir = parts[-2]
                            first_level_dirs = os.sep.join(parts[:-2])
                        else:
                            nfo_file = parts[-1]
                            second_level_dir = ""
                            first_level_dirs = ""

                        self.file_treeview.insert(
                            "",
                            "end",
                            values=(first_level_dirs, second_level_dir, nfo_file),
                        )

            # 设置焦点和选中第一项
            if self.file_treeview.get_children():
                first_item = self.file_treeview.get_children()[0]
                self.file_treeview.focus(first_item)
                self.file_treeview.selection_set(first_item)
                self.file_treeview.see(first_item)
                self.on_file_select(None)

        except OSError as e:
            messagebox.showerror("Error", f"Error loading files from folder: {str(e)}")

    def delete_selected_folders(self, event):
        """当按下删除键时，确认后将选中的文件夹移动到回收站"""
        selected_items = self.file_treeview.selection()
        if not selected_items:
            return

        # 获取选中的文件夹数量
        count = len(selected_items)

        # 构建确认消息
        if count == 1:
            item = self.file_treeview.item(selected_items[0])
            values = item["values"]
            folder_name = values[1] if values[1] else values[0]
            message = f"确定要将文件夹 '{folder_name}' 移动到回收站吗？"
        else:
            message = f"确定要将这 {count} 个文件夹移动到回收站吗？"

        # 弹出确认对话框
        if not messagebox.askyesno("确认删除", message):
            return

        try:
            for selected_item in selected_items:
                item = self.file_treeview.item(selected_item)
                values = item["values"]

                # 构建文件夹路径
                if values[1]:  # 如果有二级目录
                    folder_path = os.path.join(self.folder_path, values[0], values[1])
                else:  # 如果只有一级目录
                    folder_path = os.path.join(self.folder_path, values[0])

                # 检查文件夹是否存在
                if os.path.exists(folder_path):
                    try:
                        winshell.delete_file(
                            folder_path, no_confirm=True
                        )  # 删除到回收站
                        self.file_treeview.delete(selected_item)  # 从列表中移除
                    except Exception as e:
                        print(f"删除文件夹失败: {folder_path}\n错误信息: {str(e)}")
                else:
                    # 如果文件夹不存在，仅从列表中移除
                    self.file_treeview.delete(selected_item)

        except Exception as e:
            messagebox.showerror("错误", f"删除文件夹时发生错误: {str(e)}")

    def open_selected_nfo(self):
        selected_items = self.file_treeview.selection()
        for selected_item in selected_items:
            item = self.file_treeview.item(selected_item)
            values = item["values"]
            if values[2]:
                nfo_file_path = (
                    os.path.join(self.folder_path, values[0], values[1], values[2])
                    if values[1]
                    else os.path.join(self.folder_path, values[0], values[2])
                )
                if os.path.exists(nfo_file_path):
                    os.startfile(nfo_file_path)
                else:
                    messagebox.showerror(
                        "Error", f"NFO file does not exist: {nfo_file_path}"
                    )

    def open_selected_folder(self):
        selected_items = self.file_treeview.selection()
        for selected_item in selected_items:
            item = self.file_treeview.item(selected_item)
            values = item["values"]
            if values[2]:
                nfo_file_path = (
                    os.path.join(self.folder_path, values[0], values[1], values[2])
                    if values[1]
                    else os.path.join(self.folder_path, values[0], values[2])
                )
                if os.path.exists(nfo_file_path):
                    folder_path = os.path.dirname(nfo_file_path)
                    os.startfile(folder_path)
                else:
                    messagebox.showerror(
                        "Error", f"NFO file does not exist: {nfo_file_path}"
                    )

    def open_selected_video(self):
        video_extensions = [
            ".mp4",
            ".mkv",
            ".avi",
            ".mov",
            ".rm",
            ".mpeg",
            ".ts",
            ".strm",
        ]
        selected_items = self.file_treeview.selection()
        for selected_item in selected_items:
            item = self.file_treeview.item(selected_item)
            values = item["values"]
            if values[2]:
                nfo_file_path = (
                    os.path.join(self.folder_path, values[0], values[1], values[2])
                    if values[1]
                    else os.path.join(self.folder_path, values[0], values[2])
                )
                if os.path.exists(nfo_file_path):
                    video_file_base = os.path.splitext(nfo_file_path)[0]
                    for ext in video_extensions:
                        video_file = video_file_base + ext
                        if os.path.exists(video_file):
                            if ext == ".strm":
                                try:
                                    with open(video_file, "r", encoding="utf-8") as f:
                                        strm_url = f.readline().strip()
                                    if strm_url:
                                        # 使用 mpvnet 播放地址
                                        os.system(f'start "" "mpvnet.exe" "{strm_url}"')
                                    else:
                                        messagebox.showerror(
                                            "错误", "STRM文件内容为空或无效。"
                                        )
                                except Exception as e:
                                    messagebox.showerror(
                                        "错误", f"读取STRM文件时发生错误：{e}"
                                    )
                            else:
                                # 直接用 mpvnet 播放文件
                                os.system(f'start "" "mpvnet.exe" "{video_file}"')
                            return
                    messagebox.showerror(
                        "错误",
                        "没有找到支持的格式的视频文件：.mp4, .mkv, .avi, .mov, .strm",
                    )
                else:
                    messagebox.showerror("错误", f"NFO文件不存在：{nfo_file_path}")

    def has_unsaved_changes(self):
        """检查是否有未保存的更改"""
        try:
            # 如果没有当前文件或文件不存在，则无需检查
            if not self.current_file_path or not os.path.exists(self.current_file_path):
                return False

            # 解析当前NFO文件
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            # 检查基本字段是否有变化
            basic_fields = ["title", "plot", "series", "rating"]
            for field in basic_fields:
                current_value = self.fields_entries[field].get(1.0, tk.END).strip()
                elem = root.find(field)
                original_value = (
                    elem.text.strip() if elem is not None and elem.text else ""
                )
                if current_value != original_value:
                    return True

            # 检查演员列表是否有变化
            current_actors = set(
                actor.strip()
                for actor in self.fields_entries["actors"]
                .get(1.0, tk.END)
                .strip()
                .split(",")
                if actor.strip()
            )
            original_actors = {
                actor.find("name").text.strip()
                for actor in root.findall("actor")
                if actor.find("name") is not None and actor.find("name").text
            }
            if current_actors != original_actors:
                return True

            # 检查标签是否有变化
            current_tags = set(
                tag.strip()
                for tag in self.fields_entries["tags"]
                .get(1.0, tk.END)
                .strip()
                .split(",")
                if tag.strip()
            )
            original_tags = {
                tag.text.strip()
                for tag in root.findall("tag")
                if tag is not None and tag.text
            }
            if current_tags != original_tags:
                return True

            # 检查类别是否有变化
            current_genres = set(
                genre.strip()
                for genre in self.fields_entries["genres"]
                .get(1.0, tk.END)
                .strip()
                .split(",")
                if genre.strip()
            )
            original_genres = {
                genre.text.strip()
                for genre in root.findall("genre")
                if genre is not None and genre.text
            }
            if current_genres != original_genres:
                return True

            return False

        except Exception:
            # 发生错误时不打印错误信息，直接返回False
            return False

    def on_file_select(self, event):
        """当选择文件时触发的函数"""
        selected_items = self.file_treeview.selection()
        if not selected_items:
            return

        # 检查当前是否有未保存的更改
        if hasattr(self, "current_file_path") and self.current_file_path:
            if self.has_unsaved_changes():
                result = messagebox.askyesnocancel(
                    "保存更改", "当前有未保存的更改，是否保存？"
                )
                if result is None:  # 用户点击取消
                    return
                if result:  # 用户点击是
                    self.save_changes()  # 这里会清除缓存
                else:  # 用户点击否
                    # 不保存时也清除缓存
                    self.current_file_path = None
                    self.selected_index_cache = None

        for selected_item in selected_items:
            item = self.file_treeview.item(selected_item)
            values = item["values"]
            if values[2]:
                self.current_file_path = (
                    os.path.join(self.folder_path, values[0], values[1], values[2])
                    if values[1]
                    else os.path.join(self.folder_path, values[0], values[2])
                )

                if not os.path.exists(self.current_file_path):
                    self.file_treeview.delete(selected_item)
                    return

                self.load_nfo_fields()
                if self.show_images_var.get():
                    self.display_image()
                self.selected_index_cache = selected_items

    def load_nfo_fields(self):
        for entry in self.fields_entries.values():
            if isinstance(entry, tk.Text):
                entry.delete(1.0, tk.END)
            elif isinstance(entry, tk.Label):
                entry.config(text="")

        # 重置发行日期标签
        if hasattr(self, "release_label"):
            self.release_label.config(text="")

        try:
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            fields_to_load = ["title", "plot", "series", "rating", "num"]
            unique_actors = set()
            tags = []
            genres = []

            for child in root:
                if child.tag in fields_to_load:
                    entry = self.fields_entries.get(child.tag)
                    if entry:
                        if child.tag == "num":
                            entry.config(text=child.text if child.text else "")
                        else:
                            entry.insert(1.0, child.text if child.text else "")
                elif child.tag == "actor":
                    name_elem = child.find("name")
                    if name_elem is not None and name_elem.text:
                        unique_actors.add(name_elem.text)
                elif child.tag == "tag":
                    if child.text:
                        tags.append(child.text)
                elif child.tag == "genre":
                    if child.text:
                        genres.append(child.text)
                elif child.tag == "release":
                    if hasattr(self, "release_label"):
                        release_text = child.text.strip() if child.text else ""
                        if release_text:
                            self.release_label.config(text=f"{release_text}")

            self.fields_entries["actors"].insert(1.0, ", ".join(unique_actors))
            self.fields_entries["tags"].insert(1.0, ", ".join(tags))
            self.fields_entries["genres"].insert(1.0, ", ".join(genres))
        except Exception as e:
            messagebox.showerror("Error", f"Error loading NFO file: {str(e)}")

    def open_num_url(self, event):
        num_value = self.fields_entries["num"].cget("text")
        if num_value:
            url = f"https://javdb.com/search?q={num_value}"
            import webbrowser

            webbrowser.open(url)

    def on_entry_focus_out(self, event):
        """当输入框失去焦点时触发的函数"""
        if hasattr(self, "selected_index_cache") and self.selected_index_cache:
            # 验证每个缓存的选中项是否仍然存在
            valid_items = []
            for item_id in self.selected_index_cache:
                try:
                    # 检查项目是否仍然存在
                    if self.file_treeview.exists(item_id):
                        valid_items.append(item_id)
                except:
                    continue

            # 清除当前选择
            self.file_treeview.selection_remove(*self.file_treeview.selection())

            # 只选择仍然存在的项目
            if valid_items:
                for item_id in valid_items:
                    self.file_treeview.selection_add(item_id)
                    self.file_treeview.see(item_id)

                # 更新缓存
                self.selected_index_cache = valid_items

    def save_changes(self):
        if not self.current_file_path:
            return

        try:
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            title = self.fields_entries["title"].get(1.0, tk.END).strip()
            plot = self.fields_entries["plot"].get(1.0, tk.END).strip()
            actors_text = self.fields_entries["actors"].get(1.0, tk.END).strip()
            series = self.fields_entries["series"].get(1.0, tk.END).strip()
            tags_text = self.fields_entries["tags"].get(1.0, tk.END).strip()
            genres_text = self.fields_entries["genres"].get(1.0, tk.END).strip()
            rating = self.fields_entries["rating"].get(1.0, tk.END).strip()

            # 计算 criticrating 值
            try:
                rating_value = float(rating)
                critic_rating = int(rating_value * 10)  # 将 rating 转换为 criticrating
            except ValueError:
                critic_rating = 0

            # 更新常规字段
            updates = {"title": title, "plot": plot, "series": series, "rating": rating}
            for field, value in updates.items():
                element = root.find(field)
                if element is None:
                    element = ET.SubElement(root, field)
                element.text = value

            # 更新或创建 criticrating 字段
            critic_elem = root.find("criticrating")
            if critic_elem is None:
                critic_elem = ET.SubElement(root, "criticrating")
            critic_elem.text = str(critic_rating)

            # 更新演员信息
            unique_actors = set(actors_text.split(","))
            for actor_elem in root.findall("actor"):
                root.remove(actor_elem)
            for actor_name in unique_actors:
                actor_elem = ET.Element("actor")
                name_elem = ET.SubElement(actor_elem, "name")
                name_elem.text = actor_name.strip()
                root.append(actor_elem)

            # 更新标签
            for tag_elem in root.findall("tag"):
                root.remove(tag_elem)
            tags = tags_text.split(",")
            for tag in tags:
                tag_elem = ET.Element("tag")
                tag_elem.text = tag.strip()
                root.append(tag_elem)

            # 更新类型
            for genre_elem in root.findall("genre"):
                root.remove(genre_elem)
            genres = genres_text.split(",")
            for genre in genres:
                genre_elem = ET.Element("genre")
                genre_elem.text = genre.strip()
                root.append(genre_elem)

            # 保存文件
            xml_str = ET.tostring(root, encoding="utf-8")
            parsed_str = minidom.parseString(xml_str)
            pretty_str = parsed_str.toprettyxml(indent="  ", encoding="utf-8")

            pretty_str = "\n".join(
                [
                    line
                    for line in pretty_str.decode("utf-8").split("\n")
                    if line.strip()
                ]
            )

            with open(self.current_file_path, "w", encoding="utf-8") as file:
                file.write(pretty_str)

            self.update_save_time()

            if self.selected_index_cache:
                self.file_treeview.selection_set(self.selected_index_cache)
                for selected_index in self.selected_index_cache:
                    self.file_treeview.see(selected_index)

        except Exception as e:
            messagebox.showerror("错误", f"保存 NFO 文件时出错: {str(e)}")

    def update_save_time(self):
        self.save_time_label.config(
            text=f"保存时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def sort_files(self):
        sort_by = self.sorting_var.get()

        items = [
            (
                self.file_treeview.set(k, "一级目录"),
                self.file_treeview.set(k, "二级目录"),
                self.file_treeview.set(k, "NFO文件"),
                k,
            )
            for k in self.file_treeview.get_children("")
        ]

        if sort_by == "filename":
            items.sort(key=lambda t: t[2])
        else:

            def get_sort_key(item):
                try:
                    nfo_file_path = os.path.join(
                        self.folder_path, item[0], item[1], item[2]
                    )
                    tree = ET.parse(nfo_file_path)
                    root = tree.getroot()

                    if sort_by == "actors":
                        actors = {
                            actor_elem.find("name").text.strip()
                            for actor_elem in root.findall("actor")
                            if actor_elem.find("name") is not None
                        }
                        return ", ".join(sorted(actors)) if actors else ""

                    elif sort_by == "series":
                        series_elem = root.find("series")
                        return (
                            series_elem.text.strip()
                            if series_elem is not None and series_elem.text is not None
                            else ""
                        )

                    elif sort_by == "rating":
                        rating_elem = root.find("rating")
                        return (
                            rating_elem.text.strip()
                            if rating_elem is not None and rating_elem.text is not None
                            else ""
                        )

                    else:
                        for child in root:
                            if child.tag == sort_by and child.text is not None:
                                return child.text.strip()
                except ET.ParseError:
                    pass
                return ""

            items.sort(key=get_sort_key)

        for i, (一级目录, 二级目录, NFO文件, k) in enumerate(items):
            self.file_treeview.move(k, "", i)

    def batch_filling(self):
        def apply_fill():
            field = field_var.get()
            fill_value = fill_entry.get()
            operation_log = ""

            selected_files = self.file_treeview.selection()
            if not selected_files:
                messagebox.showwarning("警告", "请先选择要填充的文件")
                return

            if field and fill_value:
                for item in selected_files:
                    item_values = self.file_treeview.item(item, "values")
                    nfo_file = os.path.join(
                        self.folder_path, item_values[0], item_values[1], item_values[2]
                    )
                    try:
                        tree = ET.parse(nfo_file)
                        root = tree.getroot()

                        field_elem = root.find(field)
                        if field_elem is None:
                            field_elem = ET.Element(field)
                            root.append(field_elem)

                        field_elem.text = fill_value.strip()

                        xml_str = ET.tostring(root, encoding="utf-8")
                        parsed_str = minidom.parseString(xml_str)
                        pretty_str = parsed_str.toprettyxml(
                            indent="  ", encoding="utf-8"
                        )

                        pretty_lines = pretty_str.decode("utf-8").splitlines()
                        formatted_lines = [
                            line for line in pretty_lines if line.strip()
                        ]
                        formatted_str = "\n".join(formatted_lines)

                        with open(nfo_file, "w", encoding="utf-8") as file:
                            file.write(formatted_str)

                        operation_log += f"{nfo_file}: {field}字段填充成功\n"
                    except Exception as e:
                        operation_log += f"{nfo_file}: {field}字段填充失败 - {str(e)}\n"

            log_text.delete(1.0, tk.END)
            log_text.insert(1.0, operation_log)

        dialog = Toplevel(self.root)
        dialog.title("批量填充 (Batch Fill)")
        dialog.geometry("400x600+325+100")

        tk.Label(dialog, text="选择填充替换字段 (Select Field):").pack(
            pady=5, anchor=tk.W
        )
        field_var = tk.StringVar(value="series")
        tk.Radiobutton(
            dialog, text="系列 (Series)", variable=field_var, value="series"
        ).pack(anchor=tk.W)
        tk.Radiobutton(
            dialog, text="评分 (Rating)", variable=field_var, value="rating"
        ).pack(anchor=tk.W)

        tk.Label(dialog, text="填充替换值 (Fill Field Value):").pack(
            pady=5, anchor=tk.W
        )
        fill_entry = tk.Entry(dialog, width=40)
        fill_entry.pack(pady=5)

        tk.Button(dialog, text="应用填充 (Apply Fill)", command=apply_fill).pack(
            pady=10
        )

        tk.Label(dialog, text="操作日志 (Operation Log):").pack(pady=5, anchor=tk.W)
        log_text = tk.Text(dialog, width=50, height=20)
        log_text.pack(pady=5)

    def batch_add(self):
        def apply_add():
            field = field_var.get()
            add_value = add_entry.get()
            operation_log = ""

            selected_files = self.file_treeview.selection()
            if not selected_files:
                messagebox.showwarning("警告", "请先选择要新增的文件")
                return

            if field and add_value:
                for item in selected_files:
                    item_values = self.file_treeview.item(item, "values")
                    nfo_file = os.path.join(
                        self.folder_path, item_values[0], item_values[1], item_values[2]
                    )
                    try:
                        tree = ET.parse(nfo_file)
                        root = tree.getroot()

                        field_elem = root.find(field)
                        if field_elem is None:
                            field_elem = ET.Element(field)
                            root.append(field_elem)

                        field_elem.text = add_value.strip()

                        xml_str = ET.tostring(root, encoding="utf-8")
                        parsed_str = minidom.parseString(xml_str)
                        pretty_str = parsed_str.toprettyxml(
                            indent="  ", encoding="utf-8"
                        )

                        pretty_lines = pretty_str.decode("utf-8").splitlines()
                        formatted_lines = [
                            line for line in pretty_lines if line.strip()
                        ]
                        formatted_str = "\n".join(formatted_lines)

                        with open(nfo_file, "w", encoding="utf-8") as file:
                            file.write(formatted_str)

                        operation_log += f"{nfo_file}: {field}字段新增成功\n"
                    except Exception as e:
                        operation_log += f"{nfo_file}: {field}字段新增失败 - {str(e)}\n"

            log_text.delete(1.0, tk.END)
            log_text.insert(1.0, operation_log)

        dialog = Toplevel(self.root)
        dialog.title("批量新增 (Batch Add)")
        dialog.geometry("400x600+325+100")

        tk.Label(dialog, text="选择字段新增一个值 (Select Field):").pack(
            pady=5, anchor=tk.W
        )
        field_var = tk.StringVar(value="tag")
        tk.Radiobutton(dialog, text="标签 (Tag)", variable=field_var, value="tag").pack(
            anchor=tk.W
        )
        tk.Radiobutton(
            dialog, text="类型 (Genre)", variable=field_var, value="genre"
        ).pack(anchor=tk.W)

        tk.Label(dialog, text="输入新增值 (Enter Value to Add):").pack(
            pady=5, anchor=tk.W
        )
        add_entry = tk.Entry(dialog, width=40)
        add_entry.pack(pady=5)

        tk.Button(dialog, text="应用新增 (Apply Add)", command=apply_add).pack(pady=10)

        tk.Label(dialog, text="操作日志 (Operation Log):").pack(pady=5, anchor=tk.W)
        log_text = tk.Text(dialog, width=50, height=20)
        log_text.pack(pady=5)

    def load_target_files(self, path):
        self.sorted_treeview.delete(*self.sorted_treeview.get_children())
        if path != os.path.abspath(os.path.join(path, "..")):
            self.sorted_treeview.insert("", "end", values=("..",))
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir():
                    self.sorted_treeview.insert("", "end", values=(entry.name,))

    def start_move_thread(self):
        """启动移动文件的线程"""

        def move_with_system_stdout():
            original_stdout = sys.stdout  # 保存当前的标准输出
            sys.stdout = sys.__stdout__  # 切换到系统标准输出
            try:
                self.move_selected_folder()
            finally:
                # 恢复原来的标准输出
                sys.stdout = original_stdout

        move_thread = threading.Thread(target=move_with_system_stdout)
        move_thread.start()

    def move_selected_folder(self):
        """移动选中的文件夹到目标目录"""
        try:
            # 检查是否选中了文件
            selected_items = self.file_treeview.selection()
            if not selected_items or len(selected_items) == 0:
                messagebox.showwarning("警告", "请先选择要移动的文件夹")
                return

            # 检查是否已选择目标目录
            if not hasattr(self, "current_target_path") or not self.current_target_path:
                messagebox.showerror("错误", "请先选择目标目录")
                return

            # 检查目标目录是否存在
            if not os.path.exists(self.current_target_path):
                messagebox.showerror("错误", "目标目录不存在")
                return

            # 创建进度窗口
            progress_window = Toplevel(self.root)
            progress_window.title("移动进度")
            progress_window.geometry("400x150")

            # 确保进度窗口始终在最前
            progress_window.transient(self.root)
            progress_window.grab_set()

            # 添加进度条和标签
            progress_label = ttk.Label(
                progress_window, text="准备移动...", padding=(10, 5)
            )
            progress_label.pack()

            progress_bar = ttk.Progressbar(
                progress_window, mode="determinate", length=300
            )
            progress_bar.pack(pady=10)

            status_label = ttk.Label(progress_window, text="", padding=(10, 5))
            status_label.pack()

            # 计算总文件数
            total_items = len(selected_items)
            progress_bar["maximum"] = total_items
            current_item = 0

            for selected_item in selected_items:
                current_item += 1
                item = self.file_treeview.item(selected_item)
                values = item["values"]

                # 更新进度显示
                progress_label.config(
                    text=f"正在处理: {values[1] if values[1] else values[0]}"
                )
                progress_bar["value"] = current_item
                status_label.config(text=f"进度: {current_item}/{total_items}")
                progress_window.update()

                # 构建源路径
                if values[1]:  # 如果有二级目录
                    src_folder_path = os.path.join(
                        self.folder_path, values[0], values[1]
                    )
                    folder_name = values[1]
                else:  # 如果只有一级目录
                    src_folder_path = os.path.join(self.folder_path, values[0])
                    folder_name = values[0]

                # 构建目标路径
                dest_folder_path = os.path.join(self.current_target_path, folder_name)

                # 检查源文件夹是否存在
                if not os.path.exists(src_folder_path):
                    print(f"源文件夹不存在，跳过: {src_folder_path}")
                    self.file_treeview.delete(selected_item)
                    continue

                # 检查目标文件夹是否已存在
                if os.path.exists(dest_folder_path):
                    result = messagebox.askyesno(
                        "警告",
                        f"目标目录已存在同名文件夹:\n{dest_folder_path}\n是否覆盖?",
                    )
                    if not result:
                        continue

                try:
                    # 获取源和目标的盘符
                    src_drive = os.path.splitdrive(src_folder_path)[0]
                    dest_drive = os.path.splitdrive(dest_folder_path)[0]

                    if src_drive.upper() == dest_drive.upper():
                        # 同一盘符下使用shutil.move移动
                        status_label.config(
                            text=f"同盘符移动: {current_item}/{total_items}"
                        )
                        progress_window.update()
                        print(f"同盘符移动: {src_folder_path} -> {dest_folder_path}")
                        if os.path.exists(dest_folder_path):
                            shutil.rmtree(dest_folder_path)
                        shutil.move(src_folder_path, dest_folder_path)
                    else:
                        # 不同盘符使用copy和rd命令
                        status_label.config(
                            text=f"跨盘符移动: {current_item}/{total_items}"
                        )
                        progress_window.update()
                        print(f"跨盘符移动: {src_folder_path} -> {dest_folder_path}")
                        if os.path.exists(dest_folder_path):
                            # 使用rd命令删除已存在的目标文件夹
                            rd_cmd = f'rd /s /q "{dest_folder_path}"'
                            subprocess.run(rd_cmd, shell=True, check=True)

                        # 使用copy命令复制文件夹
                        progress_label.config(text=f"正在复制: {folder_name}")
                        copy_cmd = f'cmd /c "echo D | xcopy "{src_folder_path}" "{dest_folder_path}" /E /I /H /R /Y"'
                        result = subprocess.run(
                            copy_cmd,
                            shell=True,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                        )

                        # 检查复制结果
                        if result.returncode == 0 and os.path.exists(dest_folder_path):
                            progress_label.config(
                                text=f"正在删除源文件夹: {folder_name}"
                            )
                            progress_window.update()
                            # 复制成功后，删除源文件夹
                            rd_src_cmd = f'rd /s /q "{src_folder_path}"'
                            del_result = subprocess.run(
                                rd_src_cmd, shell=True, check=True
                            )
                            if del_result.returncode != 0:
                                raise Exception("删除源文件夹失败")
                        else:
                            raise Exception(f"复制失败: {result.stderr}")

                    # 移动成功，从列表中删除该项
                    self.file_treeview.delete(selected_item)
                    print(f"成功移动文件夹: {src_folder_path} -> {dest_folder_path}")

                except subprocess.CalledProcessError as e:
                    error_msg = f"命令执行失败: {str(e)}"
                    print(error_msg)
                    messagebox.showerror("错误", error_msg)
                    continue
                except Exception as e:
                    error_msg = f"移动文件夹失败: {src_folder_path}\n错误信息: {str(e)}"
                    print(error_msg)
                    messagebox.showerror("错误", error_msg)
                    continue

            # 完成后关闭进度窗口
            progress_window.destroy()

        except Exception as e:
            error_msg = f"处理过程中发生错误: {str(e)}"
            print(error_msg)
            messagebox.showerror("错误", error_msg)
        finally:
            if hasattr(self, "current_target_path"):
                # 刷新目标目录显示
                self.load_target_files(self.current_target_path)

    def select_target_folder(self):
        target_folder = filedialog.askdirectory(title="选择目标目录")
        if target_folder:
            self.current_target_path = target_folder
            self.load_target_files(target_folder)

    def load_target_folder(self, path):
        self.sorted_treeview.delete(*self.sorted_treeview.get_children())
        if path != os.path.abspath(os.path.join(path, "..")):
            self.sorted_treeview.insert("", "end", values=("..",))
        for item in os.listdir(path):
            full_path = os.path.join(path, item)
            if os.path.isdir(full_path):
                self.sorted_treeview.insert("", "end", values=(full_path,))

    def on_sorted_treeview_select(self, event):
        item = self.sorted_treeview.selection()
        if item:
            self.selected_sorted_item = self.sorted_treeview.item(item)["values"][0]

    def on_sorted_treeview_double_click(self, event):
        item = self.sorted_treeview.selection()
        if item:
            selected_path = self.sorted_treeview.item(item)["values"][0]
            if selected_path == "..":
                selected_path = os.path.abspath(
                    os.path.join(self.current_target_path, "..")
                )
            else:
                selected_path = os.path.join(self.current_target_path, selected_path)
            self.current_target_path = selected_path
            self.load_target_files(selected_path)

    def open_batch_rename_tool(self):
        """打开重命名工具并设置窗口大小"""
        if not hasattr(self, "folder_path") or not self.folder_path:
            messagebox.showerror("错误", "请先选择NFO目录")
            return

        try:
            from cg_rename import start_rename_process

            # 启动重命名进程，并获取重命名窗口实例
            rename_window = start_rename_process(self.folder_path, self.root)

            if rename_window:
                # 设置窗口大小和位置
                window_width = 900
                window_height = 500
                screen_width = self.root.winfo_screenwidth()
                screen_height = self.root.winfo_screenheight()
                x = (screen_width - window_width) // 2
                y = (screen_height - window_height) // 2
                rename_window.window.geometry(f"{window_width}x{window_height}+{x}+{y}")

                # 设置回调函数
                def on_rename_close():
                    rename_window.window.destroy()
                    self.load_files_in_folder()

                # 设置窗口关闭事件
                rename_window.window.protocol("WM_DELETE_WINDOW", on_rename_close)

        except ImportError:
            messagebox.showerror(
                "错误", "找不到 cg_rename.py 文件，请确保它与主程序在同一目录。"
            )
        except Exception as e:
            messagebox.showerror("错误", f"启动重命名工具时出错：{str(e)}")

    def toggle_image_display(self):
        if self.show_images_var.get():
            self.display_image()
        else:
            if hasattr(self.poster_label, "image") and self.poster_label.image:
                self.poster_label.config(image=None)
                self.poster_label.image = None
            if hasattr(self.thumb_label, "image") and self.thumb_label.image:
                self.thumb_label.config(image=None)
                self.thumb_label.image = None

    def display_image(self):
        if self.current_file_path:
            folder = os.path.dirname(self.current_file_path)
            poster_files = []
            thumb_files = []
            with os.scandir(folder) as entries:
                for entry in entries:
                    name = entry.name.lower()
                    if name.endswith(".jpg"):
                        if "poster" in name:
                            poster_files.append(entry.name)
                        elif "thumb" in name:
                            thumb_files.append(entry.name)

            if poster_files:
                self.load_image(poster_files[0], self.poster_label, (165, 225))
            else:
                self.poster_label.config(text="文件夹内无poster图片", fg="black")

            if thumb_files:
                self.load_image(thumb_files[0], self.thumb_label, (333, 225))
            else:
                self.thumb_label.config(text="文件夹内无thumb图片", fg="black")

    def load_image(self, image_file, label, size):
        folder = os.path.dirname(self.current_file_path)
        image_path = os.path.join(folder, image_file)
        try:
            img = Image.open(image_path)
            img.thumbnail(size, Image.LANCZOS)
            img = ImageTk.PhotoImage(img)
            label.config(image=img)
            label.image = img
        except Exception as e:
            label.config(text="加载图片失败: " + str(e))

    def open_image_and_crop(self, image_type):
        """使用UI方式打开裁剪工具"""
        if not self.current_file_path:
            return

        folder = os.path.dirname(self.current_file_path)
        image_files = [
            f
            for f in os.listdir(folder)
            if f.lower().endswith(".jpg") and image_type in f.lower()
        ]

        if not image_files:
            messagebox.showerror("错误", f"未找到{image_type}图片")
            return

        try:
            # 动态导入裁剪工具
            from cg_crop import EmbyPosterCrop
            from PyQt5.QtWidgets import QApplication

            # 初始化 QApplication
            app = QApplication.instance()
            if not app:  # 防止重复初始化
                app = QApplication([])

            # 获取图片的完整路径
            image_path = os.path.join(folder, image_files[0])

            # 获取NFO文件内容以确定水印设置
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            # 初始化水印配置
            has_subtitle = False
            mark_type = "none"  # 默认无水印

            # 检查tag标签内容
            for tag in root.findall("tag"):
                tag_text = tag.text.lower() if tag.text else ""
                if "中文字幕" in tag_text:
                    has_subtitle = True
                elif "无码破解" in tag_text:
                    mark_type = "umr"
                elif "无码流出" in tag_text:
                    mark_type = "leak"
                elif "无码" in tag_text:
                    mark_type = "wuma"
                if mark_type != "none":
                    break

            # 获取NFO文件的基础名称
            nfo_base_name = os.path.splitext(os.path.basename(self.current_file_path))[
                0
            ]

            # 创建裁剪工具窗口
            crop_tool = EmbyPosterCrop(nfo_base_name=nfo_base_name)

            # 加载初始图片
            crop_tool.load_initial_image(image_path)

            # 设置水印选项
            if has_subtitle:
                crop_tool.sub_check.setChecked(True)
            for button in crop_tool.mark_group.buttons():
                if button.property("value") == mark_type:
                    button.setChecked(True)
                    break

            # 运行裁剪工具窗口
            crop_tool.exec_()

            # 如果显示图片选项是打开的，刷新图片显示
            if self.show_images_var.get():
                self.display_image()

        except ImportError:
            messagebox.showerror(
                "错误", "找不到 cg_crop.py 文件，请确保它与主程序在同一目录。"
            )
        except Exception as e:
            error_msg = str(e)
            print(f"Error: {error_msg}")
            messagebox.showerror("错误", f"启动裁剪工具时出错：{error_msg}")


if __name__ == "__main__":
    if hasattr(ctypes, "windll"):
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    root = tk.Tk()
    app = NFOEditorApp(root)
