import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from macroflow.ui.app import plugins_page, plugins_runtime
from macroflow.ui.app.main import MacroFlowApp
from macroflow.execution.player import MacroPlayer


class PluginConditionsTests(unittest.TestCase):
    def test_text_binding_and_detection_use_module_text_rules(self):
        binding = {'module_ref': True, 'module_key': 'module:text'}
        obj = {'category': 'switch', 'recognize': 'text', 'expected_text': '准备完成',
               'match_mode': 'equals', 'region': [10, 20, 100, 50]}
        with patch.object(plugins_page, 'registered_module_object', return_value=obj):
            self.assertEqual(plugins_page.recognition_condition(binding)['type'], 'ocr_match')
        app = Mock()
        app._guard_text_detect.return_value = (True, None)
        with patch.object(plugins_runtime, 'registered_module_object', return_value=obj):
            self.assertTrue(plugins_runtime.condition_matches(app, binding))
        guard = app._guard_text_detect.call_args.args[0]
        self.assertEqual(guard['expected_text'], '准备完成')
        self.assertEqual(guard['match_mode'], 'equals')
        self.assertEqual(guard['region'], (10, 20, 100, 50))
        app._guard_image_detect.assert_not_called()
        app._guard_text_detect.return_value = (False, None)
        with patch.object(plugins_runtime, 'registered_module_object', return_value=obj):
            self.assertFalse(plugins_runtime.condition_matches(app, binding))

    def test_workflow_wait_retains_remaining_time_across_plugin(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        now = [0.0]
        app.workflow_stop = Mock(is_set=Mock(return_value=False))
        app.workflow_stop.wait.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
        app.player = Mock(stop_event=threading.Event())
        app.player.wait_while_paused.return_value = 0
        app.player.handle_guard_hit.side_effect = lambda hit: now.__setitem__(0, now[0] + 10)
        hits = iter([{'_plugin_complete': Mock()}, None])
        app._evaluate_global_guards = lambda: next(hits, None)
        app._workflow_heartbeat = Mock()
        with patch.dict(app._guard_wait.__func__.__globals__, {'time': SimpleNamespace(perf_counter=lambda: now[0])}):
            self.assertTrue(app._guard_wait(1))
        self.assertAlmostEqual(now[0], 11)

    def test_plugin_finishes_before_parent_next_action(self):
        events = []
        player = MacroPlayer(guard_settle_ms=0, on_notice=lambda text, *args: events.append(text))
        hit = {'_plugin_complete': lambda success: events.append(('complete', success)),
               'actions': [{'type': 'notice', 'text': 'plugin', 'duration_ms': 1}]}
        hits = iter([None, hit])
        player.on_guard_poll = lambda: next(hits, None)
        player.play([{'type': 'notice', 'text': 'before', 'duration_ms': 1},
                     {'type': 'notice', 'text': 'after', 'duration_ms': 1}], activate_target=False)
        self.assertEqual(events, ['before', 'plugin', ('complete', True), 'after'])
