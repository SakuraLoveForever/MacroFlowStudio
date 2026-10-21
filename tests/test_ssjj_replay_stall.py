"""Replay scheduling interruptions; no game process, windows or real input."""
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from macroflow.execution.player import MacroPlayer
from macroflow.execution.ssjj_recording import ReplayInputs, RecordedReplay
from tests.test_ssjj_waypoint_playback import state, straight_recording


class ReplayStallTests(unittest.TestCase):
    def session(self):
        player, reader = MacroPlayer(), Mock()
        player.on_log = Mock()
        player.on_trace_line = Mock()
        reader.snapshot_stable.return_value = state(tick=2)
        with patch('macroflow.execution.ssjj_recording.threading.Thread'):
            session = ReplayInputs(player, reader)
        return session

    def expire(self, session):
        with patch.object(session.closed, 'wait', side_effect=[False, True]):
            session._watch()

    def test_expired_lease_releases_then_recovers_without_failing_workflow(self):
        session = self.session()
        with patch('macroflow.input.wininput.send_key') as key, \
                patch('macroflow.input.wininput.send_button') as button:
            session.deadline = float('inf')
            session.send({'type': 'key', 'vk': 65, 'down': True})
            session.send({'type': 'mouse_button', 'button': 'left', 'down': True})
            session.deadline = 0
            self.expire(session)
            self.assertFalse(session.keys or session.buttons)
            self.assertIsNone(session.error)
            self.assertIsNotNone(session.recover(100001))
            session.renew()
            session.restore()
            self.assertEqual(session.keys, {65})
            self.assertEqual(session.buttons, {'left'})
            self.assertEqual([c.args for c in key.call_args_list], [(65, True), (65, False), (65, True)])
            self.assertEqual([c.args for c in button.call_args_list], [('left', True), ('left', False), ('left', True)])
            session.player.on_log.assert_called_once()

    def test_recovery_rejects_death_map_change_and_frozen_state(self):
        for invalid in ('dead', 'map', 'frozen'):
            with self.subTest(invalid=invalid):
                session = self.session()
                with patch('macroflow.input.wininput.send_key') as key:
                    session.send({'type': 'key', 'vk': 65, 'down': True})
                    session.deadline = 0
                    self.expire(session)
                    sample = state(tick=2)
                    if invalid == 'dead': sample = replace(sample, dead=True)
                    if invalid == 'map': sample = replace(sample, map_id=100002)
                    if invalid == 'frozen':
                        session.previous_tick = sample.client_time
                        session.progressed_at = -float('inf')
                    session.reader.snapshot_stable.return_value = sample
                    key.reset_mock()
                    with self.assertRaises(RuntimeError):
                        session.recover(100001)
                    key.assert_not_called()
                    self.assertFalse(session.keys)

    def test_replay_preserves_remaining_hold_time_after_scheduler_stall(self):
        session = self.session()
        now, stalled, events = [0.0], [False], []
        context = Mock()
        def wait(ms=0):
            now[0] += ms / 1000
            if now[0] >= .1 and not stalled[0]:
                stalled[0] = True
                now[0] += .4
                self.expire(session)
                now[0] += .6
        context.wait.side_effect = wait
        session.reader.snapshot_stable.side_effect = lambda: state(tick=int(now[0] * 1000) + 1)
        replay = RecordedReplay(session.player, session.reader, session, context, straight_recording())
        with patch('macroflow.execution.ssjj_recording.time.perf_counter', side_effect=lambda: now[0]), \
                patch('macroflow.input.wininput.send_key', side_effect=lambda vk, down: events.append((vk, down, now[0]))):
            replay._replay(0, 1, 0)
        self.assertTrue(stalled[0])
        presses = [t for vk, down, t in events if vk == 87 and down]
        releases = [t for vk, down, t in events if vk == 87 and not down]
        self.assertEqual(len(presses), 2)
        self.assertEqual(len(releases), 2)
        self.assertGreater(releases[-1] - presses[-1], .2)
        self.assertFalse(session.keys)
