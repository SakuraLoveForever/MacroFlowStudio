import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from macroflow.core.models import Workflow
from macroflow.ui.app.execution import ExecutionMixin


class ForegroundGuardTests(unittest.TestCase):
    def test_process_list_is_normalized_and_saved(self):
        workflow = Workflow.from_dict({"foreground_minimize_processes": [
            r'C:\Apps\Chrome.EXE', ' chrome.exe ', '', None, 'msedge.exe']})
        self.assertEqual(workflow.foreground_minimize_processes, ['chrome.exe', 'msedge.exe'])
        self.assertEqual(Workflow.from_dict(workflow.to_dict()), workflow)

    def test_invalid_list_is_disabled(self):
        self.assertEqual(Workflow.from_dict({'foreground_minimize_processes': 'chrome.exe'})
                         .foreground_minimize_processes, [])

    def test_only_matching_foreground_is_minimized_asynchronously(self):
        from macroflow.input import foreground_guard as guard
        info = SimpleNamespace(hwnd=11, process_path=r'C:\Apps\Chrome.EXE')
        with patch.object(guard, 'get_foreground_window_info', return_value=info), \
                patch.object(guard, 'is_current_process_window', return_value=False), \
                patch.object(guard, 'is_window_process_foreground', return_value=False), \
                patch.object(guard.win32gui, 'GetForegroundWindow', return_value=11), \
                patch.object(guard.win32gui, 'PostMessage') as post:
            self.assertEqual(guard.minimize_listed_foreground(['chrome.exe'], 99), 'chrome.exe')
            post.assert_called_once_with(11, guard.win32con.WM_SYSCOMMAND,
                                         guard.win32con.SC_MINIMIZE, 0)
            post.reset_mock()
            self.assertEqual(guard.minimize_listed_foreground(['msedge.exe'], 99), '')
            post.assert_not_called()

    def test_protected_windows_and_foreground_race_are_ignored(self):
        from macroflow.input import foreground_guard as guard
        info = SimpleNamespace(hwnd=11, process_path=r'C:\Apps\chrome.exe')
        for own, game, foreground in [(True, False, 11), (False, True, 11),
                                      (False, False, 12)]:
            with self.subTest(own=own, game=game, foreground=foreground), \
                    patch.object(guard, 'get_foreground_window_info', return_value=info), \
                    patch.object(guard, 'is_current_process_window', return_value=own), \
                    patch.object(guard, 'is_window_process_foreground', return_value=game), \
                    patch.object(guard.win32gui, 'GetForegroundWindow', return_value=foreground), \
                    patch.object(guard.win32gui, 'PostMessage') as post:
                self.assertEqual(guard.minimize_listed_foreground(['chrome.exe'], 99), '')
                post.assert_not_called()

    def app(self):
        app = ExecutionMixin()
        app.root = Mock()
        app.root.after.return_value = 'timer'
        app.worker = Mock()
        app.worker.is_alive.return_value = True
        app.workflow_stop = threading.Event()
        app.player = SimpleNamespace(paused=False, stop_event=threading.Event())
        app._bound_hwnd = Mock(return_value=99)
        app._log = Mock()
        return app

    def test_watch_runs_without_execution_mini_window(self):
        app = self.app()
        with patch('macroflow.ui.app.execution.minimize_listed_foreground', return_value='chrome.exe'), \
                patch('macroflow.ui.app.execution.activate_window') as activate:
            app._start_foreground_minimize_watch(['chrome.exe'])
            activate.assert_called_once_with(99)
            app.root.after.assert_called_once_with(500, app._poll_foreground_minimize_watch)
            app._log.assert_called_once()

    def test_pause_does_not_minimize_and_stop_cancels_timer(self):
        app = self.app()
        app.player.paused = True
        with patch('macroflow.ui.app.execution.minimize_listed_foreground') as minimize:
            app._start_foreground_minimize_watch(['chrome.exe'])
            minimize.assert_not_called()
            app._stop_foreground_minimize_watch()
            app.root.after_cancel.assert_called_once_with('timer')
            self.assertEqual(app._foreground_minimize_names, [])

    def test_finished_or_stopped_workflow_does_not_reschedule(self):
        for mode in ('finished', 'workflow_stop', 'player_stop'):
            app = self.app()
            if mode == 'finished':
                app.worker.is_alive.return_value = False
            elif mode == 'workflow_stop':
                app.workflow_stop.set()
            else:
                app.player.stop_event.set()
            with self.subTest(mode=mode), \
                    patch('macroflow.ui.app.execution.minimize_listed_foreground') as minimize:
                app._start_foreground_minimize_watch(['chrome.exe'])
                minimize.assert_not_called()
                app.root.after.assert_not_called()

    def test_worker_always_stops_watch_and_forwards_arguments(self):
        class Worker:
            def _run_workflow_worker(self, *args, **kwargs):
                self.forwarded = (args, kwargs)
                raise RuntimeError('worker failed')

        class App(ExecutionMixin, Worker):
            pass

        app = App()
        app.workflow = SimpleNamespace(foreground_minimize_processes=['chrome.exe'],
                                       foreground_minimize_interval_ms=750)
        app._ui = Mock()
        with self.assertRaisesRegex(RuntimeError, 'worker failed'):
            app._run_workflow_worker('steps', start_index=3)
        self.assertEqual(app.forwarded, (('steps',), {'start_index': 3}))
        self.assertEqual(app._ui.call_args_list[0].args,
                         (app._start_foreground_minimize_watch, ['chrome.exe'], 750))
        self.assertEqual(app._ui.call_args_list[-1].args, (app._stop_foreground_minimize_watch,))

    def test_worker_without_workflow_runs_without_watch(self):
        class Worker:
            def _run_workflow_worker(self):
                return 'done'

        class App(ExecutionMixin, Worker):
            pass

        app = App()
        app._ui = Mock()
        self.assertEqual(app._run_workflow_worker(), 'done')
        app._ui.assert_not_called()

    def test_disappeared_or_inaccessible_window_is_nonfatal(self):
        from macroflow.input import foreground_guard as guard
        info = SimpleNamespace(hwnd=11, process_path=r'C:\Apps\chrome.exe')
        with patch.object(guard, 'get_foreground_window_info', return_value=info), \
                patch.object(guard, 'is_current_process_window', return_value=False), \
                patch.object(guard, 'is_window_process_foreground', return_value=False), \
                patch.object(guard.win32gui, 'GetForegroundWindow', return_value=11), \
                patch.object(guard.win32gui, 'PostMessage', side_effect=guard.pywintypes.error(5, 'PostMessage', 'denied')):
            self.assertEqual(guard.minimize_listed_foreground(['chrome.exe'], None), '')

    def test_dialog_save_and_cancel_update_only_workflow_configuration(self):
        from macroflow.ui.app.workflow import WorkflowMixin
        app = WorkflowMixin()
        app.root = Mock()
        app.workflow = Workflow()
        app._schedule_workflow_draft_save = Mock()
        with patch('macroflow.ui.app.workflow.ForegroundProcessesDialog') as dialog:
            dialog.return_value.show.return_value = (['chrome.exe'], 1250)
            app.edit_foreground_minimize_processes()
            self.assertEqual(app.workflow.foreground_minimize_processes, ['chrome.exe'])
            self.assertEqual(app.workflow.foreground_minimize_interval_ms, 1250)
            app._schedule_workflow_draft_save.assert_called_once()
            dialog.return_value.show.return_value = None
            app.edit_foreground_minimize_processes()
            self.assertEqual(app.workflow.foreground_minimize_processes, ['chrome.exe'])
            self.assertEqual(app.workflow.foreground_minimize_interval_ms, 1250)
            app._schedule_workflow_draft_save.assert_called_once()

    def test_interval_defaults_to_half_second_and_roundtrips(self):
        self.assertEqual(Workflow().foreground_minimize_interval_ms, 500)
        workflow = Workflow.from_dict({'foreground_minimize_interval_ms': 1250})
        self.assertEqual(workflow.foreground_minimize_interval_ms, 1250)
        self.assertEqual(Workflow.from_dict(workflow.to_dict()), workflow)
        for invalid in (None, 'bad', 0, -100, 99, 60001, float('inf')):
            with self.subTest(invalid=invalid):
                self.assertEqual(Workflow.from_dict({'foreground_minimize_interval_ms': invalid})
                                 .foreground_minimize_interval_ms, 500)

    def test_watch_uses_configured_interval_after_pause_and_resume(self):
        app = self.app()
        app.player.paused = True
        with patch('macroflow.ui.app.execution.minimize_listed_foreground', return_value='') as minimize:
            app._start_foreground_minimize_watch(['chrome.exe'], 1250)
            app.root.after.assert_called_with(1250, app._poll_foreground_minimize_watch)
            minimize.assert_not_called()
            app.player.paused = False
            app._poll_foreground_minimize_watch()
            minimize.assert_called_once()
            app.root.after.assert_called_with(1250, app._poll_foreground_minimize_watch)

    def test_interval_dialog_rejects_invalid_input_without_saving(self):
        from macroflow.ui.dialogs.foreground_processes import ForegroundProcessesDialog
        for value in ('bad', '0', '99', '60001'):
            dialog = SimpleNamespace(interval=Mock(), processes=Mock(), destroy=Mock(), result=None)
            dialog.interval.get.return_value = value
            with self.subTest(value=value), \
                    patch('macroflow.ui.dialogs.foreground_processes.show_floating_notice') as notice:
                ForegroundProcessesDialog._save(dialog)
                notice.assert_called_once()
                dialog.destroy.assert_not_called()
                self.assertIsNone(dialog.result)

    def test_interval_dialog_saves_names_and_half_second(self):
        from macroflow.ui.dialogs.foreground_processes import ForegroundProcessesDialog
        dialog = SimpleNamespace(interval=Mock(), processes=Mock(), destroy=Mock(), result=None)
        dialog.interval.get.return_value = '500'
        dialog.processes.get.return_value = ('chrome.exe',)
        ForegroundProcessesDialog._save(dialog)
        self.assertEqual(dialog.result, (['chrome.exe'], 500))
        dialog.destroy.assert_called_once()
