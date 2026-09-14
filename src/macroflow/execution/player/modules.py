from __future__ import annotations

from macroflow.core.models import (
    ACTION_ID_KEY, END_CURRENT_SCRIPT_LABEL, NEXT_WORKFLOW_STEP_TARGET_ID,
    SCRIPT_START_TARGET_ID, recorded_input_steps, script_ref_repeat_count,
)
from pathlib import Path
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
import time

from .base import (
    CLICK_DEDUP_RADIUS_PX,
    CLICK_DEDUP_WINDOW_S,
    CLICK_SOURCE_ACTION,
    CLICK_SOURCE_GUARD,
    CLICK_SOURCE_MODULE,
    MAX_SCRIPT_REF_DEPTH,
)

class ModuleResultMixin:
    """模块结果路由、失败代码段与模块点击去重。"""

    def _run_failure_segment(self, action: dict, hwnd: int | None,
                             script_stack: set[str] | None,
                             depth: int) -> None:
        """脚本行级“失败后执行代码段”：识别不到 / 超时 / 阻塞超时后先跑这段。

        与模块对象里的“超时代码段”同类型，但属于当前脚本行，便于同一模块在
        不同脚本里走不同的补救动作。
        """
        if not action.get("failure_segment_enabled"):
            return
        segment = [item for item in (action.get("failure_actions") or [])
                   if isinstance(item, dict)]
        if not segment:
            return
        if depth >= MAX_SCRIPT_REF_DEPTH:
            raise RuntimeError("模块失败代码段嵌套过深，已停止执行")
        self._trace(f"模块失败，执行脚本行失败代码段（{len(segment)} 个动作）",
                    module_detail=True)
        self._run_action_sequence(
            segment, hwnd, script_stack=script_stack, depth=depth + 1,
        )
    def _module_result_route(self, action: dict, module_obj: dict, succeeded: bool,
                             result_label: str | None = None,
                             hwnd: int | None = None,
                             script_stack: set[str] | None = None,
                             depth: int = 0) -> tuple[str, str | int] | None:
        """Publish one module result and apply this reference row's branch."""
        result_text = "成功" if succeeded else "失败"
        module_label = str(module_obj.get("name") or "").strip() \
            or Path(str(module_obj.get("template", ""))).name or "模块"
        if not succeeded:
            self._run_failure_segment(action, hwnd, script_stack, depth)
        # 模块结果属于「这一行脚本做了什么」：只进执行明细，不刷事件日志。
        self._trace(f"模块 {module_label} {result_label or f'执行结果：{result_text}'}",
                    module_detail=True)
        status_result = result_label or f"执行{result_text}"
        behavior_key = "on_found" if succeeded else "on_timeout"
        target_key = "found_jump_action_id" if succeeded else "timeout_jump_action_id"
        legacy_row_key = "found_jump_row" if succeeded else "timeout_jump_row"
        behavior = str(action.get(behavior_key, "continue"))
        if behavior in {"continue", "click"}:
            self._status(f"模块{status_result}，按设置继续下一行")
            return None
        if behavior == "end_current_script":
            self._status(f"模块{status_result}，按设置结束当前最里层脚本")
            return "end_current_script", 0
        if behavior == "jump":
            self._jump_reason = f"模块{status_result}"
            target_id = str(action.get(target_key, "")).strip()
            if target_id == NEXT_WORKFLOW_STEP_TARGET_ID:
                return "next_workflow_step", 0
            if target_id:
                return "action_id", target_id
            return "row", max(1, int(action.get(legacy_row_key, 1)))
        raise RuntimeError(f"模块执行{result_text}，按设置停止全部执行")
    def _after_module_success(self, obj: dict, match: dict, hwnd: int | None,
                              script_stack: set[str] | None,
                              depth: int) -> tuple[str, str | int] | None:
        """先执行模块主动作，再执行可选代码段，最后才返回原执行流。"""
        after_action = obj.get("after_action", "click_match")
        button = str(obj.get("button", "left"))
        click_count = max(1, min(9999, int(obj.get("click_count", 1))))
        module_label = str(obj.get("name") or "").strip() \
            or Path(str(obj.get("template", ""))).name or "模块"
        result = None
        if after_action in ("click_match", "click_custom"):
            if after_action == "click_custom":
                raw_point = obj.get("click_point", [])
                if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
                    raise RuntimeError(f"模块 {module_label} 未设置自定义点击位置")
                x, y = self._scale_point(int(raw_point[0]), int(raw_point[1]))
            else:
                x, y = match["center_x"], match["center_y"]
            if after_action == "click_match" and obj.get("recognize") == "text":
                x += int(obj.get("ocr_offset_right", 0)) - int(obj.get("ocr_offset_left", 0))
                y += int(obj.get("ocr_offset_down", 0)) - int(obj.get("ocr_offset_up", 0))
            self._click_module_point(x, y, button, click_count, hwnd)
            self._trace(
                f"模块 {module_label} 已点击 ({x}, {y})"
                + (f" × {click_count}" if click_count > 1 else ""),
                module_detail=True,
            )
        elif after_action == "second_match":
            result = self._execute_second_match(obj, hwnd, match)
        # 旧对象的 run_actions 与新对象的开关都按附加代码段处理。
        run_segment = bool(obj.get("run_code_after_action", False)) \
            or after_action == "run_actions"
        if run_segment:
            segment = list(obj.get("on_success_actions") or [])
            if depth >= MAX_SCRIPT_REF_DEPTH:
                raise RuntimeError("模块代码段嵌套过深，已停止执行")
            if segment:
                self._trace(
                    f"模块 {module_label} 主动作完成，执行附加代码段"
                    f"（{len(segment)} 个动作）",
                    module_detail=True,
                )
                self._run_action_sequence(segment, hwnd,
                                          script_stack=script_stack, depth=depth + 1)
        return result
    def _click_module_point(self, x: int, y: int, button: str, count: int,
                            hwnd: int | None = None,
                            source: str | None = None) -> None:
        """Click one module target repeatedly with cooperative F12 cancellation."""
        x, y = self._clamp_click_point(x, y, hwnd)
        source = source or self._current_module_click_source()
        if self._skip_duplicate_click(int(x), int(y), button, source):
            return
        self._record_click(int(x), int(y), button, source)
        send_move_absolute(int(x), int(y))
        total = max(1, min(9999, int(count)))
        for index in range(total):
            send_button(button, True)
            self._held_buttons.add(button)
            try:
                self._wait(30)
            finally:
                send_button(button, False)
                self._held_buttons.discard(button)
            if index + 1 < total:
                self._wait(50)
    def _skip_duplicate_click(self, x: int | None, y: int | None, button: str,
                             source: str) -> bool:
        """同一个按钮刚刚被别的处理路径点过就跳过这一次。

        全局检测守卫和脚本模块会同时命中同一颗“确定”（同一张图、同一片区域），
        守卫点完之后脚本那一行紧接着又点一次。落点在
        ``CLICK_DEDUP_RADIUS_PX`` 内、按键一致、来源不同，且间隔在
        ``CLICK_DEDUP_WINDOW_S`` 内时按一次处理，并写一条执行明细。
        """
        if x is None or y is None:
            return False
        last = self._last_click
        if not last or last["button"] != button:
            return False
        if last["source"] == source:
            return False
        if abs(int(last["x"]) - int(x)) > CLICK_DEDUP_RADIUS_PX \
                or abs(int(last["y"]) - int(y)) > CLICK_DEDUP_RADIUS_PX:
            return False
        elapsed = time.perf_counter() - float(last["at"])
        if elapsed >= CLICK_DEDUP_WINDOW_S:
            return False
        self._trace(
            f"跳过重复点击：{button} @ ({int(x)}, {int(y)}) 与 "
            f"{elapsed * 1000:.0f} ms 前{last['source']}点过的 "
            f"({int(last['x'])}, {int(last['y'])}) 是同一颗按钮，按一次处理。",
            module_detail=True,
        )
        return True
    def _record_click(self, x: int | None, y: int | None, button: str,
                      source: str) -> None:
        """登记刚才那一下点击的落点，供同一点防重复点击判断。"""
        if x is None or y is None:
            self._last_click = None
            return
        self._last_click = {
            "x": int(x), "y": int(y), "button": str(button),
            "at": time.perf_counter(), "source": source,
        }
    def _current_click_source(self) -> str:
        """这一下点击是谁发的：全局检测守卫处理段，还是脚本自己。"""
        return CLICK_SOURCE_GUARD if self._handler_depth else CLICK_SOURCE_ACTION
    def _current_module_click_source(self) -> str:
        """模块点击的来源标记（守卫自己的主动作点击也算守卫）。"""
        return CLICK_SOURCE_GUARD if self._handler_depth else CLICK_SOURCE_MODULE
