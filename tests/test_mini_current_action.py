import unittest
from unittest.mock import Mock

from macroflow.execution.player import MacroPlayer
from macroflow.ui.app.main import MacroFlowApp


class MiniCurrentActionTests(unittest.TestCase):
    def test_start_is_reported_before_row_delay_and_execution(self):
        player = MacroPlayer()
        events = []
        player._poll_guards = Mock()
        player._active_script_name = "demo"
        player.on_action_start = lambda event: events.append(("start", event))
        player._wait = lambda ms: events.append(("wait", ms))
        player._execute_action = lambda *args: events.append(("execute", args[0]))
        action = {"type": "wait", "delay_ms": 5000}
        player._run_action_sequence([action], None)
        self.assertEqual([entry[0] for entry in events[:3]], ["start", "wait", "execute"])
        self.assertEqual(events[0][1], dict(script="demo", index=0, total=1, depth=0, action=action))

    def test_nested_rows_keep_their_actual_script_and_index(self):
        player = MacroPlayer()
        player._poll_guards = Mock()
        player._wait = Mock()
        player._execute_action = Mock(return_value=None)
        player._active_script_name = "child"
        player.on_action_start = Mock()
        player._run_action_sequence([{"type": "wait"}, {"type": "wait"}], None, start_index=1, depth=1)
        event = player.on_action_start.call_args.args[0]
        self.assertEqual((event["script"], event["index"], event["total"], event["depth"]), ("child", 1, 2, 1))

    def test_current_action_keeps_filename_and_close_parameters(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._ui = lambda callback, *args: callback(*args)
        app.mini_action_var = Mock()
        app._on_action_start(dict(script="启动游戏", index=2, total=8,
                                 action=dict(type="close_app", name="FlClash.exe")))
        text = app.mini_action_var.set.call_args.args[0]
        self.assertIn("启动游戏 · 第 3/8 行", text)
        self.assertIn("关闭软件", text)
        self.assertIn("FlClash.exe", text)
        self.assertEqual(app.current_execution_action_text, text)

    def test_progress_updates_do_not_replace_current_action(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.mini_mode = "execution"
        app.mini_count_var = Mock()
        app.mini_action_var = Mock()
        app._set_current_execution_action("当前脚本 · 第 2/5 行\n关闭 FlClash.exe")
        app.mini_action_var.reset_mock()
        app._set_execution_progress("工作流 1/3")
        app.mini_action_var.set.assert_not_called()
        self.assertIn("FlClash.exe", app.current_execution_action_text)
