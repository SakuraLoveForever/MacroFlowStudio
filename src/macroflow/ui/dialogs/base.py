from __future__ import annotations

from macroflow.input.wininput import (
    WindowInfo, enum_windows, get_cursor_pos,
    get_monitor_work_area_for_point,
    get_monitor_work_area_for_window, get_primary_screen_rect,
    get_virtual_screen_rect, is_current_process_window, make_window_no_activate,
    set_dark_titlebar, set_rounded_window, show_window_no_activate,
    window_from_point,
)
import ctypes
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


COLOR_BG = "#0E1419"


COLOR_SURFACE = "#182129"


COLOR_SURFACE_ALT = "#1D2831"


COLOR_BORDER = "#2D3943"


COLOR_TEXT = "#E8EDF2"


COLOR_MUTED = "#94A1AD"


COLOR_BLUE_SELECTION = "#244D78"


FONT_FAMILY = "Microsoft YaHei UI"


FONT_MONO = "Consolas"


FONT_SMALL = 8


FONT_BODY = 9


FONT_SUBTITLE = 10


FONT_TITLE = 12


_UI_SCALE = 1.0


def set_ui_scale(root) -> float:
    """记录 Tk 的 DPI 缩放系数（1.0 = 96 DPI / 100%）。"""
    global _UI_SCALE
    try:
        scaling = float(root.tk.call("tk", "scaling"))
    except (AttributeError, tk.TclError, ValueError):
        scaling = 96.0 / 72.0
    _UI_SCALE = max(1.0, scaling / (96.0 / 72.0))
    return _UI_SCALE


def px(value) -> int:
    """把按 96 DPI 设计的像素值换算到当前 DPI（0 仍为 0）。"""
    number = float(value)
    if number == 0:
        return 0
    return max(1, int(round(number * _UI_SCALE)))


def pad(*values) -> tuple[int, ...]:
    """换算一组像素值（padding/padx/pady）。"""
    return tuple(px(value) for value in values)


DIALOG_FRAME_MARGIN = 40


def monitor_work_area_for(widget) -> dict[str, int]:
    """widget（通常是父窗口）所在显示器的可用区域（桌面像素，可为负）。"""
    try:
        area = get_monitor_work_area_for_point(
            int(widget.winfo_rootx()) + max(1, int(widget.winfo_width())) // 2,
            int(widget.winfo_rooty()) + max(1, int(widget.winfo_height())) // 2,
        )
    except (AttributeError, tk.TclError, TypeError, ValueError):
        area = None
    if area:
        return area
    try:
        hwnd = int(widget.winfo_id())
    except (AttributeError, tk.TclError, TypeError, ValueError):
        hwnd = 0
    return (
        (get_monitor_work_area_for_window(hwnd) if hwnd else None)
        or get_primary_screen_rect()
    )


def clamp_to_work_area(area, width: int, height: int, x: int, y: int) -> tuple[int, int]:
    """把窗口左上角收进显示器可用区域（副屏坐标可以为负，不能用 max(0, …)）。"""
    right = int(area["left"]) + max(0, int(area["width"]) - int(width) - px(DIALOG_FRAME_MARGIN))
    bottom = int(area["top"]) + max(0, int(area["height"]) - int(height) - px(DIALOG_FRAME_MARGIN))
    return (
        max(int(area["left"]), min(int(x), right)),
        max(int(area["top"]), min(int(y), bottom)),
    )


GLOBAL_SCRIPT_END_LABEL = "脚本结束（结束当前执行）"


SCRIPT_START_LABEL = "脚本开头（从第 1 行开始）"


SCRIPT_END_LABEL = "脚本结尾（结束当前执行）"


SCRIPT_CATEGORY_LABELS = {
    "all": "全部", "level": "关卡", "level_pack": "关卡封装",
    "switch": "切换", "direction": "方向",
}


TIME_UNITS = ("ms", "s", "min")


_UNIT_TO_MS = {"ms": 1, "s": 1000, "min": 60000}


DIALOG_SPACING = (3, 6, 10, 14, 20)


DIALOG_FIELD_WIDTH = 10


DIALOG_BUTTON_WIDTH = 10


DIALOG_PRIMARY_STYLE = "primary-outline"


DIALOG_SECONDARY_STYLE = "secondary-outline"


class DurationVar(tk.StringVar):
    """Display ms/seconds/minutes, while Python callers always receive milliseconds."""

    def __init__(self, value=0, master=None):
        super().__init__(master=master, value=str(value))
        self.unit = tk.StringVar(master=master, value="ms")
        self._last_unit = "ms"
        self.unit.trace_add("write", self._unit_changed)

    def _raw(self) -> str:
        return str(self._tk.globalgetvar(self._name))

    def _unit_changed(self, *_args):
        new_unit = str(self.unit.get())
        if new_unit == self._last_unit:
            return
        try:
            value = float(self._raw())
            converted = value * _UNIT_TO_MS[self._last_unit] / _UNIT_TO_MS[new_unit]
            super().set(str(int(converted)) if converted.is_integer() else f"{converted:g}")
        except ValueError:
            pass
        self._last_unit = new_unit

    def get(self) -> str:
        value = float(self._raw())
        milliseconds = round(value * _UNIT_TO_MS[self.unit.get()])
        return str(milliseconds)


def duration_var(value=0) -> DurationVar:
    return DurationVar(value)


VK_NAMES = {
    "BACKSPACE": 0x08, "TAB": 0x09, "ENTER": 0x0D, "SHIFT": 0x10,
    "CTRL": 0x11, "ALT": 0x12, "PAUSE": 0x13, "CAPSLOCK": 0x14,
    "ESC": 0x1B, "SPACE": 0x20, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "END": 0x23, "HOME": 0x24, "LEFT": 0x25, "UP": 0x26,
    "RIGHT": 0x27, "DOWN": 0x28, "INSERT": 0x2D, "DELETE": 0x2E,
    "LWIN": 0x5B, "RWIN": 0x5C, "NUMLOCK": 0x90, "SCROLLLOCK": 0x91,
}
for _i in range(1, 25):
    VK_NAMES[f"F{_i}"] = 0x6F + _i


def dark_checkbutton(parent, text: str, variable, command=None):
    """Native dark checkbox that does not depend on ttkbootstrap icon fonts."""
    return tk.Checkbutton(
        parent, text=text, variable=variable, command=command,
        background=COLOR_BG, foreground=COLOR_TEXT,
        activebackground=COLOR_BG, activeforeground=COLOR_TEXT,
        selectcolor=COLOR_SURFACE, highlightthickness=0, borderwidth=0,
        font=(FONT_FAMILY, FONT_BODY), cursor="hand2",
    )


def selectable_target_windows(windows: list[WindowInfo]) -> list[WindowInfo]:
    """Remove MacroFlow's own windows and duplicate handles from the picker."""
    result: list[WindowInfo] = []
    seen: set[int] = set()
    for item in windows:
        if item.hwnd in seen or is_current_process_window(item.hwnd):
            continue
        seen.add(item.hwnd)
        result.append(item)
    return result


def drag_selection_region(start_x: int, start_y: int, end_x: int, end_y: int) -> list[int] | None:
    """Return x,y,w,h only for a visible upper-left to lower-right drag."""
    width, height = int(end_x) - int(start_x), int(end_y) - int(start_y)
    if width < 2 or height < 2:
        return None
    return [int(start_x), int(start_y), width, height]


def restore_modal_after_overlay(dialog, main, previous_main_state: str) -> bool:
    """Restore a hidden modal safely after a full-screen selection overlay."""
    try:
        main.deiconify()
    except tk.TclError:
        pass
    if previous_main_state == "zoomed":
        try:
            main.state("zoomed")
        except tk.TclError:
            pass
    try:
        main.update_idletasks()
    except tk.TclError:
        pass
    try:
        show_window_no_activate(int(main.winfo_id()))
    except (TypeError, ValueError, tk.TclError):
        pass
    try:
        dialog.transient(main)
    except tk.TclError:
        pass
    try:
        dialog.deiconify()
        dialog.update_idletasks()
    except tk.TclError:
        try:
            dialog.grab_release()
        except tk.TclError:
            pass
        return False
    for operation in (dialog.lift, dialog.focus_force):
        try:
            operation()
        except tk.TclError:
            pass
    try:
        dialog.grab_set()
    except tk.TclError:
        return False
    return True


def activate_main_after_modal(main) -> bool:
    """Return focus to the main editor without changing its geometry."""
    try:
        main.deiconify()
        main.update_idletasks()
        main.lift()
        main.focus_force()
    except tk.TclError:
        return False
    return True


def ancestor_windows(window) -> list:
    """Return every parent window above ``window``, nearest first."""
    result = []
    seen = {id(window)}
    current = window
    while current is not None and len(result) < 20:
        try:
            current = current.master
        except (AttributeError, tk.TclError):
            break
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        result.append(current)
    return result


def app_windows(start=None) -> list:
    """本进程里所有需要为幕布让开的顶层窗口。

    只沿着 ``master`` 链往上找是不够的：悬浮提醒小窗是挂在根窗口下的独立
    ``Toplevel``，而且 ``overrideredirect(True)`` 让它不属于任何窗口链，还是
    ``-topmost``。幕布自己也是 ``-topmost``，于是这种小窗会留在幕布之上，
    表现为「点了幕布选取，软件界面还在」。这里改为从根窗口把每一个顶层窗口
    都收进来，幕布期间软件界面整层让开。

    从 ``start`` 所在的窗口向上找根窗口，因此对调用方是透明的。
    """
    windows: list = []
    # 用 Tk 控件路径去重：winfo_children() 每次都会为同一个窗口新建一个包装
    # 对象，按 id() 去重会把真正的窗口挡在门外。
    seen: set[str] = set()

    # 先找到根窗口，再把它下面的每个顶层窗口都收进来。root 的 master 是 None，
    # 用 root is None 作为终止条件，避免把 start 与根窗口单独判重。
    root = start
    for _ in range(20):
        if root is None:
            break
        try:
            if isinstance(root, tk.Tk):
                break
            parent = root.master
        except (AttributeError, tk.TclError):
            break
        if parent is None:
            break
        root = parent

    def add(window) -> None:
        if window is None:
            return
        try:
            key = str(window)
            if not window.winfo_exists() or key in seen:
                return
        except (AttributeError, tk.TclError):
            return
        seen.add(key)
        windows.append(window)

    for candidate in (root, start):
        add(candidate)
    try:
        # 先确认拿到的是真的子窗口序列：测试替身返回不可迭代对象时不应炸掉。
        children = root.winfo_children()
        if isinstance(children, (list, tuple)):
            for child in children:
                if isinstance(child, tk.Toplevel):
                    add(child)
    except (AttributeError, tk.TclError):
        pass
    return windows


IMAGE_TIMEOUT_OPTIONS = (
    ("继续执行", "continue"),
    ("跳转到目标动作", "jump"),
    ("结束当前脚本", "end_current_script"),
    ("停止全部执行", "stop"),
)


MODULE_RESULT_OPTIONS = (
    ("继续下一行", "continue"),
    ("跳转到行对象", "jump"),
    ("结束当前最里层脚本", "end_current_script"),
)


def show_floating_notice(parent, title: str, text: str, duration_ms: int = 4500) -> None:
    """Show a reusable, non-blocking notice instead of a modal message box."""
    try:
        root = parent._root()
    except (AttributeError, tk.TclError):
        root = parent
    callback = getattr(root, "_macroflow_notice_callback", None)
    content = f"{title}：{text}" if title else str(text)
    if callable(callback):
        callback(content, duration_ms)
        return

    existing = getattr(root, "_macroflow_fallback_notice", None)
    try:
        if existing is not None and existing.winfo_exists():
            root._macroflow_fallback_notice_label.configure(text=content)
            timer = getattr(root, "_macroflow_fallback_notice_timer", None)
            if timer is not None:
                existing.after_cancel(timer)
            root._macroflow_fallback_notice_timer = existing.after(
                duration_ms, lambda: _close_fallback_notice(root, existing),
            )
            existing.deiconify()
            existing.lift()
            return
    except tk.TclError:
        pass

    notice = tk.Toplevel(root)
    root._macroflow_fallback_notice = notice
    notice.withdraw()
    notice.overrideredirect(True)
    notice.attributes("-topmost", True)
    notice.configure(background="#263541", takefocus=False)
    width, height = px(360), px(68)
    # 提醒条摆在主界面所在显示器的顶部居中：用 winfo_screenwidth 会在副屏
    # （尤其左侧副屏，x 为负）上被拽回主屏，提醒就跑到另一块屏去了。
    area = monitor_work_area_for(root)
    x = int(area["left"]) + max(px(10), (int(area["width"]) - width) // 2)
    notice.geometry(f"{width}x{height}+{x}+{int(area['top']) + px(36)}")
    set_rounded_window(notice.winfo_id(), px(10))
    frame = ttk.Frame(notice, padding=pad(12, 10))
    frame.pack(fill="both", expand=True)
    label = ttk.Label(frame, text=content, wraplength=px(330), justify="left")
    label.pack(anchor="w", fill="both", expand=True)
    root._macroflow_fallback_notice_label = label
    notice.update_idletasks()
    try:
        make_window_no_activate(notice.winfo_id())
    except Exception:
        pass
    notice.deiconify()
    notice.lift()
    root._macroflow_fallback_notice_timer = notice.after(
        duration_ms, lambda: _close_fallback_notice(root, notice),
    )


def _close_fallback_notice(root, notice) -> None:
    if notice is not getattr(root, "_macroflow_fallback_notice", None):
        return
    root._macroflow_fallback_notice = None
    root._macroflow_fallback_notice_label = None
    root._macroflow_fallback_notice_timer = None
    try:
        notice.destroy()
    except tk.TclError:
        pass


def key_to_vk(text: str) -> tuple[int, str]:
    value = text.strip().upper()
    if not value:
        raise ValueError("请输入按键")
    if value in VK_NAMES:
        return VK_NAMES[value], value
    if value.startswith("VK_"):
        return int(value[3:], 0), value
    # 单个字符优先于纯数字：按键捕获得到的数字键（如 "0" = VK 0x30）
    # 必须按字符解析，而不是当作十进制虚键码 0-9（VK 0 是空键）。
    if len(value) == 1:
        vk = ctypes.windll.user32.VkKeyScanW(value) & 0xFF
        if vk == 0xFF:
            raise ValueError(f"无法识别按键：{text}")
        return vk, value
    if value.startswith("0X") or value.isdigit():
        return int(value, 0), f"VK_{int(value, 0):#x}"
    raise ValueError("请使用单个字符、F1、ENTER、SPACE、CTRL、方向键或 VK_0xNN")


def vk_to_key_name(vk: int) -> str:
    """Inverse of key_to_vk: turn a virtual key code back into a name."""
    vk = int(vk) & 0xFF
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    for name, code in VK_NAMES.items():
        if code == vk:
            return name
    return f"VK_0x{vk:x}"


class ModalDialog(tk.Toplevel):
    def __init__(self, parent, title: str, width: int = 480, height: int = 320,
                 align_top: bool = False, defer_show: bool = False):
        super().__init__(parent)
        self._deferred_show = bool(defer_show)
        if self._deferred_show:
            # 模块表单内容较多：先在隐藏状态完成全部控件、尺寸和顶部定位，
            # show() 时再一次性显示，彻底消除中间位置或空白窗口闪现。
            self.withdraw()
        self.result = None
        self.title(title)
        self.configure(background=COLOR_BG)
        self.transient(parent)
        self.grab_set()
        self._align_top = bool(align_top)
        # 声明尺寸放不下当前显示器时按可用区域压缩：压缩后内容可能被裁，
        # 所以同时放开拉伸，用户仍能把窗口拉回来。
        area = monitor_work_area_for(parent)
        declared = (px(width), px(height))
        self._declared_size = (
            min(declared[0], max(1, int(area["width"]) - px(DIALOG_FRAME_MARGIN))),
            min(declared[1], max(1, int(area["height"]) - px(DIALOG_FRAME_MARGIN))),
        )
        clamped = self._declared_size != declared
        self.resizable(clamped, clamped)
        self.geometry(f"{self._declared_size[0]}x{self._declared_size[1]}")
        self.update_idletasks()
        set_dark_titlebar(self.winfo_id())
        # 高表单可从创建第一帧就贴顶，避免先在屏幕中间显示、完成内容布局后
        # 再跳到顶部。普通对话框仍沿用父窗口内居中。
        place_window_on_parent(
            self, parent, self._declared_size[0], self._declared_size[1],
            self._align_top, area,
        )
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def show(self):
        self._install_duration_units()
        self._shrink_to_content()
        # 置前并抢占 OS 焦点：模态框从后台窗口打开时若不激活，真实点击
        # 会被其他窗口截走，输入框永远得不到焦点（v1.82.6）。
        if getattr(self, "_deferred_show", False):
            self.deiconify()
            self.update_idletasks()
        self.lift()
        self.focus_force()
        self.wait_window()
        return self.result

    def _shrink_to_content(self):
        """把没显式定过尺寸的对话框收缩到内容需求，去掉底部那片空白。

        只缩不放：声明尺寸大于内容需求时缩到需求（内容照样装得下），小于需求时
        保持原样。已按内容定过尺寸的跳过——可滚动表单的窗口尺寸来自 content_*
        而不是自身 reqsize，再缩一次会把表单压扁。
        """
        if getattr(self, "_macroflow_fitted_to_content", False):
            return
        self.update_idletasks()
        width = min(self._declared_size[0], self.winfo_reqwidth())
        height = min(self._declared_size[1], self.winfo_reqheight())
        self.geometry(f"{width}x{height}")
        # 只改尺寸会让窗口偏向右下，按新尺寸重新居中。
        place_window_on_parent(
            self, self.master, width, height, getattr(self, "_align_top", False),
        )

    def _install_duration_units(self):
        """Add one ms/s selector beside every entry backed by DurationVar."""
        if getattr(self, "_duration_units_installed", False):
            return
        self._duration_units_installed = True
        if getattr(self, "_skip_auto_duration_units", False):
            # 对话框已手动放置单位框（如 DurationDialog），不能再自动插一个。
            return
        if not hasattr(self, "tk"):
            return
        for widget in self.winfo_children():
            self._install_duration_units_in(widget)

    def _install_duration_units_in(self, parent):
        for widget in parent.winfo_children():
            variable_name = ""
            if isinstance(widget, (ttk.Entry, ttk.Spinbox)):
                variable_name = str(widget.cget("textvariable"))
            variable = None
            for candidate in self.__dict__.values():
                if isinstance(candidate, DurationVar) and str(candidate) == variable_name:
                    variable = candidate
                    break
            if variable is not None:
                manager = widget.winfo_manager()
                combo = ttk.Combobox(
                    parent, textvariable=variable.unit, values=TIME_UNITS,
                    state="readonly", width=4,
                )
                if manager == "grid":
                    info = widget.grid_info()
                    combo.grid(
                        row=int(info["row"]), column=int(info["column"]) + 1,
                        sticky="w", padx=pad(6, 0), pady=info.get("pady", 0),
                    )
                elif manager == "pack":
                    combo.pack(side="left", padx=pad(6, 0))
            self._install_duration_units_in(widget)


def place_window_on_parent(window, parent, width: int, height: int, align_top: bool = False,
                           area: dict[str, int] | None = None) -> dict[str, int]:
    """把已定好尺寸的窗口摆到父窗口中央，并限制在父窗口所在显示器内。

    align_top=True 时只水平居中、垂直贴该显示器顶部（较高的编辑表单用）。
    """
    if area is None:
        area = monitor_work_area_for(parent)
    x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
    y = int(area["top"]) if align_top else parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    x, y = clamp_to_work_area(area, width, height, x, y)
    window.geometry(f"+{x}+{y}")
    return area


def fit_scrollable_window_to_content(window, parent, body, scrollbar,
                                     align_top: bool = False):
    """可滚动表单窗口按内容实际需求定尺寸。

    表单挂在 Canvas 里，grid/pack 的需求尺寸要等一次空闲布局才算出来：紧接着
    读 body.winfo_reqwidth/reqheight 只会拿到 1×1，窗口于是被设成一条缝
    （内容只露出一点点）。这里先跑一次空闲布局再读，读到的才是真实内容尺寸。
    """
    window.update_idletasks()
    fit_window_to_content(
        window, parent,
        content_width=body.winfo_reqwidth() + scrollbar.winfo_reqwidth(),
        content_height=body.winfo_reqheight() + 4,
        align_top=align_top,
    )


def fit_window_to_content(window, parent,
                          content_width: int | None = None,
                          content_height: int | None = None,
                          align_top: bool = False):
    """把窗口尺寸设成内容的实际需求尺寸并居中（防高 DPI 下内容被裁掉）。

    打包后的 EXE（PyInstaller onefile）按显示器真实 DPI 渲染（125% 缩放时
    内容需求高度约为开发环境的 1.3 倍），固定高度窗口会把底部内容挤出窗口。
    geometry 的宽高即内容区大小，因此按 winfo_reqwidth/reqheight 重设后
    再居中即可装下全部内容；屏幕放不下时压缩到屏幕内并用可拉伸兜底。

    content_width / content_height：可滚动表单里 Canvas 包裹内容后，窗口自身
    的 reqsize 不再反映内容尺寸，需显式传入内容实际需求尺寸（如 body 的
    winfo_reqwidth / winfo_reqheight）。
    align_top=True：保持水平居中，但窗口顶部贴到屏幕顶部；适合较高的编辑表单。

    这里不再有 minimum_width / minimum_height 兜底：窗口一旦被抬高到内容需求
    之上，多出来的高度就被 expand 的表格/文本框吃掉，表现为“明明装得下文字却
    空出一大片”。req* 值本身已随 DPI 缩放，按内容定尺寸内容永远装得下。
    """
    window.update_idletasks()
    width = int(content_width if content_width is not None else window.winfo_reqwidth())
    height = int(content_height if content_height is not None else window.winfo_reqheight())
    area = monitor_work_area_for(parent)
    width = min(width, max(1, int(area["width"]) - px(DIALOG_FRAME_MARGIN)))
    height = min(height, max(1, int(area["height"]) - px(DIALOG_FRAME_MARGIN)))
    window.geometry(f"{width}x{height}")
    place_window_on_parent(window, parent, width, height, align_top, area)
    # 已按内容定过尺寸：ModalDialog.show() 不再重复收缩（可滚动表单的尺寸来自
    # content_* 而不是自身 reqsize，再缩一次会把表单压扁）。
    window._macroflow_fitted_to_content = True
    # 兜底：若内容仍超出屏幕可手动拉伸，按钮行始终可达。
    window.resizable(True, True)


class Tooltip:
    """给控件挂悬停说明：光标停在上面约 0.4 秒后显示，移开自动消失。

    说明框定位在 anchor 控件（默认是被悬停的控件本身）正下方，绝不遮挡
    同行右侧的输入框——悬停字段名时，说明弹出在整行下方，输入框始终
    可见可点，不会被说明文字盖住（否则用户会以为"没有可以输入的地方"）。
    """

    def __init__(self, widget, text: str, anchor=None):
        self.widget = widget
        self.text = text
        self.anchor = anchor if anchor is not None else widget
        self._after_id = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        if self._after_id is not None:
            return
        self._after_id = self.widget.after(400, self._show)

    def _show(self):
        self._after_id = None
        if self._tip is not None:
            return
        anchor = self.anchor
        x = anchor.winfo_rootx() + 8
        y = anchor.winfo_rooty() + anchor.winfo_height() + 6
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(
            tip, text=self.text, background="#ffffe0", foreground="#333333",
            justify="left", padx=px(10), pady=px(6), font=(FONT_FAMILY, FONT_BODY),
        ).pack()
        tip.update_idletasks()
        area = monitor_work_area_for(anchor)
        w, h = tip.winfo_width(), tip.winfo_height()
        if x + w > area["left"] + area["width"]:
            x = max(area["left"], area["left"] + area["width"] - w - px(4))
        if y + h > area["top"] + area["height"]:
            # 下方放不下就翻到 anchor 上方。
            y = max(area["top"], anchor.winfo_rooty() - h - px(6))
        tip.geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide(self, _event=None):
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


CATEGORY_LABELS = {
    "switch": "切换模块",
    "workflow_global": "工作流全局模块",
    "script_global": "脚本全局模块",
    "special": "特殊模块",
}


AFTER_ACTION_LABELS = {
    "click_match": "点击识别区域",
    "click_custom": "点击自定义位置",
    "continue": "成功后继续",
    "second_match": "二次识别后点击",
}


AFTER_ACTION_VALUES = {label: value for value, label in AFTER_ACTION_LABELS.items()}


FALLBACK_ON_MATCH_LABELS = {
    "continue": "继续识别主模块（不点击）",
    "click_continue": "点击备用命中位置，继续识别主模块",
    "exit": "直接退出主模块识别（不点击）",
    "click_exit": "点击备用命中位置后退出主模块识别",
}


FALLBACK_ON_MATCH_VALUES = {
    label: value for value, label in FALLBACK_ON_MATCH_LABELS.items()
}


SECOND_MATCH_CLICK_TARGET_LABELS = {
    "first": "第一次识别位置",
    "second": "第二次识别位置",
    "custom_region": "自定义框选区域",
}


SECOND_MATCH_CLICK_TARGET_VALUES = {
    label: value for value, label in SECOND_MATCH_CLICK_TARGET_LABELS.items()
}


SEGMENT_DEPTH_LIMIT = 8


RESTART_USE_DEFAULT_ROW_LABEL = "（使用默认跳转行）"


def scrollable_dialog_body(window, *, padding: int = 12):
    """把对话框内容包进带纵向滚动条的 Canvas，返回内容容器。

    识别类动作表单行数多，固定高度窗口在高 DPI / 小屏下会把底部按钮挤出
    窗口；统一用 Canvas 包住后所有行始终可滚动到达。
    """
    canvas = tk.Canvas(window, background=COLOR_BG, highlightthickness=0, borderwidth=0)
    scrollbar = ttk.Scrollbar(window, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    body = ttk.Frame(canvas, padding=px(padding))
    body_window = canvas.create_window((0, 0), window=body, anchor="nw")

    def scroll(event):
        if event.delta:
            canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    window.bind("<MouseWheel>", scroll)
    canvas.bind("<MouseWheel>", scroll, add="+")
    body.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(body_window, width=event.width))
    canvas.bind("<Map>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.after_idle(lambda: canvas.configure(scrollregion=canvas.bbox("all")))
    return body, canvas, scrollbar


HOTKEY_DISALLOWED_NAMES = {
    "SHIFT", "CTRL", "ALT", "LWIN", "RWIN",
    "CAPSLOCK", "NUMLOCK", "SCROLLLOCK", "PAUSE",
}


KEY_HINT_DEFAULT = "例：A、F5、ENTER、SPACE、CTRL、LEFT、VK_0x41"


KEY_HINT_CAPTURING = "请按下要插入的按键（F8 / F9 / F12 为软件快捷键）…按 Esc 取消"


DEFAULT_GAME_SETUP_NOTE = """使用本软件前，建议在游戏中完成以下设置（可自行修改补充）：

1. 分辨率：使用与录制时一致的分辨率（默认 1920×1080），脚本坐标会按分辨率缩放。
2. 鼠标灵敏度：录制与执行时保持一致，灵敏度变化会导致转向/瞄准幅度不同。
3. 鼠标加速：关闭游戏内鼠标加速和系统「提高指针精确度」，否则转向偏移不准。
4. 窗口模式：使用全屏或窗口化运行，不要最小化；执行时游戏窗口需保持在前台。
5. 输入法：系统需安装「英语（美国）」键盘，执行时会自动切换到英语输入法。
6. 管理员权限：以管理员身份运行本软件，否则系统级输入锁定（专注模式）可能失败。
7. 绑定窗口：进入游戏后，在软件侧栏点击「绑定窗口」选择游戏窗口。
8. 快捷键：F8 开始/停止录制，F9 执行当前脚本，F12 紧急停止。"""
