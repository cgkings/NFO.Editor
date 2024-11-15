import os
import re
import sys
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading

class StdoutRedirector:
    def __init__(self, text_widget):
        self.text_widget = text_widget

    def write(self, message):
        self.text_widget.insert(tk.END, message)
        self.text_widget.see(tk.END)
        self.text_widget.update()

    def flush(self):
        pass

class RenameToolGUI:
    def __init__(self, parent=None):
        self.window = tk.Toplevel(parent) if parent else tk.Tk()
        self.window.title("批量改名工具 v0.0.3")
        self.window.geometry("600x500")
        
        # 修改图标设置逻辑
        try:
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chuizi.ico")
            if os.path.exists(icon_path):
                if sys.platform == 'win32':  # Windows系统
                    try:
                        self.window.iconbitmap(default=icon_path)
                    except tk.TclError:
                        print("Windows图标加载失败，使用默认图标")
                else:  # Linux/Mac系统
                    try:
                        img = tk.PhotoImage(file=icon_path)
                        self.window.tk.call('wm', 'iconphoto', self.window._w, img)
                    except tk.TclError:
                        print("Linux/Mac图标加载失败，使用默认图标")
        except Exception as e:
            print(f"图标设置失败（使用默认图标）: {str(e)}")

        if parent:
            self.window.transient(parent)
            window_width = 600
            window_height = 500
            screen_width = self.window.winfo_screenwidth()
            screen_height = self.window.winfo_screenheight()
            x = (screen_width - window_width) // 2
            y = (screen_height - window_height) // 2
            self.window.geometry(f"{window_width}x{window_height}+{x}+{y}")
            
        self.control_frame = tk.Frame(self.window)
        self.control_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.path_var = tk.StringVar(value=os.path.dirname(os.path.abspath(__file__)))
        path_label = tk.Label(self.control_frame, text="工作目录：")
        path_label.pack(side=tk.LEFT, padx=(0,5))
        self.path_entry = tk.Entry(self.control_frame, textvariable=self.path_var, width=50)
        self.path_entry.pack(side=tk.LEFT, padx=5)
        
        browse_btn = tk.Button(self.control_frame, text="浏览", command=self.browse_folder)
        browse_btn.pack(side=tk.LEFT, padx=5)
        
        execute_btn = tk.Button(self.control_frame, text="执行", command=self.execute_rename)
        execute_btn.pack(side=tk.LEFT, padx=5)

        # 创建一个框架来容纳标签，以便更好地控制对齐
        mapping_frame = tk.Frame(self.window)
        mapping_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.mapping_file_path = self.get_mapping_file_path()
        mapping_label = tk.Label(mapping_frame, text=self.mapping_file_path, fg="blue", anchor="w", justify=tk.LEFT)
        mapping_label.pack(fill=tk.X)

        self.log_frame = tk.Frame(self.window)
        self.log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.log_text = tk.Text(self.log_frame, height=20, width=70)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        scrollbar = tk.Scrollbar(self.log_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
        scrollbar.config(command=self.log_text.yview)
        
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(self.window, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, padx=10, pady=5)
        
        self.status_label = tk.Label(self.window, text="就绪")
        self.status_label.pack(pady=5)

        self.redirect_stdout()

        if parent:
            def on_closing():
                sys.stdout = sys.__stdout__
                self.window.destroy()
            
            self.window.protocol("WM_DELETE_WINDOW", on_closing)

    def browse_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.path_var.set(folder_selected)

    def redirect_stdout(self):
        sys.stdout = StdoutRedirector(self.log_text)

    def update_progress(self, current, total):
        progress = (current / total) * 100
        self.progress_var.set(progress)
        self.status_label.config(text=f"进度: {current}/{total}")
        self.window.update()

    def get_mapping_file_path(self):
        if getattr(sys, 'frozen', False):
            script_dir = os.path.dirname(sys.executable)
        else:
            script_dir = os.path.dirname(os.path.abspath(__file__))

        external_mapping_file = os.path.join(script_dir, 'mapping_actor.xml')
        
        # 检查外部配置文件是否存在
        if os.path.exists(external_mapping_file):
            return f"外部配置: {external_mapping_file}"
        else:
            return f"内置配置: {external_mapping_file}"  # 显示内置配置路径

    def execute_rename(self):
        if hasattr(self, 'process_thread') and self.process_thread.is_alive():
            return
            
        self.log_text.delete(1.0, tk.END)
        self.progress_var.set(0)
        self.status_label.config(text="开始处理...")
        
        directory = self.path_var.get()
        if not os.path.isdir(directory):
            messagebox.showerror("错误", f"路径 '{directory}' 不是一个有效的目录。")
            return

        self.process_thread = threading.Thread(
            target=self._process_rename_thread,
            daemon=True
        )
        self.process_thread.start()

    def _process_rename_thread(self):
        try:
            # 获取 mapping_file_path 中的实际路径
            mapping_file = self.mapping_file_path.split(': ')[-1].strip()
            directory = self.path_var.get()

            if not os.path.exists(mapping_file):
                messagebox.showerror("错误", "未找到 mapping_actor.xml 文件，请确保它位于程序所在的文件夹中。")
                return

            actor_mapping = load_actor_mapping(mapping_file)
            rename_directory(directory, actor_mapping, self)
            self.status_label.config(text="处理完成！")
                
        except Exception as e:
            error_msg = f"\n处理过程中出现错误: {str(e)}"
            print(error_msg)
            messagebox.showerror("错误", error_msg)
            self.status_label.config(text="处理出错")

    def mainloop(self):
        self.window.mainloop()

def load_actor_mapping(mapping_file):
    """解析 mapping_actor.xml 文件，构建关键词到 zh_cn 的映射字典。"""
    mapping = {}
    try:
        with open(mapping_file, 'r', encoding='utf-8') as f:
            tree = ET.parse(f)
            root = tree.getroot()
            
            for actor in root.findall('a'):
                zh_cn = actor.get('zh_cn')
                if not zh_cn:
                    continue
                    
                keywords = actor.get('keyword', '').strip(',').split(',')
                keywords = [k.strip() for k in keywords if k.strip()]
                
                for keyword in keywords:
                    mapping[keyword] = zh_cn
                    
            print(f"成功加载 {len(mapping)} 个演员映射关系。")
            
    except ET.ParseError as e:
        print(f"解析 mapping_actor.xml 时出错: {str(e)}")
        raise
    except Exception as e:
        print(f"加载映射文件出错: {str(e)}")
        raise
        
    return mapping

def modify_nfo_actor(nfo_path, actor_mapping):
    """修改 nfo 文件中的 actor 字段"""
    modified = False
    actors = []
    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()
        actor_elements = root.findall('.//actor')

        if not actor_elements:
            print(f"在文件 '{nfo_path}' 中未找到任何 <actor> 元素。")
            return {'modified': modified, 'actors': actors}

        for actor in actor_elements:
            name_element = actor.find('name')
            if name_element is not None and name_element.text:
                current_actor = name_element.text.strip()
                if current_actor in actor_mapping:
                    new_actor = actor_mapping[current_actor]
                    if new_actor != current_actor:
                        print(f"将演员 '{current_actor}' 替换为 '{new_actor}'")
                        name_element.text = new_actor
                        modified = True
                        actors.append(new_actor)
                    else:
                        actors.append(current_actor)
                else:
                    actors.append(current_actor)
            else:
                print(f"在文件 '{nfo_path}' 中发现一个 <actor> 元素，但缺少 <name> 标签或内容为空。")
                actors.append("未知演员")

        if modified:
            tree.write(nfo_path, encoding='utf-8', xml_declaration=True)
            print(f"已保存修改后的 .nfo 文件: {nfo_path}")

    except Exception as e:
        print(f"修改 nfo 文件 {nfo_path} 时出错: {e}")

    return {'modified': modified, 'actors': actors}

def extract_actors(nfo_path):
    """从 nfo 文件中提取所有 actor 字段的值"""
    actors = []
    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()
        actor_elements = root.findall('.//actor')
        for actor in actor_elements:
            name_element = actor.find('name')
            if name_element is not None and name_element.text:
                actors.append(name_element.text.strip())
            else:
                actors.append("未知演员")
    except Exception as e:
        print(f"提取 actor 字段时出错: {e}")
    return actors if actors else ["未知演员"]

def extract_rating(nfo_path):
    """从 nfo 文件中提取 rating 值"""
    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()
        rating_element = root.find('.//rating')
        if rating_element is not None and rating_element.text:
            return float(rating_element.text.strip())
    except Exception as e:
        print(f"提取 rating 时出错: {e}")
    return 0.0

def format_rating(rating):
    """格式化 rating 值为一位小数"""
    return '{:.1f}'.format(rating)

def handle_folder_with_rating(actors, nfo_filename, rating):
    """根据命名规则生成文件夹名称"""
    sanitized_actors = [re.sub(r'[\\/:*?"<>|]', '_', actor) for actor in actors]
    actors_str = ','.join(sanitized_actors)
    sanitized_nfo = re.sub(r'[\\/:*?"<>|]', '_', nfo_filename)
    return f"{actors_str} {sanitized_nfo} {format_rating(rating)}"

def find_nfo_file(folder_path):
    """查找文件夹中的 .nfo 文件"""
    nfo_files = []
    for root, _, files in os.walk(folder_path):
        for file in files:
            if file.lower().endswith('.nfo'):
                nfo_files.append(os.path.join(root, file))
    if len(nfo_files) > 1:
        print(f"警告: 文件夹 '{folder_path}' 中找到多个 .nfo 文件，选择第一个找到的文件: {nfo_files[0]}")
    return nfo_files[0] if nfo_files else None

def replace_folder_name(folder_path, new_name):
    """重命名文件夹"""
    parent_path, _ = os.path.split(folder_path)
    new_path = os.path.join(parent_path, new_name)
    try:
        os.rename(folder_path, new_path)
        return True
    except OSError as e:
        print(f"重命名文件夹时出错: {folder_path}")
        print(f"错误信息: {str(e)}")
        return False

def rename_directory(directory, actor_mapping, gui=None):
    """遍历指定目录下的所有子文件夹，修改 .nfo 文件并重命名文件夹"""
    subfolder_count = sum([len(dirs) for _, dirs, _ in os.walk(directory)])
    progress_count = 0

    for root, dirs, _ in os.walk(directory):
        for folder in dirs:
            folder_path = os.path.join(root, folder)
            nfo_path = find_nfo_file(folder_path)

            if nfo_path is None or os.path.dirname(nfo_path) != folder_path:
                print(f"跳过文件夹 '{folder_path}'，未找到对应的 .nfo 文件或 .nfo 文件不在当前文件夹。")
                continue

            print(f"\n开始处理文件夹: {folder_path}")

            actors_modified = modify_nfo_actor(nfo_path, actor_mapping)
            nfo_filename = os.path.splitext(os.path.basename(nfo_path))[0]
            nfo_rating = extract_rating(nfo_path)

            progress_count += 1
            if gui:
                gui.update_progress(progress_count, subfolder_count)

            print(f"处理进度：{progress_count}/{subfolder_count}")
            print(f"nfo评分: {nfo_rating}")
            if actors_modified['modified']:
                print(f"actor 字段已修改为: {', '.join(actors_modified['actors'])}")
            else:
                print("actor 字段未做修改")

            current_actors = actors_modified['actors'] if actors_modified['modified'] else extract_actors(nfo_path)
            if not current_actors:
                current_actors = ['未知演员']
                print("未找到任何演员信息，使用默认值 '未知演员'。")

            expected_folder_name = handle_folder_with_rating(current_actors, nfo_filename, nfo_rating)
            print(f"预期文件夹名称: {expected_folder_name}")

            if folder != expected_folder_name:
                print(f"当前文件夹名称 '{folder}' 不符合命名规则，准备重命名为 '{expected_folder_name}'")
                success = replace_folder_name(folder_path, expected_folder_name)
                if success:
                    print(f"成功重命名文件夹为 '{expected_folder_name}'")
                else:
                    print(f"重命名文件夹 '{folder_path}' 失败。")
            else:
                print("文件夹名称符合规范，跳过修改。")

    print("\n处理完成。")

def start_rename_process(directory=None, parent_window=None):
    """启动重命名进程的入口函数"""
    window = RenameToolGUI(parent_window)
    if directory:
        window.path_var.set(directory)
    
    if parent_window:
        window.window.focus_force()
    else:
        window.mainloop()
        
    return window

if __name__ == '__main__':
    try:
        if len(sys.argv) > 1:
            directory_path = sys.argv[1]
            if not os.path.isdir(directory_path):
                messagebox.showerror("错误", f"路径 '{directory_path}' 不是一个有效的目录。")
                sys.exit(1)
            start_rename_process(directory_path)
        else:
            start_rename_process()
    except Exception as e:
        messagebox.showerror("错误", f"程序运行出错：{str(e)}")
        input("按任意键退出...")