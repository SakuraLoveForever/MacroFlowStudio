"""录制步骤与坐标路线的模块化编辑器：一行一个模块，双击改其中一项。"""
import unittest
from unittest.mock import Mock, call, patch

from tests.helpers.core import FakeVar, add_src_to_path

from macroflow.execution.ssjj_recording import mark_offsets
from macroflow.ui.dialogs.recorded_steps import (
    DelayInsertDialog, RecordedStepDialog, RouteActionDialog, input_row_label,
    mark_row_label, step_offset,
)


def step_dialog(kind: str, fields: dict, source: dict | None = None):
    dialog = RecordedStepDialog.__new__(RecordedStepDialog)
    dialog._source = dict(source or {'type': kind})
    dialog.result = None
    dialog._duration_key = ("pulse_duration_ms" if "pulse_duration_ms" in dialog._source
                            else "duration_ms")
    dialog.fields = {name: FakeVar({'按下': '↓', '松开': '↑'}.get(value, value)
                                  if name == 'down' else value) for name, value in fields.items()}
    dialog.destroy = Mock()
    return dialog


def route_action() -> dict:
    from tests.test_ssjj_waypoint_playback import recording
    return recording()


def route_dialog(action: dict | None = None, marks=None, inputs=None):
    action = action or route_action()
    dialog = RouteActionDialog.__new__(RouteActionDialog)
    dialog._source = dict(action)
    dialog.marks = [dict(mark) for mark in (action['marks'] if marks is None else marks)]
    dialog.inputs = [dict(step) for step in (action['inputs'] if inputs is None else inputs)]
    offsets = mark_offsets(dialog.marks)
    dialog.input_rows = sorted(
        [('mark', i) for i in range(len(dialog.marks))] + [('input', i) for i in range(len(dialog.inputs))],
        key=lambda row: (offsets[row[1]] if row[0] == 'mark'
                         else step_offset(dialog.inputs[row[1]]), row[0] != 'mark'))
    dialog.trajectory = [dict(sample) for sample in action['trajectory']]
    dialog.duration = float(action['duration_ms'])
    dialog.marks_list = Mock()
    dialog.marks_list.curselection.return_value = ()
    dialog.inputs_list = Mock()
    dialog.inputs_list.curselection.return_value = ()
    dialog._init_history()
    dialog.result = None
    dialog.destroy = Mock()
    return dialog


class RecordedStepDialogTests(unittest.TestCase):
    def test_recorded_steps_use_the_modular_editor_and_palette_actions_do_not(self):
        self.assertTrue(RecordedStepDialog.handles({'type': 'key', 'vk': 87, 'recorded_at_ms': 0}))
        self.assertTrue(RecordedStepDialog.handles({'type': 'turn', 'dx': 1, 'recorded_at_ms': 5}))
        self.assertFalse(RecordedStepDialog.handles({'type': 'key_press', 'vk': 87}))
        self.assertFalse(RecordedStepDialog.handles({'type': 'scroll', 'direction': 'up', 'clicks': 3}))
        self.assertFalse(RecordedStepDialog.handles({'type': 'ssjj_recording'}))

    def test_key_step_saves_the_letter_back_to_a_virtual_key_code(self):
        dialog = step_dialog('key', {'key': 'E', 'down': '按下'},
                             {'type': 'key', 'vk': 87, 'name': 'w', 'down': False,
                              'recorded_at_ms': 120})
        dialog.save()
        self.assertEqual(dialog.result, {'type': 'key', 'vk': 69, 'name': 'E', 'down': True,
                                        'recorded_at_ms': 120})
        dialog.destroy.assert_called_once()

    def test_mouse_button_step_keeps_state_and_position(self):
        dialog = step_dialog('mouse_button',
                             {'button': '鼠标右键', 'down': '松开', 'x': '10', 'y': '20'},
                             {'type': 'mouse_button', 'button': 'left', 'down': True, 'x': 1, 'y': 2,
                              'recorded_at_ms': 30})
        dialog.save()
        self.assertEqual(dialog.result['button'], 'right')
        self.assertFalse(dialog.result['down'])
        self.assertEqual((dialog.result['x'], dialog.result['y']), (10, 20))
        self.assertEqual(dialog.result['recorded_at_ms'], 30)

    def test_recorded_turn_keeps_its_pulse_duration_key(self):
        dialog = step_dialog('turn', {'dx': '-30', 'dy': '4', 'pulse_duration_ms': '25'},
                             {'type': 'turn', 'dx': 0, 'dy': 0, 'pulse_duration_ms': 10,
                              'pulse_started_at_ms': 5, 'recorded_at_ms': 5})
        dialog.save()
        self.assertEqual(dialog.result['pulse_duration_ms'], 25)
        self.assertEqual(dialog.result['pulse_started_at_ms'], 5)
        self.assertNotIn('duration_ms', dialog.result)

    def test_records_the_time_field_when_the_step_has_one(self):
        dialog = step_dialog('key', {'key': 'E', 'down': '按下', 'recorded_at_ms': '250'},
                             {'type': 'key', 'vk': 87, 'name': 'w', 'down': True,
                              'recorded_at_ms': 120, 'delay_ms': 30})
        dialog.save()
        self.assertEqual(dialog.result['recorded_at_ms'], 250.0)
        self.assertEqual(dialog.result['delay_ms'], 30)

    def test_bad_key_name_is_reported_and_keeps_the_dialog_open(self):
        dialog = step_dialog('key', {'key': '没有这个键', 'down': '按下'})
        with patch('macroflow.ui.dialogs.recorded_steps.show_floating_notice') as notice:
            dialog.save()
        notice.assert_called_once()
        dialog.destroy.assert_not_called()
        self.assertIsNone(dialog.result)


class RouteActionDialogTests(unittest.TestCase):
    def test_every_mark_and_input_gets_its_own_row(self):
        action = route_action()
        self.assertEqual(mark_row_label(0, action['marks'][0]),
                         '1. 纠正点 · 起点 · (0, 0, 0) · 朝向 0.0° / 俯仰 0.0°')
        self.assertEqual(mark_row_label(1, action['marks'][1]),
                         '2. 纠正点 · 距上一点 1000 ms · (600, 0, 0) · 朝向 90.0° / 俯仰 0.0°')
        self.assertIn('朝向 180.0°', mark_row_label(0, {'gap_ms': 0, 'position': [1, 2, 3],
                                                        'yaw': 180, 'pitch': -3.3}))
        self.assertEqual(input_row_label(0, action['inputs'][0]), '1. 键盘按下：W · 0 ms')
        self.assertIn('游戏转向：ΔX=5，ΔY=0',
                      input_row_label(1, dict(action['inputs'][1], mode='relative')))
        self.assertEqual(input_row_label(2, action['inputs'][2]), '3. 键盘松开：W · 900 ms')
        self.assertEqual(input_row_label(4, action['inputs'][4]), '5. 左键按下：(0, 0) · 1150 ms')
        self.assertIn('SPACE', input_row_label(0, {'type': 'key', 'vk': 0x20, 'down': True,
                                                   'recorded_at_ms': 5}))

    def test_saving_keeps_the_trajectory_and_the_mark_gaps(self):
        dialog = route_dialog()
        dialog.save()
        self.assertEqual([mark['gap_ms'] for mark in dialog.result['marks']], [0, 1000])
        self.assertEqual(len(dialog.result['trajectory']), 4)
        self.assertEqual(dialog.result['map_id'], 100001)
        self.assertEqual(len(dialog.result['inputs']), 6)
        dialog.destroy.assert_called_once()

    def test_a_route_whose_first_mark_is_not_the_start_is_rejected(self):
        # 顺序改过以后第一个点带着非零间隔：它不再是起点，路线没法从原地开始对齐。
        dialog = route_dialog()
        dialog.marks.reverse()
        with patch('macroflow.ui.dialogs.recorded_steps.show_floating_notice') as notice:
            dialog.save()
        notice.assert_called_once()
        self.assertIsNone(dialog.result)
        dialog.destroy.assert_not_called()

    def test_editing_one_input_row_only_changes_that_row(self):
        dialog = route_dialog()
        dialog.inputs[2] = dict(dialog.inputs[2], vk=69, name='e')
        dialog.save()
        self.assertEqual(dialog.result['inputs'][2]['name'], 'e')
        self.assertEqual(dialog.result['inputs'][0]['vk'], 87)
        self.assertEqual(dialog.result['inputs'][3]['vk'], 69)

    def test_removing_every_mark_is_rejected(self):
        dialog = route_dialog(marks=[])
        with patch('macroflow.ui.dialogs.recorded_steps.show_floating_notice') as notice:
            dialog.save()
        notice.assert_called_once()
        self.assertIsNone(dialog.result)
        dialog.destroy.assert_not_called()

    def test_a_route_that_no_longer_starts_at_the_first_mark_is_rejected(self):
        marks = [{'gap_ms': 100, 'position': [0, 0, 0], 'yaw': 0, 'pitch': 0},
                 {'gap_ms': 1100, 'position': [10, 0, 0], 'yaw': 90, 'pitch': 0}]
        dialog = route_dialog(marks=marks)
        with patch('macroflow.ui.dialogs.recorded_steps.show_floating_notice') as notice:
            dialog.save()
        notice.assert_called_once()
        self.assertIsNone(dialog.result)

    def test_inserting_a_key_step_places_it_on_the_timeline(self):
        dialog = route_dialog()
        dialog.inputs_list = Mock()
        dialog.inputs_list.curselection.return_value = (dialog.input_rows.index(('input', 1)),)
        dialog.reload = Mock()
        with patch('macroflow.ui.dialogs.recorded_steps.RecordedStepDialog') as step_dialog:
            step_dialog.return_value.show.return_value = {
                'type': 'key', 'vk': 69, 'name': 'E', 'down': True,
                'recorded_at_ms': 150, 'delay_ms': 0}
            dialog._insert_step('key')
        self.assertEqual(step_dialog.call_args.args[1]['recorded_at_ms'], 600)
        self.assertEqual([step_offset(step) for step in dialog.inputs],
                         [0, 100, 150, 950, 1150, 1200, 1300])
        dialog.save()
        self.assertIn('E', [step.get('name') for step in dialog.result['inputs']])

    def test_inserting_a_delay_shifts_the_timeline_and_keeps_the_route_valid(self):
        dialog = route_dialog()
        dialog.inputs_list = Mock()
        dialog.inputs_list.curselection.return_value = (dialog.input_rows.index(('input', 2)),)
        dialog.reload = Mock()
        with patch('macroflow.ui.dialogs.recorded_steps.DelayInsertDialog') as delay_dialog, \
                patch('macroflow.ui.dialogs.recorded_steps.show_floating_notice'):
            delay_dialog.return_value.show.return_value = {'duration_ms': 400}
            dialog._insert_delay()
        self.assertEqual(dialog.duration, 1700.0)
        self.assertEqual([step_offset(step) for step in dialog.inputs], [0, 100, 900, 1500, 1550, 1650])
        self.assertEqual([mark['gap_ms'] for mark in dialog.marks], [0, 1400])
        self.assertEqual(dialog.trajectory[-1]['at_ms'], 1700.0)
        dialog.save()
        self.assertIsNotNone(dialog.result)
        self.assertEqual(dialog.result['duration_ms'], 1700.0)


class DelayInsertDialogTests(unittest.TestCase):
    def dialog(self, fields: dict):
        dialog = DelayInsertDialog.__new__(DelayInsertDialog)
        dialog.result = None
        dialog.fields = {name: FakeVar(value) for name, value in fields.items()}
        dialog.destroy = Mock()
        return dialog

    def test_asks_for_the_position_only_when_the_route_needs_it(self):
        dialog = self.dialog({'at_ms': '500', 'duration_ms': '400'})
        dialog.save()
        self.assertEqual(dialog.result, {'duration_ms': 400, 'at_ms': 500.0})
        plain = self.dialog({'duration_ms': '250'})
        plain.save()
        self.assertEqual(plain.result, {'duration_ms': 250})

    def test_zero_or_invalid_delay_is_reported(self):
        for value in ('0', '-5', 'abc'):
            with self.subTest(value=value):
                dialog = self.dialog({'duration_ms': value})
                with patch('macroflow.ui.dialogs.recorded_steps.show_floating_notice') as notice:
                    dialog.save()
                notice.assert_called_once()
                dialog.destroy.assert_not_called()
                self.assertIsNone(dialog.result)


class RecordedInputInsertTests(unittest.TestCase):
    """录制动作列表也要能插入按键/鼠标/延时。"""

    def dialog(self, steps: list[dict], selection: tuple[int, ...] = ()):
        from macroflow.ui.dialogs.segments import RecordedInputDialog
        dialog = RecordedInputDialog.__new__(RecordedInputDialog)
        dialog.steps = [dict(step) for step in steps]
        dialog.listbox = Mock()
        dialog.listbox.curselection.return_value = selection
        dialog._reload = Mock()
        return dialog

    def test_new_steps_default_to_500ms_after_the_selected_row(self):
        dialog = self.dialog([{'type': 'key', 'recorded_at_ms': 100},
                              {'type': 'key', 'recorded_at_ms': 500}], selection=(0,))
        self.assertEqual(dialog._insert_offset(), 600.0)
        dialog = self.dialog([{'type': 'key', 'recorded_at_ms': 100}], selection=(0,))
        self.assertEqual(dialog._insert_offset(), 600.0)
        dialog = self.dialog([{'type': 'key', 'recorded_at_ms': 100}])
        self.assertEqual(dialog._insert_offset(), 600.0)

    def test_inserting_a_key_step_keeps_the_order_and_selects_it(self):
        dialog = self.dialog([{'type': 'key', 'recorded_at_ms': 100}], selection=(0,))
        with patch('macroflow.ui.dialogs.recorded_steps.RecordedStepDialog') as step_dialog:
            step_dialog.return_value.show.return_value = {
                'type': 'key', 'vk': 69, 'name': 'E', 'down': True,
                'recorded_at_ms': 200, 'delay_ms': 0}
            dialog._insert_step('key')
        self.assertEqual([step.get('name') for step in dialog.steps], [None, 'E'])
        self.assertEqual(step_dialog.call_args.args[1]['recorded_at_ms'], 600.0)
        self.assertEqual(step_dialog.call_args.kwargs['delay_ms'], 500)
        dialog.listbox.selection_set.assert_called_once_with(1)

    def test_inserting_a_delay_adds_a_delay_step(self):
        dialog = self.dialog([{'type': 'key', 'recorded_at_ms': 0}], selection=(0,))
        with patch('macroflow.ui.dialogs.recorded_steps.DelayInsertDialog') as delay_dialog:
            delay_dialog.return_value.show.return_value = {'duration_ms': 250}
            dialog._insert_delay()
        self.assertEqual(dialog.steps[-1]['type'], 'delay')
        self.assertEqual(dialog.steps[-1]['ms'], 250)
        self.assertEqual(dialog.steps[-1]['recorded_at_ms'], 0.0)


class EditActionDispatchTests(unittest.TestCase):
    def test_route_steps_and_palette_keys_open_their_own_editors(self):
        from macroflow.ui.dialogs import actions as dialogs
        with patch('macroflow.ui.dialogs.recorded_steps.RouteActionDialog') as route_dialog, \
                patch('macroflow.ui.dialogs.recorded_steps.RecordedStepDialog') as step_dialog, \
                patch.object(dialogs, 'KeyActionDialog') as key_dialog:
            for dialog in (route_dialog, step_dialog, key_dialog):
                dialog.return_value.show.return_value = None
            dialogs.edit_action(Mock(), route_action())
            route_dialog.assert_called_once()
            step_dialog.assert_not_called()

            step_dialog.handles.return_value = True
            dialogs.edit_action(Mock(), {'type': 'key', 'vk': 87, 'recorded_at_ms': 0})
            step_dialog.assert_called_once()
            key_dialog.assert_not_called()

            step_dialog.handles.return_value = False
            step_dialog.reset_mock()
            dialogs.edit_action(Mock(), {'type': 'key_press', 'vk': 87, 'name': 'w'})
            key_dialog.assert_called_once()
            step_dialog.assert_not_called()


class RouteToolbarRowTests(unittest.TestCase):
    """路线编辑器工具栏：按钮之间留同样的缝隙，不再用会多画箭头的 Menubutton。"""

    def test_row_buttons_share_one_gap(self):
        from macroflow.ui.dialogs import recorded_steps as module
        with patch.object(module.ttk, 'Button') as button:
            module.pack_row_buttons(Mock(), (('插入按键', Mock()), ('等待小怪消失', Mock())))
        self.assertEqual(button.call_count, 2)
        self.assertEqual(button.return_value.pack.call_args_list,
                         [call(side='left', padx=(0, module.px(module.ROW_GAP)))] * 2)

    def test_the_action_palette_uses_a_plain_button_not_a_dropdown(self):
        import inspect
        from macroflow.ui.dialogs import recorded_steps as module
        source = inspect.getsource(module)
        # ttk 的 Menubutton 会在按钮右边多画一个下拉箭头，和文字里的 ▾ 重复。
        self.assertNotIn('ttk.Menubutton(', source)
        self.assertIn('_show_action_menu', source)


if __name__ == "__main__":
    unittest.main()
