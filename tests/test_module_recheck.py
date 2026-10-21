"""识别确认：首次识别结束后按模块设定等待，再做第二次识别。"""
import unittest
from unittest.mock import Mock, patch

from macroflow.core.storage import DEFAULT_MODULE_RECHECK_DELAY_MS, _normalize_object
from macroflow.execution.player import MacroPlayer, PlaybackStopped
from tests.test_global_detect import GuardTestHelpers
from tests.helpers.patches import package_patch


def match(x):
    return dict(x=x, y=20, width=30, height=40, center_x=x + 15, center_y=40, score=0.95)


class ModuleRecheckTests(GuardTestHelpers, unittest.TestCase):
    def test_module_defaults_keep_pre_recognition_wait(self):
        obj = _normalize_object({'start_delay_ms': 8000, 'delay_ms': 9000}, 'module:x')
        self.assertFalse(obj['recheck_enabled'])
        self.assertEqual(obj['recheck_delay_ms'], 5000)
        self.assertEqual(DEFAULT_MODULE_RECHECK_DELAY_MS, 5000)
        self.assertEqual(obj['start_delay_ms'], 8000)
        self.assertNotIn('delay_ms', obj)

    def test_obsolete_fixed_three_second_flag_is_dropped_without_migration(self):
        obj = _normalize_object(
            {'recheck_after_3s': True, 'recheck_delay_ms': 9000}, 'module:x',
        )
        self.assertNotIn('recheck_after_3s', obj)
        self.assertFalse(obj['recheck_enabled'])
        self.assertEqual(obj['recheck_delay_ms'], 9000)

    def test_module_recheck_delay_is_clamped_to_one_to_six_hundred_seconds(self):
        self.assertEqual(
            _normalize_object({'recheck_delay_ms': 0}, 'module:x')['recheck_delay_ms'], 1000,
        )
        self.assertEqual(
            _normalize_object({'recheck_delay_ms': 999999}, 'module:x')['recheck_delay_ms'], 600000,
        )
        self.assertEqual(
            _normalize_object({'recheck_delay_ms': 12500}, 'module:x')['recheck_delay_ms'], 12500,
        )

    def test_image_click_uses_second_observation_even_if_first_misses(self):
        for first, second in ((None, match(200)), (match(10), match(200)), (match(10), None)):
            with self.subTest(first=first, second=second):
                player = MacroPlayer()
                events = []
                player._wait = Mock(side_effect=lambda ms: events.append(('wait', ms)))
                obj = dict(template='images/x.png', recheck_enabled=True,
                           after_action='click_match', not_found_timeout_ms=0)
                results = iter((first, second))

                def probe(*args, **kwargs):
                    events.append(('recognize', None))
                    return next(results)

                with package_patch('player', 'registered_module_object', return_value=obj), \
                     package_patch('player', 'find_template', side_effect=probe), \
                     package_patch('player', 'show_overlay'), \
                     package_patch('player', 'send_move_absolute') as move, \
                     package_patch('player', 'send_button') as button:
                    player._execute_image(dict(module_ref=True, module_key='module:x'), None)
                self.assertEqual(events[:3], [('recognize', None), ('wait', 5000), ('recognize', None)])
                if second:
                    move.assert_called_once_with(215, 40)
                    self.assertEqual(button.call_count, 2)
                else:
                    button.assert_not_called()

    def test_image_recheck_waits_for_module_configured_seconds(self):
        player = MacroPlayer()
        player._wait = Mock()
        obj = dict(template='images/x.png', recheck_enabled=True, recheck_delay_ms=8000,
                   after_action='click_match', not_found_timeout_ms=0)
        with package_patch('player', 'registered_module_object', return_value=obj), \
             package_patch('player', 'find_template', return_value=match(10)) as probe, \
             package_patch('player', 'show_overlay'):
            player._execute_image(dict(module_ref=True, module_key='module:x'), None)
        self.assertEqual(probe.call_count, 2)
        self.assertIn(8000, [item.args[0] for item in player._wait.call_args_list])

    def test_stop_during_recheck_wait_prevents_second_read_and_click(self):
        player = MacroPlayer()
        player._wait = Mock(side_effect=PlaybackStopped())
        obj = dict(template='images/x.png', recheck_enabled=True, after_action='click_match')
        with package_patch('player', 'registered_module_object', return_value=obj), \
             package_patch('player', 'find_template', return_value=match(10)) as probe, \
             package_patch('player', 'send_button') as button:
            with self.assertRaises(PlaybackStopped):
                player._execute_image(dict(module_ref=True, module_key='module:x'), None)
        self.assertEqual(probe.call_count, 1)
        player._wait.assert_called_once_with(5000)
        button.assert_not_called()

    def test_global_recheck_does_not_block_other_guards_and_uses_fresh_result(self):
        for first in (False, True):
            app = self._make_guard_app()
            guard = self._make_guard('images/x.png', recheck_enabled=True)
            app._guard_image_detect = Mock(side_effect=[(first, match(10) if first else None), (True, match(200))])
            with patch('macroflow.ui.app.guards.time.perf_counter', return_value=100):
                self.assertIsNone(app._evaluate_one_guard(guard, None, None, 100))
            self.assertIsNone(app._evaluate_one_guard(guard, None, None, 104.9))
            self.assertEqual(app._guard_image_detect.call_count, 1)
            hit = app._evaluate_one_guard(guard, None, None, 105)
            self.assertEqual(hit['click'], (215, 40))
            self.assertEqual(app._guard_image_detect.call_count, 2)

    def test_global_recheck_uses_module_configured_delay(self):
        app = self._make_guard_app()
        guard = self._make_guard('images/x.png', recheck_enabled=True, recheck_delay_ms=7000)
        app._guard_image_detect = Mock(return_value=(True, match(10)))
        with patch('macroflow.ui.app.guards.time.perf_counter', return_value=100):
            self.assertIsNone(app._evaluate_one_guard(guard, None, None, 100))
        self.assertIsNone(app._evaluate_one_guard(guard, None, None, 106.9))
        self.assertIsNotNone(app._evaluate_one_guard(guard, None, None, 107))

    def test_global_recheck_wait_starts_after_first_recognition_finishes(self):
        app = self._make_guard_app()
        guard = self._make_guard('images/x.png', recheck_enabled=True)
        app._guard_image_detect = Mock(return_value=(True, match(10)))
        with patch('macroflow.ui.app.guards.time.perf_counter', return_value=105):
            self.assertIsNone(app._evaluate_one_guard(guard, None, None, 100))
        self.assertIsNone(app._evaluate_one_guard(guard, None, None, 109.9))
        self.assertIsNotNone(app._evaluate_one_guard(guard, None, None, 110))

    def test_global_second_miss_prevents_click(self):
        app = self._make_guard_app()
        guard = self._make_guard('images/x.png', recheck_enabled=True)
        app._guard_image_detect = Mock(side_effect=[(True, match(10)), (False, None)])
        with patch('macroflow.ui.app.guards.time.perf_counter', return_value=100):
            self.assertIsNone(app._evaluate_one_guard(guard, None, None, 100))
        self.assertIsNone(app._evaluate_one_guard(guard, None, None, 105))
        self.assertEqual(app.global_detect_trigger_count, 0)

    def test_default_module_recognizes_once_without_recheck_wait(self):
        player = MacroPlayer()
        player._wait = Mock()
        obj = dict(template='images/x.png', after_action='continue')
        with package_patch('player', 'registered_module_object', return_value=obj), \
             package_patch('player', 'find_template', return_value=match(10)) as probe, \
             package_patch('player', 'show_overlay'):
            player._execute_image(dict(module_ref=True, module_key='module:x'), None)
        self.assertEqual(probe.call_count, 1)
        self.assertNotIn(5000, [item.args[0] for item in player._wait.call_args_list])

    def test_text_recheck_uses_second_ocr_result(self):
        player = MacroPlayer()
        player._wait = Mock()
        obj = dict(recognize='text', expected_text='确认', recheck_enabled=True,
                   after_action='click_match', not_found_timeout_ms=0)
        with package_patch('player', 'registered_module_object', return_value=obj), \
             package_patch('player', 'recognize_region_with_boxes', side_effect=[('取消', []), ('确认', [])]) as probe, \
             patch('macroflow.execution.player.image.ocr_match_center', return_value=match(200)), \
             package_patch('player', 'send_move_absolute') as move, \
             package_patch('player', 'send_button') as button:
            player._execute_image(dict(module_ref=True, module_key='module:x'), None)
        self.assertEqual(probe.call_count, 2)
        move.assert_called_once_with(215, 40)
        self.assertEqual(button.call_count, 2)

    def test_global_wait_leaves_other_guards_ready_and_ignores_long_interval_for_recheck(self):
        app = self._make_guard_app()
        delayed = self._make_guard('images/x.png', key='delayed', recheck_enabled=True, interval_ms=10000)
        immediate = self._make_guard('images/y.png', key='immediate')
        app.global_guards = {'delayed': delayed, 'immediate': immediate}
        app._guard_image_detect = Mock(return_value=(True, match(200)))
        with package_patch('app', 'capture_bgr', return_value=(None, None)), \
             patch('macroflow.ui.app.guards.time.perf_counter', return_value=100):
            first = app._evaluate_global_guards_sync()
        self.assertEqual(first.hit['guard_key'], 'immediate')
        app.global_guards.pop('immediate')
        with package_patch('app', 'capture_bgr', return_value=(None, None)), \
             patch('macroflow.ui.app.guards.time.perf_counter', return_value=105):
            second = app._evaluate_global_guards_sync()
        self.assertEqual(second.hit['guard_key'], 'delayed')