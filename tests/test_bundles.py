import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from macroflow.core import storage
from macroflow.core.models import MacroScript, Workflow
from macroflow.core.bundles import export_bundle, import_bundle


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        for name, value in {
            'BASE_DIR': self.root, 'SCRIPTS_DIR': self.root / 'scripts',
            'WORKFLOWS_DIR': self.root / 'workflows', 'IMAGES_DIR': self.root / 'images',
            'TEMPLATE_REGIONS_PATH': self.root / 'modules.json',
        }.items():
            p = patch.object(storage, name, value)
            p.start()
            self.addCleanup(p.stop)
        storage.ensure_dirs()

    def fixture(self):
        image = self.root / 'images' / 'sample.png'
        image.write_bytes(b'image fixture')
        child = storage.save_script(MacroScript(name='child', actions=[
            {'type': 'script_ref', 'script': 'scripts/child.json'},
            {'type': 'module', 'module_key': 'module:main', 'module_ref': True},
        ]))
        storage.save_module_objects({
            'module:main': {'name': 'main', 'template': 'images/sample.png',
                            'on_success_actions': [{'type': 'module', 'module_ref': True, 'module_key': 'module:other'}]},
            'module:other': {'name': 'other', 'template': 'images/sample.png',
                             'on_success_actions': [{'type': 'script_ref', 'script': 'scripts/child.json'}]},
            'module:unused': {'name': 'unused', 'template': 'images/missing.png'},
        })
        return child

    def test_workflow_round_trip_includes_only_complete_dependency_closure(self):
        child = self.fixture()
        wf = Workflow(name='flow', steps=[{'script': str(child), 'repeats': 3}])
        bundle = self.root / 'flow.zip'
        export_bundle(bundle, workflow=wf)
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            self.assertEqual(sum(name.startswith('images/') for name in names), 1)
            self.assertEqual(sum(name.startswith('scripts/') for name in names), 1)
            modules = json.loads(archive.read('modules.json'))
            self.assertEqual(len(modules), 2)
            self.assertNotIn(str(self.root), archive.read('modules.json').decode())
        kind, entry = import_bundle(bundle)
        self.assertEqual(kind, 'workflow')
        imported = storage.load_workflow(entry)
        imported_script = storage.load_script(storage.resolve_path(imported.steps[0]['script']))
        key = imported_script.actions[1]['module_key']
        self.assertNotEqual(key, 'module:main')
        objects = storage.load_module_objects()
        self.assertEqual(objects['module:main']['template'], 'images/sample.png')
        self.assertTrue(storage.resolve_path(objects[key]['template']).is_file())
        self.assertIn(objects[key]['on_success_actions'][0]['module_key'], objects)
        export_bundle(self.root / 'again.zip', workflow=imported)
        self.assertEqual(import_bundle(self.root / 'again.zip')[0], 'workflow')

    def test_unsaved_script_exports_and_names_do_not_collide(self):
        script = MacroScript(name='当前脚本', actions=[{'type': 'delay', 'ms': 3}])
        export_bundle(self.root / 'script.zip', script=script)
        kind, path = import_bundle(self.root / 'script.zip')
        _, second = import_bundle(self.root / 'script.zip')
        self.assertEqual(kind, 'script')
        self.assertNotEqual(path, second)
        self.assertEqual(storage.load_script(path).actions[0]['ms'], 3)

    def test_missing_dependencies_abort_export(self):
        for action in ({'type': 'script_ref', 'script': 'missing.json'},
                       {'type': 'find_image', 'template': 'missing.png'},
                       {'type': 'module', 'module_ref': True, 'module_key': 'module:missing'}):
            with self.subTest(action=action), self.assertRaises(ValueError):
                export_bundle(self.root / 'bad.zip', script=MacroScript(actions=[action]))
        self.assertFalse((self.root / 'bad.zip').exists())

    def malicious(self, extra=None, script=None):
        path = self.root / 'bad.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('manifest.json', json.dumps({'format': 'macroflow-bundle', 'version': 1,
                             'kind': 'script', 'entry': 'scripts/main.json'}))
            archive.writestr('scripts/main.json', json.dumps(script or MacroScript().to_dict()))
            archive.writestr('modules.json', '{}')
            if extra:
                archive.writestr(extra, 'bad')
        return path

    def test_traversal_and_absolute_archive_members_are_rejected(self):
        for name in ('../outside', '/outside', 'C:/outside', 'images/../../outside', 'images\\bad'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                import_bundle(self.malicious(extra=name))
        self.assertFalse((self.root / 'imports').exists())

    def test_external_or_missing_references_are_rejected_before_install(self):
        for ref in ('C:/external.json', '../external.json', 'scripts/missing.json'):
            script = MacroScript(actions=[{'type': 'script_ref', 'script': ref}]).to_dict()
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                import_bundle(self.malicious(script=script))
        self.assertFalse((self.root / 'imports').exists())

    def test_unknown_version_is_rejected(self):
        path = self.root / 'version.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('manifest.json', json.dumps({'format': 'macroflow-bundle', 'version': 999}))
        with self.assertRaises(ValueError):
            import_bundle(path)

    def test_root_name_cannot_overwrite_dependency(self):
        child = storage.save_script(MacroScript(name='child', actions=[{'type': 'delay', 'ms': 9}]))
        root = MacroScript(name='0001_child', actions=[{'type': 'script_ref', 'script': str(child)}])
        export_bundle(self.root / 'collision.zip', script=root)
        _, entry = import_bundle(self.root / 'collision.zip')
        imported = storage.load_script(entry)
        nested = storage.load_script(storage.resolve_path(imported.actions[0]['script']))
        self.assertEqual(nested.actions, [{'type': 'delay', 'ms': 9}])

    def test_registry_replace_failure_preserves_existing_data_and_removes_import(self):
        storage.save_module_objects({'module:existing': {'name': 'keep'}})
        before = storage.TEMPLATE_REGIONS_PATH.read_bytes()
        export_bundle(self.root / 'safe.zip', script=MacroScript())
        with patch.object(storage.os, 'replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                import_bundle(self.root / 'safe.zip')
        self.assertEqual(storage.TEMPLATE_REGIONS_PATH.read_bytes(), before)
        self.assertEqual(list((self.root / 'imports').iterdir()), [])

    def test_image_keyed_module_keeps_relocated_registry_key(self):
        (storage.IMAGES_DIR / 'old.png').write_bytes(b'image')
        storage.save_module_objects({'images/old.png': {'name': 'legacy', 'template': 'images/old.png'}})
        export_bundle(self.root / 'old.zip', script=MacroScript(actions=[
            {'type': 'find_image', 'template': 'images/old.png'}]))
        _, entry = import_bundle(self.root / 'old.zip')
        image = storage.load_script(entry).actions[0]['template']
        self.assertIn(image, storage.load_module_objects())
