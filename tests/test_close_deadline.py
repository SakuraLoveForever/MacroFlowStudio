import unittest
from unittest.mock import Mock, patch

from macroflow.execution.player import MacroPlayer, PlaybackStopped
from macroflow.execution.process_close import close_targets, kill_targets


def process(pid, name="demo.exe"):
    result = Mock(pid=pid, info={"name": name})
    result.alive = True
    result.is_running.side_effect = lambda: result.alive
    result.children.return_value = []
    return result


class CloseGroupTests(unittest.TestCase):
    def setup_group(self, targets):
        player = MacroPlayer()
        clock = [0.0]
        player._wait = Mock(side_effect=lambda ms: clock.__setitem__(0, clock[0] + ms / 1000))
        return player, clock

    def test_snapshot_includes_all_descendants_and_related_roots_once(self):
        parent, child, grandchild, related, other = [process(n, name) for n, name in
            [(1, "Demo.exe"), (2, "child.exe"), (3, "leaf.exe"), (4, "guard.des"), (5, "other.exe")]]
        parent.children.return_value = [child, grandchild]
        related.children.return_value = [grandchild]
        with patch("macroflow.execution.process_close.psutil.process_iter", return_value=[parent, related, other]):
            targets = close_targets(["demo.exe", "guard.des"])
        self.assertEqual({p.pid for p in targets}, {1, 2, 3, 4})
        self.assertEqual(len(targets), 4)
        parent.children.assert_called_once_with(recursive=True)

    def test_entire_group_shares_three_seconds_and_one_force_command(self):
        targets = [process(n) for n in range(1, 5)]
        player, clock = self.setup_group(targets)
        def kill(items, **kwargs):
            if kwargs.get("force"):
                for p in items: p.alive = False
            else:
                clock[0] += 3
            return 0, ""
        with patch("macroflow.execution.player.apps.close_targets", return_value=targets) as collect, \
             patch("macroflow.execution.player.apps.kill_targets", side_effect=kill) as kill_mock, \
             patch("macroflow.execution.player.apps.time.perf_counter", side_effect=lambda: clock[0]):
            player._execute_close_app(dict(name="demo.exe", wait_for_processes=["guard.des"],
                                           tree=False, wait_for_processes_timeout_ms=60000))
        collect.assert_called_once_with(["demo.exe", "guard.des"])
        self.assertEqual(clock[0], 3)
        self.assertEqual(kill_mock.call_count, 2)
        self.assertEqual(kill_mock.call_args_list[1].args[0], targets)
        self.assertTrue(kill_mock.call_args.kwargs["force"])
        player._wait.assert_not_called()

    def test_child_survives_parent_exit_and_is_still_forced(self):
        parent, child = process(1), process(2, "child.exe")
        player, clock = self.setup_group([parent, child])
        def kill(items, **kwargs):
            if kwargs.get("force"):
                self.assertEqual(items, [child])
                child.alive = False
            else:
                parent.alive = False
                clock[0] = 3
            return 0, ""
        with patch("macroflow.execution.player.apps.close_targets", return_value=[parent, child]), \
             patch("macroflow.execution.player.apps.kill_targets", side_effect=kill) as command, \
             patch("macroflow.execution.player.apps.time.perf_counter", side_effect=lambda: clock[0]):
            player._execute_close_app(dict(name="demo.exe"))
        self.assertEqual(command.call_count, 2)

    def test_all_exit_normally_without_force(self):
        targets = [process(1), process(2)]
        player, clock = self.setup_group(targets)
        def close(items, **kwargs):
            for p in items: p.alive = False
            return 0, ""
        with patch("macroflow.execution.player.apps.close_targets", return_value=targets), \
             patch("macroflow.execution.player.apps.kill_targets", side_effect=close) as command:
            player._execute_close_app(dict(name="demo.exe", graceful=False))
        command.assert_called_once_with(targets, timeout=3.0)

    def test_absent_parent_does_not_skip_related_targets(self):
        target = process(7, "guard.des")
        with patch("macroflow.execution.process_close.psutil.process_iter", return_value=[target]):
            self.assertEqual(close_targets(["missing.exe", "guard.des"]), [target])

    def test_empty_group_skips_without_commands(self):
        with patch("macroflow.execution.player.apps.close_targets", return_value=[]), \
             patch("macroflow.execution.player.apps.kill_targets") as command:
            MacroPlayer()._execute_close_app(dict(name="missing.exe"))
        command.assert_not_called()

    def test_invalid_related_names_fail_before_closing_anything(self):
        with patch("macroflow.execution.player.apps.close_targets") as collect:
            with self.assertRaisesRegex(RuntimeError, "一同关闭"):
                MacroPlayer()._execute_close_app(dict(name="demo.exe", wait_for_processes=[""]))
        collect.assert_not_called()

    def test_stop_during_normal_close_prevents_force(self):
        player = MacroPlayer()
        with patch("macroflow.execution.player.apps.close_targets", return_value=[process(1)]), \
             patch("macroflow.execution.player.apps.kill_targets", side_effect=lambda *a, **k: (player.stop_event.set() or (0, ""))) as command:
            with self.assertRaises(PlaybackStopped):
                player._execute_close_app(dict(name="demo.exe"))
        self.assertEqual(command.call_count, 1)

    def test_permission_failure_reports_remaining_processes(self):
        target = process(10)
        player, clock = self.setup_group([target])
        with patch("macroflow.execution.player.apps.close_targets", return_value=[target]), \
             patch("macroflow.execution.player.apps.kill_targets", return_value=(1, "拒绝访问")), \
             patch("macroflow.execution.player.apps.time.perf_counter", side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(RuntimeError, "10.*拒绝访问"):
                player._execute_close_app(dict(name="demo.exe"))

    def test_admin_retry_targets_the_same_remaining_group(self):
        target = process(10)
        player, clock = self.setup_group([target])
        def kill(items, **kwargs):
            if kwargs.get("elevated"):
                target.alive = False
                return 0, ""
            return 1, "拒绝访问"
        with patch("macroflow.execution.player.apps.close_targets", return_value=[target]), \
             patch("macroflow.execution.player.apps.kill_targets", side_effect=kill) as command, \
             patch("macroflow.execution.player.apps.time.perf_counter", side_effect=lambda: clock[0]):
            player._execute_close_app(dict(name="demo.exe", elevated_retry=True))
        command.assert_called_with([target], force=True, elevated=True)

    def test_native_command_sends_all_pids_once_with_tree_and_timeout(self):
        with patch("macroflow.execution.process_close.subprocess.run", return_value=Mock(returncode=0, stderr="")) as run:
            kill_targets([process(11), process(12)])
        self.assertEqual(run.call_args.args[0], ["taskkill", "/T", "/PID", "11", "/PID", "12"])
        self.assertEqual(run.call_args.kwargs["timeout"], 3)

    def test_reused_or_exited_pid_is_not_sent_to_taskkill(self):
        gone = process(11)
        gone.alive = False
        with patch("macroflow.execution.process_close.subprocess.run") as run:
            self.assertEqual(kill_targets([gone], force=True), (0, ""))
        run.assert_not_called()
