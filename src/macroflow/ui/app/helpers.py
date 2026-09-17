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
from macroflow.core.storage import (
    BASE_DIR, IMAGES_DIR, SCRIPTS_DIR, WORKFLOWS_DIR, archive_overwritten_script,
    DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS,
    available_script_path, backup_script,
    display_path, ensure_dirs, migrate_workflow_templates, safe_name,
    DIRECTION_SCRIPTS_DIR,
    load_app_settings, load_script, load_workflow,
    script_category_for_path,
    registered_module_object, registered_template_region, remap_hotkey_script_bindings,
    resolve_path, save_app_settings,
    save_script, save_workflow,
    update_module_object,
)
from macroflow.ui.dialogs.actions import ClickDialog, CloseAppDialog, DurationDialog, GameSetupNoteDialog, JsonActionDialog, JumpActionDialog, KeyActionDialog, MouseMoveDialog, OpenAppDialog, RepeatClickDialog, ScheduleDialog, ScrollDialog, SetResolutionActionDialog, TurnActionDialog, edit_action
from macroflow.ui.dialogs.app_dialogs import HotkeyScriptsDialog, ResolutionStylesDialog, ScriptDirectoriesDialog, WindowPicker, WorkflowBatchSettingsDialog, WorkflowRepeatDialog
from macroflow.ui.dialogs.base import DurationVar, TIME_UNITS, Tooltip, key_to_vk, show_floating_notice, vk_to_key_name
from macroflow.ui.dialogs.helpers import recorded_action_description, workflow_step_label
from macroflow.ui.dialogs.module_objects import ModulePickerDialog, TemplateRegionFormDialog, TemplateRegionManagerDialog
from macroflow.ui.dialogs.recognition import GlobalDetectDialog, MultiConditionClickDialog, OcrCompareActionDialog, RowListConditionClickDialog, RowListDiagnosticResultDialog, RowRecognitionResultDialog
from pathlib import Path
import copy
from datetime import datetime
from macroflow.ui import dialogs as dialogs_ui
from tkinter import filedialog, messagebox, simpledialog
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
import re
from macroflow.core.resolution import resolution_styles_from_settings
import threading
import time
import tkinter as tk

from .base import (
    coordinate_scale_summary,
    px,
    script_category_key,
    set_ui_scale,
)
from .constants import (
    ACTION_TREE_COLUMNS,
    BACKUP_INTERVAL_CHOICES,
    BACKUP_INTERVAL_MS,
    EVENT_LOG_HEADER,
    GLOBAL_TREE_COLUMNS,
    MIN_MAIN_HEIGHT,
    MIN_MAIN_WIDTH,
    SEGMENT_BAR,
    TRACE_LOG_HEADER,
    WORKFLOW_TREE_COLUMNS,
)
from .startup import (
    set_windows_startup,
)
from .summaries import (
    action_detail,
)

class HelpersMixin:
    """通用助手：线程内 UI 回调、通知、提示条、状态与日志。"""

    def _ui(self, callback, *args):
        detection_context = getattr(self, "_detection_event_context", None)
        deferred_events = getattr(detection_context, "events", None)
        if deferred_events is not None:
            if getattr(callback, "__func__", None) is type(self)._log:
                self._defer_detection_event(deferred_events, "log", text=str(args[0]))
            elif getattr(callback, "__func__", None) is type(self)._append_mini_step:
                self._defer_detection_event(deferred_events, "mini_step", text=str(args[0]))
            return
        # 后台线程产生的运行日志必须先同步落盘，再排队更新 Tk 界面。
        # 这样即使 UI 正忙或程序随后异常退出，文件中也保留已经产生的日志。
        if getattr(callback, "__self__", None) is self \
                and getattr(callback, "__func__", None) is type(self)._log \
                and args:
            # 事件日志是「外层进度」：每条都带上当前工作流行与脚本，便于定位
            # 「软件执行到哪了」；逐行细节走执行明细（见 _trace_event）。
            message = self._with_event_context(str(args[0]))
            line = self._format_log_line(message)
            # 去重按“消息本身”判定：行首的鼠标坐标每次都变，按整行判定等于不去重。
            display = self._write_log_line(line, message)
            if display:
                self._queue_log_view_line(display, "event")
            return
        key = "status" if getattr(callback, "__func__", None) is type(self)._set_status else None
        urgent = bool(key == "status" and args and str(args[0]).lower() in {"错误", "停止"})
        ui_queue = getattr(self, "ui_queue", None)
        if ui_queue is None:
            # 允许未经过完整窗口初始化的后台测试 fixture 复用同一 UI 入口。
            self.root.after(0, callback, *args)
            return
        ui_queue.submit(callback, *args, key=key, urgent=urgent)
    def _set_status(self, text: str, style: str = "normal"):
        self.status_var.set(text)
        colors = {"success": "#12B76A", "warning": "#F79009", "error": "#F04438", "normal": "#667085"}
        self.status_dot.configure(foreground=colors.get(style, colors["normal"]))
    @staticmethod
    def _apply_column_widths(tree, columns, stretch_column: str) -> None:
        for column, text, width, anchor in columns:
            tree.heading(column, text=text)
            tree.column(column, width=px(width), anchor=anchor,
                        stretch=column == stretch_column)
    @staticmethod
    def _selected_row_segment(tree) -> tuple[int, int] | None:
        """列表里选中的那一段（第一行到最后一行），不足两行不算片段。

        片段用两次点击定出来：点一下片段的第一行，再按住 Shift 点最后一行
        （Ctrl 点两头的行同样可以，取的是选中范围的第一行到最后一行）。
        """
        rows = sorted({int(item) for item in tree.selection()})
        if len(rows) < 2:
            return None
        return rows[0], rows[-1]
    def _refresh_row_segment_bar(self, tree, painted_attr: str) -> None:
        """把选中片段画成列表最左边那根实心竖条（只重画变化的行）。"""
        segment = self._selected_row_segment(tree)
        # 未经过完整窗口初始化的测试 fixture 直接按“还没画过”处理。
        painted = getattr(self, painted_attr, None)
        if painted == segment:
            return
        if painted is not None:
            for index in range(painted[0], painted[1] + 1):
                if segment is not None and segment[0] <= index <= segment[1]:
                    continue
                iid = str(index)
                if tree.exists(iid):
                    tree.set(iid, "mark", "")
        if segment is not None:
            for index in range(segment[0], segment[1] + 1):
                iid = str(index)
                if tree.exists(iid):
                    tree.set(iid, "mark", SEGMENT_BAR)
        setattr(self, painted_attr, segment)
    def _reset_row_segment_bar(self, painted_attr: str) -> None:
        """列表要重建：行都换成新的，竖条记号一并作废（重建后按选中重画）。"""
        setattr(self, painted_attr, None)
    def _ask_repeats(self, title: str, prompt: str) -> int | None:
        """问执行次数（默认 1 次、范围 1–999999）；取消返回 None。"""
        return simpledialog.askinteger(
            title, prompt, parent=self.root,
            initialvalue=1, minvalue=1, maxvalue=999999,
        )
    def _resolution_monitor_hwnd(self) -> int | None:
        """分辨率动作未设参照窗口时改哪块屏：软件自己所在显示器。"""
        root = getattr(self, "root", None)
        if root is None:
            return None
        try:
            hwnd = int(root.winfo_id())
        except (tk.TclError, ValueError):
            return None
        return hwnd if hwnd and is_window(hwnd) else None
    def _playback_reference_screen(self) -> dict[str, int]:
        """执行参考屏 = 绑定窗口所在显示器（未绑定时主显示器）。

        录制与回放都以"显示器矩形"为基准，虚拟桌面（多屏拼接）只用于窗口
        布局，不能用它做坐标/模板缩放：接上或拔掉外接屏会改变虚拟桌面宽度，
        同屏回放的坐标就会被错误缩放。
        """
        bound = getattr(self, "bound_window", None)
        hwnd = int(bound.hwnd) if bound is not None else None
        return get_monitor_rect_for_window(hwnd) or get_primary_screen_rect()
    def _refresh_coordinate_scale_status(self):
        # 绑定/解除绑定时刷新"执行参考屏"；未完成界面初始化的测试夹具直接跳过。
        scale_var = getattr(self, "coordinate_scale_var", None)
        script = getattr(self, "script", None)
        if scale_var is None or script is None:
            return
        scale_var.set(coordinate_scale_summary(
            script.settings.get("recorded_screen"), self._playback_reference_screen(),
        ))
    def _format_log_line(self, text: str) -> str:
        stamp = datetime.now().strftime("%H:%M:%S")
        try:
            x, y = get_cursor_pos()
            cursor = f"[鼠标 {x},{y}]"
        except Exception:
            cursor = "[鼠标 ?,?]"
        return f"[{stamp}] {cursor} {text}\n"
    def _write_log_line(self, line: str, message: str | None = None) -> None:
        """写事件日志 + 界面「事件日志」视图；30 秒内重复的消息合并成一行。

        全局检测每秒轮询一次，目标一直可见时同一句“识别到/触发”会刷成千上万
        行（实测一份日志 93% 是 3 句话的重复），把真正有用的信息埋掉。
        去重按消息本身（行首鼠标坐标每次都变，按整行判定等于不去重）；

        返回界面该追加的文本（'' 表示这一条被合并掉了）。
        """
        text = (message if message is not None else line).strip()
        now = time.perf_counter()
        lock = getattr(self, "log_file_lock", None)
        if lock is None:
            lock = self.log_file_lock = threading.Lock()
        with lock:
            window_ms = getattr(self, "_log_dedup_window_ms", 30000)
            if text == getattr(self, "_log_dedup_text", "") and \
                    (now - getattr(self, "_log_dedup_since", 0.0)) * 1000 < window_ms:
                self._log_dedup_count = getattr(self, "_log_dedup_count", 1) + 1
                self._log_dedup_line = line
                return ""
            pending = self._log_dedup_pending()
            self._log_dedup_text = text
            self._log_dedup_line = line
            self._log_dedup_count = 1
            self._log_dedup_since = now
            self._append_log_file(line + pending)
            return pending + line
    def _log_dedup_pending(self) -> str:
        """被合并掉的重复行：在下一行之前补一条“重复 N 次”。"""
        count = getattr(self, "_log_dedup_count", 0)
        if count <= 1 or not getattr(self, "_log_dedup_text", ""):
            return ""
        # 用被合并那一行的格式（含它自己的时间与鼠标位置），别写成新行的副本。
        return (f"    ↳ 上一行（{getattr(self, '_log_dedup_line', '').strip()}）"
                f"重复 {count - 1} 次，已合并\n")
    def _flush_log_dedup(self) -> None:
        """退出前把累计的重复次数落盘，避免丢掉这段计数。"""
        lock = getattr(self, "log_file_lock", None)
        if lock is None:
            return
        with lock:
            pending = self._log_dedup_pending()
            if not pending:
                return
            self._log_dedup_text = ""
            self._log_dedup_count = 0
            self._append_log_file(pending)
    def _append_log_file(self, text: str) -> None:
        log_path = getattr(self, "session_log_path", None)
        if log_path is None or not text:
            return
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            new_file = not log_path.exists()
            with log_path.open("a", encoding="utf-8") as log_file:
                if new_file:
                    log_file.write(EVENT_LOG_HEADER)
                log_file.write(text)
                log_file.flush()
        except OSError:
            pass
    def _trace_event(self, text: str, module_detail: bool = False) -> None:
        """执行期细节（模块识别/点击/轮询）只进执行明细，不进事件日志。

        ``module_detail`` 表示这是“某一行脚本内部发生了什么”（模块命中、点击、
        阻塞等待、逐行扫描…），明细里缩进显示，和脚本行本身区分开。
        """
        if module_detail:
            self._emit_trace_line(f"└ {text}")
            return
        # 不传这个关键字：调用方（_ui 夹具、快捷键回调）可能只接受一个参数。
        self._emit_trace_line(text)
    def _trace_context(self) -> dict:
        context = getattr(self, "_trace_context_state", None)
        if context is None:
            context = {}
            self._trace_context_state = context
        return context
    def _set_trace_context(self, **fields) -> None:
        """记录当前执行位置（工作流第几步 / 哪个脚本 / 第几次），供两级日志分层。

        明细日志靠它打出「工作流第 3/31 步 · 脚本[X]（12 行）· 第 4/100 次」这样的
        层级标题，事件日志靠它把每条消息标到具体的工作流行与脚本上；两者都不再
        混着写，也不用每行重复一遍上下文。
        """
        context = self._trace_context()
        previous = self._trace_context_header()
        context.update(fields)
        header = self._trace_context_header()
        if header and header != previous:
            self._emit_trace_line(f"▶ {header}")
    def _clear_trace_context(self) -> None:
        self._trace_context().clear()
    def _trace_context_header(self) -> str:
        context = self._trace_context()
        parts = []
        if str(context.get("script", "")).strip():
            label = f"脚本[{context['script']}]"
            if int(context.get("total", 0) or 0) > 0:
                label += f"（{int(context['total'])} 行）"
            parts.append(label)
        if int(context.get("step", 0) or 0) > 0 and int(context.get("steps", 0) or 0) > 0:
            parts.append(f"工作流第 {int(context['step'])}/{int(context['steps'])} 步")
        if int(context.get("repeat", 0) or 0) > 1:
            total = int(context.get("repeats", 0) or 0)
            parts.append(
                f"第 {int(context['repeat'])}/{total} 次" if total > 1
                else f"第 {int(context['repeat'])} 次"
            )
        # 段落和分隔符上都不留多余空格：层级标题必须稳定成
        # 「脚本[X]（12 行）· 工作流第 3/31 步 · 第 4/100 次」这一种写法。
        header = " · ".join(" ".join(str(part).split()) for part in parts if str(part).strip())
        return " · ".join(segment.strip() for segment in header.split("·"))
    def _event_context_label(self) -> str:
        """事件日志每条消息前面的执行位置；没有正在执行的脚本时为空。"""
        context = self._trace_context()
        script = str(context.get("script", "")).strip()
        if not script:
            return ""
        parts = []
        if int(context.get("step", 0) or 0) > 0 and int(context.get("steps", 0) or 0) > 0:
            parts.append(f"工作流第 {int(context['step'])}/{int(context['steps'])} 步")
        parts.append(f"脚本[{script}]")
        return " · ".join(parts)
    def _with_event_context(self, text: str) -> str:
        context = self._event_context_label()
        if not context or text.startswith(context) or text.startswith("工作流第 "):
            # 消息本身已经写明工作流进度（"工作流第 5 行完成一次…"）时不再重复前缀。
            return text
        return f"{context}：{text}"
    def _emit_trace_line(self, text: str) -> None:
        """一条执行明细：落盘 + 排队到界面「执行明细」视图（播放线程调用）。"""
        line = f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} | {text}" + "\n"
        self._write_trace_line(line)
        self._queue_log_view_line(line, "trace")
    def _write_trace_line(self, text: str) -> None:
        """写执行明细文件（失败不影响执行）。"""
        trace_path = getattr(self, "trace_log_path", None)
        if trace_path is None or not text:
            return
        lock = getattr(self, "trace_file_lock", None)
        if lock is None:
            lock = self.trace_file_lock = threading.Lock()
        try:
            with lock:
                trace_path.parent.mkdir(parents=True, exist_ok=True)
                new_file = not trace_path.exists()
                with trace_path.open("a", encoding="utf-8") as trace_file:
                    if new_file:
                        trace_file.write(TRACE_LOG_HEADER)
                    trace_file.write(text)
                    trace_file.flush()
        except OSError:
            pass
    def _on_player_trace(self, event: dict) -> None:
        """播放器每执行一行脚本动作回调一次：写「执行明细」日志。"""
        action = event.get("action") or {}
        index = int(event.get("index", 0))
        total = int(event.get("total", 0))
        elapsed = float(event.get("elapsed_ms", 0.0) or 0.0)
        waited = float(event.get("waited_ms", 0.0) or 0.0)
        depth = int(event.get("depth", 0) or 0)
        where = f"第 {index + 1}/{total} 行"
        if depth > 0:
            owner = str(event.get("script", "")).strip() or "代码段"
            where = f"代码段[{owner}] {where}"
        # 只记有意义的时间：几十毫秒的等待/耗时写出来只是噪音。
        parts = []
        if waited >= 100:
            parts.append(f"等待 {waited:.0f}ms")
        if elapsed >= 50:
            parts.append(f"耗时 {elapsed:.0f}ms")
        timing = " ".join(parts)
        fields = " | ".join(part for part in (where, action_detail(action), timing) if part)
        self._emit_trace_line(fields)
    def _log(self, text: str):
        # 事件日志是「外层进度」：每条都带上当前工作流行与脚本，便于定位
        # 「软件执行到哪了」；逐行细节走执行明细（见 _trace_event）。
        message = self._with_event_context(text)
        line = self._format_log_line(message)
        display = self._write_log_line(line, message)
        if display:
            self._queue_log_view_line(display, "event")
    def _mark_dirty(self):
        self.dirty = True
    def _blank_script_with_activation_draft(self) -> MacroScript:
        """Create a blank script and restore the most recently selected pre-window."""
        script = MacroScript()
        app_settings = getattr(self, "app_settings", {})
        signature = app_settings.get("activation_window_draft")
        if isinstance(signature, dict) and signature.get("title"):
            script.settings["activation_window_enabled"] = bool(
                app_settings.get("activation_window_draft_enabled", True)
            )
            script.settings["activation_window"] = {
                "title": str(signature.get("title", "")),
                "class_name": str(signature.get("class_name", "")),
                "process_path": str(signature.get("process_path", "")),
            }
        return script
    def _restore_main_window_geometry(self) -> None:
        """Restore the main window size and position saved at the last close."""
        geometry = str(getattr(self, "app_settings", {}).get("main_window_geometry", "") or "").strip()
        if not geometry:
            return
        try:
            match = re.fullmatch(r"(\d+)x(\d+)[+-](-?\d+)[+-](-?\d+)", geometry)
            if match:
                width, height = (int(value) for value in match.group(1, 2))
                left, top = (int(value) for value in match.group(3, 4))
                screen = get_virtual_screen_rect()
                screen_left = int(screen.get("left", 0))
                screen_top = int(screen.get("top", 0))
                screen_width = max(1, int(screen.get("width", 0)))
                screen_height = max(1, int(screen.get("height", 0)))
                visible_width = min(left + width, screen_left + screen_width) - max(left, screen_left)
                visible_height = min(top + height, screen_top + screen_height) - max(top, screen_top)
                # 显示器配置变化后，旧位置可能落在已断开的显示器上。
                # 至少保留一小块可见区域，否则恢复到当前虚拟桌面中央。
                if visible_width < min(64, width) or visible_height < min(64, height):
                    width = min(width, screen_width)
                    height = min(height, screen_height)
                    left = screen_left + max(0, (screen_width - width) // 2)
                    top = screen_top + max(0, (screen_height - height) // 2)
                    geometry = f"{width}x{height}+{left}+{top}"
            self.root.geometry(geometry)
        except (AttributeError, tk.TclError, ValueError):
            # Ignore stale monitor coordinates or a malformed setting and keep
            # the safe default geometry assigned during window creation.
            return
    def _watch_display_dpi(self) -> None:
        """窗口拖到另一块屏（缩放不同）后重算缩放并重刷界面。"""
        if getattr(self, "exiting", False):
            return
        try:
            dpi = get_window_dpi(self.root.winfo_id())
        except (AttributeError, tk.TclError, OSError, ValueError):
            dpi = 0
        # 与"界面当前实际用的缩放"比，而不是与上次记下的值比：启动时若取错 DPI，
        # 上一次实现只在第二次探测才纠正，窗口开在另一块屏上时永远等不到。
        if dpi and dpi != self._ui_scaling_dpi():
            self._apply_display_dpi(dpi)
        self.root.after(600, self._watch_display_dpi)
    def _ui_scaling_dpi(self) -> int:
        """Tk 当前 scaling 对应的 DPI——px() 就是按它换算的。"""
        try:
            return int(round(float(self.root.tk.call("tk", "scaling")) * 72))
        except (AttributeError, tk.TclError, ValueError):
            return 0
    def _apply_display_dpi(self, dpi: int) -> None:
        """按新显示器 DPI 重算像素缩放，并重刷样式、列宽与最小尺寸。"""
        self.root.tk.call("tk", "scaling", max(1.0, dpi / 72.0))
        set_ui_scale(self.root)
        dialogs_ui.set_ui_scale(self.root)
        self._configure_dark_theme()
        self._apply_column_widths(self.action_tree, ACTION_TREE_COLUMNS, "detail")
        self._apply_column_widths(self.workflow_tree, WORKFLOW_TREE_COLUMNS, "script")
        self._apply_column_widths(self.global_tree, GLOBAL_TREE_COLUMNS, "module")
        self.root.minsize(px(MIN_MAIN_WIDTH), px(MIN_MAIN_HEIGHT))
        self._log(f"检测到显示器缩放变化（{dpi} DPI），界面已按新缩放刷新。")
    def _app_window_hwnd(self) -> int | None:
        """MacroFlow 主窗口句柄（用于判断软件在哪块屏上）。"""
        root = getattr(self, "root", None)
        if root is None:
            return None
        try:
            hwnd = int(root.winfo_id())
        except (tk.TclError, ValueError):
            return None
        return hwnd if hwnd and is_window(hwnd) else None
    def _startup_monitor_area(self) -> dict[str, int]:
        """启动时窗口要铺满的显示器：上次的位置优先，其次鼠标所在屏。

        不能用 winfo_screenwidth/height（多屏下返回的是虚拟桌面尺寸，
        会把窗口放到屏幕外面去），必须用 Win32 的显示器可用区域。
        """
        saved = str(self.app_settings.get("main_window_geometry", "") or "")
        match = re.fullmatch(r"(\d+)x(\d+)[+-](-?\d+)[+-](-?\d+)", saved)
        if match:
            width, height, left, top = (int(value) for value in match.groups())
            area = get_monitor_work_area_for_point(
                left + width // 2, top + height // 2,
            )
            if area is not None:
                return area
        try:
            cursor = get_cursor_pos()
        except Exception:
            cursor = (0, 0)
        return get_monitor_work_area_for_point(*cursor) or get_primary_screen_rect()
    def _apply_startup_window_state(self) -> None:
        """打开即铺满"窗口所在的那块屏"（视觉等同全屏，任务栏不被遮挡）。

        用工作区尺寸而不是 state("zoomed")：Windows 对最大化的窗口不绘制圆角，
        铺满工作区的普通窗口在 Windows 11 上仍保留圆角，任务栏也不会被盖住。
        """
        area = self._startup_monitor_area()
        if area["width"] < 800 or area["height"] < 500:
            return
        self.root.geometry(
            f"{area['width']}x{area['height']}+{area['left']}+{area['top']}"
        )
    def _sync_ui_scale_to_monitor(self) -> None:
        """按主窗口所在显示器的 DPI 重设界面缩放，必须在建界面之前调用。

        Tk 启动时的 scaling 取自主显示器。笔记本 200% + 外接屏 100% 时窗口虽然
        铺在外接屏上，整套 px() 常量仍按 200% 换算：文字与控件放大成两倍，按钮
        文字被裁、表格列被挤出屏幕，连 minsize 都会超过屏幕把窗口顶成满屏且缩不
        回去。这里在铺满目标屏之后、建界面之前按真实 DPI 纠正一次。
        """
        self.root.update_idletasks()
        try:
            dpi = get_window_dpi(self.root.winfo_id())
        except (AttributeError, tk.TclError, OSError, ValueError):
            return
        if dpi <= 0:
            return
        self.root.tk.call("tk", "scaling", max(1.0, dpi / 72.0))
        set_ui_scale(self.root)
        dialogs_ui.set_ui_scale(self.root)
        # 主题里的字号、行高、内边距都按 px() 算过一遍，缩放变了必须重配。
        self._configure_dark_theme()
        self.root.minsize(px(MIN_MAIN_WIDTH), px(MIN_MAIN_HEIGHT))
    def _current_main_window_geometry(self) -> str:
        root = getattr(self, "root", None)
        if root is None:
            return str(getattr(self, "app_settings", {}).get("main_window_geometry", "") or "").strip()
        try:
            geometry = str(root.geometry()).strip()
        except (AttributeError, tk.TclError, ValueError):
            geometry = ""
        return geometry or str(
            getattr(self, "app_settings", {}).get("main_window_geometry", "") or "",
        ).strip()
    def _editor_draft_snapshot(self) -> dict | None:
        """Capture the complete current editor state without overwriting its file."""
        script = getattr(self, "script", None)
        if not isinstance(script, MacroScript):
            return None
        snapshot = copy.deepcopy(script)
        name_var = getattr(self, "script_name_var", None)
        if name_var is not None:
            name = str(name_var.get()).strip()
            if name:
                snapshot.name = name
        snapshot.settings = self._current_script_settings()
        category_var = getattr(self, "script_category_var", None)
        category_label = category_var.get() if category_var is not None else "关卡"
        snapshot.settings["category"] = script_category_key(category_label)
        snapshot.is_global = is_global_script(snapshot.to_dict())
        return {
            "script": snapshot.to_dict(),
            "script_path": display_path(self.script_path) if self.script_path else "",
            "script_requires_new_file": bool(getattr(self, "script_requires_new_file", False)),
            "dirty": bool(getattr(self, "dirty", False)),
        }
    def _remember_activation_draft(self) -> None:
        """Remember an explicit sidebar change independently from script loading."""
        self.activation_draft_enabled = bool(self.activation_enabled_var.get())
        self.activation_draft_signature = (
            dict(self.saved_activation_signature) if self.saved_activation_signature else None
        )
    def _collect_sidebar_settings(self) -> dict:
        try:
            interval = max(10, min(500, int(self.interval_var.get())))
        except (tk.TclError, TypeError, ValueError):
            interval = DEFAULT_MOUSE_MOVE_INTERVAL_MS
        try:
            repeat = max(1, min(999999, int(self.repeat_var.get())))
        except (tk.TclError, TypeError, ValueError):
            repeat = 1
        backup_interval = self.backup_interval_var.get()
        if backup_interval not in BACKUP_INTERVAL_CHOICES:
            backup_interval = "1h"
        self.interval_var.set(interval)
        self.repeat_var.set(repeat)
        self.backup_interval_var.set(backup_interval)
        return {
            "sound_enabled": bool(self.sound_enabled_var.get()),
            "mini_window_enabled": bool(self.mini_window_enabled_var.get()),
            "execution_mini_enabled": bool(self.execution_mini_enabled_var.get()),
            "playback_speed": round(float(self.playback_speed_var.get()), 1),
            "execution_mini_position": list(getattr(self, "execution_mini_position", [])),
            "main_window_geometry": self._current_main_window_geometry(),
            "record_mode": "auto",
            "focus_mode_enabled": bool(self.focus_mode_enabled_var.get()),
            "activate_target_enabled": bool(self.activate_target_enabled_var.get()),
            "resolution_styles": [
                dict(style) for style in getattr(
                    self, "resolution_styles",
                    resolution_styles_from_settings(getattr(self, "app_settings", {})),
                )
            ],
            "floating_notice_position": self.floating_notice_position_var.get(),
            "close_action": self.close_action_var.get(),
            "repeat": repeat,
            "bound_window": self.saved_window_signature,
            "activation_window_draft_enabled": bool(getattr(
                self, "activation_draft_enabled", self.activation_enabled_var.get(),
            )),
            "activation_window_draft": (
                dict(self.activation_draft_signature)
                if getattr(self, "activation_draft_signature", None) else None
            ),
            "workflow_draft": self._workflow_snapshot(),
            "workflow_path": display_path(self.workflow_path) if self.workflow_path else "",
            "timed_backup_enabled": bool(self.timed_backup_enabled_var.get()),
            "backup_interval": backup_interval,
            "windows_startup_enabled": bool(self.windows_startup_enabled_var.get()),
            "start_minimized_to_tray": bool(self.start_minimized_to_tray_var.get()),
            "startup_run_workflow": bool(self.startup_run_workflow_var.get()),
            "startup_workflow_path": self.startup_workflow_path_var.get().strip(),
            "level_scripts_dir": self.level_scripts_dir_var.get().strip() or "scripts/关卡",
            "level_pack_scripts_dir": self.level_pack_scripts_dir_var.get().strip() or "scripts/关卡封装",
            "switch_scripts_dir": self.switch_scripts_dir_var.get().strip() or "scripts/切换",
            "direction_scripts_dir": self.direction_scripts_dir_var.get().strip() or DIRECTION_SCRIPTS_DIR,
            # 脚本编辑页当前打开的脚本：每次持久化（含关闭应用）都记录，
            # 下次启动时自动恢复；编辑器无脚本（新建/关闭/录制分离）时记为 ""。
            "last_script_path": display_path(self.script_path) if self.script_path else "",
            "editor_draft": self._editor_draft_snapshot(),
            "hotkey_scripts": list(getattr(self, "hotkey_scripts", [])),
            "game_setup_note": getattr(self, "_game_setup_note", None),
        }
    def _workflow_snapshot(self) -> dict:
        self.workflow.name = self.workflow_name_var.get().strip() or "未命名工作流"
        self.workflow.start_at = self.workflow_start_var.get().strip()
        self._read_workflow_start_delay(validate=False)
        return self.workflow.to_dict()
    def _persist_workflow_draft(self) -> None:
        self.workflow_draft_after_id = None
        self._persist_sidebar_settings()
    def _schedule_workflow_draft_save(self, _event=None) -> None:
        if self.workflow_draft_after_id is not None:
            self.root.after_cancel(self.workflow_draft_after_id)
        self.workflow_draft_after_id = self.root.after(350, self._persist_workflow_draft)
    def _persist_sidebar_settings(self, show_feedback: bool = False) -> bool:
        self.app_settings = self._collect_sidebar_settings()
        try:
            save_app_settings(self.app_settings)
        except OSError as exc:
            self._log(f"保存应用设置失败：{exc}")
            if show_feedback:
                self._notify("保存失败", str(exc))
            return False
        if show_feedback:
            self._set_status("左侧配置已保存", "success")
            self._log("已保存左侧配置，下次启动会自动恢复。")
        return True
    def save_sidebar_config(self):
        if self._persist_sidebar_settings(show_feedback=True):
            self._sync_windows_startup(log_errors=True)
            self._schedule_timed_backup()
    def open_game_setup_note(self):
        """打开「游戏设置说明」：查看/编辑使用本软件前游戏需要设置的参数。"""
        saved = getattr(self, "_game_setup_note", None)
        note = GameSetupNoteDialog(self.root, saved if isinstance(saved, str) else None).show()
        if note is None:
            return
        self._game_setup_note = note
        if self._persist_sidebar_settings(show_feedback=True):
            self._set_status("游戏设置说明已保存", "success")
            self._log("游戏设置说明已保存。")
        else:
            self._set_status("游戏设置说明保存失败", "danger")
    def _refresh_resolution_styles_summary(self) -> None:
        names = [
            f"{str(style.get('name', '')).strip()} {int(style.get('scale_percent', 100) or 100)}%"
            for style in self.resolution_styles
            if str(style.get("name", "")).strip()
        ]
        if len(names) <= 3:
            summary = "、".join(names) or "暂无样式"
        else:
            summary = "、".join(names[:3]) + f" 等 {len(names)} 个"
        self.resolution_styles_summary_var.set(summary)
    def _configure_resolution_styles(self):
        result = ResolutionStylesDialog(
            self.root, {"resolution_styles": self.resolution_styles},
        ).show()
        if result is None:
            return
        self.resolution_styles = result
        self._refresh_resolution_styles_summary()
        if self._persist_sidebar_settings(show_feedback=True):
            self._set_status("分辨率样式已保存", "success")
            self._log(f"已保存 {len(result)} 个分辨率样式。")
    def _settings_changed(self, _event=None):
        self._persist_sidebar_settings()
        if self.recorder.running:
            # 录制期间主窗口一律保持隐藏（避免挡住/录进要录的内容），这个勾选
            # 只切换「要不要留一个悬浮小窗显示已录条数」；主窗口在录制结束后
            # 由 stop_recording 无条件恢复，这里不做恢复。
            if self.mini_window_enabled_var.get():
                self._show_operation_mini("recording")
            else:
                self._hide_recording_mini()
    def _on_playback_speed_changed(self, value: str):
        try:
            speed = max(0.5, min(2.0, round(float(value), 1)))
        except (TypeError, ValueError):
            speed = 1.0
        if abs(float(self.playback_speed_var.get()) - speed) > 0.001:
            self.playback_speed_var.set(speed)
        self.playback_speed_label_var.set(f"{speed:.1f}×")
        self.player.set_playback_speed(speed)
        self.hotkey_player.set_playback_speed(speed)
    def _startup_backup_settings_changed(self):
        self._persist_sidebar_settings()
        self._sync_windows_startup(log_errors=True)
        self._schedule_timed_backup()
    def _sync_windows_startup(self, *, log_errors: bool = False) -> bool:
        try:
            set_windows_startup(bool(self.windows_startup_enabled_var.get()))
            return True
        except OSError as exc:
            if log_errors:
                self._log(f"同步开机自启动设置失败：{exc}")
            return False
    def _choose_startup_workflow(self):
        current = self.startup_workflow_path_var.get().strip()
        current_path = resolve_path(current) if current else WORKFLOWS_DIR
        initial_dir = current_path.parent if current and current_path.parent.is_dir() else WORKFLOWS_DIR
        path = filedialog.askopenfilename(
            parent=self.root, initialdir=initial_dir, title="选择启动时执行的工作流",
            filetypes=[("MacroFlow 工作流", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self.startup_workflow_path_var.set(display_path(path))
        self.startup_run_workflow_var.set(True)
        self._startup_backup_settings_changed()
    def _schedule_timed_backup(self):
        if self.backup_after_id is not None:
            try:
                self.root.after_cancel(self.backup_after_id)
            except tk.TclError:
                pass
            self.backup_after_id = None
        if self.exiting or not self.timed_backup_enabled_var.get():
            return
        interval = self.backup_interval_var.get()
        delay_ms = BACKUP_INTERVAL_MS.get(interval, BACKUP_INTERVAL_MS["1h"])
        self.backup_after_id = self.root.after(delay_ms, self._run_timed_backup)
    def _run_timed_backup(self):
        self.backup_after_id = None
        self._schedule_timed_backup()
        if self.exiting or self.backup_running:
            return
        roots = {
            SCRIPTS_DIR.resolve(), self._level_scripts_dir().resolve(),
            self._level_pack_scripts_dir().resolve(), self._switch_scripts_dir().resolve(),
        }
        current_path = self.script_path.resolve() if self.script_path else None
        current_snapshot = self.script.to_dict() if current_path else None
        self.backup_running = True

        def backup_worker():
            try:
                _backup_once()
            except Exception as exc:  # 备份线程异常不能让定时备份永久停摆
                self._ui(self._log, f"定时备份异常：{exc}")
            finally:
                self.backup_running = False

        def _backup_once():
            backed_up = 0
            errors: list[str] = []
            files: dict[str, Path] = {}
            for root in roots:
                if not root.is_dir():
                    continue
                try:
                    for path in root.rglob("*.json"):
                        if path.is_file():
                            files[str(path.resolve()).casefold()] = path.resolve()
                except OSError as exc:
                    errors.append(f"{root}: {exc}")
            if current_path:
                files[str(current_path).casefold()] = current_path
            for key, path in files.items():
                try:
                    snapshot = current_snapshot if current_path and key == str(current_path).casefold() else None
                    backup_script(path, snapshot)
                    backed_up += 1
                except (OSError, ValueError, TypeError) as exc:
                    errors.append(f"{path}: {exc}")
            if self.exiting:
                return
            self._ui(self._log, f"定时备份完成：已覆盖 {backed_up} 个脚本的单份备份。")
            if errors:
                self._ui(self._log, f"定时备份失败 {len(errors)} 项：{errors[0]}")

        threading.Thread(target=backup_worker, name="MacroFlowScriptBackup", daemon=True).start()
    def _run_configured_startup_workflow(self):
        raw_path = self.startup_workflow_path_var.get().strip()
        if not raw_path:
            self._log("启动工作流未执行：尚未选择工作流文件。")
            return
        path = resolve_path(raw_path)
        try:
            self.workflow = load_workflow(path)
            self.workflow_path = path
            self._clear_workflow_delete_history()
            self.workflow_name_var.set(self.workflow.name)
            self.workflow_start_var.set(self.workflow.start_at)
            self.workflow_start_delay_enabled_var.set(self.workflow.start_delay_enabled)
            self.workflow_start_delay_seconds_var.unit.set("ms")
            self.workflow_start_delay_seconds_var.set(
                str(int(self.workflow.start_delay_seconds) * 1000)
            )
            self._toggle_workflow_start_delay_control(persist=False)
            self.rebuild_workflow_tree()
            self._persist_workflow_draft()
            self._log(f"启动时自动执行工作流：{path}")
            self.run_workflow()
        except Exception as exc:
            self._log(f"启动工作流执行失败：{path}：{exc}")
    def _toggle_target_activation(self):
        """Toggle execution focus without changing the persisted target binding."""
        self._persist_sidebar_settings()
        if self.activate_target_enabled_var.get():
            self._log("已启用执行时前置目标窗口；保留当前目标窗口绑定。")
        else:
            self._log("已停用执行时前置目标窗口；目标窗口绑定仍然保留，可随时重新启用。")
    def _toggle_locked_spinbox(self, spin, button, on_save):
        """Toggle a millisecond setting between locked and editable states."""
        if button.cget("text") == "修改":
            button.configure(text="保存")
            spin.configure(state="normal")
            spin.focus_set()
        else:
            button.configure(text="修改")
            spin.configure(state="disabled")
            on_save()
