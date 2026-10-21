"""Continuous state capture and strict route replay; no game or GUI required."""
import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from tests.helpers.core import add_src_to_path
from macroflow.execution.ssjj_monitor import RouteCapture
from macroflow.execution.ssjj_state import PlayerState
from macroflow.execution.ssjj_recording import validate_recording


def state(x=0, *, tick=1, yaw=0):
    return PlayerState(100001, (x, 0, 0), yaw, False, True, tick, 0)


class AutomaticCaptureTests(unittest.TestCase):
    def test_no_marks_full_state_and_automatic_endpoint(self):
        capture = RouteCapture(state())
        capture.feed(state(), when=10)
        capture.feed(state(30, tick=2, yaw=20), when=10.05)
        capture.feed(replace(state(60, tick=3), on_ground=False, pitch=-15), when=10.1)
        capture.feed(state(90, tick=4), when=10.15)
        with patch('macroflow.execution.ssjj_monitor.time.perf_counter', return_value=10.2):
            action = capture.finish()
        self.assertNotIn('checkpoints', action)
        self.assertEqual([s['position'][0] for s in action['trajectory']], [0, 30, 60, 90, 90])
        self.assertEqual(action['trajectory'][1]['yaw'], 20)
        self.assertEqual(action['trajectory'][2]['pitch'], -15)
        self.assertFalse(action['trajectory'][2]['on_ground'])
        self.assertEqual(action['trajectory'][2]['client_time'], 3)
        self.assertEqual(action['trajectory'][-1]['at_ms'], action['duration_ms'])
        validate_recording(action)

    def test_stationary_turns_and_published_dwells_survive(self):
        capture = RouteCapture(state())
        capture.feed(state(), when=0)
        capture.feed(state(), when=.025)
        capture.feed(state(tick=2, yaw=90), when=.05)
        capture.feed(state(tick=3, yaw=90), when=.1)
        self.assertEqual([s['at_ms'] for s in capture.trajectory], [0, 50, 100])
        self.assertEqual([s['yaw'] for s in capture.trajectory], [0, 90, 90])

    def test_missing_state_is_rejected_before_reader_opens(self):
        from macroflow.execution.ssjj_recording import run_recording_action
        from macroflow.execution.player import MacroPlayer
        action = {'map_id': 100001, 'duration_ms': 100, 'inputs': [],
                  'trajectory': [{'at_ms': 0, 'position': [0, 0, 0]},
                                 {'at_ms': 100, 'position': [30, 0, 0]}]}
        with patch('macroflow.execution.ssjj_recording.SSJJReader') as reader:
            with self.assertRaises(ValueError):
                run_recording_action(MacroPlayer(), action)
        reader.assert_not_called()
