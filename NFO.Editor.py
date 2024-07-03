import tkinter as tk
from tkinter import filedialog, messagebox, Toplevel
import os
import xml.etree.ElementTree as ET
from datetime import datetime
import xml.dom.minidom as minidom
from PIL import Image, ImageTk
import subprocess
from tkinter import ttk

class NFOEditorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("大锤 NFO Editor v7.0.0")

        self.current_file_path = None
        self.fields_entries = {}

        # 图片显示开关,默认打开图片显示
        self.show_images_var = tk.BooleanVar(value=False)

        # 创建顶部按钮和路径显示
        top_frame = tk.Frame(self.root)
        top_frame.pack(side=tk.TOP, fill=tk.X)

        select_directory_button = tk.Button(top_frame, text="选择目录 (Select Directory)", command=self.open_folder)
        select_directory_button.pack(side=tk.LEFT, padx=5)

        open_nfo_button = tk.Button(top_frame, text="🖊", command=self.open_selected_nfo)
        open_nfo_button.pack(side=tk.LEFT, padx=5)

        open_folder_button = tk.Button(top_frame, text="📁", command=self.open_selected_folder)
        open_folder_button.pack(side=tk.LEFT, padx=5)

        open_video_button = tk.Button(top_frame, text="▶", command=self.open_selected_video)
        open_video_button.pack(side=tk.LEFT, padx=5)

        self.folder_path_label = tk.Label(top_frame, text="")
        self.folder_path_label.pack(side=tk.RIGHT, padx=5)

        # 图片显示开关
        image_toggle = tk.Checkbutton(top_frame, text="显示图片", variable=self.show_images_var, command=self.toggle_image_display)
        image_toggle.pack(side=tk.RIGHT, padx=5)

        # 创建排序选项
        sorting_frame = tk.Frame(self.root)
        sorting_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        
        tk.Label(sorting_frame, text="排序 (Sort by):").pack(side=tk.LEFT, padx=5)
        
        self.sorting_var = tk.StringVar(value="filename")  # 排序方式，默认按文件名排序
        
        sorting_options = [("文件名 (Filename)", "filename"), 
                           ("演员 (Actors)", "actors"), 
                           ("系列 (Series)", "series"), 
                           ("评分 (Rating)", "rating")]
        
        for text, value in sorting_options:
            tk.Radiobutton(sorting_frame, text=text, variable=self.sorting_var, value=value, command=self.sort_files).pack(side=tk.LEFT, padx=5)

        # 创建文件列表框和滚动条
        listbox_frame = tk.Frame(self.root)
        listbox_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        columns = ("一级目录", "二级目录", "NFO文件")
        self.file_treeview = ttk.Treeview(listbox_frame, columns=columns, show="headings")
        self.file_treeview.heading("一级目录", text="一级目录")
        self.file_treeview.heading("二级目录", text="二级目录")
        self.file_treeview.heading("NFO文件", text="NFO文件")
        self.file_treeview.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # 隐藏NFO文件列
        self.file_treeview.column("NFO文件", width=0, minwidth=0)
        # 调整列宽
        self.file_treeview.column("一级目录", width=260)
        self.file_treeview.column("二级目录", width=260)
        
        scrollbar = tk.Scrollbar(listbox_frame, orient=tk.VERTICAL, command=self.file_treeview.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_treeview.config(yscrollcommand=scrollbar.set)

        self.file_treeview.bind('<<TreeviewSelect>>', self.on_file_select)

        # 创建字段编辑框架
        self.fields_frame = tk.Frame(self.root, padx=10, pady=10)
        self.fields_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # 图片显示区域框架
        image_frame = tk.Frame(self.fields_frame)
        image_frame.pack(anchor=tk.W, pady=10)

        image_label = tk.Label(image_frame, text="图片:", font=("Arial", 12, "bold"))
        image_label.pack(side=tk.LEFT, padx=5, pady=5)

        poster_frame = tk.Frame(image_frame, width=165, height=225, bg="", highlightthickness=1, highlightbackground="black")
        poster_frame.pack(side=tk.LEFT, padx=5)
        poster_frame.pack_propagate(0)

        # 增加间距的空Frame
        empty_frame = tk.Frame(image_frame, width=40, bg="")
        empty_frame.pack(side=tk.LEFT)

        thumb_frame = tk.Frame(image_frame, width=333, height=225, bg="", highlightthickness=1, highlightbackground="black")
        thumb_frame.pack(side=tk.LEFT, padx=5)
        thumb_frame.pack_propagate(0)

        self.poster_label = tk.Label(poster_frame, text="封面图 (poster)", fg="black")
        self.poster_label.pack(expand=True)

        self.thumb_label = tk.Label(thumb_frame, text="缩略图 (thumb)", fg="black")
        self.thumb_label.pack(expand=True)

        # 创建字段标签和输入框
        self.create_field_labels()

        # 创建操作按钮
        self.create_operations_panel()

        # 默认打开图片显示
        #self.toggle_image_display()

        self.root = root
        self.selected_index_cache = None  # 添加此行来保存选中索引

        # 运行主循环
        self.root.mainloop()

    def toggle_image_display(self):
        if self.show_images_var.get():
            self.display_image() # 如果开关打开，则尝试显示图片
        else:
            # 确保在尝试清空图像前，图像实际存在
            if hasattr(self.poster_label, 'image') and self.poster_label.image:
                self.poster_label.config(image=None)
                self.poster_label.image = None  # 清除引用
            if hasattr(self.thumb_label, 'image') and self.thumb_label.image:
                self.thumb_label.config(image=None)
                self.thumb_label.image = None  # 清除引用

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
            label.image = img  # 保持引用防止图片被垃圾回收
        except Exception as e:
            label.config(text="加载图片失败: " + str(e))

    def create_field_labels(self):
        # 定义各字段的标签文本和高度
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

        # 创建标签和输入框，并存储到 fields_entries 中
        for field, (label_text, height) in fields.items():
            frame = tk.Frame(self.fields_frame)
            frame.pack(fill=tk.X)  # Ensure each input field fills the parent container horizontally

            label = tk.Label(frame, text=label_text + ":", font=("Arial", 12, "bold"))
            label.pack(side=tk.LEFT, padx=5, pady=5, anchor=tk.W)  # Align the label to the left

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
        # 创建操作面板，包括保存更改按钮、批量替换按钮和保存时间标签
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
        # 打开文件夹选择对话框，并加载选中文件夹中的 NFO 文件
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
                rel_dir = os.path.relpath(root, self.folder_path)
                if rel_dir == '.':
                    continue  # 跳过根目录
                parts = rel_dir.split(os.sep)
                for file in files:
                    if file.endswith('.nfo'):
                        self.nfo_files.append(os.path.join(root, file))
                        if len(parts) == 1:
                            self.file_treeview.insert("", "end", values=(parts[0], "", file))
                        elif len(parts) == 2:
                            self.file_treeview.insert("", "end", values=(parts[0], parts[1], file))
        except OSError as e:
            messagebox.showerror("Error", f"Error loading files from folder: {str(e)}")

        if self.nfo_files:  # 如果存在nfo文件，选择第一个
            first_item = self.file_treeview.get_children()[0]
            self.file_treeview.selection_set(first_item)
            self.file_treeview.see(first_item)
            self.on_file_select(None)

    def open_selected_nfo(self):
        selected_items = self.file_treeview.selection()
        for selected_item in selected_items:
            item = self.file_treeview.item(selected_item)
            values = item["values"]
            if values[2]:  # 如果有NFO文件
                if values[1]:
                    nfo_file_path = os.path.join(self.folder_path, values[0], values[1], values[2])
                else:
                    nfo_file_path = os.path.join(self.folder_path, values[0], values[2])
                if os.path.exists(nfo_file_path):
                    os.startfile(nfo_file_path)
                else:
                    messagebox.showerror("Error", f"NFO file does not exist: {nfo_file_path}")

    def open_selected_folder(self):
        selected_items = self.file_treeview.selection()
        for selected_item in selected_items:
            item = self.file_treeview.item(selected_item)
            values = item["values"]
            if values[2]:  # 如果有NFO文件
                if values[1]:
                    nfo_file_path = os.path.join(self.folder_path, values[0], values[1], values[2])
                else:
                    nfo_file_path = os.path.join(self.folder_path, values[0], values[2])
                if os.path.exists(nfo_file_path):
                    folder_path = os.path.dirname(nfo_file_path)
                    os.startfile(folder_path)
                else:
                    messagebox.showerror("Error", f"NFO file does not exist: {nfo_file_path}")

    def open_selected_video(self):
        video_extensions = ['.mp4', '.mkv', '.avi', '.mov', '.strm']  # 添加其他视频格式（如有需要）
        selected_items = self.file_treeview.selection()
        for selected_item in selected_items:
            item = self.file_treeview.item(selected_item)
            values = item["values"]
            if values[2]:  # 如果有NFO文件
                if values[1]:
                    nfo_file_path = os.path.join(self.folder_path, values[0], values[1], values[2])
                else:
                    nfo_file_path = os.path.join(self.folder_path, values[0], values[2])
                if os.path.exists(nfo_file_path):
                    video_file_base = os.path.splitext(nfo_file_path)[0]
                    for ext in video_extensions:
                        video_file = video_file_base + ext
                        if os.path.exists(video_file):
                            os.startfile(video_file)
                            return
                    messagebox.showerror("错误", "没有找到支持的格式的视频文件：.mp4, .mkv, .avi, .mov, .strm")
                else:
                    messagebox.showerror("错误", f"NFO文件不存在：{nfo_file_path}")

    def on_file_select(self, event):
        selected_items = self.file_treeview.selection()
        if selected_items:  # 确保选中的项非空
            for selected_item in selected_items:
                item = self.file_treeview.item(selected_item)
                values = item["values"]
                if values[2]:  # 如果有NFO文件
                    if values[1]:
                        self.current_file_path = os.path.join(self.folder_path, values[0], values[1], values[2])
                    else:
                        self.current_file_path = os.path.join(self.folder_path, values[0], values[2])
                    self.load_nfo_fields()
                    if self.show_images_var.get():
                        self.display_image()
            self.selected_index_cache = selected_items  # 只有在非空时才更新缓存

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
                if child.tag == 'actor':
                    unique_actors.add(child.find('name').text)
                if child.tag == 'tag':
                    tags.append(child.text)
                if child.tag == 'genre':
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
        if self.selected_index_cache:  # 确保缓存非空
            for selected_index in self.selected_index_cache:
                self.file_treeview.selection_set(selected_index)  # 使用保存的索引恢复选中状态
                self.file_treeview.see(selected_index)  # 确保选中的条目可见

    def save_changes(self):
        if not self.current_file_path:
            return

        try:
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            # 获取各字段的新值，并去除多余的换行符和空格
            title = self.fields_entries['title'].get(1.0, tk.END).strip()
            plot = self.fields_entries['plot'].get(1.0, tk.END).strip()
            actors_text = self.fields_entries['actors'].get(1.0, tk.END).strip()
            series = self.fields_entries['series'].get(1.0, tk.END).strip()
            tags_text = self.fields_entries['tags'].get(1.0, tk.END).strip()
            genres_text = self.fields_entries['genres'].get(1.0, tk.END).strip()
            rating = self.fields_entries['rating'].get(1.0, tk.END).strip()

            # 创建映射来更新字段
            updates = {
                'title': title,
                'plot': plot,
                'series': series,
                'rating': rating
            }

            # 更新 XML 树中的字段值
            for field, value in updates.items():
                element = root.find(field)
                if element is None:  # 如果字段不存在，创建新元素
                    element = ET.SubElement(root, field)
                element.text = value  # 更新或设置文本

            # 更新演员信息
            unique_actors = set(actors_text.split(','))
            for actor_elem in root.findall('actor'):
                root.remove(actor_elem)
            for actor_name in unique_actors:
                actor_elem = ET.Element('actor')
                name_elem = ET.SubElement(actor_elem, 'name')
                name_elem.text = actor_name.strip()
                root.append(actor_elem)

            # 更新标签信息
            for tag_elem in root.findall('tag'):
                root.remove(tag_elem)
            tags = tags_text.split(',')
            for tag in tags:
                tag_elem = ET.Element('tag')
                tag_elem.text = tag.strip()
                root.append(tag_elem)

            # 更新类别信息
            for genre_elem in root.findall('genre'):
                root.remove(genre_elem)
            genres = genres_text.split(',')
            for genre in genres:
                genre_elem = ET.Element('genre')
                genre_elem.text = genre.strip()
                root.append(genre_elem)

            # 保存修改后的 XML 文件
            xml_str = ET.tostring(root, encoding='utf-8')
            parsed_str = minidom.parseString(xml_str)
            pretty_str = parsed_str.toprettyxml(indent="  ", encoding='utf-8')

            # 去除多余的空行
            pretty_str = "\n".join([line for line in pretty_str.decode('utf-8').split('\n') if line.strip()])

            with open(self.current_file_path, 'w', encoding='utf-8') as file:
                file.write(pretty_str)

            self.update_save_time()

            # 操作完成后重新选中之前选中的文件
            if self.selected_index_cache:
                self.file_treeview.selection_set(self.selected_index_cache)
                for selected_index in self.selected_index_cache:
                    self.file_treeview.see(selected_index)  # 确保选中的条目可见
            
        except Exception as e:
            messagebox.showerror("Error", f"Error saving changes to NFO file: {str(e)}")

    def update_save_time(self):
        # 更新保存时间标签
        self.save_time_label.config(text=f"保存时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    def sort_files(self):
        sort_by = self.sorting_var.get()
        
        # 获取 Treeview 中的所有项
        items = [(self.file_treeview.set(k, "一级目录"), 
                  self.file_treeview.set(k, "二级目录"), 
                  self.file_treeview.set(k, "NFO文件"), 
                  k) for k in self.file_treeview.get_children("")]

        if sort_by == "filename":
            items.sort(key=lambda t: t[2])  # 按 NFO 文件名排序
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

        # 清空 Treeview 并重新插入排序后的项
        for i, (一级目录, 二级目录, NFO文件, k) in enumerate(items):
            self.file_treeview.move(k, '', i)

    def batch_filling(self):
        # 批量填充对话框逻辑
        def apply_fill():
            field = field_var.get()
            fill_value = fill_entry.get()
            operation_log = ""

            selected_files = self.file_treeview.selection()  # 获取选择的文件
            if not selected_files:
                messagebox.showwarning("警告", "请先选择要填充的文件")
                return

            if field and fill_value:
                for item in selected_files:
                    item_values = self.file_treeview.item(item, "values")
                    nfo_file = os.path.join(self.folder_path, item_values[0], item_values[1], item_values[2])  # 获取选择的文件路径
                    try:
                        tree = ET.parse(nfo_file)
                        root = tree.getroot()

                        # 如果字段不存在，则创建新的元素
                        field_elem = root.find(field)
                        if field_elem is None:
                            field_elem = ET.Element(field)
                            root.append(field_elem)

                        # 填充字段值
                        field_elem.text = fill_value.strip()

                        # 保存修改后的 XML 文件
                        xml_str = ET.tostring(root, encoding='utf-8')
                        parsed_str = minidom.parseString(xml_str)
                        pretty_str = parsed_str.toprettyxml(indent="  ", encoding='utf-8')

                        # 去除多余的空行
                        pretty_lines = pretty_str.decode('utf-8').splitlines()
                        formatted_lines = [line for line in pretty_lines if line.strip()]
                        formatted_str = "\n".join(formatted_lines)

                        with open(nfo_file, 'w', encoding='utf-8') as file:  # 保存到原文件
                            file.write(formatted_str)

                        operation_log += f"{nfo_file}: {field}字段填充成功\n"
                    except Exception as e:
                        operation_log += f"{nfo_file}: {field}字段填充失败 - {str(e)}\n"

            log_text.delete(1.0, tk.END)
            log_text.insert(1.0, operation_log)

        # 创建批量填充对话框
        dialog = Toplevel(self.root)
        dialog.title("批量填充 (Batch Fill)")
        # 设置对话框大小和位置
        dialog.geometry("400x600+325+100")  # 可以根据需要设置位置和大小

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
        # 批量新增对话框逻辑
        def apply_add():
            field = field_var.get()
            add_value = add_entry.get()
            operation_log = ""

            selected_files = self.file_treeview.selection()  # 获取选择的文件
            if not selected_files:
                messagebox.showwarning("警告", "请先选择要新增的文件")
                return

            if field and add_value:
                for item in selected_files:
                    item_values = self.file_treeview.item(item, "values")
                    nfo_file = os.path.join(self.folder_path, item_values[0], item_values[1], item_values[2])  # 获取选择的文件路径
                    try:
                        tree = ET.parse(nfo_file)
                        root = tree.getroot()

                        # 如果字段不存在，则创建新的元素
                        field_elem = root.find(field)
                        if field_elem is None:
                            field_elem = ET.Element(field)
                            root.append(field_elem)

                        # 新增字段值
                        field_elem.text = add_value.strip()

                        # 保存修改后的 XML 文件
                        xml_str = ET.tostring(root, encoding='utf-8')
                        parsed_str = minidom.parseString(xml_str)
                        pretty_str = parsed_str.toprettyxml(indent="  ", encoding='utf-8')

                        # 去除多余的空行
                        pretty_lines = pretty_str.decode('utf-8').splitlines()
                        formatted_lines = [line for line in pretty_lines if line.strip()]
                        formatted_str = "\n".join(formatted_lines)

                        with open(nfo_file, 'w', encoding='utf-8') as file:  # 保存到原文件
                            file.write(formatted_str)

                        operation_log += f"{nfo_file}: {field}字段新增成功\n"
                    except Exception as e:
                        operation_log += f"{nfo_file}: {field}字段新增失败 - {str(e)}\n"

            log_text.delete(1.0, tk.END)
            log_text.insert(1.0, operation_log)

       # 创建批量新增对话框
        dialog = Toplevel(self.root)
        dialog.title("批量新增 (Batch Add)")

        # 设置对话框大小和位置
        dialog.geometry("400x600+325+100")  # 可以根据需要设置位置和大小

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

if __name__ == "__main__":
    root = tk.Tk()
    app = NFOEditorApp(root)
