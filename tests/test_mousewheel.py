"""Mouse-wheel routing without creating windows or sending input."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, call

from macroflow.ui import form_controls
from macroflow.ui.app.base import attach_autohide_scrollbar
from macroflow.ui.app.shell import _bind_canvas_wheel


class MouseWheelTests(TestCase):
    def test_wheel_scrolls_in_both_directions_and_handles_small_deltas(self):
        widget, target = Mock(), Mock()
        form_controls.bind_vertical_mousewheel(widget, target)
        callbacks = {entry.args[0]: entry.args[1] for entry in widget.bind.call_args_list}
        for delta, units in ((120, -1), (-240, 2), (30, -1), (-30, 1)):
            self.assertEqual(callbacks['<MouseWheel>'](SimpleNamespace(delta=delta, num=0)), 'break')
            target.yview_scroll.assert_called_with(units, 'units')
        target.reset_mock()
        self.assertEqual(callbacks['<MouseWheel>'](SimpleNamespace(delta=0, num=0)), 'break')
        target.yview_scroll.assert_not_called()

    def test_page_children_scroll_the_page_and_stop_control_value_changes(self):
        canvas, body, field = Mock(), Mock(), Mock()
        body.winfo_children.return_value = [field]
        field.winfo_children.return_value = []
        _bind_canvas_wheel(canvas, body)
        for widget in (canvas, body, field):
            callbacks = {entry.args[0]: entry.args[1] for entry in widget.bind.call_args_list}
            self.assertEqual(callbacks['<MouseWheel>'](SimpleNamespace(delta=-120, num=0)), 'break')
        self.assertEqual(canvas.yview_scroll.call_args_list, [call(1, 'units')] * 3)

    def test_wheel_over_scrollbar_scrolls_its_list(self):
        tree, bar = Mock(), Mock()
        attach_autohide_scrollbar(tree, bar)
        callbacks = {entry.args[0]: entry.args[1] for entry in bar.bind.call_args_list}
        self.assertEqual(callbacks['<MouseWheel>'](SimpleNamespace(delta=120, num=0)), 'break')
        tree.yview_scroll.assert_called_once_with(-1, 'units')
