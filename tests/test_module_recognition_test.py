"""One-shot recognition tests: mock capture, OCR, native process calls and Tk."""
import importlib
import unittest
from unittest.mock import Mock, patch

from tests.helpers.core import FakeVar
from macroflow.ui.dialogs.module_objects import TemplateRegionFormDialog, TemplateRegionManagerDialog

MODULE = 'macroflow.ui.dialogs.module_test'


class ModuleRecognitionTestTests(unittest.TestCase):
    def test_image_uses_saved_region_and_threshold(self):
        module = importlib.import_module(MODULE)
        for hit in (None, {'center_x': 15, 'center_y': 25}):
            with self.subTest(hit=hit), patch(f'{MODULE}.find_template', return_value=hit) as find:
                success, text = module.recognize_module_once(dict(template='images/test.png', region=[1, 2, 30, 40], threshold=.9))
            self.assertEqual(success, hit is not None)
            self.assertEqual(find.call_args.args[1:], (.9, (1, 2, 30, 40)))

    def test_text_uses_same_whole_text_and_box_matching(self):
        module = importlib.import_module(MODULE)
        with patch(f'{MODULE}.recognize_region_with_boxes', return_value=('prefix Ready', [])):
            self.assertTrue(module.recognize_module_once(dict(recognize='text', expected_text='ready'))[0])
            self.assertFalse(module.recognize_module_once(dict(recognize='text', expected_text='ready', match_mode='equals'))[0])
        boxes = [dict(text='Ready', confidence=.95, box=[[0, 0], [20, 0], [20, 10], [0, 10]])]
        with patch(f'{MODULE}.recognize_region_with_boxes', return_value=('Ready Other', boxes)):
            self.assertTrue(module.recognize_module_once(dict(recognize='text', expected_text='ready', match_mode='equals'))[0])

    def test_number_reports_read_value_or_failure(self):
        module = importlib.import_module(MODULE)
        for text, expected in (('123', True), ('no number', False)):
            with self.subTest(text=text), patch(f'{MODULE}.recognize_region_with_boxes', return_value=(text, [])):
                success, detail = module.recognize_module_once(dict(recognize='number', region=[1, 2, 30, 40]))
            self.assertEqual(success, expected)
            if expected:
                self.assertIn('123', detail)

    def test_process_never_captures_screen(self):
        module = importlib.import_module(MODULE)
        with patch(f'{MODULE}.running_process_names', return_value=['game.exe']), \
             patch(f'{MODULE}.find_template') as image, patch(f'{MODULE}.recognize_region_with_boxes') as ocr:
            self.assertTrue(module.recognize_module_once(dict(recognize='process', process_name='GAME.EXE'))[0])
            self.assertFalse(module.recognize_module_once(dict(recognize='process', process_name='absent.exe'))[0])
        image.assert_not_called()
        ocr.assert_not_called()

    def test_no_recognition_or_pure_action_does_not_execute_anything(self):
        module = importlib.import_module(MODULE)
        for obj in (dict(recognize='none'), dict(pure_action=True, category='special')):
            with self.subTest(obj=obj), patch(f'{MODULE}.find_template') as find:
                success, detail = module.recognize_module_once(obj)
            self.assertFalse(success)
            self.assertIn('无需识别', detail)
            find.assert_not_called()

    def test_invalid_recognition_settings_are_rejected(self):
        module = importlib.import_module(MODULE)
        for obj in (dict(template=''), dict(template='a', region=[0, 0, 0, 10]),
                    dict(template='a', threshold='nan'), dict(recognize='number'),
                    dict(recognize='process', process_name='')):
            with self.subTest(obj=obj), self.assertRaises(ValueError):
                module.recognize_module_once(obj)

    def test_async_result_success_failure_and_error_all_expire_after_500ms(self):
        module = importlib.import_module(MODULE)
        for outcome in ((True, 'found'), (False, 'not found'), RuntimeError('OCR unavailable')):
            with self.subTest(outcome=outcome):
                parent, window, grab = Mock(), Mock(), Mock()
                parent._recognition_test_running = False
                parent.grab_current.return_value = grab
                window.state.return_value = 'zoomed'
                pending = []
                parent.after.side_effect = lambda ms, callback: pending.append(callback)
                def start_thread(**kwargs):
                    return Mock(start=kwargs['target'])
                with patch(f'{MODULE}.app_windows', return_value=[window]), \
                     patch(f'{MODULE}.Thread', side_effect=start_thread), \
                     patch(f'{MODULE}.recognize_module_once', side_effect=outcome if isinstance(outcome, Exception) else None,
                           return_value=outcome if isinstance(outcome, tuple) else None), \
                     patch(f'{MODULE}.show_floating_notice') as notice:
                    module.start_module_recognition_test(parent, dict(name='My module'))
                    while pending:
                        pending.pop(0)()
                self.assertFalse(parent._recognition_test_running)
                window.withdraw.assert_called_once()
                window.state.assert_any_call('zoomed')
                grab.grab_set.assert_called_once()
                self.assertEqual(notice.call_args.kwargs['duration_ms'], 500)
                self.assertIn('My module', notice.call_args.args[2])
                self.assertIn('成功' if isinstance(outcome, tuple) and outcome[0] else '失败', notice.call_args.args[1])

    def test_form_tests_unsaved_recognition_fields_without_saving(self):
        form = TemplateRegionFormDialog.__new__(TemplateRegionFormDialog)
        for name, value in dict(name_var='Unsaved', category_var='切换模块', recognize_var='识别文字',
                                image_var='', region_var='1,2,30,40', expected_text_var='Ready',
                                match_mode_var='等于', process_name_var='', threshold_var='.85').items():
            setattr(form, name, FakeVar(value))
        with patch('macroflow.ui.dialogs.module_objects.start_module_recognition_test') as start:
            form.test_recognition()
        obj = start.call_args.args[1]
        self.assertEqual(obj['expected_text'], 'Ready')
        self.assertEqual(obj['region'], [1, 2, 30, 40])
        self.assertEqual(obj['match_mode'], 'equals')
        self.assertFalse(hasattr(form, 'result'))

    def test_manager_tests_selected_module_in_any_category(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog._current_view = Mock()
        dialog._current_view.return_value.selected_keys.return_value = ['module:test']
        for category in ('switch', 'workflow_global', 'script_global', 'special'):
            dialog.objects = {'module:test': dict(name='Test', category=category)}
            with patch('macroflow.ui.dialogs.module_objects.start_module_recognition_test') as start:
                dialog._test_selected_recognition()
            self.assertEqual(start.call_args.args[1]['category'], category)
