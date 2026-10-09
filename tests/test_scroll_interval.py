import unittest
from unittest.mock import Mock, call, patch

from macroflow.execution.player import MacroPlayer, PlaybackStopped
from macroflow.ui.dialogs.actions import ScrollDialog, ScrollSequenceDialog
from macroflow.core.models import SCROLL_UP_LABEL
from tests.helpers.core import FakeTree, FakeVar


class ScrollIntervalTests(unittest.TestCase):
    def test_custom_interval_applies_between_notches_and_direction_changes(self):
        player = MacroPlayer()
        events = []
        player._wait = lambda ms: events.append(("wait", ms))
        player.wait_while_paused = Mock()
        with patch("macroflow.execution.player.core.send_scroll", side_effect=lambda x, y: events.append((x, y))):
            player._execute_scroll_steps([(0, 2), (0, 0), (0, -1)], 350)
        self.assertEqual(events, [(0, 1), ("wait", 350), (0, 1), ("wait", 350), (0, -1)])
        self.assertEqual(player.wait_while_paused.call_count, 3)

    def test_zero_interval_and_single_notch_have_no_extra_wait(self):
        player = MacroPlayer()
        player._wait = Mock()
        player.wait_while_paused = Mock()
        with patch("macroflow.execution.player.core.send_scroll") as scroll:
            player._execute_scroll_steps([(0, 1)], 200)
            player._wait.assert_not_called()
            player._execute_scroll_steps([(0, -2)], 0)
        player._wait.assert_called_once_with(0)
        self.assertEqual(scroll.call_args_list, [call(0, 1), call(0, -1), call(0, -1)])

    def test_stop_during_interval_prevents_next_notch(self):
        player = MacroPlayer()
        player.wait_while_paused = Mock()
        player._wait = Mock(side_effect=lambda ms: player.stop_event.set())
        with patch("macroflow.execution.player.core.send_scroll") as scroll:
            with self.assertRaises(PlaybackStopped):
                player._execute_scroll_steps([(0, 5)], 200)
        scroll.assert_called_once_with(0, 1)

    def test_both_dialogs_save_interval_and_reject_invalid_values(self):
        for cls in (ScrollDialog, ScrollSequenceDialog):
            for value in ("0", "375", "-1", "bad"):
                with self.subTest(dialog=cls.__name__, value=value):
                    dialog = cls.__new__(cls)
                    dialog._source = {}
                    dialog.x, dialog.y = FakeVar("1"), FakeVar("2")
                    dialog.delay, dialog.interval = FakeVar("0"), FakeVar(value)
                    dialog.clicks, dialog.direction = FakeVar("2"), FakeVar(SCROLL_UP_LABEL)
                    dialog.tree = FakeTree()
                    dialog.tree.insert("", "end", values=(SCROLL_UP_LABEL, "2"))
                    dialog.destroy = Mock()
                    with patch("macroflow.ui.dialogs.actions.show_floating_notice") as notice:
                        dialog.save()
                    if value in ("0", "375"):
                        self.assertEqual(dialog.result["interval_ms"], int(value))
                        dialog.destroy.assert_called_once()
                        notice.assert_not_called()
                    else:
                        dialog.destroy.assert_not_called()
                        notice.assert_called_once()
