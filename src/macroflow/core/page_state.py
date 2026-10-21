"""页面状态：用整页截图特征判断当前是哪个游戏页面。

大厅是 Adobe AIR/Flash，没有可读取的页面状态接口；这里用“一次抓屏 + 特征
点积”做页面判定（1920×1080 下约 1~3 ms），据此可以等页面出现/消失、按页面
直接点下一步，而不是让每个模块各自阻塞识图、等超时。

页面库就是 images/全屏 下的整页截图，文件名（去扩展名）即页面名。截图要求
是 16:9 的整屏画面；运行分辨率不同时按比例缩放后再比较。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

PAGE_LIBRARY_DIR = "images/全屏"
PAGE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp"}
# 特征尺寸：64×36 的标准化灰度图。大厅/结算页面的动画只改不到 1% 像素，
# 实测同页分数 0.995 以上、异页大多 0.9 以下；84 页一次矩阵乘约 0.2 ms。
PAGE_FEATURE_SIZE = (64, 36)
# 页面判定阈值：与页面库截图的相关系数。同一页面实机截图 0.99 以上，
# 动画/计时器覆盖时约 0.97；不同页面大多低于 0.9。
PAGE_DEFAULT_THRESHOLD = 0.97
# 当前页面追踪时，第一名与第二名至少差这么多才算“确定是这个页面”，
# 否则视为相似页面组（如 开始战斗 / 战斗引擎），保留上一次页面不瞎跳。
PAGE_DEFAULT_MARGIN = 0.02
# 页面追踪的抓屏间隔：4 帧/秒足够看清页面切换，又不至于和识图抢 CPU。
PAGE_TRACK_INTERVAL_MS = 250


@dataclass(frozen=True)
class PageMatch:
    """一次页面匹配结果。"""

    name: str
    path: Path
    score: float
    second_score: float = 0.0
    second_name: str = ""
    ambiguous: bool = False

    @property
    def margin(self) -> float:
        return self.score - self.second_score


def page_feature(frame) -> "object":
    """把一帧 BGR 截图转成标准化灰度特征向量。"""
    import cv2  # noqa: PLC0415 - 按需导入
    import numpy as np  # noqa: PLC0415 - 按需导入

    image = frame
    if image.ndim != 3:
        raise ValueError("页面特征需要彩色截图")
    small = cv2.resize(image, PAGE_FEATURE_SIZE, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray -= float(gray.mean())
    deviation = float(gray.std())
    if deviation < 1e-6:
        return np.zeros(gray.size, dtype=np.float32)
    return (gray / deviation).ravel()


def feature_similarity(left, right) -> float:
    """两个特征向量的标准化相关度（-1~1，页面匹配只看正相关）。"""
    import numpy as np  # noqa: PLC0415 - 按需导入

    return float(np.dot(left, right) / max(1, left.size))


def load_page_feature(path: Path):
    """读取一张页面截图并计算特征；读不到图片时返回 None。"""
    import cv2  # noqa: PLC0415 - 按需导入
    import numpy as np  # noqa: PLC0415 - 按需导入

    try:
        encoded = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if encoded.size == 0:
        return None
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        return None
    return page_feature(image)


class PageMatcher:
    """页面库：页面名 → 整页截图特征，以及当前帧的页面判定。"""

    def __init__(self, folder: Path | str | None = None):
        self.folder = Path(folder) if folder else None
        self._features: dict[Path, object] = {}
        self._paths: dict[str, Path] = {}
        self._matrix_cache = None
        self._matrix_names: list[str] | None = None

    def set_folder(self, folder: Path | str) -> None:
        self.folder = Path(folder)
        self._features.clear()
        self._paths.clear()
        self._matrix_cache = None
        self._matrix_names = None

    def refresh(self) -> None:
        """重新扫描页面库（用户新存了截图后调用）。"""
        self._paths.clear()
        self._matrix_cache = None
        self._matrix_names = None

    @property
    def folder_ready(self) -> bool:
        return self.folder is not None and self.folder.is_dir()

    def page_paths(self) -> dict[str, Path]:
        """页面名 → 截图路径；保持不变的文件顺序便于界面稳定显示。"""
        if not self._paths:
            if not self.folder_ready:
                return {}
            for path in sorted(self.folder.iterdir(), key=lambda item: item.name.casefold()):
                if path.is_file() and path.suffix.lower() in PAGE_IMAGE_EXTENSIONS:
                    self._paths[path.stem] = path
        return dict(self._paths)

    def page_names(self) -> list[str]:
        return list(self.page_paths())

    def page_path(self, name: str) -> Path | None:
        return self.page_paths().get(str(name).strip())

    def _feature(self, path: Path):
        feature = self._features.get(path)
        if feature is None:
            feature = load_page_feature(path)
            if feature is not None:
                self._features[path] = feature
                self._matrix_cache = None
        return feature

    def _score_matrix(self):
        """全部页面特征堆成的矩阵（行=页面）；首次调用时加载整库。"""
        import numpy as np  # noqa: PLC0415 - 按需导入

        paths = self.page_paths()
        matrix = getattr(self, "_matrix_cache", None)
        if matrix is not None and getattr(self, "_matrix_names", None) == list(paths):
            return matrix
        rows, names = [], []
        for name, path in paths.items():
            feature = self._feature(path)
            if feature is None:
                continue
            rows.append(feature)
            names.append(name)
        matrix = np.vstack(rows) if rows else None
        self._matrix_cache, self._matrix_names = matrix, names
        return matrix

    @property
    def ready(self) -> bool:
        """页面特征是否已全部加载（加载后匹配只需毫秒级）。"""
        paths = self.page_paths()
        return bool(paths) and len(self._features) >= len(paths)

    def warm(self) -> int:
        """预加载全部页面特征（84 张整屏截图约 2 秒），返回成功加载的页面数。"""
        loaded = 0
        for path in self.page_paths().values():
            if self._feature(path) is not None:
                loaded += 1
        return loaded

    def similarity(self, frame, path: Path | str) -> float | None:
        """当前帧与指定页面截图的相似度；页面图片读不到时返回 None。"""
        feature = self._feature(Path(path))
        if feature is None:
            return None
        return feature_similarity(feature, page_feature(frame))

    def match(self, frame, *, threshold: float = PAGE_DEFAULT_THRESHOLD,
              margin: float = PAGE_DEFAULT_MARGIN,
              prefer: str | None = None) -> PageMatch | None:
        """返回最可能的页面；低于阈值时返回 None，相似页面组标记 ambiguous。

        ``prefer`` 用于吸收相似页面之间的抖动：如果上一次认定的页面与第一名
        相差不超过 margin，就维持上一次的页面。
        """
        matrix = self._score_matrix()
        if matrix is None:
            return None
        names = self._matrix_names
        # 每个特征都做过 z-score（均值 0、标准差 1），点积除以维数就是相关系数。
        scores = (matrix @ page_feature(frame)) / float(matrix.shape[1])
        best_index = int(scores.argmax())
        best_score = float(scores[best_index])
        if best_score < float(threshold):
            return None
        chosen = best_index
        if prefer and prefer in names:
            index = names.index(prefer)
            if best_score - float(scores[index]) <= margin:
                chosen = index
        chosen_score = float(scores[chosen])
        others = [index for index in range(len(names)) if index != chosen]
        if others:
            second_index = max(others, key=lambda index: float(scores[index]))
            second_score, second_name = float(scores[second_index]), names[second_index]
        else:
            second_score, second_name = 0.0, ""
        return PageMatch(
            names[chosen], self.page_paths()[names[chosen]], chosen_score,
            second_score, second_name,
            ambiguous=(chosen_score - second_score) < margin,
        )


class PageTracker:
    """节流的当前页面追踪：周期抓屏、判定页面，并在页面变化时回调。"""

    def __init__(self, matcher: PageMatcher, capture, *,
                 interval_ms: int = PAGE_TRACK_INTERVAL_MS,
                 threshold: float = PAGE_DEFAULT_THRESHOLD,
                 margin: float = PAGE_DEFAULT_MARGIN,
                 on_change=None):
        self.matcher = matcher
        self.capture = capture
        self.interval_s = max(0.05, int(interval_ms) / 1000)
        self.threshold = float(threshold)
        self.margin = float(margin)
        self.on_change = on_change
        self.current: PageMatch | None = None
        self._last_poll = 0.0

    def poll(self, now: float | None = None) -> PageMatch | None:
        """到间隔才抓屏判定；返回当前页面（可能沿用上一次结果）。"""
        now = time.perf_counter() if now is None else float(now)
        if now - self._last_poll < self.interval_s:
            return self.current
        self._last_poll = now
        try:
            frame, _origin = self.capture()
        except Exception:
            return self.current
        try:
            found = self.matcher.match(
                frame, threshold=self.threshold, margin=self.margin,
                prefer=self.current.name if self.current else None,
            )
        except Exception:
            return self.current
        previous = self.current
        if found is not None:
            self.current = found
        if (found is not None and (previous is None or previous.name != found.name)
                and self.on_change is not None):
            self.on_change(found)
        return self.current

    def reset(self) -> None:
        self.current = None
        self._last_poll = 0.0

    def refresh(self) -> PageMatch | None:
        """立即抓屏判定一次（忽略节流间隔），供页面动作与 Python 逻辑使用。"""
        self._last_poll = 0.0
        return self.poll()