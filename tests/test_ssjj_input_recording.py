import copy
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path
from macroflow.execution.ssjj_monitor import RouteCapture, SSJJMonitor
from macroflow.execution.ssjj_state import PlayerState


def state(x=0, yaw=0, tick=1):
    return PlayerState(100001, (x, 0, 519), yaw, False, True, tick)


def recording():
    return {
        'type': 'ssjj_recording', 'map_id': 100001, 'duration_ms': 500,
        'inputs': [
            {'type': 'key', 'vk': 87, 'down': True, 'recorded_at_ms': 0},
            {'type': 'key', 'vk': 32, 'down': True, 'recorded_at_ms': 100},
            {'type': 'key', 'vk': 32, 'down': False, 'recorded_at_ms': 120},
            {'type': 'mouse_move', 'mode': 'relative', 'dx': -7, 'dy': 3, 'recorded_at_ms': 150},
            {'type': 'mouse_button', 'button': 'right', 'down': True, 'recorded_at_ms': 200},
            {'type': 'scroll', 'dx': 0, 'dy': -1, 'recorded_at_ms': 220},
            {'type': 'mouse_button', 'button': 'right', 'down': False, 'recorded_at_ms': 250},
            {'type': 'key', 'vk': 87, 'down': False, 'recorded_at_ms': 400},
        ],
        'samples': [
            {'at_ms': 0, 'position': [0, 0, 519], 'yaw': 0},
            {'at_ms': 250, 'position': [25, 0, 519], 'yaw': 0},
            {'at_ms': 500, 'position': [50, 0, 519], 'yaw': 0},
        ],
    }


class InputCaptureTests(unittest.TestCase):
    def setUp(self):
        self.centered = self.enterContext(patch(
            'macroflow.input.wininput.is_cursor_near_window_center', return_value=True))
        self.enterContext(patch('macroflow.input.wininput.get_foreground_window_info',
            return_value=SimpleNamespace(hwnd=123)))

    def monitor(self):
        monitor = SSJJMonitor.__new__(SSJJMonitor)
        monitor.lock = threading.Lock()
        monitor.capture = monitor.input_recorder = None
        monitor.latest, monitor.updated, monitor.error = state(), time.monotonic(), ''
        monitor.reader = Mock()
        monitor.exclude_rect = (100, 100, 200, 200)
        self.addCleanup(monitor.cancel_recording)
        return monitor

    def test_monitor_reuses_recorder_for_keyboard_raw_mouse_buttons_and_scroll(self):
        from macroflow.input import recorder as recorder_module
        monitor = self.monitor()
        with patch.object(recorder_module.keyboard, 'Listener') as keyboard, patch.object(
            recorder_module.mouse, 'Listener') as mouse, patch.object(recorder_module, 'RawMouseListener') as raw:
            monitor.start_recording(filter_vks={120})
            recorder = monitor.input_recorder
            recorder._on_press(SimpleNamespace(vk=87, char='w'))
            recorder._on_raw_move(4, -6)
            recorder._on_click(10, 10, recorder_module.mouse.Button.left, True)
            recorder._on_scroll(10, 10, 0, -1)
            recorder._on_click(150, 150, recorder_module.mouse.Button.left, True)
            recorder._on_press(SimpleNamespace(vk=120, char=None))
            action = monitor.stop_recording()
        self.assertEqual([event['type'] for event in action['inputs']],
                         ['key', 'mouse_move', 'mouse_button', 'scroll'])
        self.assertEqual(action['inputs'][1]['dy'], -6)
        self.assertFalse(recorder.running)
        for listener in (keyboard, mouse, raw):
            listener.return_value.stop.assert_called_once()
        self.assertIsNone(monitor.capture)

    def test_recording_discards_inputs_outside_game_and_fails_after_focus_loss(self):
        monitor = self.monitor()
        with patch('macroflow.input.recorder.MacroRecorder') as factory:
            factory.return_value._recording_started_at = time.perf_counter()
            factory.return_value.limit_reached = False
            factory.return_value.unsupported_buttons = set()
            monitor.start_recording()
            monitor.reader.require_foreground.side_effect = RuntimeError('前台')
            monitor._record_input({'type': 'key', 'vk': 87, 'down': True})
            self.assertEqual(monitor.capture.inputs, [])
            monitor.reader.require_foreground.side_effect = None
            monitor._record_input({'type': 'key', 'vk': 87, 'down': True})
            monitor.reader.require_foreground.side_effect = RuntimeError('前台')
            monitor._record_input({'type': 'key', 'vk': 65, 'down': True})
            with self.assertRaisesRegex(RuntimeError, '前台'):
                monitor.stop_recording()

    def test_desktop_mouse_travel_after_panel_start_does_not_become_game_turning(self):
        from macroflow.input import recorder as recorder_module
        monitor = self.monitor()
        with patch.object(recorder_module.keyboard, 'Listener'), patch.object(
            recorder_module.mouse, 'Listener'), patch.object(recorder_module, 'RawMouseListener'):
            monitor.start_recording()
            recorder = monitor.input_recorder
            self.centered.return_value = False
            recorder._on_raw_move(1198, -400)
            self.centered.return_value = True
            recorder._on_raw_move(-7, 0)
            recorder._on_press(SimpleNamespace(vk=87, char='w'))
            self.centered.return_value = False
            recorder._on_raw_move(300, 0)
            recorder._on_release(SimpleNamespace(vk=87, char='w'))
            action = monitor.stop_recording()
        self.assertEqual([event['type'] for event in action['inputs']], ['mouse_move', 'key', 'key'])
        self.assertEqual(action['inputs'][0]['dx'], -7)
        self.assertEqual([(event['vk'], event['down']) for event in action['inputs'][1:]],
                         [(87, True), (87, False)])

    def test_injected_turn_keeps_capture_time_when_flushed_after_cursor_unlock(self):
        from macroflow.input import recorder as recorder_module
        monitor = self.monitor()
        with patch.object(recorder_module.keyboard, 'Listener'), patch.object(
            recorder_module.mouse, 'Listener'), patch.object(recorder_module, 'RawMouseListener'):
            monitor.start_recording()
            recorder = monitor.input_recorder
            recorder._on_press(SimpleNamespace(vk=87, char='w'))
            recorder._on_raw_move(80, 0, injected=True)
            self.centered.return_value = False
            recorder._on_click(150, 150, recorder_module.mouse.Button.left, True)
            action = monitor.stop_recording()
        self.assertEqual(sum(e.get('dx', 0) for e in action['inputs']), 80)
        self.assertEqual(action['inputs'][0]['type'], 'key')

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

    def test_capture_keeps_stationary_samples_and_key_mouse_timing(self):
        with patch('macroflow.execution.ssjj_monitor.time.perf_counter', return_value=10):
            capture = RouteCapture(state())
        capture.feed(state(), when=10.1)
        capture.record_input({'type': 'key', 'vk': 32, 'down': True}, when=10.12)
        capture.record_input({'type': 'mouse_move', 'mode': 'relative', 'dx': 4, 'dy': -8}, when=10.15)
        capture.feed(state(20, yaw=2, tick=2), when=10.2)
        with patch('macroflow.execution.ssjj_monitor.time.perf_counter', return_value=10.3):
            action = capture.finish()
        self.assertEqual(action['type'], 'ssjj_recording')
        self.assertEqual(action['duration_ms'], 300)
        self.assertEqual([event['recorded_at_ms'] for event in action['inputs']], [120, 150])
        self.assertEqual(action['inputs'][1]['dy'], -8)
        self.assertEqual([frame['at_ms'] for frame in action['samples']], [0, 100, 200, 300])
        self.assertEqual(action['samples'][1]['position'], [0, 0, 519])

    def test_stationary_mouse_only_recording_is_valid(self):
        capture = RouteCapture(state())
        capture.record_input({'type': 'mouse_move', 'mode': 'relative', 'dx': 16, 'dy': 0})
        self.assertEqual(capture.finish()['inputs'][0]['dx'], 16)

    def test_recording_round_trips_through_normal_script_storage(self):
        from macroflow.core.models import MacroScript
        from macroflow.core.storage import load_script, save_script
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'recording.json'
            save_script(MacroScript(name='录制路线', actions=[recording()]), path)
            action = load_script(path).actions[0]
        self.assertEqual(action['inputs'], recording()['inputs'])
        self.assertEqual(action['samples'], recording()['samples'])


class ReplayTests(unittest.TestCase):
    def run_recording(self, action=None, snapshot=None, wait_hook=None, focus_hook=None, clock_offset=0,
                      prepare_hook=None):
        from macroflow.execution.ssjj_recording import run_recording_action
        from macroflow.execution.player import MacroPlayer
        player = MacroPlayer()
        self.player = player
        self.logs = []
        player.on_log = self.logs.append
        clock = [clock_offset]
        self.clock = clock
        events = []
        self.events = events
        reader = Mock()
        reader.snapshot.side_effect = snapshot or (lambda: state((clock[0] - clock_offset) * 100, tick=int(clock[0] * 1000)))
        reader.require_foreground.side_effect = focus_hook

        def wait(seconds):
            clock[0] += seconds
            if wait_hook:
                wait_hook(player, clock[0])
            return player.stop_event.is_set()

        with ExitStack() as stack:
            if prepare_hook:
                stack.enter_context(patch('macroflow.execution.ssjj_recording._prepare_start',
                    side_effect=prepare_hook))
            stack.enter_context(patch('macroflow.execution.ssjj_recording.SSJJReader')).return_value.__enter__.return_value = reader
            stack.enter_context(patch('macroflow.execution.ssjj_recording.time.perf_counter', side_effect=lambda: clock[0]))
            stack.enter_context(patch.object(player.stop_event, 'wait', side_effect=wait))
            for name in ('send_key', 'send_move_relative', 'send_button', 'send_scroll'):
                stack.enter_context(patch('macroflow.input.wininput.' + name,
                    side_effect=lambda *args, name=name: events.append((name, args, clock[0]))))
            run_recording_action(player, action or recording())
        return events, clock[0]

    def test_replays_original_inputs_at_their_times_without_extra_steering(self):
        events, elapsed = self.run_recording()
        self.assertEqual([(name, args) for name, args, _ in events], [
            ('send_key', (87, True)), ('send_key', (32, True)), ('send_key', (32, False)),
            ('send_move_relative', (-7, 3)), ('send_button', ('right', True)),
            ('send_scroll', (0, -1)), ('send_button', ('right', False)), ('send_key', (87, False)),
        ])
        for event, expected_ms in zip(events, [0, 100, 120, 150, 200, 220, 250, 400]):
            self.assertAlmostEqual(event[2], expected_ms / 1000, delta=.002)
        self.assertAlmostEqual(elapsed, .5, delta=.002)
        self.assertFalse(self.player._held_keys)

    def test_logs_recorded_input_counts_and_where_deviation_stopped_replay(self):
        with self.assertRaisesRegex(RuntimeError, '偏离录制路线'):
            self.run_recording(snapshot=lambda: state(500 if self.clock[0] >= .2 else 0,
                tick=int(self.clock[0] * 1000)))
        text = '\n'.join(self.logs)
        self.assertIn('键盘 4', text)
        self.assertIn('鼠标 4', text)
        self.assertIn('已发送', text)
        self.assertIn('实际坐标', text)
        self.assertIn('录制坐标', text)

    def test_deviation_or_read_failure_releases_held_keys(self):
        for failure in ('deviation', 'read', 'frozen', 'map', 'dead'):
            with self.subTest(failure=failure):
                def snapshot():
                    now = self.clock[0] - 1_000_000
                    if failure == 'read' and now >= .2:
                        raise RuntimeError('读取失败')
                    value = state(500 if failure == 'deviation' and now >= .2 else now * 100,
                                  tick=0 if failure == 'frozen' else int(now * 1000))
                    if failure in ('map', 'dead') and now >= .2:
                        from dataclasses import replace
                        value = replace(value, map_id=2) if failure == 'map' else replace(value, dead=True)
                    return value
                action = recording()
                if failure == 'frozen':
                    action['duration_ms'] = 1500
                    action['samples'][-1]['at_ms'] = 1500
                with self.assertRaises(RuntimeError):
                    self.run_recording(action, snapshot=snapshot, clock_offset=1_000_000)
                if failure == 'frozen':
                    self.assertLessEqual(self.clock[0] - 1_000_000, 1.1)
                self.assertFalse(self.player._held_keys)
                self.assertFalse(self.player._held_buttons)
                self.assertIn(('send_key', (87, False)), [(n, a) for n, a, _ in self.events])

    def test_auto_return_and_alignment_finish_before_recording_clock_starts(self):
        prepared = False
        def prepare(player, reader, action, current):
            nonlocal prepared
            self.assertEqual(current.position[0], 400)
            self.assertEqual(current.yaw, 90)
            self.clock[0] += 3
            prepared = True
        def snapshot():
            return state((self.clock[0] - 3) * 100 if prepared else 400,
                         yaw=0 if prepared else 90, tick=int(self.clock[0] * 1000))
        events, elapsed = self.run_recording(snapshot=snapshot, prepare_hook=prepare)
        self.assertTrue(prepared)
        self.assertAlmostEqual(events[0][2], 3, delta=.002)
        self.assertAlmostEqual(events[-1][2], 3.4, delta=.002)
        self.assertAlmostEqual(elapsed, 3.5, delta=.002)

    def test_failed_return_does_not_begin_recorded_input(self):
        def failed(*args):
            raise RuntimeError('返回起点卡住')
        with self.assertRaisesRegex(RuntimeError, '卡住'):
            self.run_recording(snapshot=lambda: state(400), prepare_hook=failed)
        self.assertEqual(self.events, [])

    def test_real_coordinate_controller_returns_from_far_away_and_aligns(self):
        from macroflow.execution.ssjj_recording import _prepare_start
        from tests.test_ssjj_continuous import Simulation
        for blocked in (False, True):
            with self.subTest(blocked=blocked):
                sim = Simulation(yaw=90, speed=0 if blocked else 600)
                sim.position = [1600., 0., 519.]
                sim.close = lambda: sim.move()
                player = SimpleNamespace(_log_event=Mock())
                with patch('macroflow.execution.ssjj_recording.RouteContext', return_value=sim), patch(
                    'macroflow.execution.ssjj_navigation.time.monotonic', side_effect=lambda: sim.now):
                    if blocked:
                        with self.assertRaisesRegex(RuntimeError, '卡住|无进展'):
                            _prepare_start(player, sim, recording(), sim.snapshot())
                    else:
                        _prepare_start(player, sim, recording(), sim.snapshot())
                        self.assertLessEqual(abs(sim.position[0]), 20)
                        self.assertLessEqual(abs(sim.yaw), 1)
                self.assertFalse(sim.forward)

    def test_focus_loss_between_events_releases_inputs(self):
        def foreground():
            if self.clock[0] >= .05:
                raise RuntimeError('游戏必须在前台')
        with self.assertRaisesRegex(RuntimeError, '前台'):
            self.run_recording(focus_hook=foreground)
        self.assertFalse(self.player._held_keys)

    def test_pause_excludes_suspended_time_and_restores_held_input(self):
        from macroflow.execution.player import MacroPlayer
        original = MacroPlayer.wait_while_paused
        resumed = False
        def wait_for_resume(player):
            nonlocal resumed
            if player.paused:
                self.assertFalse(player._held_keys)
                self.clock[0] += 5
                player.resume()
                resumed = True
            return original(player)
        def pause(player, now):
            if now >= .05 and not resumed:
                player.pause()
        def snapshot():
            now = self.clock[0] - (5 if resumed else 0)
            return state(now * 100, tick=int(now * 1000))
        with patch.object(MacroPlayer, 'wait_while_paused', wait_for_resume):
            events, elapsed = self.run_recording(wait_hook=pause, snapshot=snapshot)
        self.assertTrue(resumed)
        self.assertAlmostEqual(elapsed, 5.5, delta=.002)
        self.assertEqual([(n, a) for n, a, _ in events[:3]], [
            ('send_key', (87, True)), ('send_key', (87, False)), ('send_key', (87, True))])

    def test_guard_polling_overhead_does_not_stretch_original_input_timing(self):
        from macroflow.execution.player import MacroPlayer
        def poll(player, **kwargs):
            self.clock[0] += .02
            return False
        with patch.object(MacroPlayer, '_poll_guards', poll):
            events, elapsed = self.run_recording()
        key_events = [(args, at) for name, args, at in events if name == 'send_key' and args[0] == 87]
        self.assertLessEqual(key_events[-1][1] - key_events[0][1], .43)
        self.assertLessEqual(elapsed, .53)

    def test_guard_handler_releases_inputs_and_suspends_only_handler_time(self):
        from macroflow.execution.player import MacroPlayer
        handled = False
        def poll(player, *, before_handle):
            nonlocal handled
            if self.clock[0] >= .1 and not handled:
                before_handle()
                self.assertFalse(player._held_keys)
                self.clock[0] += 2
                handled = True
                return True
            return False
        def snapshot():
            now = self.clock[0] - (2 if handled else 0)
            return state(now * 100, tick=int(now * 1000))
        with patch.object(MacroPlayer, '_poll_guards', poll):
            events, elapsed = self.run_recording(snapshot=snapshot)
        self.assertTrue(handled)
        self.assertAlmostEqual(elapsed, 2.5, delta=.002)
        w_events = [(n, a) for n, a, _ in events if n == 'send_key' and a[0] == 87]
        self.assertEqual(w_events, [('send_key', (87, True)), ('send_key', (87, False)),
                                  ('send_key', (87, True)), ('send_key', (87, False))])

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
                        session.send({'type': 'key', 'vk': 87, 'down': True})
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

    def test_stop_between_events_releases_keys(self):
        from macroflow.execution.player.control import PlaybackStopped
        def stop(player, now):
            if now >= .05:
                player.stop()
        with self.assertRaises(PlaybackStopped):
            self.run_recording(wait_hook=stop)
        self.assertFalse(self.player._held_keys)

    def test_invalid_recording_is_rejected_before_any_input(self):
        from macroflow.execution.ssjj_recording import validate_recording
        mutations = [
            lambda a: a['inputs'][0].update(type='python_script', path='anything.py'),
            lambda a: a['inputs'][0].update(recorded_at_ms=-1),
            lambda a: a['inputs'][0].update(vk=0),
            lambda a: a['inputs'][0].update(down='false'),
            lambda a: a['inputs'][3].update(mode='absolute'),
            lambda a: a['samples'][0].update(position=[float('nan'), 0, 0]),
            lambda a: a.update(duration_ms=float('inf')),
            lambda a: a['samples'].reverse(),
        ]
        for mutate in mutations:
            value = recording()
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_recording(value)

    def test_player_dispatch_and_summary(self):
        from macroflow.execution.player import MacroPlayer
        from macroflow.ui.app.summaries import action_summary
        with patch('macroflow.execution.ssjj_recording.run_recording_action') as run:
            MacroPlayer()._execute_action(recording(), None)
        run.assert_called_once()
        label, detail, _ = action_summary(recording())
        self.assertIn('键鼠', label)
        self.assertIn('8', detail)


if __name__ == '__main__':
    unittest.main()
