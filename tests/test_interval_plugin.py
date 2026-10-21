import unittest
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path


class IntervalPluginTests(unittest.TestCase):
    def plugin(self):
        from macroflow.execution.interval_plugin import IntervalConditionPlugin
        self.now = 0.0
        plugin = IntervalConditionPlugin(clock=lambda: self.now)
        plugin.configure({'enabled': True, 'interval_ms': 3600000,
                          'condition': {'module_ref': True, 'module_key': 'image'},
                          'actions': [{'type': 'notice', 'text': 'test'}]})
        return plugin

    def test_default_hour_gate_then_one_minute_checks_until_match(self):
        plugin = self.plugin()
        match = Mock(return_value=False)
        self.now = 3599
        plugin.check(match)
        match.assert_not_called()
        self.now = 3600
        plugin.check(match)
        match.assert_called_once()
        self.now = 3659
        plugin.check(match)
        self.assertEqual(match.call_count, 1)
        self.now = 3660
        match.return_value = True
        plugin.check(match)
        hit = plugin.claim()
        self.assertIsNotNone(hit)
        self.assertIsNone(plugin.claim())

    def test_next_interval_starts_after_action_completion(self):
        plugin = self.plugin()
        self.now = 3600
        plugin.check(lambda condition: True)
        hit = plugin.claim()
        self.now = 4200
        plugin.finish(hit['generation'], True)
        self.now = 7799
        match = Mock(return_value=True)
        plugin.check(match)
        match.assert_not_called()
        self.now = 7800
        plugin.check(match)
        match.assert_called_once()

    def test_disabling_or_reconfiguring_invalidates_queued_and_running_hits(self):
        plugin = self.plugin()
        self.now = 3600
        plugin.check(lambda condition: True)
        hit = plugin.claim()
        plugin.disable()
        self.assertFalse(plugin.valid(hit['generation']))
        plugin.finish(hit['generation'], True)
        self.assertIsNone(plugin.claim())
        # F12 只是卸下运行状态，用户保存的启用偏好不能被改写（否则下次打开会变未启用）。
        self.assertTrue(plugin.config['enabled'])
        self.assertFalse(plugin.armed)
        self.assertEqual(plugin.status(), '已停止，下次执行时恢复')
        plugin.arm()
        self.assertTrue(plugin.armed)
        self.assertNotEqual(plugin.status(), '已停止，下次执行时恢复')

    def test_matcher_error_and_failed_actions_retry_at_one_minute(self):
        plugin = self.plugin()
        self.now = 3600
        with self.assertRaises(RuntimeError):
            plugin.check(Mock(side_effect=RuntimeError('capture unavailable')))
        self.now = 3659
        plugin.check(Mock(side_effect=AssertionError('too early')))
        self.now = 3660
        plugin.check(lambda condition: True)
        hit = plugin.claim()
        plugin.finish(hit['generation'], False)
        self.now = 3719
        plugin.check(Mock(side_effect=AssertionError('too early')))
        self.now = 3720
        plugin.check(lambda condition: True)
        self.assertIsNotNone(plugin.claim())

    def test_enabled_requires_valid_interval_condition_and_actions(self):
        from macroflow.execution.interval_plugin import IntervalConditionPlugin
        for config in ({'enabled': True, 'condition': []}, {'enabled': True, 'interval_ms': 0},
                       {'enabled': True, 'interval_ms': True}, {'enabled': True, 'interval_ms': float('nan')}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                IntervalConditionPlugin().configure(config)

    def test_guard_completion_resets_plugin_after_success_and_failure(self):
        from macroflow.execution.player import MacroPlayer
        for error in (None, RuntimeError('action failed')):
            player = MacroPlayer(guard_settle_ms=0)
            complete = Mock()
            player._run_action_sequence = Mock(side_effect=error)
            hit = {'kind': 'success', 'actions': [{'type': 'notice'}], '_plugin_complete': complete}
            if error:
                with self.assertRaises(RuntimeError): player.handle_guard_hit(hit)
            else:
                player.handle_guard_hit(hit)
            complete.assert_called_once_with(error is None)

    def test_inline_end_and_last_row_jump_end_only_plugin_block(self):
        from macroflow.execution.player import MacroPlayer
        for control in ('end_current_script', 'jump_current_script_last'):
            player = MacroPlayer(guard_settle_ms=0, on_notice=Mock())
            complete = Mock()
            player.handle_guard_hit({'kind': 'success', '_plugin_complete': complete,
                'actions': [{'type': control}, {'type': 'notice', 'text': 'last', 'duration_ms': 1}]})
            complete.assert_called_once_with(True)
            self.assertEqual(player.on_notice.call_count, int(control == 'jump_current_script_last'))
            self.assertIsNone(player._plugin_action_scope_ids)

    def test_inline_global_detection_can_unblock_its_own_script(self):
        from macroflow.execution.player import MacroPlayer
        player = MacroPlayer(guard_settle_ms=0, on_notice=Mock())
        ids = ('global', 'wait', 'done')
        checks = [0]
        def poll():
            checks[0] += 1
            if checks[0] == 3:
                return {'kind': 'success', 'jump_action_id': 'done', 'scope_action_ids': ids}
            return None
        player.on_guard_poll = poll
        complete = Mock()
        player.handle_guard_hit({'kind': 'success', '_plugin_complete': complete, 'actions': [
            {'type': 'global_detect', 'action_id': ids[0]},
            {'type': 'block', 'action_id': ids[1]},
            {'type': 'notice', 'text': 'done', 'action_id': ids[2], 'duration_ms': 1}]})
        self.assertGreaterEqual(checks[0], 4)
        player.on_notice.assert_called_once_with('done', 500)
        complete.assert_called_once_with(True)

    def test_stale_and_disabled_match_results_cannot_be_claimed(self):
        plugin = self.plugin()
        self.now = 3600
        plugin.check(lambda condition: True)
        self.now = 3661
        self.assertIsNone(plugin.claim())
        plugin.check(lambda condition: (plugin.disable() or True))
        self.assertIsNone(plugin.claim())

    def test_plugin_scope_rejects_parent_hits_without_consuming_them(self):
        from types import SimpleNamespace
        from macroflow.execution.player import MacroPlayer
        from macroflow.ui.app.guards import GuardsMixin
        player = MacroPlayer()
        player._plugin_action_scope_ids = frozenset({'plugin'})
        app = SimpleNamespace(player=player, _rollback_detection_hit=Mock())
        parent_hit = {'scope_action_ids': ('parent',)}
        plugin_hit = {'scope_action_ids': ('plugin',)}
        self.assertIsNone(GuardsMixin._plugin_scope_hit(app, parent_hit))
        app._rollback_detection_hit.assert_called_once_with(parent_hit)
        self.assertIs(GuardsMixin._plugin_scope_hit(app, plugin_hit), plugin_hit)

    def test_referenced_script_scopes_and_nonjump_hits_belong_to_plugin(self):
        from types import SimpleNamespace
        from macroflow.execution.player import MacroPlayer
        from macroflow.ui.app.guards import GuardsMixin
        player = MacroPlayer(guard_settle_ms=0)
        player.on_script_scope_enter = Mock(return_value=())
        player.on_script_scope_exit = Mock()
        app = SimpleNamespace(player=player, _rollback_detection_hit=Mock(),
                              _global_monitor_subject=lambda *args: 'test',
                              _warn_guard_without_action=Mock())
        def referenced(*args, **kwargs):
            from pathlib import Path
            player.on_script_scope_enter([{'type': 'global_detect', 'action_id': 'reference'}])
            for kind in ('success', 'timeout', 'timed'):
                guard = {'trigger_kind': kind, 'scope_action_ids': ('reference',), 'template': Path('test.png')}
                hit = GuardsMixin._build_guard_hit(app, guard, resolve_hwnd=False)
                self.assertEqual(hit['scope_action_ids'], ('reference',))
                self.assertIs(GuardsMixin._plugin_scope_hit(app, hit), hit)
        player._run_action_sequence = referenced
        complete = Mock()
        original = player.on_script_scope_enter
        player.handle_guard_hit({'_plugin_complete': complete, 'actions': [{'type': 'script_ref'}]})
        complete.assert_called_once_with(True)
        self.assertIs(player.on_script_scope_enter, original)
        self.assertIsNone(player._plugin_owned_scopes)

    def test_idle_plugin_rearms_pause_state_and_cannot_restart_after_cancel_race(self):
        import threading
        from types import SimpleNamespace
        from macroflow.execution.player import MacroPlayer, PlaybackStopped
        from macroflow.ui.app.plugins_runtime import _start_idle_plugin
        plugin = self.plugin()
        self.now = 3600
        plugin.check(lambda condition: True)
        claimed = plugin.claim()
        player = MacroPlayer()
        original = player.on_action_start = Mock()
        stop = threading.Event()
        stop.set()
        app = SimpleNamespace(player=player, workflow_stop=stop, _bound_hwnd=Mock(return_value=None),
                              activate_target_enabled_var=Mock(get=Mock(return_value=False)),
                              _ui=Mock(), _log=Mock(), _refresh_execution_pause_controls=Mock())
        def play(*args, **kwargs):
            plugin.disable()
            stop.set()
            player.stop()
            player.reset()  # Playback's initial reset must not undo F12's generation cancellation.
            player.on_action_start({})
        player.play = Mock(side_effect=play)
        with patch('macroflow.ui.app.plugins_runtime.threading.Thread') as thread:
            thread.side_effect = lambda **kwargs: SimpleNamespace(start=kwargs['target'])
            _start_idle_plugin(app, plugin, claimed)
        original.assert_not_called()
        self.assertIs(player.on_action_start, original)
        self.assertTrue(stop.is_set())
        self.assertFalse(plugin.armed)
        self.assertTrue(plugin.config['enabled'])
        app._refresh_execution_pause_controls.assert_called_once()


if __name__ == '__main__': unittest.main()
