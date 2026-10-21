"""Select captured process names once and reuse them throughout the editor."""
import tkinter as tk
from tkinter import ttk

from macroflow.core.process_library import (
    add_process_name, load_process_names, process_name_choices, remove_process_names,
)
from macroflow.execution.player import running_process_names
from .base import COLOR_SURFACE, COLOR_TEXT, ModalDialog, app_windows, pad, show_floating_notice
from .screen_pickers import ScreenProcessPicker


class ProcessLibraryDialog(ModalDialog):
    def __init__(self, parent):
        super().__init__(parent, "进程名库", width=620, height=440)
        body = ttk.Frame(self, padding=pad(16))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="保存多个进程名，供所有插件和模块选用；目标软件关闭后仍可选择。\n"
                  "只能从运行列表选取或用鼠标抓取，不能手动输入。", justify="left").pack(anchor="w")
        row = ttk.Frame(body)
        row.pack(fill="x", pady=pad(12, 8))
        self.name = tk.StringVar(self)
        self.running = ttk.Combobox(row, textvariable=self.name, state="readonly",
                                    postcommand=self._refresh_running)
        self.running.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="鼠标抓取…", command=self._pick).pack(side="left", padx=pad(6, 6))
        ttk.Button(row, text="保存到库", command=self._add).pack(side="left")
        self.names = tk.Listbox(body, height=12, selectmode="extended", exportselection=False,
                                background=COLOR_SURFACE, foreground=COLOR_TEXT)
        self.names.pack(fill="both", expand=True)
        self.names.bind("<Double-1>", self._use)
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=pad(12, 0))
        ttk.Button(buttons, text="移除选中", command=self._remove).pack(side="left")
        ttk.Button(buttons, text="关闭", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="使用选中", command=self._use).pack(side="right", padx=pad(0, 6))
        self._refresh_saved()

    def _refresh_saved(self):
        try:
            names = load_process_names()
        except (OSError, ValueError) as exc:
            show_floating_notice(self, "读取进程名库失败", str(exc))
            return
        self.names.delete(0, "end")
        for name in names:
            self.names.insert("end", name)

    def _refresh_running(self):
        try:
            names = running_process_names()
        except OSError as exc:
            show_floating_notice(self, "无法枚举进程", str(exc))
            return
        self.running.configure(values=names)

    def _pick(self):
        self.picker = ScreenProcessPicker(
            self, self.master, self.name.set, hidden_windows=app_windows(self.master),
            tip_text="移动到目标软件，预览进程名；单击抓取，Esc 取消",
        )
        self.picker.start()

    def _add(self):
        try:
            add_process_name(self.name.get())
        except (OSError, ValueError) as exc:
            show_floating_notice(self, "保存进程名失败", str(exc))
            return
        self._refresh_saved()

    def _remove(self):
        selected = [self.names.get(index) for index in self.names.curselection()]
        if not selected:
            return
        try:
            remove_process_names(selected)
        except (OSError, ValueError) as exc:
            show_floating_notice(self, "移除进程名失败", str(exc))
            return
        self._refresh_saved()

    def _use(self, _event=None):
        selected = self.names.curselection()
        if selected:
            self.result = self.names.get(selected[0])
            self.destroy()


class ProcessNamePicker(ttk.Frame):
    """A saved-name selector; process discovery belongs to the library dialog."""
    def __init__(self, parent, variable):
        super().__init__(parent)
        self.variable = variable
        self.combo = ttk.Combobox(self, textvariable=variable, state="readonly", width=24,
                                  postcommand=self.refresh)
        self.combo.pack(side="left", fill="x", expand=True)
        ttk.Button(self, text="进程名库…", command=self._open_library).pack(side="left", padx=pad(6, 0))
        self.refresh()

    def refresh(self):
        try:
            self.combo.configure(values=process_name_choices(current=self.variable.get()))
        except (OSError, ValueError) as exc:
            show_floating_notice(self, "读取进程名库失败", str(exc))

    def _open_library(self):
        selected = ProcessLibraryDialog(self.winfo_toplevel()).show()
        if selected is not None:
            self.variable.set(selected)
        self.refresh()
