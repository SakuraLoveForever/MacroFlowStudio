"""页面状态：整页特征匹配、当前页面追踪、页面动作执行。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

import macroflow.execution.player.page as page_module
from macroflow.core.page_state import (
    PAGE_DEFAULT_THRESHOLD, PageMatcher, PageTracker,
    feature_similarity, page_feature,
)
from macroflow.execution.player import MacroPlayer


def make_frame(seed: int, size: tuple[int, int] = (180, 320)):
    """随机色块整页画面：不同 seed 之间基本不相关。"""
    rng = np.random.default_rng(seed)
    blocks = rng.integers(0, 255, size=(6, 10, 3), dtype=np.uint8)
    return cv2.resize(blocks, size, interpolation=cv2.INTER_NEAREST)


def save_page(folder: Path, name: str, frame) -> Path:
    path = folder / f"{name}.png"
    self_ok, encoded = cv2.imencode(".png", frame)
    assert self_ok
    encoded.tofile(str(path))
    return path


class PageFeatureTests(unittest.TestCase):
    def test_same_frame_similarity_is_one(self):
        frame = make_frame(1)
        self.assertAlmostEqual(
            feature_similarity(page_feature(frame), page_feature(frame)), 1.0, places=5,
        )

    def test_different_frames_are_clearly_below_threshold(self):
        score = feature_similarity(page_feature(make_frame(1)), page_feature(make_frame(2)))
        self.assertLess(score, 0.5)

    def test_flat_frame_has_zero_feature(self):
        flat = np.full((90, 160, 3), 120, dtype=np.uint8)
        feature = page_feature(flat)
        self.assertEqual(float(np.abs(feature).max()), 0.0)


class PageMatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.frame_a = make_frame(1)
        self.frame_b = make_frame(2)
        save_page(self.folder, "页面A", self.frame_a)
        save_page(self.folder, "页面B", self.frame_b)
        self.matcher = PageMatcher(self.folder)

    def tearDown(self):
        self.temp.cleanup()

    def test_lists_page_names_from_library(self):
        self.assertEqual(self.matcher.page_names(), ["页面A", "页面B"])

    def test_match_picks_the_matching_page(self):
        match = self.matcher.match(self.frame_a)
        self.assertIsNotNone(match)
        self.assertEqual(match.name, "页面A")
        self.assertGreater(match.score, 0.99)
        self.assertGreater(match.margin, 0.5)

    def test_match_returns_none_below_threshold(self):
        self.assertIsNone(self.matcher.match(make_frame(9), threshold=0.99))

    def test_similarity_to_named_page(self):
        path = self.matcher.page_path("页面B")
        self.assertGreater(self.matcher.similarity(self.frame_b, path), 0.99)
        self.assertLess(self.matcher.similarity(self.frame_a, path), 0.5)

    def test_similarity_returns_none_for_missing_image(self):
        self.assertIsNone(self.matcher.similarity(self.frame_a, self.folder / "无.png"))

    def _near_duplicate(self):
        """与页面A几乎相同、只有中心一小块不同（模拟真实近似页面）。"""
        near = self.frame_a.copy()
        near[85:93, 155:163] = 255
        return near

    def test_prefer_keeps_previous_page_when_scores_are_close(self):
        save_page(self.folder, "页面A2", self._near_duplicate())
        matcher = PageMatcher(self.folder)
        plain = matcher.match(self.frame_a, threshold=0.95)
        kept = matcher.match(self.frame_a, threshold=0.95, margin=0.02, prefer="页面A2")
        self.assertEqual(plain.name, "页面A")
        self.assertEqual(kept.name, "页面A2")

    def test_near_duplicate_pages_are_flagged_ambiguous(self):
        save_page(self.folder, "页面A2", self._near_duplicate())
        matcher = PageMatcher(self.folder)
        match = matcher.match(self.frame_a, threshold=0.9, margin=0.02)
        self.assertEqual(match.name, "页面A")
        self.assertEqual(match.second_name, "页面A2")
        self.assertTrue(match.ambiguous)

    def test_clear_page_is_not_ambiguous(self):
        match = self.matcher.match(self.frame_a)
        self.assertFalse(match.ambiguous)
        self.assertGreater(match.margin, 0.5)

    def test_matcher_without_folder_returns_none(self):
        self.assertIsNone(PageMatcher(None).match(self.frame_a))


class PageTrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.frame_a = make_frame(1)
        self.frame_b = make_frame(2)
        save_page(self.folder, "页面A", self.frame_a)
        save_page(self.folder, "页面B", self.frame_b)
        self.matcher = PageMatcher(self.folder)
        self.frames = [self.frame_a]
        self.captures = 0
        self.changes = []

    def tearDown(self):
        self.temp.cleanup()

    def capture(self):
        self.captures += 1
        return self.frames[min(self.captures, len(self.frames)) - 1], (0, 0)

    def test_throttles_capture_by_interval(self):
        tracker = PageTracker(self.matcher, self.capture, interval_ms=250)
        tracker.poll(now=10.0)
        self.assertEqual(self.captures, 1)
        tracker.poll(now=10.1)
        self.assertEqual(self.captures, 1)
        tracker.poll(now=10.3)
        self.assertEqual(self.captures, 2)
        self.assertEqual(tracker.current.name, "页面A")

    def test_reports_change_once_per_page(self):
        self.frames = [self.frame_a, self.frame_b, self.frame_b]
        tracker = PageTracker(self.matcher, self.capture, interval_ms=250,
                              on_change=self.changes.append)
        for now in (0.0, 1.0, 2.0):
            tracker.poll(now=now)
        self.assertEqual([match.name for match in self.changes], ["页面A", "页面B"])

    def test_capture_failure_keeps_last_page(self):
        self.frames = [self.frame_a]

        def failing_capture():
            self.captures += 1
            raise RuntimeError("抓屏失败")

        changes = []
        tracker = PageTracker(self.matcher, failing_capture, interval_ms=250,
                              on_change=changes.append)
        tracker.poll(now=0.0)
        self.assertIsNone(tracker.current)
        self.assertEqual(changes, [])


class PageActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.frame_a = make_frame(1)
        self.frame_b = make_frame(2)
        save_page(self.folder, "页面A", self.frame_a)
        save_page(self.folder, "页面B", self.frame_b)
        self.matcher = PageMatcher(self.folder)
        self.clock = SimpleNamespace(value=0.0)
        self.frames: list = []
        self.capture_index = 0
        self.logs: list[str] = []
        self.player = MacroPlayer()
        self.player._page_matcher = lambda: self.matcher
        self.player._capture_page_frame = self._capture
        self.player._wait = self._wait
        self.player._log_event = self.logs.append
        self.player._status = lambda _text: None
        self.player._trace = lambda text, module_detail=False: None
        self.player._click_module_point = Mock()

    def tearDown(self):
        self.temp.cleanup()

    def _capture(self):
        frame = self.frames[min(self.capture_index, len(self.frames) - 1)]
        self.capture_index += 1
        return frame, (0, 0)

    def _wait(self, _milliseconds):
        self.clock.value += 0.2

    def _execute(self, action: dict):
        fake_time = SimpleNamespace(perf_counter=lambda: self.clock.value)
        with patch.object(page_module, "time", fake_time):
            return self.player._execute_page(action, None)

    def test_enter_succeeds_immediately(self):
        self.frames = [self.frame_a]
        result = self._execute({"type": "page", "page": "页面A", "mode": "enter"})
        self.assertIsNone(result)
        self.assertEqual(self.player.current_page_name(), "页面A")
        self.assertEqual(self.capture_index, 1)

    def test_enter_waits_until_page_appears(self):
        self.frames = [self.frame_b, self.frame_b, self.frame_a]
        result = self._execute({
            "type": "page", "page": "页面A", "mode": "enter", "timeout_ms": 5000,
        })
        self.assertIsNone(result)
        self.assertEqual(self.capture_index, 3)

    def test_enter_times_out_and_continues(self):
        self.frames = [self.frame_b]
        result = self._execute({
            "type": "page", "page": "页面A", "mode": "enter", "timeout_ms": 300,
            "on_timeout": "continue",
        })
        self.assertIsNone(result)
        self.assertEqual(self.capture_index, 3)

    def test_enter_timeout_can_end_current_script(self):
        self.frames = [self.frame_b]
        result = self._execute({
            "type": "page", "page": "页面A", "mode": "enter", "timeout_ms": 300,
            "on_timeout": "end_current_script",
        })
        self.assertEqual(result, ("end_current_script", 0))

    def test_enter_timeout_can_jump_to_action(self):
        self.frames = [self.frame_b]
        result = self._execute({
            "type": "page", "page": "页面A", "mode": "enter", "timeout_ms": 300,
            "on_timeout": "jump", "timeout_jump_action_id": "abc",
        })
        self.assertEqual(result, ("action_id", "abc"))

    def test_leave_returns_when_page_is_gone(self):
        self.frames = [self.frame_b]
        result = self._execute({"type": "page", "page": "页面A", "mode": "leave"})
        self.assertIsNone(result)
        self.assertEqual(self.player.current_page_name(), "")

    def test_leave_waits_while_page_is_visible(self):
        self.frames = [self.frame_a, self.frame_a, self.frame_b]
        result = self._execute({
            "type": "page", "page": "页面A", "mode": "leave", "timeout_ms": 5000,
        })
        self.assertIsNone(result)
        self.assertEqual(self.capture_index, 3)

    def test_check_only_captures_once_and_returns_continue(self):
        self.frames = [self.frame_b]
        result = self._execute({"type": "page", "page": "页面A", "mode": "check"})
        self.assertIsNone(result)
        self.assertEqual(self.capture_index, 1)

    def test_check_can_jump_on_mismatch(self):
        self.frames = [self.frame_b]
        result = self._execute({
            "type": "page", "page": "页面A", "mode": "check",
            "on_timeout": "jump", "timeout_jump_action_id": "abc",
        })
        self.assertEqual(result, ("action_id", "abc"))

    def test_missing_page_library_entry_raises(self):
        with self.assertRaises(RuntimeError):
            self._execute({"type": "page", "page": "不存在的页面", "mode": "enter"})

    def test_click_after_success(self):
        self.frames = [self.frame_a]
        self._execute({
            "type": "page", "page": "页面A", "mode": "enter",
            "click": True, "click_point": [100, 200], "click_count": 2,
        })
        self.player._click_module_point.assert_called_once()
        args = self.player._click_module_point.call_args.args
        self.assertEqual(args[:4], (100, 200, "left", 2))

    def test_click_without_point_raises(self):
        self.frames = [self.frame_a]
        with self.assertRaises(RuntimeError):
            self._execute({
                "type": "page", "page": "页面A", "mode": "enter", "click": True,
            })

    def test_page_change_is_logged(self):
        self.frames = [self.frame_a, self.frame_b]
        tracker = self.player._page_tracker()
        tracker.interval_s = 0.0
        tracker.poll(now=0.0)
        tracker.poll(now=1.0)
        self.assertTrue(
            any("页面切换：页面B" in text for text in self.logs), self.logs,
        )


if __name__ == "__main__":
    unittest.main()