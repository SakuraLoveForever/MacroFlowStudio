"""Form control sizing and native integer stepping, without creating Tk windows."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from macroflow.ui import form_controls


class FormControlTests(unittest.TestCase):
    def test_combo_uses_current_text_then_longest_option_and_caps_width(self):
        font = Mock()
        font.measure.side_effect = lambda text: len(text) * (20 if "中" in text else 10)
        self.assertEqual(form_controls.combobox_width(font, "a", ("中" * 100,), 26), 4)
        self.assertEqual(form_controls.combobox_width(font, "", ("中文",), 26), 5)
        self.assertEqual(form_controls.combobox_width(font, "中" * 100, (), 26), 26)
        self.assertEqual(form_controls.combobox_width(font, "中文", (), 2), 2)

    def test_integer_control_uses_native_step_one_and_explicit_bounds(self):
        variable = object()
        with patch.object(form_controls.ttk, "Spinbox") as spinbox:
            form_controls.integer_spinbox(None, textvariable=variable, from_=-100, to=100, width=8)
        spinbox.assert_called_once_with(
            None, textvariable=variable, from_=-100, to=100, increment=1, width=8,
        )

    def test_changed_values_resize_using_latest_bound_value_without_tk(self):
        combo = object.__new__(form_controls.compact_combobox)
        combo._compact_font = Mock()
        combo._compact_font.measure.side_effect = lambda text: len(text) * 10
        combo._variable = Mock()
        combo._variable.get.return_value = "current value"
        combo._max_width = 26
        combo.cget = Mock(return_value=("longer option remains unselected",))
        with patch.object(form_controls.ttk.Combobox, "configure") as configure:
            combo.configure(values=("changed option",))
        self.assertEqual(configure.call_args_list[-1].kwargs, {"width": 14})

    def test_destroy_removes_only_own_variable_trace(self):
        combo = object.__new__(form_controls.compact_combobox)
        combo._variable = Mock()
        combo._trace = "trace-id"
        combo._remove_trace(Mock(widget=combo))
        combo._remove_trace(Mock(widget=combo))
        combo._variable.trace_remove.assert_called_once_with("write", "trace-id")


if __name__ == "__main__":
    unittest.main()
