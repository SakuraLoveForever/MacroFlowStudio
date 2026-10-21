import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path
from tests.test_ssjj_waypoint_playback import recording, state


class RecordingIntegrationTests(unittest.TestCase):
    def test_f8_saves_route_and_prevents_starting_a_second_recorder(self):
        from macroflow.ui.app.recording import RecordingMixin
        app = SimpleNamespace(_ssjj_coordinates=Mock(), _notify=Mock())
        app._ssjj_coordinates.monitor.status.return_value = (state(), '', True, 2)
        RecordingMixin.toggle_record(app)
        app._ssjj_coordinates.save.assert_called_once()
        RecordingMixin.start_recording(app)
        app._notify.assert_called_once()

    def test_recording_blocks_script_and_workflow_start_and_f12_stops_capture(self):
        from macroflow.ui.app.execution import ExecutionMixin
        app = Mock()
        app._ssjj_coordinates.monitor.status.return_value = (state(), '', True, 2)
        callback = Mock()
        ExecutionMixin._run_detection_entrypoint(app, callback)
        callback.assert_not_called()
        app._notify.assert_called_once()
        app.recorder, app.worker = SimpleNamespace(running=False), None
        ExecutionMixin.stop_all(app)
        app._ssjj_coordinates.save.assert_called_once()

    def test_toolbar_recording_cannot_leave_pending_callback_during_route_capture(self):
        from macroflow.ui.app.recording import RecordingMixin
        app = SimpleNamespace(_ssjj_coordinates=Mock(), _notify=Mock(), recorder=None, worker=None)
        app._ssjj_coordinates.monitor.status.return_value = (state(), '', True, 2)
        app.toggle_record = lambda **kw: RecordingMixin.toggle_record(app, **kw)
        result = RecordingMixin._start_action_recording(app, Mock(), '录制动作')
        self.assertFalse(result)
        self.assertFalse(hasattr(app, '_pending_recorded_input'))
        app._ssjj_coordinates.save.assert_not_called()

    def test_recording_round_trips_through_normal_script_storage(self):
        from macroflow.core.models import MacroScript
        from macroflow.core.storage import load_script, save_script
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'recording.json'
            save_script(MacroScript(name='录制路线', actions=[recording()]), path)
            action = load_script(path).actions[0]
        self.assertEqual(action['inputs'], recording()['inputs'])
        self.assertEqual(action['trajectory'], recording()['trajectory'])

    def test_watchdog_releases_keyboard_and_mouse_without_controller_poll(self):
        from macroflow.execution.ssjj_recording import ReplayInputs
        from macroflow.execution.player import MacroPlayer
        for failure in ('pause', 'stop', 'focus', 'lease'):
            with self.subTest(failure=failure):
                player, reader = MacroPlayer(), Mock()
                released_key, released_button = threading.Event(), threading.Event()
                with patch('macroflow.input.wininput.send_key',
                    side_effect=lambda vk, down: released_key.set() if not down else None), patch(
                    'macroflow.input.wininput.send_button',
                    side_effect=lambda button, down: released_button.set() if not down else None):
                    session = ReplayInputs(player, reader)
                    try:
                        session.renew()
                        session.send({'type': 'key', 'vk': 69, 'down': True})
                        session.send({'type': 'mouse_button', 'button': 'left', 'down': True})
                        if failure == 'pause':
                            player.pause()
                        elif failure == 'stop':
                            player.stop()
                        elif failure == 'focus':
                            reader.require_foreground.side_effect = RuntimeError('前台')
                        else:
                            session.deadline = time.perf_counter() - .01
                        self.assertTrue(released_key.wait(.3))
                        self.assertTrue(released_button.wait(.3))
                    finally:
                        session.close()

    def test_player_dispatch_and_summary(self):
        from macroflow.execution.player import MacroPlayer
        from macroflow.ui.app.summaries import action_summary
        with patch('macroflow.execution.ssjj_recording.run_recording_action') as run:
            MacroPlayer()._execute_action(recording(), None)
        run.assert_called_once()
        label, detail, _ = action_summary(recording())
        self.assertIn('键鼠', label)
        self.assertIn('2 个纠正点', detail)
        self.assertIn('6 个键鼠输入', detail)

    def test_input_trace_cannot_block_watchdog_release(self):
        from macroflow.execution.ssjj_recording import ReplayInputs
        from macroflow.execution.player import MacroPlayer
        player, reader = MacroPlayer(), Mock()
        released = threading.Event()
        player.on_trace_line = lambda *args, **kwargs: self.assertTrue(released.wait(.4))
        with patch('macroflow.input.wininput.send_key',
                   side_effect=lambda vk, down: released.set() if not down else None):
            session = ReplayInputs(player, reader)
            try:
                session.deadline = time.perf_counter() + .03
                session.send({'type': 'key', 'vk': 69, 'down': True})
                self.assertFalse(player._held_keys)
            finally:
                session.close()
