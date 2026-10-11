import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from tests.helpers.core import add_src_to_path
from macroflow.execution.ssjj_navigation import Route, follow_route
from macroflow.execution.ssjj_state import PlayerState


class Simulation:
    """Time-based movement and discrete telemetry, with no OS input or game."""
    def __init__(self, *, speed=600, period=.05, yaw=0, sensitivity=-.2):
        self.now = 0.0
        self.position = [0., 0., 0.]
        self.yaw = yaw
        self.speed, self.period, self.sensitivity = speed, period, sensitivity
        self.forward = False
        self.downs = 0
        self.samples_moving = 0
        self.turns_moving = 0
        self.maximum_y = 0
        self.logs = []
        self.frozen = False
        self.tick = -1
        self.value = None
        self.foreground = True
        self.dead = False
        self.map_id = 100001
        self.trace = []

    def wait(self, ms=0):
        seconds = ms / 1000
        if self.forward:
            angle = math.radians(self.yaw)
            self.position[0] += math.cos(angle) * self.speed * seconds
            self.position[1] += math.sin(angle) * self.speed * seconds
            self.maximum_y = max(self.maximum_y, self.position[1])
        self.now += seconds
        self.trace.append(tuple(self.position))
        if self.now > 45:
            raise AssertionError('controller failed to finish within simulation limit')

    def move(self, *, forward=False, dx=0):
        if forward and not self.forward:
            self.downs += 1
        if dx and self.forward and forward:
            self.turns_moving += 1
        self.forward = forward
        self.yaw = (self.yaw + dx * self.sensitivity + 180) % 360 - 180

    def snapshot(self):
        tick = int(self.now / self.period)
        if self.value is None or (tick != self.tick and not self.frozen):
            self.tick = tick
            self.value = PlayerState(self.map_id, tuple(self.position), self.yaw,
                                     self.dead, True, tick)
        if self.forward:
            self.samples_moving += 1
        return self.value

    def require_foreground(self):
        if not self.foreground:
            raise RuntimeError('游戏必须在前台')

    def checkpoint(self):
        pass

    def log(self, message):
        self.logs.append(message)

    def run(self, points, **kwargs):
        with patch('macroflow.execution.ssjj_navigation.time.monotonic',
                   side_effect=lambda: self.now):
            follow_route(self, Route(100001, points), reader=self, **kwargs)


class ContinuousRouteTests(unittest.TestCase):
    def test_speed_and_telemetry_period_matrix_preserves_route_and_endpoint(self):
        routes = (
            [(0, 0, 0), (600, 0, 0), (1200, 0, 0)],
            [(0, 0, 0), (500, 0, 0), (500, 500, 0)],
            [(0, 0, 0), (300, 0, 0), (300, 300, 0), (600, 300, 0), (600, 0, 0)],
            [(0, 0, 0), (400, 0, 0), (400, 400, 0), (0, 400, 0), (0, 0, 0)],
            [(0, 0, 0), (300, 200, 0), (600, 400, 0), (900, 600, 0)],
        )
        for points in routes:
            for speed in (300, 600, 1200, 1800):
                for period in (.02, .05, .1, .15, .2, .3):
                    with self.subTest(points=points, speed=speed, period=period):
                        sim = Simulation(speed=speed, period=period)
                        sim.run(points)
                        self.assertLessEqual(math.dist(sim.position, points[-1]), 20)
                        self.assertFalse(sim.forward)

    def test_invalid_start_state_is_rejected_before_moving_or_calibration(self):
        for failure in ('dead', 'map', 'focus', 'distance'):
            with self.subTest(failure=failure):
                sim = Simulation()
                if failure == 'dead':
                    sim.dead = True
                elif failure == 'map':
                    sim.map_id = 2
                elif failure == 'focus':
                    sim.foreground = False
                points = [(500, 0, 0), (800, 0, 0)] if failure == 'distance' else [
                    (0, 0, 0), (600, 0, 0)]
                with self.assertRaises(RuntimeError):
                    sim.run(points)
                self.assertEqual(sim.downs, 0)
                self.assertEqual(sim.yaw, 0)

    def test_frozen_or_unresponsive_calibration_never_moves_forward(self):
        for frozen in (True, False):
            with self.subTest(frozen=frozen):
                sim = Simulation(sensitivity=-.2 if frozen else 0)
                sim.frozen = frozen
                with self.assertRaisesRegex(RuntimeError, '快照未更新|校准失败'):
                    sim.run([(0, 0, 0), (600, 0, 0)])
                self.assertEqual(sim.downs, 0)

    def test_calibration_waits_for_delayed_yaw_after_fresh_ticks(self):
        for delay in (.15, .45, .9):
            for sensitivity in (-.2, .2):
                with self.subTest(delay=delay, sensitivity=sensitivity):
                    sim = Simulation(yaw=179, sensitivity=sensitivity)
                    snapshot, move = sim.snapshot, sim.move
                    calibration_moves = []

                    def delayed_snapshot():
                        value = snapshot()
                        if sim.now < delay:
                            return replace(value, yaw=179)
                        return value

                    def tracked_move(*, forward=False, dx=0):
                        if sim.now < delay:
                            self.assertFalse(forward)
                            if dx:
                                calibration_moves.append(dx)
                        move(forward=forward, dx=dx)

                    sim.snapshot, sim.move = delayed_snapshot, tracked_move
                    sim.run([(0, 0, 0), (600, 0, 0)])
                    self.assertEqual(calibration_moves, [16])
                    self.assertLessEqual(math.dist(sim.position, (600, 0, 0)), 20)
                    self.assertFalse(sim.forward)

    def test_calibration_without_yaw_response_waits_then_reports_reading_failure(self):
        sim = Simulation(sensitivity=0)
        with self.assertRaisesRegex(RuntimeError, '校准失败.*未读到朝向变化'):
            sim.run([(0, 0, 0), (600, 0, 0)])
        self.assertGreaterEqual(sim.now, 1)
        self.assertLessEqual(sim.now, 1.02)
        self.assertEqual(sim.downs, 0)
        self.assertFalse(sim.forward)

    def test_stuck_character_stops_and_releases_forward(self):
        sim = Simulation(speed=0)
        with self.assertRaisesRegex(RuntimeError, '进展|卡住'):
            sim.run([(0, 0, 0), (600, 0, 0)], progress_timeout=.3)
        self.assertFalse(sim.forward)

    def test_straight_route_samples_while_w_is_held_and_finishes_near_endpoint(self):
        sim = Simulation()
        sim.run([(0, 0, 0), (600, 0, 0), (1200, 0, 0)])
        self.assertLessEqual(math.dist(sim.position, (1200, 0, 0)), 20)
        self.assertLess(sim.now, 2.6)
        self.assertEqual(sim.downs, 1)
        self.assertGreater(sim.samples_moving, 10)
        self.assertFalse(sim.forward)

    def test_small_heading_errors_are_corrected_without_stopping(self):
        sim = Simulation(yaw=12)
        wait = sim.wait
        disturbed = False
        def drift(ms=0):
            nonlocal disturbed
            wait(ms)
            if sim.now > .3 and not disturbed:
                sim.yaw += 8
                disturbed = True
        sim.wait = drift
        sim.run([(0, 0, 0), (600, 0, 0), (1200, 0, 0)])
        self.assertLessEqual(math.dist(sim.position, (1200, 0, 0)), 20)
        self.assertGreater(sim.turns_moving, 0)
        self.assertEqual(sim.downs, 1)

    def test_fast_updates_can_pass_intermediate_points_without_turning_back(self):
        sim = Simulation(speed=1800, period=.1)
        sim.run([(x, 0, 0) for x in range(0, 1001, 100)])
        self.assertLessEqual(math.dist(sim.position, (1000, 0, 0)), 20)
        self.assertLess(sim.now, 1.2)
        self.assertLessEqual(sim.downs, 3)

    def test_slow_telemetry_still_measures_speed_after_lease_releases_forward(self):
        sim = Simulation(speed=1800, period=.2)
        sim.run([(0, 0, 0), (499, 0, 0), (499, 499, 0)])
        self.assertLessEqual(math.dist(sim.position, (499, 499, 0)), 20)
        self.assertFalse(sim.forward)

    def test_corner_is_preserved_instead_of_cutting_diagonally(self):
        sim = Simulation()
        sim.run([(0, 0, 0), (500, 0, 0), (500, 500, 0)])
        self.assertLessEqual(math.dist(sim.position, (500, 500, 0)), 20)
        self.assertTrue(all(abs(y) < 45 for x, y, z in sim.trace if x < 450))
        self.assertGreaterEqual(sim.downs, 2)
        self.assertFalse(sim.forward)

    def test_low_sensitivity_large_turn_does_not_count_as_stuck(self):
        sim = Simulation(yaw=180, sensitivity=-.02)
        sim.run([(0, 0, 0), (600, 0, 0)], progress_timeout=.5)
        self.assertLessEqual(math.dist(sim.position, (600, 0, 0)), 20)

    def test_map_change_death_focus_and_read_errors_release_forward(self):
        for failure in ('map', 'dead', 'focus', 'read'):
            with self.subTest(failure=failure):
                sim = Simulation()
                snapshot = sim.snapshot
                def fail():
                    if sim.now > .3:
                        if failure == 'map':
                            sim.map_id = 2
                        elif failure == 'dead':
                            sim.dead = True
                        elif failure == 'focus':
                            sim.foreground = False
                        else:
                            raise RuntimeError('读取失败')
                    return snapshot()
                sim.snapshot = fail
                with self.assertRaises(RuntimeError):
                    sim.run([(0, 0, 0), (600, 0, 0)])
                self.assertFalse(sim.forward)

    def test_frozen_telemetry_releases_held_key(self):
        sim = Simulation()
        original = sim.snapshot
        def snapshot():
            if sim.forward:
                sim.frozen = True
            return original()
        sim.snapshot = snapshot
        with self.assertRaisesRegex(RuntimeError, '快照未更新'):
            sim.run([(0, 0, 0), (600, 0, 0)])
        self.assertFalse(sim.forward)

    def test_height_cannot_be_skipped_by_passing_a_point_in_xy(self):
        sim = Simulation()
        with self.assertRaisesRegex(RuntimeError, '进展|卡住|超时'):
            sim.run([(0, 0, 0), (100, 0, 100), (500, 0, 0)], progress_timeout=.3)
        self.assertFalse(sim.forward)

    def test_wide_intermediate_radius_cannot_skip_a_different_floor(self):
        sim = Simulation()
        with self.assertRaisesRegex(RuntimeError, '进展|卡住|超时'):
            sim.run([(0, 0, 0), (100, 0, 70), (500, 0, 70), (900, 0, 0)],
                    progress_timeout=.3)
        self.assertFalse(sim.forward)

    def test_pause_time_is_excluded_and_forward_is_reacquired_after_resume(self):
        sim = Simulation()
        paused = False
        def checkpoint():
            nonlocal paused
            if sim.forward and not paused:
                sim.move()
                sim.now += 40
                paused = True
        sim.checkpoint = checkpoint
        sim.run([(0, 0, 0), (600, 0, 0)], progress_timeout=.5)
        self.assertTrue(paused)
        self.assertLessEqual(math.dist(sim.position, (600, 0, 0)), 20)
        self.assertFalse(sim.forward)

    def test_python_entry_delegates_to_native_route_action(self):
        from unittest.mock import Mock
        ctx = Mock()
        route = Route(100001, [(0, 0, 0), (300, 0, 0)])
        with patch('macroflow.execution.ssjj_navigation.SSJJReader') as reader:
            follow_route(ctx, route)
        reader.assert_not_called()
        ctx.action.assert_called_once_with({'type': 'ssjj_route', 'map_id': 100001,
                                           'points': route.points})
