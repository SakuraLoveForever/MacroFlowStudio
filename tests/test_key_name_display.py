"""高级动作编辑把虚键码显示成按键名；后台验证，不创建窗口。"""
import unittest

from tests.helpers.core import add_src_to_path

from macroflow.ui.dialogs.actions import JsonActionDialog


class AdvancedEditorKeyNameTests(unittest.TestCase):
    """编辑键盘动作时显示 W/E/SPACE 这类名称，保存时再换回虚键码。"""

    def test_key_events_show_names_inside_recorded_inputs(self):
        action = {'type': 'ssjj_recording', 'map_id': 100001, 'inputs': [
            {'type': 'key', 'vk': 87, 'name': 'w', 'down': True, 'recorded_at_ms': 0},
            {'type': 'mouse_button', 'button': 'left', 'down': True, 'recorded_at_ms': 0},
            {'type': 'key', 'vk': 0x20, 'down': False, 'recorded_at_ms': 900}]}
        shown = JsonActionDialog._with_key_names(action)
        self.assertEqual(shown['inputs'][0]['vk'], 'W')
        self.assertNotIn('name', shown['inputs'][0])
        self.assertEqual(shown['inputs'][2]['vk'], 'SPACE')
        self.assertEqual(shown['inputs'][1]['button'], 'left')
        self.assertEqual(action['inputs'][0]['vk'], 87)      # 原动作不被改动

    def test_typed_letters_become_virtual_key_codes_again(self):
        stored = JsonActionDialog._with_key_codes({'type': 'recorded_input', 'steps': [
            {'type': 'key', 'vk': 'E', 'down': True},
            {'type': 'key_press', 'vk': 'space'}]})
        self.assertEqual(stored['steps'][0], {'type': 'key', 'vk': 69, 'name': 'E', 'down': True})
        self.assertEqual(stored['steps'][1], {'type': 'key_press', 'vk': 0x20, 'name': 'SPACE'})

    def test_display_round_trip_keeps_codes_and_syncs_the_name(self):
        action = {'type': 'key', 'vk': 87, 'name': 'w', 'down': True}
        stored = JsonActionDialog._with_key_codes(JsonActionDialog._with_key_names(action))
        self.assertEqual(stored, {'type': 'key', 'vk': 87, 'name': 'W', 'down': True})

    def test_unknown_names_are_shown_as_is_and_rejected_on_save(self):
        self.assertEqual(JsonActionDialog._with_key_names({'type': 'key', 'vk': '未知键'})['vk'], '未知键')
        with self.assertRaises(ValueError):
            JsonActionDialog._with_key_codes({'type': 'key', 'vk': '没有这个键', 'down': True})

    def test_non_key_actions_are_untouched(self):
        action = {'type': 'mouse_button', 'button': 'left', 'down': True, 'vk': 0}
        self.assertEqual(JsonActionDialog._with_key_names(action), action)
        self.assertEqual(JsonActionDialog._with_key_codes(action), action)


if __name__ == "__main__":
    unittest.main()
