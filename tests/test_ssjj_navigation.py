import math
import tempfile
import unittest
import struct
import threading
import time
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from tests.helpers.core import add_src_to_path

from macroflow.execution.ssjj_state import PlayerState, SSJJReader
from macroflow.execution.ssjj_navigation import (
    Route, follow_route, steering, record_route, send_route_input,
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

    def test_closed_loop_reaches_taught_route_and_releases_each_pulse(self):
        reader = FakeReader()
        context = FakeContext(reader)
        follow_route(context, Route(100001, [(0, 0, 0), (300, 0, 0), (300, 300, 0)]),
                     reader=reader)
        self.assertLess(math.dist(reader.position, (300, 300, 0)), 85)
        self.assertTrue(context.presses)
        self.assertTrue(all(key == 'W' and hold <= 150 for key, hold in context.presses))

    def test_refuses_dead_wrong_map_and_distant_start_before_input(self):
        route = Route(100001, [(0, 0, 0), (300, 0, 0)])
        for sample in [state(dead=True), state(map_id=2), state(x=5000)]:
            reader = FakeReader(sample)
            context = FakeContext(reader)
            with self.assertRaises(RuntimeError):
                follow_route(context, route, reader=reader)
            self.assertFalse(context.presses)
            self.assertFalse(context.turns)

    def test_lost_focus_stops_without_input(self):
        reader = FakeReader()
        reader.foreground = False
        context = FakeContext(reader)
        with self.assertRaisesRegex(RuntimeError, '前台'):
            follow_route(context, Route(100001, [(0, 0, 0), (300, 0, 0)]), reader=reader)
        self.assertFalse(context.presses)
        self.assertFalse(context.turns)

    def test_no_movement_stops_instead_of_walking_forever(self):
        reader = FakeReader()
        context = FakeContext(reader)
        context.moving = False
        with self.assertRaisesRegex(RuntimeError, '进展|卡住'):
            follow_route(context, Route(100001, [(0, 0, 0), (300, 0, 0)]),
                         reader=reader, progress_timeout=0.01)
        self.assertLess(len(context.presses), 100)

    def test_unresponsive_mouse_stops_before_forward_input(self):
        reader = FakeReader()
        context = FakeContext(reader)
        context.action = lambda action: None
        with self.assertRaisesRegex(RuntimeError, '校准失败'):
            follow_route(context, Route(100001, [(0, 0, 0), (300, 0, 0)]), reader=reader)
        self.assertFalse(context.presses)

    def test_map_change_during_following_stops(self):
        reader = FakeReader()
        context = FakeContext(reader)
        original_press = context.press
        def press(key, hold_ms):
            original_press(key, hold_ms)
            reader.sample = state(map_id=2)
        context.press = press
        with self.assertRaisesRegex(RuntimeError, '地图'):
            follow_route(context, Route(100001, [(0, 0, 0), (300, 0, 0)]), reader=reader)
        self.assertEqual(len(context.presses), 1)

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

    def test_low_sensitivity_turn_and_pauses_do_not_count_as_stuck(self):
        reader = FakeReader(state(yaw=180))
        context = FakeContext(reader)
        clock = [0.0]
        def wait(ms=0, seconds=0):
            clock[0] += ms / 1000 + seconds
        def turn(action):
            context.turns.append(action)
            reader.yaw += action['dx'] * -0.02
        def checkpoint():
            clock[0] += 40  # A parent-side pause on every RPC checkpoint.
        context.wait, context.action, context.checkpoint = wait, turn, checkpoint
        with patch('macroflow.execution.ssjj_navigation.time.monotonic', side_effect=lambda: clock[0]):
            follow_route(context, Route(100001, [(0, 0, 0), (300, 0, 0)]),
                         reader=reader, progress_timeout=0.5)
        self.assertLess(math.dist(reader.position, (300, 0, 0)), 85)

    def test_frozen_snapshot_stops_before_forward_input(self):
        reader = FakeReader()
        original = reader.snapshot
        def frozen():
            value = original()
            return PlayerState(value.map_id, value.position, value.yaw, False, True, 1)
        reader.snapshot = frozen
        context = FakeContext(reader)
        clock = [0.0]
        def wait(ms=0, seconds=0):
            clock[0] += ms / 1000 + seconds
        context.wait = wait
        with patch('macroflow.execution.ssjj_navigation.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(RuntimeError, '快照未更新'):
                follow_route(context, Route(100001, [(0, 0, 0), (300, 0, 0)]), reader=reader)
        self.assertFalse(context.presses)


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
        return SimpleNamespace(stop_event=threading.Event(), paused=False, _held_keys=set())

    def test_pause_releases_w_without_waiting_for_resume(self):
        player = self.player()
        def send_key(vk, down):
            if down:
                player.paused = True
        with patch('macroflow.input.wininput.send_key', side_effect=send_key) as key:
            started = time.monotonic()
            send_route_input(player, FakeReader(), 100001, hold_ms=150)
            self.assertLess(time.monotonic() - started, 0.1)
            self.assertEqual([call.args for call in key.call_args_list], [(87, True), (87, False)])
        self.assertEqual(player._held_keys, set())

    def test_rechecks_map_at_actual_input_boundary(self):
        reader = FakeReader(state(map_id=2))
        with patch('macroflow.input.wininput.send_move_relative') as move:
            with self.assertRaisesRegex(RuntimeError, '地图'):
                send_route_input(self.player(), reader, 100001, dx=16)
            move.assert_not_called()

    def test_focus_lost_during_pulse_releases_key(self):
        reader = FakeReader()
        def send_key(vk, down):
            if down:
                reader.foreground = False
        with patch('macroflow.input.wininput.send_key', side_effect=send_key) as key:
            with self.assertRaisesRegex(RuntimeError, '前台'):
                send_route_input(self.player(), reader, 100001, hold_ms=150)
            self.assertEqual(key.call_args.args, (87, False))

    def test_rpc_dispatch_owns_reader_and_closes_it(self):
        from macroflow.execution.python_script import run_python_script
        from macroflow.execution.player import MacroPlayer
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'rpc.py'
            path.write_text('def main(ctx):\n    ctx.ssjj_input(100001, dx=16)\n', encoding='utf-8')
            with patch('macroflow.execution.ssjj_state.SSJJReader') as reader, patch(
                'macroflow.execution.ssjj_navigation.send_route_input', return_value=None
            ) as sender:
                run_python_script(path, MacroPlayer(), None, set(), 0)
                reader.assert_called_once_with()
                sender.assert_called_once()
                self.assertEqual(sender.call_args.kwargs, {'map_id': 100001, 'dx': 16, 'hold_ms': None})
                reader.return_value.close.assert_called_once_with()


class FakeReader:
    def __init__(self, sample=None):
        self.sample = sample or state()
        self.position = list(self.sample.position)
        self.yaw = self.sample.yaw
        self.foreground = True
        self.tick = 0

    def snapshot(self):
        self.tick += 1
        return PlayerState(self.sample.map_id, tuple(self.position), self.yaw,
                           self.sample.dead, True, self.tick)

    def require_foreground(self):
        if not self.foreground:
            raise RuntimeError('游戏必须在前台')


class FakeContext:
    def __init__(self, reader):
        self.reader = reader
        self.presses = []
        self.turns = []
        self.moving = True

    def action(self, action):
        self.turns.append(action)
        self.reader.yaw += action['dx'] * -0.2

    def press(self, key, hold_ms):
        self.presses.append((key, hold_ms))
        if self.moving:
            angle = math.radians(self.reader.yaw)
            self.reader.position[0] += math.cos(angle) * hold_ms * 0.6
            self.reader.position[1] += math.sin(angle) * hold_ms * 0.6

    def wait(self, ms=0, seconds=0):
        import time
        time.sleep(0.001)

    def checkpoint(self):
        pass

    def ssjj_input(self, map_id, *, dx=None, hold_ms=None):
        if dx is not None:
            self.action({'dx': dx})
        else:
            self.press('W', hold_ms)

    def log(self, message):
        pass
