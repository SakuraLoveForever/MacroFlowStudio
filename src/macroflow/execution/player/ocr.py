from __future__ import annotations

from macroflow.core.models import (
    ACTION_ID_KEY, END_CURRENT_SCRIPT_LABEL, NEXT_WORKFLOW_STEP_TARGET_ID,
    SCRIPT_START_TARGET_ID, recorded_input_steps, script_ref_repeat_count,
)
from macroflow.core.ocr import (
    extract_ocr_integer, find_expected_match, format_ocr_observation, matches_expected,
    parse_ocr_number_pair,
    ocr_match_center, recognize_image_with_boxes, recognize_region,
    recognize_region_with_boxes,
)
from macroflow.core.image_match import (
    CAPTURE_ERRORS, capture_bgr, find_template, find_template_in_image, load_image,
    stabilize_row_offsets,
)
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
from macroflow.core.storage import (
    DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS, load_script, registered_module_object,
    registered_template_region, resolve_path,
)
import time

from .control import (
    PlaybackStopped,
)

class OcrMixin:
    """识别文字、数字比较与多条件识图点击。"""

    def _execute_text_ocr(self, action: dict, hwnd: int | None,
                          script_stack: set[str] | None = None,
                          depth: int = 0) -> tuple[str, str | int] | None:
        """识别文字动作：截取区域 OCR，命中则继续/跳转，未命中轮询到超时。

        期望文字为空时识别到任意文字即命中；timeout_ms=0 只识别一次。
        """
        region = None
        region_mode = action.get("region_mode", "screen")
        if region_mode == "custom":
            raw = action.get("region", [0, 0, 0, 0])
            if len(raw) == 4 and int(raw[2]) > 0 and int(raw[3]) > 0:
                region = self._scale_region(tuple(map(int, raw)))
        elif region_mode == "window":
            if not hwnd:
                raise RuntimeError("窗口区域识别需要有效的目标窗口，请重新绑定")
            region = get_window_rect(hwnd)
        expected = str(action.get("expected_text", "")).strip()
        match_mode = str(action.get("match_mode", "contains"))
        timeout_ms = max(0, int(action.get("timeout_ms", 3000)))
        interval_ms = max(200, int(action.get("interval_ms", 500)))
        start = time.perf_counter()
        last_ocr_observation = None
        while True:
            if self.stop_event.is_set():
                raise PlaybackStopped()
            # OCR 引擎未就绪时等待（可中断）：F12 能中止，不会卡死在首次导入。
            if self.on_ocr_engine_wait and not self.on_ocr_engine_wait():
                raise PlaybackStopped()
            recognized = recognize_region(region)
            matched = matches_expected(recognized, expected, match_mode)
            observation = format_ocr_observation(
                recognized, expected, matched, "识别文字动作",
            )
            if observation != last_ocr_observation:
                last_ocr_observation = observation
                self._trace(observation, module_detail=True)
                self._status(observation)
            if matched:
                if expected:
                    self._status(f"识别文字命中：{recognized[:40]}")
                else:
                    self._status(f"识别到文字：{recognized[:40]}")
                if action.get("show_result_notice") and self.on_notice:
                    self.on_notice(
                        f"识别文字命中：{recognized[:40] or '（空）'}", 3500,
                    )
                self._wait(max(0, int(action.get("found_delay_ms", 0))))
                if action.get("on_found", "continue") == "jump":
                    self._jump_reason = "识别文字命中"
                    target_id = str(action.get("found_jump_action_id", "")).strip()
                    if target_id == NEXT_WORKFLOW_STEP_TARGET_ID:
                        return "next_workflow_step", 0
                    if target_id:
                        return "action_id", target_id
                    return "row", max(1, int(action.get("found_jump_row", 1)))
                return None
            if timeout_ms <= 0 or (time.perf_counter() - start) * 1000 >= timeout_ms:
                break
            self._wait(interval_ms)
        self._status(f"识别文字未命中：{recognized[:40] or '（无文字）'}")
        if action.get("show_result_notice") and self.on_notice:
            self.on_notice(
                f"识别文字未命中：{expected or '任意文字'} · "
                f"{timeout_ms or 0} ms 超时",
                3500,
            )
        self._wait(max(0, int(action.get("timeout_delay_ms", 0))))
        self._run_failure_segment(action, hwnd, script_stack, depth)
        timeout_action = action.get("on_timeout", "continue")
        if timeout_action == "continue":
            self._status("识别文字超时，按设置继续")
            return None
        if timeout_action == "jump":
            self._jump_reason = "识别文字超时"
            target_id = str(action.get("timeout_jump_action_id", "")).strip()
            if target_id:
                return "action_id", target_id
            return "row", max(1, int(action.get("timeout_jump_row", 1)))
        raise RuntimeError("识别文字超时未命中，按设置停止")
    def _execute_ocr_compare(self, action: dict, hwnd: int | None,
                             script_stack: set[str] | None = None,
                             depth: int = 0) -> tuple[str, str | int] | None:
        """OCR a number pair such as ``12/34`` and run its comparison branch."""
        region = None
        region_mode = action.get("region_mode", "screen")
        if region_mode == "custom":
            raw = action.get("region", [0, 0, 0, 0])
            if len(raw) == 4 and int(raw[2]) > 0 and int(raw[3]) > 0:
                region = self._scale_region(tuple(map(int, raw)))
        elif region_mode == "window":
            if not hwnd:
                raise RuntimeError("绑定窗口识别需要有效的目标窗口，请重新绑定")
            region = get_window_rect(hwnd)
        raw_click_region = action.get("click_region", [0, 0, 0, 0])
        if not isinstance(raw_click_region, (list, tuple)) or len(raw_click_region) != 4 \
                or int(raw_click_region[2]) <= 0 or int(raw_click_region[3]) <= 0:
            raise RuntimeError("识别数字比较动作未设置有效的自定义点击区域")
        click_region = self._scale_region(tuple(map(int, raw_click_region)))
        separator = str(action.get("separator", "/")).strip() or "/"
        timeout_ms = max(0, int(action.get("timeout_ms", 3000)))
        interval_ms = max(200, int(action.get("interval_ms", 500)))
        start = time.perf_counter()
        while True:
            if self.stop_event.is_set():
                raise PlaybackStopped()
            if self.on_ocr_engine_wait and not self.on_ocr_engine_wait():
                raise PlaybackStopped()
            recognized, _matches = recognize_region_with_boxes(region)
            pair = parse_ocr_number_pair(recognized, separator)
            if pair is not None:
                left, right = pair
                equal = left == right
                result_name = "相等" if equal else "不相等"
                self._trace(
                    f"识别数字比较：读取「{recognized}」→ {left} {separator} {right}，结果：{result_name}",
                    module_detail=True,
                )
                self._status(f"识别数字比较：{left} {separator} {right} · {result_name}")
                prefix = "equal" if equal else "not_equal"
                behavior = str(action.get(f"{prefix}_action", "continue"))
                if behavior == "click":
                    x = click_region[0] + click_region[2] // 2
                    y = click_region[1] + click_region[3] // 2
                    count = max(1, min(9999, int(action.get(f"{prefix}_click_count", 1))))
                    self._click_module_point(
                        x, y, str(action.get("button", "left")), count, hwnd,
                    )
                    self._status(f"数字比较{result_name}，自定义区域连续点击 {count} 次")
                    return None
                if behavior == "jump":
                    self._jump_reason = f"数字比较{result_name}"
                    target_id = str(action.get(f"{prefix}_jump_action_id", "")).strip()
                    if target_id == NEXT_WORKFLOW_STEP_TARGET_ID:
                        return "next_workflow_step", 0
                    if target_id:
                        return "action_id", target_id
                    return "row", max(1, int(action.get(f"{prefix}_jump_row", 1)))
                return None
            if timeout_ms <= 0 or (time.perf_counter() - start) * 1000 >= timeout_ms:
                timeout_action = str(action.get("on_timeout", "continue"))
                self._log_event(
                    f"识别数字比较连续 {timeout_ms} ms 未识别到“数字{separator}数字”",
                )
                self._run_failure_segment(action, hwnd, None, 0)
                if timeout_action == "jump":
                    self._jump_reason = "识别数字比较超时"
                    target_id = str(action.get("timeout_jump_action_id", "")).strip()
                    if target_id:
                        return "action_id", target_id
                    return "row", max(1, int(action.get("timeout_jump_row", 1)))
                if timeout_action == "stop":
                    raise RuntimeError("识别数字比较超时")
                return None
            self._wait(interval_ms)
    def _execute_multi_condition_click(self, action: dict, hwnd: int | None,
                                       script_stack: set[str] | None = None,
                                       depth: int = 0) -> tuple[str, str | int] | None:
        """Wait for the enabled image/OCR/number conditions, then click once."""
        conditions = action.get("conditions")
        if not isinstance(conditions, list) or len(conditions) != 3:
            raise RuntimeError("多条件识图点击必须配置三个条件")
        enabled = [condition for condition in conditions if isinstance(condition, dict)
                   and bool(condition.get("enabled", False))]
        if not enabled:
            raise RuntimeError("多条件识图点击至少需要启用一个条件")
        raw_click_region = action.get("click_region", [0, 0, 0, 0])
        if not isinstance(raw_click_region, (list, tuple)) or len(raw_click_region) != 4:
            raise RuntimeError("多条件识图点击未设置有效的自定义点击区域")
        try:
            click_values = tuple(map(int, raw_click_region))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("多条件识图点击区域不是有效坐标") from exc
        if click_values[2] <= 0 or click_values[3] <= 0:
            raise RuntimeError("多条件识图点击区域的宽高必须大于零")
        click_region = self._scale_region(click_values)
        timeout_ms = max(0, int(action.get("timeout_ms", 3000)))
        interval_ms = max(200, int(action.get("interval_ms", 500)))
        start = time.perf_counter()
        while True:
            if self.stop_event.is_set():
                raise PlaybackStopped()
            all_match = True
            for condition in enabled:
                if not self._multi_condition_matches(condition, hwnd):
                    all_match = False
                    break
            if all_match:
                x = click_region[0] + click_region[2] // 2
                y = click_region[1] + click_region[3] // 2
                count = max(1, min(9999, int(action.get("click_count", 1))))
                button = str(action.get("button", "left"))
                self._click_module_point(x, y, button, count, hwnd)
                self._status(f"多条件识图点击：{len(enabled)} 个条件满足，连续点击 {count} 次")
                self._log_event(f"多条件识图点击：{len(enabled)} 个条件全部满足，连续点击 {count} 次")
                return None
            if timeout_ms <= 0 or (time.perf_counter() - start) * 1000 >= timeout_ms:
                self._log_event(f"多条件识图点击在 {timeout_ms} ms 内未同时满足条件")
                self._run_failure_segment(action, hwnd, None, 0)
                if str(action.get("on_timeout", "continue")) == "stop":
                    raise RuntimeError("多条件识图点击超时")
                return None
            self._wait(interval_ms)
    def _multi_condition_matches(self, condition: dict, hwnd: int | None) -> bool:
        """Evaluate one enabled condition in its own custom recognition region."""
        kind = str(condition.get("type", "")).strip()
        module_obj = None
        if kind == "image" and condition.get("module_ref"):
            module_key = str(condition.get("module_key", "")).strip()
            module_obj = registered_module_object(module_key)
            if module_obj is None:
                raise RuntimeError(f"多条件识图点击引用的图片模块不存在：{module_key or '未设置'}")
        raw_region = (
            module_obj.get("region", [0, 0, 0, 0])
            if module_obj is not None else condition.get("region", [0, 0, 0, 0])
        )
        if not isinstance(raw_region, (list, tuple)) or len(raw_region) != 4:
            raise RuntimeError("多条件识图点击的识别区域无效")
        try:
            region_values = tuple(map(int, raw_region))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("多条件识图点击的识别区域不是有效坐标") from exc
        if region_values[2] <= 0 or region_values[3] <= 0:
            raise RuntimeError("多条件识图点击的识别区域宽高必须大于零")
        region = self._scale_region(region_values)
        if kind == "image":
            source = module_obj if module_obj is not None else condition
            template = str(source.get("template", "")).strip()
            if not template:
                raise RuntimeError("多条件识图点击的图片条件未设置模板")
            threshold = min(1.0, max(0.1, float(source.get("threshold", 0.85))))
            return find_template(
                resolve_path(template), threshold, region,
                ignore_background=bool(source.get("ignore_background", False)),
                scale=self._template_scale(),
            ) is not None
        if kind == "ocr":
            if self.on_ocr_engine_wait and not self.on_ocr_engine_wait():
                raise PlaybackStopped()
            recognized, matches = recognize_region_with_boxes(region)
            if str(condition.get("ocr_mode", "text")) == "number":
                pair = parse_ocr_number_pair(recognized, str(condition.get("separator", "/")))
                if pair is None:
                    return False
                left, right = pair
                relation = str(condition.get("relation", "equal"))
                return left == right if relation == "equal" else left != right
            expected = str(condition.get("expected_text", ""))
            mode = str(condition.get("match_mode", "contains"))
            return find_expected_match(matches, expected, mode) is not None \
                or matches_expected(recognized, expected, mode)
        raise RuntimeError(f"多条件识图点击存在未知条件类型：{kind}")
