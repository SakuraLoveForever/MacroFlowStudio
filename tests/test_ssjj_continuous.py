import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from tests.helpers.core import add_src_to_path
from macroflow.execution.ssjj_navigation import Route, follow_route
from macroflow.execution.ssjj_state import GameNotForeground, PlayerState


class Simulation:
    """Time-based movement and discrete telemetry, with no OS input or game."""
    def __init__(self, *, speed=600, period=.05, yaw=0, sensitivity=-.2,
                 pitch=0, pitch_sensitivity=.2):
        self.now = 0.0
        self.position = [0., 0., 0.]
        self.yaw = yaw
        self.pitch, self.pitch_sensitivity = pitch, pitch_sensitivity
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

    def move(self, *, forward=False, dx=0, dy=0):
        if forward and not self.forward:
            self.downs += 1
        if dx and self.forward and forward:
            self.turns_moving += 1
        self.forward = forward
        self.yaw = (self.yaw + dx * self.sensitivity + 180) % 360 - 180
        self.pitch = max(-89, min(89, self.pitch + dy * self.pitch_sensitivity))

    def brake(self):
        self.move()

    def snapshot(self):
        tick = int(self.now / self.period)
        if self.value is None or (tick != self.tick and not self.frozen):
            self.tick = tick
            self.value = PlayerState(self.map_id, tuple(self.position), self.yaw,
                                     self.dead, True, tick, self.pitch)
        if self.forward:
            self.samples_moving += 1
        return self.value

    def require_foreground(self, *, activate=True):
        if not self.foreground:
            raise GameNotForeground('游戏必须在前台')

    def snapshot_stable(self):
        return self.snapshot()

    def checkpoint(self):
        pass

    def log(self, message, *, detail=False):
        self.logs.append(message)

    def run(self, points, **kwargs):
        with patch('macroflow.execution.ssjj_navigation.time.monotonic',
                   side_effect=lambda: self.now):
            follow_route(self, Route(100001, points), reader=self, **kwargs)


class ContinuousRouteTests(unittest.TestCase):
    def test_pitch_alignment_handles_inversion_low_sensitivity_and_vertical_limits(self):
        for initial in (-89, -88.5, -88, 0, 88, 88.5, 89):
            for sensitivity in (-.2, .2, -.02):
                with self.subTest(initial=initial, sensitivity=sensitivity):
                    sim = Simulation(pitch=initial, pitch_sensitivity=sensitivity)
                    sim.run([(0, 0, 0), (0, 0, 0)], final_yaw=60, final_pitch=30)
                    self.assertLessEqual(abs(sim.pitch - 30), 1)
                    self.assertLessEqual(abs(sim.yaw - 60), 1)
                    self.assertEqual(sim.downs, 0)
                    self.assertFalse(sim.forward)

    def test_unresponsive_pitch_is_bounded_and_never_reports_completion(self):
        sim = Simulation(pitch_sensitivity=0)
        with self.assertRaisesRegex(RuntimeError, '俯仰.*校准'):
            sim.run([(0, 0, 0), (0, 0, 0)], final_yaw=0, final_pitch=30)
        # One bounded yaw probe and two bounded vertical probes, plus alignment.
        self.assertLess(sim.now, 3.5)
        self.assertFalse(any('路线完成' in log for log in sim.logs))
        self.assertFalse(sim.forward)

    def test_pitch_changed_during_horizontal_alignment_is_still_corrected(self):
        sim = Simulation()
        move = sim.move
        def disturbed(*, forward=False, dx=0, dy=0):
            move(forward=forward, dx=dx, dy=dy)
            if dx and abs(sim.yaw) > 10:
                sim.pitch = 15
        sim.move = disturbed
        sim.run([(0, 0, 0), (0, 0, 0)], final_yaw=60, final_pitch=0)
        self.assertLessEqual(abs(sim.pitch), 1)
        self.assertLessEqual(abs(sim.yaw - 60), 1)

    def test_position_changed_during_vertical_alignment_returns_current_state(self):
        sim = Simulation()
        move = sim.move
        def displaced(*, forward=False, dx=0, dy=0):
            move(forward=forward, dx=dx, dy=dy)
            if dy:
                sim.position[1] = 100
        sim.move = displaced
        sim.run([(0, 0, 0), (0, 0, 0)], final_yaw=0, final_pitch=30)
        self.assertAlmostEqual(sim.pitch, 30, delta=1)
        self.assertEqual(sim.position[1], 100)
        self.assertFalse(sim.forward)


    def test_drift_during_final_turn_does_not_abort_navigation(self):
        sim = Simulation()
        move = sim.move
        def displaced(*, forward=False, dx=0):
            if dx and sim.position[0] > 550 and not forward:
                sim.position[1] = 100
            move(forward=forward, dx=dx)
        sim.move = displaced
        sim.run([(0, 0, 0), (600, 0, 0)], final_yaw=90)
        self.assertAlmostEqual(sim.yaw, 90, delta=1)
        self.assertFalse(sim.forward)

    def test_large_turn_is_submitted_once_after_calibration(self):
        sim = Simulation(yaw=150, sensitivity=-.02)
        turns = []
        original = sim.move
        def move(**kwargs):
            if kwargs.get('dx'):
                turns.append(kwargs['dx'])
            original(**kwargs)
        sim.move = move
        sim.run([(0, 0, 0), (600, 0, 0)])
        self.assertEqual(len(turns), 2)  # One sensitivity probe, one complete turn.
        self.assertGreater(abs(turns[-1]), 200)
        self.assertLessEqual(math.dist(sim.position, (600, 0, 0)), 20)

    def test_route_can_align_final_heading_after_releasing_forward(self):
        sim = Simulation()
        sim.run([(0, 0, 0), (600, 0, 0)], final_yaw=90)
        self.assertLessEqual(math.dist(sim.position, (600, 0, 0)), 20)
        self.assertLessEqual(abs((sim.yaw - 90 + 180) % 360 - 180), 1)
        self.assertFalse(sim.forward)

    def test_final_heading_uses_shortest_turn_across_wrap(self):
        sim = Simulation(yaw=179)
        sim._route_sensitivity = sim.sensitivity
        sim.run([(0, 0, 0), (0, 0, 0)], final_yaw=-179)
        self.assertLessEqual(abs((sim.yaw + 179 + 180) % 360 - 180), 1)
        self.assertLess(sim.now, .5)
        self.assertEqual(sim.downs, 0)

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
        for failure in ('dead', 'map', 'focus'):
            with self.subTest(failure=failure):
                sim = Simulation()
                if failure == 'dead':
                    sim.dead = True
                elif failure == 'map':
                    sim.map_id = 2
                elif failure == 'focus':
                    sim.foreground = False
                points = [(0, 0, 0), (600, 0, 0)]
                with self.assertRaises(RuntimeError):
                    sim.run(points)
                self.assertEqual(sim.downs, 0)
                self.assertEqual(sim.yaw, 0)

    def test_distant_position_is_used_as_navigation_origin(self):
        sim = Simulation()
        sim.position[:] = (-600, 100, 0)
        sim.run([(0, 0, 0), (600, 0, 0)])
        self.assertLessEqual(math.dist(sim.position, (600, 0, 0)), 20)

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

    def test_fragmented_calibration_response_still_turns_and_presses_w(self):
        for delay in (.15, .2, .45, .9):
            with self.subTest(delay=delay):
                sim = Simulation(yaw=45, sensitivity=.5)
                move, wait = sim.move, sim.wait
                pending = []
                first = True

                def fragmented_move(*, forward=False, dx=0, dy=0):
                    nonlocal first
                    if dx and first:
                        first = False
                        move(forward=forward, dx=1)
                        pending.append((sim.now + delay, dx - 1))
                    else:
                        move(forward=forward, dx=dx, dy=dy)

                def fragmented_wait(ms=0):
                    wait(ms=ms)
                    for due, dx in list(pending):
                        if due <= sim.now:
                            move(forward=sim.forward, dx=dx)
                            pending.remove((due, dx))

                sim.move, sim.wait = fragmented_move, fragmented_wait
                sim.run([(0, 0, 0), (0, 29.1, 0)])
                self.assertAlmostEqual(sim._route_sensitivity, .5)
                self.assertGreater(sim.downs, 0)
                self.assertLessEqual(math.dist(sim.position, (0, 29.1, 0)), 20)
                self.assertFalse(sim.forward)

    def test_delayed_turns_do_not_queue_repeated_corrections_before_response(self):
        sim = Simulation(sensitivity=.2)
        sim._route_sensitivity = .2
        move, wait = sim.move, sim.wait
        pending = []

        def delayed_move(*, forward=False, dx=0, dy=0):
            move(forward=forward)
            if dx or dy:
                self.assertEqual(pending, [], '上一条鼠标输入尚未反馈，不能追加转向')
                pending.append((sim.now + .15, dx, dy))

        def delayed_wait(ms=0):
            wait(ms=ms)
            for due, dx, dy in list(pending):
                if due <= sim.now:
                    move(forward=sim.forward, dx=dx, dy=dy)
                    pending.remove((due, dx, dy))

        sim.move, sim.wait = delayed_move, delayed_wait
        sim.run([(0, 0, 0), (0, 29.1, 0)], final_yaw=179.64, final_pitch=30)
        self.assertEqual(pending, [])
        self.assertLessEqual(abs((sim.yaw - 179.64 + 180) % 360 - 180), 1)
        self.assertLessEqual(abs(sim.pitch - 30), 1)
        self.assertGreater(sim.downs, 0)
        self.assertLessEqual(math.dist(sim.position, (0, 29.1, 0)), 20)
        self.assertFalse(sim.forward)

    def test_stuck_character_stops_and_releases_forward(self):
        sim = Simulation(speed=0)
        with self.assertRaisesRegex(RuntimeError, '进展|卡住'):
            sim.run([(0, 0, 0), (600, 0, 0)], progress_timeout=.3)
        self.assertFalse(sim.forward)

    def test_straight_route_samples_while_w_is_held_and_finishes_near_endpoint(self):
        sim = Simulation()
        move = sim.move
        started = None
        def track_start(**kwargs):
            nonlocal started
            if kwargs.get('forward') and started is None:
                started = sim.now
            move(**kwargs)
        sim.move = track_start
        sim.run([(0, 0, 0), (600, 0, 0), (1200, 0, 0)])
        self.assertLessEqual(math.dist(sim.position, (1200, 0, 0)), 20)
        self.assertLess(sim.now - started, 2.6)
        self.assertEqual(sim.downs, 1)
        self.assertGreater(sim.samples_moving, 10)
        self.assertFalse(sim.forward)

    def test_small_heading_errors_are_corrected_without_stopping(self):
        sim = Simulation(yaw=12)
        wait = sim.wait
        disturbed = False
        walking_started = None
        def drift(ms=0):
            nonlocal disturbed, walking_started
            wait(ms)
            if sim.forward and walking_started is None:
                walking_started = sim.now
            if walking_started is not None and sim.now - walking_started >= .3 and not disturbed:
                sim.yaw += 8
                disturbed = True
        sim.wait = drift
        sim.run([(0, 0, 0), (600, 0, 0), (1200, 0, 0)])
        self.assertLessEqual(math.dist(sim.position, (1200, 0, 0)), 20)
        self.assertGreater(sim.turns_moving, 0)
        self.assertEqual(sim.downs, 1)

    def test_fast_updates_can_pass_intermediate_points_without_turning_back(self):
        sim = Simulation(speed=1800, period=.1)
        sim._route_sensitivity = sim.sensitivity
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


class SlidingCharacter(Simulation):
    """Character with the inertia measured from the 2026-10-14 recorded route.

    That recording cruises at 871 u/s, keeps sliding ~210 units after W is
    released, publishes telemetry every ~105 ms and each snapshot costs real
    time. A controller that only stops once a sample lands inside the 20 unit
    tolerance therefore never settles: it walks past the endpoint, turns 180°
    and walks back, forever.
    """

    def __init__(self, *, cruise=871., accel=3000., coast_seconds=.243, read_ms=60., **kwargs):
        super().__init__(**kwargs)
        self.cruise = cruise
        self.accel = accel
        self.coast_seconds = coast_seconds
        self.read_ms = read_ms
        self.speed_now = 0.
        self.direction = 0.
        self.value = None
        self.tick = -1

    def wait(self, ms=0):
        self._advance(ms / 1000)
        self.trace.append(tuple(self.position))
        if self.now > 60:
            raise AssertionError('controller failed to finish within simulation limit')

    def snapshot(self):
        self._advance(self.read_ms / 1000)          # a cross-process read costs time
        tick = int(self.now / self.period)
        if self.value is None or tick != self.tick:
            self.tick = tick
            self.value = PlayerState(self.map_id, tuple(self.position), self.yaw,
                                     self.dead, True, tick, self.pitch)
        return self.value

    def move(self, *, forward=False, dx=0, dy=0):
        if not forward and not (dx or dy):
            self.forward = False                    # release reads no telemetry
            return
        self.snapshot()                             # the context validates before sending
        if forward and not self.forward:
            self.downs += 1
        self.forward = forward
        self.yaw = (self.yaw + dx * self.sensitivity + 180) % 360 - 180

    def _advance(self, seconds):
        if seconds <= 0:
            return
        if self.forward:
            self.direction = self.yaw              # movement is camera-relative
            start = self.speed_now
            self.speed_now = min(self.cruise, start + self.accel * seconds)
            travel = (start + self.speed_now) / 2 * seconds
        else:
            start = self.speed_now
            self.speed_now = start * math.exp(-seconds / self.coast_seconds)
            travel = (start + self.speed_now) / 2 * seconds
        angle = math.radians(self.direction)
        self.position[0] += math.cos(angle) * travel
        self.position[1] += math.sin(angle) * travel
        self.now += seconds


class SlidingEndpointTests(unittest.TestCase):
    def test_next_correction_reuses_measured_slide_to_reduce_extra_presses(self):
        sim = SlidingCharacter(period=.105)
        sim.run([(0, 0, 0), (900, 0, 0)])
        first_presses = sim.downs
        sim.position[:] = (0, 0, 0)
        sim.speed_now = 0
        sim.value = None
        sim.run([(0, 0, 0), (900, 0, 0)])
        self.assertLess(sim.downs - first_presses, first_presses)
        self.assertLessEqual(math.dist(sim.position, (900, 0, 0)), 20)

    def test_correction_settles_previous_recorded_movement_before_accepting_position(self):
        sim = SlidingCharacter(period=.105, read_ms=0)
        sim._route_sensitivity = sim.sensitivity
        sim.speed_now = 500
        sim.run([(0, 0, 0), (0, 0, 0)], final_yaw=90)
        self.assertLessEqual(math.dist(sim.position, (0, 0, 0)), 20)
        self.assertAlmostEqual(sim.yaw, 90, delta=1)
        self.assertFalse(sim.forward)

    def test_expired_movement_lease_waits_for_slide_before_repressing(self):
        sim = SlidingCharacter(period=.2, read_ms=0)
        move = sim.move
        released = None
        premature = []

        def tracked_move(*, forward=False, dx=0, dy=0):
            nonlocal released
            if sim.forward and not forward:
                released = sim.now
            if forward and not sim.forward and released is not None:
                if sim.speed_now > 50:
                    premature.append(sim.speed_now)
            move(forward=forward, dx=dx, dy=dy)

        sim.move = tracked_move
        sim.run([(0, 0, 0), (300, 0, 0)])
        self.assertEqual(premature, [], '不能把松键后仍在滑行的角色当成已停稳')
        self.assertLessEqual(math.dist(sim.position, (300, 0, 0)), 20)

    def test_endpoint_is_reached_without_the_full_speed_turnaround(self):
        for distance in (76.7, 300., 900.):
            for period in (.05, .105, .2):
                with self.subTest(distance=distance, period=period):
                    sim = SlidingCharacter(period=period)
                    sim.run([(0, 0, 0), (distance, 0, 0)])
                    self.assertLessEqual(
                        math.dist(sim.position, (distance, 0, 0)), 20)
                    self.assertFalse(sim.forward)
                    self.assertLess(sim.now, 20)
                    self.assertLessEqual(sim.downs, 10)

