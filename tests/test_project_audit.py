"""Regression checks for the project audit; no windows or OS input are created."""
import io
import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np

from macroflow.core import storage
from macroflow.core.models import MacroScript, Workflow
from macroflow.execution.player import MacroPlayer
from macroflow.execution.player.control import PlaybackStopped
from macroflow.ui.app.guards import GuardsMixin
from macroflow.ui.update_queue import UIUpdateQueue
from tests.test_update_queue import FakeRoot


class UpdateDeliveryTests(unittest.TestCase):
    def test_distinct_functions_with_same_batch_key_keep_their_own_events(self):
        queue = UIUpdateQueue(FakeRoot())
        seen = []
        queue.submit(lambda items: seen.append(('first', items)), 'a', batch_key='log')
        queue.submit(lambda items: seen.append(('second', items)), 'b', batch_key='log')
        queue.flush()
        self.assertEqual(seen, [('first', ['a']), ('second', ['b'])])

    def test_distinct_builtin_methods_on_same_receiver_are_not_merged(self):
        queue = UIUpdateQueue(FakeRoot())
        seen = []
        queue.submit(seen.append, 'a', batch_key='log')
        queue.submit(seen.extend, 'b', batch_key='log')
        queue.flush()
        self.assertEqual(seen, [['a'], 'b'])

    def test_failed_callback_does_not_drop_later_events_or_stop_the_pump(self):
        root = FakeRoot()
        errors = []
        root.report_callback_exception = lambda kind, error, tb: errors.append(error)
        queue = UIUpdateQueue(root)
        queue.start()
        seen = []

        def fail():
            raise ValueError('failed update')

        queue.submit(fail, urgent=True)
        queue.submit(seen.append, 'following event')
        root.calls[0][2]()
        self.assertEqual(seen, ['following event'])
        self.assertEqual([str(error) for error in errors], ['failed update'])
        self.assertEqual(len(root.calls), 2)  # FakeRoot retains fired timers.
        queue.submit(seen.append, 'next tick')
        root.calls[-1][2]()
        self.assertEqual(seen, ['following event', 'next tick'])


class RecognitionBoundaryTests(unittest.TestCase):
    def test_ocr_crops_only_the_intersection_with_the_captured_frame(self):
        frame = np.arange(100).reshape(10, 10)
        for region, expected, origin in (
            ((95, 198, 8, 6), frame[:4, :3], (100, 200)),
            ((95, 198, 3, 2), None, None),
            ((108, 208, 8, 6), frame[8:, 8:], (108, 208)),
            ((101, 202, 3, 4), frame[2:6, 1:4], (101, 202)),
        ):
            with self.subTest(region=region):
                cropped, actual_origin = GuardsMixin._ocr_region_in_frame(frame, (100, 200), region)
                self.assertEqual(actual_origin, origin)
                if expected is None:
                    self.assertIsNone(cropped)
                else:
                    np.testing.assert_array_equal(cropped, expected)

    def test_failed_resolution_change_requires_the_requested_refresh_rate(self):
        player = MacroPlayer()
        for actual, refresh, expected in ((60, 144, False), (144, 144, True),
                                          (143, 144, True), (60, 0, True)):
            with self.subTest(actual=actual, requested=refresh), patch(
                'macroflow.execution.player.window.get_display_resolution_for_window',
                side_effect=[(1280, 720, 60), (1920, 1080, actual)],
            ), patch('macroflow.execution.player.window.set_display_resolution_for_window',
                     return_value=False):
                self.assertEqual(player._ensure_display_resolution(1, 1920, 1080, refresh, 'test'), expected)


class PlaybackLifecycleTests(unittest.TestCase):
    def test_zero_delay_text_stops_before_sending_another_character(self):
        player = MacroPlayer()
        sent = []

        def send(char, delay):
            sent.append(char)
            player.stop()

        with patch('macroflow.execution.player.core.send_text', side_effect=send):
            with self.assertRaises(PlaybackStopped):
                player._execute_action({'type': 'text', 'text': 'abc', 'char_delay_ms': 0}, None)
        self.assertEqual(sent, ['a'])

    def test_screen_setup_error_does_not_leave_player_permanently_busy(self):
        player = MacroPlayer()
        with patch('macroflow.execution.player.core.get_playback_screen_rect', side_effect=OSError('display unavailable')):
            with self.assertRaisesRegex(OSError, 'display unavailable'):
                player.play([], source_screen={'width': 1920, 'height': 1080})
        self.assertFalse(player.running)
        player.play([])

    def test_timing_callback_error_does_not_block_the_next_playback(self):
        def fail(_metrics):
            raise ValueError('timing callback failed')

        player = MacroPlayer(on_timing=fail)
        with self.assertRaisesRegex(ValueError, 'timing callback failed'):
            player.play([])
        self.assertFalse(player.running)
        player.on_timing = None
        player.play([])

    def test_scope_exit_error_still_releases_held_inputs_and_clears_running_state(self):
        def fail(_scope):
            raise ValueError('scope exit failed')

        player = MacroPlayer(on_script_scope_enter=lambda *args: ('scope',),
                             on_script_scope_exit=fail)
        sent = []
        with patch('macroflow.execution.player.core.send_key',
                   side_effect=lambda vk, down: sent.append((vk, down))):
            with self.assertRaisesRegex(ValueError, 'scope exit failed'):
                player.play([{'type': 'key', 'vk': 65, 'down': True}])
        self.assertEqual(sent, [(65, True), (65, False)])
        self.assertFalse(player.running)


class StorageIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        stack = self.enterContext(ExitStack())
        for name, relative in (
            ('SCRIPTS_DIR', 'scripts'), ('WORKFLOWS_DIR', 'workflows'),
            ('IMAGES_DIR', 'images'), ('SCRIPT_BACKUPS_DIR', 'backups'),
            ('OVERWRITTEN_BACKUPS_DIR', 'overwritten'), ('BASE_DIR', '.'),
            ('SETTINGS_PATH', 'app_settings.json'), ('MODULE_SETTINGS_PATH', 'module_settings.json'),
            ('TEMPLATE_REGIONS_PATH', 'template_regions.json'),
        ):
            stack.enter_context(patch.object(storage, name, self.root / relative))
        storage.ensure_dirs()

    def saves(self):
        source = storage.SCRIPTS_DIR / 'source.json'
        return (
            (self.root / 'script.json', lambda: storage.save_script(MacroScript(name='new'), self.root / 'script.json')),
            (self.root / 'workflow.json', lambda: storage.save_workflow(Workflow(name='new'), self.root / 'workflow.json')),
            (storage.SETTINGS_PATH, lambda: storage.save_app_settings({'name': 'new'})),
            (storage.MODULE_SETTINGS_PATH, lambda: storage.save_module_settings({'name': 'new'})),
            (storage.SCRIPT_BACKUPS_DIR / 'source.json', lambda: storage.backup_script(source, {'name': 'new'})),
            (storage.TEMPLATE_REGIONS_PATH, lambda: storage.save_module_objects({'module:new': {'name': 'new'}})),
        )

    def test_partial_write_failure_keeps_each_previous_json_file_intact(self):
        original_open = io.open

        class FailingWriter:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def write(self, text):
                self.stream.write(text[:max(1, len(text) // 2)])
                raise OSError('disk full')

        def faulty_open(file, mode='r', *args, **kwargs):
            stream = original_open(file, mode, *args, **kwargs)
            return FailingWriter(stream) if mode == 'w' else stream

        for path, save in self.saves():
            with self.subTest(file=path.name):
                before = b'{"name": "old"}'
                path.write_bytes(before)
                files_before = set(path.parent.iterdir())
                with patch.object(io, 'open', side_effect=faulty_open), self.assertRaisesRegex(OSError, 'disk full'):
                    save()
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(set(path.parent.iterdir()), files_before)

    def test_replace_failure_keeps_previous_data_and_removes_temporary_files(self):
        for path, save in self.saves():
            with self.subTest(file=path.name):
                path.write_text('{"name": "old"}', encoding='utf-8')
                files_before = set(path.parent.iterdir())
                with patch.object(storage.os, 'replace', side_effect=OSError('replace failed')):
                    with self.assertRaisesRegex(OSError, 'replace failed'):
                        save()
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'name': 'old'})
                self.assertEqual(set(path.parent.iterdir()), files_before)

    def test_overwrites_at_the_same_timestamp_keep_both_historical_versions(self):
        source = storage.SCRIPTS_DIR / 'script.json'
        with patch.object(storage, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 13, 10, 0, 0)
            source.write_text('first', encoding='utf-8')
            first = storage.archive_overwritten_script(source)
            source.write_text('second', encoding='utf-8')
            second = storage.archive_overwritten_script(source)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_text(encoding='utf-8'), 'first')
        self.assertEqual(second.read_text(encoding='utf-8'), 'second')

    def test_failed_file_copy_preserves_the_previous_backup(self):
        source = storage.SCRIPTS_DIR / 'source.json'
        source.write_bytes(b'new source')
        target = storage.SCRIPT_BACKUPS_DIR / 'source.json'
        target.write_bytes(b'previous backup')

        def partial_copy(_source, destination):
            Path(destination).write_bytes(b'partial')
            raise OSError('copy failed')

        with patch.object(storage.shutil, 'copy2', side_effect=partial_copy):
            with self.assertRaisesRegex(OSError, 'copy failed'):
                storage.backup_script(source)
        self.assertEqual(target.read_bytes(), b'previous backup')
        self.assertEqual(list(target.parent.iterdir()), [target])
