"""OCR 文字识别：RapidOCR CPU 引擎的惰性单例封装。"""
from __future__ import annotations

import re
import sys
import threading
import unicodedata
from pathlib import Path

import numpy as np

from macroflow.core.image_match import capture_bgr  # noqa: E402

_engine = None
_progress_callback = None
# 引擎初始化可能被启动预加载线程与播放线程并发触发，必须串行化。
_engine_lock = threading.Lock()

RAPIDOCR_MODEL_FILES = {
    "det": "PP-OCRv6_det_small.onnx",
    "cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "rec": "PP-OCRv6_rec_small.onnx",
}


def set_progress_callback(callback) -> None:
    """Set a best-effort callback for coarse OCR initialization progress."""
    global _progress_callback
    _progress_callback = callback


def _report_progress(stage: str, percent: int) -> None:
    callback = _progress_callback
    if callback is not None:
        try:
            callback(stage, max(0, min(100, int(percent))))
        except Exception:
            pass


def _ocr_component_root() -> Path | None:
    """打包版 OCR 组件目录：exe 同目录的 rapidocr_ocr/。"""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent / "rapidocr_ocr"


def _rapidocr_model_paths() -> dict[str, Path]:
    """Resolve the three ONNX models shipped in the pinned RapidOCR wheel."""
    ocr_root = _ocr_component_root()
    if ocr_root is not None:
        model_root = ocr_root / "rapidocr" / "models"
    else:
        model_root = Path(__file__).resolve().parents[3] / ".deps" / "rapidocr" / "models"
    return {name: model_root / filename for name, filename in RAPIDOCR_MODEL_FILES.items()}


def _get_engine():
    """初始化并返回全局 OCR 引擎（线程安全，初始化串行化）。"""
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        _report_progress("准备 OCR 组件", 5)
        ocr_root = _ocr_component_root()
        if ocr_root is not None:
            sys.path.insert(0, str(ocr_root))
        model_paths = _rapidocr_model_paths()
        for index, (name, model_path) in enumerate(model_paths.items()):
            _report_progress(f"正在检查模型 {index + 1}/{len(model_paths)}", 15 + index * 15)
            if not model_path.is_file():
                raise RuntimeError(f"OCR 引擎不可用：缺少 {name} 模型文件 {model_path}")
        try:
            _report_progress("正在导入 RapidOCR", 60)
            from rapidocr import RapidOCR
            from rapidocr.utils.typings import EngineType, ModelType, OCRVersion
        except ImportError as exc:
            raise RuntimeError(
                f"OCR 引擎不可用：缺少 RapidOCR 或 ONNX Runtime 依赖（{exc}）。"
                "打包版请保持 rapidocr_ocr 目录与程序同目录，或重新安装软件；"
                "源码运行请使用 run.bat 启动"
            ) from exc
        _report_progress("正在创建 OCR 引擎", 75)
        params = {
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "EngineConfig.onnxruntime.use_cuda": False,
            "Det.lang_type": "ch",
            "Det.model_type": ModelType.SMALL,
            "Det.ocr_version": OCRVersion.PPOCRV6,
            "Det.model_path": str(model_paths["det"]),
            "Det.limit_side_len": 960,
            "Det.limit_type": "max",
            "Cls.engine_type": EngineType.ONNXRUNTIME,
            "Cls.lang_type": "ch",
            "Cls.model_type": ModelType.MOBILE,
            "Cls.ocr_version": OCRVersion.PPOCRV4,
            "Cls.model_path": str(model_paths["cls"]),
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.lang_type": "ch",
            "Rec.model_type": ModelType.SMALL,
            "Rec.ocr_version": OCRVersion.PPOCRV6,
            "Rec.model_path": str(model_paths["rec"]),
            "Global.max_side_len": 960,
        }
        _engine = RapidOCR(params=params)
        _report_progress("OCR 引擎已加载", 100)
    return _engine


def recognize_image_with_boxes(
    screen: np.ndarray, origin: tuple[int, int] = (0, 0),
) -> tuple[str, list[dict]]:
    """识别图片并返回拼接文字及每一行文字的绝对屏幕坐标。"""
    try:
        result = _get_engine()(screen)
    except Exception as exc:
        raise RuntimeError(f"OCR 识别失败：{exc}") from exc
    result_texts = getattr(result, "txts", None) if result is not None else None
    result_boxes = getattr(result, "boxes", None) if result is not None else None
    result_scores = getattr(result, "scores", None) if result is not None else None
    texts = list(result_texts) if result_texts is not None else []
    boxes = list(result_boxes) if result_boxes is not None else []
    scores = list(result_scores) if result_scores is not None else []
    matches = []
    origin_x, origin_y = map(int, origin)
    for index, text in enumerate(texts):
        if not text or index >= len(boxes):
            continue
        points = np.asarray(boxes[index]).reshape(-1, 2)
        if points.shape != (4, 2):
            continue
        left = int(np.floor(points[:, 0].min())) + origin_x
        top = int(np.floor(points[:, 1].min())) + origin_y
        right = int(np.ceil(points[:, 0].max())) + origin_x
        bottom = int(np.ceil(points[:, 1].max())) + origin_y
        width = max(1, right - left)
        height = max(1, bottom - top)
        matches.append({
            "text": str(text),
            "x": left, "y": top, "width": width, "height": height,
            "center_x": left + width // 2,
            "center_y": top + height // 2,
            "score": float(scores[index]) if index < len(scores) else 1.0,
        })
    return "".join(str(text) for text in texts), matches


def recognize_image(screen: np.ndarray) -> str:
    """识别一张已截取的 BGR 图片，返回全部识别文本拼接。"""
    text, _matches = recognize_image_with_boxes(screen)
    return text


def recognize_region(region: tuple[int, int, int, int] | None = None) -> str:
    """截取指定区域并识别文字，返回全部识别文本拼接（空串 = 没识别到）。"""
    screen, _origin = capture_bgr(region)
    return recognize_image(screen)


def recognize_region_with_boxes(
    region: tuple[int, int, int, int] | None = None,
) -> tuple[str, list[dict]]:
    """截取区域并返回文字及命中文字的绝对屏幕坐标。"""
    screen, origin = capture_bgr(region)
    return recognize_image_with_boxes(screen, origin)


def extract_ocr_integer(recognized: str, matches: list[dict]) -> tuple[int | None, str]:
    """拼接 OCR 数字段并返回整数及原始数字串。

    有坐标的文字框按屏幕 x 坐标从左到右排列；每个框内部保留 OCR 原顺序。
    全角数字先经 NFKC 转为半角。没有任何 0-9 时返回 ``(None, "")``。
    """
    positioned: list[tuple[int, str]] = []
    for match in matches or []:
        text = unicodedata.normalize("NFKC", str(match.get("text", "")))
        digits = "".join(char for char in text if char in "0123456789")
        if not digits:
            continue
        try:
            x = int(match.get("x", match.get("center_x", 0)))
        except (TypeError, ValueError):
            x = 0
        positioned.append((x, digits))
    if positioned:
        raw_digits = "".join(digits for _x, digits in sorted(positioned))
    else:
        text = unicodedata.normalize("NFKC", str(recognized or ""))
        raw_digits = "".join(char for char in text if char in "0123456789")
    return (int(raw_digits), raw_digits) if raw_digits else (None, "")


def parse_ocr_number_pair(recognized: str, separator: str = "/") -> tuple[int, int] | None:
    """Parse the first integer pair separated by a configured OCR symbol.

    OCR may return full-width punctuation or spaces around the separator, so
    both the recognized text and separator are normalized with NFKC first.
    When the configured separator is ``/``, common OCR confusions such as
    ``.``, ``(``, or ``|`` are accepted between the two numbers as well.
    Only the first integer on each side is used; malformed text returns None.
    """
    text = unicodedata.normalize("NFKC", str(recognized or ""))
    token = unicodedata.normalize("NFKC", str(separator or "")).strip()
    if not token:
        return None
    parts = text.split(token, 1)
    if len(parts) != 2:
        if token not in {"/", "\\"}:
            return None
        confused = re.search(
            r"(?<!\d)(\d+)\s*[.．。·•|丨｜\\()（）,，;；]+\s*(\d+)(?!\d)",
            text,
        )
        if confused is None:
            return None
        return int(confused.group(1)), int(confused.group(2))
    left = re.search(r"\d+", parts[0])
    right = re.search(r"\d+", parts[1])
    if left is None or right is None:
        return None
    return int(left.group(0)), int(right.group(0))


_OCR_ZERO_CONFUSIONS = str.maketrans({
    "O": "0", "o": "0", "□": "0", "○": "0", "〇": "0",
})


def matches_expected(recognized: str, expected: str, mode: str = "contains") -> bool:
    """判断识别文字是否命中期望文字。

    期望文字为空时只要识别到任意文字即命中；"equals" 忽略大小写、去掉
    两端空白后整体相等；纯数字的整体匹配会修正常见数字 0 OCR 混淆；
    "contains" 为子串包含（同样忽略大小写）。
    """
    expected = (expected or "").strip()
    if not expected:
        return bool((recognized or "").strip())
    if mode == "equals":
        actual = unicodedata.normalize("NFKC", recognized or "").strip()
        target = unicodedata.normalize("NFKC", expected)
        if target.isascii() and target.isdigit():
            actual = actual.translate(_OCR_ZERO_CONFUSIONS)
        return actual.casefold() == target.casefold()
    return expected.casefold() in (recognized or "").casefold()


def find_expected_match(
    matches: list[dict], expected: str, mode: str = "contains",
) -> dict | None:
    """返回第一条命中期望内容的 OCR 文字框。"""
    for match in matches:
        if matches_expected(str(match.get("text", "")), expected, mode):
            return match
    return None


def format_ocr_observation(
    recognized: str, expected: str, matched: bool, subject: str = "识别文字",
    limit: int = 80,
) -> str:
    """Format one compact OCR observation for the log and execution mini window."""
    text = " ".join(str(recognized or "").split())
    if len(text) > limit:
        text = text[:limit] + "…"
    target = " ".join(str(expected or "").split()) or "任意文字"
    if len(target) > 40:
        target = target[:40] + "…"
    actual = f"识别到「{text}」" if text else "未识别到文字"
    return f"{subject} OCR：{actual}；期望「{target}」· {'命中' if matched else '未命中'}"


def ocr_match_center(region: tuple[int, int, int, int] | None = None) -> dict:
    """识别文字命中时构造的伪匹配：点击识别区域用区域中心，全屏用主屏中心。

    文字识别没有模板那样的精确位置，只有识别区域；区域为空（全屏）时
    退化为主屏中心，保证"点击识别区域"仍有坐标可用。
    """
    if region:
        x, y, w, h = (int(part) for part in region)
    else:
        import mss

        with mss.mss() as grabber:
            monitor = grabber.monitors[1]
        x, y, w, h = (int(monitor[k]) for k in ("left", "top", "width", "height"))
    return {
        "x": x, "y": y, "width": w, "height": h,
        "center_x": x + w // 2, "center_y": y + h // 2,
        "score": 1.0,
    }
