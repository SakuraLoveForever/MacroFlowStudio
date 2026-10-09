import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from tests.helpers.core import add_src_to_path


class PythonScriptTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'logic.py'

    def write(self, text):
        self.path.write_text(text, encoding='utf-8')

    def test_load_does_not_execute_and_save_never_overwrites_python(self):
        from macroflow.core.storage import load_script, save_script
        self.write('raise RuntimeError("do not execute while loading")\ndef main(ctx): pass\n')
        script = load_script(self.path)
        self.assertEqual(script.actions, [{'type': 'python_script', 'path': str(self.path.resolve())}])
        with self.assertRaises(ValueError):
            save_script(script, self.path)
        self.assertTrue(self.path.read_text().startswith('raise'))

    def player(self):
        from macroflow.execution.player import MacroPlayer
        player = MacroPlayer(on_log=Mock())
        player._execute_action = Mock(return_value=None)
        return player

    def run_code(self, player):
        from macroflow.execution.python_script import run_python_script
        return run_python_script(self.path, player, None, set(), 0)

    def test_code_calls_actions_and_reloads_latest_source(self):
        player = self.player()
        self.write('def main(ctx):\n    for key in "2v": ctx.press(key)\n    ctx.wait(ms=50)\n    print("done")\n')
        self.run_code(player)
        self.assertEqual([c.args[0]['type'] for c in player._execute_action.call_args_list], ['key_press', 'key_press', 'delay'])
        self.assertEqual(player._execute_action.call_args_list[0].args[0]['vk'], 50)
        player._execute_action.reset_mock()
        self.write('def main(ctx):\n    ctx.press("F2")\n')
        self.run_code(player)
        self.assertEqual(player._execute_action.call_args.args[0]['vk'], 113)

    def test_error_contains_filename_and_line(self):
        self.write('def main(ctx):\n    raise ValueError("bad logic")\n')
        with self.assertRaisesRegex(RuntimeError, 'logic.py') as caught:
            self.run_code(self.player())
        self.assertIn('line 2', str(caught.exception))
        self.assertIn('bad logic', str(caught.exception))

    def test_stop_terminates_busy_loop(self):
        from macroflow.execution.player.control import PlaybackStopped
        player = self.player()
        self.write('def main(ctx):\n    while True: pass\n')
        timer = threading.Timer(0.8, player.stop)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        with self.assertRaises(PlaybackStopped):
            self.run_code(player)
        self.assertLess(time.monotonic() - started, 4)

    def test_rpc_returns_results_and_relative_paths(self):
        player = self.player()
        self.write('def main(ctx):\n    assert ctx.exists("target.png")\n    assert ctx.read_text() == "hello"\n    ctx.run_script("next.json", repeats=2)\n')
        player._execute_action.return_value = None
        from unittest.mock import patch
        with patch('macroflow.core.image_match.find_template', return_value={'x': 1}), patch('macroflow.core.ocr.recognize_region', return_value='hello'):
            self.run_code(player)
        self.assertEqual(player._execute_action.call_args.args[0]['script'], str(self.path.parent / 'next.json'))

    def test_pure_code_respects_pause_and_resume(self):
        player = self.player()
        self.write('def main(ctx):\n    ctx.press("a")\n    ctx.press("b")\n')
        first = threading.Event()
        def action(*args, **kwargs):
            if not first.is_set():
                player.pause()
                first.set()
        player._execute_action.side_effect = action
        errors = []
        def work():
            try: self.run_code(player)
            except BaseException as exc: errors.append(exc)
        thread = threading.Thread(target=work)
        thread.start()
        try:
            self.assertTrue(first.wait(4))
            time.sleep(0.15)
            self.assertEqual(player._execute_action.call_count, 1)
        finally:
            player.resume()
            thread.join(4)
            if thread.is_alive(): player.stop(); thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(player._execute_action.call_count, 2)

    def test_reference_python_file_and_recursion_detection(self):
        from macroflow.execution.player import MacroPlayer
        from macroflow.execution.python_script import run_python_script
        self.write('def main(ctx):\n    ctx.log("referenced")\n')
        player = MacroPlayer()
        player._execute_action({'type': 'script_ref', 'script': str(self.path)}, None)
        with self.assertRaisesRegex(RuntimeError, '循环引用'):
            run_python_script(self.path, player, None, {str(self.path)}, 0)

    def test_export_declared_assets_and_remap_module_binding_without_executing(self):
        import json
        import zipfile
        from unittest.mock import patch
        from macroflow.core import storage
        from macroflow.core.bundles import export_bundle, import_bundle
        self.write('MACROFLOW = {"files": ["target.png", "next.json"], "modules": ["module:original"]}\ndef main(ctx):\n    raise RuntimeError("never run during import")\n')
        root = self.path.parent
        (root / 'target.png').write_bytes(b'asset')
        (root / 'next.json').write_text(json.dumps({'name': 'next', 'actions': []}), encoding='utf-8')
        with patch.object(storage, 'BASE_DIR', root), patch.object(storage, 'TEMPLATE_REGIONS_PATH', root / 'modules.json'):
            storage.save_module_objects({'module:original': {'name': 'attack', 'category': 'switch'}})
            script = storage.load_script(self.path)
            bundle = export_bundle(root / 'export.zip', script=script)
            with zipfile.ZipFile(bundle) as archive:
                self.assertIn('python/0001/target.png', archive.namelist())
                self.assertIn('python/0001/next.json', archive.namelist())
            kind, entry = import_bundle(bundle)
            action = storage.load_script(entry).actions[0]
            self.assertNotEqual(action['module_bindings']['module:original'], 'module:original')
            self.assertEqual(storage.resolve_path(action['path']).parent.joinpath('target.png').read_bytes(), b'asset')
            # Re-export uses the imported binding, even while the old ID remains locally.
            again = export_bundle(root / 'again.zip', script=storage.load_script(entry))
            with zipfile.ZipFile(again) as archive:
                modules = json.loads(archive.read('modules.json'))
                self.assertIn(action['module_bindings']['module:original'], modules)
                self.assertNotIn('module:original', modules)

    def test_export_processes_each_entry_manifest_in_the_same_directory(self):
        import zipfile
        from unittest.mock import patch
        from macroflow.core import storage
        from macroflow.core.models import MacroScript
        from macroflow.core.bundles import export_bundle
        root = self.path.parent
        self.write('MACROFLOW = {"files": ["first.txt"]}\ndef main(ctx): pass\n')
        second = root / 'second.py'
        second.write_text('MACROFLOW = {"files": ["second.txt"]}\ndef main(ctx): pass\n', encoding='utf-8')
        for name in ('first.txt', 'second.txt'):
            (root / name).write_text(name, encoding='utf-8')
        script = MacroScript(actions=[storage.load_script(self.path).actions[0], storage.load_script(second).actions[0]])
        with patch.object(storage, 'BASE_DIR', root), patch.object(storage, 'TEMPLATE_REGIONS_PATH', root / 'modules.json'):
            bundle = export_bundle(root / 'both.zip', script=script)
        with zipfile.ZipFile(bundle) as archive:
            self.assertIn('python/0001/first.txt', archive.namelist())
            self.assertIn('python/0001/second.txt', archive.namelist())

    def test_load_rejects_missing_entry_and_discovery_includes_python(self):
        from macroflow.core.storage import load_script
        from macroflow.ui.dialogs.module_objects import _valid_scripts_in
        self.write('def main(ctx): pass\n')
        self.assertIn(self.path.resolve(), _valid_scripts_in(self.path.parent))
        self.write('value = 1\n')
        with self.assertRaisesRegex(ValueError, 'main'):
            load_script(self.path)
