import tkinter as tk
from tkinter import ttk

from macroflow.core.models import normalize_foreground_processes
from macroflow.ui.form_controls import integer_seconds_spinbox
from .base import (
    COLOR_SURFACE, COLOR_TEXT, ModalDialog, interval_var, pad,
    show_floating_notice,
)
from .process_names import ProcessNamePicker


class ForegroundProcessesDialog(ModalDialog):
    def __init__(self, parent, names, interval_ms=3000):
        super().__init__(parent, '工作流防遮挡进程', width=560, height=420)
        frame = ttk.Frame(self, padding=pad(16))
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='工作流运行时，下列进程的窗口切到前台就自动最小化。\n'
                  '暂停时不处理，结束后停止；绑定的游戏和本软件窗口受保护。\n'
                  '留空关闭此功能；保存后下次运行生效。', justify='left').pack(anchor='w')
        interval_row = ttk.Frame(frame)
        interval_row.pack(fill='x', pady=pad(10, 0))
        ttk.Label(interval_row, text='检测间隔（秒）').pack(side='left')
        self.interval = interval_var(interval_ms)
        self.interval_spinbox = integer_seconds_spinbox(
            interval_row, textvariable=self.interval,
        )
        self.interval_spinbox.pack(side='left', padx=pad(8, 0))
        row = ttk.Frame(frame)
        row.pack(fill='x', pady=pad(10, 8))
        self.name = tk.StringVar(self)
        entry = ProcessNamePicker(row, self.name)
        entry.pack(side='left', fill='x', expand=True)
        entry.combo.bind('<Return>', lambda _event: self._add())
        ttk.Button(row, text='添加', command=self._add).pack(side='left', padx=pad(6, 6))
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
        save_button = ttk.Button(buttons, text='保存', command=self._save)
        save_button.pack(side='right', padx=pad(0, 6))
        from macroflow.ui.save_state import SaveState
        self.save_state = SaveState(self, save_button,
            lambda: (self.processes.get(0, 'end'), self.interval.get()))

    def _add(self):
        names = normalize_foreground_processes([self.name.get()])
        if names and names[0] not in self.processes.get(0, 'end'):
            self.processes.insert('end', names[0])
        self.name.set('')

    def _remove(self):
        for index in reversed(self.processes.curselection()):
            self.processes.delete(index)

    def _save(self):
        try:
            interval_ms = int(self.interval.get())
            if not 1000 <= interval_ms <= 60000:
                raise ValueError
        except (ValueError, OverflowError):
            show_floating_notice(self, '检测间隔无效', '请输入 1–60 秒之间的整数检测间隔。')
            return
        self.result = (list(self.processes.get(0, 'end')), interval_ms)
        self.destroy()
