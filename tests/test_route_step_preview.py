"""键鼠连续路线：从选中的某一步开始执行（跳过前面的步骤并重设路线时钟）。"""
import unittest
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path
from tests.test_ssjj_waypoint_playback import PlaybackTests, straight_recording
from macroflow.execution.ssjj_recording import validate_recording


def stepped_recording(start_index=None):
    """W 按住 → 左键一下 → E 敲击：可以从中间任何一步起跑。"""
    action = straight_recording()
    action['inputs'] = [
        {'type': 'key', 'vk': 87, 'down': True, 'recorded_at_ms': 0},
        {'type': 'mouse_button', 'button': 'left', 'down': True, 'recorded_at_ms': 400},
        {'type': 'mouse_button', 'button': 'left', 'down': False, 'recorded_at_ms': 450},
        {'type': 'key', 'vk': 69, 'down': True, 'recorded_at_ms': 600},
        {'type': 'key', 'vk': 69, 'down': False, 'recorded_at_ms': 900},
    ]
    if start_index is not None:
        action['start_step_index'] = start_index
    return action


def key_events(events):
    return [(args, at) for name, args, at, *_ in events if name == 'send_key']


class RouteStepPreviewTests(unittest.TestCase):
    def test_running_from_a_later_step_skips_the_earlier_inputs(self):
        events = PlaybackTests().run_recording(stepped_recording(3))
        # 起跑步骤之前的左键按下/松开不再回放。
        self.assertEqual([args for name, args, *_ in events if name == 'send_button'], [])
        self.assertTrue(PlaybackTests.played(events, 'send_key', 69, True))

    def test_starting_later_replays_from_the_step_clock_and_restores_held_keys(self):
        events = PlaybackTests().run_recording(stepped_recording(3))
        keys = key_events(events)
        e_down_at = next(at for args, at in keys if args == (69, True))
        # 走到试跑起点时按过的 W 松开了，回放开始前按录制状态重新按住。
        self.assertEqual([args[1] for args, at in keys
                          if args[0] == 87 and at <= e_down_at][-1], True)
        # 时钟从起跑步骤的 600 ms 起算：E 立刻按下（不再等 600 ms），300 ms 后松开。
        e_up_at = next(at for args, at in keys if args == (69, False))
        self.assertAlmostEqual(e_up_at - e_down_at, .3, delta=.02)
        walk_release = max(at for args, at in keys if args == (87, False) and at <= e_down_at)
        self.assertLess(e_down_at - walk_release, .3)

    def test_the_first_step_still_replays_the_whole_route(self):
        events = PlaybackTests().run_recording(stepped_recording(0))
        keys = [args for name, args, *_ in events if name == 'send_key']
        self.assertEqual(keys[:3], [(87, True), (69, True), (69, False)])

    def test_the_preview_start_is_logged(self):
        case = PlaybackTests()
        case.run_recording(stepped_recording(3))
        logged = [call.args[0] for call in case.player.on_log.call_args_list]
        self.assertTrue(any('从第 4/5 步开始试跑' in message for message in logged))

    def test_start_step_index_must_be_inside_the_route(self):
        validate_recording(stepped_recording(4))
        for value in (-1, 5, '1', None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_recording(dict(stepped_recording(), start_step_index=value))


class RouteStepMenuTests(unittest.TestCase):
    def dialog(self, selection=(0,), marks=None):
        from tests.test_recorded_step_editor import route_dialog
        dialog = route_dialog(marks=marks)
        dialog.inputs_list = Mock()
        dialog.inputs_list.curselection.return_value = selection
        return dialog

    def test_selected_step_index_follows_the_selected_row(self):
        dialog = self.dialog(selection=(2,))
        self.assertEqual(dialog.selected_step_index(), dialog.input_rows[2][1])

    def test_a_correction_row_starts_at_the_first_input_after_it(self):
        dialog = self.dialog()
        row = dialog.input_rows.index(('mark', 1))
        dialog.inputs_list.curselection.return_value = (row,)
        from macroflow.execution.ssjj_recording import mark_offsets
        first_after = next(i for i, step in enumerate(dialog.inputs)
                           if step['recorded_at_ms'] >= mark_offsets(dialog.marks)[1])
        self.assertEqual(dialog.selected_step_index(), first_after)

    def test_no_selection_is_reported(self):
        dialog = self.dialog(selection=())
        with patch('macroflow.ui.dialogs.recorded_steps.show_floating_notice') as notice:
            self.assertIsNone(dialog.selected_step_index())
            dialog.run_from_selected_step()
        notice.assert_called_once()
        dialog.destroy.assert_not_called()

    def test_running_applies_the_edit_and_asks_the_app_to_start_at_that_step(self):
        dialog = self.dialog(selection=(1,))
        app = Mock()
        with patch('macroflow.ui.dialogs.helpers._app_via_parent', return_value=app):
            dialog.run_from_selected_step()
        worker_args = app._run_detection_entrypoint.call_args.args
        self.assertEqual(worker_args[2]["type"], "ssjj_recording")
        self.assertEqual(worker_args[3], dialog.input_rows[1][1])
        self.assertEqual(worker_args[2], dialog.result)
        dialog.destroy.assert_called_once()

    def test_running_without_an_app_reports_and_keeps_the_dialog_open(self):
        dialog = self.dialog(selection=(1,))
        with patch('macroflow.ui.dialogs.helpers._app_via_parent', return_value=None), \
                patch('macroflow.ui.dialogs.recorded_steps.show_floating_notice') as notice:
            dialog.run_from_selected_step()
        notice.assert_called_once()
        dialog.destroy.assert_not_called()
        self.assertIsNone(dialog.result)

    def test_an_invalid_route_is_reported_before_running(self):
        dialog = self.dialog(marks=[])
        app = Mock()
        with patch('macroflow.ui.dialogs.helpers._app_via_parent', return_value=app), \
                patch('macroflow.ui.dialogs.recorded_steps.show_floating_notice') as notice:
            dialog.run_from_selected_step()
        notice.assert_called_once()
        app._run_detection_entrypoint.assert_not_called()
        dialog.destroy.assert_not_called()


class RouteStepRunTests(unittest.TestCase):
    """执行入口：从选中的步骤单独试跑一条键鼠连续路线。"""

    def _app(self):
        from tests.test_script_editing import SingleActionRunTests
        return SingleActionRunTests()._app()

    def route_action(self):
        return {'type': 'ssjj_recording', 'map_id': 100001, 'duration_ms': 900,
                'marks': [{'gap_ms': 0, 'position': [0, 0, 0], 'yaw': 0, 'pitch': 0}],
                'inputs': [{'type': 'key', 'vk': 87, 'down': True, 'recorded_at_ms': 0}],
                'trajectory': []}

    def test_the_start_step_travels_with_a_copy_of_the_action(self):
        from macroflow.ui.app.route_preview import _run_route_step_test
        app = self._app()
        action = self.route_action()
        with patch("threading.Thread") as thread_class:
            _run_route_step_test(app, action, 3, 2)
        worker_args = thread_class.call_args.kwargs["args"]
        self.assertEqual(worker_args[0][0]["start_step_index"], 3)
        self.assertNotIn("start_step_index", action)
        self.assertEqual(worker_args[1], 2)
        self.assertIs(thread_class.call_args.kwargs["kwargs"]["single_action"], True)
        self.assertIn("路线试跑：从第 4 步开始", app._set_execution_progress.call_args.args[0])
        thread_class.return_value.start.assert_called_once()

    def test_entrypoint_runs_through_the_detection_guard(self):
        from macroflow.ui.app.route_preview import _run_route_step_test, run_route_step_test
        app = self._app()
        app._run_detection_entrypoint = Mock()
        action = self.route_action()
        run_route_step_test(app, action, 2)
        app._run_detection_entrypoint.assert_called_once_with(
            _run_route_step_test, app, action, 2, 1)

    def test_a_running_worker_blocks_the_preview(self):
        from macroflow.ui.app.route_preview import _run_route_step_test
        app = self._app()
        app.worker.is_alive.return_value = True
        with patch("threading.Thread") as thread_class:
            _run_route_step_test(app, self.route_action(), 1)
        app._notify.assert_called_once_with("正在运行", "已有脚本或工作流正在执行。")
        thread_class.assert_not_called()


if __name__ == '__main__':
    unittest.main()
