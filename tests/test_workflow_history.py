"""Workflow undo regressions using real edit methods and windowless controls."""
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.helpers.core import FakeVar
from macroflow.core.models import Workflow
from macroflow.ui.app.main import MacroFlowApp


class WorkflowHistoryTests(unittest.TestCase):
    def app(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[
            {'script': 'a.json', 'step_id': 'a', 'repeats': 1, 'enabled': True},
            {'script': 'b.json', 'step_id': 'b', 'repeats': 2, 'enabled': True},
        ])
        app.workflow_name_var = FakeVar(app.workflow.name)
        app.workflow_start_var = FakeVar('')
        app.workflow_start_delay_enabled_var = FakeVar(False)
        app.workflow_start_delay_seconds_var = FakeVar('5000')
        app.workflow_start_delay_seconds_var.unit = FakeVar('ms')
        app.workflow_insert_position_var = FakeVar('below')
        app.workflow_path = None
        app.workflow_draft_after_id = None
        app.workflow_tree = Mock()
        app.workflow_tree.selection.return_value = ('0',)
        app.global_tree = Mock()
        app.global_tree.selection.return_value = ()
        app.workflow_undo_button = Mock()
        app.global_undo_button = Mock()
        app.rebuild_workflow_tree = Mock()
        app._refresh_one_workflow_row = Mock()
        app._update_workflow_selection_color = Mock()
        app._toggle_workflow_start_delay_control = Mock()
        app._sync_workflow_resolution_ui = Mock()
        app._persist_sidebar_settings = Mock(side_effect=lambda: app._workflow_snapshot())
        app._notify = Mock()
        app._set_status = Mock()
        app.root = Mock()
        app.worker = None
        app._clear_workflow_history()
        return app

    def test_add_move_edit_and_delete_undo_in_reverse_operation_order(self):
        app = self.app()
        initial = copy.deepcopy(app.workflow)
        app._append_workflow_step(Path('c.json'))
        added = copy.deepcopy(app.workflow)
        app._move_selected_workflow_steps([0], 2)
        moved = copy.deepcopy(app.workflow)
        app.toggle_selected_workflow_step()
        toggled = copy.deepcopy(app.workflow)
        app.delete_workflow_step()
        for expected in (toggled, moved, added, initial):
            app.undo_workflow_edit()
            self.assertEqual(app.workflow, expected)
        app.workflow_undo_button.configure.assert_called_with(state='disabled')

    def test_batch_add_and_batch_delete_are_each_one_undo(self):
        app = self.app()
        initial = copy.deepcopy(app.workflow)
        app._insert_workflow_tasks_at([
            {'script': 'c.json', 'step_id': 'c'},
            {'script': 'd.json', 'step_id': 'd'},
        ], 1)
        app.undo_workflow_edit()
        self.assertEqual(app.workflow, initial)
        app.workflow_tree.selection.return_value = ('0', '1')
        app.delete_workflow_step()
        app.undo_workflow_edit()
        self.assertEqual(app.workflow, initial)

    def test_drag_is_one_undo_and_no_change_does_not_create_history(self):
        app = self.app()
        initial = copy.deepcopy(app.workflow)
        app._persist_workflow_draft()
        app._move_selected_workflow_steps([0], 0)
        app.undo_workflow_edit()
        self.assertEqual(app.workflow, initial)
        app.workflow_drag_index = 0
        app.workflow_tree.identify_row.side_effect = ('1', '0', '1')
        for _ in range(3):
            app._workflow_drag_motion(SimpleNamespace(y=20))
        app._workflow_drag_end(Mock())
        app.undo_workflow_edit()
        self.assertEqual(app.workflow, initial)
        self.assertFalse(app._workflow_history.undo)

    def test_global_steps_share_the_same_history_as_ordinary_steps(self):
        app = self.app()
        initial = copy.deepcopy(app.workflow)
        app._append_global_module(config={'plugin': 'test', 'nested': {'value': 1}})
        with_global = copy.deepcopy(app.workflow)
        app.global_tree.selection.return_value = ('0',)
        app.toggle_selected_global_module()
        app.undo_workflow_edit()
        self.assertEqual(app.workflow, with_global)
        app.undo_workflow_edit()
        self.assertEqual(app.workflow, initial)

    def test_pending_metadata_and_saved_changes_can_be_undone(self):
        app = self.app()
        app.workflow_name_var.set('新名称')
        app.undo_workflow_edit()
        self.assertEqual(app.workflow_name_var.get(), '未命名工作流')
        app.workflow.restart_default_row = 2
        app._persist_workflow_draft()
        app._workflow_saved()
        app.undo_workflow_edit()
        self.assertEqual(app.workflow.restart_default_row, 0)

    def test_pending_name_edit_and_following_add_are_separate_undo_steps(self):
        app = self.app()
        app.workflow_name_var.set('新名称')
        app.workflow_draft_after_id = 'pending-name-save'
        app._append_workflow_step(Path('c.json'))
        app.undo_workflow_edit()
        self.assertEqual(len(app.workflow.steps), 2)
        self.assertEqual(app.workflow_name_var.get(), '新名称')
        app.undo_workflow_edit()
        self.assertEqual(app.workflow_name_var.get(), '未命名工作流')
        app.root.after_cancel.assert_called_with('pending-name-save')

    def test_new_document_resets_history_and_running_execution_cannot_be_undone(self):
        app = self.app()
        app._append_workflow_step(Path('c.json'))
        app.worker = Mock()
        app.worker.is_alive.return_value = True
        current = copy.deepcopy(app.workflow)
        app.undo_workflow_edit()
        self.assertEqual(app.workflow, current)
        app.worker = None
        app._clear_workflow_history()
        app.undo_workflow_edit()
        self.assertEqual(app.workflow, current)

    def test_cell_edit_batch_options_and_restore_counts_are_undoable(self):
        for operation in ('cell', 'batch', 'restore_one', 'restore_all'):
            with self.subTest(operation=operation):
                app = self.app()
                app.workflow.steps[0]['original_repeats'] = 8
                app._clear_workflow_history()
                before = copy.deepcopy(app.workflow)
                if operation == 'cell':
                    app.workflow_tree.identify_row.return_value = '0'
                    app.workflow_tree.identify_column.return_value = '#7'
                    with patch('macroflow.ui.app.workflow.DurationDialog') as dialog:
                        dialog.return_value.show.return_value = 1234
                        app._edit_workflow_cell(SimpleNamespace(x=10, y=10))
                elif operation == 'batch':
                    with patch('macroflow.ui.app.workflow.WorkflowBatchSettingsDialog') as dialog:
                        dialog.return_value.show.return_value = {'repeats': 5, 'before_ms': 10}
                        app.set_all_workflow_step_options()
                elif operation == 'restore_one':
                    app.restore_workflow_step_count(0)
                else:
                    app.restore_all_workflow_counts()
                self.assertNotEqual(app.workflow, before)
                app.undo_workflow_edit()
                self.assertEqual(app.workflow, before)

    def test_automatic_repeat_updates_reset_history_after_worker_has_finished(self):
        app = self.app()
        app._append_workflow_step(Path('c.json'))
        app.workflow.steps[0]['repeats'] = 0
        app._refresh_workflow_repeat_ui(0)
        after_execution = copy.deepcopy(app.workflow)
        app.undo_workflow_edit()
        self.assertEqual(app.workflow, after_execution)
        self.assertFalse(app._workflow_history.undo)

    def test_history_is_bounded_and_nested_snapshots_do_not_alias(self):
        from macroflow.ui.app.workflow_history import WorkflowEditHistory
        state = {'nested': {'values': [0]}}
        history = WorkflowEditHistory(state)
        for value in range(1, 111):
            state['nested']['values'][0] = value
            history.record(state)
        self.assertEqual(len(history.undo), 100)
        restored = history.step()
        self.assertEqual(restored['nested']['values'], [109])
        restored['nested']['values'][0] = -1
        self.assertEqual(history.current['nested']['values'], [109])


if __name__ == '__main__':
    unittest.main()
