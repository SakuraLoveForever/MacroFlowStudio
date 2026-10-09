from __future__ import annotations

import time
from pathlib import Path

# mss 很轻（28 个模块），且测试要替换 image_match.mss 注入假抓屏器，
# 所以放在模块级；真正重的是 cv2 / numpy，它们在各自函数里按需导入。
import mss
from mss.exception import ScreenShotError

# 截图失败的异常集合：锁屏 / 屏保 / 独占全屏 / 切换显示模式时 GDI 的 BitBlt
# 会对普通进程返回「拒绝访问」（WinError 5）。调用方据此把「截图暂时不可用」
# 与「这一轮没识别到」区分开，不再让一次瞬时失败打断整个工作流。
# 截图失败的异常集合。mss 只在截图时才需要，所以这里按需导入，
# 让导入本模块本身不拉起截图 / 图像依赖。
def _capture_errors():
    import mss  # noqa: PLC0415 - 按需导入
    try:
        from mss.exception import ScreenShotError
    except ImportError:  # 没装截图依赖时只保留 OSError
        return (OSError,)
    return (ScreenShotError, OSError)


CAPTURE_ERRORS = _capture_errors()

# 单次抓屏的瞬时失败重试：显示模式切换、独占全屏进出时的一两帧失败应当
# 由截图层自己吸收，不升级成动作失败。
CAPTURE_ATTEMPTS = 3
CAPTURE_RETRY_DELAY_S = 0.12


def load_image(path: str | Path) -> np.ndarray | None:
    """Load an image without relying on OpenCV's Windows path handling."""
    import cv2  # noqa: PLC0415 - 按需导入
    import numpy as np  # noqa: PLC0415 - 按需导入
    try:
        encoded = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if encoded.size == 0:
        return None
    return cv2.imdecode(encoded, cv2.IMREAD_COLOR)


def capture_bgr(region: tuple[int, int, int, int] | None = None) -> tuple[np.ndarray, tuple[int, int]]:
    """抓屏；瞬时失败重试几次后仍失败才抛 CAPTURE_ERRORS。

    重试只覆盖「同一时刻系统不让截图」这种可自愈的情况（显示模式切换、
    独占全屏进出）。持续失败（锁屏 / 屏保 / DRM 独占）由调用方决定是继续
    轮询还是收尾报错，因此这里不吞异常。
    """
    import cv2  # noqa: PLC0415 - 按需导入
    import numpy as np  # noqa: PLC0415 - 按需导入
    attempts_left = CAPTURE_ATTEMPTS
    while True:
        attempts_left -= 1
        try:
            with mss.mss() as grabber:
                if region:
                    left, top, width, height = region
                    monitor = {"left": left, "top": top, "width": max(1, width), "height": max(1, height)}
                    origin = (left, top)
                else:
                    monitor = grabber.monitors[0]
                    origin = (int(monitor["left"]), int(monitor["top"]))
                shot = np.asarray(grabber.grab(monitor))
            return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR), origin
        except CAPTURE_ERRORS:
            if attempts_left <= 0:
                raise
            time.sleep(CAPTURE_RETRY_DELAY_S)


def stabilize_row_offsets(
    screen: np.ndarray,
    predicted_offsets: list[int],
    *,
    first_row_bottom: int,
    tolerance: int = 3,
) -> list[int]:
    """Locally align predicted rows to full-width horizontal separators.

    The configured first/second-row spacing remains authoritative.  A detected
    separator may only make a small correction around each independently
    predicted row, so one imperfect row never shifts every row below it.
    """
    import cv2  # noqa: PLC0415 - 按需导入
    import numpy as np  # noqa: PLC0415 - 按需导入
    offsets = [max(0, int(value)) for value in predicted_offsets]
    if not offsets or not isinstance(screen, np.ndarray) or screen.ndim < 2:
        return offsets
    if screen.shape[0] < 3 or screen.shape[1] < 4:
        return offsets

    gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY) if screen.ndim == 3 else screen
    differences = np.abs(np.diff(gray.astype(np.float32), axis=0))
    edge_threshold = max(12.0, float(np.percentile(differences, 90)))
    continuity = np.mean(differences >= edge_threshold, axis=1)
    raw_candidates = [
        index + 1 for index, score in enumerate(continuity)
        if float(score) >= 0.55
    ]
    if not raw_candidates:
        return offsets

    grouped: list[list[int]] = []
    for candidate in raw_candidates:
        if grouped and candidate - grouped[-1][-1] <= 2:
            grouped[-1].append(candidate)
        else:
            grouped.append([candidate])
    candidates = [group[0] for group in grouped]
    tolerance = max(0, int(tolerance))
    first_expected = int(first_row_bottom)
    first_actual = min(candidates, key=lambda value: abs(value - first_expected))
    if abs(first_actual - first_expected) > tolerance:
        return offsets

    corrected = [offsets[0]]
    for offset in offsets[1:]:
        expected = first_actual + offset
        actual = min(candidates, key=lambda value: abs(value - expected))
        candidate_offset = offset + actual - expected
        if abs(actual - expected) > tolerance or candidate_offset <= corrected[-1]:
            corrected.append(offset)
        else:
            corrected.append(candidate_offset)
    return corrected


def find_template(template_path: str | Path, threshold: float = 0.85,
                  region: tuple[int, int, int, int] | None = None,
                  scale: float = 1.0) -> dict | None:
    screen, origin = capture_bgr(region)
    return find_template_in_image(template_path, screen, threshold, origin,
                                  region, scale=scale)



def find_template_in_image(template_path: str | Path, screen: np.ndarray,
                           threshold: float = 0.85,
                           origin: tuple[int, int] = (0, 0),
                           region: tuple[int, int, int, int] | None = None,
                           scale: float = 1.0) -> dict | None:
    """Match one template against an existing screenshot.

    Global detectors call this repeatedly with the same full-desktop screenshot,
    cropping their own regions in memory instead of capturing the desktop again.

    scale 为模板缩放系数：模板录自“录制屏幕”，执行机截图尺寸不同（多屏、
    分辨率/DPI 差异）时，目标在截图里的像素大小与模板不一致会导致匹配度
    下降。调用方按 执行机屏幕宽度 / 录制机屏幕宽度 传入，模板先等比缩放到
    当前尺寸再匹配；匹配结果坐标仍处于截图坐标系，无需换算。
    """
    import cv2  # noqa: PLC0415 - 按需导入
    template = load_image(template_path)
    if template is None:
        raise FileNotFoundError(f"无法读取模板图片：{template_path}")
    if scale and scale != 1.0:
        template_height, template_width = template.shape[:2]
        scaled_height = max(1, round(template_height * scale))
        scaled_width = max(1, round(template_width * scale))
        if (scaled_height, scaled_width) != (template_height, template_width):
            interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
            template = cv2.resize(
                template, (scaled_width, scaled_height), interpolation=interpolation,
            )
    search = screen
    search_origin = (int(origin[0]), int(origin[1]))
    if region:
        left, top, width, height = map(int, region)
        image_height, image_width = screen.shape[:2]
        raw_x1 = left - int(origin[0])
        raw_y1 = top - int(origin[1])
        x1 = max(0, raw_x1)
        y1 = max(0, raw_y1)
        x2 = min(image_width, raw_x1 + max(1, width))
        y2 = min(image_height, raw_y1 + max(1, height))
        if x2 <= x1 or y2 <= y1:
            return None
        search = screen[y1:y2, x1:x2]
        search_origin = (int(origin[0]) + x1, int(origin[1]) + y1)
    th, tw = template.shape[:2]
    sh, sw = search.shape[:2]
    if th > sh or tw > sw:
        return None
    result = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, point = cv2.minMaxLoc(result)
    if score < float(threshold):
        return None
    x = search_origin[0] + point[0]
    y = search_origin[1] + point[1]
    return {
        "x": x, "y": y, "width": tw, "height": th,
        "center_x": x + tw // 2, "center_y": y + th // 2,
        "score": float(score),
    }
