import time
import unittest
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path
from macroflow.execution.ssjj_state import PlayerState
from macroflow.execution.ssjj_monitor import RouteCapture, SSJJMonitor


def sample(x=0, *, map_id=100001, dead=False):
    return PlayerState(map_id, (x, 0, 519), 0, dead, True, 1)


class CaptureTests(unittest.TestCase):
    def test_records_spacing_endpoint_and_builds_native_action(self):
        capture = RouteCapture(sample())
        for x in [50, 110, 180, 240]:
            capture.feed(sample(x))
        action = capture.finish()
        self.assertEqual(action['type'], 'ssjj_route')
        self.assertEqual(action['map_id'], 100001)
        self.assertEqual(action['points'], [(0, 0, 519), (110, 0, 519), (240, 0, 519)])

    def test_invalid_capture_cannot_be_used(self):
        for changed in [sample(map_id=2), sample(dead=True), sample(5000)]:
            capture = RouteCapture(sample())
            with self.assertRaises(RuntimeError):
                capture.feed(changed)
            with self.assertRaises(RuntimeError):
                capture.finish()
        with self.assertRaises(ValueError):
            RouteCapture(sample()).finish()

    def test_monitor_reads_off_thread_and_closes_reader(self):
        reader = Mock()
        reader.snapshot.return_value = sample()
        with patch('macroflow.execution.ssjj_monitor.SSJJReader', return_value=reader):
            monitor = SSJJMonitor()
            deadline = time.monotonic() + 2
            while monitor.status()[0] is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(monitor.status()[0], sample())
            monitor.close()
        reader.close.assert_called_once_with()
        self.assertFalse(monitor.thread.is_alive())

    def test_native_action_dispatches_without_python_file(self):
        from macroflow.execution.player import MacroPlayer
        action = {'type': 'ssjj_route', 'map_id': 100001,
                  'points': [(0, 0, 519), (300, 0, 519)]}
        player = MacroPlayer()
        with patch('macroflow.execution.ssjj_navigation.run_route_action') as run:
            player._execute_action(action, None)
            run.assert_called_once_with(player, action)

    def test_toolbar_exposes_coordinate_panel(self):
        from macroflow.ui.app.shell import ShellMixin
        self.assertTrue(any(method == 'open_ssjj_coordinates'
                            for _, method, _ in ShellMixin._script_action_button_specs()))

    def test_route_summary_shows_map_and_point_count(self):
        from macroflow.ui.app.summaries import action_summary
        kind, detail, _ = action_summary({'type': 'ssjj_route', 'map_id': 100001,
                                        'points': [(0, 0, 0), (300, 0, 0)]})
        self.assertIn('坐标', kind)
        self.assertIn('100001', detail)
        self.assertIn('2', detail)

    def test_route_survives_script_save_and_load(self):
        import tempfile
        from pathlib import Path
        from macroflow.core.models import MacroScript
        from macroflow.core.storage import load_script, save_script
        capture = RouteCapture(sample())
        capture.feed(sample(300))
        script = MacroScript(name='坐标路线', actions=[capture.finish()])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'route.json'
            save_script(script, path)
            loaded = load_script(path)
        action = loaded.actions[0]
        self.assertEqual(action['type'], 'ssjj_route')
        self.assertEqual(action['points'], [[0, 0, 519], [300, 0, 519]])

    def test_native_adapter_uses_checked_input_and_closes_handle(self):
        from macroflow.execution.ssjj_navigation import run_route_action
        from macroflow.execution.player import MacroPlayer
        player = MacroPlayer()
        with patch('macroflow.execution.ssjj_navigation.SSJJReader') as factory, patch(
            'macroflow.execution.ssjj_navigation.send_route_input'
        ) as sender, patch('macroflow.execution.ssjj_navigation.follow_route') as follower:
            reader = factory.return_value.__enter__.return_value
            def follow(context, route, *, reader):
                context.ssjj_input(100001, dx=16)
            follower.side_effect = follow
            run_route_action(player, {'map_id': 100001, 'points': [(0, 0, 0), (300, 0, 0)]})
            sender.assert_called_once_with(player, reader, 100001, dx=16, hold_ms=None)
            factory.return_value.__exit__.assert_called_once()

    def test_quit_does_not_discard_an_unsaved_route(self):
        from macroflow.ui.app.scripts import ScriptsMixin
        class Base:
            def _quit_app(self):
                self.exited = True
        class App(ScriptsMixin, Base):
            exited = False
        app = App()
        app._notify = Mock()
        app._ssjj_coordinates = Mock()
        app._ssjj_coordinates.prepare_exit.return_value = False
        app._quit_app()
        self.assertFalse(app.exited)
        app._notify.assert_called_once()
        app._ssjj_coordinates.prepare_exit.return_value = True
        app._quit_app()
        self.assertTrue(app.exited)
