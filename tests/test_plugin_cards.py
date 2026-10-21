import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from macroflow.ui.app import plugins_page as ui
from macroflow.ui.app import disconnect_monitor as monitor_ui


class PluginCardTests(unittest.TestCase):
    def test_right_click_opens_settings_without_expanding_the_page(self):
        with patch.object(ui.tk.Canvas, '__init__', return_value=None), patch.object(ui.tk.Canvas, 'bind') as bind, patch.object(ui.PluginCard, '_draw'), patch.object(ui.tk, 'BooleanVar'), patch.object(ui.ttk, 'Checkbutton'):
            edit = Mock()
            card = ui.PluginCard(Mock(), '定时条件执行', edit, Mock())
        handlers = {call.args[0]: call.args[1] for call in bind.call_args_list}
        self.assertIn('<Button-3>', handlers)
        handlers['<Button-3>'](Mock())
        edit.assert_called_once_with()
        self.assertEqual(card.title, '定时条件执行')

    def test_card_checkbox_toggles_independently_of_right_click(self):
        variable, toggle, edit = Mock(), Mock(), Mock()
        variable.get.return_value = True
        with patch.object(ui.tk.Canvas, '__init__', return_value=None), patch.object(ui.tk.Canvas, 'bind'), patch.object(ui.PluginCard, '_draw'), patch.object(ui.tk, 'BooleanVar', return_value=variable), patch.object(ui.ttk, 'Checkbutton') as checkbox:
            card = ui.PluginCard(Mock(), '插件', edit, toggle)
        checkbox.call_args.kwargs['command']()
        toggle.assert_called_once_with(True)
        edit.assert_not_called()
        card._draw = Mock()
        card.update_status('每 1 h', '待配置', True)
        variable.set.assert_called_once_with(True)

    def test_edit_opens_separate_dialog_and_refreshes_summary_on_return(self):
        page = object.__new__(ui.PluginsPage)
        page.app = SimpleNamespace(root=Mock())
        page.rebuild_cards = Mock()
        with patch.object(ui, 'PluginSettingsDialog') as dialog:
            page.edit('interval')
        dialog.assert_called_once_with(page.app.root, page.app, 'interval')
        dialog.return_value.show.assert_called_once_with()
        page.rebuild_cards.assert_called_once_with()

    def test_cards_reflow_between_one_two_and_three_columns(self):
        page = object.__new__(ui.PluginsPage)
        page.cards = [Mock(), Mock()]
        page._columns = 0
        page.grid_body = Mock()
        with patch.object(ui, 'px', side_effect=lambda value: value):
            for width, expected in ((240, 1), (600, 2), (1000, 3), (420, 1)):
                page.relayout(SimpleNamespace(width=width))
                self.assertEqual(page._columns, expected)
                self.assertEqual(page.cards[1].grid.call_args.kwargs['row'], 1 // expected)
                self.assertEqual(page.cards[1].grid.call_args.kwargs['column'], 1 % expected)
            count = page.cards[0].grid.call_count
            page.relayout(SimpleNamespace(width=420))
            self.assertEqual(page.cards[0].grid.call_count, count)

    def test_saved_disconnect_monitor_starts_without_opening_settings_and_is_reused(self):
        app = SimpleNamespace(app_settings={'disconnect_monitor_enabled': True}, root=Mock(), _ui=Mock(), _log=Mock())
        with patch.object(monitor_ui, 'DisconnectMonitor') as factory:
            first = monitor_ui.get_disconnect_monitor(app)
            self.assertIs(first, monitor_ui.get_disconnect_monitor(app))
        factory.assert_called_once()
        first.start.assert_called_once_with()
        callback = app.root.bind.call_args.args[1]
        callback(SimpleNamespace(widget=app.root))
        first.stop.assert_called_once_with()
