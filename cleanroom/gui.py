"""Chinese desktop interface; workers never access Tk objects."""

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .core import clean_document, inspect_document

FILE_TYPES = (
    ("Word 文档", "*.docx"), ("PDF 文档", "*.pdf"),
    ("UTF-8 文本", "*.txt"), ("Markdown", "*.md"),
)


class TaskRunner:
    """Only the main thread submits/polls; the worker writes to a safe queue."""

    def __init__(self):
        self.busy = False
        self._results = queue.Queue()

    def submit(self, work):
        if self.busy:
            return False
        self.busy = True
        threading.Thread(target=self._execute, args=(work,), daemon=False).start()
        return True

    def _execute(self, work):
        try:
            result = (True, work())
        except Exception as exc:
            result = (False, str(exc) or type(exc).__name__)
        self._results.put(result)

    def poll(self):
        try:
            result = self._results.get_nowait()
        except queue.Empty:
            return None
        self.busy = False
        return result


class DocumentApp:
    """All Tk interactions happen in event callbacks on the main thread."""

    def __init__(self, root):
        self.root = root
        self.runner = TaskRunner()
        self.source = tk.StringVar(root)
        self.output = tk.StringVar(root)
        self.mode = tk.StringVar(root, value="preserve")
        self.remove_invisibles = tk.BooleanVar(root, value=True)
        self.allow_lossy = tk.BooleanVar(root, value=False)
        self.status = tk.StringVar(root, value="请选择文件；建议先检查，再选择处理方式。")
        self._success_message = ""
        root.title("文档清理与重建 · Doc Cleanroom")
        root.geometry("900x720")
        root.minsize(800, 650)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        root.after(75, self._poll)

    def _build(self):
        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(8, weight=1)
        ttk.Label(frame, text=(
            "本工具不检测 textGrain 等统计文本水印；原样重打不能保证消除。\n"
            "仅检查/清理说明中列出的字符与元数据；不证明文字由人类撰写或无法被 AI 检测。"
        ), foreground="#943127", wraplength=820).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))
        self._path_row(frame, 1, "输入文件", self.source, self.choose_source)
        self._path_row(frame, 2, "输出文件", self.output, self.choose_output)
        modes = ttk.Frame(frame)
        modes.grid(row=3, column=0, columnspan=3, sticky="w", pady=10)
        ttk.Label(modes, text="处理方式：").pack(side="left")
        for value, label in (("preserve", "保版清理"), ("rebuild", "文字重建")):
            ttk.Radiobutton(modes, text=label, value=value,
                            variable=self.mode).pack(side="left", padx=8)
        ttk.Label(frame, text=(
            "保版：保持文件类型；DOCX 清理支持的隐藏字符/元数据，PDF 仅清理元数据。\n"
            "文字重建：可更换输出类型，会丢失原有版式，图片、公式和其他内容可能丢失。\n"
            "不自动删除可见文字或图片水印；复杂文档处理后必须对照原件人工检查。"
        ), wraplength=820).grid(row=4, column=0, columnspan=3, sticky="w")
        options = ttk.Frame(frame)
        options.grid(row=5, column=0, columnspan=3, sticky="w", pady=10)
        ttk.Checkbutton(options, text=(
            "清理 4 种字符：U+200B、U+2060、U+FEFF、U+00AD（可能影响断行）"
        ), variable=self.remove_invisibles).pack(anchor="w")
        ttk.Checkbutton(options, text=(
            "允许复杂文档有损重建：我了解图片、公式、页眉页脚等可能丢失"
        ), variable=self.allow_lossy).pack(anchor="w", pady=(6, 0))
        actions = ttk.Frame(frame)
        actions.grid(row=6, column=0, columnspan=3, sticky="w", pady=4)
        self.inspect_button = ttk.Button(actions, text="1. 检查文件", command=self.inspect)
        self.inspect_button.pack(side="left")
        self.process_button = ttk.Button(actions, text="2. 处理并导出", command=self.process)
        self.process_button.pack(side="left", padx=12)
        ttk.Label(frame, textvariable=self.status, wraplength=820).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=8)
        self.results = scrolledtext.ScrolledText(frame, wrap="word", height=14, state="disabled")
        self.results.grid(row=8, column=0, columnspan=3, sticky="nsew")

    @staticmethod
    def _path_row(frame, row, label, variable, command):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(frame, text="选择…", command=command).grid(row=row, column=2, padx=(8, 0))

    def choose_source(self):
        path = filedialog.askopenfilename(parent=self.root, title="选择待检查文档", filetypes=FILE_TYPES)
        if path:
            self.source.set(path)
            source = Path(path)
            self.output.set(str(source.with_name(source.stem + ".cleaned" + source.suffix)))

    def choose_output(self):
        source = Path(self.source.get())
        suffix = source.suffix or ".docx"
        types = (("与输入相同类型", "*" + suffix),) if self.mode.get() == "preserve" else FILE_TYPES
        path = filedialog.asksaveasfilename(
            parent=self.root, title="另存为新文件（已有文件不会被覆盖）",
            filetypes=types, defaultextension=suffix, confirmoverwrite=False,
            initialfile=Path(self.output.get()).name or "cleaned" + suffix,
        )
        if path:
            self.output.set(path)

    def inspect(self):
        if not self.source.get().strip():
            self._error("请先选择输入文件。")
            return
        source = Path(self.source.get())
        self._submit(lambda: inspect_document(source), "检查完成：以下是可观察项目，不代表水印判断。")

    def process(self):
        if not self.source.get().strip() or not self.output.get().strip():
            self._error("请选择输入文件，并指定一个新的输出文件。")
            return
        source, output = Path(self.source.get()), Path(self.output.get())
        options = {"mode": self.mode.get(), "remove_invisibles": self.remove_invisibles.get(),
                   "allow_lossy": self.allow_lossy.get()}
        self._submit(lambda: clean_document(source, output, **options),
                     f"已导出：{output}\n审计报告：{output}.audit.json")

    def _submit(self, work, success_message):
        if self.runner.submit(work):
            self._success_message = success_message
            self.status.set("处理中，请等待。源文件不会被覆盖。")
            self.inspect_button.state(["disabled"])
            self.process_button.state(["disabled"])

    def _poll(self):
        result = self.runner.poll()
        if result is not None:
            self.inspect_button.state(["!disabled"])
            self.process_button.state(["!disabled"])
            success, payload = result
            if success:
                self.status.set(self._success_message)
                self._show(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                self._error(payload)
        self.root.after(75, self._poll)

    def _show(self, text):
        self.results.configure(state="normal")
        self.results.delete("1.0", "end")
        self.results.insert("1.0", text)
        self.results.configure(state="disabled")

    def _error(self, text):
        self.status.set("操作失败：" + text)
        self._show(text)
        messagebox.showerror("文档处理失败", text, parent=self.root)

    def close(self):
        if self.runner.busy:
            messagebox.showwarning("正在处理", "请等待当前任务完成后再关闭窗口。", parent=self.root)
            return
        self.root.destroy()


def launch() -> None:
    root = tk.Tk()
    DocumentApp(root)
    root.mainloop()
