import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from tests.helpers.core import add_src_to_path
from macroflow.ui.app.recording import RecordingMixin


class RouteRecordingHotkeyTests(unittest.TestCase):
    def test_f8_routes_to_panel_only_when_open_and_normal_recorder_idle(self):
        for closed, running, from_ui, pending, expected in (
            (False, False, False, None, 'route_start'),
            (False, False, False, {'type': 'ssjj_recording'}, 'route_save'),
            (True, False, False, None, 'normal_start'),
            (False, False, True, None, 'normal_start'),
            (False, True, False, None, 'normal_stop'),
        ):
            with self.subTest(closed=closed, running=running, from_ui=from_ui, pending=pending):
                calls = []
                panel = SimpleNamespace(
                    closed=closed, pending_action=pending,
                    monitor=Mock(),
                    start=lambda: calls.append('route_start'),
                    save=lambda: calls.append('route_save'),
                )
                panel.monitor.status.return_value = (None, '', False, 0)
                app = SimpleNamespace(
                    _ssjj_coordinates=panel,
                    recorder=SimpleNamespace(running=running),
                    start_recording=lambda **kw: calls.append('normal_start'),
                    stop_recording=lambda **kw: calls.append('normal_stop'),
                )
                RecordingMixin.toggle_record(app, from_ui=from_ui)
                self.assertEqual(calls, [expected])
