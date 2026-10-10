"""Background-only checks; never kill a real process or create a window."""
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psutil
from macroflow.execution.disconnect_monitor import DisconnectMonitor


def connection(status=psutil.CONN_ESTABLISHED, ip="203.0.113.1", port=443):
    return SimpleNamespace(status=status, raddr=SimpleNamespace(ip=ip, port=port))


class DisconnectMonitorTests(unittest.TestCase):
    def setUp(self):
        self.process = Mock(pid=42)
        self.process.info = {"name": "SSJJ_BattleClient_Unity.exe", "create_time": 100}
        self.process.net_connections.return_value = [connection()]
        self.log = Mock()
        self.monitor = DisconnectMonitor(self.log)
        self.processes = patch("macroflow.execution.disconnect_monitor.psutil.process_iter",
                               return_value=[self.process])
        self.processes.start()
        self.addCleanup(self.processes.stop)

    def scan(self, now=121):
        with patch("macroflow.execution.disconnect_monitor.time.time", return_value=now):
            self.monitor.check_once(threading.Event())

    def test_only_exact_target_name_after_twenty_seconds(self):
        for name, now in [("other.exe", 121), ("SSJJ_BattleClient_Unity.exe", 120)]:
            with self.subTest(name=name, now=now):
                self.process.info["name"] = name
                self.scan(now)
                self.process.net_connections.assert_not_called()
                self.process.kill.assert_not_called()

    def test_established_connection_is_kept(self):
        self.scan()
        self.process.net_connections.assert_called_once_with(kind="tcp4")
        self.process.kill.assert_not_called()

    def test_process_name_is_case_insensitive(self):
        self.process.info["name"] = "ssjj_battleclient_unity.EXE"
        self.process.net_connections.return_value = []
        self.scan()
        self.process.kill.assert_called_once_with()

    def test_no_connections_kills_target(self):
        self.process.net_connections.return_value = []
        self.scan()
        self.process.kill.assert_called_once_with()
        self.assertIn("42", self.log.call_args.args[0])

    def test_any_abnormal_connection_kills_even_with_established_connection(self):
        for state in (psutil.CONN_CLOSE, psutil.CONN_CLOSE_WAIT, psutil.CONN_SYN_SENT,
                      psutil.CONN_TIME_WAIT, psutil.CONN_LISTEN):
            with self.subTest(state=state):
                self.process.kill.reset_mock()
                self.process.net_connections.return_value = [connection(), connection(state)]
                self.scan()
                self.process.kill.assert_called_once_with()

    def test_loopback_and_remote_port_eighty_are_excluded(self):
        self.process.net_connections.return_value = [
            connection(), connection(psutil.CONN_CLOSE_WAIT, ip="127.0.0.1"),
            connection(psutil.CONN_CLOSE_WAIT, port=80),
        ]
        self.scan()
        self.process.kill.assert_not_called()
        self.process.net_connections.return_value = [connection(ip="127.0.0.1")]
        self.scan()
        self.process.kill.assert_called_once_with()

    def test_listener_without_remote_address_is_abnormal(self):
        self.process.net_connections.return_value = [
            SimpleNamespace(status=psutil.CONN_LISTEN, raddr=())]
        self.scan()
        self.process.kill.assert_called_once_with()

    def test_access_denied_never_counts_as_disconnection(self):
        self.process.net_connections.side_effect = psutil.AccessDenied(42)
        self.scan()
        self.process.kill.assert_not_called()
        self.log.assert_called_once()

    def test_vanished_process_is_ignored(self):
        self.process.net_connections.side_effect = psutil.NoSuchProcess(42)
        self.scan()
        self.process.kill.assert_not_called()
        self.log.assert_not_called()

    def test_disable_during_scan_prevents_kill(self):
        stop = threading.Event()
        def disconnect(**kwargs):
            stop.set()
            return []
        self.process.net_connections.side_effect = disconnect
        with patch("macroflow.execution.disconnect_monitor.time.time", return_value=121):
            self.monitor.check_once(stop)
        self.process.kill.assert_not_called()

    def test_worker_waits_five_seconds_and_stop_wakes_wait(self):
        stop = Mock()
        stop.wait.side_effect = [False, True]
        with patch.object(self.monitor, "check_once") as check:
            self.monitor._run(stop)
        self.assertEqual([c.args for c in stop.wait.call_args_list], [(5,), (5,)])
        check.assert_called_once_with(stop)

    def test_reenable_uses_new_event_without_reviving_old_worker(self):
        with patch("macroflow.execution.disconnect_monitor.threading.Thread") as thread:
            self.monitor.start()
            old_stop = thread.call_args.kwargs["args"][0]
            self.monitor.start()
            self.assertEqual(thread.call_count, 1)
            self.monitor.stop()
            self.assertTrue(old_stop.is_set())
            self.monitor.start()
            new_stop = thread.call_args.kwargs["args"][0]
            self.assertIsNot(old_stop, new_stop)
            self.assertTrue(old_stop.is_set())
            self.assertFalse(new_stop.is_set())
            self.monitor.stop()
            self.assertTrue(new_stop.is_set())


class DisconnectMonitorControlTests(unittest.TestCase):
    def test_switch_is_opt_in_and_root_destruction_stops_worker(self):
        from macroflow.ui.app import disconnect_monitor as ui
        app, parent = Mock(), Mock()
        variable = Mock()
        variable.get.return_value = False
        with patch.object(ui.tk, "BooleanVar", return_value=variable) as var, \
                patch.object(ui.ttk, "Checkbutton") as button, \
                patch.object(ui, "Tooltip"), \
                patch.object(ui, "DisconnectMonitor") as factory:
            ui.add_disconnect_monitor(parent, app)
        monitor = factory.return_value
        var.assert_called_once_with(master=app.root, value=False)
        monitor.start.assert_not_called()
        toggle = button.call_args.kwargs["command"]
        variable.get.return_value = True
        toggle()
        monitor.start.assert_called_once_with()
        variable.get.return_value = False
        toggle()
        monitor.stop.assert_called_once_with()
        destroyed = app.root.bind.call_args.args[1]
        destroyed(SimpleNamespace(widget=parent))
        self.assertEqual(monitor.stop.call_count, 1)
        destroyed(SimpleNamespace(widget=app.root))
        self.assertEqual(monitor.stop.call_count, 2)
        factory.call_args.args[0]("test log")
        app._ui.assert_called_once_with(app._log, "test log")


if __name__ == "__main__":
    unittest.main()
