import unittest
from unittest.mock import Mock

from macroflow.ui.dialogs.virtual_tree import VirtualRow, VirtualTreeRows, visible_range


class FakeTree:
    def __init__(self):
        self.inserted = []
        self.attached = []
        self.items = {}
        self.item_calls = []
        self.callbacks = []
        self.bindings = {}
        self.selected = ()

    def bind(self, event, callback, add=None):
        self.bindings[event] = callback

    def after_idle(self, callback):
        self.callbacks.append(callback)
        return len(self.callbacks)

    def after_cancel(self, _job):
        pass

    def flush(self):
        callback, self.callbacks = self.callbacks[0], self.callbacks[1:]
        callback()

    def winfo_height(self):
        return 320

    def cget(self, _name):
        return 16

    def insert(self, parent, index, **kwargs):
        self.inserted.append(kwargs["iid"])
        self.attached.append(kwargs["iid"])

    def detach(self, iid):
        if iid in self.attached:
            self.attached.remove(iid)

    def move(self, iid, parent, index):
        if iid in self.attached:
            self.attached.remove(iid)
        self.attached.append(iid)

    def item(self, iid, **kwargs):
        self.items[iid] = kwargs
        self.item_calls.append((iid, kwargs))

    def tag_configure(self, iid, **kwargs):
        pass

    def selection(self):
        return self.selected

    def selection_set(self, items):
        self.selected = tuple(items)

    def yview_moveto(self, fraction):
        assert fraction == 0


def rows(count, kind="normal"):
    return [VirtualRow(str(index), str(index), (index,), (), kind) for index in range(count)]


class VirtualTreeTests(unittest.TestCase):
    def test_visible_range_is_bounded_and_clamped(self):
        self.assertEqual(visible_range(5000, 4999, 16, 3), (4984, 5000))
        self.assertEqual(visible_range(2, 50, 16, 3), (0, 2))

    def test_100_and_5000_rows_create_the_same_number_of_items(self):
        counts = []
        for count in (100, 5000):
            tree = FakeTree()
            view = VirtualTreeRows(tree, Mock(), row_height=20)
            view.set_rows(rows(count))
            tree.flush()
            counts.append(len(tree.inserted))
        self.assertEqual(counts, [19, 19])

    def test_pool_reuses_slots_across_category_switches(self):
        tree = FakeTree()
        view = VirtualTreeRows(tree, Mock(), row_height=20)
        view.set_rows(rows(100))
        tree.flush()
        original = set(tree.inserted)
        view.set_rows(rows(5000))
        tree.flush()
        self.assertEqual(set(tree.inserted), original)
        self.assertEqual([view.key_for_iid(iid) for iid in tree.attached[:3]],
                         ["0", "1", "2"])

    def test_pool_for_each_type_is_ready_before_first_filter_switch(self):
        tree = FakeTree()
        view = VirtualTreeRows(tree, Mock(), row_height=20)
        mixed = [VirtualRow(str(index), str(index), (), (),
                            "adopted" if index % 2 else "unused") for index in range(200)]
        view.set_rows(mixed)
        tree.flush()
        self.assertEqual(len(tree.inserted), 38)
        view.set_rows([row for row in mixed if row.kind == "adopted"])
        tree.flush()
        self.assertEqual(len(tree.inserted), 38)

    def test_multiple_changes_commit_only_once_at_idle(self):
        tree = FakeTree()
        view = VirtualTreeRows(tree, Mock(), row_height=20)
        first, last = Mock(), Mock()
        view.set_rows(rows(100), commit_ui=first)
        view.set_rows(rows(5000), commit_ui=last)
        view.schedule()
        self.assertEqual(len(tree.callbacks), 1)
        tree.flush()
        self.assertEqual(len(tree.inserted), 19)
        first.assert_not_called()
        last.assert_called_once_with()

    def test_category_switch_resets_scroll_to_top(self):
        tree = FakeTree()
        scrollbar = Mock()
        view = VirtualTreeRows(tree, scrollbar, row_height=20)
        view.set_rows(rows(5000))
        tree.flush()
        view.yview("moveto", "0.8")
        tree.flush()
        self.assertGreater(view.start, 0)
        view.set_rows(rows(100))
        tree.flush()
        self.assertEqual(view.start, 0)
        self.assertEqual(view.key_for_iid(tree.attached[0]), "0")
        self.assertEqual(scrollbar.set.call_args.args[0], 0)

    def test_scroll_rebinds_keys_without_creating_more_items(self):
        tree = FakeTree()
        view = VirtualTreeRows(tree, Mock(), row_height=20)
        view.set_rows(rows(5000))
        tree.flush()
        first_slot = tree.attached[0]
        tree.selected = (first_slot,)
        tree.bindings["<<TreeviewSelect>>"]()
        view.yview_scroll(100, "units")
        tree.flush()
        self.assertEqual(view.key_for_iid(tree.attached[0]), "100")
        self.assertEqual(len(tree.inserted), 19)
        view.yview("moveto", 0)
        tree.flush()
        self.assertEqual(view.selected_keys(), ("0",))

    def test_deferred_selection_events_after_scroll_keep_selected_module(self):
        tree = FakeTree()
        view = VirtualTreeRows(tree, Mock(), row_height=20)
        view.set_rows(rows(100))
        tree.flush()
        tree.selected = (tree.attached[2],)
        view._remember_selection()
        view.yview_scroll(40, "units")
        tree.flush()
        # Tk 在 detach/selection_set 之后异步发送 TreeviewSelect。
        view._remember_selection()
        self.assertEqual(view.selected_keys(), ("2",))
        view.yview("moveto", 0)
        tree.flush()
        view._remember_selection()
        self.assertEqual(view.selected_keys(), ("2",))
        self.assertEqual(tree.selected, (tree.attached[2],))
        tree.selected = (tree.attached[5],)
        view._remember_selection()
        self.assertEqual(view.selected_keys(), ("5",))

    def test_entrance_updates_only_first_screen_without_creating_items(self):
        tree = FakeTree()
        animator = Mock()
        view = VirtualTreeRows(tree, Mock(), row_height=20, animator=animator)
        view.set_rows(rows(5000), animate=True)
        tree.flush()
        self.assertEqual(len(tree.inserted), 19)
        self.assertEqual(len(tree.item_calls), 19 + 16)
        self.assertEqual(animator.animate.call_args.args[1], 180)
        animator.animate.call_args.args[2](1)
        self.assertEqual(len(tree.inserted), 19)

    def test_switch_fades_old_rows_before_one_idle_commit(self):
        tree = FakeTree()
        animator = Mock()
        view = VirtualTreeRows(tree, Mock(), row_height=20, animator=animator)
        view.set_rows(rows(100))
        tree.flush()
        view.set_rows(rows(5000), animate=True)
        self.assertEqual(tree.callbacks, [])
        self.assertEqual(animator.animate.call_args.args[1], 90)
        animator.animate.call_args.args[2](1)
        self.assertEqual(len(tree.callbacks), 1)
        tree.flush()
        self.assertEqual(animator.animate.call_args.args[1], 120)
        self.assertEqual(len(tree.inserted), 19)

    def test_hover_and_selection_animate_existing_slot(self):
        tree = FakeTree()
        animator = Mock()
        view = VirtualTreeRows(tree, Mock(), row_height=20, animator=animator)
        view.set_rows(rows(100))
        tree.flush()
        iid = tree.attached[0]
        tree.identify_row = Mock(return_value=iid)
        tree.bindings["<Motion>"](Mock(y=5))
        self.assertEqual(animator.animate.call_args.args[1], 160)
        tree.selected = (iid,)
        tree.bindings["<<TreeviewSelect>>"]()
        self.assertEqual(view.selected_keys(), ("0",))
        self.assertEqual(animator.animate.call_args.args[1], 160)
