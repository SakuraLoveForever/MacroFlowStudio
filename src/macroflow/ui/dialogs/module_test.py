"""Read-only, one-shot module recognition with a short result notice."""
from copy import deepcopy
from queue import Empty, SimpleQueue
from threading import Thread
import tkinter as tk

from macroflow.core.image_match import find_template
from macroflow.core.ocr import (
    extract_ocr_integer, find_expected_match, matches_expected, recognize_region_with_boxes,
)
from macroflow.core.storage import resolve_path
from macroflow.execution.player import running_process_names
from .base import app_windows, show_floating_notice


def recognize_module_once(obj: dict) -> tuple[bool, str]:
    """Observe the primary target only; never execute a module's actions."""
    mode = obj.get("recognize") or "image"
    if obj.get("pure_action") or obj.get("category") == "special" or mode == "none":
        return False, "该模块无需识别，仅包含动作。"
    if mode == "process":
        name = str(obj.get("process_name", "")).strip().lower()
        if not name:
            raise ValueError("请先设置进程名。")
        present = name in running_process_names()
        return present, f"进程{name}{'存在' if present else '不存在'}。"
    raw_region = obj.get("region") or []
    region = None
    if raw_region:
        if len(raw_region) != 4:
            raise ValueError("识别区域需要 x,y,w,h 四个整数。")
        region = tuple(int(value) for value in raw_region)
        if region[2] <= 0 or region[3] <= 0:
            raise ValueError("识别区域的宽高必须大于 0。")
    if mode in ("text", "number"):
        if mode == "number" and region is None:
            raise ValueError("读取数字需要指定识别区域。")
        text, boxes = recognize_region_with_boxes(region)
        if mode == "number":
            number, _ = extract_ocr_integer(text, boxes)
            return number is not None, f"读取数字：{number}" if number is not None else "未识别到数字。"
        expected, match_mode = str(obj.get("expected_text", "")), obj.get("match_mode", "contains")
        present = matches_expected(text, expected, match_mode) or find_expected_match(boxes, expected, match_mode) is not None
        return present, f"识别文字：{text or '（空）'}"
    if mode != "image":
        raise ValueError(f"未知识别方式：{mode}")
    template = str(obj.get("template", "")).strip()
    if not template:
        raise ValueError("请先选择模板图片。")
    threshold = float(obj.get("threshold", .85))
    if not .1 <= threshold <= 1:
        raise ValueError("相似度必须在 0.1 到 1.0 之间。")
    match = find_template(resolve_path(template), threshold, region)
    return match is not None, "已识别到模板图片。" if match is not None else "未识别到模板图片。"


def start_module_recognition_test(parent, obj: dict) -> None:
    """Hide application windows while probing; keep recognition off the Tk thread."""
    if getattr(parent, "_recognition_test_running", False):
        return
    parent._recognition_test_running = True
    obj = deepcopy(obj)
    name = str(obj.get("name") or "当前模块")
    windows = []
    grab = parent.grab_current()
    results = SimpleQueue()

    def finish(success, detail):
        for window, state in windows:
            try:
                if window.winfo_exists():
                    window.state(state)
            except tk.TclError:
                pass
        if grab is not None:
            try:
                if grab.winfo_exists():
                    grab.grab_set()
            except tk.TclError:
                pass
        parent._recognition_test_running = False
        if parent.winfo_exists():
            show_floating_notice(parent, "识别测试成功" if success else "识别测试失败",
                                 f"{name}：{detail}", duration_ms=500)

    def recognize():
        try:
            results.put(recognize_module_once(obj))
        except Exception as exc:
            results.put((False, str(exc)))

    def poll():
        try:
            result = results.get_nowait()
        except Empty:
            parent.after(20, poll)
        else:
            finish(*result)

    def start():
        try:
            Thread(target=recognize, daemon=True, name="ModuleRecognitionTest").start()
        except Exception as exc:
            finish(False, str(exc))
        else:
            poll()

    try:
        if grab is not None:
            grab.grab_release()
        for window in app_windows(parent):
            state = str(window.state())
            if state in ("normal", "zoomed"):
                windows.append((window, state))
                window.withdraw()
        parent.after(100, start)
    except Exception as exc:
        finish(False, str(exc))
