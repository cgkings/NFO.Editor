import tkinter as tk
from tkinter import filedialog, messagebox, Toplevel
import os
import xml.etree.ElementTree as ET
from datetime import datetime
import xml.dom.minidom as minidom

class NFOEditorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("大锤 NFO.Editor v 1.0 20240628")
        
        self.folder_path = ""  # 存储选中的文件夹路径
        self.current_file_path = ""  # 存储当前选中的 NFO 文件路径
        self.fields_entries = {}  # 存储各字段的输入框对象
        self.nfo_files = []  # 存储当前文件夹中的所有 NFO 文件路径列表

        # 创建顶部按钮和路径显示
        top_frame = tk.Frame(self.root)
        top_frame.pack(side=tk.TOP, fill=tk.X)
        
        select_directory_button = tk.Button(top_frame, text="选择目录 (Select Directory)", command=self.open_folder)
        select_directory_button.pack(side=tk.LEFT, padx=5)
        
        open_nfo_button = tk.Button(top_frame, text="🖊", command=self.open_selected_nfo)
        open_nfo_button.pack(side=tk.LEFT, padx=5)
        
        open_folder_button = tk.Button(top_frame, text="📁", command=self.open_selected_folder)
        open_folder_button.pack(side=tk.LEFT, padx=5)
        
        self.folder_path_label = tk.Label(top_frame, text="")
        self.folder_path_label.pack(side=tk.RIGHT, padx=5)

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

        # 创建文件列表框
        self.file_listbox = tk.Listbox(self.root, width=50, selectmode=tk.EXTENDED)
        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = tk.Scrollbar(self.root, orient=tk.VERTICAL)
        scrollbar.pack(side=tk.LEFT, fill=tk.Y)
        self.file_listbox.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.file_listbox.yview)
        self.file_listbox.bind('<<ListboxSelect>>', self.on_file_select)

        # 创建字段编辑框架
        self.fields_frame = tk.Frame(self.root, padx=10, pady=10)
        self.fields_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # 创建字段标签和输入框
        self.create_field_labels()

        # 创建操作按钮
        self.create_operations_panel()

        # 运行主循环
        self.root.mainloop()

    def create_field_labels(self):
        # 定义各字段的标签文本和高度
        fields = {
            'title': ('标题 (Title)', 2),
            'plot': ('简介 (Plot)', 6),
            'actors': ('演员 (Actors)', 2),
            'series': ('系列 (Series)', 2),
            'tags': ('标签 (Tags)', 3),
            'genres': ('类别 (Genre)', 3),
            'rating': ('评分 (Rating)', 1)
        }

        # 创建标签和输入框，并存储到 fields_entries 中
        for row, (field, (label_text, height)) in enumerate(fields.items()):
            label = tk.Label(self.fields_frame, text=label_text + ":", font=("Arial", 12, "bold"))
            label.grid(row=row, column=0, sticky=tk.W, pady=5)
            entry = tk.Text(self.fields_frame, width=60, height=height)
            entry.grid(row=row, column=1, sticky=tk.W, pady=5)
            self.fields_entries[field] = entry

    def create_operations_panel(self):
        # 创建操作面板，包括保存更改按钮、批量替换按钮和保存时间标签
        operations_frame = tk.Frame(self.fields_frame, padx=10, pady=10)
        operations_frame.grid(row=len(self.fields_entries), column=0, columnspan=2, pady=10)

        save_button = tk.Button(operations_frame, text="保存更改 (Save Changes)", command=self.save_changes)
        save_button.grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)

        batch_replace_button = tk.Button(operations_frame, text="批量替换 (Batch Replace)", command=self.batch_replace)
        batch_replace_button.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)

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
        # 加载选中文件夹中的 NFO 文件列表到文件列表框中
        self.file_listbox.delete(0, tk.END)
        self.nfo_files = []
        try:
            for root, dirs, files in os.walk(self.folder_path):
                for file in files:
                    if file.endswith('.nfo'):
                        nfo_file = os.path.join(root, file)
                        self.nfo_files.append(nfo_file)
                        self.file_listbox.insert(tk.END, os.path.relpath(nfo_file, self.folder_path))  # 插入相对路径以供显示
        except OSError as e:
            messagebox.showerror("Error", f"Error loading files from folder: {str(e)}")

    def open_selected_nfo(self):
        # 打开选中的 NFO 文件
        selected_indices = self.file_listbox.curselection()
        if selected_indices:
            selected_index = selected_indices[0]
            nfo_file_path = os.path.join(self.folder_path, self.file_listbox.get(selected_index))
            if os.path.exists(nfo_file_path):
                os.startfile(nfo_file_path)
            else:
                messagebox.showerror("Error", f"NFO file does not exist: {nfo_file_path}")

    def open_selected_folder(self):
        # 打开包含选中 NFO 文件的文件夹
        selected_indices = self.file_listbox.curselection()
        if selected_indices:
            selected_index = selected_indices[0]
            nfo_file_path = os.path.join(self.folder_path, self.file_listbox.get(selected_index))
            if os.path.exists(nfo_file_path):
                folder_path = os.path.dirname(nfo_file_path)
                os.startfile(folder_path)
            else:
                messagebox.showerror("Error", f"NFO file does not exist: {nfo_file_path}")

    def on_file_select(self, event):
        # 当文件列表框中选中文件发生变化时，更新当前选中的 NFO 文件路径并加载其字段内容
        selected_index = self.file_listbox.curselection()
        if selected_index:
            self.current_file_path = os.path.join(self.folder_path, self.file_listbox.get(selected_index[0]))
            self.load_nfo_fields()

    def load_nfo_fields(self):
        # 加载当前选中 NFO 文件的字段内容到对应的输入框中
        for entry in self.fields_entries.values():
            entry.delete(1.0, tk.END)

        try:
            tree = ET.parse(self.current_file_path)
            root = tree.getroot()

            fields_to_load = ['title', 'plot', 'series', 'rating']
            unique_actors = set()
            tags = []
            genres = []

            for child in root:
                if child.tag in fields_to_load:
                    entry = self.fields_entries.get(child.tag)
                    if entry:
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
            for child in root:
                if child.tag in updates:
                    child.text = updates[child.tag]

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
        except Exception as e:
            messagebox.showerror("Error", f"Error saving changes to NFO file: {str(e)}")

    def update_save_time(self):
        # 更新保存时间标签
        self.save_time_label.config(text=f"保存时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    def sort_files(self):
        # 根据选中的排序方式对文件列表进行排序
        sort_by = self.sorting_var.get()
        if sort_by == "filename":
            self.nfo_files.sort()
        else:
            # 加载并解析每个 NFO 文件以获取排序依据字段的值
            def get_sort_key(nfo_file):
                try:
                    tree = ET.parse(nfo_file)
                    root = tree.getroot()
                    if sort_by == "actors":
                        actors = set()
                        for actor_elem in root.findall('actor'):
                            name_elem = actor_elem.find('name')
                            if name_elem is not None:
                                actors.add(name_elem.text)
                        return ', '.join(actors)
                    else:
                        for child in root:
                            if child.tag == sort_by:
                                return child.text
                except ET.ParseError:
                    return ""
                return ""

            self.nfo_files.sort(key=get_sort_key)

        # 更新文件列表框
        self.file_listbox.delete(0, tk.END)
        for nfo_file in self.nfo_files:
            self.file_listbox.insert(tk.END, os.path.relpath(nfo_file, self.folder_path))

    def batch_replace(self):
        # 批量替换对话框逻辑
        def apply_replacement():
            field = field_var.get()
            original_text = original_entry.get()
            new_text = new_entry.get()
            operation_log = ""
            
            if field and original_text and new_text:
                for nfo_file in self.nfo_files:
                    try:
                        tree = ET.parse(nfo_file)
                        root = tree.getroot()
                        modified = False

                        if field in ['series', 'rating']:
                            for child in root:
                                if child.tag == field and child.text == original_text:
                                    child.text = new_text
                                    modified = True
                        elif field == 'actors':
                            for actor_elem in root.findall('actor'):
                                name_elem = actor_elem.find('name')
                                if name_elem is not None and name_elem.text == original_text:
                                    name_elem.text = new_text
                                    modified = True
                        if modified:
                            xml_str = ET.tostring(root, encoding='utf-8')
                            parsed_str = minidom.parseString(xml_str)
                            pretty_str = parsed_str.toprettyxml(indent="  ", encoding='utf-8')
                            with open(nfo_file, 'wb') as file:
                                file.write(pretty_str)
                            operation_log += f"{nfo_file}: 修改成功\n"
                        else:
                            operation_log += f"{nfo_file}: 未找到匹配内容\n"
                    except Exception as e:
                        operation_log += f"{nfo_file}: 修改失败 - {str(e)}\n"

            log_text.delete(1.0, tk.END)
            log_text.insert(1.0, operation_log)

        dialog = Toplevel(self.root)
        dialog.title("批量替换 (Batch Replace)")
        dialog.geometry("400x300")

        tk.Label(dialog, text="选择字段 (Field):").pack(pady=5)
        field_var = tk.StringVar(value="title")
        tk.Radiobutton(dialog, text="演员 (Actors)", variable=field_var, value="actors").pack(anchor=tk.W)
        tk.Radiobutton(dialog, text="系列 (Series)", variable=field_var, value="series").pack(anchor=tk.W)
        tk.Radiobutton(dialog, text="评分 (Rating)", variable=field_var, value="rating").pack(anchor=tk.W)

        tk.Label(dialog, text="原内容 (Original Content):").pack(pady=5)
        original_entry = tk.Entry(dialog, width=40)
        original_entry.pack(pady=5)

        tk.Label(dialog, text="新内容 (New Content):").pack(pady=5)
        new_entry = tk.Entry(dialog, width=40)
        new_entry.pack(pady=5)

        tk.Button(dialog, text="应用 (Apply)", command=apply_replacement).pack(pady=10)

        tk.Label(dialog, text="操作日志 (Operation Log):").pack(pady=5)
        log_text = tk.Text(dialog, width=50, height=10)
        log_text.pack(pady=5)

if __name__ == "__main__":
    root = tk.Tk()
    app = NFOEditorApp(root)
