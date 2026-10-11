import math
import tempfile
import unittest
import struct
import threading
from pathlib import Path
from unittest.mock import patch

from tests.helpers.core import add_src_to_path

from macroflow.execution.ssjj_state import PlayerState, SSJJReader
from macroflow.execution.ssjj_navigation import (
    Route, steering, record_route, RouteContext,
)


def state(x=0, y=0, z=0, yaw=0, map_id=100001, dead=False):
    return PlayerState(map_id, (x, y, z), yaw, dead, True, 1)


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
        self.assertEqual(context.player._held_keys, set())

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

    def require_foreground(self):
        if not self.foreground:
            raise RuntimeError('游戏必须在前台')
