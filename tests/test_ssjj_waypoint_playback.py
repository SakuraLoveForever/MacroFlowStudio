"""F7 纠正点回放：两点之间直接重放录制的键鼠，只在纠正点按坐标纠偏。"""
import copy
import math
import time
import threading
import unittest
from contextlib import ExitStack
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path
from macroflow.execution.ssjj_monitor import RouteCapture, SSJJMonitor
from macroflow.execution.ssjj_recording import run_recording_action, validate_recording
from macroflow.execution.ssjj_state import PlayerState
from macroflow.execution.player import MacroPlayer, PlaybackStopped
from tests.test_ssjj_continuous import Simulation


def state(x=0, yaw=0, tick=1, pitch=0):
    return PlayerState(100001, (x, 0, 0), yaw, False, True, tick, pitch)


def sample(at, point, *, yaw=0, pitch=0):
    value = asdict(state(yaw=yaw, pitch=pitch, tick=int(at)))
    value.pop('map_id')
    value.update(at_ms=at, position=list(point))
    return value


def mark(gap, point, *, yaw=0, pitch=0):
    """纠正点只记「距上一点的间隔」；第一个点的间隔是 0（它就是起点）。"""
    return {'gap_ms': gap, 'position': list(point), 'yaw': yaw, 'pitch': pitch}


def recording():
    """两个纠正点：起点 (0,0) 和 (600,0)，中间按住 W 走 900 毫秒。"""
    return {'type': 'ssjj_recording', 'map_id': 100001, 'duration_ms': 1300,
            'marks': [mark(0, (0, 0, 0)), mark(1000, (600, 0, 0), yaw=90)],
            'trajectory': [sample(0, (0, 0, 0)), sample(500, (300, 0, 0)),
                           sample(1000, (600, 0, 0), yaw=90),
                           sample(1300, (600, 0, 0), yaw=90)],
            'inputs': [
                {'type': 'key', 'vk': 87, 'down': True, 'recorded_at_ms': 0},
                {'type': 'mouse_move', 'dx': 5, 'dy': 0, 'recorded_at_ms': 100},
                {'type': 'key', 'vk': 87, 'down': False, 'recorded_at_ms': 900},
                {'type': 'key', 'vk': 69, 'down': True, 'recorded_at_ms': 1100},
                {'type': 'mouse_button', 'button': 'left', 'down': True, 'recorded_at_ms': 1150},
                {'type': 'mouse_button', 'button': 'left', 'down': False, 'recorded_at_ms': 1250}]}


def straight_recording():
    """只有一个起点纠正点：整段都是按键回放，没有任何逐点坐标跟踪。"""
    return {'type': 'ssjj_recording', 'map_id': 100001, 'duration_ms': 900,
            'marks': [mark(0, (0, 0, 0))],
            'trajectory': [sample(0, (0, 0, 0)), sample(450, (270, 0, 0)),
                           sample(900, (540, 0, 0))],
            'inputs': [{'type': 'key', 'vk': 87, 'down': True, 'recorded_at_ms': 0},
                       {'type': 'key', 'vk': 87, 'down': False, 'recorded_at_ms': 880}]}


class CaptureTests(unittest.TestCase):
    def monitor(self):
        monitor = SSJJMonitor.__new__(SSJJMonitor)
        monitor.lock = threading.Lock()
        monitor.capture = monitor.input_recorder = None
        monitor.latest, monitor.updated, monitor.error = state(), time.monotonic(), ''
        monitor.reader = Mock()
        monitor.reader.snapshot.return_value = state(60, tick=3)
        monitor._start_key_sampling = Mock()
        monitor.exclude_rect = (100, 100, 200, 200)
        self.addCleanup(monitor.cancel_recording)
        return monitor

    def test_start_mark_is_recorded_and_f7_adds_correction_points(self):
        monitor = self.monitor()
        with patch('macroflow.input.recorder.keyboard.Listener'), patch(
                'macroflow.input.recorder.mouse.Listener'):
            monitor.start_recording(filter_vks={118})
            monitor.capture.feed(state(), when=time.perf_counter() - .5)
            monitor.capture.latest = state(120, yaw=45, tick=2)
            monitor.mark_point()
            action = monitor.stop_recording()
        self.assertEqual([m['gap_ms'] for m in action['marks']][0], 0)
        self.assertEqual(action['marks'][0]['position'], [0, 0, 0])
        self.assertEqual(len(action['marks']), 2)
        self.assertEqual(action['marks'][1]['position'], [120, 0, 0])
        self.assertEqual(action['marks'][1]['yaw'], 45)
        # 第二个点记的是距起点（上一个纠正点）的间隔，不是绝对时间。
        self.assertAlmostEqual(action['marks'][1]['gap_ms'], 500, delta=50)
        self.assertIsNone(monitor.capture)
        self.assertNotIn('checkpoints', action)

    def test_mark_without_recording_is_rejected(self):
        monitor = self.monitor()
        with self.assertRaisesRegex(RuntimeError, '尚未开始'):
            monitor.mark_point()

    def test_recording_can_start_outside_a_match_and_begins_at_the_first_sample(self):
        # 游戏外/大厅按 F8：先建录制器，读到第一帧对局状态才开始计时并记起点。
        monitor = self.monitor()
        monitor.latest = None
        with patch('macroflow.input.recorder.keyboard.Listener'), patch(
                'macroflow.input.recorder.mouse.Listener'):
            monitor.start_recording()
            self.assertIsNotNone(monitor.capture)
            self.assertIsNone(monitor.capture.started_at)
            self.assertEqual(monitor.status()[3], 0)
            with self.assertRaisesRegex(RuntimeError, '还没读到对局状态'):
                monitor.mark_point()
            with self.assertRaisesRegex(RuntimeError, '还没读到对局状态'):
                monitor.stop_recording()
            monitor.capture.feed(state(60, tick=3, yaw=30), when=time.perf_counter() - .2)
            self.assertEqual(monitor.capture.trajectory[0]['at_ms'], 0)
            self.assertEqual(monitor.capture.marks[0]['position'], [60, 0, 0])
            self.assertEqual(monitor.capture.marks[0]['yaw'], 30)
            action = monitor.stop_recording()
        self.assertEqual(action['marks'][0]['position'], [60, 0, 0])
        self.assertEqual(action['trajectory'][0]['at_ms'], 0)

    def test_read_failure_while_waiting_does_not_invalidate_the_recording(self):
        from macroflow.execution.ssjj_monitor import SSJJMonitor, RouteCapture
        monitor = SSJJMonitor.__new__(SSJJMonitor)
        monitor.lock = threading.Lock()
        monitor.capture = RouteCapture(None)          # 游戏外按的 F8
        monitor.input_recorder = None
        monitor.latest, monitor.error, monitor.reader = None, '', None
        calls = {'n': 0}
        monitor.stop = SimpleNamespace(
            is_set=lambda: calls['n'] > 0,
            wait=lambda delay: calls.__setitem__('n', calls['n'] + 1) or True)
        reader = Mock()
        reader.snapshot.side_effect = RuntimeError('尚未进入对局：没有本人角色')
        with patch('macroflow.execution.ssjj_monitor.SSJJReader', return_value=reader):
            monitor._run()
        self.assertEqual(monitor.capture.error, '')
        self.assertIn('尚未进入对局', monitor.error)

    def test_losing_foreground_keeps_coordinates_and_sampling(self):
        # 点浮窗/切到软件上只是不录输入，不能把面板读取和录制采样一起打断。
        from macroflow.execution.ssjj_monitor import SSJJMonitor, RouteCapture
        monitor = SSJJMonitor.__new__(SSJJMonitor)
        monitor.lock = threading.Lock()
        monitor.capture = RouteCapture(state())
        monitor.input_recorder = None
        monitor.latest, monitor.updated, monitor.error, monitor.reader = None, 0.0, '', None
        reader = Mock()
        reader.snapshot.return_value = state(10, tick=2)
        reader.require_foreground.side_effect = RuntimeError('游戏必须在前台')
        calls = {'n': 0}
        monitor.stop = SimpleNamespace(
            is_set=lambda: calls['n'] > 0,
            wait=lambda delay: calls.__setitem__('n', calls['n'] + 1) or True)
        with patch('macroflow.execution.ssjj_monitor.SSJJReader', return_value=reader):
            monitor._run()
        self.assertIsNotNone(monitor.latest)
        self.assertEqual(monitor.error, '')
        self.assertEqual(monitor.capture.error, '')
        self.assertEqual(len(monitor.capture.trajectory), 1)
        reader.require_foreground.assert_not_called()

    def test_inputs_are_skipped_while_the_game_is_not_foreground(self):
        from macroflow.execution.ssjj_monitor import RouteCapture
        monitor = self.monitor()
        monitor.capture = RouteCapture(state())
        monitor.capture.started_at = time.perf_counter()
        monitor.input_recorder = Mock(_recording_started_at=time.perf_counter())
        monitor.reader.require_foreground.side_effect = RuntimeError('游戏必须在前台')
        monitor._record_input({'type': 'key', 'vk': 87, 'down': True, 'recorded_at_ms': 0})
        self.assertEqual(monitor.capture.inputs, [])
        self.assertEqual(monitor.capture.error, '')

    def test_recorded_route_inputs_keep_the_key_name(self):
        # 高级动作编辑与录制列表要显示 W/E 这类按键名，只留虚键码看不懂。
        from macroflow.execution.ssjj_monitor import RouteCapture
        capture = RouteCapture(state())
        capture.feed(state(), when=0)
        capture.record_input({'type': 'key', 'vk': 87, 'name': 'w', 'down': True},
                             when=capture.started_at + .1)
        capture.record_input({'type': 'mouse_button', 'button': 'left', 'down': True},
                             when=capture.started_at + .2)
        self.assertEqual(capture.inputs[0]['name'], 'w')
        self.assertEqual(capture.inputs[0]['vk'], 87)
        self.assertEqual(capture.inputs[1], {'type': 'mouse_button', 'button': 'left',
                                             'down': True, 'recorded_at_ms': 200.0})

    def monitor_loop(self, reader, *, capture=None, input_recorder=None, rounds=1):
        """跑 rounds 轮真实读取循环，返回监控对象。"""
        from macroflow.execution.ssjj_monitor import SSJJMonitor, RouteCapture
        monitor = SSJJMonitor.__new__(SSJJMonitor)
        monitor.lock = threading.Lock()
        monitor.capture = capture if capture is not None else RouteCapture(state())
        monitor.input_recorder = input_recorder
        monitor.latest, monitor.updated, monitor.error, monitor.reader = None, 0.0, '', None
        if input_recorder is None:
            monitor.input_recorder = None
        calls = {'n': 0}
        monitor.stop = SimpleNamespace(
            is_set=lambda: calls['n'] >= rounds,
            wait=lambda delay: calls.__setitem__('n', calls['n'] + 1) or calls['n'] > rounds)
        with patch('macroflow.execution.ssjj_monitor.SSJJReader', return_value=reader):
            monitor._run()
        return monitor

    def test_capture_failure_does_not_clear_the_coordinates(self):
        # 录制自己的错误（采样中断/坐标跳变）只记在录制上，面板坐标要照常刷新。
        from macroflow.execution.ssjj_monitor import RouteCapture
        capture = RouteCapture(state())
        capture.feed = Mock(side_effect=RuntimeError('连续内存采样中断超过 3 秒'))
        reader = Mock()
        reader.snapshot.return_value = state(10, tick=2)
        monitor = self.monitor_loop(reader, capture=capture)
        self.assertIsNotNone(monitor.latest)
        self.assertEqual(monitor.error, '')

    def test_read_failure_does_not_stop_input_recording(self):
        reader = Mock()
        reader.snapshot.side_effect = RuntimeError('读取失败')
        recorder = Mock()
        monitor = self.monitor_loop(reader, input_recorder=recorder)
        recorder.stop.assert_not_called()
        self.assertIn('读取失败', monitor.error)

    def test_missing_game_process_reports_a_waiting_message(self):
        from macroflow.execution.ssjj_monitor import SSJJMonitor
        monitor = SSJJMonitor.__new__(SSJJMonitor)
        monitor.lock = threading.Lock()
        monitor.capture = monitor.input_recorder = None
        monitor.latest, monitor.updated, monitor.error, monitor.reader = None, 0.0, '', None
        calls = {'n': 0}
        monitor.stop = SimpleNamespace(
            is_set=lambda: calls['n'] > 0,
            wait=lambda delay: calls.__setitem__('n', calls['n'] + 1) or True)
        with patch('macroflow.execution.ssjj_monitor.SSJJReader',
                   side_effect=RuntimeError('需要且只能运行一个 SSJJ_BattleClient_Unity.exe')):
            monitor._run()
        self.assertIn('游戏未运行', monitor.error)

    def test_wasd_look_buttons_and_scroll_kept_panel_clicks_excluded(self):
        from macroflow.input import recorder as module
        monitor = self.monitor()
        with patch.object(module.keyboard, 'Listener'), patch.object(module.mouse, 'Listener'):
            monitor.start_recording(filter_vks={120})
            recorder = monitor.input_recorder
            recorder._on_press(SimpleNamespace(vk=69, char='e'))
            monitor.capture.feed(state(), when=time.perf_counter() - .1)
            monitor.capture.feed_keys({87: True, 69: True, 120: True})
            recorder._on_raw_move(12, 3)
            recorder._on_click(10, 10, module.mouse.Button.left, True)
            recorder._on_scroll(10, 10, 0, -1)
            recorder._on_click(150, 150, module.mouse.Button.left, True)
            recorder._on_press(SimpleNamespace(vk=120, char=None))
            action = monitor.stop_recording()
        types = [e['type'] for e in action['inputs']]
        self.assertEqual(types, ['key', 'key', 'mouse_move', 'mouse_button', 'scroll'])
        # WASD 现在录进去（回放要靠它走路），面板上的点击仍然排除。
        self.assertEqual([e['vk'] for e in action['inputs'] if e['type'] == 'key'], [69, 87])
        self.assertEqual((action['inputs'][2]['dx'], action['inputs'][2]['dy']), (12, 3))

    def test_inputs_made_outside_the_game_are_dropped_but_stopping_still_works(self):
        # 人在游戏外（浮窗/软件在前台）时：实体输入不是游戏操作，丢掉不录；但停止不再要求
        # 游戏在前台，也不抢前台——读内存与焦点无关，否则「到游戏外再按 F8」会停不下来。
        from macroflow.input import recorder as module
        monitor = self.monitor()
        with patch.object(module.keyboard, 'Listener'), patch.object(module.mouse, 'Listener'):
            monitor.start_recording()
            monitor.capture.feed(state(), when=time.perf_counter() - .1)
            monitor.reader.require_foreground.side_effect = RuntimeError('游戏不在前台')
            monitor.input_recorder._on_press(SimpleNamespace(vk=69, char='e'))
            monitor.input_recorder._on_click(150, 150, module.mouse.Button.left, True)
            action = monitor.stop_recording()
        self.assertEqual(action['inputs'], [])
        self.assertIsNone(monitor.capture)
        # 前台只用来过滤实体输入，而且不主动把游戏切到前台。
        monitor.reader.require_foreground.assert_called_with(activate=False)

    def test_fresh_state_gap_is_not_silently_bridged(self):
        # 短暂停顿（切窗口、读进程重建）不算中断，超过 3 秒才判定录制断了。
        capture = RouteCapture(state())
        capture.feed(state(), when=0)
        capture.feed(state(30, tick=2), when=1)
        with self.assertRaisesRegex(RuntimeError, '中断'):
            capture.feed(state(60, tick=3), when=4.5)


class PlaybackTests(unittest.TestCase):
    def run_recording(self, action=None, *, speed=600, start=0, wait_hook=None, pause_hook=None,
                      guard_hook=None, snapshot_hook=None, input_hook=None, correction_hook=None):
        sim = Simulation(speed=speed)
        sim.position[:] = start if isinstance(start, tuple) else (start, 0, 0)
        player = MacroPlayer()
        player.on_log = Mock()
        self.sim, self.player, self.events = sim, player, []
        if snapshot_hook:
            snapshot = sim.snapshot
            sim.snapshot = lambda: snapshot_hook(sim, snapshot())

        def send(name, *args):
            if input_hook:
                input_hook(sim, name, args)
            self.events.append((name, args, sim.now, tuple(sim.position), sim.yaw))
            if name == 'send_key' and args[0] == 87:
                sim.forward = args[1]
            if name == 'send_move_relative':
                sim.yaw = (sim.yaw + args[0] * sim.sensitivity + 180) % 360 - 180
                sim.pitch = max(-89, min(89, sim.pitch + args[1] * sim.pitch_sensitivity))

        def wait(seconds):
            sim.wait(ms=seconds * 1000)
            if wait_hook:
                wait_hook(player, sim)
            return player.stop_event.is_set()

        with ExitStack() as stack:
            if correction_hook:
                from macroflow.execution.ssjj_recording import RecordedReplay
                correct = RecordedReplay.correct
                def tracked_correct(replay, current, mark, **kwargs):
                    correction_hook(sim, replay.marks.index(mark))
                    return correct(replay, current, mark, **kwargs)
                stack.enter_context(patch.object(RecordedReplay, 'correct', tracked_correct))
            stack.enter_context(patch('macroflow.execution.ssjj_recording.SSJJReader')).return_value.__enter__.return_value = sim
            stack.enter_context(patch('macroflow.execution.ssjj_recording.time.perf_counter', side_effect=lambda: sim.now))
            stack.enter_context(patch('macroflow.execution.ssjj_navigation.time.monotonic', side_effect=lambda: sim.now))
            stack.enter_context(patch.object(player.stop_event, 'wait', side_effect=wait))
            if pause_hook:
                stack.enter_context(patch.object(player, 'wait_while_paused', side_effect=lambda: pause_hook(player, sim)))
            if guard_hook:
                stack.enter_context(patch.object(player, '_poll_guards',
                    side_effect=lambda **kw: guard_hook(player, sim, **kw)))
            for name in ('send_key', 'send_move_relative', 'send_button', 'send_scroll'):
                stack.enter_context(patch('macroflow.input.wininput.' + name,
                    side_effect=lambda *args, name=name: send(name, *args)))
            run_recording_action(player, copy.deepcopy(action or recording()))
        return self.events

    @staticmethod
    def played(events, name, *args):
        return [event for event in events if event[0] == name and event[1] == args]

    def test_recorded_keys_are_replayed_verbatim_between_marks(self):
        events = self.run_recording()
        down = self.played(events, 'send_key', 87, True)[0]
        up = self.played(events, 'send_key', 87, False)[0]
        # 录制的按住时长按原时间轴保留：W 松开在按下后约 900 毫秒。
        self.assertAlmostEqual(up[2] - down[2], .9, delta=.1)
        # 两点之间只有录制的那次镜头位移，没有任何跟踪产生的按键或转向。
        between = [e[1] for e in events
                   if down[2] < e[2] < up[2] and e[0] in ('send_key', 'send_move_relative')]
        self.assertEqual(between, [(5, 0)])
        self.assertEqual(len(self.played(events, 'send_button', 'left', True)), 1)
        self.assertFalse(self.player._held_keys | self.player._held_buttons)

    def test_mark_waits_for_its_time_while_a_recorded_key_is_held(self):
        action = straight_recording()
        action['marks'].append(mark(500, (300, 0, 0)))
        corrections = []
        events = self.run_recording(action, correction_hook=lambda sim, index:
                                    corrections.append((index, sim.now, tuple(sim.position))))
        down = self.played(events, 'send_key', 87, True)[0]
        self.assertAlmostEqual(corrections[1][1] - down[2], .5, delta=.02)
        self.assertAlmostEqual(corrections[1][2][0], 300, delta=12)

    def test_mark_after_the_last_input_still_waits_for_its_recorded_time(self):
        action = straight_recording()
        action['marks'].append(mark(500, (0, 0, 0), yaw=90))
        action['inputs'] = [{'type': 'key', 'vk': 69, 'down': True, 'recorded_at_ms': 0},
                            {'type': 'key', 'vk': 69, 'down': False, 'recorded_at_ms': 100}]
        corrections = []
        events = self.run_recording(action, correction_hook=lambda sim, index:
                                    corrections.append((index, sim.now)))
        down = self.played(events, 'send_key', 69, True)[0]
        self.assertAlmostEqual(corrections[1][1] - down[2], .5, delta=.02)

    def test_replay_keeps_final_hold_until_duration_and_releases_before_verification(self):
        from macroflow.execution.ssjj_recording import RecordedReplay
        action = straight_recording()
        action['inputs'] = action['inputs'][:1]
        verify_end = RecordedReplay.verify_end

        def verify_released(replay):
            self.assertFalse(replay.player._held_keys | replay.player._held_buttons)
            return verify_end(replay)

        with patch.object(RecordedReplay, 'verify_end', verify_released):
            events = self.run_recording(action)
        down = self.played(events, 'send_key', 87, True)[0]
        up = self.played(events, 'send_key', 87, False)[0]
        self.assertAlmostEqual(up[2] - down[2], .9, delta=.02)
        self.assertAlmostEqual(up[3][0], 540, delta=12)

    def test_correction_point_walks_to_recorded_position_and_heading(self):
        events = self.run_recording()
        press = self.played(events, 'send_key', 69, True)
        self.assertEqual(len(press), 1)
        point, yaw = press[0][3], press[0][4]
        self.assertLessEqual(math.dist(point, (600, 0, 0)), 20)
        self.assertAlmostEqual(yaw, 90, delta=1)
        self.assertLessEqual(math.dist(self.sim.position, (600, 0, 0)), 20)

    def test_no_trajectory_tracking_when_only_the_start_is_marked(self):
        events = self.run_recording(straight_recording())
        # 只有回放的两个 W 事件：中间没有逐点跟踪产生的额外按键。
        self.assertEqual(len(self.played(events, 'send_key', 87, True)), 1)
        self.assertEqual(len(self.played(events, 'send_key', 87, False)), 1)
        down = self.played(events, 'send_key', 87, True)[0]
        up = self.played(events, 'send_key', 87, False)[0]
        # 回放窗口内没有任何跟踪输入（校准/对齐只在纠正点发生）。
        between = [e[1] for e in events
                   if down[2] < e[2] < up[2] and e[0] in ('send_key', 'send_move_relative')]
        self.assertEqual(between, [])
        self.assertAlmostEqual(self.sim.position[0], 528, delta=30)

    def test_spawn_offset_corrects_at_the_start_mark_before_replaying(self):
        action = recording()
        action['marks'][0]['yaw'] = 90
        action['marks'][0]['pitch'] = -20
        action['inputs'] = [
            {'type': 'key', 'vk': 69, 'down': True, 'recorded_at_ms': 0},
            {'type': 'key', 'vk': 87, 'down': True, 'recorded_at_ms': 50},
            {'type': 'key', 'vk': 87, 'down': False, 'recorded_at_ms': 900},
            {'type': 'key', 'vk': 69, 'down': False, 'recorded_at_ms': 1000}]
        for start in ((-300, 120, 0), (200, -150, 0)):
            with self.subTest(start=start):
                first = []
                def on_input(sim, name, args):
                    if not first and name == 'send_key' and args == (69, True):
                        first.append((tuple(sim.position), sim.yaw, sim.pitch))
                self.run_recording(action, start=start, input_hook=on_input)
                point, yaw, pitch = first[0]
                self.assertLessEqual(math.dist(point, (0, 0, 0)), 20)
                self.assertAlmostEqual(yaw, 90, delta=1)
                self.assertAlmostEqual(pitch, -20, delta=1)
                self.assertFalse(self.player._held_keys | self.player._held_buttons)

    def test_blocked_start_approach_skips_route_without_replaying_inputs(self):
        def wall(player, sim):
            if sim.position[0] > -200:
                sim.position[0] = -200
        self.run_recording(start=-300, wait_hook=wall)
        messages = [call.args[0] for call in self.player.on_log.call_args_list]
        self.assertTrue(any('跳过剩余路线' in message and '继续执行' in message for message in messages))
        self.assertFalse(any(e[0] in ('send_button', 'send_scroll') for e in self.events))
        self.assertFalse(self.played(self.events, 'send_key', 69, True))
        self.assertFalse(self.player._held_keys | self.player._held_buttons)

    def test_death_read_failure_and_leaving_game_skip_route_without_ending_script(self):
        """运行故障释放路线输入并续行，不能结束外层脚本或工作流。"""
        for failure in ('dead', 'frozen', 'read'):
            def fault(player, sim, failure=failure):
                if not sim.forward:
                    return
                if failure == 'dead':
                    sim.dead = True
                elif failure == 'frozen':
                    sim.frozen = True
                else:
                    raise OSError('读取失败')
            with self.subTest(failure=failure):
                self.run_recording(wait_hook=fault)
            self.assertFalse(self.player._held_keys | self.player._held_buttons)
            messages = [call.args[0] for call in self.player.on_log.call_args_list]
            self.assertTrue(any('跳过剩余路线' in message and '继续执行' in message for message in messages))

        for failure in ('map', 'focus'):
            def leave(player, sim, failure=failure):
                if not sim.forward:
                    return
                if failure == 'map':
                    sim.map_id = 2
                else:
                    sim.foreground = False
            with self.subTest(failure=failure):
                self.run_recording(wait_hook=leave)
            self.assertFalse(self.player._held_keys | self.player._held_buttons)
            messages = [call.args[0] for call in self.player.on_log.call_args_list]
            self.assertTrue(any('跳过' in message for message in messages), messages)

    def test_stop_while_replaying_releases_recorded_holds(self):
        def stop(player, sim):
            if sim.forward: player.stop_event.set()
        with self.assertRaises(PlaybackStopped):
            self.run_recording(wait_hook=stop)
        self.assertFalse(self.player._held_keys | self.player._held_buttons)

    def test_pause_releases_and_restores_holds_without_stretching_them(self):
        action = recording()
        action['marks'][1] = mark(1100, (540, 0, 0))
        action['inputs'] = [{'type': 'mouse_button', 'button': 'left', 'down': True, 'recorded_at_ms': 0},
                            {'type': 'key', 'vk': 87, 'down': True, 'recorded_at_ms': 0},
                            {'type': 'key', 'vk': 87, 'down': False, 'recorded_at_ms': 900},
                            {'type': 'mouse_button', 'button': 'left', 'down': False, 'recorded_at_ms': 1000}]
        suspended = []
        def trigger(player, sim):
            if sim.forward and sim.position[0] > 100 and not suspended:
                player.pause()
        def pause(player, sim):
            if player.paused:
                self.assertFalse(player._held_keys | player._held_buttons)
                sim.wait(ms=2000)
                player.resume()
                suspended.append(2)
                return 2
            return 0
        events = self.run_recording(action, wait_hook=trigger, pause_hook=pause)
        presses = self.played(events, 'send_button', 'left', True)
        releases = self.played(events, 'send_button', 'left', False)
        self.assertEqual(suspended, [2])
        self.assertGreaterEqual(len(presses), 2)      # 暂停后补按回去
        self.assertEqual(len(presses), len(releases))
        # 录制时钟排除了暂停的 2 秒：按住时长仍是录制的 1 秒。
        self.assertAlmostEqual(releases[-1][2] - presses[0][2] - 2.0, 1.0, delta=.15)
        self.assertFalse(self.player._held_keys | self.player._held_buttons)

    def test_guard_control_request_preserves_identity_and_releases(self):
        from macroflow.execution.player.control import GuardJumpRequest
        requested = GuardJumpRequest()
        def guard(player, sim, before_handle):
            if sim.forward:
                before_handle()
                raise requested
        with self.assertRaises(GuardJumpRequest) as result:
            self.run_recording(guard_hook=guard)
        self.assertIs(result.exception, requested)
        self.assertFalse(self.player._held_keys | self.player._held_buttons)

    def test_validation_rejects_old_or_malformed_recordings_before_input(self):
        cases = (
            lambda a: a.update(checkpoints=[]),
            lambda a: a.pop('marks'),
            lambda a: a.update(marks=[]),
            lambda a: a['marks'].__setitem__(0, mark(50, (0, 0, 0))),
            # 旧格式：纠正点记的是绝对时间，没有「距上一点的间隔」。
            lambda a: a['marks'].__setitem__(
                1, {'at_ms': 1000, 'position': [600, 0, 0], 'yaw': 90, 'pitch': 0}),
            lambda a: a['marks'][1].update(gap_ms=99999),
            lambda a: a['marks'][1].update(gap_ms=-1),
            lambda a: a['marks'][1].update(position=[math.nan, 0, 0]),
            lambda a: a['marks'][1].pop('yaw'),
            lambda a: a['inputs'][0].update(recorded_at_ms=99999),
            lambda a: a['trajectory'][1].update(position=[math.nan, 0, 0]),
            lambda a: a['trajectory'].reverse(),
        )
        for change in cases:
            action = recording()
            change(action)
            with patch('macroflow.execution.ssjj_recording.SSJJReader') as factory:
                with self.assertRaises(ValueError):
                    run_recording_action(MacroPlayer(), action)
                factory.assert_not_called()

    def test_valid_recording_passes_validation(self):
        validate_recording(recording())
        validate_recording(straight_recording())

    def test_the_old_absolute_mark_format_says_to_record_again(self):
        action = recording()
        action['marks'][1] = {'at_ms': 1000, 'position': [600, 0, 0], 'yaw': 90, 'pitch': 0}
        with self.assertRaisesRegex(ValueError, '旧的绝对时间格式'):
            validate_recording(action)

    def test_mark_times_are_accumulated_from_the_gaps(self):
        from macroflow.execution.ssjj_recording import mark_offsets
        marks = [mark(0, (0, 0, 0)), mark(700, (100, 0, 0)), mark(300, (400, 0, 0))]
        self.assertEqual(mark_offsets(marks), [0.0, 700.0, 1000.0])
        self.assertEqual(mark_offsets([]), [])

    def test_the_route_corrects_at_the_time_accumulated_from_the_gaps(self):
        action = straight_recording()
        action['duration_ms'] = 1500
        action['marks'].append(mark(900, (300, 0, 0)))
        action['trajectory'].append(sample(1500, (900, 0, 0)))
        validate_recording(action)
        corrections = []
        events = self.run_recording(action, correction_hook=lambda sim, index:
                                    corrections.append((index, sim.now, tuple(sim.position))))
        down = self.played(events, 'send_key', 87, True)[0]
        # 第二个纠正点在起点之后 900 ms（间隔累加成绝对时刻，不是把间隔当绝对时间）。
        self.assertAlmostEqual(corrections[1][1] - down[2], .9, delta=.05)
        self.assertLessEqual(math.dist(self.sim.position, (300, 0, 0)), 20)


class MarkHotkeyTests(unittest.TestCase):
    def test_f7_marks_the_open_panel(self):
        from macroflow.ui.app.main import MacroFlowApp
        app = MacroFlowApp.__new__(MacroFlowApp)
        panel = Mock()
        panel.closed = False
        app._ssjj_coordinates = panel
        app.mark_route_point()
        panel.mark.assert_called_once_with()

    def test_f7_without_panel_does_nothing(self):
        from macroflow.ui.app.main import MacroFlowApp
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._ssjj_coordinates = None
        app.mark_route_point()

    def test_panel_mark_reports_the_point_and_errors(self):
        from macroflow.ui.dialogs.ssjj_coordinates import SSJJCoordinatePanel
        panel = SSJJCoordinatePanel.__new__(SSJJCoordinatePanel)
        panel.monitor = Mock()
        panel.monitor.mark_point.return_value = {'position': [12.5, -3.0, 519.0]}
        panel.monitor.status.return_value = (None, '', True, 5, 2)
        panel.state = Mock()
        panel.mark()
        self.assertIn('第 2', panel.state.set.call_args.args[0])
        panel.monitor.mark_point.side_effect = RuntimeError('尚未开始路线录制')
        panel.mark()
        self.assertEqual(panel.state.set.call_args.args[0], '尚未开始路线录制')


class GameUnavailableRouteTests(unittest.TestCase):
    """人不在这一局游戏里（没开/没进对局/不在前台/换图）时跳过，而不是异常收尾。"""

    def player(self):
        player = MacroPlayer()
        self.logs = []
        player.on_log = self.logs.append
        return player

    def test_the_route_is_skipped_when_the_game_is_not_running(self):
        from macroflow.execution import ssjj_recording as module
        from macroflow.execution.ssjj_state import GameUnavailable
        with patch.object(module, 'SSJJReader',
                          side_effect=GameUnavailable('需要且只能运行一个 SSJJ_BattleClient_Unity.exe')):
            result = module.run_recording_action(self.player(), straight_recording())
        self.assertIsNone(result)
        self.assertTrue(any('跳过键鼠连续路线' in message for message in self.logs))

    def test_the_route_is_skipped_when_the_game_is_not_in_the_foreground(self):
        from macroflow.execution import ssjj_recording as module
        from macroflow.execution.ssjj_state import GameNotForeground

        class Reader:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def require_foreground(self, *, activate=True):
                raise GameNotForeground('游戏不在前台：请点一下游戏画面后重试')

        with patch.object(module, 'SSJJReader', Reader):
            result = module.run_recording_action(self.player(), straight_recording())
        self.assertIsNone(result)
        self.assertTrue(any('跳过键鼠连续路线：游戏不在前台' in message for message in self.logs))

    def test_losing_the_game_mid_route_skips_the_remaining_steps(self):
        from macroflow.execution import ssjj_recording as module
        from macroflow.execution.ssjj_state import GameNotForeground

        class Reader:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def require_foreground(self, *, activate=True):
                return None

        player = self.player()
        with patch.object(module, 'SSJJReader', Reader), \
                patch.object(module.RecordedReplay, 'run',
                             side_effect=GameNotForeground('游戏不在前台：请点一下游戏画面后重试')):
            result = module.run_recording_action(player, straight_recording())
        self.assertIsNone(result)
        self.assertTrue(any('路线中断' in message for message in self.logs))

    def test_endpoint_verification_is_skipped_when_the_game_is_gone(self):
        from macroflow.execution.ssjj_recording import RecordedReplay
        from macroflow.execution.ssjj_state import GameNotForeground
        session = Mock()
        session.check_state.side_effect = GameNotForeground('游戏不在前台：请点一下游戏画面后重试')
        replay = RecordedReplay(self.player(), Mock(), session, Mock(), straight_recording())
        replay.verify_end()
        self.assertTrue(any('终点验收跳过' in message for message in self.logs))
