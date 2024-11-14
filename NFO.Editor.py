import tkinter as tk
from tkinter import filedialog, messagebox, Toplevel, ttk
import os
import shutil
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from PIL import Image, ImageTk
from idlelib.tooltip import Hovertip
import xml.dom.minidom as minidom
import subprocess
import sys
from PyQt5 import QtWidgets
from cg_crop import EmbyPosterCrop

class NFOEditorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("大锤 NFO Editor v9.0.9")

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
        self.root.geometry(f'{width}x{height}+{x}+{y}')

    def setup_ui(self):
        self.top_frame = tk.Frame(self.root)
        self.top_frame.pack(side=tk.TOP, fill=tk.X)
        self.create_top_buttons()

        self.sorting_frame = tk.Frame(self.root)
        self.sorting_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        self.create_sorting_options()

        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.create_file_list(self.main_frame)
        self.create_sorted_list(self.main_frame)
        self.create_fields_frame(self.main_frame)
        self.create_operations_panel()

    def create_top_buttons(self):
        buttons_info = [
            ("选择nfo目录", self.open_folder, '选择目录以加载NFO文件'),
            ("选择整理目录", self.select_target_folder, '选择整理目录'),
            ("🖊", self.open_selected_nfo, '打开选中的NFO文件'),
            ("📁", self.open_selected_folder, '打开选中的文件夹'),
            ("⏯", self.open_selected_video, '播放选中的视频文件'),
            ("🔗", self.open_batch_rename_tool, '统一演员名并重命名文件夹'),
            ("🔁", self.load_files_in_folder, '刷新文件列表'),
            ("=>", self.start_move_thread, '移动nfo所在文件夹到目标目录'),
        ]

        for text, command, tooltip in buttons_info:
            button = tk.Button(self.top_frame, text=text, command=command, font=("Arial", 12))
            button.pack(side=tk.LEFT, padx=5)
            Hovertip(button, tooltip)

        self.folder_path_label = tk.Label(self.top_frame, text="")
        self.folder_path_label.pack(side=tk.RIGHT, padx=5)

        image_toggle = tk.Checkbutton(self.top_frame, text="显示图片", variable=self.show_images_var, command=self.toggle_image_display)
        image_toggle.pack(side=tk.RIGHT, padx=5)
        Hovertip(image_toggle, '显示或隐藏图片')

    def create_sorting_options(self):
        tk.Label(self.sorting_frame, text="排序 (Sort by):").pack(side=tk.LEFT, padx=5)
        self.sorting_var = tk.StringVar(value="filename")
        sorting_options = [("文件名 (Filename)", "filename"), ("演员 (Actors)", "actors"), ("系列 (Series)", "series"), ("评分 (Rating)", "rating")]
        for text, value in sorting_options:
            tk.Radiobutton(self.sorting_frame, text=text, variable=self.sorting_var, value=value, command=self.sort_files).pack(side=tk.LEFT, padx=5)

    def create_file_list(self, parent):
        listbox_frame = tk.Frame(parent, width=150)
        listbox_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        columns = ("一级目录", "二级目录", "NFO文件")
        self.file_treeview = ttk.Treeview(listbox_frame, columns=columns, show="headings")
        self.file_treeview.heading("一级目录", text="一级目录")
        self.file_treeview.heading("二级目录", text="二级目录")
        self.file_treeview.heading("NFO文件", text="NFO文件")
        self.file_treeview.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.file_treeview.column("NFO文件", width=0, minwidth=0)
        self.file_treeview.column("一级目录")
        self.file_treeview.column("二级目录")

        scrollbar = tk.Scrollbar(listbox_frame, orient=tk.VERTICAL, command=self.file_treeview.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_treeview.config(yscrollcommand=scrollbar.set)

        self.file_treeview.bind('<<TreeviewSelect>>', self.on_file_select)

    def create_sorted_list(self, parent):
        sorted_list_frame = tk.Frame(parent, width=300)
        sorted_list_frame.pack(side=tk.LEFT, fill=tk.Y, expand=False)

        self.sorted_treeview = ttk.Treeview(sorted_list_frame, columns=("目标文件夹",), show="headings")
        self.sorted_treeview.heading("目标文件夹", text="目标文件夹")
        self.sorted_treeview.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.sorted_treeview.column("目标文件夹", width=280)
        self.sorted_treeview.bind("<Button-1>", self.on_sorted_treeview_select)
        self.sorted_treeview.bind("<Double-1>", self.on_sorted_treeview_double_click)

        scrollbar = tk.Scrollbar(sorted_list_frame, orient=tk.VERTICAL, command=self.sorted_treeview.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.sorted_treeview.config(yscrollcommand=scrollbar.set)

    def create_fields_frame(self, parent):
        self.fields_frame = tk.Frame(parent, padx=10, pady=10)
        self.fields_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        image_frame = tk.Frame(self.fields_frame)
        image_frame.pack(anchor=tk.W, pady=10)

        image_label = tk.Label(image_frame, text="图片:", font=("Arial", 12, "bold"))
        image_label.pack(side=tk.LEFT, padx=5, pady=5)

        poster_frame = tk.Frame(image_frame, width=165, height=225, highlightthickness=1, highlightbackground="black")
        poster_frame.pack(side=tk.LEFT, padx=5)
        poster_frame.pack_propagate(0)

        thumb_frame = tk.Frame(image_frame, width=333, height=225, highlightthickness=1, highlightbackground="black")
        thumb_frame.pack(side=tk.LEFT, padx=5)
        thumb_frame.pack_propagate(0)

        self.poster_label = tk.Label(poster_frame, text="封面图 (poster)", fg="black")
        self.poster_label.pack(expand=True)
        self.poster_label.bind("<Button-1>", lambda event: self.open_image_and_crop('fanart'))

        self.thumb_label = tk.Label(thumb_frame, text="缩略图 (thumb)", fg="black")
        self.thumb_label.pack(expand=True)
        self.thumb_label.bind("<Button-1>", lambda event: self.open_image_and_crop('fanart'))

        self.create_field_labels()

    def create_field_labels(self):
        fields = {
            'num': ('番号', 1),
            'title': ('标题', 2),
            'plot': ('简介', 5),
            'tags': ('标签', 3),
            'genres': ('类别', 3),
            'actors': ('演员', 1),
            'series': ('系列', 1),
            'rating': ('评分', 1)
        }

        for field, (label_text, height) in fields.items():
            frame = tk.Frame(self.fields_frame)
            frame.pack(fill=tk.X)

            label = tk.Label(frame, text=label_text + ":", font=("Arial", 12, "bold"))
            label.pack(side=tk.LEFT, padx=5, pady=5, anchor=tk.W)

            if field == 'num':
                entry = tk.Label(frame, text="", width=60, height=height, fg="blue", cursor="hand2", anchor='w', font=("Arial", 12, "bold"))
                entry.pack(side=tk.LEFT, padx=5, pady=5, fill=tk.X, expand=True)
                entry.bind("<Button-1>", self.open_num_url)
            else:
                entry = tk.Text(frame, width=60, height=height)
                entry.pack(side=tk.LEFT, padx=5, pady=5, fill=tk.X, expand=True)

            self.fields_entries[field] = entry
            if field != 'num':
                entry.bind('<FocusOut>', self.on_entry_focus_out)

    def create_operations_panel(self):
        operations_frame = tk.Frame(self.fields_frame, padx=10, pady=10)
        operations_frame.pack(fill=tk.X)

        save_button = tk.Button(operations_frame, text="保存更改 (Save Changes)", command=self.save_changes, width=25)
        save_button.grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)

        batch_filling_button = tk.Button(operations_frame, text="批量填充 (Batch Filling)", command=self.batch_filling, width=25)
        batch_filling_button.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)

        batch_add_button = tk.Button(operations_frame, text="批量新增 (Batch Add)", command=self.batch_add, width=25)
        batch_add_button.grid(row=0, column=2, padx=5, pady=5, sticky=tk.W)

        self.save_time_label = tk.Label(operations_frame, text="")
        self.save_time_label.grid(row=1, column=0, columnspan=2, padx=5, pady=5, sticky=tk.W)

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
                    if file.endswith('.nfo'):
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

                        self.file_treeview.insert("", "end", values=(first_level_dirs, second_level_dir, nfo_file))
        except OSError as e:
            messagebox.showerror("Error", f"Error loading files from folder: {str(e)}")

        if self.nfo_files:
            first_item = self.file_treeview.get_children()[0]
            self.file_treeview.selection_set(first_item)
            self.file_treeview.see(first_item)
            self.on_file_select(None)

    def open_selected_nfo(self):
        selected_items = self.file_treeview.selection()
        for selected_item in selected_items:
            item = self.file_treeview.item(selected_item)
            values = item["values"]
            if values[2]:
                nfo_file_path = os.path.join(self.folder_path, values[0], values[1], values[2]) if values[1] else os.path.join(self.folder_path, values[0], values[2])
                if os.path.exists(nfo_file_path):
                    os.startfile(nfo_file_path)
                else:
                    messagebox.showerror("Error", f"NFO file does not exist: {nfo_file_path}")

    def open_selected_folder(self):
        selected_items = self.file_treeview.selection()
        for selected_item in selected_items:
            item = self.file_treeview.item(selected_item)
            values = item["values"]
            if values[2]:
                nfo_file_path = os.path.join(self.folder_path, values[0], values[1], values[2]) if values[1] else os.path.join(self.folder_path, values[0], values[2])
                if os.path.exists(nfo_file_path):
                    folder_path = os.path.dirname(nfo_file_path)
                    os.startfile(folder_path)
                else:
                    messagebox.showerror("Error", f"NFO file does not exist: {nfo_file_path}")

    def open_selected_video(self):
        video_extensions = ['.mp4', '.mkv', '.avi', '.mov', '.strm']
        selected_items = self.file_treeview.selection()
        for selected_item in selected_items:
            item = self.file_treeview.item(selected_item)
            values = item["values"]
            if values[2]:
                nfo_file_path = os.path.join(self.folder_path, values[0], values[1], values[2]) if values[1] else os.path.join(self.folder_path, values[0], values[2])
                if os.path.exists(nfo_file_path):
                    video_file_base = os.path.splitext(nfo_file_path)[0]
                    for ext in video_extensions:
                        video_file = video_file_base + ext
                        if os.path.exists(video_file):
                            if ext == '.strm':
                                with open(video_file, 'r') as strm_file:
                                    video_file_path = strm_file.readline().strip()
                                    if os.path.exists(video_file_path):
                                        os.startfile(video_file_path)
                                        return
                                    else:
                                        messagebox.showerror("错误", f"视频文件路径不存在：{video_file_path}")
                            else:
                                os.startfile(video_file)
                                return
                    messagebox.showerror("错误", "没有找到支持的格式的视频文件：.mp4, .mkv, .avi, .mov, .strm")
                else:
                    messagebox.showerror("错误", f"NFO文件不存在：{nfo_file_path}")

    def on_file_select(self, event):
        """当选择文件时触发的函数"""
        selected_items = self.file_treeview.selection()
        if selected_items:
            for selected_item in selected_items:
                item = self.file_treeview.item(selected_item)
                values = item["values"]
                if values[2]:
                    # 构建文件路径
                    self.current_file_path = os.path.join(self.folder_path, values[0], values[1], values[2]) if values[1] else os.path.join(self.folder_path, values[0], values[2])
                    
                    # 检查文件是否存在
                    if not os.path.exists(self.current_file_path):
                        # 只删除不存在的文件项
                        self.file_treeview.delete(selected_item)
                        return
                    
                    # 文件存在则加载信息和图片
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

        try:
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            fields_to_load = ['title', 'plot', 'series', 'rating', 'num']
            unique_actors = set()
            tags = []
            genres = []

            for child in root:
                if child.tag in fields_to_load:
                    entry = self.fields_entries.get(child.tag)
                    if entry:
                        if child.tag == 'num':
                            entry.config(text=child.text if child.text else "")
                        else:
                            entry.insert(1.0, child.text if child.text else "")
                elif child.tag == 'actor':
                    name_elem = child.find('name')
                    if name_elem is not None and name_elem.text:
                        unique_actors.add(name_elem.text)
                elif child.tag == 'tag':
                    if child.text:
                        tags.append(child.text)
                elif child.tag == 'genre':
                    if child.text:
                        genres.append(child.text)

            self.fields_entries['actors'].insert(1.0, ', '.join(unique_actors))
            self.fields_entries['tags'].insert(1.0, ', '.join(tags))
            self.fields_entries['genres'].insert(1.0, ', '.join(genres))
        except Exception as e:
            messagebox.showerror("Error", f"Error loading NFO file: {str(e)}")

    def open_num_url(self, event):
        num_value = self.fields_entries['num'].cget("text")
        if num_value:
            url = f"https://javdb.com/search?q={num_value}"
            import webbrowser
            webbrowser.open(url)

    def on_entry_focus_out(self, event):
        if self.selected_index_cache:
            for selected_index in self.selected_index_cache:
                self.file_treeview.selection_set(selected_index)
                self.file_treeview.see(selected_index)

    def save_changes(self):
        if not self.current_file_path:
            return

        try:
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            title = self.fields_entries['title'].get(1.0, tk.END).strip()
            plot = self.fields_entries['plot'].get(1.0, tk.END).strip()
            actors_text = self.fields_entries['actors'].get(1.0, tk.END).strip()
            series = self.fields_entries['series'].get(1.0, tk.END).strip()
            tags_text = self.fields_entries['tags'].get(1.0, tk.END).strip()
            genres_text = self.fields_entries['genres'].get(1.0, tk.END).strip()
            rating = self.fields_entries['rating'].get(1.0, tk.END).strip()

            updates = {
                'title': title,
                'plot': plot,
                'series': series,
                'rating': rating
            }

            for field, value in updates.items():
                element = root.find(field)
                if element is None:
                    element = ET.SubElement(root, field)
                element.text = value

            unique_actors = set(actors_text.split(','))
            for actor_elem in root.findall('actor'):
                root.remove(actor_elem)
            for actor_name in unique_actors:
                actor_elem = ET.Element('actor')
                name_elem = ET.SubElement(actor_elem, 'name')
                name_elem.text = actor_name.strip()
                root.append(actor_elem)

            for tag_elem in root.findall('tag'):
                root.remove(tag_elem)
            tags = tags_text.split(',')
            for tag in tags:
                tag_elem = ET.Element('tag')
                tag_elem.text = tag.strip()
                root.append(tag_elem)

            for genre_elem in root.findall('genre'):
                root.remove(genre_elem)
            genres = genres_text.split(',')
            for genre in genres:
                genre_elem = ET.Element('genre')
                genre_elem.text = genre.strip()
                root.append(genre_elem)

            xml_str = ET.tostring(root, encoding='utf-8')
            parsed_str = minidom.parseString(xml_str)
            pretty_str = parsed_str.toprettyxml(indent="  ", encoding='utf-8')

            pretty_str = "\n".join([line for line in pretty_str.decode('utf-8').split('\n') if line.strip()])

            with open(self.current_file_path, 'w', encoding='utf-8') as file:
                file.write(pretty_str)

            self.update_save_time()

            if self.selected_index_cache:
                self.file_treeview.selection_set(self.selected_index_cache)
                for selected_index in self.selected_index_cache:
                    self.file_treeview.see(selected_index)
            
        except Exception as e:
            messagebox.showerror("Error", f"Error saving changes to NFO file: {str(e)}")

    def update_save_time(self):
        self.save_time_label.config(text=f"保存时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    def sort_files(self):
        sort_by = self.sorting_var.get()
        
        items = [(self.file_treeview.set(k, "一级目录"), self.file_treeview.set(k, "二级目录"), self.file_treeview.set(k, "NFO文件"), k) for k in self.file_treeview.get_children("")]

        if sort_by == "filename":
            items.sort(key=lambda t: t[2])
        else:
            def get_sort_key(item):
                try:
                    nfo_file_path = os.path.join(self.folder_path, item[0], item[1], item[2])
                    tree = ET.parse(nfo_file_path)
                    root = tree.getroot()
                    
                    if sort_by == "actors":
                        actors = {actor_elem.find('name').text.strip() for actor_elem in root.findall('actor') if actor_elem.find('name') is not None}
                        return ', '.join(sorted(actors)) if actors else ""
                    
                    elif sort_by == "series":
                        series_elem = root.find('series')
                        return series_elem.text.strip() if series_elem is not None and series_elem.text is not None else ""
                    
                    elif sort_by == "rating":
                        rating_elem = root.find('rating')
                        return rating_elem.text.strip() if rating_elem is not None and rating_elem.text is not None else ""
                    
                    else:
                        for child in root:
                            if child.tag == sort_by and child.text is not None:
                                return child.text.strip()
                except ET.ParseError:
                    pass
                return ""
            
            items.sort(key=get_sort_key)

        for i, (一级目录, 二级目录, NFO文件, k) in enumerate(items):
            self.file_treeview.move(k, '', i)

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
                    nfo_file = os.path.join(self.folder_path, item_values[0], item_values[1], item_values[2])
                    try:
                        tree = ET.parse(nfo_file)
                        root = tree.getroot()

                        field_elem = root.find(field)
                        if field_elem is None:
                            field_elem = ET.Element(field)
                            root.append(field_elem)

                        field_elem.text = fill_value.strip()

                        xml_str = ET.tostring(root, encoding='utf-8')
                        parsed_str = minidom.parseString(xml_str)
                        pretty_str = parsed_str.toprettyxml(indent="  ", encoding='utf-8')

                        pretty_lines = pretty_str.decode('utf-8').splitlines()
                        formatted_lines = [line for line in pretty_lines if line.strip()]
                        formatted_str = "\n".join(formatted_lines)

                        with open(nfo_file, 'w', encoding='utf-8') as file:
                            file.write(formatted_str)

                        operation_log += f"{nfo_file}: {field}字段填充成功\n"
                    except Exception as e:
                        operation_log += f"{nfo_file}: {field}字段填充失败 - {str(e)}\n"

            log_text.delete(1.0, tk.END)
            log_text.insert(1.0, operation_log)

        dialog = Toplevel(self.root)
        dialog.title("批量填充 (Batch Fill)")
        dialog.geometry("400x600+325+100")

        tk.Label(dialog, text="选择填充替换字段 (Select Field):").pack(pady=5, anchor=tk.W)
        field_var = tk.StringVar(value="series")
        tk.Radiobutton(dialog, text="系列 (Series)", variable=field_var, value="series").pack(anchor=tk.W)
        tk.Radiobutton(dialog, text="评分 (Rating)", variable=field_var, value="rating").pack(anchor=tk.W)

        tk.Label(dialog, text="填充替换值 (Fill Field Value):").pack(pady=5, anchor=tk.W)
        fill_entry = tk.Entry(dialog, width=40)
        fill_entry.pack(pady=5)

        tk.Button(dialog, text="应用填充 (Apply Fill)", command=apply_fill).pack(pady=10)

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
                    nfo_file = os.path.join(self.folder_path, item_values[0], item_values[1], item_values[2])
                    try:
                        tree = ET.parse(nfo_file)
                        root = tree.getroot()

                        field_elem = root.find(field)
                        if field_elem is None:
                            field_elem = ET.Element(field)
                            root.append(field_elem)

                        field_elem.text = add_value.strip()

                        xml_str = ET.tostring(root, encoding='utf-8')
                        parsed_str = minidom.parseString(xml_str)
                        pretty_str = parsed_str.toprettyxml(indent="  ", encoding='utf-8')

                        pretty_lines = pretty_str.decode('utf-8').splitlines()
                        formatted_lines = [line for line in pretty_lines if line.strip()]
                        formatted_str = "\n".join(formatted_lines)

                        with open(nfo_file, 'w', encoding='utf-8') as file:
                            file.write(formatted_str)

                        operation_log += f"{nfo_file}: {field}字段新增成功\n"
                    except Exception as e:
                        operation_log += f"{nfo_file}: {field}字段新增失败 - {str(e)}\n"

            log_text.delete(1.0, tk.END)
            log_text.insert(1.0, operation_log)

        dialog = Toplevel(self.root)
        dialog.title("批量新增 (Batch Add)")
        dialog.geometry("400x600+325+100")

        tk.Label(dialog, text="选择字段新增一个值 (Select Field):").pack(pady=5, anchor=tk.W)
        field_var = tk.StringVar(value="tag")
        tk.Radiobutton(dialog, text="标签 (Tag)", variable=field_var, value="tag").pack(anchor=tk.W)
        tk.Radiobutton(dialog, text="类型 (Genre)", variable=field_var, value="genre").pack(anchor=tk.W)

        tk.Label(dialog, text="输入新增值 (Enter Value to Add):").pack(pady=5, anchor=tk.W)
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
        for item in os.listdir(path):
            full_path = os.path.join(path, item)
            if os.path.isdir(full_path):
                self.sorted_treeview.insert("", "end", values=(item,))

    def start_move_thread(self):
        """启动移动文件的线程"""
        def move_with_system_stdout():
            original_stdout = sys.stdout  # 保存当前的标准输出
            sys.stdout = sys.__stdout__   # 切换到系统标准输出
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
            if not hasattr(self, 'current_target_path') or not self.current_target_path:
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
            progress_label = ttk.Label(progress_window, text="准备移动...", padding=(10, 5))
            progress_label.pack()
            
            progress_bar = ttk.Progressbar(progress_window, mode='determinate', length=300)
            progress_bar.pack(pady=10)
            
            status_label = ttk.Label(progress_window, text="", padding=(10, 5))
            status_label.pack()

            # 计算总文件数
            total_items = len(selected_items)
            progress_bar['maximum'] = total_items
            current_item = 0

            for selected_item in selected_items:
                current_item += 1
                item = self.file_treeview.item(selected_item)
                values = item["values"]
                
                # 更新进度显示
                progress_label.config(text=f"正在处理: {values[1] if values[1] else values[0]}")
                progress_bar['value'] = current_item
                status_label.config(text=f"进度: {current_item}/{total_items}")
                progress_window.update()
                
                # 构建源路径
                if values[1]:  # 如果有二级目录
                    src_folder_path = os.path.join(self.folder_path, values[0], values[1])
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
                    result = messagebox.askyesno("警告", 
                        f"目标目录已存在同名文件夹:\n{dest_folder_path}\n是否覆盖?")
                    if not result:
                        continue

                try:
                    # 获取源和目标的盘符
                    src_drive = os.path.splitdrive(src_folder_path)[0]
                    dest_drive = os.path.splitdrive(dest_folder_path)[0]

                    if src_drive.upper() == dest_drive.upper():
                        # 同一盘符下使用shutil.move移动
                        status_label.config(text=f"同盘符移动: {current_item}/{total_items}")
                        progress_window.update()
                        print(f"同盘符移动: {src_folder_path} -> {dest_folder_path}")
                        if os.path.exists(dest_folder_path):
                            shutil.rmtree(dest_folder_path)
                        shutil.move(src_folder_path, dest_folder_path)
                    else:
                        # 不同盘符使用copy和rd命令
                        status_label.config(text=f"跨盘符移动: {current_item}/{total_items}")
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
                            text=True
                        )

                        # 检查复制结果
                        if result.returncode == 0 and os.path.exists(dest_folder_path):
                            progress_label.config(text=f"正在删除源文件夹: {folder_name}")
                            progress_window.update()
                            # 复制成功后，删除源文件夹
                            rd_src_cmd = f'rd /s /q "{src_folder_path}"'
                            del_result = subprocess.run(rd_src_cmd, shell=True, check=True)
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
            if hasattr(self, 'current_target_path'):
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
                selected_path = os.path.abspath(os.path.join(self.current_target_path, ".."))
            else:
                selected_path = os.path.join(self.current_target_path, selected_path)
            self.current_target_path = selected_path
            self.load_target_files(selected_path)

    def open_batch_rename_tool(self):
        if not hasattr(self, 'folder_path') or not self.folder_path:
            messagebox.showerror("错误", "请先选择NFO目录")
            return

        try:
            from cg_rename import start_rename_process
            
            # 启动重命名进程，并获取重命名窗口实例
            rename_window = start_rename_process(self.folder_path, self.root)
            
            if rename_window:
                # 设置回调函数
                def on_rename_close():
                    rename_window.window.destroy()
                    self.load_files_in_folder()
                
                # 设置窗口关闭事件
                rename_window.window.protocol("WM_DELETE_WINDOW", on_rename_close)
                
        except ImportError:
            messagebox.showerror("错误", "找不到 cg_rename.py 文件，请确保它与主程序在同一目录。")
        except Exception as e:
            messagebox.showerror("错误", f"启动重命名工具时出错：{str(e)}")

    def toggle_image_display(self):
        if self.show_images_var.get():
            self.display_image()
        else:
            if hasattr(self.poster_label, 'image') and self.poster_label.image:
                self.poster_label.config(image=None)
                self.poster_label.image = None
            if hasattr(self.thumb_label, 'image') and self.thumb_label.image:
                self.thumb_label.config(image=None)
                self.thumb_label.image = None

    def display_image(self):
        if self.current_file_path:
            folder = os.path.dirname(self.current_file_path)
            poster_files = [f for f in os.listdir(folder) if f.lower().endswith('.jpg') and 'poster' in f.lower()]
            thumb_files = [f for f in os.listdir(folder) if f.lower().endswith('.jpg') and 'thumb' in f.lower()]

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

    def launch_crop_tool(self, image_path, nfo_base_name):
        try:
            if not QtWidgets.QApplication.instance():
                app = QtWidgets.QApplication([])
            
            main_window = QtWidgets.QMainWindow()
            dialog = EmbyPosterCrop(parent=main_window, nfo_base_name=nfo_base_name)
            
            # 使用新方法直接加载图片
            dialog.load_initial_image(image_path)
            
            result = dialog.exec_()
            
            if result == QtWidgets.QDialog.Accepted and self.show_images_var.get():
                self.display_image()
                
        except Exception as e:
            messagebox.showerror("错误", f"处理图片时出错：{str(e)}")

    def open_image_and_crop(self, image_type):
        if not self.current_file_path:
            return
            
        folder = os.path.dirname(self.current_file_path)
        image_files = [f for f in os.listdir(folder) if f.lower().endswith('.jpg') and image_type in f.lower()]
        
        if not image_files:
            messagebox.showerror("错误", f"未找到{image_type}图片")
            return

        try:
            # 获取NFO文件内容
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()
            
            # 初始化水印配置
            has_subtitle = False
            mark_type = "none"  # 默认无水印
            
            # 检查tag标签内容
            for tag in root.findall('tag'):
                tag_text = tag.text.lower() if tag.text else ""
                if "中文字幕" in tag_text:
                    has_subtitle = True
                elif "无码破解" in tag_text:
                    mark_type = "umr"
                elif "无码流出" in tag_text:
                    mark_type = "leak"
                elif "无码" in tag_text:
                    mark_type = "wuma"
                # 如果已经找到非none的mark_type，就不再继续查找
                if mark_type != "none":
                    break

            # 获取当前NFO文件的基础名称
            nfo_base_name = os.path.splitext(os.path.basename(self.current_file_path))[0]
            
            # 创建QApplication实例（如果还没有创建）
            if not QtWidgets.QApplication.instance():
                app = QtWidgets.QApplication([])
            
            # 创建并显示裁剪窗口
            main_window = QtWidgets.QMainWindow()
            dialog = EmbyPosterCrop(main_window, nfo_base_name)
            
            # 设置初始图片
            image_path = os.path.join(folder, image_files[0])
            dialog.image_path = image_path
            dialog.image_label.set_image(image_path)
            
            # 设置水印选项
            if has_subtitle:
                dialog.sub_check.setChecked(True)
                
            # 设置分类水印
            for button in dialog.mark_group.buttons():
                if button.property('value') == mark_type:
                    button.setChecked(True)
                    break
                    
            # 显示对话框并等待结果
            result = dialog.exec_()
            
            # 如果用户确认了操作（点击了"裁剪并关闭"按钮）
            if result == QtWidgets.QDialog.Accepted:
                # 刷新图片显示
                if self.show_images_var.get():
                    self.display_image()
                    
        except Exception as e:
            messagebox.showerror("错误", f"处理图片时出错：{str(e)}")

if __name__ == "__main__":
    root = tk.Tk()
    app = NFOEditorApp(root)
