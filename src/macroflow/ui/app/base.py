from __future__ import annotations

from macroflow.core.models import (
    ACTION_ID_KEY, DEFAULT_MOUSE_MOVE_INTERVAL_MS, DEFAULT_RECORDED_SCREEN,
    DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS,
    END_CURRENT_SCRIPT_LABEL, JUMP_TARGET_KEYS, NEXT_WORKFLOW_STEP_TARGET_ID,
    RECORDED_INPUT_STEPS_KEY, RECORDED_INPUT_TYPE,
    SCRIPT_START_TARGET_ID,
    MacroScript, Workflow, clone_actions_with_new_ids,
    ensure_action_ids, ensure_workflow_step_ids, is_global_script,
    new_action_id,
    recorded_input_steps,
    script_ref_repeat_count, scroll_clicks, scroll_direction_label,
)
from pathlib import Path
import ctypes
from macroflow.input.wininput import (
    WindowInfo, activate_window, enum_windows, get_cursor_pos,
    get_monitor_rect_for_window, get_monitor_work_area_for_point,
    get_monitor_work_area_for_window, get_primary_screen_rect, get_virtual_screen_rect,
    get_window_dpi,
    force_english_input, get_foreground_window_info, get_window_rect,
    is_current_process_window, is_window, is_window_process_foreground,
    make_window_no_activate, send_button, send_move_absolute,
    set_dark_titlebar, set_rounded_window, show_window,
    show_window_no_activate,
)
import tkinter as tk
import ttkbootstrap as ttk

from .constants import (
    ACTION_ICONS,
    COLOR_HOVER,
    FLOATING_NOTICE_HEIGHT,
    FLOATING_NOTICE_WIDTH,
    MIN_MAIN_HEIGHT,
    MIN_MAIN_WIDTH,
    SCRIPT_CATEGORY_LABELS,
)

def enable_per_monitor_dpi_awareness() -> None:
    """按显示器缩放（Per-Monitor v2），必须在创建 Tk 窗口之前调用。

    ttkbootstrap 在创建窗口时会调 SetProcessDPIAware()（SYSTEM_AWARE），而
    进程的 DPI 感知级别**只有第一次调用生效**。笔记本 + 外接屏不同缩放时，
    SYSTEM_AWARE 会把另一块屏按主屏 DPI 虚拟化：坐标、截图、虚拟桌面尺寸
    全部错位（实测虚拟桌面被算成 6360×2522，真实只有 4440×1680）。
    """
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except (AttributeError, OSError):
        pass


# 必须在任何 Tk 窗口创建之前设置：见上面的说明（拆分前这行在 app.py 的
# 模块级，ttkbootstrap 导入之后、窗口创建之前执行）。
enable_per_monitor_dpi_awareness()

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
def split_toolbar_specs(specs, primary: set[str]) -> tuple[tuple, tuple]:
    """把按钮清单拆成「常驻按钮」与「更多菜单项」，保持各自原顺序。

    界面减法用：动作类型太多时，常驻只留最常用的那几个，其余进「+ 添加动作 ▾」
    的菜单。两个清单来自同一份 specs，因此不会有动作只存在于一个入口——
    菜单项执行的是同一个命令函数，快捷键与右键菜单也照旧。
    """
    items = tuple(specs)
    head = tuple(item for item in items if item[1] in primary)
    tail = tuple(item for item in items if item[1] not in primary)
    return head, tail
def floating_notice_xy(position: str, screen_width: int, screen_height: int,
                       width: int = FLOATING_NOTICE_WIDTH,
                       height: int = FLOATING_NOTICE_HEIGHT) -> tuple[int, int]:
    margin = 18
    left = margin
    center = max(margin, (int(screen_width) - width) // 2)
    right = max(margin, int(screen_width) - width - margin)
    top = margin
    bottom = max(margin, int(screen_height) - height - margin)
    positions = {
        "左上": (left, top), "顶部居中": (center, top), "右上": (right, top),
        "左下": (left, bottom), "底部居中": (center, bottom), "右下": (right, bottom),
    }
    return positions.get(position, positions["顶部居中"])
def action_kind_label(kind: str, label: str) -> str:
    return f"{ACTION_ICONS.get(kind, '•')}  {label}"
def _key_vk(key) -> int:
    """Extract the Windows virtual key code from a pynput key object."""
    vk = getattr(key, "vk", None)
    if vk is None and hasattr(key, "value"):
        vk = getattr(key.value, "vk", None)
    try:
        return int(vk or 0)
    except (TypeError, ValueError):
        return 0
def disable_combobox_wheel_selection(root) -> None:
    """Prevent every ttk combobox from changing value via the mouse wheel.

    The popup list is a separate Listbox, so users can still scroll the opened
    option list; only an unfurled combobox under the pointer is protected.
    """
    stop = lambda _event: "break"
    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        root.bind_class("TCombobox", sequence, stop)
def apply_pointer_cursors(widget) -> None:
    """Give every clickable control a hand cursor (Tk has no hover CSS)."""
    for child in widget.winfo_children():
        if isinstance(child, (ttk.Button, ttk.Checkbutton, tk.Button, tk.Checkbutton)):
            try:
                child.configure(cursor="hand2")
            except tk.TclError:
                pass
        apply_pointer_cursors(child)
def attach_autohide_scrollbar(tree, scrollbar) -> None:
    """Hide the vertical scrollbar while the whole list already fits.

    ttk 没有自动隐藏：内容不满一屏时滚动条仍会显示成一条空槽，看起来像“没
    占满却先上了滚动条”。这里按 tree 的可见范围自动显隐。

    放进布局的布局管理器要现问现用：调用方是「先 attach、再 grid」，attach
    时滚动条还没被任何布局管理器接管（winfo_manager() 返回空串），照当时的
    值记下来就会把「显示」走成 pack —— 在 grid 管理的外壳里 Tk 直接报
    "cannot use geometry manager pack inside ... grid is already managing its
    content windows"，滚动条再也显示不出来（打开长脚本也看不到、拖不动）。
    """
    state = {"visible": None}

    def set_visible(visible: bool) -> None:
        if visible == state["visible"]:
            return
        state["visible"] = visible
        if (scrollbar.winfo_manager() or "grid") == "grid":
            if visible:
                scrollbar.grid()
            else:
                scrollbar.grid_remove()
        elif visible:
            scrollbar.pack(side="right", fill="y")
        else:
            scrollbar.pack_forget()

    def on_yscrollcommand(first, last) -> None:
        set_visible(not (float(first) <= 0.0 and float(last) >= 1.0))
        scrollbar.set(first, last)

    tree.configure(yscrollcommand=on_yscrollcommand)
def bind_tree_hover(tree, tag: str = "hover") -> None:
    """Highlight the row under the cursor so long lists stay easy to follow."""
    tree.tag_configure(tag, background=COLOR_HOVER)

    def set_hover(row: str | None) -> None:
        previous = getattr(tree, "_hover_row", "")
        if previous == (row or ""):
            return
        if previous and tree.exists(previous) and tag in tree.item(previous, "tags"):
            tree.item(previous, tags=tuple(
                item for item in tree.item(previous, "tags") if item != tag
            ))
        if row and tree.exists(row):
            tags = tree.item(row, "tags")
            if tag not in tags:
                tree.item(row, tags=tuple(tags) + (tag,))
        tree._hover_row = row or ""

    def on_motion(event):
        set_hover(tree.identify_row(event.y))

    def on_leave(_event):
        set_hover(None)

    tree.bind("<Motion>", on_motion, add="+")
    tree.bind("<Leave>", on_leave, add="+")
def default_main_geometry() -> str:
    """默认窗口尺寸：比旧版更紧凑，并按当前显示器收敛，永不超过屏幕。"""
    screen = get_virtual_screen_rect()
    scale = _UI_SCALE if _UI_SCALE > 0 else 1.0
    logical_width = int(screen.get("width", 0)) / scale
    logical_height = int(screen.get("height", 0)) / scale
    width = min(1440, max(MIN_MAIN_WIDTH, int(logical_width) - 80))
    height = min(820, max(MIN_MAIN_HEIGHT, int(logical_height) - 80))
    return f"{px(width)}x{px(height)}"
def workflow_script_name(value: str) -> str:
    """Show a workflow script as a clean name, never as scripts/name.json."""
    return Path(str(value).replace("\\", "/")).stem or str(value)
def script_category_key(label: str) -> str:
    """脚本类别显示名 → 保存键；未知显示名按关卡处理。"""
    for key, value in SCRIPT_CATEGORY_LABELS.items():
        if value == label:
            return key
    return "level"
def script_category_label(key: str) -> str:
    """保存键 → 类别显示名；旧脚本缺失或非法键按关卡处理。"""
    return SCRIPT_CATEGORY_LABELS.get(str(key).strip(), "关卡")
def workflow_execution_progress(script_number: int, script_total: int, script_name: str,
                                repeat_total: int, repeat_current: int | None = None,
                                unlimited: bool = False) -> str:
    short_name = script_name if len(script_name) <= 18 else f"{script_name[:18]}…"
    if unlimited:
        return (f"工作流 {script_number}/{script_total} · {short_name}\n"
                f"不计次数 · 每次到达执行 1 次 · F12 停止")
    repeat_text = "正在准备" if repeat_current is None else f"当前第 {repeat_current}/{repeat_total} 次"
    return (f"工作流 {script_number}/{script_total} · {short_name}\n"
            f"共执行 {repeat_total} 次 · {repeat_text} · F12 停止")
def coordinate_scale_summary(source: dict | None, current: dict | None) -> str:
    source = source or DEFAULT_RECORDED_SCREEN
    current = current or DEFAULT_RECORDED_SCREEN
    source_size = f"{int(source.get('width', 1920))}×{int(source.get('height', 1080))}"
    current_size = f"{int(current.get('width', 1920))}×{int(current.get('height', 1080))}"
    suffix = "（1:1）" if source_size == current_size else "（自动缩放）"
    return f"坐标缩放  {source_size} → {current_size} {suffix}"
def short_region_text(region, fallback: str = "全屏") -> str:
    if isinstance(region, (list, tuple)) and len(region) == 4:
        try:
            width = int(region[2])
        except (TypeError, ValueError):
            width = 0
        return "自定义区域" if width > 0 else fallback
    return fallback
