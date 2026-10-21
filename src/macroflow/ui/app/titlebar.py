"""附属于主窗口的标题栏托盘入口。"""
import tkinter as tk

from macroflow.input.wininput import (
    make_window_no_activate, move_window_no_activate, set_window_owner,
    titlebar_hide_button_rect,
)
from .base import px
from .constants import FONT_FAMILY


class TitlebarHideButton:
    def __init__(self, app):
        self.app = app
        self.pending = False
        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        tk.Button(
            self.window, text="隐藏", command=self.hide,
            font=(FONT_FAMILY, 9), bg="#0E1419", fg="#E8EDF2",
            activebackground="#263541", activeforeground="#FFFFFF",
            relief="flat", bd=0, highlightthickness=0, takefocus=False,
        ).pack(fill="both", expand=True)
        for event in ("<Configure>", "<Map>", "<Unmap>"):
            app.root.bind(event, self._schedule_sync, add="+")
        app.root.after_idle(self.sync)

    def _schedule_sync(self, event):
        if event.widget is not self.app.root or self.pending:
            return
        self.pending = True
        self.app.root.after_idle(self.sync)

    def sync(self):
        self.pending = False
        root = self.app.root
        if root.state() not in ("normal", "zoomed"):
            self.window.withdraw()
            return
        rect = titlebar_hide_button_rect(root.winfo_id(), px(46))
        if rect is None:
            self.window.withdraw()
            return
        x, y, width, height = rect
        self.window.geometry(f"{width}x{height}")
        self.window.deiconify()
        # Tk creates/replaces the native wrapper during its mapping idle work.
        self.window.update_idletasks()
        set_window_owner(self.window.winfo_id(), root.winfo_id())
        make_window_no_activate(self.window.winfo_id(), topmost=False)
        move_window_no_activate(self.window.winfo_id(), x, y)

    def hide(self):
        self.app._persist_workflow_draft()
        if not self.app._hide_main_to_tray():
            self.app._notify("无法隐藏", "托盘图标暂时不可用，请稍后重试。")
