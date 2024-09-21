import tkinter as tk
from tkinter import filedialog, messagebox, Toplevel, ttk
import os
import subprocess
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from PIL import Image, ImageTk
from idlelib.tooltip import Hovertip
import xml.dom.minidom as minidom
import io
import shutil
import tempfile
import queue

class ImageLoader:
    def __init__(self):
        self.cache = {}
        self.queue = queue.Queue()

    def load_image(self, image_path, size):
        if image_path in self.cache:
            return self.cache[image_path]
        try:
            with open(image_path, 'rb') as f:
                img_data = f.read()
            img = Image.open(io.BytesIO(img_data))
            img.thumbnail(size, Image.LANCZOS)
            self.cache[image_path] = img
            return img
        except Exception as e:
            raise e

class NFOEditorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("大锤 NFO Editor v9.0.1")

        self.current_file_path = None
        self.fields_entries = {}
        self.show_images_var = tk.BooleanVar(value=False)
        self.image_loader = ImageLoader()
        self.directory_queue = queue.Queue()
        self.image_queue = queue.Queue()

        self.setup_ui()
        self.center_window()
        self.process_directory_queue()
        self.process_image_queue()
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
            ("🔗", self.open_batch_copy_tool, '打开strm同步工具'),
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
        self.file_treeview.column("一级目录", width=150)
        self.file_treeview.column("二级目录", width=150)

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
        self.file_loading_thread = threading.Thread(target=self.scan_nfo_files, args=(self.folder_path,))
        self.file_loading_thread.start()

    def scan_nfo_files(self, folder_path):
        try:
            for root_dir, dirs, files in os.walk(folder_path):
                for file in files:
                    if file.endswith('.nfo'):
                        nfo_file_path = os.path.join(root_dir, file)
                        relative_path = os.path.relpath(nfo_file_path, folder_path)
                        parts = relative_path.split(os.sep)

                        if len(parts) > 1:
                            nfo_file = parts[-1]
                            second_level_dir = parts[-2]
                            first_level_dirs = os.sep.join(parts[:-2])
                        else:
                            nfo_file = parts[-1]
                            second_level_dir = ""
                            first_level_dirs = ""

                        self.directory_queue.put((first_level_dirs, second_level_dir, nfo_file))
        except Exception as e:
            self.directory_queue.put(("Error", "", str(e)))

    def process_directory_queue(self):
        try:
            while not self.directory_queue.empty():
                item = self.directory_queue.get_nowait()
                if item[0] == "Error":
                    messagebox.showerror("Error", f"Error loading files from folder: {item[2]}")
                else:
                    self.file_treeview.insert("", "end", values=item)
        except queue.Empty:
            pass
        self.root.after(100, self.process_directory_queue)

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
                    messagebox.showerror("Error", f"NFO文件不存在: {nfo_file_path}")

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
                    messagebox.showerror("Error", f"NFO文件不存在: {nfo_file_path}")

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
                                try:
                                    with open(video_file, 'r') as strm_file:
                                        video_file_path = strm_file.readline().strip()
                                        if os.path.exists(video_file_path):
                                            os.startfile(video_file_path)
                                            break
                                        else:
                                            messagebox.showerror("错误", f"视频文件路径不存在：{video_file_path}")
                                except Exception as e:
                                    messagebox.showerror("错误", f"读取strm文件失败：{str(e)}")
                            else:
                                os.startfile(video_file)
                                break
                    else:
                        messagebox.showerror("错误", "没有找到支持的格式的视频文件：.mp4, .mkv, .avi, .mov, .strm")
                else:
                    messagebox.showerror("错误", f"NFO文件不存在：{nfo_file_path}")

    def on_file_select(self, event):
        selected_items = self.file_treeview.selection()
        if selected_items:
            for selected_item in selected_items:
                item = self.file_treeview.item(selected_item)
                values = item["values"]
                if values[2]:
                    self.current_file_path = os.path.join(self.folder_path, values[0], values[1], values[2]) if values[1] else os.path.join(self.folder_path, values[0], values[2])
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
        if hasattr(self, 'selected_index_cache') and self.selected_index_cache:
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

            if hasattr(self, 'selected_index_cache') and self.selected_index_cache:
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
                        actors = {actor_elem.find('name').text.strip() for actor_elem in root.findall('actor') if actor_elem.find('name') is not None and actor_elem.find('name').text}
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
                except Exception:
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
        move_thread = threading.Thread(target=self.move_selected_folder)
        move_thread.start()

    def move_selected_folder(self):
        selected_items = self.file_treeview.selection()
        if not selected_items:
            messagebox.showwarning("警告", "请先选择要移动的文件夹")
            return

        for selected_item in selected_items:
            item = self.file_treeview.item(selected_item)
            values = item["values"]
            src_folder_path = os.path.join(self.folder_path, values[0], values[1])
            dest_folder_path = os.path.join(self.current_target_path, os.path.basename(src_folder_path))
            if os.path.exists(dest_folder_path):
                messagebox.showerror("错误", f"目标目录中已存在同名文件夹: {dest_folder_path}")
                continue
            try:
                robocopy_cmd = f'robocopy "{src_folder_path}" "{dest_folder_path}" /MOVE /E /R:3 /W:5 /MT'
                result = subprocess.run(robocopy_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                
                # Robocopy 返回码：
                # 0 - No changes were made.
                # 1 - All files were copied successfully.
                # 2-7 - Various levels of success.
                # >=8 - Errors occurred.
                if result.returncode >= 8:
                    messagebox.showerror("错误", f"移动文件夹失败: {result.stderr}")
                else:
                    self.sorted_treeview.insert("", "end", values=(dest_folder_path,))
                    self.file_treeview.delete(selected_item)
            except Exception as e:
                messagebox.showerror("错误", f"移动文件夹失败: {str(e)}")

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

    def open_batch_copy_tool(self):
        from cg_strm import BatchCopyTool
        new_window = tk.Toplevel(self.root)
        batch_copy_tool_app = BatchCopyTool(new_window, self.folder_path)
        new_window.grab_set()

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
            try:
                poster_files = [f for f in os.listdir(folder) if f.lower().endswith('.jpg') and 'poster' in f.lower()]
                thumb_files = [f for f in os.listdir(folder) if f.lower().endswith('.jpg') and 'thumb' in f.lower()]
            except Exception as e:
                self.poster_label.config(text="读取文件夹失败", fg="black")
                self.thumb_label.config(text="读取文件夹失败", fg="black")
                return

            if poster_files:
                self.load_image_async(poster_files[0], self.poster_label, (165, 225))
            else:
                self.poster_label.config(text="文件夹内无poster图片", fg="black")

            if thumb_files:
                self.load_image_async(thumb_files[0], self.thumb_label, (333, 225))
            else:
                self.thumb_label.config(text="文件夹内无thumb图片", fg="black")

    def load_image_async(self, image_file, label, size):
        image_path = os.path.join(os.path.dirname(self.current_file_path), image_file)
        threading.Thread(target=self.enqueue_image, args=(image_path, label, size)).start()

    def enqueue_image(self, image_path, label, size):
        try:
            img = self.image_loader.load_image(image_path, size)
            self.image_queue.put((label, img))
        except Exception as e:
            self.image_queue.put((label, f"加载图片失败: {e}"))

    def process_image_queue(self):
        try:
            while not self.image_queue.empty():
                label, img = self.image_queue.get_nowait()
                if isinstance(img, Image.Image):
                    img_tk = ImageTk.PhotoImage(img)
                    label.config(image=img_tk)
                    label.image = img_tk
                else:
                    label.config(text=img, fg="black")
        except queue.Empty:
            pass
        self.root.after(100, self.process_image_queue)

    def process_directory_queue(self):
        try:
            while not self.directory_queue.empty():
                item = self.directory_queue.get_nowait()
                if item[0] == "Error":
                    messagebox.showerror("Error", f"Error loading files from folder: {item[2]}")
                else:
                    self.file_treeview.insert("", "end", values=item)
        except queue.Empty:
            pass
        self.root.after(100, self.process_directory_queue)

    def load_image(self, image_file, label, size):
        folder = os.path.dirname(self.current_file_path)
        image_path = os.path.join(folder, image_file)
        try:
            img = self.image_loader.load_image(image_path, size)
            img_tk = ImageTk.PhotoImage(img)
            label.config(image=img_tk)
            label.image = img_tk
        except Exception as e:
            label.config(text="加载图片失败: " + str(e))

    def launch_crop_tool(self, image_path):
        from PyQt5 import QtWidgets
        from cg_crop import Ui_Dialog_cut_poster
        import sys

        app = QtWidgets.QApplication(sys.argv)
        main_window = QtWidgets.QMainWindow()
        ui = Ui_Dialog_cut_poster(main_window)
        ui.setupUi(main_window)
        ui.load_image(image_path)  # 使用新的 load_image 方法
        main_window.show()
        app.exec_()

    def open_image_and_crop(self, image_type):
        folder = os.path.dirname(self.current_file_path)
        try:
            image_files = [f for f in os.listdir(folder) if f.lower().endswith('.jpg') and image_type in f.lower()]
            if image_files:
                self.launch_crop_tool(os.path.join(folder, image_files[0]))
            else:
                messagebox.showerror("错误", f"未找到{image_type}图片")
        except Exception as e:
            messagebox.showerror("错误", f"读取图片文件夹失败: {str(e)}")

if __name__ == "__main__":
    root = tk.Tk()
    app = NFOEditorApp(root)
