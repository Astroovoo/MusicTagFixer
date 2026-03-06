#!/usr/bin/env python3
from __future__ import annotations

import threading
from pathlib import Path
from queue import Empty, Queue
from typing import Optional
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from fix_mp3_japanese_mojibake import fix_filename, fix_tags, iter_mp3_files


class MP3FixerGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MusicTagFixer GUI")
        self.root.geometry("900x650")

        self.queue: Queue = Queue()
        self.worker: Optional[threading.Thread] = None
        self._build_ui()
        self._poll_queue()

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        path_frame = ttk.LabelFrame(container, text="1) 选择目标", padding=10)
        path_frame.pack(fill=tk.X)

        self.path_var = tk.StringVar()
        ttk.Label(path_frame, text="目录或单个 MP3:").grid(row=0, column=0, sticky=tk.W)
        self.path_entry = ttk.Entry(path_frame, textvariable=self.path_var)
        self.path_entry.grid(row=0, column=1, sticky=tk.EW, padx=8)
        ttk.Button(path_frame, text="选择目录", command=self._pick_directory).grid(row=0, column=2, padx=4)
        ttk.Button(path_frame, text="选择 MP3", command=self._pick_file).grid(row=0, column=3, padx=4)
        path_frame.columnconfigure(1, weight=1)

        opt_frame = ttk.LabelFrame(container, text="2) 处理选项", padding=10)
        opt_frame.pack(fill=tk.X, pady=10)

        self.recursive_var = tk.BooleanVar(value=True)
        self.dry_run_var = tk.BooleanVar(value=True)
        self.rename_var = tk.BooleanVar(value=True)
        self.tags_var = tk.BooleanVar(value=True)
        self.verbose_var = tk.BooleanVar(value=True)

        ttk.Checkbutton(opt_frame, text="递归子目录", variable=self.recursive_var).grid(row=0, column=0, sticky=tk.W)
        ttk.Checkbutton(opt_frame, text="仅预览 (Dry Run)", variable=self.dry_run_var).grid(row=0, column=1, sticky=tk.W, padx=8)
        ttk.Checkbutton(opt_frame, text="修复文件名", variable=self.rename_var).grid(row=0, column=2, sticky=tk.W)
        ttk.Checkbutton(opt_frame, text="修复 ID3 标签", variable=self.tags_var).grid(row=0, column=3, sticky=tk.W, padx=8)
        ttk.Checkbutton(opt_frame, text="详细日志", variable=self.verbose_var).grid(row=0, column=4, sticky=tk.W)

        genre_frame = ttk.LabelFrame(container, text="3) Genre 批量设置 (TCON)", padding=10)
        genre_frame.pack(fill=tk.X)

        self.genre_var = tk.StringVar()
        self.genre_mode_var = tk.StringVar(value="fill")
        ttk.Label(genre_frame, text="设置 Genre(可留空):").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(genre_frame, textvariable=self.genre_var, width=28).grid(row=0, column=1, sticky=tk.W, padx=8)
        ttk.Label(genre_frame, text="模式:").grid(row=0, column=2, sticky=tk.W, padx=(12, 4))
        ttk.Radiobutton(genre_frame, text="fill(仅填空)", variable=self.genre_mode_var, value="fill").grid(row=0, column=3, sticky=tk.W)
        ttk.Radiobutton(genre_frame, text="overwrite(覆盖)", variable=self.genre_mode_var, value="overwrite").grid(row=0, column=4, sticky=tk.W, padx=8)
        ttk.Radiobutton(genre_frame, text="merge(合并)", variable=self.genre_mode_var, value="merge").grid(row=0, column=5, sticky=tk.W)

        run_frame = ttk.Frame(container)
        run_frame.pack(fill=tk.X, pady=10)
        self.start_btn = ttk.Button(run_frame, text="开始处理", command=self._start)
        self.start_btn.pack(side=tk.LEFT)
        ttk.Button(run_frame, text="清空日志", command=self._clear_log).pack(side=tk.LEFT, padx=8)

        self.progress_var = tk.StringVar(value="等待开始")
        ttk.Label(run_frame, textvariable=self.progress_var).pack(side=tk.LEFT, padx=12)

        self.progress = ttk.Progressbar(container, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(0, 10))

        log_frame = ttk.LabelFrame(container, text="4) 输出日志", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, height=20)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

    def _pick_directory(self) -> None:
        selected = filedialog.askdirectory()
        if selected:
            self.path_var.set(selected)

    def _pick_file(self) -> None:
        selected = filedialog.askopenfilename(
            filetypes=[("MP3 Files", "*.mp3"), ("All Files", "*.*")]
        )
        if selected:
            self.path_var.set(selected)

    def _clear_log(self) -> None:
        self.log_text.delete("1.0", tk.END)

    def _append_log(self, line: str) -> None:
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("处理中", "任务正在运行，请等待完成。")
            return

        path_text = self.path_var.get().strip()
        if not path_text:
            messagebox.showerror("路径缺失", "请先选择目录或 MP3 文件。")
            return

        target = Path(path_text).resolve()
        if not target.exists():
            messagebox.showerror("路径不存在", "找不到路径:\n{0}".format(target))
            return

        if not self.rename_var.get() and not self.tags_var.get():
            messagebox.showerror("选项错误", "至少勾选一个处理项：修复文件名 / 修复 ID3 标签。")
            return

        files = list(iter_mp3_files(target, self.recursive_var.get()))
        if not files:
            messagebox.showinfo("没有文件", "目标中未找到 mp3 文件。")
            return

        self.progress["maximum"] = len(files)
        self.progress["value"] = 0
        self.progress_var.set("共 {0} 个文件，准备开始".format(len(files)))
        self.start_btn.config(state=tk.DISABLED)

        self.worker = threading.Thread(
            target=self._run_job,
            args=(files,),
            daemon=True,
        )
        self.worker.start()

    def _run_job(self, files: list) -> None:
        dry_run = self.dry_run_var.get()
        do_rename = self.rename_var.get()
        do_tags = self.tags_var.get()
        verbose = self.verbose_var.get()
        genre_value = self.genre_var.get().strip() or None
        genre_mode = self.genre_mode_var.get()

        total = 0
        renamed = 0
        tagged = 0
        errors = 0

        for idx, file in enumerate(files, start=1):
            total += 1
            current_file = file

            if do_rename:
                ok, rename_log, new_path = fix_filename(file, dry_run)
                if rename_log and (ok or verbose):
                    self.queue.put(("log", "[{0}] {1}".format(file, rename_log)))
                if ok:
                    renamed += 1
                    if not dry_run and new_path is not None:
                        current_file = new_path
                elif rename_log.startswith("skip"):
                    errors += 1

            if do_tags:
                changed, tag_logs = fix_tags(current_file, dry_run, genre_value, genre_mode)
                if changed:
                    tagged += 1
                    if verbose:
                        for line in tag_logs:
                            self.queue.put(("log", "[{0}] {1}".format(current_file, line)))
                elif verbose and tag_logs:
                    for line in tag_logs:
                        self.queue.put(("log", "[{0}] {1}".format(current_file, line)))

                if tag_logs:
                    for line in tag_logs:
                        if "tag error:" in line:
                            errors += 1

            self.queue.put(("progress", (idx, len(files))))

        mode = "DRY RUN" if dry_run else "APPLIED"
        summary = "[{0}] scanned={1}, renamed={2}, tag_updated={3}, errors={4}".format(
            mode, total, renamed, tagged, errors
        )
        self.queue.put(("done", summary))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "progress":
                    idx, total = payload
                    self.progress["value"] = idx
                    self.progress_var.set("处理中: {0}/{1}".format(idx, total))
                elif kind == "done":
                    self._append_log(str(payload))
                    self.progress_var.set("完成")
                    self.start_btn.config(state=tk.NORMAL)
                    messagebox.showinfo("完成", str(payload))
        except Empty:
            pass
        finally:
            self.root.after(120, self._poll_queue)


def main() -> int:
    root = tk.Tk()
    MP3FixerGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
