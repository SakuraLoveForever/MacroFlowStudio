from __future__ import annotations

import ctypes
from macroflow.input.wininput import (
    activate_window, get_cursor_pos, get_foreground_window_info,
    get_display_resolution_for_window, get_display_scaling_for_window,
    get_monitor_rect_for_window, get_primary_screen_rect, get_virtual_screen_rect,
    get_window_rect, is_window,
    is_window_process_foreground, resolve_window_signature, send_button, send_key,
    send_move_absolute, send_move_relative, send_scroll,
    set_display_resolution_for_window, set_display_scaling_for_window,
    send_text, set_cursor_pos,
)
import subprocess
from ctypes import wintypes

JUMP_CURRENT_SCRIPT_LAST_RESULT = "jump_current_script_last"
INPUT_ACTION_KINDS = frozenset({
    "key", "key_press", "text",
    "mouse_button", "mouse_move", "click", "repeat_click", "scroll", "turn",
})
MAX_SCRIPT_REF_DEPTH = 16
CAPTURE_FAILURE_GRACE_S = 30.0
CLICK_DEDUP_WINDOW_S = 0.3
CLICK_DEDUP_RADIUS_PX = 12
CLICK_SOURCE_MODULE = "模块"
CLICK_SOURCE_GUARD = "全局检测守卫"
CLICK_SOURCE_ACTION = "脚本动作"
GUARD_SETTLE_MS = 1000
def scale_screen_point(x: int, y: int, source: dict | None,
                       target: dict | None) -> tuple[int, int]:
    """Scale an absolute desktop point from its recorded screen to this PC."""
    if not source or not target:
        return int(x), int(y)
    try:
        source_width, source_height = int(source.get("width", 0)), int(source.get("height", 0))
        target_width, target_height = int(target.get("width", 0)), int(target.get("height", 0))
    except (TypeError, ValueError):
        return int(x), int(y)
    if min(source_width, source_height, target_width, target_height) <= 0:
        return int(x), int(y)
    source_left, source_top = int(source.get("left", 0)), int(source.get("top", 0))
    target_left, target_top = int(target.get("left", 0)), int(target.get("top", 0))
    scaled_x = target_left + round((int(x) - source_left) * target_width / source_width)
    scaled_y = target_top + round((int(y) - source_top) * target_height / source_height)
    return scaled_x, scaled_y
def screen_template_scale(source: dict | None, target: dict | None) -> float:
    """模板缩放系数：执行机屏幕宽度 / 录制机屏幕宽度。

    截图尺寸不同（多显示器虚拟屏幕、分辨率/DPI 差异）时，目标在截图里的
    像素大小与录制时不同，固定尺寸的模板匹配度会下降；把模板按该系数
    等比缩放到当前尺寸再匹配即可恢复。尺寸相同或不可用时返回 1.0。
    """
    if not source or not target:
        return 1.0
    try:
        source_width = int(source.get("width", 0))
        target_width = int(target.get("width", 0))
    except (TypeError, ValueError):
        return 1.0
    if source_width <= 0 or target_width <= 0 or source_width == target_width:
        return 1.0
    return target_width / source_width
def get_playback_screen_rect(hwnd: int | None) -> dict[str, int]:
    """Use the target window's monitor instead of the combined virtual desktop."""
    return get_monitor_rect_for_window(hwnd) or get_primary_screen_rect()
TH32CS_SNAPPROCESS = 0x00000002
class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * wintypes.MAX_PATH),
    ]
def running_process_names() -> list[str]:
    """Return every running image name (lowercase, unique, sorted)."""
    kernel32 = ctypes.windll.kernel32
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return []
        names: set[str] = set()
        while True:
            if entry.szExeFile:
                names.add(entry.szExeFile.lower())
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return sorted(names)
    finally:
        kernel32.CloseHandle(snapshot)
def is_process_running(image_name: str) -> bool:
    image_name = image_name.strip().lower()
    return bool(image_name) and image_name in running_process_names()
def taskkill_process(image_name: str, force: bool = False, tree: bool = False) -> tuple[int, str]:
    """End a process via taskkill; returns (returncode, stderr).

    returncode 0 means the request was accepted; /T ends the whole process tree.
    """
    cmd = ["taskkill", "/IM", image_name]
    if force:
        cmd.append("/F")
    if tree:
        cmd.append("/T")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=15,
        )
        return proc.returncode, (proc.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)
def elevated_taskkill(image_name: str, tree: bool = False) -> bool:
    """Kill via an elevated taskkill (UAC prompt).

    True when the elevated taskkill process was started; the user may still
    decline the prompt or the kill may fail afterwards.
    """
    cmd = f'taskkill /IM "{image_name}" /F'
    if tree:
        cmd += " /T"
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", "taskkill.exe", cmd, None, 0,
    )
    return result > 32
