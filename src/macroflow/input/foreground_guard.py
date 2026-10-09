"""Minimize listed foreground applications without waiting on their UI threads."""
from pathlib import PureWindowsPath

import pywintypes
import win32con
import win32gui

from .wininput import (
    get_foreground_window_info, is_current_process_window,
    is_window_process_foreground,
)


def minimize_listed_foreground(names: list[str], protected_hwnd: int | None) -> str:
    info = get_foreground_window_info()
    if not info:
        return ''
    name = PureWindowsPath(info.process_path).name.casefold()
    if name not in names or is_current_process_window(info.hwnd) \
            or is_window_process_foreground(protected_hwnd):
        return ''
    try:
        # PostMessage is asynchronous: an unresponsive browser cannot block Tk.
        if win32gui.GetForegroundWindow() != info.hwnd:
            return ''
        win32gui.PostMessage(info.hwnd, win32con.WM_SYSCOMMAND, win32con.SC_MINIMIZE, 0)
    except pywintypes.error:
        return ''
    return name
