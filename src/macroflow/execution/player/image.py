from __future__ import annotations

from macroflow.core.image_match import (
    CAPTURE_ERRORS, capture_bgr, find_template, find_template_in_image, load_image,
    stabilize_row_offsets,
)
from macroflow.core.storage import (
    DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS, load_script, registered_module_object,
    registered_template_region, resolve_path,
)
from macroflow.core.models import (
    ACTION_ID_KEY, END_CURRENT_SCRIPT_LABEL, NEXT_WORKFLOW_STEP_TARGET_ID,
    SCRIPT_START_TARGET_ID, recorded_input_steps, script_ref_repeat_count,
)
from pathlib import Path
from macroflow.core.ocr import (
    extract_ocr_integer, find_expected_match, format_ocr_observation, matches_expected,
    parse_ocr_number_pair,
    ocr_match_center, recognize_image_with_boxes, recognize_region,
    recognize_region_with_boxes,
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
from macroflow.ui.detect_overlay import show_overlay
import time

from .base import (
    MAX_SCRIPT_REF_DEPTH,
)
from .control import (
    PlaybackStopped,
)

class ImageMixin:
    """识图动作：等待命中、超时分支、备用模块与二次识别。"""

    def _execute_image(self, action: dict, hwnd: int | None,
                       script_stack: set[str] | None = None,
                       depth: int = 0) -> tuple[str, str | int] | None:
        template = resolve_path(str(action.get("template", "")))
        module_obj = None
        if action.get("module_ref"):
            # 实时引用：阻塞/相似度/间隔/延时/动作B 全部从模块区域对象读取。
            module_key = str(action.get("module_key") or action.get("template", "")).strip()
            module_obj = registered_module_object(module_key)
            if module_obj is None:
                raise RuntimeError(f"引用的模块不存在：{module_key or '未设置'}")
            elif str(module_obj.get("template", "")).strip():
                template = resolve_path(str(module_obj["template"]))
        if module_obj is not None:
            # 模块对象自带的「进入模块前延时」：引用该模块的每一行都先等这段
            # 再开始识别；与模块对象的「延时」（识别成功后、执行动作前）不同。
            start_delay_ms = max(0, int(module_obj.get("start_delay_ms", 0) or 0))
            if start_delay_ms:
                start_label = (
                    str(module_obj.get("name") or "").strip()
                    or Path(template).name or "模块"
                )
                self._trace(
                    f"模块 {start_label} 进入前延时 {start_delay_ms} ms",
                    module_detail=True,
                )
                self._wait(self._scaled_delay(start_delay_ms))
        if module_obj is not None and module_obj.get("recognize") == "none":
            module_label = str(module_obj.get("name") or "无需识图模块")
            self._status(f"无需识图，直接执行模块：{module_label}")
            self._trace(f"模块 {module_label} 无需识图，直接执行", module_detail=True)
            self._wait(max(0, int(module_obj.get("delay_ms", 0))))
            result = self._after_module_success(
                module_obj, {}, hwnd, script_stack, depth,
            )
            return result if result is not None else self._module_result_route(
                action, module_obj, succeeded=True,
            )
        timeout_ms = max(0, int(action.get("timeout_ms", 3000)))
        wait_forever = bool(action.get("wait_forever", False))
        blocking_timeout_enabled = False
        interval_ms = max(50, int(action.get("interval_ms", 250)))
        threshold = min(1.0, max(0.1, float(action.get("threshold", 0.85))))
        if module_obj is not None:
            module_blocking = bool(module_obj.get("blocking", False))
            blocking_timeout_enabled = module_blocking and bool(
                action.get("blocking_timeout_enabled", False)
            )
            wait_forever = module_blocking and not blocking_timeout_enabled
            interval_ms = max(50, int(module_obj.get("interval_ms", 250)))
            threshold = min(1.0, max(0.1, float(module_obj.get("threshold", 0.85))))
            if blocking_timeout_enabled:
                try:
                    timeout_ms = max(0, int(action.get(
                        "blocking_timeout_ms", DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS,
                    )))
                except (TypeError, ValueError):
                    timeout_ms = DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS
            else:
                timeout_ms = max(0, int(module_obj.get("not_found_timeout_ms", timeout_ms)))
        text_module = bool(module_obj is not None and module_obj.get("recognize") == "text")
        number_module = bool(module_obj is not None and module_obj.get("recognize") == "number")
        wait_target_absent = bool(
            module_obj is not None and module_obj.get("wait_text_absent", False)
        )
        if wait_forever:
            module_label = (
                str((module_obj or {}).get("name", "")).strip()
                or Path(template).name
                or "识图模块"
            )
            script_context = f"脚本[{self._active_script_name}]：" if self._active_script_name else ""
            target_label = Path(template).name if str(template).strip() else "目标"
            if wait_target_absent:
                # “等待目标消失”与“阻塞等待出现”语义相反：看到目标才执行动作，
                # 检测不到就算完成。日志若仍写“等待 …… 出现”，用户会以为模块
                # 在等图片出现，实际它第一帧就判定“已消失”并直接成功了。
                self._trace(
                    f"{script_context}模块 {module_label} 是“等待目标消失”：看到 "
                    f"{target_label} 就执行动作并重新识别，检测不到即完成当前模块。",
                    module_detail=True,
                )
            else:
                self._trace(
                    f"{script_context}模块 {module_label} 开始阻塞等待 {target_label} 出现。",
                    module_detail=True,
                )
        # OCR 单次约几百毫秒，文字和数字模式的轮询间隔不能太短。
        if text_module or number_module:
            interval_ms = max(interval_ms, 200)
        expected_number = None
        if number_module:
            if "expected_number" not in action:
                raise RuntimeError("数字读取模块的当前脚本行未设置比较数字")
            try:
                expected_number = int(action["expected_number"])
            except (TypeError, ValueError) as exc:
                raise RuntimeError("数字读取模块的比较数字不是有效整数") from exc
        if wait_target_absent and not blocking_timeout_enabled:
            # “直到目标消失”本身就是无限等待条件，不受普通识别超时影响。
            wait_forever = True
        ignore_background = bool(
            (module_obj or action).get("ignore_background", False)
        )
        module_timeout_enabled = bool(
            module_obj is not None and module_obj.get("run_code_on_timeout", False)
        )
        if wait_target_absent:
            module_timeout_enabled = False
        if number_module:
            module_timeout_enabled = False
        module_timeout_ms = max(
            0, int(module_obj.get(
                "not_found_timeout_ms", DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS,
            ))
        ) if module_obj is not None else 0
        fallback_template = None
        if module_obj is None:
            fallback_template = (
                resolve_path(str(action.get("fallback_template", "")))
                if str(action.get("fallback_template", "")).strip() else None
            )
        fallback_switch_ms = max(0, int(action.get("fallback_switch_ms", 3000)))
        fallback_region = None
        module_fallback = None
        if module_obj is not None:
            fallback_key = str(module_obj.get("fallback_module_key", "")).strip()
            candidate = registered_module_object(fallback_key) if fallback_key else None
            if candidate is not None and candidate.get("recognize") not in ("number", "none"):
                module_fallback = candidate
        fallback_on_match = "continue"
        if module_obj is not None:
            fallback_on_match = str(module_obj.get("fallback_on_match", "")).strip()
            if fallback_on_match not in ("continue", "click_continue", "exit", "click_exit"):
                fallback_on_match = "click_continue" if bool(module_obj.get("fallback_click", False)) else "continue"
        if fallback_template is not None:
            fallback_region_mode = action.get("fallback_region_mode", "screen")
            if fallback_region_mode == "template":
                # 备用模板引用已登记模板：区域运行时从模板登记表读取。
                fallback_region = self._template_region(fallback_template)
            elif fallback_region_mode == "custom":
                raw = action.get("fallback_region", [0, 0, 0, 0])
                if len(raw) == 4 and int(raw[2]) > 0 and int(raw[3]) > 0:
                    fallback_region = self._scale_region(tuple(map(int, raw)))
            elif fallback_region_mode == "window":
                if not hwnd:
                    raise RuntimeError("窗口区域识别需要有效的目标窗口，请重新绑定")
                fallback_region = get_window_rect(hwnd)
        region = None
        region_mode = action.get("region_mode", "screen")
        if region_mode == "template":
            # 模块引用使用自己的独立区域；同一图片可被多个模块以不同区域复用。
            if module_obj is not None:
                raw = module_obj.get("region", [])
                if len(raw) == 4 and int(raw[2]) > 0 and int(raw[3]) > 0:
                    region = self._scale_region(tuple(map(int, raw)))
            else:
                region = self._template_region(template)
        elif region_mode == "custom":
            raw = action.get("region", [0, 0, 0, 0])
            if len(raw) == 4 and int(raw[2]) > 0 and int(raw[3]) > 0:
                region = tuple(map(int, raw))
                region = self._scale_region(region)
        elif region_mode == "window":
            if not hwnd:
                raise RuntimeError("窗口区域识别需要有效的目标窗口，请重新绑定")
            region = get_window_rect(hwnd)
        if number_module and region is None:
            raise RuntimeError("数字读取模块未设置有效的指定识别区域")
        # “全屏”识别应指目标显示器，而不是把多屏拼接后的虚拟桌面作为一张
        # 图。否则两个 1920 宽显示器会让模板按 2 倍缩放，导致同分辨率换屏
        # 后也无法命中。
        recognition_region = region if region is not None else self._target_screen
        fallback_capture_region = (
            fallback_region if fallback_region is not None else self._target_screen
        )
        start = time.perf_counter()
        match = None
        fallback_active = False
        module_fallback_present = False
        recognized = ""
        waiting_absent_logged = False
        last_ocr_observation = None
        while True:
            if self.stop_event.is_set():
                raise PlaybackStopped()
            capture_failed = False
            if number_module:
                # OCR 引擎未就绪时等待（可中断）：F12 能中止，不会卡死在首次导入。
                if self.on_ocr_engine_wait and not self.on_ocr_engine_wait():
                    raise PlaybackStopped()
                try:
                    recognized, ocr_matches = recognize_region_with_boxes(recognition_region)
                except CAPTURE_ERRORS as exc:
                    self._note_capture_failure(exc)
                    capture_failed = True
                    recognized, ocr_matches = "", []
                else:
                    self._clear_capture_failure()
                number_value, raw_digits = extract_ocr_integer(recognized, ocr_matches)
                module_label = str(module_obj.get("name") or "读取数字")
                if number_value is not None:
                    equal = number_value == expected_number
                    observation = (
                        f"模块 {module_label} 读取「{raw_digits}」→ 数字 {number_value}；"
                        f"比较 {expected_number} · {'相等' if equal else '不相等'}"
                    )
                    self._trace(observation, module_detail=True)
                    self._status(observation)
                    return self._module_result_route(
                        action, module_obj, succeeded=equal,
                        result_label=f"比较结果：{'相等' if equal else '不相等'}",
                        hwnd=hwnd, script_stack=script_stack, depth=depth,
                    )
                observation = f"模块 {module_label}：指定区域内未读取到数字"
                # 截图失败那一轮已经写过原因，不再补一条“未读取到数字”的误导结论。
                if not capture_failed and observation != last_ocr_observation:
                    last_ocr_observation = observation
                    self._trace(observation, module_detail=True)
                match = None
            elif text_module:
                # OCR 引擎未就绪时等待（可中断）：F12 能中止，不会卡死在首次导入。
                if self.on_ocr_engine_wait and not self.on_ocr_engine_wait():
                    raise PlaybackStopped()
                try:
                    recognized, ocr_matches = recognize_region_with_boxes(recognition_region)
                except CAPTURE_ERRORS as exc:
                    self._note_capture_failure(exc)
                    capture_failed = True
                    recognized, ocr_matches = "", []
                else:
                    self._clear_capture_failure()
                expected_text = str(module_obj.get("expected_text", ""))
                match_mode = str(module_obj.get("match_mode", "contains"))
                match = find_expected_match(ocr_matches, expected_text, match_mode)
                text_present = match is not None
                if (not text_present and not capture_failed
                        and matches_expected(recognized, expected_text, match_mode)):
                    # 极少数期望内容可能横跨多个 OCR 行；仍保留旧的整体匹配能力。
                    text_present = True
                    match = ocr_match_center(recognition_region)
                observation = format_ocr_observation(
                    recognized, expected_text, text_present,
                    str(module_obj.get("name") or "识别文字模块"),
                )
                if not capture_failed and observation != last_ocr_observation:
                    last_ocr_observation = observation
                    self._trace(observation, module_detail=True)
                    self._status(observation)
                if not capture_failed and wait_target_absent and text_present:
                    if not waiting_absent_logged:
                        waiting_absent_logged = True
                        self._status(
                            f"识别文字命中，循环点击直到消失：{recognized[:40] or '（无文字）'}"
                        )
                    self._wait(max(0, int(module_obj.get("delay_ms", 0))))
                    result = self._after_module_success(
                        module_obj, match, hwnd, script_stack, depth,
                    )
                    if result is not None:
                        return result
                    self._wait(interval_ms)
                    continue
                elif not capture_failed and wait_target_absent:
                    self._status("框选区域内已检测不到期望文字，结束循环")
                    if action.get("show_result_notice") and self.on_notice:
                        self.on_notice("期望文字已消失，循环点击完成", 3500)
                    return self._module_result_route(action, module_obj, succeeded=True)
                elif text_present:
                    break
                else:
                    match = None
            else:
                # 主模板始终在自己的区域检测；备用激活后两者同时检测（各自区域）。
                try:
                    match = find_template(template, threshold, recognition_region,
                                          ignore_background=ignore_background,
                                          scale=self._template_scale())
                except CAPTURE_ERRORS as exc:
                    self._note_capture_failure(exc)
                    capture_failed = True
                    match = None
                else:
                    self._clear_capture_failure()
                if not capture_failed and wait_target_absent and match:
                    if not waiting_absent_logged:
                        waiting_absent_logged = True
                        self._status(
                            f"模板图片命中，循环执行直到消失：{Path(template).name}"
                        )
                    show_overlay(
                        match["x"], match["y"], match["width"], match["height"],
                    )
                    self._wait(max(0, int(module_obj.get("delay_ms", 0))))
                    result = self._after_module_success(
                        module_obj, match, hwnd, script_stack, depth,
                    )
                    if result is not None:
                        return result
                    self._wait(interval_ms)
                    continue
                if not capture_failed and wait_target_absent:
                    self._status("框选区域内已检测不到目标模板，结束循环")
                    if action.get("show_result_notice") and self.on_notice:
                        self.on_notice("目标模板已消失，循环执行完成", 3500)
                    return self._module_result_route(action, module_obj, succeeded=True)
                if match:
                    break
            fallback_match = None
            if module_fallback is not None:
                try:
                    fallback_match = self._match_fallback_module(module_fallback, hwnd)
                except CAPTURE_ERRORS as exc:
                    # 备用模块自己也要截屏：截图不可用时和主模板一样按未命中处理。
                    self._note_capture_failure(exc)
                    fallback_match = None
                if fallback_match and not module_fallback_present:
                    module_fallback_present = True
                    fallback_name = str(module_fallback.get("name") or "备用识别模块")
                    show_overlay(
                        fallback_match["x"], fallback_match["y"],
                        fallback_match["width"], fallback_match["height"],
                    )
                    if fallback_on_match.startswith("click_"):
                        self._click_module_point(
                            int(fallback_match["center_x"]), int(fallback_match["center_y"]),
                            str(module_fallback.get("button", "left")),
                            max(1, int(module_fallback.get("click_count", 1))),
                            hwnd,
                        )
                        self._status(
                            f"备用模块 {fallback_name} 已识别并点击，"
                            f"{'退出' if fallback_on_match == 'click_exit' else '继续'}识别主模块"
                        )
                    else:
                        self._status(
                            f"备用模块 {fallback_name} 已识别，"
                            f"{'退出' if fallback_on_match == 'exit' else '继续'}识别主模块"
                        )
                    if fallback_on_match in ("exit", "click_exit"):
                        return
                elif not fallback_match:
                    module_fallback_present = False
            if wait_forever and fallback_template is not None and (
                fallback_active
                or (time.perf_counter() - start) * 1000 >= fallback_switch_ms
            ):
                if not fallback_active:
                    fallback_active = True
                    self._status(
                        f"等待 {Path(template).name} 超过 {fallback_switch_ms} ms，"
                        f"备用模板 {Path(fallback_template).name} 加入同时检测",
                    )
                try:
                    fallback_match = find_template(
                        fallback_template, threshold, fallback_capture_region,
                        ignore_background=ignore_background,
                        scale=self._template_scale(),
                    )
                except CAPTURE_ERRORS as exc:
                    self._note_capture_failure(exc)
                    fallback_match = None
            if module_obj is None and fallback_match:
                # 备用模板命中：圈出匹配区域提醒；可选点击；出现后回到主模板检测或直接退出识图。
                show_overlay(
                    fallback_match["x"], fallback_match["y"],
                    fallback_match["width"], fallback_match["height"],
                )
                fallback_x, fallback_y = fallback_match["center_x"], fallback_match["center_y"]
                if action.get("fallback_click", True):
                    if action.get("click_target", "match") == "custom":
                        raw_point = action.get("click_point", [fallback_x, fallback_y])
                        if isinstance(raw_point, (list, tuple)) and len(raw_point) >= 2:
                            fallback_x, fallback_y = self._scale_point(
                                int(raw_point[0]), int(raw_point[1]),
                            )
                    # 用 _click_module_point：按下即登记 held 并在 finally 抬起，
                    # 停止信号落在按住窗口内也不会让物理键卡住。
                    self._click_module_point(
                        fallback_x, fallback_y,
                        str(action.get("button", "left")), 1, hwnd,
                    )
                if action.get("show_result_notice") and self.on_notice:
                    self.on_notice(
                        f"备用模板已出现：{Path(fallback_template).name} · "
                        f"({fallback_x}, {fallback_y})",
                        3500,
                    )
                if action.get("fallback_on_match", "回到主模板的检测") == "直接退出识别":
                    self._status(
                        f"备用模板 {Path(fallback_template).name} 已出现，退出识图",
                    )
                    return
                click_text = "点击后" if action.get("fallback_click", True) else ""
                self._status(
                    f"备用模板 {Path(fallback_template).name} 已出现，{click_text}回到主模板检测",
                )
                self._wait(interval_ms)
                continue
            if blocking_timeout_enabled and (time.perf_counter() - start) * 1000 >= timeout_ms:
                timeout_subject = (
                    str(module_obj.get("name") or "读取数字")
                    if number_module else
                    str(module_obj.get("name") or "识别文字")
                    if text_module else
                    str(module_obj.get("name") or Path(template).name or "模块")
                )
                self._trace(
                    f"模块 {timeout_subject} 阻塞等待达到 {timeout_ms} ms，"
                    "按失败分支处理（默认跳过当前脚本行）",
                    module_detail=True,
                )
                if action.get("show_result_notice") and self.on_notice:
                    self.on_notice(
                        f"阻塞模块超时：{timeout_subject} · {timeout_ms} ms",
                        3500,
                    )
                return self._module_result_route(
                    action, module_obj, succeeded=False,
                    result_label=f"阻塞超时 {timeout_ms} ms",
                    hwnd=hwnd, script_stack=script_stack, depth=depth,
                )
            if module_timeout_enabled and (time.perf_counter() - start) * 1000 >= module_timeout_ms:
                segment = list(module_obj.get("on_timeout_actions") or [])
                timeout_subject = (
                    str(module_obj.get("name") or module_obj.get("expected_text") or "识别文字")
                    if text_module else str(module_obj.get("name") or "").strip()
                    or Path(template).name
                )
                self._trace(
                    f"模块 {timeout_subject} 连续 {module_timeout_ms} ms 未识别到，"
                    f"执行超时代码段（{len(segment)} 个动作）",
                    module_detail=True,
                )
                if depth >= MAX_SCRIPT_REF_DEPTH:
                    raise RuntimeError("模块超时代码段嵌套过深，已停止执行")
                if segment:
                    self._run_action_sequence(
                        segment, hwnd,
                        script_stack=script_stack, depth=depth + 1,
                    )
                return self._module_result_route(
                    action, module_obj, succeeded=False,
                    result_label="读取结果：未读取到数字" if number_module else None,
                    hwnd=hwnd, script_stack=script_stack, depth=depth,
                )
            if not wait_forever and (time.perf_counter() - start) * 1000 >= timeout_ms:
                subject = (
                    str(module_obj.get("name") or "读取数字")
                    if number_module else "识别文字" if text_module else Path(template).name
                )
                if action.get("show_result_notice") and self.on_notice:
                    self.on_notice(
                        f"未读取到数字：{subject} · {timeout_ms} ms 内未读取到数字"
                        if number_module else f"识别文字未找到：{subject} · {timeout_ms} ms 超时"
                        if text_module else
                        f"识图未找到：{subject} · {timeout_ms} ms 超时",
                        3500,
                    )
                self._wait(max(0, int(action.get("timeout_delay_ms", 0))))
                if module_obj is not None:
                    if number_module:
                        self._log_event(
                            f"模块 {subject} 连续 {timeout_ms} ms 未读取到数字，"
                            "按“不等于或未读取到”分支处理"
                        )
                    return self._module_result_route(
                        action, module_obj, succeeded=False,
                        result_label="读取结果：未读取到数字" if number_module else None,
                        hwnd=hwnd, script_stack=script_stack, depth=depth,
                    )
                self._run_failure_segment(action, hwnd, script_stack, depth)
                timeout_action = action.get("on_timeout", "continue")
                if timeout_action == "continue":
                    self._status("识别文字超时，按设置继续" if text_module
                                 else "识图超时，按设置继续")
                    return
                if timeout_action == "end_current_script":
                    self._status("识图超时，结束当前脚本")
                    return "end_current_script", 0
                if timeout_action == "jump":
                    target_id = str(action.get("timeout_jump_action_id", "")).strip()
                    if target_id:
                        self._jump_reason = "识别文字超时" if text_module else "识图超时"
                        return "action_id", target_id
                    self._jump_reason = "识别文字超时" if text_module else "识图超时"
                    return "row", max(1, int(action.get("timeout_jump_row", 1)))
                raise RuntimeError(f"识别文字超时：{subject}" if text_module
                                   else f"识图超时：{subject}")
            self._wait(interval_ms)

        if not text_module:
            show_overlay(match["x"], match["y"], match["width"], match["height"])
        if text_module:
            self._status(f"识别文字命中：{recognized[:40]}")
        else:
            self._status(f"识图成功，相似度 {match['score']:.1%}")
        if action.get("show_result_notice") and self.on_notice:
            self.on_notice(
                f"识别文字命中：{recognized[:40] or '（空）'}"
                if text_module else
                f"识图成功：{Path(template).name} · {match['score']:.1%} · "
                f"({match['center_x']}, {match['center_y']})",
                3500,
            )
        found_delay = (module_obj.get("delay_ms", 0) if module_obj is not None
                       else action.get("found_delay_ms", 0))
        self._wait(max(0, int(found_delay)))
        if module_obj is not None:
            # 实时引用：动作 B 由模块对象决定（点击识别区域/自定义/继续/二次识别/代码段）。
            result = self._after_module_success(module_obj, match, hwnd,
                                                script_stack, depth)
            return result if result is not None else self._module_result_route(
                action, module_obj, succeeded=True,
            )
        if action.get("on_found", "click") == "click":
            x, y = match["center_x"], match["center_y"]
            if action.get("click_target", "match") == "custom":
                raw_point = action.get("click_point", [x, y])
                if isinstance(raw_point, (list, tuple)) and len(raw_point) >= 2:
                    x, y = self._scale_point(int(raw_point[0]), int(raw_point[1]))
            # 同上：识图命中点击也必须登记 held，异常/停止路径才有人补抬起。
            self._click_module_point(x, y, str(action.get("button", "left")), 1, hwnd)
        elif action.get("on_found") == "jump":
            target_id = str(action.get("found_jump_action_id", "")).strip()
            if target_id == NEXT_WORKFLOW_STEP_TARGET_ID:
                self._jump_reason = "识图成功"
                return "next_workflow_step", 0
            if target_id:
                self._jump_reason = "识图成功"
                return "action_id", target_id
            self._jump_reason = "识图成功"
            return "row", max(1, int(action.get("found_jump_row", 1)))
    def _match_fallback_module(self, obj: dict, hwnd: int | None) -> dict | None:
        raw_region = obj.get("region") or []
        region = None
        if len(raw_region) == 4 and int(raw_region[2]) > 0 and int(raw_region[3]) > 0:
            region = self._scale_region(tuple(map(int, raw_region)))
        if obj.get("recognize") == "text":
            # OCR 引擎未就绪时等待（可中断）：F12 能中止，不会卡死在首次导入。
            if self.on_ocr_engine_wait and not self.on_ocr_engine_wait():
                raise PlaybackStopped()
            recognized, boxes = recognize_region_with_boxes(region)
            expected = str(obj.get("expected_text", ""))
            mode = str(obj.get("match_mode", "contains"))
            match = find_expected_match(boxes, expected, mode)
            if match is None and matches_expected(recognized, expected, mode):
                match = ocr_match_center(region)
            return match
        template = resolve_path(str(obj.get("template", "")))
        if not template.is_file():
            return None
        return find_template(
            template,
            min(1.0, max(0.1, float(obj.get("threshold", 0.85)))),
            region,
            ignore_background=bool(obj.get("ignore_background", False)),
            scale=self._template_scale(),
        )
    def _execute_second_match(self, obj: dict, hwnd: int | None,
                              first_match: dict | None = None) -> None:
        """二次识别成功后，按配置点击首次、二次或自定义区域中心。"""
        second = str(obj.get("second_match_template", "")).strip()
        if not second:
            self._status("模块未设置二次识别模板，直接继续")
            return None
        second_path = resolve_path(second)
        threshold = min(1.0, max(0.1, float(obj.get("threshold", 0.85))))
        interval_ms = max(50, int(obj.get("interval_ms", 250)))
        ignore_background = bool(obj.get("ignore_background", False))
        region = None
        # 二次模板沿用它在模块对象仓库中登记的搜索区域；未登记有效区域则全屏。
        raw = registered_template_region(second)
        if isinstance(raw, (list, tuple)) and len(raw) == 4:
            if int(raw[2]) > 0 and int(raw[3]) > 0:
                region = self._scale_region(tuple(map(int, raw)))
        blocking = bool(obj.get("blocking", False))
        timeout_ms = max(0, int(obj.get("second_match_timeout_ms", 3000)))
        start = time.perf_counter()
        while True:
            if self.stop_event.is_set():
                raise PlaybackStopped()
            second_match = find_template(second_path, threshold, region,
                                         ignore_background=ignore_background,
                                         scale=self._template_scale())
            if second_match:
                break
            if not blocking and (time.perf_counter() - start) * 1000 >= timeout_ms:
                self._status(f"二次识别超时：{Path(second_path).name} 未出现，继续")
                return None
            self._wait(interval_ms)
        show_overlay(second_match["x"], second_match["y"],
                     second_match["width"], second_match["height"])
        click_target = str(obj.get("second_match_click_target", "second"))
        if click_target == "first" and first_match:
            x, y = first_match["center_x"], first_match["center_y"]
            target_label = "第一次识别位置"
        elif click_target == "custom_region":
            raw_click_region = obj.get("second_match_click_region", [])
            if isinstance(raw_click_region, (list, tuple)) and len(raw_click_region) == 4:
                click_region = self._scale_region(tuple(map(int, raw_click_region)))
                x = click_region[0] + click_region[2] // 2
                y = click_region[1] + click_region[3] // 2
                target_label = "自定义框选区域"
            else:
                x, y = second_match["center_x"], second_match["center_y"]
                target_label = "第二次识别位置"
        else:
            x, y = second_match["center_x"], second_match["center_y"]
            target_label = "第二次识别位置"
        click_count = max(1, min(9999, int(obj.get("click_count", 1))))
        self._click_module_point(
            x, y, str(obj.get("button", "left")), click_count, hwnd,
        )
        self._status(
            f"二次识别成功，已点击{target_label} ({x}, {y})"
            + (f" × {click_count}" if click_count > 1 else "")
            + f" · {Path(second_path).name}",
        )
        return None
