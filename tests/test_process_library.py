"""Shared process names remain selectable while their applications are closed."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class ProcessLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "process_names.json"
        self.file_patch = patch("macroflow.core.process_library.PROCESS_NAMES_PATH", self.path)
        self.file_patch.start()
        self.addCleanup(self.file_patch.stop)

    def test_names_survive_reloading_without_running_processes(self):
        from macroflow.core.process_library import add_process_name, load_process_names, process_name_choices
        add_process_name(" Game.exe ")
        add_process_name("GAME.EXE")
        self.assertEqual(load_process_names(), ["Game.exe"])
        self.assertEqual(process_name_choices(), ["Game.exe"])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), ["Game.exe"])

    def test_picker_keeps_current_target_and_deduplicates_saved_names(self):
        from macroflow.core.process_library import add_process_name, process_name_choices
        add_process_name("Saved.exe")
        add_process_name("saved.EXE")
        self.assertEqual(process_name_choices("Current.exe"), ["Current.exe", "Saved.exe"])

    def test_removal_preserves_other_names_and_current_selection(self):
        from macroflow.core.process_library import add_process_name, remove_process_names, process_name_choices
        add_process_name("First.exe")
        add_process_name("Second.exe")
        remove_process_names(["FIRST.EXE"])
        self.assertEqual(process_name_choices("First.exe"), ["First.exe", "Second.exe"])

    def test_invalid_names_cannot_replace_saved_data(self):
        from macroflow.core.process_library import add_process_name, load_process_names
        add_process_name("Good.exe")
        for name in ("", "   ", "C:/Game.exe", "bad\\game.exe", "bad:name", "bad\x00.exe", None):
            with self.subTest(name=name), self.assertRaises(ValueError):
                add_process_name(name)
        self.assertEqual(load_process_names(), ["Good.exe"])

    def test_failed_atomic_write_leaves_previous_library_intact(self):
        from macroflow.core.process_library import add_process_name, load_process_names
        add_process_name("First.exe")
        with patch("macroflow.core.process_library.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                add_process_name("Second.exe")
        self.assertEqual(load_process_names(), ["First.exe"])
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_corrupt_library_is_not_silently_overwritten(self):
        from macroflow.core.process_library import add_process_name
        self.path.write_text("broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            add_process_name("Game.exe")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "broken")

    def test_picker_still_offers_saved_names_when_process_enumeration_fails(self):
        from macroflow.core.process_library import add_process_name
        from macroflow.ui.dialogs.process_names import ProcessNamePicker
        add_process_name("Closed.exe")
        picker = ProcessNamePicker.__new__(ProcessNamePicker)
        picker.variable = SimpleNamespace(get=lambda: "")
        values = {}
        picker.combo = SimpleNamespace(configure=lambda **kwargs: values.update(kwargs))
        with patch("macroflow.ui.dialogs.process_names.running_process_names", side_effect=OSError("unavailable")):
            picker.refresh()
        self.assertEqual(values["values"], ["Closed.exe"])

    def test_shared_picker_does_not_allow_typing(self):
        from macroflow.core.process_library import add_process_name
        from macroflow.ui.dialogs.process_names import ProcessNamePicker
        add_process_name("Closed.exe")
        variable = SimpleNamespace(get=lambda: "")
        with patch("macroflow.ui.dialogs.process_names.ttk.Frame.__init__", return_value=None), \
                patch("macroflow.ui.dialogs.process_names.ttk.Combobox") as combo, \
                patch("macroflow.ui.dialogs.process_names.ttk.Button"):
            ProcessNamePicker(None, variable)
        self.assertEqual(combo.call_args.kwargs["state"], "readonly")
        self.assertEqual(combo.return_value.configure.call_args.kwargs["values"], ["Closed.exe"])

    def test_library_running_selector_does_not_allow_typing(self):
        from macroflow.ui.dialogs.process_names import ProcessLibraryDialog
        with patch("macroflow.ui.dialogs.process_names.ModalDialog.__init__", return_value=None), \
                patch("macroflow.ui.dialogs.process_names.tk.StringVar"), \
                patch("macroflow.ui.dialogs.process_names.tk.Listbox"), \
                patch("macroflow.ui.dialogs.process_names.ttk.Frame"), \
                patch("macroflow.ui.dialogs.process_names.ttk.Label"), \
                patch("macroflow.ui.dialogs.process_names.ttk.Button"), \
                patch("macroflow.ui.dialogs.process_names.ttk.Combobox") as combo:
            ProcessLibraryDialog(None)
        self.assertEqual(combo.call_args.kwargs["state"], "readonly")

    def test_manager_keeps_multiple_names_and_removes_only_selected_entry(self):
        from macroflow.core.process_library import load_process_names
        from macroflow.ui.dialogs.process_names import ProcessLibraryDialog
        dialog = ProcessLibraryDialog.__new__(ProcessLibraryDialog)
        rows = []
        dialog.names = SimpleNamespace(delete=lambda *args: rows.clear(),
                                       insert=lambda index, name: rows.append(name),
                                       get=lambda index: rows[index], curselection=lambda: (0,))
        for name in ("First.exe", "Second.exe", "Third.exe"):
            dialog.name = SimpleNamespace(get=lambda name=name: name)
            dialog._add()
        self.assertEqual(rows, ["First.exe", "Second.exe", "Third.exe"])
        dialog._remove()
        self.assertEqual(load_process_names(), ["Second.exe", "Third.exe"])


if __name__ == "__main__":
    unittest.main()
