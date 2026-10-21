import math
import tempfile
import unittest
import struct
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path

from macroflow.execution import ssjj_navigation
from macroflow.execution.ssjj_state import (
    GameNotForeground, GameUnavailable, PlayerState, SSJJReader,
)
from macroflow.execution.player import MacroPlayer
from macroflow.execution.ssjj_navigation import (
    Route, steering, record_route, RouteContext,
)


def state(x=0, y=0, z=0, yaw=0, map_id=100001, dead=False):
    return PlayerState(map_id, (x, y, z), yaw, dead, True, 1, 0)


class RouteTests(unittest.TestCase):
    def test_route_round_trip_and_validation(self):
        route = Route(100001, [(0, 0, 0), (300, 0, 0)])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'route.json'
            route.save(path)
            self.assertEqual(Route.load(path), route)
        for points in [[], [(0, 0, 0)], [(0, 0, 0), (math.nan, 0, 0)],
                       [(0, 0, 0), (0, 0)]]:
            with self.assertRaises(ValueError):
                Route(100001, points)

    def test_shortest_turn_wraps_and_height_matters(self):
        distance, error = steering(state(yaw=179), (-100, -2, 0))
        self.assertAlmostEqual(distance, math.hypot(100, 2))
        self.assertLess(abs(error), 3)
        self.assertEqual(steering(state(), (0, 0, 300))[0], 300)

    def test_recording_collects_coordinates_and_rejects_teleport(self):
        for end_position, error in [(state(x=300), None), (state(x=5000), '跳变')]:
            reader = FakeReader()
            samples = iter([state(), state(x=150), end_position])
            reader.snapshot = lambda: next(samples)
            with patch('macroflow.execution.ssjj_navigation.time.sleep'), patch(
                'macroflow.execution.ssjj_navigation.time.monotonic', side_effect=[0, 0.1, 0.2, 2]
            ):
                if error:
                    with self.assertRaisesRegex(RuntimeError, error):
                        record_route(reader, 1)
                else:
                    self.assertEqual(record_route(reader, 1).points,
                                     [(0, 0, 0), (150, 0, 0), (300, 0, 0)])



class StateLayoutTests(unittest.TestCase):
    def test_nullable_value_before_has_value_and_absent_value_rejected(self):
        reader = SSJJReader.__new__(SSJJReader)
        reader.field = lambda obj, name: 0x10000
        reader.read = lambda address, size: struct.pack('<fB', 519.75, 1)
        self.assertEqual(reader.nullable(0, '_z', '<f'), 519.75)
        reader.read = lambda address, size: struct.pack('<fB', 519.75, 0)
        with self.assertRaisesRegex(RuntimeError, '有效值'):
            reader.nullable(0, '_z', '<f')
        reader.read = lambda address, size: b'\x00\x01'
        self.assertFalse(reader.nullable(0, '_isDead', '<?'))

    def test_invalid_memory_ranges_rejected_before_os_call(self):
        reader = SSJJReader.__new__(SSJJReader)
        for address, size in [(0, 8), (0x10000, 0), (0x10000, 8_000_001)]:
            with self.assertRaisesRegex(RuntimeError, '范围'):
                reader.read(address, size)

    def test_absent_player_or_map_reports_not_in_match(self):
        # 大厅/加载中没有本人角色或房间数据：报“没进对局”，而不是底层的“无效内存范围”。
        reader = SSJJReader.__new__(SSJJReader)
        reader.ui = 1
        reader.contexts = 1
        reader.static = lambda klass: 0
        reader.fields = lambda klass: {'MyPlayerEntity': 0, '_sharedInstance': 0}
        reader.ptr = lambda address: 0
        with self.assertRaisesRegex(RuntimeError, '尚未进入对局'):
            reader.snapshot()
        with self.assertRaisesRegex(RuntimeError, '尚未进入对局'):
            reader.map_id()
        with self.assertRaisesRegex(RuntimeError, '尚未进入对局'):
            reader.components(0)

    def test_destroying_entities_are_skipped_instead_of_raising(self):
        # 生成/销毁中的实体组件数组或对象头为空：跳过，不能读地址 0 报错。
        reader = SSJJReader.__new__(SSJJReader)
        reader.field = lambda obj, name: 0x20000
        reader.ptr = lambda address: 0
        self.assertEqual(reader.components(0x10000), [])
        self.assertEqual(reader.classname(0), '')
        reader.ptr = lambda address: 0x30000 if address == 0x20000 else (
            2 if address == 0x30018 else 0)
        reader.read = lambda address, size: struct.pack('<QQ', 0x40000, 0)
        self.assertEqual(reader.components(0x10000), [])

    def test_snapshot_retries_transient_read_failures(self):
        # 进图/重生瞬间实体指针会短暂失效：重试几次，不要一帧失败就停整段跑图。
        reader = SSJJReader.__new__(SSJJReader)
        calls = []

        def snapshot(**kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError('无效的游戏内存范围')
            return state(1, 2, 3)

        reader.snapshot = snapshot
        with patch('macroflow.execution.ssjj_state.time.sleep'):
            value = reader.snapshot_stable()
        self.assertEqual(len(calls), 3)
        self.assertEqual(value.position, (1, 2, 3))

    def test_snapshot_stable_reports_attempts_when_reads_keep_failing(self):
        reader = SSJJReader.__new__(SSJJReader)
        reader.snapshot = Mock(side_effect=RuntimeError('无效的游戏内存范围'))
        with patch('macroflow.execution.ssjj_state.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, '已重试 2 次'):
                reader.snapshot_stable(attempts=2)
        self.assertEqual(reader.snapshot.call_count, 2)


class ForegroundTests(unittest.TestCase):
    """前置窗口常绑定启动器；跑图前应自己把战斗端窗口切到前台。"""

    def reader(self, foreground):
        reader = SSJJReader.__new__(SSJJReader)
        reader.pid = 42
        reader.user = SimpleNamespace(
            GetForegroundWindow=lambda: 1,
            IsWindowVisible=lambda hwnd: True,
            GetTopWindow=lambda hwnd: 0,
            EnumChildWindows=lambda hwnd, callback, data: True,
            GetWindowRect=lambda hwnd, rect: False,
            GetWindowThreadProcessId=lambda hwnd, ref: setattr(
                ref._obj, 'value', foreground['pid']))
        return reader

    def window(self, name='SSJJ_BattleClient_Unity.exe', size=(800, 600)):
        from macroflow.input.wininput import WindowInfo
        return WindowInfo(99, '生死狙击微端战斗', 'UnityWndClass',
                          f'E:\\SSJJ-4399\\MicroClient64\\{name}', (0, 0, *size), size)

    def test_foreground_check_activates_the_battle_window(self):
        foreground = {'pid': 7}
        reader = self.reader(foreground)
        seen = []

        def activate(hwnd):
            seen.append(hwnd)
            foreground['pid'] = 42
            return True

        with patch('macroflow.input.wininput.enum_windows', return_value=[self.window()]), \
                patch('macroflow.input.wininput.activate_window', side_effect=activate):
            reader.require_foreground()
        self.assertEqual(seen, [99])

    def test_launcher_window_is_not_used_as_the_game_window(self):
        foreground = {'pid': 7}
        reader = self.reader(foreground)
        with patch('macroflow.input.wininput.enum_windows',
                   return_value=[self.window('MicroClient.exe')]), \
                patch('macroflow.input.wininput.activate_window') as activate:
            with self.assertRaisesRegex(RuntimeError, '游戏不在前台'):
                reader.require_foreground()
        activate.assert_not_called()

    def test_failed_activation_explains_and_recording_never_steals_focus(self):
        foreground = {'pid': 7}
        reader = self.reader(foreground)
        with patch('macroflow.input.wininput.enum_windows', return_value=[self.window()]), \
                patch('macroflow.input.wininput.activate_window', return_value=False) as activate:
            with self.assertRaisesRegex(RuntimeError, '点一下游戏画面'):
                reader.require_foreground(activate=False)
            activate.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, '已尝试切到游戏窗口'):
                reader.require_foreground()

    def test_game_in_foreground_skips_activation(self):
        reader = self.reader({'pid': 42})
        with patch('macroflow.input.wininput.activate_window') as activate:
            reader.require_foreground()
        activate.assert_not_called()


class InputBoundaryTests(unittest.TestCase):
    def player(self):
        from macroflow.execution.player import MacroPlayer
        return MacroPlayer()

    def context(self, player=None, reader=None):
        context = RouteContext(player or self.player(), reader or FakeReader(), 100001)
        self.addCleanup(lambda: getattr(context, 'close', context.release)())
        return context

    def test_slow_guard_poll_cannot_leave_forward_held_indefinitely(self):
        context = self.context()
        released = threading.Event()
        observed = []
        def key(vk, down):
            if not down:
                released.set()
        def poll():
            observed.append(released.wait(.4))
            return None
        context.player.on_guard_poll = poll
        with patch('macroflow.input.wininput.send_key', side_effect=key):
            context.move(forward=True)
            context.checkpoint()
            context.release()
        self.assertEqual(observed, [True])

    def test_watchdog_releases_on_pause_stop_or_focus_loss_without_controller_poll(self):
        for failure in ('pause', 'stop', 'focus'):
            with self.subTest(failure=failure):
                context = self.context()
                released = threading.Event()
                def key(vk, down):
                    if not down:
                        released.set()
                with patch('macroflow.input.wininput.send_key', side_effect=key):
                    context.move(forward=True)
                    if failure == 'pause':
                        context.player.pause()
                    elif failure == 'stop':
                        context.player.stop()
                    else:
                        context.reader.foreground = False
                    self.assertTrue(released.wait(.1))
                    context.release()

    def test_watchdog_focus_failure_cannot_be_rearmed_by_an_inflight_move(self):
        context = self.context()
        released = threading.Event()
        armed = False
        original = context.reader.require_foreground
        def focus_check():
            original()
            if armed and threading.current_thread() is threading.main_thread():
                context.reader.foreground = False
                self.assertTrue(released.wait(.4))
        context.reader.require_foreground = focus_check
        def key(vk, down):
            if not down:
                released.set()
        with patch('macroflow.input.wininput.send_key', side_effect=key) as sender:
            context.move(forward=True)
            armed = True
            with self.assertRaisesRegex(RuntimeError, '前台'):
                context.move(forward=True, dx=10)
            self.assertEqual([call.args for call in sender.call_args_list], [(87, True), (87, False)])
            self.assertFalse(context.forward)

    def test_continuous_forward_sends_one_down_and_turns_while_held(self):
        context = self.context()
        trace = []
        context.player.on_log = trace.append
        with patch('macroflow.input.wininput.send_key') as key, patch(
            'macroflow.input.wininput.send_move_relative'
        ) as move:
            context.move(forward=True)
            context.move(forward=True, dx=10)
            self.assertEqual(key.call_args_list[0].args, (87, True))
            self.assertEqual(key.call_count, 1)
            move.assert_called_once_with(10, 0)
            self.assertIn(87, context.player._held_keys)
            context.release()
            self.assertEqual(key.call_args.args, (87, False))
        context.close()
        self.assertEqual(context.player._held_keys, set())
        self.assertEqual(sum('W 按下已提交' in line for line in trace), 1)
        self.assertEqual(sum('W 松开' in line for line in trace), 1)

    def test_blocked_input_trace_cannot_block_watchdog_release_or_run_in_watchdog(self):
        context = self.context()
        released = threading.Event()
        messages, threads, observed = [], [], []

        def trace(message):
            messages.append(message)
            threads.append(threading.current_thread())
            if 'W 按下' in message:
                observed.append(released.wait(.4))

        def send(vk, down):
            if not down:
                released.set()

        context.player.on_log = trace
        with patch('macroflow.input.wininput.send_key', side_effect=send):
            context.move(forward=True)
            self.assertFalse(context.forward)
            context.checkpoint()
        self.assertEqual(observed, [True])
        self.assertEqual(len(messages), 2)
        self.assertTrue(all(thread is threading.main_thread() for thread in threads))
        self.assertIn('W 松开', messages[-1])

    def test_pause_releases_before_waiting_for_resume(self):
        player = self.player()
        context = self.context(player)
        def resume():
            self.assertFalse(context.forward)
            player.resume()
        player.wait_while_paused = resume
        with patch('macroflow.input.wininput.send_key') as key:
            context.move(forward=True)
            player.pause()
            context.checkpoint()
            self.assertEqual(key.call_args.args, (87, False))
        self.assertEqual(player._held_keys, set())

    def test_vertical_input_uses_the_same_validated_boundary(self):
        context = self.context()
        with patch('macroflow.input.wininput.send_move_relative') as move:
            context.move(dy=-16)
            move.assert_called_once_with(0, -16)
            for value in (True, .5, 32768):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    context.move(dy=value)
            context.reader.foreground = False
            with self.assertRaisesRegex(RuntimeError, '前台'):
                context.move(dy=16)
            self.assertEqual(move.call_count, 1)

    def test_guard_releases_before_handler_and_settle_wait(self):
        player = self.player()
        context = self.context(player)
        hits = iter([{'kind': 'success'}, None])
        player.on_guard_poll = lambda: next(hits)
        player.guard_settle_ms = 0
        def handle(hit):
            self.assertFalse(context.forward)
            self.assertNotIn(87, player._held_keys)
        player.handle_guard_hit = handle
        with patch('macroflow.input.wininput.send_key') as key:
            context.move(forward=True)
            context.checkpoint()
            self.assertEqual(key.call_args.args, (87, False))

    def test_stop_during_wait_releases_key(self):
        from macroflow.execution.player.control import PlaybackStopped
        context = self.context()
        with patch('macroflow.input.wininput.send_key') as key:
            context.move(forward=True)
            context.player.stop()
            with self.assertRaises(PlaybackStopped):
                context.wait(ms=10)
            self.assertEqual(key.call_args.args, (87, False))
        self.assertEqual(context.player._held_keys, set())

    def test_state_or_focus_failure_at_input_boundary_releases_key(self):
        for failure in ('map', 'focus', 'read'):
            with self.subTest(failure=failure):
                reader = FakeReader()
                context = self.context(reader=reader)
                with patch('macroflow.input.wininput.send_key') as key:
                    context.move(forward=True)
                    if failure == 'map':
                        reader.sample = state(map_id=2)
                    elif failure == 'focus':
                        reader.foreground = False
                    else:
                        reader.snapshot = lambda: (_ for _ in ()).throw(RuntimeError('读取失败'))
                    with self.assertRaises(RuntimeError):
                        context.move(forward=True, dx=16)
                    self.assertEqual(key.call_args.args, (87, False))
                    context.release()
                self.assertEqual(context.player._held_keys, set())

    def test_input_failure_still_attempts_key_up(self):
        context = self.context()
        def send(vk, down):
            if down:
                raise OSError('input unavailable')
        with patch('macroflow.input.wininput.send_key', side_effect=send) as key:
            with self.assertRaises(OSError):
                context.move(forward=True)
            self.assertEqual(key.call_args.args, (87, False))
        self.assertFalse(context.forward)


class FakeReader:
    def __init__(self, sample=None):
        self.sample = sample or state()
        self.foreground = True

    def snapshot(self):
        return self.sample

    def snapshot_stable(self):
        return self.snapshot()

    def require_foreground(self):
        if not self.foreground:
            raise GameNotForeground('游戏必须在前台')


class CoordinateRouteUnavailableTests(unittest.TestCase):
    """坐标跑图碰上「不在这一局游戏里」时跳过，而不是让整条脚本异常收尾。"""

    def player(self):
        player = MacroPlayer()
        self.logs = []
        player.on_log = self.logs.append
        return player

    def test_the_route_is_skipped_when_the_game_is_not_in_the_foreground(self):
        class Reader:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def require_foreground(self, *, activate=True):
                raise GameNotForeground('游戏不在前台：请点一下游戏画面后重试')

        player = self.player()
        with patch.object(ssjj_navigation, 'SSJJReader', Reader):
            result = ssjj_navigation.run_route_action(
                player, {'map_id': 100001, 'points': [(0, 0, 0), (300, 0, 0)]})
        self.assertIsNone(result)
        self.assertTrue(any('跳过坐标跑图' in message for message in self.logs))

    def test_a_map_change_mid_route_skips_the_remaining_path(self):
        class Reader:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def require_foreground(self, *, activate=True):
                return None

        def follow(context, route, **kwargs):
            raise GameUnavailable('地图已变化，停止跑图')

        player = self.player()
        with patch.object(ssjj_navigation, 'SSJJReader', Reader), \
                patch.object(ssjj_navigation, 'follow_route', side_effect=follow):
            result = ssjj_navigation.run_route_action(
                player, {'map_id': 100001, 'points': [(0, 0, 0), (300, 0, 0)]})
        self.assertIsNone(result)
        self.assertTrue(any('坐标跑图中断' in message for message in self.logs))
