"""Persistent battle-client monitor switch in the Studio sidebar."""
import tkinter as tk

import ttkbootstrap as ttk

from macroflow.execution.disconnect_monitor import DisconnectMonitor
from macroflow.ui.dialogs.base import Tooltip

from .base import pad


def add_disconnect_monitor(parent, app) -> None:
    app.disconnect_monitor_enabled = bool(
        app.app_settings.get("disconnect_monitor_enabled", False)
    )
    enabled = tk.BooleanVar(master=app.root, value=app.disconnect_monitor_enabled)
    monitor = DisconnectMonitor(lambda text: app._ui(app._log, text))

    def toggle():
        app.disconnect_monitor_enabled = bool(enabled.get())
        if app.disconnect_monitor_enabled:
            monitor.start()
            app._log("战斗端断线检测已开启：每 5 秒检查，启动宽限 20 秒，连接异常时结束战斗端。")
        else:
            monitor.stop()
            app._log("战斗端断线检测已关闭。")

    control = ttk.Checkbutton(parent, text="战斗端断线检测", variable=enabled,
                              command=toggle)
    control.pack(anchor="w", pady=pad(0, 8))
    Tooltip(control, "保存配置后，下次启动恢复此选项。每 5 秒检查 SSJJ_BattleClient_Unity.exe；"
            "启动超过 20 秒后，排除远端 127.0.0.1 和端口 80，"
            "存在非已建立的 TCP 连接或无剩余连接时强制结束战斗端。")

    def destroyed(event):
        if event.widget is app.root:
            monitor.stop()

    app.root.bind("<Destroy>", destroyed, add="+")
    if app.disconnect_monitor_enabled:
        toggle()
