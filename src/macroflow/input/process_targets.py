from __future__ import annotations

import os
import psutil


class ProcessTargetReader:
    """Resolve a live window through pywinauto; never activate or click it."""

    def __init__(self):
        from pywinauto import Desktop

        self.desktop = Desktop(backend="win32")

    def at(self, x: int, y: int, full_path: bool = False) -> str:
        window = self.desktop.top_from_point(int(x), int(y))
        if window is None:
            return ""
        pid = window.process_id()
        if pid <= 0 or pid == os.getpid():
            return ""
        process = psutil.Process(pid)
        return process.exe() if full_path else process.name()
