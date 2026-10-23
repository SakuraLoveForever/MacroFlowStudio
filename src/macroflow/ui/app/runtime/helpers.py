from __future__ import annotations

from macroflow.core.models import MacroScript
from macroflow.core.storage import display_path, DIRECTION_SCRIPTS_DIR, save_app_settings
from datetime import datetime
from macroflow.ui.dialogs import base as dialogs_ui
from macroflow.input.wininput import get_cursor_pos, get_monitor_rect_for_window, get_monitor_work_area_for_point, get_monitor_work_area_for_window, get_primary_screen_rect, get_virtual_screen_rect, get_window_dpi, is_window
import re
from macroflow.core.resolution import resolution_styles_from_settings
import threading
import time
import tkinter as tk

from ..base import coordinate_scale_summary, px, set_ui_scale
from ..constants import EVENT_LOG_HEADER, MIN_MAIN_HEIGHT, MIN_MAIN_WIDTH, TARGET_MAIN_HEIGHT, TARGET_MAIN_WIDTH, TRACE_LOG_HEADER
from ..summaries import action_detail

class RuntimeHelpersMixin:
    """Shared helpers behavior used by both clients."""

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

    def _global_event(self, scope: str, text: str) -> None:
        """Send one global-module event to its own log and pane."""
        line = f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} | {text}\n"
        path = getattr(self, "global_log_path", None)
        if path is not None:
            lock = getattr(self, "global_file_lock", None)
            if lock is None:
                lock = self.global_file_lock = threading.Lock()
            try:
                with lock:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a", encoding="utf-8") as output:
                        output.write(f"[{scope}] {line}")
            except OSError:
                pass
        self._queue_global_view_line(line, scope)

    def _guard_global_event(self, guard: dict, text: str) -> None:
        scope = "workflow" if str(guard.get("key", "")).startswith("workflow:") else "script"
        self._global_event(scope, text)

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
        state = self.__dict__.get('_script_save_state')
        if state is not None:
            state.refresh()

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
            script.settings["activation_window_interval_ms"] = int(
                app_settings.get("activation_window_draft_interval_ms", 0)
            )
        return script

    def _restore_main_window_geometry(self) -> None:
        """只恢复位置：窗口大小固定，不再沿用上次保存的尺寸。"""
        geometry = str(getattr(self, "app_settings", {}).get("main_window_geometry", "") or "").strip()
        if not geometry:
            return
        try:
            match = re.fullmatch(r"(\d+)x(\d+)[+-](-?\d+)[+-](-?\d+)", geometry)
            if not match:
                return
            left, top = (int(value) for value in match.group(3, 4))
            screen = get_virtual_screen_rect()
            screen_left = int(screen.get("left", 0))
            screen_top = int(screen.get("top", 0))
            screen_width = max(1, int(screen.get("width", 0)))
            screen_height = max(1, int(screen.get("height", 0)))
            width = min(px(TARGET_MAIN_WIDTH), screen_width)
            height = min(px(TARGET_MAIN_HEIGHT), screen_height)
            # 显示器配置变化后，旧位置可能落在已断开的显示器上：把固定大小的
            # 窗口整体夹回当前虚拟桌面内，而不是把窗口缩成别的尺寸。
            left = max(screen_left, min(left, screen_left + screen_width - width))
            top = max(screen_top, min(top, screen_top + screen_height - height))
            self.root.geometry(f"{width}x{height}+{left}+{top}")
        except (AttributeError, tk.TclError, ValueError):
            return

    def _watch_display_dpi(self) -> None:
        """显示器缩放或工作区变化后，重刷界面并让窗口重新适配。"""
        if getattr(self, "exiting", False):
            return
        try:
            hwnd = self.root.winfo_id()
            dpi = get_window_dpi(hwnd)
        except (AttributeError, tk.TclError, OSError, ValueError):
            hwnd = None
            dpi = 0
        area = get_monitor_work_area_for_window(hwnd) if hwnd else None
        previous_area = getattr(self, "_display_work_area", None)
        previous_dpi = getattr(self, "_display_dpi", None)
        display_changed = (
            area is not None and previous_area is not None and area != previous_area
        ) or bool(dpi and previous_dpi is not None and dpi != previous_dpi)
        # 固定像素布局需要足够的物理屏幕空间；高缩放配低分辨率时按工作区收敛。
        target_dpi = self._fitted_display_dpi(dpi, area)
        if area is not None:
            area = dict(area)
            self._display_work_area = area
        if dpi:
            self._display_dpi = dpi
        # 不同显示器可能收敛到相同缩放，仍需按新的工作区重建固定像素布局。
        if target_dpi and (display_changed or target_dpi != self._ui_scaling_dpi()):
            self._apply_display_dpi(target_dpi)
            if area is not None:
                # 缩放变了，固定尺寸窗口的物理像素尺寸也要跟着变。
                try:
                    self.root.geometry(self._fixed_main_geometry(area))
                except (tk.TclError, ValueError, OSError):
                    pass
        if display_changed and area is not None:
            self._adapt_execution_mini_position(area)
        self.root.after(600, self._watch_display_dpi)

    def _fitted_display_dpi(self, dpi: int, area: dict | None) -> int:
        if not dpi or not area:
            return dpi
        # 常规屏幕保留舒适缩放，小屏按紧凑布局收敛，并预留系统标题栏。
        fit = min(area["width"] / 1920, area["height"] / 1080)
        compact_fit = min(
            area["width"] / TARGET_MAIN_WIDTH,
            max(1, area["height"] - 32) / TARGET_MAIN_HEIGHT,
        )
        return min(dpi, max(72, round(96 * min(max(1, fit), compact_fit))))

    def _ui_scaling_dpi(self) -> int:
        """Tk 当前 scaling 对应的 DPI——px() 就是按它换算的。"""
        try:
            return int(round(float(self.root.tk.call("tk", "scaling")) * 72))
        except (AttributeError, tk.TclError, ValueError):
            return 0

    def _apply_display_dpi(self, dpi: int) -> None:
        """按适合当前屏幕的 DPI 重建固定像素布局。"""
        self.root.tk.call("tk", "scaling", max(1.0, dpi / 72.0))
        set_ui_scale(self.root)
        dialogs_ui.set_ui_scale(self.root)
        self._configure_dark_theme()
        self.root.minsize(px(MIN_MAIN_WIDTH), px(MIN_MAIN_HEIGHT))
        self._rebuild_ui_for_display_change()
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

    def _fixed_main_geometry(self, area: dict | None = None) -> str:
        """固定紧凑窗口：在给定显示器工作区居中，不随屏幕大小铺满。

        尺寸按 96 DPI 设计、随 DPI 缩放；极端小屏（物理工作区比设计尺寸还小）
        时收敛到工作区内，保证窗口始终完整可见。
        """
        area = dict(area or self._startup_monitor_area())
        width = min(px(TARGET_MAIN_WIDTH), max(1, int(area["width"])))
        height = min(px(TARGET_MAIN_HEIGHT), max(1, int(area["height"]) - 32))
        left = area["left"] + max(0, (area["width"] - width) // 2)
        top = area["top"] + max(0, (area["height"] - 32 - height) // 3)
        return f"{width}x{height}+{left}+{top}"

    def _apply_startup_window_state(self) -> None:
        """固定大小的紧凑窗口：开在窗口所在显示器中间偏上，不铺满屏幕。

        主界面按 96 DPI 设计成固定尺寸，用户不需要全屏也不需要拖拽拉伸；
        在不同分辨率下由 DPI 适配算法收敛，窗口始终保持同一套布局。
        """
        area = self._startup_monitor_area()
        self._display_work_area = dict(area)
        self.root.geometry(self._fixed_main_geometry(area))

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
        area = get_monitor_work_area_for_window(self.root.winfo_id())
        dpi = self._fitted_display_dpi(dpi, area)
        self.root.tk.call("tk", "scaling", max(1.0, dpi / 72.0))
        set_ui_scale(self.root)
        dialogs_ui.set_ui_scale(self.root)
        # 主题里的字号、行高、内边距都按 px() 算过一遍，缩放变了必须重配。
        self._configure_dark_theme()
        self.root.minsize(px(MIN_MAIN_WIDTH), px(MIN_MAIN_HEIGHT))
        if area:
            try:
                self.root.geometry(self._fixed_main_geometry(area))
            except (tk.TclError, ValueError, OSError):
                pass

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

    def _remember_activation_draft(self) -> None:
        """Remember an explicit sidebar change independently from script loading."""
        self.activation_draft_enabled = bool(self.activation_enabled_var.get())
        self.activation_draft_signature = (
            dict(self.saved_activation_signature) if self.saved_activation_signature else None
        )
        self.activation_draft_interval_ms = int(
            self.script.settings.get("activation_window_interval_ms", 0)
        )

    def _collect_sidebar_settings(self) -> dict:
        try:
            repeat = max(1, min(999999, int(self.repeat_var.get())))
        except (tk.TclError, TypeError, ValueError):
            repeat = 1
        self.repeat_var.set(repeat)
        return {
            "sound_enabled": bool(self.sound_enabled_var.get()),
            "mini_window_enabled": bool(self.mini_window_enabled_var.get()),
            "execution_mini_enabled": bool(self.execution_mini_enabled_var.get()),
            "execution_mini_position": list(getattr(self, "execution_mini_position", [])),
            "main_window_geometry": self._current_main_window_geometry(),
            "record_mode": "auto",
            "focus_mode_enabled": bool(self.focus_mode_enabled_var.get()),
            "activate_target_enabled": bool(self.activate_target_enabled_var.get()),
            "partial_script_globals": bool(self.partial_script_globals_var.get()),
            "partial_workflow_globals": bool(self.partial_workflow_globals_var.get()),
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
            "activation_window_draft_interval_ms": int(
                getattr(self, "activation_draft_interval_ms", 0)
            ),
            "workflow_draft": self._workflow_snapshot(),
            "workflow_path": display_path(self.workflow_path) if self.workflow_path else "",
            "level_scripts_dir": self.level_scripts_dir_var.get().strip() or "scripts/关卡",
            "level_pack_scripts_dir": self.level_pack_scripts_dir_var.get().strip() or "scripts/关卡封装",
            "switch_scripts_dir": self.switch_scripts_dir_var.get().strip() or "scripts/切换",
            "direction_scripts_dir": self.direction_scripts_dir_var.get().strip() or DIRECTION_SCRIPTS_DIR,
            # 脚本编辑页当前打开的脚本：每次持久化（含关闭应用）都记录，
            # 下次启动时自动恢复；编辑器无脚本（新建/关闭/录制分离）时记为 ""。
            "last_script_path": display_path(self.script_path) if self.script_path else "",
            "hotkey_scripts": list(getattr(self, "hotkey_scripts", [])),
            "game_setup_note": getattr(self, "_game_setup_note", None),
        }

    def _workflow_snapshot(self) -> dict:
        self.workflow.name = self.workflow_name_var.get().strip() or "未命名工作流"
        self.workflow.start_at = self.workflow_start_var.get().strip()
        return self.workflow.to_dict()

    def _persist_workflow_draft(self, *, record_history: bool = True) -> None:
        if self.workflow_draft_after_id is not None:
            self.root.after_cancel(self.workflow_draft_after_id)
        self.workflow_draft_after_id = None
        self._persist_sidebar_settings()
        record = getattr(self, '_record_workflow_edit' if record_history else '_clear_workflow_history', None)
        if record is not None:
            record()

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

    def _settings_changed(self, _event=None):
        state = self.__dict__.get('_sidebar_save_state')
        if state is not None:
            state.refresh()
        if self.recorder is not None and self.recorder.running:
            # 录制期间主窗口一律保持隐藏（避免挡住/录进要录的内容），这个勾选
            # 只切换「要不要留一个悬浮小窗显示已录条数」；主窗口在录制结束后
            # 由 stop_recording 无条件恢复，这里不做恢复。
            if self.mini_window_enabled_var.get():
                self._show_operation_mini("recording")
            else:
                self._hide_recording_mini()
        return True

    def _toggle_target_activation(self):
        """Toggle execution focus without changing the persisted target binding."""
        self._settings_changed()
        if self.activate_target_enabled_var.get():
            self._log("已启用执行时前置目标窗口；保留当前目标窗口绑定。")
        else:
            self._log("已停用执行时前置目标窗口；目标窗口绑定仍然保留，可随时重新启用。")

    def _toggle_locked_spinbox(self, spin, button, on_save):
        """Toggle a millisecond setting between locked and editable states."""
        if button.cget("text") == "修改":
            from macroflow.ui.save_state import SaveState
            previous = spin.get()
            state = spin.__dict__.get('_save_state')
            if state is not None:
                state.close()
            button.configure(text="保存")
            spin.configure(state="normal")
            spin.focus_set()
            spin._save_state = SaveState(spin, button,
                lambda: button.cget('text') != '保存' or spin.get() != previous, baseline=False)
            def cancel(_event=None):
                spin.delete(0, 'end')
                spin.insert(0, previous)
                spin.configure(state='disabled')
                button.configure(text='修改')
                spin._save_state.refresh()
            spin.bind('<Escape>', cancel)
        else:
            if on_save() is False:
                return
            button.configure(text="修改")
            spin.configure(state="disabled")
            state = spin.__dict__.get('_save_state')
            if state is not None:
                state.refresh()

