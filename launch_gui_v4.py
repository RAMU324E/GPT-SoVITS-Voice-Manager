# --- START OF FILE launch_gui_v2_proxy_editable.py ---
# 注意：文件名再次修改，表示既是代理模式，又可编辑配置

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import os
import subprocess
import threading
import queue
import json
import sys
import signal
import yaml # 导入 yaml 库

script_dir = os.path.dirname(os.path.abspath(__file__))
# 使用新的配置文件名
CONFIG_FILE = os.path.join(script_dir, "gui_config_v2_proxy_editable.json")

class ApiV2ProxyEditableLauncherApp: # 修改类名
    def __init__(self, root):
        self.root = root
        self.root.title("GPT-SoVITS 代理 API 启动器 (可编辑配置)") # 修改标题
        self.root.geometry("800x900") # 进一步增加高度

        self.process = None
        self.output_queue = queue.Queue()
        self.current_yaml_data = None
        self.current_config_path = None

        # --- GUI 配置加载 ---
        self.config = self.load_config()

        # --- 样式 ---
        style = ttk.Style()
        # ... 样式 ...

        # --- 主框架 ---
        main_frame = ttk.Frame(root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- 1. 选择并加载 YAML 配置文件 ---
        config_select_frame = ttk.LabelFrame(main_frame, text="1. 选择并加载 YAML 配置文件", padding="10")
        config_select_frame.pack(fill=tk.X, pady=5)
        # ... (YAML 文件选择的控件和逻辑不变) ...
        ttk.Label(config_select_frame, text="配置文件 (.yaml) 文件夹:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.config_dir_var = tk.StringVar(value=self.config.get("config_dir", os.path.join(script_dir, "GPT_SoVITS", "configs")))
        self.config_dir_entry = ttk.Entry(config_select_frame, textvariable=self.config_dir_var, width=60)
        self.config_dir_entry.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=2)
        ttk.Button(config_select_frame, text="浏览...", command=lambda: self.browse_directory(self.config_dir_var, self.update_config_files)).grid(row=0, column=2, padx=5, pady=2)

        ttk.Label(config_select_frame, text="选择 YAML 配置文件:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.config_file_var = tk.StringVar(value=self.config.get("config_file", "tts_infer.yaml"))
        self.config_file_combo = ttk.Combobox(config_select_frame, textvariable=self.config_file_var, width=58, state="readonly")
        self.config_file_combo.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=2)
        self.config_file_combo.bind("<<ComboboxSelected>>", self.load_and_display_config) # <--- 绑定加载/显示函数

        config_select_frame.columnconfigure(1, weight=1)

        # --- 2. 编辑 Custom 配置 (加载文件后启用) ---
        self.edit_frame = ttk.LabelFrame(main_frame, text="2. 编辑 Custom 配置 (加载文件后可用)", padding="10")
        self.edit_frame.pack(fill=tk.X, pady=5)
        # ... (从 editable 版本复制编辑控件的创建和变量定义) ...
        self.custom_gpt_path_var = tk.StringVar()
        self.custom_sovits_path_var = tk.StringVar()
        self.custom_device_var = tk.StringVar(value="cuda")
        self.custom_precision_var = tk.StringVar(value="半精度")
        self.custom_version_var = tk.StringVar(value="v2")
        self.custom_bert_path_var = tk.StringVar()
        self.custom_hubert_path_var = tk.StringVar()

        row_idx = 0
        ttk.Label(self.edit_frame, text="GPT 模型路径 (.ckpt):").grid(row=row_idx, column=0, sticky=tk.W, padx=5, pady=2)
        self.custom_gpt_entry = ttk.Entry(self.edit_frame, textvariable=self.custom_gpt_path_var, width=50)
        self.custom_gpt_entry.grid(row=row_idx, column=1, sticky=tk.EW, padx=5, pady=2)
        ttk.Button(self.edit_frame, text="浏览...", command=lambda: self.browse_model_file(self.custom_gpt_path_var, [("CKPT 文件", "*.ckpt")])).grid(row=row_idx, column=2, padx=5, pady=2)
        row_idx += 1
        # ... (其他编辑控件: SoVITS路径, Version, Device, Precision) ...
        ttk.Label(self.edit_frame, text="SoVITS 模型路径 (.pth):").grid(row=row_idx, column=0, sticky=tk.W, padx=5, pady=2)
        self.custom_sovits_entry = ttk.Entry(self.edit_frame, textvariable=self.custom_sovits_path_var, width=50)
        self.custom_sovits_entry.grid(row=row_idx, column=1, sticky=tk.EW, padx=5, pady=2)
        ttk.Button(self.edit_frame, text="浏览...", command=lambda: self.browse_model_file(self.custom_sovits_path_var, [("PTH 文件", "*.pth")])).grid(row=row_idx, column=2, padx=5, pady=2)
        row_idx += 1

        ttk.Label(self.edit_frame, text="版本 (Version):").grid(row=row_idx, column=0, sticky=tk.W, padx=5, pady=2)
        self.custom_version_combo = ttk.Combobox(self.edit_frame, textvariable=self.custom_version_var, values=["v1", "v2", "v3", "v4"], width=8, state="readonly")
        self.custom_version_combo.grid(row=row_idx, column=1, sticky=tk.W, padx=5, pady=2)
        row_idx += 1

        ttk.Label(self.edit_frame, text="推理设备 (Device):").grid(row=row_idx, column=0, sticky=tk.W, padx=5, pady=2)
        self.custom_device_combo = ttk.Combobox(self.edit_frame, textvariable=self.custom_device_var, values=["cuda", "cpu"], width=8, state="readonly")
        self.custom_device_combo.grid(row=row_idx, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(self.edit_frame, text="推理精度 (Precision):").grid(row=row_idx, column=2, sticky=tk.W, padx=5, pady=2)
        self.custom_precision_combo = ttk.Combobox(self.edit_frame, textvariable=self.custom_precision_var, values=["半精度", "全精度"], width=8, state="readonly")
        self.custom_precision_combo.grid(row=row_idx, column=3, sticky=tk.W, padx=5, pady=2)
        row_idx += 1

        # 保存按钮
        self.save_button = ttk.Button(self.edit_frame, text="保存修改到 Custom 配置", command=self.save_custom_config)
        self.save_button.grid(row=row_idx, column=0, columnspan=4, pady=10)
        self.edit_frame.columnconfigure(1, weight=1)
        self.set_edit_frame_state(tk.DISABLED) # 初始禁用


        # --- 3. 设置默认参考音频 (传递给代理) ---
        ref_frame = ttk.LabelFrame(main_frame, text="3. 设置默认参考音频 (将传递给代理 API)", padding="10")
        ref_frame.pack(fill=tk.X, pady=5)
        # ... (参考音频的控件不变) ...
        ttk.Label(ref_frame, text="参考音频文件:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.ref_audio_var = tk.StringVar(value=self.config.get("ref_audio", ""))
        self.ref_audio_entry = ttk.Entry(ref_frame, textvariable=self.ref_audio_var, width=60)
        self.ref_audio_entry.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=2)
        ttk.Button(ref_frame, text="浏览...", command=self.browse_ref_audio).grid(row=0, column=2, padx=5, pady=2)
        # ... (参考文本、参考语言控件) ...
        ttk.Label(ref_frame, text="参考文本:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.ref_text_var = tk.StringVar(value=self.config.get("ref_text", ""))
        self.ref_text_entry = ttk.Entry(ref_frame, textvariable=self.ref_text_var, width=60)
        self.ref_text_entry.grid(row=1, column=1, sticky=tk.EW, padx=5, pady=2)

        ttk.Label(ref_frame, text="参考语言:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
        self.ref_lang_var = tk.StringVar(value=self.config.get("ref_lang", "zh"))
        self.ref_lang_combo = ttk.Combobox(ref_frame, textvariable=self.ref_lang_var, width=10,
                                        values=["zh", "ja", "en", "ko", "yue", "auto", "auto_yue", "all_zh", "all_ja", "all_en", "all_ko", "all_yue"])
        self.ref_lang_combo.grid(row=2, column=1, sticky=tk.W, padx=5, pady=2)

        ref_frame.columnconfigure(1, weight=1)


        # --- 4. 代理 API 服务配置与启动 ---
        api_frame = ttk.LabelFrame(main_frame, text="4. 代理 API 服务配置与启动", padding="10")
        api_frame.pack(fill=tk.X, pady=5)
        # ... (Host, Port, Python Exec 控件不变) ...
        ttk.Label(api_frame, text="监听地址 (Host):").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.host_var = tk.StringVar(value=self.config.get("host", "127.0.0.1"))
        self.host_entry = ttk.Entry(api_frame, textvariable=self.host_var, width=15)
        self.host_entry.grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)
        # ... (Port, Python Exec) ...
        ttk.Label(api_frame, text="端口 (Port):").grid(row=0, column=2, sticky=tk.W, padx=5, pady=2)
        self.port_var = tk.StringVar(value=self.config.get("port", "9880"))
        self.port_entry = ttk.Entry(api_frame, textvariable=self.port_var, width=8)
        self.port_entry.grid(row=0, column=3, sticky=tk.W, padx=5, pady=2)

        ttk.Label(api_frame, text="Python 解释器 (留空则使用默认):").grid(row=1, column=0, columnspan=1, sticky=tk.W, padx=5, pady=2)
        self.python_exec_var = tk.StringVar(value=self.config.get("python_exec", sys.executable or "python"))
        self.python_exec_entry = ttk.Entry(api_frame, textvariable=self.python_exec_var, width=40)
        self.python_exec_entry.grid(row=1, column=1, columnspan=3, sticky=tk.EW, padx=5, pady=2)

        api_frame.columnconfigure(1, weight=1)

        # 启动/停止按钮
        control_frame = ttk.Frame(api_frame) # 将按钮放在 API 配置框内
        control_frame.grid(row=2, column=0, columnspan=4, pady=10)
        self.launch_button = ttk.Button(control_frame, text="启动代理 API 服务", command=self.launch_api)
        self.launch_button.pack(side=tk.LEFT, padx=10)
        self.stop_button = ttk.Button(control_frame, text="停止代理 API 服务", command=self.stop_api, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=10)


        # --- 5. 输出日志 ---
        output_frame = ttk.LabelFrame(main_frame, text="5. 代理 API 输出日志", padding="10")
        output_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        # ... (ScrolledText 不变) ...
        try:
            font_spec = ("Microsoft YaHei", 9)
            self.output_text = scrolledtext.ScrolledText(output_frame, wrap=tk.WORD, height=10, state=tk.DISABLED, font=font_spec)
        except tk.TclError:
            self.output_text = scrolledtext.ScrolledText(output_frame, wrap=tk.WORD, height=10, state=tk.DISABLED)
        except Exception as e:
            self.output_text = scrolledtext.ScrolledText(output_frame, wrap=tk.WORD, height=10, state=tk.DISABLED)
        self.output_text.pack(fill=tk.BOTH, expand=True)

        # --- 初始加载和关闭处理 ---
        self.update_config_files() # 更新文件列表
        self.load_and_display_config() # 尝试加载默认配置文件并填充编辑区
        self.root.after(100, self.process_queue)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # --- 配置读写 (GUI 配置) ---
    def save_config(self):
        """保存当前GUI设置到 JSON 文件"""
        current_config = {
            "config_dir": self.config_dir_var.get(),
            "config_file": self.config_file_var.get(),
            "ref_audio": self.ref_audio_var.get(), # 保存参考音频信息
            "ref_text": self.ref_text_var.get(),
            "ref_lang": self.ref_lang_var.get(),
            "host": self.host_var.get(),
            "port": self.port_var.get(),
            "python_exec": self.python_exec_var.get(),
        }
        # ... (保存 JSON 的 try-except 不变) ...
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(current_config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"保存GUI配置失败: {e}")


    def load_config(self):
        """加载GUI配置文件"""
        # ... (加载 JSON 的 try-except 不变) ...
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"加载GUI配置失败: {e}")
        return {}


    # --- 文件/目录浏览 ---
    def browse_directory(self, var, update_func):
        # ... (不变) ...
        directory = filedialog.askdirectory(initialdir=var.get() or os.getcwd())
        if directory:
            var.set(directory)
            update_func()

    def browse_ref_audio(self):
        # ... (不变) ...
        filepath = filedialog.askopenfilename(
            initialdir=os.path.dirname(self.ref_audio_var.get()) or os.getcwd(),
            title="选择参考音频文件",
            filetypes=(("音频文件", "*.wav *.mp3 *.ogg *.flac"), ("所有文件", "*.*"))
        )
        if filepath:
            self.ref_audio_var.set(filepath)

    def browse_model_file(self, var, filetypes):
        # ... (不变) ...
        current_path = var.get()
        initial_dir = os.path.dirname(current_path) if current_path and os.path.exists(os.path.dirname(current_path)) else os.getcwd()
        filepath = filedialog.askopenfilename(
            initialdir=initial_dir,
            title=f"选择 {filetypes[0][0]}",
            filetypes=filetypes + [("所有文件", "*.*")]
        )
        if filepath:
            var.set(filepath)


    # --- YAML 配置处理 ---
    def update_config_files(self):
        """更新 YAML 配置文件下拉列表"""
        self.update_files_list(self.config_dir_var.get(), self.config_file_combo, (".yaml", ".yml"))
        # 列表更新后，也尝试重新加载当前选项
        # self.load_and_display_config() # 避免过早调用

    def update_files_list(self, directory, combobox, extensions):
        """通用函数：根据文件夹和扩展名更新文件列表"""
        # ... (不变) ...
        files = []
        if directory and os.path.isdir(directory):
            try:
                for filename in os.listdir(directory):
                    if filename.lower().endswith(extensions):
                        files.append(filename)
            except Exception as e:
                self.log_output(f"错误：读取目录 {directory} 失败: {e}")
        combobox['values'] = sorted(files)
        last_selected = self.config.get("config_file", "")
        if last_selected in files:
            combobox.set(last_selected)
        elif files:
            combobox.current(0)
        else:
            combobox.set("")


    def load_and_display_config(self, event=None):
        """加载选定的YAML文件，解析custom块并填充编辑UI"""
        # ... (与 editable 版本基本一致) ...
        config_dir = self.config_dir_var.get()
        config_file = self.config_file_var.get()

        if not config_dir or not config_file:
            self.set_edit_frame_state(tk.DISABLED)
            self.current_yaml_data = None
            self.current_config_path = None
            return

        self.current_config_path = os.path.join(config_dir, config_file)

        if os.path.exists(self.current_config_path):
            try:
                with open(self.current_config_path, 'r', encoding='utf-8') as f:
                    self.current_yaml_data = yaml.safe_load(f)
                    if not isinstance(self.current_yaml_data, dict):
                        messagebox.showerror("YAML 错误", f"配置文件 '{config_file}' 的顶层结构无效。")
                        self.current_yaml_data = None
                        self.set_edit_frame_state(tk.DISABLED)
                        return

                custom_config = self.current_yaml_data.get('custom')
                if custom_config is None:
                    if messagebox.askyesno("确认", f"配置文件 '{config_file}' 中没有找到 'custom:' 配置块。\n是否要创建一个新的 custom 配置块？"):
                        custom_config = {}
                        self.current_yaml_data['custom'] = custom_config
                    else:
                        self.current_yaml_data = None
                        self.set_edit_frame_state(tk.DISABLED)
                        return

                if not isinstance(custom_config, dict):
                    messagebox.showerror("YAML 错误", f"配置文件 '{config_file}' 中的 'custom:' 块结构无效。")
                    self.current_yaml_data = None
                    self.set_edit_frame_state(tk.DISABLED)
                    return

                self.custom_gpt_path_var.set(custom_config.get('t2s_weights_path', ''))
                self.custom_sovits_path_var.set(custom_config.get('vits_weights_path', ''))
                self.custom_device_var.set(custom_config.get('device', 'cuda'))
                is_half = custom_config.get('is_half', True)
                self.custom_precision_var.set("半精度" if is_half else "全精度")
                self.custom_version_var.set(custom_config.get('version', 'v2'))
                self.custom_bert_path_var.set(custom_config.get('bert_base_path', ''))
                self.custom_hubert_path_var.set(custom_config.get('cnhuhbert_base_path', ''))

                self.set_edit_frame_state(tk.NORMAL)
                self.log_output(f"已加载配置文件: {config_file}")

            except yaml.YAMLError as e:
                messagebox.showerror("YAML 解析错误", f"无法解析 '{config_file}':\n{e}")
                self.current_yaml_data = None
                self.set_edit_frame_state(tk.DISABLED)
            except Exception as e:
                messagebox.showerror("文件读取错误", f"读取或处理时出错:\n{e}")
                self.current_yaml_data = None
                self.set_edit_frame_state(tk.DISABLED)
        else:
            messagebox.showwarning("文件未找到", f"文件 '{config_file}' 不存在。")
            self.current_yaml_data = None
            self.set_edit_frame_state(tk.DISABLED)

    def set_edit_frame_state(self, state):
        """启用或禁用编辑区域的所有子控件"""
        # ... (与 editable 版本一致) ...
        for widget in self.edit_frame.winfo_children():
            try:
                if isinstance(widget, ttk.Combobox): widget.configure(state='readonly' if state == tk.NORMAL else tk.DISABLED)
                else: widget.configure(state=state)
            except tk.TclError: pass


    def save_custom_config(self):
        """将编辑控件中的值保存回 self.current_yaml_data['custom'] 并写入文件"""
        # ... (与 editable 版本一致) ...
        if self.current_yaml_data is None or self.current_config_path is None:
            messagebox.showerror("错误", "没有加载有效的配置文件，无法保存。")
            return

        try:
            if 'custom' not in self.current_yaml_data or not isinstance(self.current_yaml_data.get('custom'), dict):
                self.current_yaml_data['custom'] = {}
            custom_config = self.current_yaml_data['custom']

            custom_config['t2s_weights_path'] = self.custom_gpt_path_var.get()
            custom_config['vits_weights_path'] = self.custom_sovits_path_var.get()
            custom_config['device'] = self.custom_device_var.get()
            custom_config['is_half'] = True if self.custom_precision_var.get() == "半精度" else False
            custom_config['version'] = self.custom_version_var.get()
            custom_config['bert_base_path'] = self.custom_bert_path_var.get()
            custom_config['cnhuhbert_base_path'] = self.custom_hubert_path_var.get()

            with open(self.current_config_path, 'w', encoding='utf-8') as f:
                yaml.dump(self.current_yaml_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            messagebox.showinfo("成功", f"Custom 配置已保存到:\n{self.current_config_path}")
            self.log_output(f"Custom 配置已保存到: {os.path.basename(self.current_config_path)}")
        except Exception as e:
            messagebox.showerror("保存错误", f"保存配置时出错:\n{e}")


    # --- API 进程管理与日志 ---
    def log_output(self, message):
        # ... (不变) ...
        self.output_text.config(state=tk.NORMAL)
        self.output_text.insert(tk.END, message + "\n")
        self.output_text.see(tk.END)
        self.output_text.config(state=tk.DISABLED)

    def read_output(self, pipe):
        # ... (不变) ...
        try:
            for line in iter(pipe.readline, ''): self.output_queue.put(line)
        except Exception: pass
        finally: pipe.close(); self.output_queue.put(None)

    def process_queue(self):
        # ... (不变) ...
        try:
            while True:
                line = self.output_queue.get_nowait()
                if line is None: pass
                elif isinstance(line, str): self.log_output(line.strip())
                elif isinstance(line, bytes): self.log_output(line.decode('utf-8', errors='replace').strip())
        except queue.Empty: pass
        if self.process and self.process.poll() is not None:
            self.log_output("--- 代理 API 服务已停止 ---")
            self.stop_button.config(state=tk.DISABLED)
            self.launch_button.config(state=tk.NORMAL)
            self.process = None
        self.root.after(100, self.process_queue)

    def launch_api(self):
        """启动 proxy_api.py 脚本，并传递所有必需参数"""
        if self.process is not None and self.process.poll() is None:
            messagebox.showwarning("警告", "代理 API 服务已在运行中。")
            return

        config_path = self.current_config_path
        ref_audio_path = self.ref_audio_var.get()
        ref_text = self.ref_text_var.get()
        ref_lang = self.ref_lang_var.get()
        host = self.host_var.get()
        port = self.port_var.get()
        python_exec = self.python_exec_var.get() or "python"
        api_script_path = os.path.join(os.path.dirname(__file__), "proxy_api.py") # 目标是代理脚本

        # 基础检查
        if not os.path.exists(api_script_path):
            messagebox.showerror("错误", f"无法找到 proxy_api.py 脚本于: {api_script_path}")
            return
        if not config_path:
            messagebox.showerror("错误", "请先选择一个有效的 YAML 配置文件！")
            return
        if not os.path.exists(config_path):
            messagebox.showerror("错误", f"选择的配置文件不存在: {config_path}")
            return
        if not ref_audio_path or not ref_text or not ref_lang:
            messagebox.showerror("错误", "请设置完整的默认参考音频信息（文件、文本、语言）！")
            return
        if not os.path.exists(ref_audio_path):
            messagebox.showerror("错误", f"选择的参考音频文件不存在: {ref_audio_path}")
            return

        project_root = os.path.dirname(os.path.abspath(__file__))
        api_script_path = os.path.join(project_root, "proxy_api.py")

        # --- 构建命令 ---
        cmd = [python_exec, api_script_path]
        cmd.extend(["-c", config_path])
        cmd.extend(["-dr", ref_audio_path])
        cmd.extend(["-dt", ref_text]) # 需要确保文本中的特殊字符能正确传递，或在 proxy_api.py 中处理
        cmd.extend(["-dl", ref_lang])
        if host: cmd.extend(["-a", host])
        if port: cmd.extend(["-p", port])

        # 清空日志 & 启动
        self.output_text.config(state=tk.NORMAL)
        self.output_text.delete(1.0, tk.END)
        self.output_text.config(state=tk.DISABLED)
        self.log_output(f"执行命令: {' '.join(cmd)}") # 打印前确认 cmd 列表内容正确
        self.log_output("--- 正在启动代理 API 服务... ---")
        self.log_output(f"设置工作目录为: {project_root}") # 确认打印了这行


        try:
            # ... (启动 subprocess 的代码不变) ...
            creationflags = 0
            if sys.platform == "win32": creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding='utf-8', errors='replace', bufsize=1, universal_newlines=True,
                creationflags=creationflags,
                cwd=project_root
            )
            threading.Thread(target=self.read_output, args=(self.process.stdout,), daemon=True).start()
            threading.Thread(target=self.read_output, args=(self.process.stderr,), daemon=True).start()
            self.launch_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.NORMAL)
        except FileNotFoundError:
            self.log_output(f"错误：无法找到 Python '{python_exec}' 或 proxy_api.py。")
            messagebox.showerror("启动错误", f"无法找到 Python '{python_exec}' 或 proxy_api.py。")
            self.process = None
        except Exception as e:
            self.log_output(f"启动代理 API 时发生未知错误: {e}")
            messagebox.showerror("启动错误", f"启动代理 API 时发生未知错误: {e}")
            self.process = None

    def stop_api(self):
        """停止正在运行的 proxy_api.py 进程"""
        # ... (不变) ...
        if self.process and self.process.poll() is None:
            self.log_output("--- 正在停止代理 API 服务... ---")
            try:
                if sys.platform == "win32":
                    os.kill(self.process.pid, signal.CTRL_BREAK_EVENT)
                    try: self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.process.pid)], check=False, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                    try: self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
            except Exception as e:
                self.log_output(f"停止进程时出错: {e}.")
            finally:
                self.process = None
                self.stop_button.config(state=tk.DISABLED)
                self.launch_button.config(state=tk.NORMAL)
        else:
            self.log_output("代理 API 服务未运行或已停止。")
            self.stop_button.config(state=tk.DISABLED)
            self.launch_button.config(state=tk.NORMAL)
            self.process = None


    def on_closing(self):
        """关闭窗口时的处理"""
        self.save_config() # 保存 GUI 设置
        if self.process and self.process.poll() is None:
            if messagebox.askyesno("退出", "代理 API 服务仍在运行中。是否停止服务并退出？"):
                self.stop_api()
                self.root.destroy()
        else:
            self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = ApiV2ProxyEditableLauncherApp(root) # 实例化最终的类
    root.mainloop()
# --- END OF FILE launch_gui_v2_proxy_editable.py ---