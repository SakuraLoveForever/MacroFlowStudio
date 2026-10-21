"""战斗端内存页面动作测试：不连游戏，快照与时钟全部替换。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import macroflow.execution.player.page as page_module
from macroflow.execution.player import MacroPlayer


class BattlePageActionTests(unittest.TestCase):
    def setUp(self):
        self.clock = SimpleNamespace(value=0.0)
        self.logs: list[str] = []
        self.traces: list[str] = []
        self.snapshots: list[dict[str, bool] | None] = []
        self.snapshot_calls = 0
        self.player = MacroPlayer()
        self.player.battle_ui_snapshot = self._snapshot
        self.player._wait = self._wait
        self.player._log_event = self.logs.append
        self.player._status = lambda _text: None
        self.player._trace = lambda text, module_detail=False: self.traces.append(text)
        self.player._click_module_point = Mock()

    def _snapshot(self):
        if not self.snapshots:
            return None
        index = min(self.snapshot_calls, len(self.snapshots) - 1)
        self.snapshot_calls += 1
        return self.snapshots[index]

    def _wait(self, _milliseconds):
        self.clock.value += 0.2

    def _execute(self, action: dict):
        fake_time = SimpleNamespace(perf_counter=lambda: self.clock.value)
        with patch.object(page_module, "time", fake_time):
            return self.player._execute_page(action, None)

    def test_enter_succeeds_immediately(self):
        self.snapshots = [{"小地图UI": True}]
        result = self._execute({
            "type": "page", "source": "battle_ui", "ui_key": "小地图UI", "mode": "enter",
        })
        self.assertIsNone(result)
        self.assertEqual(self.snapshot_calls, 1)
        self.player._click_module_point.assert_not_called()

    def test_enter_waits_until_ui_appears(self):
        self.snapshots = [{}, {}, {"小地图UI": True}]
        result = self._execute({
            "type": "page", "source": "battle_ui", "ui_key": "小地图UI",
            "mode": "enter", "timeout_ms": 5000,
        })
        self.assertIsNone(result)
        self.assertEqual(self.snapshot_calls, 3)

    def test_check_is_single_frame(self):
        self.snapshots = [{"小地图UI": True}]
        result = self._execute({
            "type": "page", "source": "battle_ui", "ui_key": "小地图UI", "mode": "check",
        })
        self.assertIsNone(result)
        self.assertEqual(self.snapshot_calls, 1)

    def test_check_mismatch_continues(self):
        self.snapshots = [{"小地图UI": False}]
        result = self._execute({
            "type": "page", "source": "battle_ui", "ui_key": "小地图UI", "mode": "check",
        })
        self.assertIsNone(result)
        self.assertTrue(any("判断失败" in text for text in self.traces))

    def test_leave_waits_until_ui_is_gone(self):
        self.snapshots = [{"对战战绩UI": True}, {"对战战绩UI": True}, {}]
        result = self._execute({
            "type": "page", "source": "battle_ui", "ui_key": "对战战绩UI",
            "mode": "leave", "timeout_ms": 5000,
        })
        self.assertIsNone(result)
        self.assertEqual(self.snapshot_calls, 3)

    def test_timeout_can_end_current_script(self):
        self.snapshots = [{}]
        result = self._execute({
            "type": "page", "source": "battle_ui", "ui_key": "小地图UI",
            "mode": "enter", "timeout_ms": 300, "on_timeout": "end_current_script",
        })
        self.assertEqual(result, ("end_current_script", 0))

    def test_timeout_can_jump_to_action(self):
        self.snapshots = [{}]
        result = self._execute({
            "type": "page", "source": "battle_ui", "ui_key": "小地图UI",
            "mode": "enter", "timeout_ms": 300, "on_timeout": "jump",
            "timeout_jump_action_id": "abc",
        })
        self.assertEqual(result, ("action_id", "abc"))

    def test_timeout_jumps_to_row_when_no_action_id(self):
        self.snapshots = [{}]
        result = self._execute({
            "type": "page", "source": "battle_ui", "ui_key": "小地图UI",
            "mode": "enter", "timeout_ms": 300, "on_timeout": "jump",
            "timeout_jump_row": 7,
        })
        self.assertEqual(result, ("row", 7))

    def test_missing_snapshot_keeps_waiting_then_continues(self):
        self.snapshots = [None]
        result = self._execute({
            "type": "page", "source": "battle_ui", "ui_key": "小地图UI",
            "mode": "enter", "timeout_ms": 300,
        })
        self.assertIsNone(result)
        self.assertTrue(any("读不到战斗内存" in text for text in self.traces))

    def test_missing_ui_key_raises(self):
        with self.assertRaises(RuntimeError):
            self._execute({
                "type": "page", "source": "battle_ui", "ui_key": "", "mode": "enter",
            })

    def test_click_after_success(self):
        self.snapshots = [{"小地图UI": True}]
        self._execute({
            "type": "page", "source": "battle_ui", "ui_key": "小地图UI", "mode": "enter",
            "click": True, "click_point": [100, 200], "click_count": 2,
        })
        self.player._click_module_point.assert_called_once()
        args = self.player._click_module_point.call_args.args
        self.assertEqual(args[:4], (100, 200, "left", 2))

    def test_screen_source_still_uses_page_library(self):
        matcher = Mock()
        matcher.match.side_effect = RuntimeError("library used")
        self.player._page_matcher = lambda: matcher
        self.player.battle_ui_snapshot = lambda: {"小地图UI": True}
        with self.assertRaises(RuntimeError):
            self._execute({"type": "page", "page": "页面A", "mode": "check"})


class BattleStateMixinTests(unittest.TestCase):
    def setUp(self):
        self.player = MacroPlayer()
        self.logs: list[str] = []
        self.player._log_event = self.logs.append

    def test_snapshot_helpers_read_from_one_snapshot_call(self):
        calls = []

        def fake_snapshot():
            calls.append(1)
            return {"小地图UI": True, "对战战绩UI": False}

        self.player.battle_ui_snapshot = fake_snapshot
        self.assertTrue(self.player.battle_ui_visible("小地图UI"))
        self.assertFalse(self.player.battle_ui_visible("对战战绩UI"))
        self.assertEqual(self.player.battle_ui_names(), ["小地图UI"])
        self.assertEqual(len(calls), 3)

    def test_log_battle_once_deduplicates(self):
        self.player._log_battle_once("战斗内存不可用：x")
        self.player._log_battle_once("战斗内存不可用：x")
        self.assertEqual(self.logs, ["战斗内存不可用：x"])

    def test_reader_backoff_skips_connection_attempt(self):
        self.player._battle_reader_retry_at = 1e18
        self.assertIsNone(self.player._battle_reader())
        self.assertIsNone(self.player.battle_ui_snapshot())

    def test_reset_clears_caches_and_sets_backoff(self):
        reader = Mock()
        self.player._battle_reader_cache = reader
        self.player._battle_ui_state_cache = object()
        with patch.object(page_module.time, "perf_counter", return_value=10.0):
            self.player._battle_reset()
        reader.close.assert_called_once()
        self.assertIsNone(self.player._battle_reader_cache)
        self.assertIsNone(self.player._battle_ui_state_cache)
        self.assertGreater(self.player._battle_reader_retry_at, 10.0)


if __name__ == "__main__":
    unittest.main()