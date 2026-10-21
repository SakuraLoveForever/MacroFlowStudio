from __future__ import annotations

from pathlib import Path
from macroflow.input.wininput import WindowInfo, enum_windows, get_cursor_pos, is_current_process_window, window_from_point
from tkinter import ttk
import tkinter as tk

from .base import COLOR_BLUE_SELECTION, COLOR_MUTED, COLOR_TEXT, ModalDialog, pad, px, selectable_target_windows, show_floating_notice



class WindowPicker(ModalDialog):
    def __init__(self, parent, title: str = "选择要绑定的窗口",
                 confirm_text: str = "绑定所选窗口"):
        super().__init__(parent, title, 820, 520)
        self.confirm_text = confirm_text
        self.windows: list[WindowInfo] = []
        self.search_var = tk.StringVar()
        self.dragging = False
        top = ttk.Frame(self, padding=px(14))
        top.pack(fill="x")
        ttk.Label(top, text="搜索窗口").pack(side="left")
        entry = ttk.Entry(top, textvariable=self.search_var, width=42)
        entry.pack(side="left", padx=px(10))
        entry.bind("<KeyRelease>", lambda _: self._render())
        self.drag_handle = tk.Label(
            top, text="✚", width=3, cursor="crosshair",
            background=COLOR_BLUE_SELECTION, foreground=COLOR_TEXT,
            font=("Segoe UI Symbol", 13), relief="raised", bd=1,
        )
        self.drag_handle.pack(side="right", padx=pad(8, 0))
        self.drag_handle.bind("<ButtonPress-1>", self._drag_start)
        self.drag_handle.bind("<B1-Motion>", self._drag_motion)
        self.drag_handle.bind("<ButtonRelease-1>", self._drag_release)
        ttk.Label(top, text="按住十字拖到目标窗口后松开", foreground=COLOR_MUTED).pack(side="right", padx=pad(8, 0))
        ttk.Button(top, text="刷新", command=self.refresh).pack(side="right")
        frame = ttk.Frame(self, padding=pad(14, 0, 14, 8))
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("title", "class", "process"), show="headings", selectmode="browse")
        self.tree.heading("title", text="窗口标题")
        self.tree.heading("class", text="窗口类")
        self.tree.heading("process", text="进程")
        self.tree.column("title", width=px(360))
        self.tree.column("class", width=px(220))
        self.tree.column("process", width=px(180))
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _: self.choose())
        bottom = ttk.Frame(self, padding=px(14))
        bottom.pack(fill="x")
        ttk.Label(bottom, text="仅显示当前可见且有标题的顶层窗口。", foreground=COLOR_MUTED).pack(side="left")
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text=self.confirm_text, command=self.choose).pack(side="right", padx=px(8))
        self.refresh()

    def refresh(self):
        self.windows = selectable_target_windows(enum_windows())
        self._render()

    def _render(self):
        query = self.search_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.windows):
            process_path = str(item.process_path or "")
            if query and query not in item.title.lower() and query not in item.class_name.lower() \
                    and query not in process_path.lower():
                continue
            self.tree.insert("", "end", iid=str(index),
                             values=(item.title, item.class_name, Path(process_path).name))

    def choose(self):
        selected = self.tree.selection()
        if not selected:
            show_floating_notice(self, "请选择", "请先选择一个窗口。")
            return
        self.result = self.windows[int(selected[0])]
        self.destroy()


    def _drag_start(self, _event):
        self.dragging = True
        self.configure(cursor="crosshair")
        self.drag_handle.configure(text="●")

    def _drag_motion(self, _event):
        if not self.dragging:
            return
        x, y = get_cursor_pos()
        info = window_from_point(x, y)
        if info and not is_current_process_window(info.hwnd):
            self.title(f"拖放选择：{info.title or info.class_name}")
        else:
            self.title("选择要绑定的窗口")

    def _drag_release(self, _event):
        if not self.dragging:
            return
        self.dragging = False
        self.configure(cursor="")
        self.drag_handle.configure(text="✚")
        x, y = get_cursor_pos()
        info = window_from_point(x, y)
        if info and not is_current_process_window(info.hwnd):
            self.result = info
            self.destroy()
        else:
            self.title("选择要绑定的窗口")

