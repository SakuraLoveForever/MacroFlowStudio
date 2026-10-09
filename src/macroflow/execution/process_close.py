"""Capture process identities before closing a whole application group."""
from __future__ import annotations

import ctypes
import subprocess

import psutil


def include_descendants(processes: list[psutil.Process]) -> list[psutil.Process]:
    targets = {process.pid: process for process in processes}
    for process in processes:
        try:
            targets.update((child.pid, child) for child in process.children(recursive=True))
        except psutil.NoSuchProcess:
            pass
    return list(targets.values())


def close_targets(names: list[str]) -> list[psutil.Process]:
    wanted = {name.strip().casefold() for name in names}
    roots = [process for process in psutil.process_iter(["name"])
             if str(process.info["name"] or "").casefold() in wanted]
    return include_descendants(roots)


def pending_targets(processes: list[psutil.Process]) -> list[psutil.Process]:
    # Process.is_running also detects PID reuse, unlike checking an image name.
    return [process for process in processes if process.is_running()]


def kill_targets(processes: list[psutil.Process], *, force: bool = False,
                 elevated: bool = False, timeout: float = 3.0) -> tuple[int, str]:
    processes = pending_targets(processes)
    if not processes:
        return 0, ""
    command = ["taskkill", "/T"]
    for process in processes:
        command.extend(["/PID", str(process.pid)])
    if force:
        command.append("/F")
    if elevated:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "taskkill.exe", subprocess.list2cmdline(command[1:]), None, 0,
        )
        return (0, "") if result > 32 else (-1, "管理员授权失败或被取消")
    try:
        result = subprocess.run(
            command, capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=max(0.001, timeout),
        )
        return result.returncode, (result.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)
