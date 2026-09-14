from __future__ import annotations

from macroflow.execution.player import (
    JUMP_CURRENT_SCRIPT_LAST_RESULT, MAX_SCRIPT_REF_DEPTH,
    AdvanceToNextWorkflowStep, EndCurrentScriptRequest, GuardJumpRequest,
    JumpToCurrentScriptLastAction, MacroPlayer, PlaybackStopped,
    screen_template_scale,
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
from macroflow.execution.detection_worker import DetectionEvaluation, DetectionWorker
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
from macroflow.core.image_match import capture_bgr, find_template, find_template_in_image
from datetime import datetime
from macroflow.core.ocr import (
    _get_engine, find_expected_match, format_ocr_observation, matches_expected,
    ocr_match_center, recognize_image_with_boxes, recognize_region_with_boxes,
    set_progress_callback,
)
import os
from macroflow.core.alerts import play_alert, prewarm_alert
from macroflow.ui.detect_overlay import show_overlay
import threading
import time
import tkinter as tk
import ttkbootstrap as ttk

from .base import (
    pad,
    px,
    workflow_execution_progress,
)
from .constants import (
    COLOR_BG,
    COLOR_GREEN,
    COLOR_RED,
    COLOR_SURFACE_ALT,
    COLOR_TEXT,
    FONT_BODY,
    FONT_FAMILY,
)

class GuardsMixin:
    """守卫引擎：共享截图、图片 / OCR / 数字条件评估与命中构造。"""

    @staticmethod
    def _ocr_region_in_frame(screen, origin, region) -> tuple:
        """从共享截图帧中切出 OCR 区域（含坐标换算）；region 为空返回全帧。"""
        if screen is None or origin is None:
            return None, None
        if region:
            left, top, width, height = (int(part) for part in region)
            x1 = max(0, left - int(origin[0]))
            y1 = max(0, top - int(origin[1]))
            x2 = min(screen.shape[1], x1 + max(1, width))
            y2 = min(screen.shape[0], y1 + max(1, height))
            if x2 <= x1 or y2 <= y1:
                return None, None
            return screen[y1:y2, x1:x2], (int(origin[0]) + x1, int(origin[1]) + y1)
        return screen, (int(origin[0]), int(origin[1]))
    def _guard_check_interval(self, guard: dict) -> float:
        """守卫最小检查间隔（秒）：文字 OCR 单次较贵，下限 500ms。"""
        interval = max(100, int(guard.get("interval_ms", 500)))
        if str(guard.get("recognize", "")) == "text":
            interval = max(interval, 500)
        return interval / 1000.0
    def _evaluate_global_guards(self) -> dict | None:
        worker = getattr(self, "_detection_worker", None)
        if worker is None:
            evaluation = self._evaluate_global_guards_sync()
            if isinstance(evaluation, DetectionEvaluation):
                self._consume_detection_events(evaluation.deferred_events)
                return evaluation.hit
            return evaluation
        if getattr(self, "exiting", False):
            return None
        result = worker.poll()
        if result is not None:
            self._detection_request = None
            if (result.run_id != getattr(self, "_detection_run_id", 0)
                    or result.config_version != getattr(self, "_guard_config_version", 0)):
                # 守卫状态已在检测线程里推进（triggered/awaiting_clear/重臂锁），
                # 结果却不能交付：必须回滚，否则目标一直可见时该守卫在本次执行
                # 剩余时间里静默失效（命中被丢掉，日志里也没有任何痕迹）。
                self._rollback_detection_hit(result.hit)
                result = None
            elif result.error is not None:
                self._ui(self._log, f"全局检测失败：{result.error}")
                result = None
            else:
                if isinstance(result.hit, dict) and "hwnd" in result.hit \
                        and result.hit.get("hwnd") is None:
                    result.hit["hwnd"] = self._bound_hwnd(update_display=False)
                self._consume_detection_events(result.deferred_events)
                return result.hit
        player = getattr(self, "player", None)
        if player is None or player.stop_event.is_set():
            return None
        if self._detection_request is None:
            run_id = getattr(self, "_detection_run_id", 0)
            config_version = getattr(self, "_guard_config_version", 0)
            worker.submit(run_id, config_version)
            self._detection_request = (run_id, config_version)
        return None
    def _rollback_detection_hit(self, hit) -> None:
        """Undo the guard state advanced by one detection result we cannot deliver."""
        if not isinstance(hit, dict):
            return
        key = str(hit.get("guard_key", "")).strip()
        guards = getattr(self, "global_guards", None)
        if not key or guards is None:
            return
        with self.guards_lock:
            guard = guards.get(key)
            if guard is None:
                return
            guard["triggered"] = False
            guard["timeout_triggered"] = False
            guard["awaiting_clear"] = False
            guard["awaiting_clear_logged"] = False
            locks = getattr(self, "global_detect_rearm_locks", None)
            if locks is not None:
                locks.discard(key)
    @staticmethod
    def _defer_detection_event(events: list[dict], kind: str, **payload) -> None:
        events.append({"kind": kind, **payload})
    def _consume_detection_events(self, events) -> None:
        for event in events or ():
            kind = event.get("kind")
            if kind == "log":
                self._ui(self._log, event["text"])
            elif kind == "mini_step":
                self._ui(self._append_mini_step, event["text"])
            elif kind == "overlay":
                show_overlay(event["x"], event["y"], event["width"], event["height"])
            elif kind == "restore_foreground":
                self._restore_workflow_scan_foreground()
            elif kind == "fallback_click":
                self._guard_fallback_click(
                    event["guard"], event["match"], event["fallback_name"],
                )
    def _detection_overlay(self, x, y, width, height) -> None:
        detection_context = getattr(self, "_detection_event_context", None)
        deferred_events = getattr(detection_context, "events", None)
        if deferred_events is not None:
            self._defer_detection_event(
                deferred_events, "overlay", x=x, y=y, width=width, height=height,
            )
            return
        show_overlay(x, y, width, height)
    def _evaluate_global_guards_sync(self, _run_id: int | None = None,
                                     _config_version: int | None = None) -> DetectionEvaluation:
        """守卫引擎单轮评估（播放器线程调用），按顺序返回命中处理段。

        节流未到点的守卫跳过；至少一个守卫到点才截图一次，全部图片守卫
        共享同一帧。普通模块命中一次后等目标消失再重新武装；勾选“直到
        目标消失”的模块则在目标持续存在时按检测间隔反复返回处理段。
        同一帧命中的多个守卫先排队，再由播放器逐个执行。
        """
        if getattr(self, "exiting", False) or getattr(self, "_evaluating_guards", False):
            return DetectionEvaluation()
        player = getattr(self, "player", None)
        if player is None or player.stop_event.is_set():
            pending = getattr(self, "_pending_global_guard_hits", None)
            if pending is not None:
                pending.clear()
            self._pending_global_guard_hits_version = None
            return DetectionEvaluation()
        pending = getattr(self, "_pending_global_guard_hits", None)
        if pending:
            version = (
                getattr(self, "_detection_run_id", 0),
                getattr(self, "_guard_config_version", 0),
            )
            pending_version = getattr(self, "_pending_global_guard_hits_version", None)
            if pending_version is None or pending_version == version:
                return DetectionEvaluation(pending.pop(0))
            pending.clear()
            self._pending_global_guard_hits_version = None
        now = time.perf_counter()
        with self.guards_lock:
            guards = [guard for guard in list(self.global_guards.values())]
        if not guards:
            return DetectionEvaluation()
        due: list[dict] = []
        for guard in guards:
            if now - float(guard.get("last_check_time", 0.0)) < self._guard_check_interval(guard):
                continue
            start_delay = int(guard.get("start_delay_ms", 0))
            if start_delay and (now - float(guard.get("start_delay_since", now))) * 1000 < start_delay:
                continue
            if start_delay and not guard.get("start_delay_done"):
                guard["start_delay_done"] = True
                guard["not_found_since"] = now
            due.append(guard)
            guard["last_check_time"] = now
        if not due:
            return DetectionEvaluation()
        deferred_events: list[dict] = []
        detection_context = getattr(self, "_detection_event_context", None)
        if detection_context is None:
            detection_context = self._detection_event_context = threading.local()
        detection_context.events = deferred_events
        needs_capture = any(str(guard.get("recognize", "")) != "none" for guard in due)
        screen = origin = None
        if needs_capture:
            try:
                screen, origin = capture_bgr()
            except Exception as exc:
                self._defer_detection_event(
                    deferred_events, "log", text=f"全局检测：屏幕截图失败：{exc}",
                )
                detection_context.events = None
                return DetectionEvaluation(None, tuple(deferred_events))
            # 全屏截图偶发会让独占全屏游戏短暂失焦：截图后立即校验并恢复绑定窗口前台。
            self._defer_detection_event(deferred_events, "restore_foreground")
        self._evaluating_guards = True
        hits: list[dict] = []
        try:
            for guard in due:
                hit = self._evaluate_one_guard(guard, screen, origin, now, deferred_events)
                if hit is not None:
                    hits.append(hit)
        finally:
            self._evaluating_guards = False
            detection_context.events = None
        if not hits:
            return DetectionEvaluation(None, tuple(deferred_events))
        if pending is None:
            pending = self._pending_global_guard_hits = []
        pending.extend(hits[1:])
        self._pending_global_guard_hits_version = (
            _run_id if _run_id is not None else getattr(self, "_detection_run_id", 0),
            _config_version if _config_version is not None
            else getattr(self, "_guard_config_version", 0),
        )
        return DetectionEvaluation(hits[0], tuple(deferred_events))
    def _evaluate_one_guard(self, guard: dict, screen, origin, now: float,
                            deferred_events: list[dict] | None = None) -> dict | None:
        deferred_events = deferred_events if deferred_events is not None else []
        if guard.get("module_ref"):
            self._refresh_guard_from_module(guard)
        if guard.get("region_mode") == "window":
            guard["region"] = get_window_rect(self._bound_hwnd(update_display=False))
        recognize = str(guard.get("recognize", ""))
        detected = False
        match = None
        if recognize == "none":
            detected = False
            guard["warned_missing_template"] = False
        elif recognize == "text":
            detected, match = self._guard_text_detect(guard, screen, origin)
        else:
            detected, match = self._guard_image_detect(guard, screen, origin)
        if guard.get("wait_text_absent"):
            if detected:
                if not guard.get("target_absent_armed"):
                    guard["target_absent_armed"] = True
                    self._ui(
                        self._trace_event,
                        f"全局检测：{self._global_monitor_subject(guard, '已识别到目标')}，开始持续执行直到消失。",
                    )
                if match:
                    guard["last_present_match"] = dict(match)
            elif guard.get("target_absent_armed"):
                guard["target_absent_armed"] = False
        fallback_key = str(guard.get("fallback_module_key", "")).strip()
        if not detected and fallback_key:
            fallback_obj = registered_module_object(fallback_key)
            fallback_match = self._guard_fallback_match(guard, fallback_obj, screen, origin)
            if fallback_match and not guard.get("fallback_present"):
                guard["fallback_present"] = True
                fallback_name = str((fallback_obj or {}).get("name") or "备用识别模块")
                self._detection_overlay(
                    fallback_match["x"], fallback_match["y"],
                    fallback_match["width"], fallback_match["height"],
                )
                if guard.get("fallback_click"):
                    self._guard_fallback_click(guard, fallback_match, fallback_name)
                else:
                    self._ui(
                        self._trace_event,
                        f"全局检测：备用模块 {fallback_name} 已识别，继续识别主模块。",
                    )
            elif not fallback_match:
                guard["fallback_present"] = False
        subject = (
            str(guard.get("expected_text", "")).strip() or "识别文字"
            if recognize == "text" else
            "无需识图" if recognize == "none" else guard["template"].name
        )
        condition_subject = self._global_monitor_subject(guard, subject)
        absent_target_name = "期望文字" if recognize == "text" else "目标模板"
        repeat_while_detected = bool(guard.get("wait_text_absent"))
        if guard.get("awaiting_clear") and not repeat_while_detected:
            if detected:
                if not guard.get("awaiting_clear_logged"):
                    guard["awaiting_clear_logged"] = True
                    self._ui(
                        self._trace_event,
                        f"全局检测：{condition_subject} 刚刚已触发，等待消失后再允许下次触发。",
                    )
            else:
                locks = getattr(self, "global_detect_rearm_locks", None)
                if locks is not None:
                    locks.discard(str(guard.get("key", "")))
                guard["awaiting_clear"] = False
                guard["awaiting_clear_logged"] = False
                guard["was_detected"] = False
                guard["triggered"] = False
                guard["match_since"] = None
                guard["not_found_since"] = now
                self._ui(self._log, f"全局检测：{condition_subject} 已确认消失，允许下次触发。")
            return None
        if detected:
            guard["not_found_since"] = None
            guard["timeout_triggered"] = False
            if match:
                # 持续重试时目标位置可能变化，每轮都使用最新命中位置。
                guard["match_data"] = dict(match)
            if not guard.get("was_detected"):
                guard["was_detected"] = True
                guard["match_since"] = now
                if match:
                    self._ui(
                        self._trace_event,
                        f"全局检测：识别到 {condition_subject} @ "
                        f"({match['center_x']}, {match['center_y']})，"
                        + (f"等待持续超过 {guard['hold_ms']} ms 后触发。"
                           if guard.get("hold_enabled", False) else "立即触发。"),
                    )
                    self._detection_overlay(match["x"], match["y"], match["width"], match["height"])
                else:
                    self._ui(
                        self._trace_event,
                        f"全局检测：识别到 {condition_subject}，"
                        + (f"等待持续超过 {guard['hold_ms']} ms 后触发。"
                           if guard.get("hold_enabled", False) else "立即触发。"),
                    )
            hold_ms = guard["hold_ms"] if guard.get("hold_enabled", False) else 0
            elapsed_ms = (now - (guard["match_since"] or now)) * 1000
            if not guard.get("triggered") and elapsed_ms >= hold_ms:
                guard["triggered"] = not repeat_while_detected
                if not repeat_while_detected:
                    guard["awaiting_clear"] = True
                    locks = getattr(self, "global_detect_rearm_locks", None)
                    if locks is None:
                        locks = self.global_detect_rearm_locks = set()
                    locks.add(str(guard.get("key", "")))
                guard["trigger_kind"] = "success"
                self.global_detect_trigger_count += 1
                self._ui(self._log, f"全局检测触发：{condition_subject}。")
                return self._build_guard_hit(guard, resolve_hwnd=False)
        else:
            if guard.get("was_detected"):
                self._ui(
                    self._trace_event,
                    f"全局检测：{self._global_monitor_subject(guard, absent_target_name + '已消失')}，持续触发完成。"
                    if repeat_while_detected else
                    f"全局检测：{self._global_monitor_subject(guard, '图片已消失')}，计时重置。",
                )
            guard["was_detected"] = False
            guard["triggered"] = False
            guard["match_since"] = None
            if guard.get("not_found_since") is None:
                guard["not_found_since"] = now
            timeout_elapsed = (now - guard["not_found_since"]) * 1000
            if (
                guard.get("timeout_enabled")
                and not guard.get("timeout_triggered")
                and timeout_elapsed >= int(
                    guard.get("not_found_timeout_ms", DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS),
                )
            ):
                guard["timeout_triggered"] = True
                guard["trigger_kind"] = "timeout"
                timeout_ms = int(
                    guard.get("not_found_timeout_ms", DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS),
                )
                segment = list(guard.get("timeout_segment") or [])
                self._ui(
                    self._log,
                    f"全局检测：连续 {timeout_ms} ms 未识别到 {condition_subject}，"
                    f"执行超时处理段（{len(segment)} 个动作）。",
                )
                return self._build_guard_hit(guard, resolve_hwnd=False)
        return None
    def _refresh_guard_from_module(self, guard: dict) -> None:
        """引用模块守卫：每轮实时重读模块对象（阈值/间隔/区域/持续时长等）。

        含**命中后动作**：改「识别后的行为」（点击识别区域 / 点击自定义位置 /
        成功后继续）或改自定义点击坐标后，运行中的检测下一轮就用新配置——
        以前这两项只在注册那一刻读一次，运行中改了要等重新开始执行才生效。
        """
        obj = registered_module_object(
            str(guard.get("module_key") or guard.get("template") or ""),
        )
        if obj is None:
            if not guard.get("warned_missing_module"):
                guard["warned_missing_module"] = True
                missing_name = str(
                    guard.get("module_key") or guard.get("template") or "",
                ).replace("\\", "/").rsplit("/", 1)[-1]
                self._ui(self._log, f"全局检测：引用模块 {missing_name} 不存在，沿用当前配置。")
            return
        guard["warned_missing_module"] = False
        try:
            template = str(obj.get("template", "")).strip()
            if template:
                guard["template"] = resolve_path(template)
            guard["threshold"] = max(
                0.1, min(1.0, float(obj.get("threshold", guard["threshold"]))),
            )
            guard["interval_ms"] = max(
                100, min(10000, int(obj.get("interval_ms", guard["interval_ms"]))),
            )
            guard["ignore_background"] = bool(obj.get("ignore_background", False))
            guard["hold_enabled"] = bool(obj.get("hold_enabled", False))
            guard["hold_ms"] = max(0, int(obj.get("hold_ms", guard["hold_ms"])))
            # 命中后、点击前的等待（就是模块表单里的「延时」）：游戏弹窗刚出现那
            # 一瞬间点击常常被吞掉，这个值要能边跑边调（实时重读，见方法注释）。
            guard["delay_ms"] = max(
                0, min(60000, int(obj.get("delay_ms", guard.get("delay_ms", 0)))),
            )
            guard["timeout_enabled"] = bool(obj.get("run_code_on_timeout", False)) and not bool(
                obj.get("wait_text_absent", False)
            )
            guard["not_found_timeout_ms"] = max(
                0, int(obj.get(
                    "not_found_timeout_ms",
                    guard.get("not_found_timeout_ms", DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS),
                )),
            )
            guard["timeout_segment"] = list(obj.get("on_timeout_actions") or [])
            guard["success_segment"] = (
                list(obj.get("on_success_actions") or [])
                if bool(obj.get("run_code_after_action", False)) else []
            )
            guard["recognize"] = str(obj.get("recognize", ""))
            guard["expected_text"] = str(obj.get("expected_text", ""))
            guard["match_mode"] = str(obj.get("match_mode", "contains"))
            guard["wait_text_absent"] = bool(obj.get("wait_text_absent", False))
            guard["click_count"] = max(1, min(9999, int(obj.get("click_count", 1))))
            # 命中后动作与自定义点击位置随模块对象的当前配置走（见方法注释）。
            after_action = str(obj.get("after_action", guard.get("after_action", "click_match")))
            guard["after_action"] = after_action
            guard["button"] = str(obj.get("button", guard.get("button", "left")))
            raw_click = obj.get("click_point") or []
            if after_action == "click_custom" and len(raw_click) == 2:
                guard["click"] = (int(raw_click[0]), int(raw_click[1]))
            else:
                guard["click"] = None
            guard["fallback_module_key"] = str(obj.get("fallback_module_key", "")).strip()
            guard["fallback_on_match"] = str(obj.get("fallback_on_match", "continue")).strip()
            guard["fallback_click"] = bool(obj.get("fallback_click", False))
            guard["fallback_click_count"] = max(
                1, min(9999, int(obj.get("fallback_click_count", 1))),
            )
            guard["fallback_click_interval_ms"] = max(
                0, min(60000, int(obj.get("fallback_click_interval_ms", 100))),
            )
            for field in (
                "ocr_offset_up", "ocr_offset_down", "ocr_offset_left", "ocr_offset_right",
            ):
                guard[field] = max(0, int(obj.get(field, 0)))
            if guard["wait_text_absent"]:
                # 模块对象可在运行中切换为持续重试，不能继承旧的单次触发锁。
                guard["awaiting_clear"] = False
                guard["awaiting_clear_logged"] = False
                guard["triggered"] = False
                locks = getattr(self, "global_detect_rearm_locks", None)
                if locks is not None:
                    locks.discard(str(guard.get("key", "")))
            else:
                guard["target_absent_armed"] = False
        except (TypeError, ValueError):
            pass
        raw_region = obj.get("region") or []
        if len(raw_region) == 4 and raw_region[2] > 0 and raw_region[3] > 0:
            guard["region"] = tuple(int(part) for part in raw_region)
        else:
            guard["region"] = None
    def _guard_text_detect(self, guard: dict, screen, origin) -> tuple[bool, dict | None]:
        """文字守卫：优先在共享帧上切片 OCR，无共享帧时自行截取区域。"""
        # 引擎未就绪时等待加载（可中断轮询）：F12 能中止，不会卡死在导入里。
        if not self._wait_ocr_ready():
            return False, None
        try:
            ocr_screen, ocr_origin = self._ocr_region_in_frame(screen, origin, guard.get("region"))
            if ocr_screen is None:
                recognized, ocr_matches = recognize_region_with_boxes(guard.get("region"))
            else:
                recognized, ocr_matches = recognize_image_with_boxes(ocr_screen, ocr_origin)
        except Exception as exc:
            if not guard.get("warned_find_error"):
                guard["warned_find_error"] = True
                self._ui(self._log, f"全局检测：OCR 识别失败：{exc}")
            return False, None
        guard["warned_find_error"] = False
        expected = str(guard.get("expected_text", ""))
        mode = str(guard.get("match_mode", "contains"))
        match = find_expected_match(ocr_matches, expected, mode)
        present = match is not None
        if not present and matches_expected(recognized, expected, mode):
            present = True
            match = ocr_match_center(guard.get("region"))
        observation = format_ocr_observation(
            recognized, expected, present,
            str(guard.get("expected_text", "")).strip() or "全局文字模块",
        )
        if observation != guard.get("last_ocr_observation"):
            guard["last_ocr_observation"] = observation
            # 逐次识别结果（每次 OCR 识别成什么）属于执行明细，不进事件日志。
            self._ui(self._trace_event, observation)
            self._ui(self._append_mini_step, observation)
        return present, match
    def _guard_template_scale(self) -> float:
        """守卫模板缩放系数：当前正在播放的脚本的录制屏幕 → 当前屏幕宽度比。

        守卫评估发生在播放器线程（动作边界/等待），此时播放器持有当前
        脚本的 recorded_screen；截图尺寸不同时模板需等比缩放再匹配。
        """
        player = getattr(self, "player", None)
        if player is None:
            return 1.0
        return screen_template_scale(
            getattr(player, "_source_screen", None),
            getattr(player, "_target_screen", None),
        )
    def _guard_image_detect(self, guard: dict, screen, origin) -> tuple[bool, dict | None]:
        template = guard["template"]
        if not template.is_file():
            if not guard.get("warned_missing_template"):
                guard["warned_missing_template"] = True
                self._ui(self._log, f"全局检测：模板图片不存在，跳过检测：{template}")
            return False, None
        guard["warned_missing_template"] = False
        try:
            if screen is not None and origin is not None:
                match = find_template_in_image(
                    template, screen, float(guard["threshold"]), origin,
                    guard.get("region"),
                    ignore_background=bool(guard.get("ignore_background", False)),
                    scale=self._guard_template_scale(),
                )
            else:
                match = find_template(
                    template, float(guard["threshold"]), guard.get("region"),
                    ignore_background=bool(guard.get("ignore_background", False)),
                    scale=self._guard_template_scale(),
                )
        except Exception as exc:
            if not guard.get("warned_find_error"):
                guard["warned_find_error"] = True
                self._ui(self._log, f"全局检测：识别失败：{exc}")
            return False, None
        guard["warned_find_error"] = False
        return match is not None, match
    def _guard_fallback_match(self, guard: dict, obj: dict | None, screen, origin) -> dict | None:
        if not obj or obj.get("recognize") in ("number", "none"):
            return None
        raw_region = obj.get("region") or []
        region = (
            tuple(int(part) for part in raw_region)
            if len(raw_region) == 4 and int(raw_region[2]) > 0 and int(raw_region[3]) > 0
            else None
        )
        if obj.get("recognize") == "text":
            # 引擎未就绪时等待加载（可中断轮询）：F12 能中止，不会卡死在导入里。
            if not self._wait_ocr_ready():
                return None
            try:
                ocr_screen, ocr_origin = self._ocr_region_in_frame(screen, origin, region)
                if ocr_screen is None:
                    recognized, boxes = recognize_region_with_boxes(region)
                else:
                    recognized, boxes = recognize_image_with_boxes(ocr_screen, ocr_origin)
            except Exception:
                return None
            expected = str(obj.get("expected_text", ""))
            mode = str(obj.get("match_mode", "contains"))
            match = find_expected_match(boxes, expected, mode)
            if match is None and matches_expected(recognized, expected, mode):
                if region:
                    x, y, width, height = region
                    return {
                        "x": x, "y": y, "width": width, "height": height,
                        "center_x": x + width // 2, "center_y": y + height // 2,
                    }
            return match
        template = resolve_path(str(obj.get("template", "")))
        if not template.is_file():
            return None
        try:
            threshold = min(1.0, max(0.1, float(obj.get("threshold", 0.85))))
            ignore_background = bool(obj.get("ignore_background", False))
            if screen is not None and origin is not None:
                return find_template_in_image(
                    template, screen, threshold, origin, region,
                    ignore_background=ignore_background,
                    scale=self._guard_template_scale(),
                )
            return find_template(
                template, threshold, region,
                ignore_background=ignore_background,
                scale=self._guard_template_scale(),
            )
        except Exception:
            return None
    def _guard_fallback_click(self, guard: dict, match: dict, fallback_name: str) -> None:
        """备用模块命中点击（播放器线程内联，按间隔节流防连点）。"""
        detection_context = getattr(self, "_detection_event_context", None)
        deferred_events = getattr(detection_context, "events", None)
        if deferred_events is not None:
            self._defer_detection_event(
                deferred_events, "fallback_click", guard=dict(guard),
                match=dict(match), fallback_name=fallback_name,
            )
            return
        now = time.perf_counter()
        interval_ms = max(0, int(guard.get("fallback_click_interval_ms", 100)))
        if (now - float(guard.get("fallback_click_since", 0.0))) * 1000 < interval_ms:
            return
        guard["fallback_click_since"] = now
        count = max(1, min(9999, int(guard.get("fallback_click_count", 1))))
        button = str(guard.get("button", "left"))
        hwnd = self._bound_hwnd(update_display=False)
        player = getattr(self, "player", None)
        try:
            if player is not None:
                player._click_module_point(
                    int(match["center_x"]), int(match["center_y"]), button, count, hwnd,
                )
            else:
                send_move_absolute(int(match["center_x"]), int(match["center_y"]))
                for index in range(count):
                    send_button(button, True)
                    time.sleep(0.03)
                    send_button(button, False)
                    if index + 1 < count:
                        time.sleep(interval_ms / 1000)
        except Exception as exc:
            self._ui(self._log, f"全局检测：备用模块点击失败：{exc}")
            return
        self._ui(
            self._log,
            f"全局检测：备用模块 {fallback_name} 已识别并点击，继续识别主模块。",
        )
    def _build_guard_hit(self, guard: dict, *, resolve_hwnd: bool = True) -> dict:
        """把命中的守卫打包成播放器处理段描述（hit）。"""
        hwnd = self._bound_hwnd(update_display=False) if resolve_hwnd else None
        recognize = str(guard.get("recognize", ""))
        subject = (
            str(guard.get("expected_text", "")).strip() or "识别文字"
            if recognize == "text" else
            "无需识图" if recognize == "none" else guard["template"].name
        )
        hit = {
            "kind": str(guard.get("trigger_kind", "success")),
            "guard_key": str(guard.get("key", "")),
            "log_subject": self._global_monitor_subject(guard, subject),
            "delay_ms": int(guard.get("delay_ms", 0)),
            "hwnd": hwnd,
            "match": guard.get("match_data"),
        }
        click = guard.get("click")
        # 旧配置兼容：没有显式点击位置、没有语句体回放时，点击识别到的位置
        # （与识图动作默认行为一致）。配置了跳转目标且跳转已启用时同样点击
        # （旧引擎语义：先点击识别处再跳转）；跳转停用的行是纯触发（不点击）。
        if not click and hit["kind"] == "success" and not guard.get("standalone_replay") \
                and not guard.get("module_ref") and guard.get("match_data"):
            has_jump_target = bool(guard.get("jump_row") or guard.get("jump_action_id"))
            if not has_jump_target or not guard.get("jump_disabled"):
                match = guard["match_data"]
                click = (match["center_x"], match["center_y"])
        # 引用模块守卫：命中后按模块对象配置的“动作”分发。
        # click_custom 的自定义坐标在注册时已写入 guard["click"]；
        # click_match（“点击识别区域”，模块默认动作）补上识别位置
        # （含 OCR 偏移，与旧全局检测引擎行为一致）——否则引用模块
        # 命中后只触发不点击。
        if not click and hit["kind"] == "success" and guard.get("module_ref") \
                and str(guard.get("after_action", "click_match")) == "click_match" \
                and guard.get("match_data"):
            match = guard["match_data"]
            click = (
                match["center_x"] + int(guard.get("ocr_offset_right", 0))
                - int(guard.get("ocr_offset_left", 0)),
                match["center_y"] + int(guard.get("ocr_offset_down", 0))
                - int(guard.get("ocr_offset_up", 0)),
            )
        if click and len(click) == 2:
            hit["click"] = (int(click[0]), int(click[1]))
            hit["button"] = str(guard.get("button", "left"))
            hit["click_count"] = max(1, min(9999, int(guard.get("click_count", 1))))
        second = guard.get("second")
        if second and second.get("template"):
            hit["second"] = {
                "name": str(guard.get("module_display_name", "")).strip() or "全局模块",
                "second_match_template": str(second.get("template", "")),
                "threshold": float(guard.get("threshold", 0.85)),
                "interval_ms": max(50, int(guard.get("interval_ms", 250))),
                "ignore_background": bool(guard.get("ignore_background", False)),
                "blocking": bool(second.get("blocking", False)),
                "second_match_timeout_ms": max(0, int(second.get("timeout_ms", 3000))),
                "second_match_click_target": str(second.get("click_target", "second")),
                "second_match_click_region": second.get("click_region") or [],
                "button": str(guard.get("button", "left")),
                "click_count": max(1, min(9999, int(guard.get("click_count", 1)))),
            }
            hit["match"] = guard.get("match_data")
        if hit["kind"] == "timeout":
            hit["actions"] = list(guard.get("timeout_segment") or [])
        elif guard.get("success_segment"):
            # 引用模块勾选“再执行代码段”时，命中后在主动作之外播放成功代码段。
            # （旧引擎在触发时把 success_segment 搬进 segment 并置 segment_ready；
            # 守卫引擎直接读取实时刷新的 success_segment。）
            hit["actions"] = list(guard["success_segment"])
        replay = guard.get("standalone_replay")
        if replay:
            hit["actions"] = list(replay.get("actions") or [])
            hit["source_screen"] = replay.get("source_screen")
            hit["activation_hwnd"] = replay.get("activation_hwnd")
            hit["activate_target"] = bool(replay.get("activate_target"))
        module = guard.get("module")
        script_value = str((module or {}).get("script", "")).strip()
        if hit["kind"] == "success" and script_value:
            script_path = resolve_path(script_value)
            if script_path.is_file():
                try:
                    script = load_script(script_path)
                    hit["script"] = script_value
                    if bool(script.settings.get("activation_window_enabled", False)):
                        try:
                            hit["activation_hwnd"] = self._execution_activation_hwnd(
                                hwnd, True, script.settings.get("activation_window"),
                            )
                        except RuntimeError:
                            pass
                except Exception:
                    pass
        if hit["kind"] == "success":
            jump_action_id = str(guard.get("jump_action_id", "")).strip()
            if not guard.get("jump_disabled") and (jump_action_id or guard.get("jump_row")):
                hit["jump_action_id"] = jump_action_id
                hit["jump_row"] = max(1, int(guard.get("jump_row", 1)))
                scope_action_ids = guard.get("scope_action_ids")
                if scope_action_ids:
                    hit["scope_action_ids"] = tuple(scope_action_ids)
        self._warn_guard_without_action(guard, hit)
        return hit
    def _warn_guard_without_action(self, guard: dict, hit: dict) -> None:
        """命中后什么都不做时明确说一次，别让「模块名出现了但没点击」无迹可寻。

        引用模块的「识别后的行为」可以是不点击（成功后继续 / 二次识别 /
        只跑代码段）；自定义点击位置没填也一样点不出去。这两种情况下模块照样
        命中、日志照样写“全局检测触发”，用户只会看到“它没点击”。
        """
        if hit.get("kind") != "success":
            return
        after_action = str(guard.get("after_action", "click_match"))
        if str(guard.get("recognize", "")) == "text" and after_action == "second_match":
            # 识别文字方式没有二次图片匹配，编辑窗口会回落到点击识别区域。
            after_action = "click_match"
        if after_action == "click_custom" and not guard.get("click"):
            if not guard.get("warned_missing_click_point"):
                guard["warned_missing_click_point"] = True
                self._ui(
                    self._log,
                    f"全局检测：模块[{guard.get('module_display_name', '')}] 命中但"
                    "自定义点击位置没设置，本次不会点击；请在「模块管理」里补上"
                    "点击位置 (x, y)。",
                )
            return
        if after_action in ("click_match", "click_custom", "second_match"):
            return
        if after_action == "run_actions" or bool(guard.get("run_code_after_action", False)):
            return
        if hit.get("click") or hit.get("second") or hit.get("actions") \
                or hit.get("script") or hit.get("jump_action_id"):
            return
        if not guard.get("warned_no_action"):
            guard["warned_no_action"] = True
            self._ui(
                self._log,
                f"全局检测：模块[{guard.get('module_display_name', '')}] 命中但"
                "「识别后的行为」是不点击（成功后继续），本次只触发不点击；"
                "要点击请把识别后的行为改成「点击识别区域」或「点击自定义位置」。",
            )
    def _invalidate_detection_config(self) -> None:
        self._guard_config_version = getattr(self, "_guard_config_version", 0) + 1
        self._detection_request = None
        pending = getattr(self, "_pending_global_guard_hits", None)
        if pending is not None:
            pending.clear()
        self._pending_global_guard_hits_version = None
    def _clear_global_guards(self) -> None:
        """清空全部守卫（执行开始/结束/停止时）。"""
        self._invalidate_detection_config()
        pending = getattr(self, "_pending_global_guard_hits", None)
        if pending is not None:
            pending.clear()
        guards = getattr(self, "global_guards", None)
        if guards is None:
            return
        lock = getattr(self, "guards_lock", None)
        if lock is not None:
            with lock:
                guards.clear()
        else:
            guards.clear()
    def _clear_global_detect_rearm_locks(self):
        locks = getattr(self, "global_detect_rearm_locks", None)
        if locks is not None:
            locks.clear()
    def _guard_wait(self, seconds: float) -> bool:
        """守卫感知的等待（工作流步骤间隙）：等待期间周期评估守卫并内联执行处理段。

        停止时返回 False；处理段要求结束当前脚本/推进/跳转时，间隙中没有
        脚本上下文可作用，跳过剩余等待并返回 True 继续工作流——不能让守卫
        处理段在步骤间隙静默终止整个工作流。
        """
        deadline = time.perf_counter() + max(0.0, float(seconds))
        while True:
            if self.workflow_stop.is_set() or self.player.stop_event.is_set():
                return False
            hit = self._evaluate_global_guards()
            if hit is not None:
                try:
                    self.player.handle_guard_hit(hit)
                except PlaybackStopped:
                    return False
                except (EndCurrentScriptRequest, AdvanceToNextWorkflowStep,
                        JumpToCurrentScriptLastAction, GuardJumpRequest):
                    self._ui(
                        self._log,
                        "全局检测：处理段请求已生效，跳过剩余等待，继续工作流。",
                    )
                    return True
                continue
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                return True
            self.workflow_stop.wait(min(0.1, remaining))
    def _restore_workflow_scan_foreground(self) -> None:
        """截图轮后校验主绑定窗口仍在前台，截图导致失焦时立即恢复。

        截图本身不改变焦点，但独占全屏游戏对桌面访问敏感，个别客户端
        会因此短暂失焦并弹出“点击游戏画面继续操作”。这里在每次截图后做
        一次廉价的前台校验（GetForegroundWindow 进程比对），只有发现
        失焦才激活，正常时零开销。
        """
        detection_context = getattr(self, "_detection_event_context", None)
        deferred_events = getattr(detection_context, "events", None)
        if deferred_events is not None:
            self._defer_detection_event(deferred_events, "restore_foreground")
            return
        hwnd = self._bound_hwnd(update_display=False)
        if hwnd and not is_window_process_foreground(hwnd):
            activate_window(hwnd)
    def _restart_workflow_resolved_row(self, action: dict) -> int:
        """解析「重新执行工作流」的跳转行：动作 → 工作流默认 → 第 1 行。

        默认跳转行在工作流页面统一设置（随工作流文件保存）；行号是当前
        工作流里的 1 基行号（对应工作流树里的行对象）；越界时由
        _launch_workflow_restart 收敛到首尾。
        """
        try:
            row = max(0, int(action.get("restart_workflow_target_row", 0) or 0))
        except (TypeError, ValueError):
            row = 0
        if not row:
            workflow = getattr(self, "workflow", None)
            try:
                row = max(0, int(getattr(workflow, "restart_default_row", 0) or 0))
            except (TypeError, ValueError):
                row = 0
        return max(1, row)
    def _on_restart_workflow_request(self, action: dict) -> bool:
        if self.current_workflow_step_index is None:
            # 固定特殊动作只对当前工作流生效；独立脚本运行时直接跳过。
            self._ui(
                self._log,
                "特殊模块：当前未在工作流中执行，已跳过“重新执行工作流”。",
            )
            return False
        self.workflow_restart_requested = True
        self.workflow_restart_target_row = self._restart_workflow_resolved_row(action)
        self.workflow_stop.set()
        self.player.stop()
        self._clear_global_guards()
        self._ui(self._poll_workflow_stop_for_restart_workflow)
        return True
    def _poll_workflow_stop_for_restart_workflow(self):
        # F12 紧急停止或退出会清掉 workflow_restart_requested：交接窗口内
        # 必须复查，否则残留的轮询会在 worker 死亡后把工作流重新拉起来。
        if not getattr(self, "workflow_restart_requested", False) \
                or getattr(self, "exiting", False):
            return
        if (self.worker and self.worker.is_alive()) \
                or getattr(getattr(self, "player", None), "running", False) is True:
            self.root.after(100, lambda: self._poll_workflow_stop_for_restart_workflow())
            return
        self._launch_workflow_restart()
    def _launch_workflow_restart(self):
        if not getattr(self, "workflow_restart_requested", False) \
                or getattr(self, "exiting", False):
            return
        steps = self._workflow_only_steps()
        target_row = max(1, int(getattr(self, "workflow_restart_target_row", 1) or 1))
        target_index = min(target_row, len(steps)) - 1 if steps else 0
        self.workflow_restart_requested = False
        self.workflow_restart_target_row = 1
        self.rebuild_workflow_tree()
        self._persist_workflow_draft()
        target_text = f"第 {target_index + 1} 行" if steps else "工作流开头"
        self._log(f"特殊模块：重新执行工作流，跳转到{target_text}。")
        self._append_mini_step(f"特殊模块：重新执行工作流，跳转到{target_text}。")
        self.run_workflow(
            start_index=target_index, start_repeat=0, resume_action_index=0,
            preserve_global_rearm_locks=True, suppress_start_sound=True,
        )
    def _record_workflow_action(self, next_index):
        """Record the next action index of the current script (player thread)."""
        self.current_workflow_action_index = max(0, int(next_index))
    def _record_workflow_repeat(self, current, total, number, count, name):
        """Record the repeat index about to run, then update the progress label."""
        self.current_workflow_repeat_index = max(0, current - 1)
        self._set_trace_context(
            step=number, steps=count, script=name, repeat=current, repeats=total,
        )
        self._ui(
            self._set_execution_progress,
            workflow_execution_progress(number, count, name, total, current),
        )
    def _sound(self, name: str):
        play_alert(name, bool(self.sound_enabled_var.get()))
    def test_sound(self):
        if not self.sound_enabled_var.get():
            self._set_status("提示音已关闭", "warning")
            self._log("测试提示音未播放：请先勾选“快捷键提示音”。")
            return
        self._sound("record_start")
        self._set_status("正在播放测试提示音", "success")
    def _show_recording_mini(self):
        # 无论是否显示悬浮小窗，录制期间主窗口都要让开屏幕（见 _hide_main_for_recording）。
        must_show_mini = self._hide_main_for_recording()
        if not (self.mini_window_enabled_var.get() or must_show_mini):
            return
        self._show_operation_mini("recording")
    def _show_execution_mini(self):
        if not self.execution_mini_enabled_var.get():
            return
        self._show_operation_mini("execution")
    def _show_operation_mini(self, mode: str):
        if self.mini_window and self.mini_window.winfo_exists() and self.mini_mode == mode:
            # 窗口创建后一直保持可见并置顶，无需重新显示。Tk 的 deiconify 会
            # Restack 激活（SetWindowPos 不带 SWP_NOACTIVATE），抢走游戏前台
            # 导致游戏退全屏，所以这里不做任何激活性操作。
            if not self.mini_window.winfo_ismapped():
                show_window_no_activate(self.mini_window.winfo_id())
            # 全局模块中断期间旧 worker 会结束，原刷新循环随之停下；断点恢复
            # 复用同一个小窗时必须重新启动刷新，否则时间会永远停在 00:00。
            if getattr(self, "mini_update_after_id", None) is None:
                self._update_operation_mini()
            return
        self._hide_operation_mini()
        self.mini_mode = mode
        mini = tk.Toplevel(self.root)
        self.mini_window = mini
        mini.title("MacroFlow 录制中" if mode == "recording" else "MacroFlow 执行中")
        mini.configure(background=COLOR_BG)
        mini.resizable(False, False)
        mini.attributes("-topmost", True)
        try:
            mini.wm_attributes("-toolwindow", True)
        except tk.TclError:
            pass
        # Keep the panel compact so it does not cover the game. The denser log
        # below carries the useful detail instead of spending space on chrome.
        width, height = (px(420), px(248)) if mode == "recording" else (px(420), px(292))
        # 录制小窗和执行小窗共用同一个用户调节的位置。
        x, y = self._execution_mini_position(width, height)
        mini.geometry(f"{width}x{height}+{x}+{y}")
        mini.protocol("WM_DELETE_WINDOW", self._hide_operation_mini)
        # 在窗口首次映射（显示）之前就设置 WS_EX_NOACTIVATE。winfo_id 只创建
        # HWND 不会显示窗口；若等到 update_idletasks 映射之后再设置，Tk 映射
        # 新顶层窗口时会先激活它，小窗弹出的瞬间就会抢走激活窗口。
        make_window_no_activate(mini.winfo_id())

        body = ttk.Frame(mini, padding=px(8), style="Surface.TFrame")
        body.pack(fill="both", expand=True)
        top = ttk.Frame(body, style="Surface.TFrame")
        top.pack(fill="x")
        ttk.Label(top, textvariable=self.mini_context_var, style="MiniTitle.TLabel").pack(side="left")
        ttk.Label(top, textvariable=self.mini_elapsed_var, style="MiniTime.TLabel").pack(side="right")
        ttk.Label(body, textvariable=self.mini_count_var, style="MiniText.TLabel",
                  wraplength=px(390), justify="left").pack(anchor="w", pady=pad(5, 2))
        if mode == "execution":
            self.mini_ocr_progressbar = ttk.Progressbar(
                body, maximum=100, variable=self.mini_ocr_progress_var,
                mode="determinate", length=390,
            )
            self.mini_ocr_progressbar.pack(fill="x", pady=pad(0, 5))
        else:
            self.mini_ocr_progressbar = None
        self.mini_binding_label = ttk.Label(body, textvariable=self.mini_window_var,
                                            style="MiniText.TLabel", wraplength=px(390))
        self.mini_binding_label.pack(anchor="w", pady=pad(0, 5))
        steps_frame = ttk.Frame(body, style="Surface.TFrame")
        steps_frame.pack(fill="both", expand=True, pady=pad(0, 7))
        self.mini_steps_text = tk.Text(
            steps_frame, height=5, state="disabled", wrap="word",
            background=COLOR_SURFACE_ALT, foreground=COLOR_TEXT,
            insertbackground=COLOR_TEXT, selectbackground="#244D78",
            relief="flat", bd=0, font=(FONT_FAMILY, FONT_BODY),
            padx=px(6), pady=px(4), takefocus=False,
        )
        self.mini_steps_text.pack(side="left", fill="both", expand=True)
        mini_scroll = ttk.Scrollbar(steps_frame, orient="vertical",
                                    command=self.mini_steps_text.yview, takefocus=False)
        mini_scroll.pack(side="right", fill="y")
        self.mini_steps_text.configure(yscrollcommand=mini_scroll.set)
        # A normal top-level window may make an exclusive/fullscreen game leave
        # fullscreen. WS_EX_NOACTIVATE has already been applied above, before the
        # window was first mapped, so it never takes activation.
        mini.update_idletasks()
        set_dark_titlebar(mini.winfo_id())
        # 无边框悬浮小窗的圆角只能靠窗口区域（DWM 只处理标准边框窗口）。
        set_rounded_window(mini.winfo_id(), px(10))
        buttons = ttk.Frame(body, style="Surface.TFrame")
        buttons.pack(fill="x")
        if mode == "recording":
            buttons.columnconfigure(0, weight=3)
            buttons.columnconfigure(1, weight=3)
            buttons.columnconfigure(2, weight=2)
            ttk.Button(buttons, text="停止录制  F8", command=lambda: self.toggle_record(from_ui=True),
                       bootstyle="danger", takefocus=False).grid(row=0, column=0, sticky="ew")
            ttk.Button(buttons, text="紧急停止  F12", command=lambda: self.stop_all(from_ui=True),
                       bootstyle="danger-outline", takefocus=False).grid(row=0, column=1, sticky="ew", padx=px(6))
            ttk.Button(buttons, text="隐藏", command=self._hide_operation_mini,
                       bootstyle="secondary-outline", takefocus=False).grid(row=0, column=2, sticky="ew")
            self._append_mini_step("实时记录已打开，不会切换或恢复游戏窗口。")
        else:
            buttons.columnconfigure(0, weight=1)
            if self.execution_focus_requested:
                ttk.Label(
                    body,
                    text="紧急恢复：先按 F12；若无响应，按 Ctrl + Alt + Del",
                    style="MiniWarning.TLabel", wraplength=px(390), justify="center",
                ).pack(fill="x", pady=pad(0, 7), before=buttons)
                ttk.Button(buttons, text="强制专注中 · 按 F12 停止并解除",
                           command=lambda: None, bootstyle="danger",
                           takefocus=False).grid(row=0, column=0, sticky="ew")
                self._append_mini_step("强制专注已开启：实体键鼠已锁定，按 F12 停止并解除。")
            else:
                ttk.Button(buttons, text="普通执行模式 · 按 F12 停止",
                           command=lambda: None, bootstyle="secondary",
                           takefocus=False).grid(row=0, column=0, sticky="ew")
                self._append_mini_step("普通执行模式：未锁定实体键鼠，点击正常发送。")
        self._update_operation_mini()
    def _execution_mini_position(self, width: int | None = None,
                                 height: int | None = None) -> tuple[int, int]:
        width = px(420) if width is None else int(width)
        height = px(292) if height is None else int(height)
        # 用"软件所在显示器的可用区域"，不能用 winfo_screenwidth/height：
        # 多屏下后者返回虚拟桌面尺寸，小窗会被推到屏幕外面（右下角外）。
        area = get_monitor_work_area_for_window(self._app_window_hwnd()) \
            or get_primary_screen_rect()
        default_x = area["left"] + area["width"] - width - px(24)
        default_y = area["top"] + area["height"] - height - px(72)
        x, y = default_x, default_y
        saved = getattr(self, "execution_mini_position", None)
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            try:
                x, y = int(saved[0]), int(saved[1])
            except (TypeError, ValueError):
                x, y = default_x, default_y
        return (
            max(area["left"], min(x, area["left"] + max(0, area["width"] - width))),
            max(area["top"], min(y, area["top"] + max(0, area["height"] - height))),
        )
    def _adjust_execution_mini_position(self):
        """Show a draggable, bordered preview and persist its top-left position."""
        if getattr(self, "execution_mini_position_editor", None):
            return
        width, height = px(420), px(316)
        preview = tk.Toplevel(self.root)
        self.execution_mini_position_editor = preview
        preview.title("调节执行小窗位置")
        preview.geometry(
            f"{width}x{height}+{self._execution_mini_position(width, height)[0]}+"
            f"{self._execution_mini_position(width, height)[1]}"
        )
        preview.resizable(False, False)
        preview.attributes("-topmost", True)
        preview.configure(background="#E04444", highlightthickness=3, highlightbackground="#FF6B6B")
        body = ttk.Frame(preview, padding=px(12), style="Surface.TFrame")
        body.pack(fill="both", expand=True, padx=px(3), pady=px(3))
        ttk.Label(
            body, text="执行小窗边界（拖动标题区域调整位置）",
            style="MiniWarning.TLabel", wraplength=px(380), justify="center",
        ).pack(fill="x", pady=pad(4, 12))
        ttk.Label(
            body, text="红色边框就是执行小窗的完整占用范围\n确认后执行小窗会固定在此位置。",
            style="MiniText.TLabel", justify="center",
        ).pack(expand=True)
        buttons = ttk.Frame(body, style="Surface.TFrame")
        buttons.pack(fill="x", pady=pad(10, 0))
        ttk.Button(buttons, text="确认并保存", command=lambda: self._confirm_execution_mini_position(preview),
                   bootstyle="success").pack(side="left", fill="x", expand=True)
        ttk.Button(buttons, text="取消", command=lambda: self._close_execution_mini_position_editor(preview),
                   bootstyle="secondary").pack(side="left", fill="x", expand=True, padx=pad(8, 0))
        drag = {"x": 0, "y": 0}
        def begin(event):
            drag["x"], drag["y"] = event.x_root, event.y_root
        def move(event):
            current_x, current_y = preview.winfo_x(), preview.winfo_y()
            preview.geometry(f"+{current_x + event.x_root - drag['x']}+{current_y + event.y_root - drag['y']}")
            drag["x"], drag["y"] = event.x_root, event.y_root
        for widget in (preview, body):
            widget.bind("<ButtonPress-1>", begin)
            widget.bind("<B1-Motion>", move)
        preview.protocol("WM_DELETE_WINDOW", lambda: self._close_execution_mini_position_editor(preview))
        preview.focus_force()
    def _confirm_execution_mini_position(self, preview):
        self.execution_mini_position = [preview.winfo_x(), preview.winfo_y()]
        self._persist_sidebar_settings()
        self._close_execution_mini_position_editor(preview)
    def _close_execution_mini_position_editor(self, preview):
        if getattr(self, "execution_mini_position_editor", None) is preview:
            self.execution_mini_position_editor = None
        try:
            preview.destroy()
        except tk.TclError:
            pass
    def _hide_recording_mini(self):
        self._hide_operation_mini()
    def _hide_execution_mini(self):
        if self.mini_mode == "execution":
            self._hide_operation_mini()
    def _hide_operation_mini(self):
        after_id = getattr(self, "mini_update_after_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
            self.mini_update_after_id = None
        if self.mini_window and self.mini_window.winfo_exists():
            self.mini_window.destroy()
        self.mini_window = None
        self.mini_steps_text = None
        self.mini_mode = ""
    def _update_operation_mini(self):
        self.mini_update_after_id = None
        active = self.recorder.running or (self.worker and self.worker.is_alive())
        if not active or not self.mini_window or not self.mini_window.winfo_exists():
            return
        # 兜底防失焦：Tk 窗口/控件若意外抢到前台（小窗映射/刷新/滚动时
        # 焦点管理，WS_EX_NOACTIVATE 挡不住 SetFocus 给子控件），把焦点
        # 还给绑定窗口。仅执行中且激活目标开启时生效；用户切到外屏工作
        # 窗口时前台不是本进程窗口，不会被打断。
        if self.mini_mode == "execution" and self.activate_target_enabled_var.get():
            foreground = get_foreground_window_info()
            if foreground and is_current_process_window(foreground.hwnd):
                target_hwnd = self._bound_hwnd(update_display=False)
                if target_hwnd and is_window(target_hwnd) \
                        and not is_current_process_window(target_hwnd):
                    activate_window(target_hwnd)
        started_at = self.record_started_at if self.mini_mode == "recording" else self.execution_started_at
        elapsed = max(0, int(time.perf_counter() - started_at))
        self.mini_elapsed_var.set(f"{elapsed // 60:02d}:{elapsed % 60:02d}")
        if self.mini_mode == "recording":
            self.mini_context_var.set("正在录制")
            capture_mode = "原始相对坐标" if self.recorder.current_mode() == "relative" else "普通桌面坐标"
            self.mini_count_var.set(f"已记录 {len(self.recorder.actions):,} 个动作 · {capture_mode} · F8 停止")
        else:
            self.mini_context_var.set("强制专注执行中" if self.execution_focus_requested else "普通执行中")
            self.mini_count_var.set(self.execution_progress_text or "正在准备 · F12 停止")
        info = get_foreground_window_info()
        self._refresh_binding_for_display()
        target = self.bound_window
        target_title = target.title if target else (
            self.saved_window_signature.get("title", "未设置") if self.saved_window_signature else "未设置"
        )
        current_title = (info.title or info.class_name or "无标题窗口") if info else "未知"
        bound = bool(info and self._foreground_matches_target(info))
        state = "已绑定" if bound else "未绑定"
        if self.mini_mode == "execution":
            activation_title = (
                self.activation_window.title if self.activation_window else target_title
            )
            self.mini_window_var.set(
                f"前台：{current_title} · 目标：{target_title} · 前置：{activation_title}"
            )
        else:
            self.mini_window_var.set(f"前台：{current_title} · 目标：{target_title} · {state}")
        color = COLOR_GREEN if bound else COLOR_RED
        if self.mini_binding_label and self.mini_binding_label.winfo_exists():
            self.mini_binding_label.configure(foreground=color)
        if self.bind_label_widget and self.bind_label_widget.winfo_exists():
            self.bind_label_widget.configure(foreground=color if target else COLOR_RED)
        self.mini_update_after_id = self.root.after(250, self._update_operation_mini)
    def _refresh_binding_for_display(self):
        """Re-resolve a restarted target so the status never relies on a stale HWND."""
        if self.saved_window_signature and (not self.bound_window or not is_window(self.bound_window.hwnd)):
            self._restore_saved_window_binding()
    def _foreground_matches_target(self, info: WindowInfo) -> bool:
        bound_window = getattr(self, "bound_window", None)
        if bound_window and int(info.hwnd) == int(bound_window.hwnd):
            return True
        signature = getattr(self, "saved_window_signature", None)
        if not signature:
            return False
        expected_class = str(signature.get("class_name", ""))
        expected_title = str(signature.get("title", ""))
        expected_path = os.path.normcase(str(signature.get("process_path", ""))).casefold()
        if expected_class and info.class_name != expected_class:
            return False
        if expected_path and info.process_path and os.path.normcase(info.process_path).casefold() != expected_path:
            return False
        # 游戏标题可能随服务器/关卡改变。已有窗口类或进程路径时，它们才是
        # 稳定身份；只有两者都缺失时才退回标题匹配。
        if not expected_class and not expected_path and expected_title and info.title != expected_title:
            return False
        # Position and client size are diagnostic metadata only. Fullscreen,
        # DPI, and border changes must not disable raw-relative recording.
        return True
    def _append_mini_step(self, text: str):
        mini_steps_text = getattr(self, "mini_steps_text", None)
        if not mini_steps_text or not mini_steps_text.winfo_exists():
            return
        try:
            x, y = get_cursor_pos()
            cursor = f"[鼠标 {x},{y}]"
        except Exception:
            cursor = "[鼠标 ?,?]"
        self.mini_steps_text.configure(state="normal")
        self.mini_steps_text.insert(
            "end", f"{datetime.now():%H:%M:%S}  {cursor} {text}\n",
        )
        lines = int(self.mini_steps_text.index("end-1c").split(".")[0])
        if lines > 120:
            self.mini_steps_text.delete("1.0", f"{lines - 120}.0")
        self.mini_steps_text.see("end")
        self.mini_steps_text.configure(state="disabled")
