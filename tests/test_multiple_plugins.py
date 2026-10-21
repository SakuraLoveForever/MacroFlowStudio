import unittest
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

from macroflow.ui.app import plugins_runtime as runtime
from macroflow.execution.interval_plugin import IntervalConditionPlugin


class MultiplePluginTests(unittest.TestCase):
    def app(self):
        return SimpleNamespace(_interval_plugins={}, app_settings={}, player=SimpleNamespace(paused=False, stop_event=threading.Event()),
                               _bound_hwnd=Mock(return_value=123))

    def test_add_defaults_enabled_and_persists_independent_ids(self):
        app = self.app()
        with patch.object(runtime, 'save_app_settings'):
            first = runtime.add_interval_plugin(app)
            second = runtime.add_interval_plugin(app)
        self.assertNotEqual(first, second)
        self.assertEqual(len(app._interval_plugins), 2)
        for plugin in app._interval_plugins.values():
            self.assertTrue(plugin.config['enabled'])
            self.assertEqual(plugin.config['interval_ms'], 3600000)
            self.assertEqual(plugin.status(), '待配置')
            plugin.next_check = 0
            match = Mock()
            plugin.check(match)
            match.assert_not_called()
        self.assertEqual(len(app.app_settings['interval_condition_plugins']), 2)

    def test_save_and_disable_do_not_reset_another_plugins_clock(self):
        app = self.app()
        with patch.object(runtime, 'save_app_settings'):
            first = runtime.add_interval_plugin(app)
            second = runtime.add_interval_plugin(app)
            other = app._interval_plugins[second]
            before = (other.generation, other.next_check)
            runtime.save_interval_plugin(app, {**app._interval_plugins[first].config, 'enabled': False})
        self.assertEqual((other.generation, other.next_check), before)
        self.assertTrue(other.config['enabled'])
        self.assertFalse(app._interval_plugins[first].config['enabled'])

    def test_ready_plugins_run_serially_and_f12_disables_all(self):
        app = self.app()
        for name in ('first', 'second'):
            plugin = IntervalConditionPlugin(clock=lambda: 10)
            plugin.configure({'id': name, 'name': name, 'enabled': True, 'interval_ms': 1000,
                              'condition': {'module_key': name}, 'actions': [{'type': 'notice', 'text': name}]})
            plugin.next_check = 0
            plugin.check(lambda condition: True)
            app._interval_plugins[name] = plugin
        first = runtime.claim_plugin_hit(app)
        self.assertEqual(first['actions'][0]['text'], 'first')
        self.assertIsNone(runtime.claim_plugin_hit(app))
        first['_plugin_complete'](True)
        second = runtime.claim_plugin_hit(app)
        self.assertEqual(second['actions'][0]['text'], 'second')
        runtime.stop_interval_plugins(app)
        self.assertFalse(second['_plugin_valid']())
        # F12 只是卸下运行状态：用户保存的启用开关必须原样保留。
        self.assertTrue(all(p.config['enabled'] for p in app._interval_plugins.values()))
        self.assertTrue(all(not p.armed for p in app._interval_plugins.values()))
        self.assertIsNone(runtime.claim_plugin_hit(app))

    def test_f12_stop_keeps_saved_enable_preference_and_next_run_rearms(self):
        app = self.app()
        with patch.object(runtime, 'save_app_settings'):
            key = runtime.add_interval_plugin(app)
        plugin = app._interval_plugins[key]
        plugin.configure({**plugin.config, 'condition': {'module_key': 'image'},
                          'actions': [{'type': 'notice', 'text': 'once'}]})
        runtime.stop_interval_plugins(app)
        # 侧栏保存直接读 plugin.config：停止执行后再保存设置仍是启用，下次打开不会变关闭。
        self.assertTrue(plugin.config['enabled'])
        self.assertFalse(plugin.armed)
        self.assertEqual([p.config for p in app._interval_plugins.values()][0]['enabled'], True)
        runtime.arm_interval_plugins(app)
        self.assertTrue(plugin.armed)
        self.assertNotEqual(plugin.status(), '已停止，下次执行时恢复')

    def test_detection_run_entry_rearms_plugins_after_f12(self):
        from macroflow.execution.player import MacroPlayer
        from macroflow.ui.app.execution import ExecutionMixin
        plugin = IntervalConditionPlugin(clock=lambda: 0)
        plugin.configure({'id': 'p', 'name': 'p', 'enabled': True, 'interval_ms': 1000,
                          'condition': {'module_key': 'image'}, 'actions': [{'type': 'notice'}]})
        plugin.disable()
        app = SimpleNamespace(player=MacroPlayer(), _interval_plugins={'p': plugin},
                              _on_action_start=Mock(), _set_current_execution_action=Mock())
        ExecutionMixin._begin_detection_run(app)
        self.assertTrue(plugin.armed)

    def test_arm_is_a_noop_while_still_armed(self):
        now = [0]
        plugin = IntervalConditionPlugin(clock=lambda: now[0])
        plugin.configure({'enabled': True, 'interval_ms': 1000, 'condition': {'module_key': 'm'},
                          'actions': [{'type': 'notice'}]})
        now[0] = 1
        plugin.check(lambda condition: True)
        generation, next_check = plugin.generation, plugin.next_check
        runtime.arm_interval_plugins(SimpleNamespace(_interval_plugins={'p': plugin}))
        self.assertEqual((plugin.generation, plugin.next_check), (generation, next_check))
        self.assertIsNotNone(plugin.claim())

    def test_hotkey_script_start_rearms_plugins_after_f12(self):
        from macroflow.ui.app.runtime.hotkeys import RuntimeHotkeysMixin
        plugin = IntervalConditionPlugin(clock=lambda: 0)
        plugin.configure({'enabled': True, 'interval_ms': 1000, 'condition': {'module_key': 'm'},
                          'actions': [{'type': 'notice'}]})
        plugin.disable()
        app = SimpleNamespace(exiting=False, hotkey_config_open=False, _hotkey_script_running=False,
                              _hotkey_script_lock=threading.Lock(), _hotkey_active_binding=None,
                              _hotkey_cancel=Mock(), _hotkey_script_worker=Mock(),
                              _interval_plugins={'p': plugin})
        with patch('macroflow.ui.app.runtime.hotkeys.threading.Thread') as thread:
            thread.side_effect = lambda **kwargs: SimpleNamespace(start=lambda: None)
            RuntimeHotkeysMixin._trigger_hotkey_script(app, {'key': 'F6', 'vk': 1})
        self.assertTrue(plugin.armed)

    def test_failed_persistence_does_not_mutate_existing_plugins(self):
        app = self.app()
        with patch.object(runtime, 'save_app_settings'):
            key = runtime.add_interval_plugin(app)
        before = app._interval_plugins[key].generation
        with patch.object(runtime, 'save_app_settings', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                runtime.save_interval_plugin(app, {**app._interval_plugins[key].config, 'enabled': False})
        self.assertTrue(app._interval_plugins[key].config['enabled'])
        self.assertEqual(app._interval_plugins[key].generation, before)

    def test_restart_restores_multiple_instances_and_one_monitor(self):
        app = self.app()
        del app._interval_plugins
        app.root, app._ui, app._log = Mock(), Mock(), Mock()
        app.app_settings['interval_condition_plugins'] = [
            {'id': 'one', 'name': '插件一', 'enabled': True},
            {'id': 'two', 'name': '插件二', 'enabled': False},
        ]
        with patch.object(runtime.threading, 'Thread') as thread:
            plugins = runtime.get_interval_plugins(app)
            self.assertIs(runtime.get_interval_plugins(app), plugins)
        self.assertEqual(list(plugins), ['one', 'two'])
        self.assertTrue(plugins['one'].config['enabled'])
        self.assertFalse(plugins['two'].config['enabled'])
        thread.assert_called_once()
        self.assertEqual(app.root.after.call_count, 1)

    def test_card_toggle_changes_only_its_target_plugin(self):
        from macroflow.ui.app.plugins_page import PluginsPage
        app = self.app()
        with patch.object(runtime, 'save_app_settings'):
            first, second = runtime.add_interval_plugin(app), runtime.add_interval_plugin(app)
            page = object.__new__(PluginsPage)
            page.app, page.plugins, page.refresh_cards = app, app._interval_plugins, Mock()
            page.toggle_enabled(first, False)
        self.assertFalse(page.plugins[first].config['enabled'])
        self.assertTrue(page.plugins[second].config['enabled'])
        page.refresh_cards.assert_called_once_with()
