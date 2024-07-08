import os
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import queue
from datetime import datetime

class BatchCopyTool:
    def __init__(self, root, src_dir=None):
        self.root = root
        self.root.title("批量strm工具")
        
        self.src_dir = src_dir if src_dir else ""
        self.dest_dir = ""
        self.file_hashes = {}
        self.file_hash_lock = threading.Lock()
        self.log_queue = queue.Queue()
        self.log_file_path = None

        # 创建UI
        self.create_ui()

    def create_ui(self):
        frame = tk.Frame(self.root)
        frame.pack(pady=20)

        tk.Label(frame, text="来源目录:").grid(row=0, column=0, padx=5, pady=5, sticky=tk.W)
        self.src_entry = tk.Entry(frame, width=50)
        self.src_entry.grid(row=0, column=1, padx=5, pady=5)
        tk.Button(frame, text="选择", command=self.select_src_dir).grid(row=0, column=2, padx=5, pady=5)

        tk.Label(frame, text="目标目录:").grid(row=1, column=0, padx=5, pady=5, sticky=tk.W)
        self.dest_entry = tk.Entry(frame, width=50)
        self.dest_entry.grid(row=1, column=1, padx=5, pady=5)
        tk.Button(frame, text="选择", command=self.select_dest_dir).grid(row=1, column=2, padx=5, pady=5)

        # 同步内容选择
        tk.Label(frame, text="同步内容:").grid(row=2, column=0, padx=0, sticky=tk.W)
        self.sync_images_var = tk.BooleanVar(value=True)
        self.sync_subtitles_var = tk.BooleanVar(value=True)
        self.sync_videos_var = tk.BooleanVar(value=True)
        tk.Checkbutton(frame, text="同步图片", variable=self.sync_images_var).grid(row=2, column=1, padx=0, sticky=tk.W)
        tk.Checkbutton(frame, text="同步字幕", variable=self.sync_subtitles_var).grid(row=2, column=1, padx=80, sticky=tk.W)
        tk.Checkbutton(frame, text="同步视频", variable=self.sync_videos_var).grid(row=2, column=1, padx=160, sticky=tk.W)

        # 同步线程
        tk.Label(frame, text="同步线程:").grid(row=3, column=0, padx=0, pady=0, sticky=tk.W)
        self.threads_entry = tk.Entry(frame, width=30)
        self.threads_entry.grid(row=3, column=1, padx=0, sticky=tk.W)
        self.threads_entry.insert(0, "16")  # 增加默认线程数

        # 同步依据
        self.sync_basis_var = tk.StringVar(value="size")
        tk.Label(frame, text="同步依据:").grid(row=4, column=0, padx=5, pady=5, sticky=tk.W)
        tk.Radiobutton(frame, text="hash", variable=self.sync_basis_var, value="hash").grid(row=4, column=1, padx=0, sticky=tk.W)
        tk.Radiobutton(frame, text="大小", variable=self.sync_basis_var, value="size").grid(row=4, column=1, padx=80, sticky=tk.W)

        # 同步类型
        self.sync_type_var = tk.StringVar(value="overwrite")
        tk.Label(frame, text="同步类型:").grid(row=5, column=0, padx=5, pady=5, sticky=tk.W)
        tk.Radiobutton(frame, text="覆盖替换", variable=self.sync_type_var, value="overwrite").grid(row=5, column=1, padx=0, sticky=tk.W)
        tk.Radiobutton(frame, text="重命名新增", variable=self.sync_type_var, value="rename").grid(row=5, column=1, padx=80, sticky=tk.W)

        tk.Button(frame, text="开始同步", command=self.start_copy).grid(row=6, column=0, columnspan=3, pady=10)

        # 日志显示框
        self.log_text = tk.Text(frame, width=70, height=20)
        self.log_text.grid(row=7, column=0, columnspan=3, padx=5, pady=5)
        self.log_text.config(state=tk.DISABLED)

        # 如果传递了来源目录，直接更新UI
        if self.src_dir:
            self.src_entry.insert(0, self.src_dir)

        # 启动日志更新线程
        self.root.after(100, self.process_log_queue)

    def select_src_dir(self):
        self.src_dir = filedialog.askdirectory(title="选择源目录", parent=self.root)
        self.src_entry.delete(0, tk.END)
        self.src_entry.insert(0, self.src_dir)

    def select_dest_dir(self):
        self.dest_dir = filedialog.askdirectory(title="选择目标目录", parent=self.root)
        self.dest_entry.delete(0, tk.END)
        self.dest_entry.insert(0, self.dest_dir)

    def start_copy(self):
        if not self.src_dir or not self.dest_dir:
            messagebox.showwarning("警告", "请先选择源目录和目标目录")
            return

        # 设置日志文件路径
        log_dir = 'log'
        os.makedirs(log_dir, exist_ok=True)
        current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file_path = os.path.join(log_dir, f"{current_time}.log")

        threading.Thread(target=self.copy_task).start()

    def copy_task(self):
        self.load_file_hashes()
        self.copy_structure(self.src_dir, self.dest_dir)
        self.remove_extra_files(self.src_dir, self.dest_dir)
        self.save_file_hashes()
        self.log_message("复制完成")
        # 删除file_hashes.txt文件
        if os.path.exists('file_hashes.txt'):
            os.remove('file_hashes.txt')
        messagebox.showinfo("完成", "复制完成")

    def load_file_hashes(self):
        try:
            with open('file_hashes.txt', 'r') as f:
                for line in f:
                    path, file_hash = line.strip().rsplit(' ', 1)
                    self.file_hashes[path] = file_hash
        except FileNotFoundError:
            self.log_message("未找到file_hashes.txt文件，将创建新文件。")

    def save_file_hashes(self):
        with self.file_hash_lock:
            with open('file_hashes.txt', 'w') as f:
                for path, file_hash in self.file_hashes.items():
                    f.write(f"{path} {file_hash}\n")

    def calculate_file_hash(self, file_path):
        hash_md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def copy_structure(self, src, dest):
        max_workers = int(self.threads_entry.get())
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for root, dirs, files in os.walk(src):
                # 创建目录结构
                for dir in dirs:
                    dest_dir_path = os.path.join(dest, os.path.relpath(os.path.join(root, dir), src))
                    os.makedirs(dest_dir_path, exist_ok=True)

                # 复制图片、字幕文件，生成.strm文件
                for file in files:
                    file_lower = file.lower()
                    src_file_path = os.path.join(root, file)
                    dest_file_path = os.path.join(dest, os.path.relpath(src_file_path, src))

                    if self.sync_images_var.get() and file_lower.endswith(('.jpg', '.jpeg', '.png')):
                        self.log_message(f"准备复制图片文件: {src_file_path}")
                        futures.append(executor.submit(self.copy_file, src_file_path, dest_file_path))
                    elif self.sync_subtitles_var.get() and file_lower.endswith(('.srt', '.ass')):
                        self.log_message(f"准备复制字幕文件: {src_file_path}")
                        futures.append(executor.submit(self.copy_file, src_file_path, dest_file_path))
                    elif self.sync_videos_var.get() and file_lower.endswith(('.mp4', '.mkv', '.avi')):
                        self.log_message(f"准备创建.strm文件: {src_file_path}")
                        strm_file_path = os.path.join(dest, os.path.relpath(os.path.splitext(src_file_path)[0] + ".strm", src))
                        futures.append(executor.submit(self.create_strm_file, src_file_path, strm_file_path))
                    elif file_lower.endswith('.nfo'):
                        self.log_message(f"准备复制NFO文件: {src_file_path}")
                        futures.append(executor.submit(self.copy_file, src_file_path, dest_file_path))

            for future in as_completed(futures):
                future.result()

    def remove_extra_files(self, src, dest):
        for root, dirs, files in os.walk(dest, topdown=False):
            # 删除多余文件
            for file in files:
                dest_file_path = os.path.join(root, file)
                src_file_path = os.path.join(src, os.path.relpath(dest_file_path, dest))
                if not os.path.exists(src_file_path) and not file.endswith('.strm'):
                    os.remove(dest_file_path)
                    self.log_message(f"删除多余文件: {dest_file_path}")
            # 删除多余目录
            for dir in dirs:
                dest_dir_path = os.path.join(root, dir)
                src_dir_path = os.path.join(src, os.path.relpath(dest_dir_path, dest))
                if not os.path.exists(src_dir_path):
                    shutil.rmtree(dest_dir_path)
                    self.log_message(f"删除多余目录: {dest_dir_path}")

    def copy_file(self, src_file_path, dest_file_path):
        try:
            sync_basis = self.sync_basis_var.get()
            sync_type = self.sync_type_var.get()
            should_copy = False
            src_file_hash = None

            if sync_basis == "hash":
                src_file_hash = self.calculate_file_hash(src_file_path)
                with self.file_hash_lock:
                    dest_file_hash = self.file_hashes.get(dest_file_path)
                    self.log_message(f"源文件哈希: {src_file_hash}, 目标文件哈希: {dest_file_hash}")
                    if dest_file_hash != src_file_hash:
                        should_copy = True
            else:  # 基于大小
                src_file_size = os.path.getsize(src_file_path)
                if os.path.exists(dest_file_path):
                    dest_file_size = os.path.getsize(dest_file_path)
                    self.log_message(f"源文件大小: {src_file_size}, 目标文件大小: {dest_file_size}")
                    if src_file_size != dest_file_size:
                        should_copy = True
                else:
                    should_copy = True

            if should_copy:
                if sync_type == "overwrite":
                    shutil.copy2(src_file_path, dest_file_path)
                    self.log_message(f"复制文件: {src_file_path} 到 {dest_file_path}")
                else:  # 重命名新增
                    base, ext = os.path.splitext(dest_file_path)
                    counter = 1
                    new_dest_file_path = dest_file_path
                    while os.path.exists(new_dest_file_path):
                        new_dest_file_path = f"{base}_{counter}{ext}"
                        counter += 1
                    shutil.copy2(src_file_path, new_dest_file_path)
                    self.log_message(f"复制文件: {src_file_path} 到 {new_dest_file_path}")

                if sync_basis == "hash":
                    with self.file_hash_lock:
                        self.file_hashes[dest_file_path] = src_file_hash
            else:
                self.log_message(f"跳过未变更文件: {src_file_path}")

        except Exception as e:
            self.log_message(f"复制文件出错: {src_file_path}, 错误: {e}")

    def create_strm_file(self, src_file_path, strm_file_path):
        try:
            # 生成.strm文件
            with open(strm_file_path, 'w') as strm_file:
                strm_file.write(src_file_path)
            self.log_message(f"创建.strm文件: {strm_file_path} 内容: {src_file_path}")
        except Exception as e:
            self.log_message(f"创建.strm文件出错: {src_file_path}, 错误: {e}")

    def log_message(self, message):
        self.log_queue.put(message)
        if self.log_file_path:
            with open(self.log_file_path, 'a', encoding='utf-8') as log_file:
                log_file.write(message + '\n')

    def process_log_queue(self):
        try:
            while True:
                message = self.log_queue.get_nowait()
                self.log_text.config(state=tk.NORMAL)
                self.log_text.insert(tk.END, message + "\n")
                self.log_text.config(state=tk.DISABLED)
                self.log_text.yview(tk.END)
        except queue.Empty:
            pass
        self.root.after(100, self.process_log_queue)

if __name__ == "__main__":
    root = tk.Tk()
    app = BatchCopyTool(root)
    root.mainloop()
