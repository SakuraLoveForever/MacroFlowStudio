"""Observation timing retained for the PVE execution plugin."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import unittest
from macroflow.core.timed_detection import sustained_detection

class TimedDetectionTests(unittest.TestCase):
    def test_present_resets_and_triggers_once_until_state_changes(self):
        state = {}
        for detected, now, expected in [(True, 0, False), (True, 1, False),
                (False, 1.5, False), (True, 2, False), (True, 4, True),
                (True, 8, False), (False, 9, False), (True, 10, False),
                (True, 12, True)]:
            self.assertEqual(sustained_detection(state, detected, 'present', 2000, now), expected)

    def test_absent_and_invalid_observations_reset_timer(self):
        state = {}
        for detected, now, expected in [(False, 0, False), (None, 3, False),
                (False, 4, False), (False, 6, True), (True, 7, False),
                (False, 8, False), (False, 10, True)]:
            self.assertEqual(sustained_detection(state, detected, 'absent', 2000, now), expected)

    def test_configuration_change_restarts_timing(self):
        state = {}
        self.assertFalse(sustained_detection(state, True, 'present', 2000, 0))
        self.assertFalse(sustained_detection(state, True, 'present', 3000, 2))
        self.assertTrue(sustained_detection(state, True, 'present', 3000, 5))

