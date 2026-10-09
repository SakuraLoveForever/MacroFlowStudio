import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path


class ModuleModifiedSortTests(unittest.TestCase):
    def test_all_categories_default_to_newest_first_and_reverse(self):
        from macroflow.ui.dialogs.module_objects import TemplateRegionManagerDialog
        for category in TemplateRegionManagerDialog.TAB_KEYS:
            with self.subTest(category=category):
                dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
                dialog.objects = {
                    'old': {'name': 'A', 'category': category, 'modified_at': '2026-10-01T10:00:00'},
                    'new': {'name': 'Z', 'category': category, 'modified_at': '2026-10-09T10:00:00'},
                    'unknown': {'name': 'B', 'category': category},
                }
                view = Mock()
                dialog.virtual_trees = {category: view}
                dialog._reload_tree(category, Mock())
                rows = view.set_rows.call_args.args[0]
                self.assertEqual([row.key for row in rows], ['new', 'old', 'unknown'])
                self.assertEqual(rows[0].values[-1], '2026-10-09 10:00:00')
                self.assertEqual(rows[-1].values[-1], '—')
                dialog.sort_direction = 'asc'
                dialog._reload_tree(category, Mock())
                self.assertEqual([row.key for row in view.set_rows.call_args.args[0]], ['unknown', 'old', 'new'])

    def test_time_heading_switches_sort_and_direction(self):
        from macroflow.ui.dialogs.module_objects import TemplateRegionManagerDialog
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.trees = {key: Mock() for key in dialog.TAB_KEYS}
        dialog._reload_trees = Mock()
        dialog.sort_column = 'modified_at'
        dialog.sort_direction = 'desc'
        dialog._toggle_time_sort()
        self.assertEqual(dialog.sort_direction, 'asc')
        for tree in dialog.trees.values():
            self.assertEqual(tree.heading.call_args_list[-1].kwargs['text'], '修改时间 ↑')
        dialog.sort_column = 'name'
        dialog._toggle_time_sort()
        self.assertEqual(dialog.sort_column, 'modified_at')
        self.assertEqual(dialog.sort_direction, 'desc')

    def test_save_only_updates_changed_modules_and_persists_time(self):
        from macroflow.core import storage
        with tempfile.TemporaryDirectory() as folder, patch.object(storage, 'TEMPLATE_REGIONS_PATH', Path(folder) / 'modules.json'):
            objects = {'a': {'name': 'A'}, 'b': {'name': 'B'}}
            storage.save_module_objects(objects)
            first = storage.load_module_objects()
            self.assertTrue(first['a']['modified_at'])
            first['a']['modified_at'] = '2000-01-01T00:00:00'
            first['b']['modified_at'] = '2000-01-01T00:00:00'
            storage.TEMPLATE_REGIONS_PATH.write_text(json.dumps(first), encoding='utf-8')
            objects = storage.load_module_objects()
            storage.save_module_objects(objects)
            self.assertEqual(storage.load_module_objects()['a']['modified_at'], '2000-01-01T00:00:00')
            objects['a']['enabled'] = False
            storage.save_module_objects(objects)
            saved = storage.load_module_objects()
            self.assertNotEqual(saved['a']['modified_at'], '2000-01-01T00:00:00')
            self.assertEqual(saved['b']['modified_at'], '2000-01-01T00:00:00')
            self.assertEqual(objects['a']['modified_at'], saved['a']['modified_at'])
