import unittest
from unittest.mock import Mock, patch

from macroflow.execution.player import MacroPlayer
from macroflow.execution.player.base import taskkill_process
from tests.helpers.patches import package_patch


class CloseDeadlineTests(unittest.TestCase):
    def test_normal_close_command_has_three_second_timeout(self):
        with patch("macroflow.execution.player.base.subprocess.run", return_value=Mock(returncode=0, stderr="")) as run:
            taskkill_process("demo.exe")
        self.assertEqual(run.call_args.kwargs["timeout"], 3)

    def test_command_runtime_consumes_normal_exit_budget(self):
        player = MacroPlayer()
        player._wait = Mock()
        with package_patch("player", "is_process_running", side_effect=[True, True, True, True, False]), \
             package_patch("player", "taskkill_process", return_value=(0, "")) as kill, \
             patch("macroflow.execution.player.apps.time.perf_counter", side_effect=[100, 103, 103, 103]):
            player._execute_close_app(dict(name="demo.exe", graceful_wait_ms=60000))
        self.assertEqual([c.kwargs["force"] for c in kill.call_args_list], [False, True])
        player._wait.assert_not_called()

    def test_related_wait_logs_the_pending_process_and_independent_limit(self):
        messages = []
        player = MacroPlayer(on_log=messages.append)
        with package_patch("player", "is_process_running", side_effect=[False, True]):
            with self.assertRaisesRegex(RuntimeError, "关联进程尚未退出"):
                player._execute_close_app(dict(name="demo.exe", wait_for_processes=["child.exe"],
                                               wait_for_processes_timeout_ms=0))
        self.assertTrue(any("等待关联进程退出：child.exe" in message for message in messages))
