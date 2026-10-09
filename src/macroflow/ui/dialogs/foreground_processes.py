import tkinter as tk
from tkinter import ttk

from macroflow.core.models import normalize_foreground_processes
from .base import (
    COLOR_SURFACE, COLOR_TEXT, ModalDialog, app_windows, pad,
)
from .screen_pickers import ScreenProcessPicker


class ForegroundProcessesDialog(ModalDialog):
    def __init__(self, parent, names):
        super().__init__(parent, '工作流防遮挡进程', width=560, height=370)
        frame = ttk.Frame(self, padding=pad(16))
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='工作流运行时，下列进程的窗口切到前台就自动最小化。\n'
                  '暂停时不处理，结束后停止；绑定的游戏和本软件窗口受保护。\n'
                  '留空关闭此功能；保存后下次运行生效。', justify='left').pack(anchor='w')
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=pad(10, 8))
        self.name = tk.StringVar(self)
        entry = ttk.Entry(row, textvariable=self.name)
        entry.pack(side='left', fill='x', expand=True)
        entry.bind('<Return>', lambda _event: self._add())
        ttk.Button(row, text='添加', command=self._add).pack(side='left', padx=pad(6, 6))
        ttk.Button(row, text='鼠标选取…', command=self._pick).pack(side='left')
        self.processes = tk.Listbox(frame, height=8, selectmode='extended',
                                   background=COLOR_SURFACE, foreground=COLOR_TEXT,
                                   exportselection=False)
        self.processes.pack(fill='both', expand=True)
        for name in normalize_foreground_processes(names):
            self.processes.insert('end', name)
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=pad(10, 0))
        ttk.Button(buttons, text='移除选中', command=self._remove).pack(side='left')
        ttk.Button(buttons, text='取消', command=self.destroy).pack(side='right')
        ttk.Button(buttons, text='保存', command=self._save).pack(side='right', padx=pad(0, 6))

    def _add(self):
        names = normalize_foreground_processes([self.name.get()])
        if names and names[0] not in self.processes.get(0, 'end'):
            self.processes.insert('end', names[0])
        self.name.set('')

    def _picked(self, name):
        self.name.set(name)
        self._add()

    def _pick(self):
        self.picker = ScreenProcessPicker(
            self, self.master, self._picked, hidden_windows=app_windows(self.master),
            tip_text='移动到目标软件，预览进程名；单击添加，Esc 取消',
        )
        self.picker.start()

    def _remove(self):
        for index in reversed(self.processes.curselection()):
            self.processes.delete(index)

    def _save(self):
        self.result = list(self.processes.get(0, 'end'))
        self.destroy()
