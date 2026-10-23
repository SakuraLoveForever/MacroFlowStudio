import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from macroflow.core.storage import DEFAULT_MODULE_OBJECT
from macroflow.core.workflow_plugins import process_absence_guard_config

class ModuleDetectionTests(unittest.TestCase):
    def test_module_defaults_only_contain_current_detection_features(self):
        self.assertFalse(any(key.startswith(('fallback_', 'timed_')) for key in DEFAULT_MODULE_OBJECT))
        self.assertIn('recheck_enabled', DEFAULT_MODULE_OBJECT)
        self.assertIn('hold_enabled', DEFAULT_MODULE_OBJECT)

    def test_process_absence_plugin_uses_global_timeout_actions(self):
        guard = process_absence_guard_config({'process_name': 'Game.exe', 'duration_ms': 60000})
        self.assertTrue(guard.get('run_code_on_timeout'))
        self.assertEqual(guard.get('not_found_timeout_ms'), 60000)
        self.assertEqual(guard.get('on_timeout_actions'), [{'type': 'restart_workflow', 'restart_workflow_target_row': 1}])
        self.assertFalse(any(key.startswith('timed_') for key in guard))

    def test_normalization_drops_removed_detection_fields(self):
        from macroflow.core.storage import _normalize_object
        removed = {
            'fallback_module_key': 'module:backup', 'fallback_on_match': 'click_exit',
            'fallback_click': True, 'fallback_click_count': 5, 'fallback_click_interval_ms': 200,
            'timed_detection': True, 'timed_condition': 'absent', 'timed_duration_ms': 5000,
            'timed_actions': [{'type': 'script_ref', 'script': 'old.json'}],
        }
        obj = _normalize_object(removed)
        self.assertTrue(set(removed).isdisjoint(obj))
