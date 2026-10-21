import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path
from tests.test_recorded_step_editor import route_dialog
from tests.test_ssjj_waypoint_playback import straight_recording


class RouteScriptActionTests(unittest.TestCase):
    def test_wait_action_validates_and_blocks_route_clock_with_keys_released(self):
        from macroflow.execution.ssjj_recording import validate_recording
        action = straight_recording()
        action['inputs'].insert(1, {'type': 'ssjj_wait_mobs_absent', 'duration_ms': 5000,
            'route_script_action': True, 'recorded_at_ms': 450, 'delay_ms': 0})
        validate_recording(action)
        from tests.test_ssjj_waypoint_playback import PlaybackTests
        case = PlaybackTests()
        observations = []
        def wait(player, event):
            observations.append((set(player._held_keys), case.sim.forward))
            case.sim.wait(ms=5000)
        with patch('macroflow.execution.ssjj_pve.wait_mobs_absent', side_effect=wait):
            case.run_recording(action)
        self.assertEqual(observations, [(set(), False)])
        key_events = [(args, at) for name, args, at, *_ in case.events if name == 'send_key' and args[0] == 87]
        self.assertEqual([args for args, _ in key_events], [(87, True), (87, False), (87, True), (87, False)])
        self.assertAlmostEqual(key_events[-1][1] - key_events[-2][1], .430, delta=.02)

    def test_main_action_insert_uses_existing_dialog_and_current_row_below(self):
        from macroflow.ui.dialogs.route_action_insert import RouteActionInserter
        dialog = route_dialog()
        dialog.inputs_list = Mock()
        dialog.reload = Mock()
        dialog._select_input_row = Mock()
        dialog.inputs_list.curselection.return_value = (dialog.input_rows.index(('input', 1)),)
        insert = RouteActionInserter(dialog, app=None)
        with patch('macroflow.ui.app.scripts.DurationDialog') as duration:
            duration.return_value.show.return_value = 5000
            insert.add_wait_mobs_absent()
        step = dialog.inputs[2]
        self.assertEqual(step['type'], 'ssjj_wait_mobs_absent')
        self.assertTrue(step['route_script_action'])
        self.assertEqual(step['recorded_at_ms'], 600)
        self.assertEqual(dialog.inputs[3]['recorded_at_ms'], 1400)
        dialog.save()
        self.assertIsNotNone(dialog.result)

    def test_edit_uses_main_dialog_and_keeps_route_time_and_identity(self):
        dialog = route_dialog()
        dialog.inputs_list = Mock()
        event = {'type': 'ssjj_wait_mobs_absent', 'duration_ms': 5000,
                 'route_script_action': True, 'recorded_at_ms': 100, 'action_id': 'keep'}
        dialog.inputs[1] = event
        dialog.inputs_list.curselection.return_value = (dialog.input_rows.index(('input', 1)),)
        dialog.reload = Mock()
        dialog._select_input_row = Mock()
        with patch('macroflow.ui.dialogs.actions.edit_action',
                   return_value={'type': 'ssjj_wait_mobs_absent', 'duration_ms': 8000, 'action_id': 'keep'}) as edit:
            dialog._edit_input()
        self.assertNotIn('recorded_at_ms', edit.call_args.args[1])
        self.assertEqual(dialog.inputs[1]['recorded_at_ms'], 100)
        self.assertEqual(dialog.inputs[1]['duration_ms'], 8000)
        self.assertTrue(dialog.inputs[1]['route_script_action'])

    def test_palette_reuses_all_main_insertion_actions(self):
        from macroflow.ui.dialogs.route_action_insert import route_action_categories
        from macroflow.ui.app.shell import ShellMixin
        commands = {spec[1] for _, specs in route_action_categories() for spec in specs}
        expected = {spec[1] for spec in ShellMixin._script_action_button_specs()} - {
            'open_ssjj_coordinates', '_toggle_record_from_toolbar'}
        self.assertEqual(commands, expected)

    def test_jump_over_recorded_release_does_not_restore_old_hold(self):
        from tests.test_ssjj_waypoint_playback import PlaybackTests
        action = straight_recording()
        action['inputs'].append({'type': 'notice', 'text': 'done', 'action_id': 'after-release',
                                'route_script_action': True, 'recorded_at_ms': 900})
        action['inputs'].insert(1, {'type': 'jump', 'jump_action_id': 'after-release',
            'workflow_repeat_at_least_2': False, 'route_script_action': True, 'recorded_at_ms': 450})
        case = PlaybackTests()
        case.run_recording(action)
        keys = [args for name, args, *_ in case.events if name == 'send_key' and args[0] == 87]
        self.assertEqual(keys, [(87, True), (87, False)])

    def replay(self, action=None):
        from macroflow.execution.ssjj_recording import RecordedReplay
        from macroflow.execution.player import MacroPlayer
        player = MacroPlayer()
        session = Mock()
        session.wanted_keys = {99}
        session.wanted_buttons = {'right'}
        return RecordedReplay(player, Mock(), session, Mock(), action or straight_recording())

    def test_branch_restores_key_and_button_state_before_target(self):
        action = straight_recording()
        action['inputs'].insert(1, {'type': 'mouse_button', 'button': 'left', 'down': True,
                                    'recorded_at_ms': 100})
        replay = self.replay(action)
        replay.restore_branch_holds(2)
        self.assertEqual(replay.session.wanted_keys, {87})
        self.assertEqual(replay.session.wanted_buttons, {'left'})
        replay.restore_branch_holds(0)
        self.assertEqual(replay.session.wanted_keys, set())
        self.assertEqual(replay.session.wanted_buttons, set())

    def test_route_global_scope_is_entered_and_exited_even_on_stop(self):
        from macroflow.execution.player import PlaybackStopped
        replay = self.replay()
        replay.correct = Mock()
        replay.replay = Mock(side_effect=PlaybackStopped())
        replay.player.on_script_scope_enter = Mock(return_value=('guard',))
        replay.player.on_script_scope_exit = Mock()
        with self.assertRaises(PlaybackStopped): replay.run()
        replay.player.on_script_scope_enter.assert_called_once_with(replay.inputs)
        replay.player.on_script_scope_exit.assert_called_once_with(('guard',))
        self.assertFalse(replay.player._script_scope_managed)

    def test_route_guard_jumps_locally_and_parent_guard_propagates(self):
        from macroflow.execution.player.control import GuardJumpRequest
        replay = self.replay()
        for i, event in enumerate(replay.inputs): event['action_id'] = f'row{i}'
        replay._replay = Mock(side_effect=[GuardJumpRequest('row1', scope_action_ids=('row0','row1')), None])
        replay.replay()
        self.assertEqual(replay._replay.call_args.args, (1, 1, 880))
        replay._replay.side_effect = GuardJumpRequest('outer', scope_action_ids=('outer',))
        with self.assertRaises(GuardJumpRequest): replay.replay()

    def test_script_action_uses_before_and_after_delay_order_and_restores_aim_ownership(self):
        replay = self.replay()
        events = []
        replay.player._ssjj_aim_suspended = True
        replay.player._wait = lambda ms: events.append(('wait', ms))
        replay.player._execute_action = Mock(side_effect=lambda *args: events.append(('action', args[0]['type'])))
        replay.script_action({'type': 'notice', 'text': 'test', 'recorded_at_ms': 300,
                              'route_script_action': True, 'delay_ms': 250, 'after_delay_ms': 300})
        self.assertEqual(events, [('wait', 250), ('action', 'notice'), ('wait', 300)])
        self.assertTrue(replay.player._ssjj_aim_suspended)

    def test_generic_keys_do_not_change_raw_key_repeat_filter(self):
        from macroflow.execution.ssjj_recording import distinct_presses
        raw = {'type': 'key', 'vk': 87, 'down': True}
        generic = dict(raw, route_script_action=True)
        self.assertEqual(list(distinct_presses([generic, raw, raw])), [generic, raw])

    def test_disabled_globals_cannot_be_registered_by_route(self):
        replay = self.replay()
        replay.correct = replay.replay = replay.verify_end = Mock()
        replay.player._enable_script_globals = False
        replay.player.on_script_scope_enter = Mock()
        replay.run()
        replay.player.on_script_scope_enter.assert_not_called()

    def test_single_route_execution_does_not_block_on_inner_global_row(self):
        replay = self.replay()
        replay.player._single_action = True
        replay.player._script_scope_managed = True
        replay.player._wait = Mock()
        replay.player._poll_guards = Mock(side_effect=AssertionError('single-row global wait must not run'))
        replay.script_action({'type': 'global_detect', 'route_script_action': True, 'recorded_at_ms': 0})
        self.assertTrue(replay.player._single_action)

    def test_repeated_key_row_remains_a_valid_jump_target(self):
        action = straight_recording()
        action['inputs'].insert(1, {'type': 'key', 'vk': 87, 'down': True,
            'recorded_at_ms': 100, 'action_id': 'repeat'})
        replay = self.replay(action)
        self.assertEqual(replay.jump_index(('action_id', 'repeat')), 1)


class FakeAimLock:
    """只保留 run_aim_action / update_aim_ownership 用到的开关状态。"""

    def __init__(self, targets=()):
        self.enabled = set(targets)

    def toggle(self, target):
        self.enabled.symmetric_difference_update({target})
        return target in self.enabled

    def set_enabled(self, target, enabled):
        (self.enabled.add if enabled else self.enabled.discard)(target)


class RouteAimOwnershipTests(unittest.TestCase):
    """路线里的光标锁定动作：后面没有录制镜头时才接管镜头。"""

    def replay(self, action=None):
        return RouteScriptActionTests().replay(action)

    def aim_event(self, at=450, operation='toggle'):
        return {'type': 'ssjj_aim', 'operation': operation, 'target': 'boss',
                'route_script_action': True, 'recorded_at_ms': at, 'delay_ms': 0}

    def prepare(self, action=None, *, enabled=True):
        replay = self.replay(action)
        replay.player._ssjj_aim_lock = FakeAimLock({'boss'} if enabled else ())
        replay.player._ssjj_aim_suspended = True
        replay.player._wait = Mock()
        return replay

    def test_the_lock_takes_the_camera_when_no_recorded_camera_inputs_follow(self):
        replay = self.prepare(enabled=False)
        logs = []
        replay.player.on_log = logs.append
        replay.script_action(self.aim_event())
        self.assertTrue(replay.aim_takes_camera)
        self.assertFalse(replay.player._ssjj_aim_suspended, "锁定后才能真的跟随")
        self.assertTrue(any('后面不再有录制镜头' in message for message in logs))

    def test_recorded_camera_inputs_keep_the_route_owning_the_camera(self):
        action = straight_recording()
        action['inputs'].append({'type': 'mouse_move', 'dx': 5, 'dy': 0, 'recorded_at_ms': 700})
        replay = self.prepare(action, enabled=False)
        logs = []
        replay.player.on_log = logs.append
        replay.script_action(self.aim_event())
        self.assertFalse(replay.aim_takes_camera)
        self.assertTrue(replay.player._ssjj_aim_suspended, "录制的转向照旧独占镜头")
        self.assertTrue(any('还有 1 条录制镜头输入' in message for message in logs))

    def test_unlocking_gives_the_camera_back_to_the_route(self):
        replay = self.prepare(enabled=False)
        replay.script_action(self.aim_event())
        self.assertTrue(replay.aim_takes_camera)
        replay.script_action(self.aim_event(at=500, operation='unlock'))
        self.assertFalse(replay.aim_takes_camera)
        self.assertTrue(replay.player._ssjj_aim_suspended)

    def test_navigation_temporarily_takes_the_camera_back(self):
        replay = self.prepare()
        replay.aim_takes_camera = True
        replay.player._ssjj_aim_suspended = False
        with replay.navigation_camera():
            self.assertTrue(replay.player._ssjj_aim_suspended)
        self.assertFalse(replay.player._ssjj_aim_suspended)

    def test_navigation_leaves_the_route_ownership_untouched(self):
        replay = self.prepare()
        replay.player._ssjj_aim_suspended = True
        with replay.navigation_camera():
            self.assertTrue(replay.player._ssjj_aim_suspended)
        self.assertTrue(replay.player._ssjj_aim_suspended)


if __name__ == '__main__': unittest.main()
