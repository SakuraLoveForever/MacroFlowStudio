"""Plugin form and code-block editing checks without Tk windows."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from macroflow.ui.app.plugins_page import IntervalPluginForm, interval_ms, interval_display, recognition_condition
from macroflow.ui.dialogs.plugin_actions import ActionListEditor
from macroflow.ui.dialogs.route_action_insert import ScriptActionInserter, route_action_categories


class PluginFormTests(unittest.TestCase):
    def test_interval_units_roundtrip_and_invalid_input(self):
        for value, unit, expected in (("1", "h", 3600000), ("2.5", "min", 150000), ("1.5", "s", 1500), ("8760", "h", 31536000000)):
            self.assertEqual(interval_ms(value, unit), expected)
            number, display_unit = interval_display(expected)
            self.assertEqual(interval_ms(number, display_unit), expected)
        for value in ("0", "-1", "nan", "inf", "", "0.00001", "0.999", "31536001", "1e999999"):
            with self.assertRaises(ValueError):
                interval_ms(value, "s")

    def test_recognition_condition_rejects_non_image_or_deleted_modules(self):
        binding = {"module_ref": True, "module_key": "module:one"}
        with patch("macroflow.ui.app.plugins_page.registered_module_object", return_value={"recognize": "image", "category": "switch"}):
            self.assertEqual(recognition_condition(binding)["type"], "image_match")
        for obj in (None, {"recognize": "number", "category": "switch"}, {"recognize": "image", "category": "special"}):
            with patch("macroflow.ui.app.plugins_page.registered_module_object", return_value=obj):
                with self.assertRaises(ValueError):
                    recognition_condition(binding)

    def test_status_refresh_syncs_external_disable_without_changing_drafts(self):
        form = object.__new__(IntervalPluginForm)
        form.plugin = SimpleNamespace(config={"enabled": False}, status=lambda: "未启用")
        form.draft = {"enabled": True, "condition": {"module_key": "draft"}, "actions": [{"type": "block"}]}
        before = copy.deepcopy(form.draft)
        form.enabled, form.status, form.parent = Mock(), Mock(), Mock()
        form.refresh_status()
        form.enabled.set.assert_called_once_with(False)
        form.status.set.assert_called_once_with("未启用")
        self.assertEqual(form.draft, before)
        form.parent.after.assert_called_once_with(1000, form.refresh_status)

    def test_disabling_still_works_when_selected_module_was_deleted(self):
        form = object.__new__(IntervalPluginForm)
        form.app = Mock()
        form.plugin = SimpleNamespace(config={"enabled": True}, status=lambda: "已关闭")
        form.draft = {"enabled": True, "condition": {"module_key": "module:deleted"}, "actions": []}
        form.enabled = Mock(get=Mock(return_value=False))
        form.hours = Mock(get=Mock(return_value="1"))
        form.minutes = Mock(get=Mock(return_value="0"))
        form.status = Mock()
        form.recheck_number = Mock(get=Mock(return_value="1"))
        form.recheck_unit = Mock(get=Mock(return_value="min"))
        form.trigger_on = Mock(get=Mock(return_value="成功"))
        form.name = Mock(get=Mock(return_value="定时插件"))
        save = Mock(side_effect=lambda app, config: setattr(form.plugin, "config", config))
        runtime = SimpleNamespace(save_interval_plugin=save)
        with patch.dict("sys.modules", {"macroflow.ui.app.plugins_runtime": runtime}), \
                patch("macroflow.ui.app.plugins_page.recognition_condition") as validate:
            form.save()
        validate.assert_not_called()
        self.assertFalse(save.call_args.args[1]["enabled"])
        form.app._notify.assert_not_called()


class PluginActionsTests(unittest.TestCase):
    def editor(self):
        editor = object.__new__(ActionListEditor)
        editor.actions = [{"type": "delay", "ms": 1, "action_id": "first"}, {"type": "block", "action_id": "last"}]
        editor.tree = Mock()
        editor.tree.selection.return_value = ("0",)
        editor.reload = Mock()
        return editor

    def test_insert_command_is_isolated_and_keeps_action_ids_without_route_fields(self):
        editor = self.editor()
        app = SimpleNamespace(script=SimpleNamespace(actions=[{"type": "block"}]))
        before = copy.deepcopy(app.script.actions)
        inserter = ScriptActionInserter(editor, app, editor.actions)
        inserter.add_block()
        self.assertEqual([a["type"] for a in editor.actions], ["delay", "block", "block"])
        self.assertTrue(editor.actions[1]["action_id"])
        self.assertNotIn("recorded_at_ms", editor.actions[1])
        self.assertNotIn("route_script_action", editor.actions[1])
        self.assertEqual(app.script.actions, before)

    def test_inline_batch_preserves_jump_references(self):
        editor = self.editor()
        actions = [{"type": "jump", "action_id": "jump", "jump_action_id": "target"}, {"type": "block", "action_id": "target"}]
        ScriptActionInserter(editor, None, editor.actions)._insert_script_actions(1, actions)
        self.assertEqual(editor.actions[1]["jump_action_id"], editor.actions[2]["action_id"])
        self.assertEqual([a["action_id"] for a in editor.actions], ["first", "jump", "target", "last"])

    def test_all_script_insertion_commands_are_available(self):
        commands = {spec[1] for _, specs in route_action_categories() for spec in specs}
        for command in ("_insert_script_reference", "_insert_script_inline", "_insert_script_range", "add_module", "add_jump", "add_ocr_compare", "add_key_sequence"):
            self.assertIn(command, commands)

    def test_move_and_delete_preserve_surviving_action_ids(self):
        editor = self.editor()
        editor.move_selected(1)
        self.assertEqual([a["action_id"] for a in editor.actions], ["last", "first"])
        editor.tree.selection.return_value = ("0",)
        editor.delete_selected()
        self.assertEqual([a["action_id"] for a in editor.actions], ["first"])

    def test_edit_preserves_identity_and_cancel_does_not_replace_action(self):
        editor = self.editor()
        editor.inserter = SimpleNamespace(test_row_list_condition_click=Mock(), resolution_styles=[])
        before = copy.deepcopy(editor.actions)
        with patch("macroflow.ui.dialogs.plugin_actions.edit_action", return_value=None):
            editor.edit_selected()
        self.assertEqual(editor.actions, before)
        with patch("macroflow.ui.dialogs.plugin_actions.edit_action", return_value={"type": "delay", "ms": 10}):
            editor.edit_selected()
        self.assertEqual(editor.actions[0]["action_id"], "first")
        self.assertEqual(editor.actions[0]["ms"], 10)


if __name__ == "__main__":
    unittest.main()
