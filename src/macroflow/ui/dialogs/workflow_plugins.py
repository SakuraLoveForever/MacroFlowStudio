"""Configuration for built-in workflow plugins."""
import tkinter as tk
from tkinter import ttk

from macroflow.core.workflow_plugins import process_absence_config
from .process_names import ProcessNamePicker
from .base import ModalDialog, TIME_UNITS, duration_var, pad, show_floating_notice


class ProcessAbsencePluginDialog(ModalDialog):
    def __init__(self, parent, config=None):
        super().__init__(parent, "插件 · 进程缺失重启", width=560, height=310)
        config = config or {}
        self._skip_auto_duration_units = True
        body = ttk.Frame(self, padding=pad(16))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="连续指定时间未检测到进程时，从第 1 行重新执行工作流。\n"
                  "进程重新出现即清零计时；暂停期间不计时，工作流结束后停止监控。",
                  justify="left").pack(anchor="w")
        ttk.Label(body, text="指定进程（从进程名库选择）").pack(anchor="w", pady=pad(14, 6))
        self.process_name = tk.StringVar(self, value=config.get("process_name", ""))
        ProcessNamePicker(body, self.process_name).pack(fill="x")
        row = ttk.Frame(body)
        row.pack(fill="x", pady=pad(14, 0))
        ttk.Label(row, text="连续未检测到时长").pack(side="left")
        self.duration = duration_var(config.get("duration_ms", 60000))
        self.duration.unit.set("min")
        ttk.Entry(row, textvariable=self.duration, width=12).pack(side="left", padx=pad(10, 6))
        ttk.Combobox(row, textvariable=self.duration.unit, values=TIME_UNITS,
                     state="readonly", width=5).pack(side="left")
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=pad(20, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        save_button = ttk.Button(buttons, text="保存", command=self._save)
        save_button.pack(side="right", padx=pad(0, 6))
        from macroflow.ui.save_state import SaveState
        self.save_state = SaveState(self, save_button,
            lambda: (self.process_name.get().strip(), self.duration.get()), new=not bool(config))

    def _save(self):
        try:
            self.result = process_absence_config(self.process_name.get(), self.duration.get())
        except (ValueError, OverflowError) as exc:
            show_floating_notice(self, "插件配置无效", str(exc))
            return
        self.destroy()
