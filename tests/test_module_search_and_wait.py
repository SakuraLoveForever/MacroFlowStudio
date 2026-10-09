"""Module search, fixed waits, and orderly process exit without visible UI."""
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from macroflow.ui.dialogs.base import DurationVar
from macroflow.ui.dialogs.module_objects import TemplateRegionManagerDialog
from macroflow.execution.player import MacroPlayer
from tests.helpers.patches import package_patch


class ModuleSearchAndWaitTests(unittest.TestCase):
    def test_keyword_search_preserves_the_selected_category(self):
        manager = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        manager.objects = {
            'module:chat': {'name': '聊天', 'category': 'switch'},
            'module:chat-global': {'name': '聊天检查', 'category': 'workflow_global'},
            'module:other': {'name': '领取奖励', 'category': 'switch'},
        }
        manager.search_query = '聊天'
        manager.virtual_trees = {tab: Mock() for tab in ('all', 'switch', 'workflow_global')}
        for tab, expected in (
            ('all', ['module:chat', 'module:chat-global']),
            ('switch', ['module:chat']),
            ('workflow_global', ['module:chat-global']),
        ):
            with self.subTest(tab=tab):
                manager._reload_tree(tab, Mock())
                rows = manager.virtual_trees[tab].set_rows.call_args.args[0]
                self.assertEqual(sorted(row.key for row in rows), sorted(expected))

    def test_duration_defaults_to_seconds_without_changing_stored_time(self):
        value = DurationVar(1500, master=tk.Tcl())
        self.assertEqual(value.unit.get(), 's')
        self.assertEqual(value._raw(), '1.5')
        self.assertEqual(value.get(), '1500')
        value.set('5')
        self.assertEqual(value.get(), '5000')

    def test_module_wait_precedes_first_detection_and_ignores_playback_speed(self):
        player = MacroPlayer()
        player.speed = 10
        events = []
        player._wait = Mock(side_effect=lambda ms: events.append(('wait', ms)))
        obj = dict(template='images/target.png', start_delay_ms=5000,
                   not_found_timeout_ms=0, after_action='continue')
        with package_patch('player', 'registered_module_object', return_value=obj), \
             package_patch('player', 'find_template', side_effect=lambda *_a, **_k: events.append(('detect', None))), \
             package_patch('player', 'show_overlay'):
            player._execute_image(dict(module_ref=True, module_key='module:target'), None)
        self.assertEqual(events[:2], [('wait', 5000), ('detect', None)])

    def test_graceful_close_escalates_after_timeout(self):
        player = MacroPlayer()
        player._wait = Mock()
        with package_patch('player', 'is_process_running', side_effect=[True, True, True, True, False]), \
             package_patch('player', 'taskkill_process', return_value=(0, '')) as kill, \
             patch('macroflow.execution.player.apps.time.perf_counter', side_effect=[0, 3, 3, 3]):
            player._execute_close_app(dict(name='game.exe', graceful_wait_ms=0))
        self.assertEqual([c.kwargs['force'] for c in kill.call_args_list], [False, True])

    def test_related_process_wait_stops_instead_of_relaunching_too_soon(self):
        player = MacroPlayer()
        player._close_process = Mock()
        player._wait = Mock()
        with package_patch('player', 'is_process_running', return_value=True):
            with self.assertRaises(RuntimeError):
                player._execute_close_app(dict(name='game.exe', graceful=True,
                    wait_for_processes_timeout_ms=0, wait_for_processes=['GameMon64.des']))

    def test_related_processes_are_waited_even_if_main_process_is_gone(self):
        player = MacroPlayer()
        player._wait = Mock()
        with package_patch('player', 'is_process_running', side_effect=[False, True, False]) as running, \
             package_patch('player', 'taskkill_process') as kill:
            player._execute_close_app(dict(name='game.exe', graceful=True,
                graceful_wait_ms=1000, wait_for_processes=['GameMon64.des']))
        self.assertEqual([call.args[0] for call in running.call_args_list],
                         ['game.exe', 'GameMon64.des', 'GameMon64.des'])
        player._wait.assert_called_once_with(100)
        kill.assert_not_called()
