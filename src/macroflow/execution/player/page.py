"""页面动作与当前页面追踪：大厅页面用整页截图特征快速判定。

页面库在 images/全屏；动作语义：
- enter：等待页面出现（timeout_ms=0 一直等）；
- leave：等待页面消失；
- check：立即判断一次，不等待。
超时后按 on_timeout 继续/结束脚本/跳转；成功后可选点击固定坐标。
"""

from __future__ import annotations

from macroflow.core.image_match import CAPTURE_ERRORS, capture_bgr
from macroflow.core.page_state import (
    PAGE_DEFAULT_THRESHOLD, PAGE_LIBRARY_DIR, PAGE_TRACK_INTERVAL_MS,
    PageMatch, PageMatcher, PageTracker,
)
from macroflow.core.storage import BASE_DIR
import time

from .control import PlaybackStopped

PAGE_MODES = ("enter", "leave", "check")
PAGE_ACTION_DEFAULT_INTERVAL_MS = 150
PAGE_ACTION_DEFAULT_TIMEOUT_MS = 3000


class PageMixin:
    """页面动作执行、当前页面追踪与页面库缓存。"""

    def _page_matcher(self) -> PageMatcher:
        matcher = getattr(self, "_page_matcher_cache", None)
        if matcher is None:
            matcher = self._page_matcher_cache = PageMatcher(BASE_DIR / PAGE_LIBRARY_DIR)
        return matcher

    def _page_capture_region(self) -> tuple[int, int, int, int] | None:
        target = getattr(self, "_target_screen", None)
        if not target:
            return None
        return (
            int(target.get("left", 0)), int(target.get("top", 0)),
            int(target.get("width", 0)), int(target.get("height", 0)),
        )

    def _capture_page_frame(self):
        region = self._page_capture_region()
        if region is None:
            raise RuntimeError("页面识别需要目标窗口所在显示器，请重新绑定窗口")
        return capture_bgr(region)

    def _page_tracker(self) -> PageTracker:
        tracker = getattr(self, "_page_tracker_cache", None)
        if tracker is None:
            tracker = self._page_tracker_cache = PageTracker(
                self._page_matcher(), self._capture_page_frame,
                interval_ms=PAGE_TRACK_INTERVAL_MS,
                on_change=self._on_page_change,
            )
        return tracker

    def _track_page(self) -> None:
        """等待片段里顺带更新当前页面；失败不打断正常执行。"""
        tracker = getattr(self, "_page_tracker_cache", None)
        if tracker is None and not self._page_library_exists():
            return
        self._warm_page_library()
        try:
            self._page_tracker().poll()
        except Exception:
            pass

    def _warm_page_library(self) -> None:
        """首次用到页面库时预加载特征：84 张整屏截图约 2 秒，只发生一次。"""
        matcher = self._page_matcher()
        if matcher.ready or not matcher.page_paths():
            return
        if not getattr(self, "_page_library_loading_logged", False):
            self._page_library_loading_logged = True
            self._status("正在加载页面库（首次约 2 秒）…")
        matcher.warm()

    @staticmethod
    def _page_library_exists() -> bool:
        return (BASE_DIR / PAGE_LIBRARY_DIR).is_dir()

    def _on_page_change(self, match) -> None:
        similar = (
            f"，与 {match.second_name} 相近" if match.ambiguous and match.second_name else ""
        )
        self._log_event(f"页面切换：{match.name}（相似度 {match.score:.1%}{similar}）")

    def current_page_name(self) -> str:
        tracker = getattr(self, "_page_tracker_cache", None)
        return tracker.current.name if tracker is not None and tracker.current else ""

    def current_page(self) -> str:
        """立即抓屏判定一次当前页面名；识别不出返回空字符串。"""
        self._warm_page_library()
        tracker = self._page_tracker()
        match = tracker.refresh()
        return match.name if match is not None else ""

    def page_similarity(self, name: str = "") -> float:
        """当前画面与指定页面截图的相似度；未指定页面时按当前页面比较。"""
        matcher = self._page_matcher()
        target = str(name or "").strip() or self.current_page_name()
        if not target:
            return 0.0
        page_path = matcher.page_path(target)
        if page_path is None:
            raise ValueError(f"页面库中没有「{target}」，页面截图应放在 {PAGE_LIBRARY_DIR}")
        frame, _origin = self._capture_page_frame()
        return float(matcher.similarity(frame, page_path) or 0.0)

    def _execute_page(self, action: dict, hwnd: int | None,
                      script_stack: set[str] | None = None,
                      depth: int = 0) -> tuple[str, str | int] | None:
        name = str(action.get("page") or "").strip()
        if not name:
            raise RuntimeError("页面动作未选择页面，请重新编辑这一行")
        matcher = self._page_matcher()
        page_path = matcher.page_path(name)
        if page_path is None:
            raise RuntimeError(f"页面库中没有「{name}」，页面截图应放在 {PAGE_LIBRARY_DIR}")
        self._warm_page_library()
        try:
            threshold = min(1.0, max(0.1, float(action.get("threshold", PAGE_DEFAULT_THRESHOLD))))
        except (TypeError, ValueError):
            threshold = PAGE_DEFAULT_THRESHOLD
        mode = str(action.get("mode", "enter"))
        if mode not in PAGE_MODES:
            mode = "enter"
        try:
            interval_ms = max(50, int(action.get("interval_ms", PAGE_ACTION_DEFAULT_INTERVAL_MS)))
        except (TypeError, ValueError):
            interval_ms = PAGE_ACTION_DEFAULT_INTERVAL_MS
        try:
            timeout_ms = max(0, int(action.get("timeout_ms", PAGE_ACTION_DEFAULT_TIMEOUT_MS)))
        except (TypeError, ValueError):
            timeout_ms = PAGE_ACTION_DEFAULT_TIMEOUT_MS
        if mode == "check":
            timeout_ms = 0

        action_label = {"enter": "出现", "leave": "消失", "check": "存在"}[mode]
        self._trace(
            f"页面动作：{'立即判断' if mode == 'check' else '等待'}页面 {name} {action_label}"
            + ("" if mode == "check" or timeout_ms <= 0 else f"（最多 {timeout_ms} ms）"),
            module_detail=True,
        )
        start = time.perf_counter()
        score = None
        while True:
            if self.stop_event.is_set():
                raise PlaybackStopped()
            try:
                frame, _origin = self._capture_page_frame()
            except CAPTURE_ERRORS as exc:
                self._note_capture_failure(exc)
                score = None
            else:
                self._clear_capture_failure()
                score = matcher.similarity(frame, page_path)
            present = None if score is None else score >= threshold
            matched = bool(present) if mode in ("enter", "check") else present is False
            if matched:
                break
            timed_out = (
                mode != "check" and timeout_ms > 0
                and (time.perf_counter() - start) * 1000 >= timeout_ms
            )
            if mode == "check" or timed_out:
                return self._page_timeout(
                    action, name, timeout_ms, mode, score,
                )
            self._wait(interval_ms)

        # 命中：更新当前页面缓存，再执行可选的点击与后续动作。
        tracker = self._page_tracker()
        tracker.current = (
            PageMatch(name, page_path, float(score or 0.0)) if mode != "leave" else None
        )
        result_text = f"页面{'已出现' if mode != 'leave' else '已消失'}：{name}"
        if score is not None:
            result_text += f"（相似度 {score:.1%}）"
        self._status(result_text)
        self._log_event(result_text)
        self._trace(result_text, module_detail=True)
        if action.get("show_result_notice") and self.on_notice:
            self.on_notice(result_text, 3500)
        if action.get("click"):
            raw_point = action.get("click_point") or []
            if len(raw_point) != 2:
                raise RuntimeError("页面动作勾选了成功后点击，但没有设置点击坐标")
            x, y = self._scale_point(int(raw_point[0]), int(raw_point[1]))
            self._click_module_point(
                x, y, str(action.get("button", "left")),
                max(1, int(action.get("click_count", 1))), hwnd,
            )
        return None

    def _page_timeout(self, action: dict, name: str, timeout_ms: int, mode: str,
                      score) -> tuple[str, str | int] | None:
        elapsed = "" if mode == "check" else f"（{timeout_ms} ms）"
        subject = "消失" if mode == "leave" else "出现"
        detail = self._page_timeout_text(mode, name, elapsed, score)
        self._trace(detail, module_detail=True)
        self._log_event(detail)
        if action.get("show_result_notice") and self.on_notice:
            self.on_notice(detail, 3500)
        behavior = str(action.get("on_timeout", "continue"))
        if behavior == "continue":
            self._status(f"页面 {name} 未{subject}，按设置继续")
            return None
        if behavior == "end_current_script":
            self._status(f"页面 {name} 未{subject}，结束当前脚本")
            return "end_current_script", 0
        if behavior == "jump":
            self._jump_reason = f"页面 {name} 未{subject}"
            target_id = str(action.get("timeout_jump_action_id", "")).strip()
            if target_id:
                return "action_id", target_id
            return "row", max(1, int(action.get("timeout_jump_row", 1)))
        raise RuntimeError(detail)

    @staticmethod
    def _page_timeout_text(mode: str, name: str, elapsed: str, score) -> str:
        if mode == "leave":
            text = f"页面动作超时{elapsed}：{name} 一直未消失"
        elif mode == "check":
            text = f"页面动作判断失败：当前不是页面 {name}"
        else:
            text = f"页面动作超时{elapsed}：{name} 一直未出现"
        if score is not None:
            text += f"（最好相似度 {score:.1%}）"
        return text