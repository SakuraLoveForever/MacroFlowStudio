"""Persistent battle-client monitor switch in the plugins page."""
import tkinter as tk

import ttkbootstrap as ttk

from macroflow.execution.disconnect_monitor import CONSECUTIVE_FAILURES, DisconnectMonitor
from macroflow.ui.dialogs.base import Tooltip

from .base import pad


def get_disconnect_monitor(app):
    monitor = app.__dict__.get('_disconnect_monitor')
    if monitor is not None:
        return monitor
    app.disconnect_monitor_enabled = bool(app.app_settings.get("disconnect_monitor_enabled", False))
    monitor = app._disconnect_monitor = DisconnectMonitor(lambda text: app._ui(app._log, text))

    def destroyed(event):
        if event.widget is app.root:
            monitor.stop()

    app.root.bind("<Destroy>", destroyed, add="+")
    if app.disconnect_monitor_enabled:
        monitor.start()
    return monitor


def add_disconnect_monitor(parent, app) -> None:
    get_disconnect_monitor(app)
    enabled = tk.BooleanVar(master=app.root, value=app.disconnect_monitor_enabled)

    def toggle():
        set_disconnect_monitor_enabled(app, bool(enabled.get()))

    control = ttk.Checkbutton(parent, text="战斗端断线检测", variable=enabled,
                              command=toggle)
    control.pack(anchor="w", pady=pad(0, 8))
    Tooltip(control, "保存配置后，下次启动恢复此选项。每 5 秒检查 SSJJ_BattleClient_Unity.exe；"
            "启动超过 20 秒后，排除远端 127.0.0.1 和端口 80，"
            f"存在非已建立的 TCP 连接或无剩余连接时记为一次异常，"
            f"连续 {CONSECUTIVE_FAILURES} 次异常才强制结束战斗端。")


def set_disconnect_monitor_enabled(app, enabled):
    monitor = get_disconnect_monitor(app)
    app.disconnect_monitor_enabled = bool(enabled)
    if app.disconnect_monitor_enabled:
        monitor.start()
        app._log("战斗端断线检测已开启：每 5 秒检查战斗端，启动宽限 20 秒，"
                 f"连续 {CONSECUTIVE_FAILURES} 次连接异常才结束战斗端。")
    else:
        monitor.stop()
        app._log("战斗端断线检测已关闭。")
