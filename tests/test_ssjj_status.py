import math
import struct
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path, FakeVar
from macroflow.execution.ssjj_state import SSJJReader


class StatusReadingTests(unittest.TestCase):
    def reader(self, overrides=None):
        values = {
            '_hp': ('<f', 1748313.0), '_maxHp': ('<f', 1748313.0),
            '_stamina': ('<f', 0.0), '_velocity2D': ('<i', 1603),
            '_moving': ('<?', True), '_viewPitch': ('<f', -5.5),
            '_moveYaw': ('<f', -17.0), '_duckState': ('<i', 3),
            '_currentWeapon': ('<i', 3), '_canReliveTime': ('<i', None),
            '_isFreeze': ('<?', None),
        }
        values.update(overrides or {})
        memory = {}
        addresses = {}
        for index, (name, (fmt, value)) in enumerate(values.items()):
            address = 0x10000 + index * 32
            addresses[name] = address
            memory[address] = struct.pack(fmt, value or 0) + bytes([value is not None])
        reader = SSJJReader.__new__(SSJJReader)
        reader.field = lambda obj, name: addresses[name]
        reader.read = lambda address, size: memory[address][:size]
        return reader

    def test_optional_states_preserve_zero_and_false_and_missing_values(self):
        reader = self.reader({'_moving': ('<?', False), '_hp': ('<f', 0.0)})
        details = reader.read_details(0)
        self.assertEqual(details.hp, 0.0)
        self.assertEqual(details.max_hp, 1748313.0)
        self.assertEqual(details.stamina, 0.0)
        self.assertEqual(details.velocity_2d, 1603)
        self.assertFalse(details.moving)
        self.assertEqual(details.view_pitch, -5.5)
        self.assertEqual(details.move_yaw, -17.0)
        self.assertEqual(details.duck_state, 3)
        self.assertEqual(details.current_weapon, 3)
        self.assertIsNone(details.can_relive_time)
        self.assertIsNone(details.is_frozen)

    def test_nonfinite_optional_numbers_are_unknown(self):
        details = self.reader({'_hp': ('<f', math.nan),
                               '_stamina': ('<f', math.inf)}).read_details(0)
        self.assertIsNone(details.hp)
        self.assertIsNone(details.stamina)
        self.assertEqual(details.velocity_2d, 1603)

    def test_memory_failure_is_not_hidden_as_unknown(self):
        reader = self.reader()
        reader.read = Mock(side_effect=RuntimeError('游戏状态读取失败'))
        with self.assertRaisesRegex(RuntimeError, '读取失败'):
            reader.read_details(0)

    def test_monitor_publishes_details_from_its_background_reader(self):
        from macroflow.execution.ssjj_monitor import SSJJMonitor
        from macroflow.execution.ssjj_state import PlayerState
        details = self.reader().read_details(0)
        reader = SimpleNamespace(
            snapshot=lambda *, include_details=False: PlayerState(
                100001, (1, 2, 3), 0, False, True, 42,
                details=details if include_details else None),
            close=lambda: None,
        )
        with patch('macroflow.execution.ssjj_monitor.SSJJReader', return_value=reader):
            monitor = SSJJMonitor()
            try:
                deadline = time.monotonic() + 2
                while monitor.status()[0] is None and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(monitor.status()[0].details.hp, 1748313.0)
            finally:
                monitor.close()

    def test_basic_snapshot_skips_details_until_requested(self):
        reader = self.reader({
            '_x': ('<f', 1.0), '_y': ('<f', 2.0), '_z': ('<f', 519.0),
            '_viewYaw': ('<f', 0.0), '_isDead': ('<?', False),
            '_onGround': ('<?', True), '_clientTime': ('<i', 42),
            'Current': ('<i', 0),
        })
        reader.ui = 1
        reader.static = lambda klass: 0x20000
        reader.fields = lambda klass: {'MyPlayerEntity': 0}
        reader.ptr = lambda address: 0x30000
        reader.components = lambda player: [0x40000]
        reader.classof = lambda component: 2
        reader.classname = lambda klass: 'BasicInfoComponent'
        reader.map_id = lambda: 100001
        with patch.object(reader, 'read_details', side_effect=AssertionError('额外读取')):
            basic = reader.snapshot()
        self.assertEqual(basic.position, (1.0, 2.0, 519.0))
        self.assertIsNone(basic.details)
        self.assertEqual(reader.snapshot(include_details=True).details.velocity_2d, 1603)


class StatusPanelTests(unittest.TestCase):
    def test_status_formats_missing_values_without_hiding_zero(self):
        from macroflow.execution.ssjj_state import PlayerState
        from macroflow.ui.dialogs.ssjj_coordinates import format_player_details
        details = StatusReadingTests().reader({'_moving': ('<?', False)}).read_details(0)
        sample = PlayerState(100001, (1, 2, 3), 0, False, True, 42, details=details)
        text = format_player_details(sample)
        self.assertIn('1,748,313', text)
        self.assertIn('100.0%', text)
        self.assertIn('体力 0', text)
        self.assertIn('静止', text)
        self.assertIn('复活时间（原始值） 未知', text)
        self.assertIn('冻结 未知', text)
        self.assertIn('状态时间 42', text)
        self.assertNotIn('1,748,313', format_player_details(None))
        self.assertIn('未知', format_player_details(None))

    def test_missing_or_zero_max_health_does_not_compute_a_percentage(self):
        from macroflow.execution.ssjj_state import PlayerState
        from macroflow.ui.dialogs.ssjj_coordinates import format_player_details
        for maximum in [None, 0.0]:
            with self.subTest(maximum=maximum):
                details = StatusReadingTests().reader({'_maxHp': ('<f', maximum)}).read_details(0)
                sample = PlayerState(100001, (1, 2, 3), 0, False, True, 42, details=details)
                self.assertNotIn('%', format_player_details(sample))

    def test_toggle_expands_and_collapses_without_changing_capture(self):
        from macroflow.ui.dialogs.ssjj_coordinates import SSJJCoordinatePanel

        class Frame:
            visible = False

            def pack(self, **kwargs):
                self.visible = True

            def pack_forget(self):
                self.visible = False

        panel = SSJJCoordinatePanel.__new__(SSJJCoordinatePanel)
        panel.status_expanded = False
        panel.status_frame = Frame()
        panel.status_button = Mock()
        panel.pending_action = {'points': [(1, 2, 3)]}
        panel.toggle_status()
        self.assertTrue(panel.status_frame.visible)
        panel.toggle_status()
        self.assertFalse(panel.status_frame.visible)
        self.assertEqual(panel.pending_action, {'points': [(1, 2, 3)]})

    def test_expired_snapshot_clears_expanded_details(self):
        from macroflow.ui.dialogs.ssjj_coordinates import SSJJCoordinatePanel
        panel = SSJJCoordinatePanel.__new__(SSJJCoordinatePanel)
        panel.closed = False
        panel.monitor = SimpleNamespace(status=lambda: (None, '游戏已退出', False, 0))
        panel.coordinates = FakeVar('old coordinates')
        panel.details = FakeVar('血量 100')
        panel.start_button = panel.save_button = Mock()
        panel.pending_action = None
        panel.window = Mock()
        panel.poll()
        self.assertIn('退出', panel.coordinates.get())
        self.assertNotIn('100', panel.details.get())
        self.assertIn('未知', panel.details.get())
