from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass

from macroflow.core.resolution import SUPPORTED_SCALE_PERCENTS


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi
imm32 = ctypes.windll.imm32

# Explicit signatures are required on 64-bit Windows so HWND/HANDLE values are
# not truncated to the default c_int return type.
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.WPARAM]
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, wintypes.WPARAM]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.EnumDisplaySettingsW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
]
user32.EnumDisplaySettingsW.restype = wintypes.BOOL
user32.ChangeDisplaySettingsExW.argtypes = [
    wintypes.LPCWSTR, ctypes.c_void_p, wintypes.HWND, wintypes.DWORD, ctypes.c_void_p,
]
user32.ChangeDisplaySettingsExW.restype = wintypes.LONG
user32.GetDisplayConfigBufferSizes.argtypes = [
    wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
]
user32.GetDisplayConfigBufferSizes.restype = wintypes.LONG
user32.QueryDisplayConfig.argtypes = [
    wintypes.UINT, ctypes.POINTER(wintypes.UINT), ctypes.c_void_p,
    ctypes.POINTER(wintypes.UINT), ctypes.c_void_p, ctypes.c_void_p,
]
user32.QueryDisplayConfig.restype = wintypes.LONG
user32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.c_void_p]
user32.DisplayConfigGetDeviceInfo.restype = wintypes.LONG
user32.DisplayConfigSetDeviceInfo.argtypes = [ctypes.c_void_p]
user32.DisplayConfigSetDeviceInfo.restype = wintypes.LONG
user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
user32.LoadKeyboardLayoutW.restype = wintypes.HANDLE
user32.ActivateKeyboardLayout.argtypes = [wintypes.HANDLE, wintypes.UINT]
user32.ActivateKeyboardLayout.restype = wintypes.HANDLE
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetKeyboardLayout.restype = wintypes.HANDLE
imm32.ImmGetContext.argtypes = [wintypes.HWND]
imm32.ImmGetContext.restype = wintypes.HANDLE
imm32.ImmSetOpenStatus.argtypes = [wintypes.HANDLE, wintypes.BOOL]
imm32.ImmSetOpenStatus.restype = wintypes.BOOL
imm32.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
imm32.ImmReleaseContext.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetCurrentProcessId.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
# Identifies input packets generated by MacroFlow itself.  FocusInputGuard uses
# this marker so third-party mouse/keyboard drivers cannot bypass the lock just
# because Windows labels their packets as injected.
MACROFLOW_INPUT_TAG = 0x4D46574C
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

SW_RESTORE = 9
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
GA_ROOT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWM_WINDOW_CORNER_ROUND = 2
KLF_ACTIVATE = 0x00000001
WM_INPUTLANGCHANGEREQUEST = 0x0050
ENGLISH_US_LAYOUT = "00000409"
ENGLISH_US_LANGUAGE_ID = 0x0409


_input_dispatcher = None


def set_input_dispatcher(dispatcher) -> None:
    """Route SendInput packets through the system-input-lock owner thread."""
    global _input_dispatcher
    _input_dispatcher = dispatcher


ULONG_PTR = wintypes.WPARAM


def force_english_input(hwnd: int | None = None) -> bool:
    """Switch the target window to US English and close any active IME."""
    target = int(hwnd or user32.GetForegroundWindow() or 0)
    # 目标窗口线程已是英语（美国）布局时直接返回：反复按 F9 执行不再
    # 重复切换输入法，消除执行前的输入法切换开销。
    if target:
        target_thread = user32.GetWindowThreadProcessId(wintypes.HWND(target), None)
        if target_thread and (int(user32.GetKeyboardLayout(target_thread)) & 0xFFFF) == ENGLISH_US_LANGUAGE_ID:
            return True
    layout = user32.LoadKeyboardLayoutW(ENGLISH_US_LAYOUT, KLF_ACTIVATE)
    if not layout:
        return False
    layout_value = int(layout) if isinstance(layout, int) else int(layout.value)

    # Activate English for MacroFlow's thread and explicitly request the same
    # layout in the target/game thread, because Windows stores input languages
    # per GUI thread on many systems.
    user32.ActivateKeyboardLayout(layout, 0)
    if not target:
        return True

    handle = wintypes.HWND(target)
    for _ in range(2):
        user32.PostMessageW(handle, WM_INPUTLANGCHANGEREQUEST, 0, layout_value)
        context = imm32.ImmGetContext(handle)
        if context:
            try:
                imm32.ImmSetOpenStatus(context, False)
            finally:
                imm32.ImmReleaseContext(handle, context)
        time.sleep(0.025)

        thread_id = user32.GetWindowThreadProcessId(handle, None)
        active_layout = user32.GetKeyboardLayout(thread_id) if thread_id else 0
        if int(active_layout or 0) & 0xFFFF == ENGLISH_US_LANGUAGE_ID:
            return True
    # The request was still delivered even if the target does not expose its
    # active layout (some games hide or proxy the render window thread).
    return True


def _colorref(hex_color: str) -> int:
    value = hex_color.lstrip("#")
    red, green, blue = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return red | (green << 8) | (blue << 16)


def set_dark_titlebar(hwnd: int, background: str = "#0E1419", foreground: str = "#E8EDF2") -> bool:
    """Apply a native dark caption to a Tk top-level window on Windows 10/11."""
    if not hwnd:
        return False
    root_hwnd = user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT) or wintypes.HWND(hwnd)
    enabled = ctypes.c_int(1)
    result = dwmapi.DwmSetWindowAttribute(
        root_hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
        ctypes.byref(enabled), ctypes.sizeof(enabled),
    )
    caption = wintypes.DWORD(_colorref(background))
    text = wintypes.DWORD(_colorref(foreground))
    dwmapi.DwmSetWindowAttribute(root_hwnd, DWMWA_CAPTION_COLOR, ctypes.byref(caption), ctypes.sizeof(caption))
    dwmapi.DwmSetWindowAttribute(root_hwnd, DWMWA_TEXT_COLOR, ctypes.byref(text), ctypes.sizeof(text))
    corners = ctypes.c_int(DWM_WINDOW_CORNER_ROUND)
    # Windows 11 ignores this attribute on unsupported builds; the dark title
    # bar and normal square frame remain the safe fallback on older Windows.
    dwmapi.DwmSetWindowAttribute(
        root_hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
        ctypes.byref(corners), ctypes.sizeof(corners),
    )
    return result == 0


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    class_name: str
    process_path: str = ""
    window_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    client_size: tuple[int, int] = (0, 0)

    @property
    def label(self) -> str:
        return f"{self.title}  ·  {self.class_name}"


def get_process_image_path(process_id: int) -> str:
    if not process_id:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(process_id))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
    finally:
        kernel32.CloseHandle(handle)
    return ""


def get_window_info(hwnd: int | None) -> WindowInfo | None:
    if not hwnd or not is_window(hwnd):
        return None
    handle = wintypes.HWND(int(hwnd))
    length = user32.GetWindowTextLengthW(handle)
    title_buf = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(handle, title_buf, len(title_buf))
    class_buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(handle, class_buf, len(class_buf))
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
    process_path = get_process_image_path(int(process_id.value))
    window_rect = wintypes.RECT()
    if user32.GetWindowRect(handle, ctypes.byref(window_rect)):
        rect = (int(window_rect.left), int(window_rect.top),
                int(window_rect.right - window_rect.left), int(window_rect.bottom - window_rect.top))
    else:
        rect = (0, 0, 0, 0)
    client_rect = wintypes.RECT()
    if user32.GetClientRect(handle, ctypes.byref(client_rect)):
        client_size = (int(client_rect.right - client_rect.left), int(client_rect.bottom - client_rect.top))
    else:
        client_size = (0, 0)
    return WindowInfo(int(hwnd), title_buf.value, class_buf.value, process_path, rect, client_size)


def window_from_point(x: int, y: int) -> WindowInfo | None:
    point = wintypes.POINT(int(x), int(y))
    hwnd = user32.WindowFromPoint(point)
    if hwnd:
        root = user32.GetAncestor(hwnd, GA_ROOT)
        if root:
            hwnd = root
    return get_window_info(int(hwnd)) if hwnd else None


def get_foreground_window_info() -> WindowInfo | None:
    hwnd = int(user32.GetForegroundWindow() or 0)
    return get_window_info(hwnd)


def enum_windows() -> list[WindowInfo]:
    result: list[WindowInfo] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        info = get_window_info(int(hwnd))
        if not info or not info.title:
            return True
        result.append(info)
        return True

    user32.EnumWindows(callback, 0)
    return sorted(result, key=lambda item: item.title.lower())


def resolve_window_signature(signature: dict | None) -> WindowInfo | None:
    """按保存的窗口签名（title/class_name/process_path）在当前窗口表里找回窗口。

    窗口重启/重建后句柄会变化，标题可能附带会话状态；先用三项全匹配
    （标题权重最低，防止标题近似但程序不同的窗口误配），再退化为
    “类名 + 进程路径”稳定匹配。窗口未打开或签名无效时返回 None。
    """
    signature = signature or {}
    title = str(signature.get("title", "")).strip()
    class_name = str(signature.get("class_name", "")).strip()
    process_path = os.path.normcase(str(signature.get("process_path", "")).strip()).casefold()
    if not title and not class_name and not process_path:
        return None

    candidates = enum_windows()

    def score(item) -> int:
        item_path = os.path.normcase(str(item.process_path or "")).casefold()
        value = 0
        if title and item.title == title:
            value += 4
        if class_name and item.class_name == class_name:
            value += 3
        if process_path and item_path == process_path:
            value += 5
        return value

    required = (4 if title else 0) + (3 if class_name else 0) + (5 if process_path else 0)
    for item in candidates:
        if score(item) == required:
            return item
    # 窗口标题可能包含会话状态；类名 + 程序路径仍能稳定找回重启后的窗口。
    for item in candidates:
        if (class_name or process_path) \
                and (not class_name or item.class_name == class_name) \
                and (not process_path or os.path.normcase(str(item.process_path or "")).casefold() == process_path):
            return item
    return None


def is_window(hwnd: int | None) -> bool:
    return bool(hwnd and user32.IsWindow(wintypes.HWND(hwnd)))


def is_window_process_foreground(hwnd: int | None) -> bool:
    """Accept focused child/render windows that belong to the target process."""
    if not hwnd or not is_window(hwnd):
        return False
    foreground = user32.GetForegroundWindow()
    if not foreground:
        return False
    if int(foreground) == int(hwnd):
        return True
    target_process_id = wintypes.DWORD()
    foreground_process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(target_process_id))
    user32.GetWindowThreadProcessId(foreground, ctypes.byref(foreground_process_id))
    return bool(target_process_id.value and target_process_id.value == foreground_process_id.value)


def is_current_process_window(hwnd: int | None) -> bool:
    """Return whether a live window belongs to this running application."""
    if not hwnd or not is_window(hwnd):
        return False
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(process_id))
    return bool(process_id.value == kernel32.GetCurrentProcessId())


def is_cursor_near_window_center(hwnd: int | None, tolerance: int = 8) -> bool:
    """Detect the stable center cursor used by first-person camera controls."""
    center_hwnd = int(hwnd) if hwnd else 0
    foreground = user32.GetForegroundWindow()
    if foreground and is_window_process_foreground(hwnd):
        # Launchers and render surfaces are often separate top-level windows in
        # the same game process. The focused render window owns the real center.
        center_hwnd = int(foreground)
    rect = get_window_rect(center_hwnd) if center_hwnd else None
    if not rect:
        return False
    x, y = get_cursor_pos()
    left, top, width, height = rect
    center_x = left + width // 2
    center_y = top + height // 2
    return abs(x - center_x) <= tolerance and abs(y - center_y) <= tolerance


def activate_window(hwnd: int) -> bool:
    if not is_window(hwnd):
        return False
    # SW_RESTORE also restores normal/maximized/full-screen windows to their
    # previous size. Only use it for an actually minimized window; foreground
    # activation must otherwise leave the user's window geometry untouched.
    if user32.IsIconic(wintypes.HWND(hwnd)):
        user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
    current_thread = kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), None)
    attached = False
    if target_thread and target_thread != current_thread:
        attached = bool(user32.AttachThreadInput(current_thread, target_thread, True))
    try:
        user32.BringWindowToTop(wintypes.HWND(hwnd))
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
        # SetFocus 会把键盘焦点从窗口内部的子渲染表面（Flash/CEF 画布）
        # 夺走，游戏客户端随即弹出“点击游戏画面继续操作”的失焦遮罩；
        # 窗口每次播放启动都会被激活，因此这里绝不能无条件 SetFocus。
        # 仅当 SetForegroundWindow 没有把窗口带到前台（激活失败）时才
        # 补 SetFocus 强制转移焦点；窗口本就在前台时保持焦点不动。
        if int(user32.GetForegroundWindow()) != int(hwnd):
            user32.SetFocus(wintypes.HWND(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(current_thread, target_thread, False)
    time.sleep(0.08)
    return int(user32.GetForegroundWindow()) == int(hwnd)


def show_window(hwnd: int) -> bool:
    """Make a window visible without changing a normal window's geometry."""
    if not is_window(hwnd):
        return False
    handle = wintypes.HWND(hwnd)
    command = SW_RESTORE if user32.IsIconic(handle) else SW_SHOW
    user32.ShowWindow(handle, command)
    return bool(user32.IsWindowVisible(handle))


def _send_input_direct(input_obj: INPUT) -> None:
    # SendInput can transiently return zero without setting LastError (which
    # previously surfaced as the contradictory "[WinError 0] 操作成功完成").
    # Retry first, then use the older event API for ordinary key/mouse packets.
    for attempt in range(3):
        ctypes.set_last_error(0)
        sent = user32.SendInput(1, ctypes.byref(input_obj), ctypes.sizeof(INPUT))
        if sent == 1:
            return
        if attempt < 2:
            time.sleep(0.01)
    if input_obj.type == INPUT_MOUSE:
        mouse = input_obj.mi
        user32.mouse_event(mouse.dwFlags, mouse.dx, mouse.dy, mouse.mouseData, mouse.dwExtraInfo)
        return
    if input_obj.type == INPUT_KEYBOARD and not (input_obj.ki.dwFlags & KEYEVENTF_UNICODE):
        key = input_obj.ki
        user32.keybd_event(key.wVk, key.wScan, key.dwFlags, key.dwExtraInfo)
        return
    raise RuntimeError("系统拒绝模拟输入，请确认目标程序与 MacroFlow 使用相同或更低的权限运行。")


def _send_input(input_obj: INPUT) -> None:
    dispatcher = _input_dispatcher
    if dispatcher is not None:
        dispatcher(input_obj)
        return
    _send_input_direct(input_obj)


def send_key(vk: int, down: bool, scan: int = 0, extended: bool = False) -> None:
    flags = 0 if down else KEYEVENTF_KEYUP
    if scan:
        flags |= KEYEVENTF_SCANCODE
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(0 if scan else vk, scan, flags, 0, MACROFLOW_INPUT_TAG)
    _send_input(inp)


def send_text(text: str, char_delay_ms: int = 10) -> None:
    units = text.encode("utf-16-le", errors="surrogatepass")
    for index in range(0, len(units), 2):
        codepoint = units[index] | (units[index + 1] << 8)
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.ki = KEYBDINPUT(0, codepoint, flags, 0, MACROFLOW_INPUT_TAG)
            _send_input(inp)
        if char_delay_ms:
            time.sleep(char_delay_ms / 1000)


def send_move_absolute(x: int, y: int) -> None:
    left = user32.GetSystemMetrics(76)
    top = user32.GetSystemMetrics(77)
    width = max(1, user32.GetSystemMetrics(78) - 1)
    height = max(1, user32.GetSystemMetrics(79) - 1)
    nx = round((x - left) * 65535 / width)
    ny = round((y - top) * 65535 / height)
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(
        nx, ny, 0,
        MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
        0, MACROFLOW_INPUT_TAG,
    )
    _send_input(inp)


def send_move_relative(dx: int, dy: int) -> None:
    """Replay game deltas exactly as the working v1.2.1 implementation did."""
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(int(dx), int(dy), 0, MOUSEEVENTF_MOVE, 0, MACROFLOW_INPUT_TAG)
    _send_input(inp)


def _mouse_flags(button: str, down: bool) -> int:
    table = {
        ("left", True): MOUSEEVENTF_LEFTDOWN, ("left", False): MOUSEEVENTF_LEFTUP,
        ("right", True): MOUSEEVENTF_RIGHTDOWN, ("right", False): MOUSEEVENTF_RIGHTUP,
        ("middle", True): MOUSEEVENTF_MIDDLEDOWN, ("middle", False): MOUSEEVENTF_MIDDLEUP,
    }
    return table[(button, down)]


def send_button(button: str, down: bool) -> None:
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(0, 0, 0, _mouse_flags(button, down), 0, MACROFLOW_INPUT_TAG)
    _send_input(inp)


def send_scroll(dx: int, dy: int) -> None:
    if dy:
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(
            0, 0, ctypes.c_ulong(dy * 120).value, MOUSEEVENTF_WHEEL,
            0, MACROFLOW_INPUT_TAG,
        )
        _send_input(inp)
    if dx:
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(
            0, 0, ctypes.c_ulong(dx * 120).value, MOUSEEVENTF_HWHEEL,
            0, MACROFLOW_INPUT_TAG,
        )
        _send_input(inp)


def get_cursor_pos() -> tuple[int, int]:
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def set_cursor_pos(x: int, y: int) -> bool:
    """Move the cursor directly to a desktop position (no injected event)."""
    return bool(user32.SetCursorPos(int(x), int(y)))


def get_virtual_screen_rect() -> dict[str, int]:
    """Return the complete desktop coordinate space, including extra monitors."""
    return {
        "left": int(user32.GetSystemMetrics(76)),
        "top": int(user32.GetSystemMetrics(77)),
        "width": max(1, int(user32.GetSystemMetrics(78))),
        "height": max(1, int(user32.GetSystemMetrics(79))),
    }


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
user32.SetWindowRgn.restype = ctypes.c_int
gdi32 = ctypes.windll.gdi32
gdi32.CreateRoundRectRgn.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
]
gdi32.CreateRoundRectRgn.restype = wintypes.HANDLE

user32.GetMonitorInfoW.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(_MONITORINFO),
]
user32.GetMonitorInfoW.restype = wintypes.BOOL


DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DM_DISPLAYFREQUENCY = 0x00400000
CDS_UPDATEREGISTRY = 0x00000001
DISP_CHANGE_SUCCESSFUL = 0
ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
QDC_ONLY_ACTIVE_PATHS = 0x00000002
DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME = 1
DISPLAYCONFIG_DEVICE_INFO_GET_DPI_SCALE = -3
DISPLAYCONFIG_DEVICE_INFO_SET_DPI_SCALE = -4
ERROR_SUCCESS = 0


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _DISPLAYCONFIG_DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int32),
        ("size", wintypes.UINT),
        ("adapterId", _LUID),
        ("id", wintypes.UINT),
    ]


class _DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", _LUID),
        ("id", wintypes.UINT),
        ("modeInfoIdx", wintypes.UINT),
        ("statusFlags", wintypes.UINT),
    ]


class _DISPLAYCONFIG_RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", wintypes.UINT), ("Denominator", wintypes.UINT)]


class _DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", _LUID),
        ("id", wintypes.UINT),
        ("modeInfoIdx", wintypes.UINT),
        ("outputTechnology", ctypes.c_int32),
        ("rotation", wintypes.UINT),
        ("scaling", wintypes.UINT),
        ("refreshRate", _DISPLAYCONFIG_RATIONAL),
        ("scanLineOrdering", ctypes.c_int32),
        ("targetAvailable", wintypes.BOOL),
        ("statusFlags", wintypes.UINT),
    ]


class _DISPLAYCONFIG_PATH_INFO(ctypes.Structure):
    _fields_ = [
        ("sourceInfo", _DISPLAYCONFIG_PATH_SOURCE_INFO),
        ("targetInfo", _DISPLAYCONFIG_PATH_TARGET_INFO),
        ("flags", wintypes.UINT),
    ]


class _DISPLAYCONFIG_SOURCE_DEVICE_NAME(ctypes.Structure):
    _fields_ = [
        ("header", _DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("viewGdiDeviceName", wintypes.WCHAR * 32),
    ]


class _DISPLAYCONFIG_SOURCE_DPI_SCALE_GET(ctypes.Structure):
    _fields_ = [
        ("header", _DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("minScaleRel", ctypes.c_int32),
        ("curScaleRel", ctypes.c_int32),
        ("maxScaleRel", ctypes.c_int32),
    ]


class _DISPLAYCONFIG_SOURCE_DPI_SCALE_SET(ctypes.Structure):
    _fields_ = [
        ("header", _DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("scaleRel", ctypes.c_int32),
    ]


class _DEVMODE_DISPLAY_FIELDS(ctypes.Structure):
    _fields_ = [
        ("dmPosition", wintypes.POINT),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
    ]


class _DEVMODE_DISPLAY_UNION(ctypes.Union):
    _fields_ = [
        ("printer_fields", wintypes.SHORT * 8),
        ("dmPosition", wintypes.POINT),
        ("display_fields", _DEVMODE_DISPLAY_FIELDS),
    ]


class _DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmUnion", _DEVMODE_DISPLAY_UNION),
        ("dmColor", wintypes.SHORT),
        ("dmDuplex", wintypes.SHORT),
        ("dmYResolution", wintypes.SHORT),
        ("dmTTOption", wintypes.SHORT),
        ("dmCollate", wintypes.SHORT),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


def set_rounded_window(hwnd: int | None, radius: int = 10) -> bool:
    """Clip a window to a rounded rectangle.

    DWM 只对带标准边框的顶层窗口画圆角；无边框的悬浮小窗/提醒条用窗口区域
    自己做圆角。区域由系统接管，成功时不要 DeleteObject。
    """
    if not hwnd:
        return False
    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(int(hwnd)), ctypes.byref(rect)):
        return False
    width = max(1, int(rect.right - rect.left))
    height = max(1, int(rect.bottom - rect.top))
    diameter = max(2, int(radius) * 2)
    region = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, diameter, diameter)
    if not region:
        return False
    if not user32.SetWindowRgn(wintypes.HWND(int(hwnd)), region, True):
        gdi32.DeleteObject(region)
        return False
    return True


def get_window_dpi(hwnd: int | None) -> int:
    """DPI of the monitor the window currently sits on (per-monitor aware)."""
    if hwnd:
        try:
            dpi = int(user32.GetDpiForWindow(wintypes.HWND(int(hwnd))))
        except (AttributeError, OSError, TypeError, ValueError):
            dpi = 0
        if dpi > 0:
            return dpi
    try:
        return int(user32.GetDpiForSystem()) or 96
    except (AttributeError, OSError):
        return 96


def get_primary_screen_rect() -> dict[str, int]:
    """Return the primary monitor rectangle, excluding other monitors."""
    return {
        "left": 0,
        "top": 0,
        "width": max(1, int(user32.GetSystemMetrics(0))),
        "height": max(1, int(user32.GetSystemMetrics(1))),
    }


def _monitor_info_for_window(hwnd: int | None) -> _MONITORINFO | None:
    if not hwnd:
        return None
    monitor = user32.MonitorFromWindow(wintypes.HWND(int(hwnd)), 2)
    if not monitor:
        return None
    info = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    return info


def get_display_device_name_for_window(hwnd: int | None) -> str | None:
    """Return the Windows display-device name for the monitor of ``hwnd``."""
    info = _monitor_info_for_window(hwnd)
    return str(info.szDevice).strip() if info and info.szDevice else None


def set_display_resolution_for_window(hwnd: int | None, width: int, height: int,
                                      refresh_rate: int = 0) -> bool:
    """Change only the monitor containing ``hwnd`` to the requested mode."""
    device_name = get_display_device_name_for_window(hwnd)
    if not device_name:
        return False
    try:
        width = int(width)
        height = int(height)
        refresh_rate = int(refresh_rate or 0)
    except (TypeError, ValueError):
        return False
    if width <= 0 or height <= 0 or refresh_rate < 0:
        return False

    mode = _DEVMODEW()
    mode.dmSize = ctypes.sizeof(_DEVMODEW)
    if not user32.EnumDisplaySettingsW(
        device_name, ENUM_CURRENT_SETTINGS, ctypes.byref(mode),
    ):
        return False
    mode.dmPelsWidth = width
    mode.dmPelsHeight = height
    mode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
    if refresh_rate:
        mode.dmDisplayFrequency = refresh_rate
        mode.dmFields |= DM_DISPLAYFREQUENCY
    result = user32.ChangeDisplaySettingsExW(
        device_name, ctypes.byref(mode), None, CDS_UPDATEREGISTRY, None,
    )
    return int(result) == DISP_CHANGE_SUCCESSFUL


def _display_config_source_for_device(device_name: str) -> tuple[_LUID, int] | None:
    """Find the CCD source identified by a GDI device name such as DISPLAY2."""
    path_count = wintypes.UINT()
    mode_count = wintypes.UINT()
    if user32.GetDisplayConfigBufferSizes(
            QDC_ONLY_ACTIVE_PATHS, ctypes.byref(path_count), ctypes.byref(mode_count)) != ERROR_SUCCESS:
        return None
    paths = (_DISPLAYCONFIG_PATH_INFO * max(1, int(path_count.value)))()
    # DISPLAYCONFIG_MODE_INFO is 64 bytes on supported Windows versions.  The
    # mode data is not inspected here, but QueryDisplayConfig still requires a
    # correctly sized output buffer.
    modes = ctypes.create_string_buffer(max(1, int(mode_count.value)) * 64)
    if user32.QueryDisplayConfig(
            QDC_ONLY_ACTIVE_PATHS,
            ctypes.byref(path_count), ctypes.byref(paths),
            ctypes.byref(mode_count), ctypes.byref(modes), None) != ERROR_SUCCESS:
        return None
    for path in paths[:int(path_count.value)]:
        packet = _DISPLAYCONFIG_SOURCE_DEVICE_NAME()
        packet.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME
        packet.header.size = ctypes.sizeof(packet)
        packet.header.adapterId = path.sourceInfo.adapterId
        packet.header.id = path.sourceInfo.id
        if user32.DisplayConfigGetDeviceInfo(ctypes.byref(packet.header)) == ERROR_SUCCESS \
                and str(packet.viewGdiDeviceName).casefold() == str(device_name).casefold():
            return path.sourceInfo.adapterId, int(path.sourceInfo.id)
    return None


def _display_scale_info(adapter_id: _LUID, source_id: int) -> tuple[int, int, int, int] | None:
    packet = _DISPLAYCONFIG_SOURCE_DPI_SCALE_GET()
    packet.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_DPI_SCALE
    packet.header.size = ctypes.sizeof(packet)
    packet.header.adapterId = adapter_id
    packet.header.id = int(source_id)
    if user32.DisplayConfigGetDeviceInfo(ctypes.byref(packet.header)) != ERROR_SUCCESS:
        return None
    min_index = max(0, -int(packet.minScaleRel))
    minimum_index = min_index + int(packet.minScaleRel)
    current_index = min_index + int(packet.curScaleRel)
    max_index = min_index + int(packet.maxScaleRel)
    if not 0 <= minimum_index <= min_index < len(SUPPORTED_SCALE_PERCENTS) \
            or not min_index <= current_index <= max_index \
            or max_index >= len(SUPPORTED_SCALE_PERCENTS):
        return None
    return (
        SUPPORTED_SCALE_PERCENTS[current_index],
        SUPPORTED_SCALE_PERCENTS[minimum_index],
        SUPPORTED_SCALE_PERCENTS[min_index],
        SUPPORTED_SCALE_PERCENTS[max_index],
    )


def get_display_resolution_for_window(hwnd: int | None) -> tuple[int, int, int] | None:
    """当前显示器分辨率与刷新率 (width, height, hz)；取不到返回 None。"""
    device_name = get_display_device_name_for_window(hwnd)
    if not device_name:
        return None
    mode = _DEVMODEW()
    mode.dmSize = ctypes.sizeof(_DEVMODEW)
    if not user32.EnumDisplaySettingsW(
            device_name, ENUM_CURRENT_SETTINGS, ctypes.byref(mode)):
        return None
    return int(mode.dmPelsWidth), int(mode.dmPelsHeight), int(mode.dmDisplayFrequency)


def get_display_scaling_for_window(hwnd: int | None) -> int | None:
    """当前显示器缩放百分比；CCD 不支持时按窗口 DPI 反推。"""
    device_name = get_display_device_name_for_window(hwnd)
    if device_name:
        source = _display_config_source_for_device(device_name)
        if source is not None:
            info = _display_scale_info(*source)
            if info is not None:
                return info[0]
    dpi = get_window_dpi(hwnd)
    if not dpi:
        return None
    percent = round(dpi / 96.0 * 100)
    return min(SUPPORTED_SCALE_PERCENTS, key=lambda value: abs(value - percent))


def set_display_scaling_for_window(hwnd: int | None, scale_percent: int = 100) -> bool:
    """Set the Windows per-monitor display scale for the monitor of ``hwnd``."""
    device_name = get_display_device_name_for_window(hwnd)
    if not device_name:
        return False
    try:
        scale_percent = int(scale_percent)
    except (TypeError, ValueError):
        return False
    if scale_percent not in SUPPORTED_SCALE_PERCENTS:
        return False
    source = _display_config_source_for_device(device_name)
    if source is None:
        return False
    adapter_id, source_id = source
    scale_info = _display_scale_info(adapter_id, source_id)
    if scale_info is None:
        return False
    _current, minimum, recommended, maximum = scale_info
    if not minimum <= scale_percent <= maximum:
        return False
    recommended_index = SUPPORTED_SCALE_PERCENTS.index(recommended)
    desired_index = SUPPORTED_SCALE_PERCENTS.index(scale_percent)
    packet = _DISPLAYCONFIG_SOURCE_DPI_SCALE_SET()
    packet.header.type = DISPLAYCONFIG_DEVICE_INFO_SET_DPI_SCALE
    packet.header.size = ctypes.sizeof(packet)
    packet.header.adapterId = adapter_id
    packet.header.id = source_id
    packet.scaleRel = desired_index - recommended_index
    return user32.DisplayConfigSetDeviceInfo(ctypes.byref(packet.header)) == ERROR_SUCCESS


user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE


def _work_area_of(monitor) -> dict[str, int] | None:
    if not monitor:
        return None
    info = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    rect = info.rcWork
    return {
        "left": int(rect.left),
        "top": int(rect.top),
        "width": max(1, int(rect.right - rect.left)),
        "height": max(1, int(rect.bottom - rect.top)),
    }


def get_monitor_work_area_for_window(hwnd: int | None) -> dict[str, int] | None:
    """显示器可用区域（排除任务栏），窗口所在屏；坐标是物理像素，可为负。"""
    if not hwnd:
        return None
    return _work_area_of(user32.MonitorFromWindow(wintypes.HWND(int(hwnd)), 2))


def get_monitor_work_area_for_point(x: int, y: int) -> dict[str, int] | None:
    """包含该点的那块显示器的可用区域。"""
    return _work_area_of(user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2))


def get_monitor_rect_for_window(hwnd: int | None) -> dict[str, int] | None:
    """Return the physical desktop rectangle of the monitor containing ``hwnd``."""
    info = _monitor_info_for_window(hwnd)
    if info is None:
        return None
    rect = info.rcMonitor
    return {
        "left": int(rect.left),
        "top": int(rect.top),
        "width": max(1, int(rect.right - rect.left)),
        "height": max(1, int(rect.bottom - rect.top)),
    }


def get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not is_window(hwnd) or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
def make_window_no_activate(hwnd: int) -> bool:
    """Keep a status overlay visible without taking focus from a game."""
    if not hwnd:
        return False
    handle = wintypes.HWND(hwnd)
    ex_style = user32.GetWindowLongW(handle, GWL_EXSTYLE)
    user32.SetWindowLongW(handle, GWL_EXSTYLE, ex_style | WS_EX_NOACTIVATE)
    return bool(user32.SetWindowPos(
        handle, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    ))


def show_window_no_activate(hwnd: int) -> bool:
    """Show a hidden window at its existing geometry without taking focus."""
    if not is_window(hwnd):
        return False
    handle = wintypes.HWND(hwnd)
    user32.ShowWindow(handle, SW_SHOWNOACTIVATE)
    return bool(user32.SetWindowPos(
        handle, wintypes.HWND(0), 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
    ))
