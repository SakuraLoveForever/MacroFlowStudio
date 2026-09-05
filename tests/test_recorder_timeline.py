# -*- coding: utf-8 -*-
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from macroflow.core.models import (  # noqa: E402
    PULSE_DURATION_KEY,
    PULSE_STARTED_AT_KEY,
    RECORDED_AT_KEY,
)
from macroflow.input.recorder import MacroRecorder  # noqa: E402

from pynput import mouse  # noqa: E402


def make_running_recorder(mode="auto"):
    rec = MacroRecorder()
    rec.mode = mode
    rec.running = True
    rec._last_action_time = 50.0
    return rec


class RecorderTimelineTests(unittest.TestCase):
    def test_append_saves_relative_high_precision_timestamp(self):
        rec = make_running_recorder()
        rec._recording_started_at = 50.0
        with patch("macroflow.input.recorder.time.perf_counter", return_value=50.123456):
            rec._append({"type": "key", "vk": 65, "name": "a", "down": True})

        self.assertAlmostEqual(rec.actions[0]["recorded_at_ms"], 123.456, places=3)
        self.assertIn("recorded_at_ms", rec.actions[0])
        self.assertEqual(rec.actions[0][RECORDED_AT_KEY], rec.actions[0]["recorded_at_ms"])

    def test_absolute_click_keeps_coordinates_and_mode(self):
        rec = make_running_recorder(mode="absolute")
        rec._on_click(320, 240, mouse.Button.left, True)

        self.assertEqual(rec.actions[-1]["mode"], "absolute")
        self.assertEqual((rec.actions[-1]["x"], rec.actions[-1]["y"]), (320, 240))

    def test_relative_click_flushes_pending_move_before_button(self):
        rec = make_running_recorder(mode="relative")
        rec._raw_dx, rec._raw_dy = 4, -2
        rec._raw_last_flush = 0.0
        rec._on_click(900, 700, mouse.Button.left, True)

        self.assertEqual([action["type"] for action in rec.actions], ["mouse_move", "mouse_button"])
        self.assertEqual((rec.actions[0]["dx"], rec.actions[0]["dy"]), (4, -2))

    def test_injected_pulse_saves_start_and_duration(self):
        rec = make_running_recorder()
        rec._recording_started_at = 100.0
        with patch("macroflow.input.recorder.time.perf_counter", side_effect=[100.0, 100.024]):
            rec._on_raw_move(10, 20, injected=True)
            rec._on_raw_move(5, -2, injected=True)
            rec._flush_injected(force=True)

        action = rec.actions[0]
        self.assertEqual(action[PULSE_STARTED_AT_KEY], 0.0)
        self.assertAlmostEqual(action[PULSE_DURATION_KEY], 24.0, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
