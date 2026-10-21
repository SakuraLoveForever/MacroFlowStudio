"""Game-recognized keyboard recording without a keyboard hook or game UI."""
import struct
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path
from tests.test_ssjj_waypoint_playback import state
from macroflow.execution.ssjj_state import SSJJReader
from macroflow.execution.ssjj_monitor import RouteCapture, SSJJMonitor


class GameKeyTests(unittest.TestCase):
    def test_key_worker_is_independent_of_slow_coordinate_reads_and_closes_its_reader(self):
        capture = RouteCapture(state())
        capture.feed(state())
        monitor = SSJJMonitor.__new__(SSJJMonitor)
        monitor.capture, monitor.lock = capture, threading.Lock()
        monitor.reader = Mock()
        reader = Mock()
        reader.key_states.side_effect = [{87: True}, {87: False}]
        waits = []
        stop = SimpleNamespace(is_set=lambda: len(waits) == 2)
        with patch('macroflow.execution.ssjj_monitor.SSJJReader', return_value=reader), \
                patch('macroflow.execution.ssjj_monitor.time.sleep', side_effect=waits.append):
            monitor._run_keys(capture, stop)
        self.assertEqual([(e['vk'], e['down']) for e in capture.inputs], [(87, True), (87, False)])
        self.assertEqual(waits, [.005, .005])
        monitor.reader.snapshot.assert_not_called()
        reader.close.assert_called_once()

    def test_known_keys_ignore_hooks_but_unlisted_modifiers_keep_recording(self):
        monitor = SSJJMonitor.__new__(SSJJMonitor)
        monitor.lock = threading.Lock()
        monitor.capture = RouteCapture(state())
        monitor.capture.feed(state(), when=1)
        monitor.capture.feed_keys({87: False}, when=1)
        monitor.input_recorder = Mock(_recording_started_at=1)
        monitor.reader, monitor.exclude_rect = Mock(), None
        monitor._record_input({'type': 'key', 'vk': 87, 'down': True, 'recorded_at_ms': 10})
        monitor._record_input({'type': 'key', 'vk': 0x10, 'down': True, 'recorded_at_ms': 20})
        monitor.capture.feed_keys({87: True}, when=1.03)
        self.assertEqual([(e['vk'], e['down']) for e in monitor.capture.inputs],
                         [(0x10, True), (87, True)])

    def test_pressed_released_and_already_held_game_keys_are_recorded_once(self):
        capture = RouteCapture(state())
        capture.feed(state(), when=1)
        capture.feed_keys({87: True, 69: False}, when=1)
        capture.feed_keys({87: True, 69: True}, when=1.01)
        capture.feed_keys({87: False, 69: True}, when=1.02)
        capture.feed_keys({87: False, 69: False}, when=1.03)
        self.assertEqual([(e['vk'], e['down'], e['recorded_at_ms']) for e in capture.inputs],
                         [(87, True, 0), (69, True, 10), (87, False, 20), (69, False, 30)])

    def test_software_shortcuts_and_filtered_keys_are_not_game_route_inputs(self):
        capture = RouteCapture(state())
        capture.filter_vks = {69}
        capture.feed(state(), when=1)
        capture.feed_keys({87: True, 69: True, 120: True}, when=1.01)
        self.assertEqual([e['vk'] for e in capture.inputs], [87])

    def test_a_long_sampling_gap_is_reported_instead_of_silently_saving_lost_keys(self):
        capture = RouteCapture(state())
        capture.feed(state(), when=1)
        capture.feed_keys({87: True}, when=1)
        capture.feed_keys({87: False}, when=1.3)
        self.assertIn('按键采样中断', capture.error)

    def test_unity_keycodes_are_mapped_and_shared_enter_keys_are_merged(self):
        reader = SSJJReader.__new__(SSJJReader)
        reader._key_dictionary = 0x10000
        reader.classof = lambda obj: 1
        reader.fields = lambda klass: {'entries': 24, 'count': 64, 'version': 68}
        data = b''.join(struct.pack('<iii?3x', key, -1, key, down) for key, down in
                        [(119, True), (32, False), (273, True), (13, True), (271, False)])
        reader.ptr = lambda addr: 0x20000 if addr == 0x10018 else 5
        reader.u32 = lambda addr: 5 if addr == 0x10040 else 1
        reader.read = lambda addr, size: data
        self.assertEqual(reader.key_states(), {87: True, 32: False, 38: True, 13: True})


if __name__ == '__main__':
    unittest.main()
