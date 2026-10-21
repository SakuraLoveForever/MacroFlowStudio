import unittest
import threading
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path
from macroflow.execution.player import MacroPlayer, PlaybackStopped
from macroflow.execution.ssjj_navigation import RouteContext
from tests.test_ssjj_navigation import state


class ShiftBrakeTests(unittest.TestCase):
    def context(self):
        reader = Mock()
        reader.snapshot_stable.return_value = state()
        context = RouteContext(MacroPlayer(), reader, 100001)
        self.addCleanup(context.close)
        return context

    def test_brake_releases_movement_before_short_shift_press(self):
        context = self.context()
        context.wait = Mock()
        with patch('macroflow.input.wininput.send_key') as key:
            context.move(forward=True)
            context.brake()
        self.assertEqual([call.args for call in key.call_args_list],
                         [(87, True), (87, False), (16, True), (16, False)])
        context.wait.assert_called_once_with(ms=40)
        self.assertFalse(context.player._held_keys)
        self.assertFalse(context.forward)

    def test_stop_during_brake_still_releases_shift(self):
        context = self.context()
        def stop(**kwargs):
            context.player.stop()
            raise PlaybackStopped()
        context.wait = stop
        with patch('macroflow.input.wininput.send_key') as key:
            with self.assertRaises(PlaybackStopped): context.brake()
        self.assertEqual([call.args for call in key.call_args_list], [(16, True), (16, False)])
        self.assertFalse(context.player._held_keys)

    def test_pause_stop_focus_loss_and_existing_shift_do_not_press_brake(self):
        for reason in ('pause', 'stop', 'focus', 'shift'):
            with self.subTest(reason=reason):
                context = self.context()
                if reason == 'pause': context.player.pause()
                if reason == 'stop': context.player.stop()
                if reason == 'focus': context.reader.require_foreground.side_effect = RuntimeError('focus lost')
                if reason == 'shift': context.player._held_keys.add(160)
                with patch('macroflow.input.wininput.send_key') as key:
                    if reason == 'focus':
                        with self.assertRaises(RuntimeError): context.brake()
                    else:
                        context.brake()
                    key.assert_not_called()
                if reason == 'shift': self.assertIn(160, context.player._held_keys)

    def test_shift_send_failure_still_attempts_release(self):
        context = self.context()
        with patch('macroflow.input.wininput.send_key', side_effect=[RuntimeError('send failed'), None]) as key:
            with self.assertRaisesRegex(RuntimeError, 'send failed'): context.brake()
        self.assertEqual([call.args for call in key.call_args_list], [(16, True), (16, False)])
        self.assertFalse(context.player._held_keys)

    def test_watchdog_releases_shift_even_when_brake_wait_stalls(self):
        context = self.context()
        released = threading.Event()
        def send(vk, down):
            if vk == 16 and not down: released.set()
        def stalled_wait(**kwargs):
            self.assertTrue(released.wait(.4))
        context.wait = stalled_wait
        with patch('macroflow.input.wininput.send_key', side_effect=send):
            context.brake()
        self.assertFalse(context._braking)
        self.assertNotIn(16, context.player._held_keys)

    def test_braking_stops_inertial_route_inside_target_tolerance(self):
        from tests.test_ssjj_continuous import SlidingCharacter
        class BrakingSimulation(SlidingCharacter):
            def brake(self):
                self.move()
                self.speed_now = 0
        sim = BrakingSimulation()
        sim.run([(0, 0, 0), (500, 0, 0)])
        self.assertLessEqual(abs(sim.position[0] - 500), 20)
        self.assertEqual(sim.speed_now, 0)

    def test_recording_end_brakes_after_releasing_recorded_movement(self):
        from tests.test_ssjj_waypoint_playback import PlaybackTests, straight_recording
        case = PlaybackTests()
        case.run_recording(straight_recording())
        keys = [event[1] for event in case.events if event[0] == 'send_key']
        release = keys.index((87, False))
        self.assertEqual(keys[release+1:release+3], [(16, True), (16, False)])


if __name__ == '__main__': unittest.main()
