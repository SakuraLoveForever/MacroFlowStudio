from __future__ import annotations

from typing import Callable
from macroflow.core.models import (
    ACTION_ID_KEY, END_CURRENT_SCRIPT_LABEL, NEXT_WORKFLOW_STEP_TARGET_ID,
    SCRIPT_START_TARGET_ID, recorded_input_steps, script_ref_repeat_count,
)
from macroflow.core.image_match import (
    CAPTURE_ERRORS, capture_bgr, find_template, find_template_in_image, load_image,
    stabilize_row_offsets,
)
import cv2
from macroflow.core.ocr import (
    extract_ocr_integer, find_expected_match, format_ocr_observation, matches_expected,
    parse_ocr_number_pair,
    ocr_match_center, recognize_image_with_boxes, recognize_region,
    recognize_region_with_boxes,
)
from macroflow.core.storage import (
    DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS, load_script, registered_module_object,
    registered_template_region, resolve_path,
)

from .control import (
    PlaybackStopped,
)

class RowListMixin:
    """列表逐行条件点击：诊断、扫描、快照裁剪与逐行匹配。"""

    def _row_list_result_route(self, action: dict, succeeded: bool,
                               subject: str = "列表逐行点击",
                               hwnd: int | None = None,
                               script_stack: set[str] | None = None,
                               depth: int = 0,
                               ) -> tuple[str, str | int] | None:
        """Resolve a row-list click's success/failure branch."""
        result_text = "成功" if succeeded else "失败"
        if not succeeded:
            self._run_failure_segment(action, hwnd, script_stack, depth)
        behavior_key = "on_found" if succeeded else "on_timeout"
        target_key = "found_jump_action_id" if succeeded else "timeout_jump_action_id"
        legacy_row_key = "found_jump_row" if succeeded else "timeout_jump_row"
        behavior = str(action.get(behavior_key, "continue")).strip() or "continue"
        if behavior == "continue":
            self._status(f"{subject}{result_text}，按设置继续下一行")
            return None
        if behavior == "end_current_script":
            self._status(f"{subject}{result_text}，按设置结束当前最里层脚本")
            return "end_current_script", 0
        if behavior == "jump":
            self._jump_reason = f"{subject}{result_text}"
            target_id = str(action.get(target_key, "")).strip()
            if target_id == NEXT_WORKFLOW_STEP_TARGET_ID:
                return "next_workflow_step", 0
            if target_id:
                return "action_id", target_id
            return "row", max(1, int(action.get(legacy_row_key, 1)))
        raise RuntimeError(f"{subject}{result_text}后按设置停止全部执行")
    def _diagnose_row_list_condition_click(
            self, action: dict, hwnd: int | None,
            result_sink: Callable[[str], None] | None = None,
            image_path: str | None = None) -> dict:
        """Scan every configured row once and log both condition observations.

        This is deliberately separate from playback: it never clicks, retries,
        or follows result routes.  A single snapshot keeps every row's output
        tied to the same screen frame while still evaluating the right side
        when the left side does not match.  ``image_path`` reads a chosen
        full-screen image instead of capturing the current screen.
        """
        regions: dict[str, tuple[int, int, int, int]] = {}
        for key in ("list_region", "left_region", "right_region", "click_region"):
            raw_region = action.get(key, [])
            if not isinstance(raw_region, (list, tuple)) or len(raw_region) != 4:
                raise RuntimeError("列表逐行条件点击的区域无效")
            try:
                region = tuple(map(int, raw_region))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("列表逐行条件点击的区域不是有效坐标") from exc
            if region[2] <= 0 or region[3] <= 0:
                raise RuntimeError("列表逐行条件点击的区域宽高必须大于零")
            regions[key] = region
        list_x, list_y, list_width, list_height = regions["list_region"]
        max_child_bottom = max(
            regions[key][1] + regions[key][3]
            for key in ("left_region", "right_region", "click_region")
        )
        try:
            row_height = int(action.get("row_height", 0))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("列表逐行条件点击的行高不是有效数值") from exc
        if not 0 < row_height <= list_height:
            raise RuntimeError("列表逐行条件点击的行高必须大于零且不能超过列表高度")
        for key in ("left_region", "right_region", "click_region"):
            x, y, width, height = regions[key]
            if x < 0 or y < 0 or x + width > list_width or y + height > list_height:
                raise RuntimeError("列表逐行条件点击的子区域必须位于列表区域内")
        row_offsets = list(range(0, list_height - max_child_bottom + 1, row_height))
        if not row_offsets:
            raise RuntimeError("列表逐行条件点击的首行区域超出列表有效扫描范围")

        if image_path:
            # 用选择的整屏截图做识别：图片像素即录制坐标，不再按当前屏幕缩放。
            source_path = resolve_path(str(image_path))
            source_label = f"图片 {source_path}"
            if not source_path.is_file():
                raise RuntimeError(f"选择的图片不存在：{source_path}")
            image = load_image(source_path)
            if image is None or getattr(image, "ndim", 0) < 2:
                raise RuntimeError(f"选择的图片无法读取：{source_path}")
            image_height, image_width = image.shape[:2]
            if image_width < list_x + list_width or image_height < list_y + list_height:
                raise RuntimeError(
                    f"选择的图片尺寸 {image_width}×{image_height} 覆盖不到列表区域 "
                    f"({list_x},{list_y},{list_width},{list_height})，请选择整屏截图",
                )
            snapshot = (image, (0, 0))
            target_list_region = tuple(regions["list_region"])
            scale_region = lambda region: tuple(region)  # noqa: E731
            stabilize_image, _ = self._row_list_snapshot_crop(snapshot, target_list_region)
        else:
            # 诊断截整屏：结果窗口要显示列表所在的实际画面，而不是一条窄条。
            source_label = "当前屏幕截图"
            snapshot = capture_bgr()
            target_list_region = self._scale_region(regions["list_region"])
            scale_region = self._scale_region
            stabilize_image, _ = self._row_list_snapshot_crop(snapshot, target_list_region)
        self._row_list_active_snapshot = snapshot
        self._row_list_diagnostic_ocr_cache = {}
        scale_y = target_list_region[3] / list_height
        predicted = [round(offset * scale_y) for offset in row_offsets]
        corrected = stabilize_row_offsets(
            stabilize_image, predicted,
            first_row_bottom=round(max_child_bottom * scale_y),
            tolerance=max(1, round(3 * scale_y)),
        )
        left_condition = action.get("left_condition", {})
        right_condition = action.get("right_condition", {})
        if not isinstance(left_condition, dict) or not isinstance(right_condition, dict):
            raise RuntimeError("列表逐行条件点击的条件无效")
        cells: list[dict] = []
        self._diagnostic_log_event(
            f"列表逐行识别诊断开始：共 {len(row_offsets)} 行，"
            f"行高 {row_height} 像素；来源 {source_label}；不执行点击",
            result_sink,
        )
        try:
            for row_index, row_offset in enumerate(row_offsets):
                def translated(key: str) -> tuple[int, int, int, int]:
                    x, y, width, height = regions[key]
                    target = scale_region(
                        (list_x + x, list_y + row_offset + y, width, height),
                    )
                    correction = corrected[row_index] - predicted[row_index]
                    return target[0], target[1] + correction, target[2], target[3]

                translated_regions = {
                    key: translated(key)
                    for key in ("left_region", "right_region", "click_region")
                }
                self._diagnostic_log_event(
                    f"列表逐行识别诊断：第{row_index + 1}行，"
                    f"左区域 {translated_regions['left_region']}，"
                    f"右区域 {translated_regions['right_region']}，"
                    f"点击区域 {translated_regions['click_region']}",
                    result_sink,
                )
                target_x, target_y, target_width, target_height = target_list_region
                if any(
                    x < target_x or y < target_y
                    or x + width > target_x + target_width
                    or y + height > target_y + target_height
                    for x, y, width, height in translated_regions.values()
                ):
                    self._diagnostic_log_event(
                        f"列表逐行识别诊断：第{row_index + 1}行超出列表截图边界，跳过条件识别",
                        result_sink,
                    )
                    continue
                self._row_list_condition_label = f"第{row_index + 1}行左侧"
                left_matched = self._row_list_condition_matches(
                    left_condition, translated_regions["left_region"],
                )
                self._row_list_condition_label = f"第{row_index + 1}行右侧"
                right_matched = self._row_list_condition_matches(
                    right_condition, translated_regions["right_region"],
                )
                self._diagnostic_log_event(
                    f"列表逐行识别诊断：第{row_index + 1}行结果："
                    f"左侧{'命中' if left_matched else '未命中'}，"
                    f"右侧{'命中' if right_matched else '未命中'}",
                    result_sink,
                )
                for column_index, (key, matched) in enumerate(
                        (("left_region", left_matched), ("right_region", right_matched)),
                        start=1,
                ):
                    region = translated_regions[key]
                    cells.append({
                        "row": row_index + 1,
                        "column": column_index,
                        "region": list(region),
                        "text": self._row_list_diagnostic_cell_text(region, matched),
                        "matched": bool(matched),
                    })
        finally:
            self._row_list_active_snapshot = None
            self._row_list_diagnostic_ocr_cache = None
        self._diagnostic_log_event(
            "列表逐行识别诊断结束：已输出全部行结果，未执行点击",
            result_sink,
        )
        return {
            "subject": "列表逐行",
            "image_array": snapshot[0],
            "image_origin": list(snapshot[1]),
            "left_column": 0,
            "right_column": 1,
            "click_column": 2,
            "cells": cells,
        }
    def _row_list_diagnostic_cell_text(self, region: tuple[int, int, int, int],
                                       matched: bool) -> str:
        """One cell's overlay text: OCR output, or the image-match verdict."""
        cache = getattr(self, "_row_list_diagnostic_ocr_cache", None) or {}
        cached = cache.get(tuple(region))
        if cached is not None:
            return str(cached[0] or "").strip() or "未识别到文字"
        return f"图片{'命中' if matched else '未命中'}"
    def _execute_row_list_condition_click(self, action: dict, hwnd: int | None,
                                          script_stack: set[str] | None = None,
                                          depth: int = 0) -> tuple[str, str | int] | None:
        """Click the first list row whose left and right conditions both match."""
        regions: dict[str, tuple[int, int, int, int]] = {}
        for key in ("list_region", "left_region", "right_region", "click_region"):
            raw_region = action.get(key, [])
            if not isinstance(raw_region, (list, tuple)) or len(raw_region) != 4:
                raise RuntimeError("列表逐行条件点击的区域无效")
            try:
                region = tuple(map(int, raw_region))
            except (TypeError, ValueError) as exc:
                raise RuntimeError("列表逐行条件点击的区域不是有效坐标") from exc
            if region[2] <= 0 or region[3] <= 0:
                raise RuntimeError("列表逐行条件点击的区域宽高必须大于零")
            regions[key] = region
        list_x, list_y, list_width, list_height = regions["list_region"]
        max_child_bottom = max(
            regions[key][1] + regions[key][3]
            for key in ("left_region", "right_region", "click_region")
        )
        try:
            row_height = int(action.get("row_height", 0))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("列表逐行条件点击的行高不是有效数值") from exc
        if not 0 < row_height <= list_height:
            raise RuntimeError("列表逐行条件点击的行高必须大于零且不能超过列表高度")
        for key in ("left_region", "right_region", "click_region"):
            x, y, width, height = regions[key]
            if x < 0 or y < 0 or x + width > list_width or y + height > list_height:
                raise RuntimeError("列表逐行条件点击的子区域必须位于列表区域内")
        row_offsets = list(range(0, list_height - max_child_bottom + 1, row_height))
        if not row_offsets:
            raise RuntimeError("列表逐行条件点击的首行区域超出列表有效扫描范围")
        self._trace(
            f"列表逐行点击：使用配置行高 {row_height} 像素，共扫描 {len(row_offsets)} 行",
            module_detail=True,
        )
        try:
            click_count = int(action.get("click_count", 1))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("列表逐行条件点击的连续点击次数不是有效数值") from exc
        if not 1 <= click_count <= 9999:
            raise RuntimeError("列表逐行条件点击的连续点击次数必须是 1 到 9999 之间的整数")
        left_condition = action.get("left_condition", {})
        right_condition = action.get("right_condition", {})
        if not isinstance(left_condition, dict) or not isinstance(right_condition, dict):
            raise RuntimeError("列表逐行条件点击的条件无效")
        while True:
            if self.stop_event.is_set():
                raise PlaybackStopped()
            target_list_region = self._scale_region(regions["list_region"])
            self._row_list_active_snapshot = capture_bgr(target_list_region)
            scale_y = target_list_region[3] / list_height
            predicted_target_offsets = [round(offset * scale_y) for offset in row_offsets]
            corrected_target_offsets = stabilize_row_offsets(
                self._row_list_active_snapshot[0], predicted_target_offsets,
                first_row_bottom=round(max_child_bottom * scale_y),
                tolerance=max(1, round(3 * scale_y)),
            )
            for index, (predicted, corrected) in enumerate(
                zip(predicted_target_offsets, corrected_target_offsets), start=1,
            ):
                if predicted != corrected:
                    self._trace(
                        f"列表逐行点击：第{index}行位置局部校正 {corrected - predicted:+d} 像素",
                        module_detail=True,
                    )
            try:
                for row_index, row_offset in enumerate(row_offsets):

                    def translated(key: str) -> tuple[int, int, int, int]:
                        x, y, width, height = regions[key]
                        target = self._scale_region(
                            (list_x + x, list_y + row_offset + y, width, height),
                        )
                        correction = (
                            corrected_target_offsets[row_index]
                            - predicted_target_offsets[row_index]
                        )
                        return target[0], target[1] + correction, target[2], target[3]

                    translated_regions = {
                        key: translated(key)
                        for key in ("left_region", "right_region", "click_region")
                    }
                    target_list_x, target_list_y, target_list_width, target_list_height = (
                        target_list_region
                    )
                    if any(
                        x < target_list_x or y < target_list_y
                        or x + width > target_list_x + target_list_width
                        or y + height > target_list_y + target_list_height
                        for x, y, width, height in translated_regions.values()
                    ):
                        self._trace(
                            f"列表逐行点击：第{row_index + 1}行局部校正后超出列表边界，已跳过",
                            module_detail=True,
                        )
                        continue

                    self._row_list_condition_label = f"第{row_index + 1}行左侧"
                    if not self._row_list_condition_matches(
                        left_condition, translated_regions["left_region"],
                    ):
                        continue
                    self._row_list_condition_label = f"第{row_index + 1}行右侧"
                    if not self._row_list_condition_matches(
                        right_condition, translated_regions["right_region"],
                    ):
                        continue
                    click_x, click_y, click_width, click_height = translated_regions["click_region"]
                    self._click_module_point(
                        click_x + click_width // 2, click_y + click_height // 2,
                        str(action.get("button", "left")), click_count, hwnd,
                    )
                    self._trace(
                        f"列表逐行点击：第{row_index + 1}行命中，已连续点击 {click_count} 次",
                        module_detail=True,
                    )
                    return self._row_list_result_route(
                        action, succeeded=True, hwnd=hwnd,
                        script_stack=script_stack, depth=depth,
                    )
            finally:
                self._row_list_active_snapshot = None
            if str(action.get("no_match_action", "finish")) != "retry":
                self._trace("列表逐行点击：本轮没有满足条件的行，结束扫描", module_detail=True)
                return self._row_list_result_route(
                    action, succeeded=False, hwnd=hwnd,
                    script_stack=script_stack, depth=depth,
                )
            self._trace("列表逐行点击：本轮没有满足条件的行，等待后从顶部重新扫描",
                        module_detail=True)
            self._wait(max(0, int(action.get("retry_interval_ms", 500))))
    @staticmethod
    def _row_list_snapshot_crop(snapshot, region: tuple[int, int, int, int]):
        screen, origin = snapshot
        left, top, width, height = map(int, region)
        origin_x, origin_y = map(int, origin)
        image_height, image_width = screen.shape[:2]
        x1 = max(0, left - origin_x)
        y1 = max(0, top - origin_y)
        x2 = min(image_width, left - origin_x + width)
        y2 = min(image_height, top - origin_y + height)
        if x2 <= x1 or y2 <= y1:
            raise RuntimeError("列表逐行条件区域超出本轮列表截图")
        return screen[y1:y2, x1:x2], (origin_x + x1, origin_y + y1)
    def _row_list_condition_matches(self, condition: dict,
                                    region: tuple[int, int, int, int],
                                    label: str | None = None) -> bool:
        """Evaluate one row-list condition within its translated row region."""
        label = label or getattr(self, "_row_list_condition_label", "列表条件")
        kind = str(condition.get("type", "")).strip()
        if kind == "image":
            module_key = str(condition.get("module_key", "")).strip()
            module = registered_module_object(module_key)
            if module is None:
                raise RuntimeError(f"列表逐行条件点击引用的图片模块不存在：{module_key}")
            snapshot = getattr(self, "_row_list_active_snapshot", None)
            if snapshot is None:
                match = find_template(
                    resolve_path(str(module.get("template", ""))),
                    float(module.get("threshold", 0.85)), region,
                    ignore_background=bool(module.get("ignore_background", False)),
                    scale=self._template_scale(),
                )
            else:
                screen, origin = snapshot
                match = find_template_in_image(
                    resolve_path(str(module.get("template", ""))), screen,
                    float(module.get("threshold", 0.85)), origin, region,
                    ignore_background=bool(module.get("ignore_background", False)),
                    scale=self._template_scale(),
                )
            matched = match is not None
            module_label = str(module.get("name") or "").strip() or module_key or "未设置模块"
            self._trace(
                f"{label} 图片识别：{module_label}；"
                f"{'命中' if matched else '未命中'}",
                module_detail=True,
            )
            return matched
        if self.on_ocr_engine_wait and not self.on_ocr_engine_wait():
            raise PlaybackStopped()
        snapshot = getattr(self, "_row_list_active_snapshot", None)
        diagnostic_cache = getattr(self, "_row_list_diagnostic_ocr_cache", None)
        cached = diagnostic_cache.get(tuple(region)) if diagnostic_cache is not None else None
        if cached is not None:
            recognized, matches = cached
        elif snapshot is None:
            recognized, matches = recognize_region_with_boxes(region)
        else:
            crop, crop_origin = self._row_list_snapshot_crop(snapshot, region)
            recognized, matches = recognize_image_with_boxes(crop, crop_origin)
            needs_retry = not str(recognized or "").strip()
            if kind == "number" and not needs_retry:
                needs_retry = parse_ocr_number_pair(
                    recognized, str(condition.get("separator", "/")),
                ) is None
            if needs_retry:
                enlarged = cv2.resize(
                    crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC,
                )
                gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
                enhanced = cv2.createCLAHE(
                    clipLimit=2.0, tileGridSize=(4, 4),
                ).apply(gray)
                enhanced = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
                recognized, matches = recognize_image_with_boxes(enhanced, crop_origin)
                self._trace(f"{label} OCR：首次未能解析，已使用同帧图像增强重试",
                            module_detail=True)
            if diagnostic_cache is not None:
                # 诊断期间留存本轮 OCR 结果，供结果窗口逐格显示识别文字。
                diagnostic_cache[tuple(region)] = (recognized, matches)
        recognized_text = str(recognized or "").strip() or "未识别到文字"
        if kind == "text":
            expected = str(condition.get("expected_text", ""))
            mode = str(condition.get("match_mode", "contains"))
            matched = find_expected_match(matches, expected, mode) is not None \
                or matches_expected(recognized, expected, mode)
            self._trace(
                f"{label} OCR：识别成「{recognized_text}」；"
                f"期望「{expected.strip() or '任意文字'}」；"
                f"{'命中' if matched else '未命中'}",
            )
            return matched
        if kind == "number":
            relation = str(condition.get("relation", "equal"))
            if relation not in {"equal", "not_equal"}:
                raise RuntimeError(f"列表逐行条件点击存在未知数字关系：{relation}")
            separator = str(condition.get("separator", "/"))
            pair = parse_ocr_number_pair(recognized, separator)
            if pair is None:
                self._trace(
                    f"{label} OCR：识别成「{recognized_text}」；"
                    f"无法按分隔符「{separator}」解析数字；未命中",
                    module_detail=True,
                )
                return False
            left, right = pair
            matched = left == right if relation == "equal" else left != right
            relation_text = "相等" if relation == "equal" else "不相等"
            self._trace(
                f"{label} OCR：识别成「{recognized_text}」；"
                f"解析为 {left}{separator}{right}，要求{relation_text}；"
                f"{'命中' if matched else '未命中'}",
                module_detail=True,
            )
            return matched
        raise RuntimeError(f"列表逐行条件点击存在未知条件类型：{kind}")
