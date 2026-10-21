"""快捷键脚本类别选择，使用控件替身，不创建窗口。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.helpers.core import FakeVar
from macroflow.core.models import MacroScript
from macroflow.core.storage import save_script
from macroflow.ui.dialogs.app_dialogs import HotkeyBindingDialog


class HotkeyCategoryTests(unittest.TestCase):
    def test_all_configured_categories_and_duplicate_names_are_selectable(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = {}
            paths = []
            for category in ("level", "level_pack", "switch", "direction"):
                root = Path(folder) / category
                root.mkdir()
                settings[f"{category}_scripts_dir"] = str(root)
                path = root / "同名.json"
                save_script(MacroScript(name="同名"), path)
                paths.append(path.resolve())
            (Path(folder) / "level" / "无效.json").write_text("broken", encoding="utf-8")
            for path, category_label in zip(paths, ("关卡", "关卡封装", "切换", "方向")):
                dialog = HotkeyBindingDialog.__new__(HotkeyBindingDialog)
                dialog.key_vk = 65
                dialog.script_var = FakeVar(str(path))
                dialog.category_var = FakeVar("关卡")
                dialog.settings = settings
                dialog.script_box = Mock()
                dialog.destroy = Mock()
                with patch('macroflow.ui.dialogs.module_objects.load_app_settings', return_value=settings):
                    dialog._refresh_script_options()
                self.assertEqual(dialog.category_var.get(), category_label)
                self.assertEqual(set(dialog._script_labels.values()), {path})
                self.assertEqual(dialog._script_labels[dialog.script_var.get()], path)
                dialog.save()
                self.assertEqual(Path(dialog.result["script"]).resolve(), path)
                dialog.destroy.assert_called_once()

    def test_switching_category_refreshes_scripts_and_clears_previous_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = {}
            paths = []
            for category in ("level", "switch"):
                root = Path(folder) / category
                root.mkdir()
                settings[f"{category}_scripts_dir"] = str(root)
                path = root / "同名.json"
                save_script(MacroScript(name="同名"), path)
                paths.append(path.resolve())
            settings["direction_scripts_dir"] = str(Path(folder) / "empty")
            settings["level_pack_scripts_dir"] = str(Path(folder) / "empty")
            dialog = HotkeyBindingDialog.__new__(HotkeyBindingDialog)
            dialog.settings = settings
            dialog.category_var = FakeVar("关卡")
            dialog.script_var = FakeVar(str(paths[0]))
            dialog.script_box = Mock()
            dialog._refresh_script_options()
            dialog.category_var.set("切换")
            dialog._script_category_changed()
            self.assertEqual(dialog.script_var.get(), "")
            self.assertEqual(set(dialog._script_labels.values()), {paths[1]})
            dialog.category_var.set("方向")
            dialog._script_category_changed()
            self.assertEqual(dialog._script_labels, {})

    def test_arbitrary_unlisted_path_cannot_be_bound(self):
        dialog = HotkeyBindingDialog.__new__(HotkeyBindingDialog)
        dialog.key_vk = 65
        dialog.script_var = FakeVar("not-listed.json")
        dialog._script_labels = {}
        dialog.destroy = Mock()
        with patch('macroflow.ui.dialogs.app_dialogs.show_floating_notice') as notice:
            dialog.save()
        notice.assert_called_once()
        dialog.destroy.assert_not_called()
