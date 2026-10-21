import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from macroflow.execution.interval_plugin import IntervalConditionPlugin
from macroflow.ui.app import plugins_runtime as runtime

class PluginRetryTests(unittest.TestCase):
 def plugin(self, **values):
  self.now=0
  p=IntervalConditionPlugin(clock=lambda:self.now)
  p.configure({'enabled':True,'interval_ms':1000,'condition':{'module_key':'test'},'actions':[{'type':'delay','ms':0}], **values})
  return p
 def test_failure_can_trigger_and_custom_retry_waits_until_chosen_result(self):
  p=self.plugin(trigger_on='failure',recheck_ms=5000)
  self.now=1
  p.check(lambda _:True)
  self.assertIsNone(p.claim())
  self.now=5
  p.check(Mock(side_effect=AssertionError('too early')))
  self.now=6
  p.check(lambda _:False)
  hit=p.claim()
  self.assertIsNotNone(hit)
  self.assertFalse(hit['condition_result'])
  p.finish(hit['generation'],True)
  self.assertEqual(p.next_check,7)
 def test_errors_do_not_trigger_failure_branch_and_retry_is_configurable(self):
  p=self.plugin(trigger_on='failure',recheck_ms=9000)
  self.now=1
  with self.assertRaises(RuntimeError):p.check(Mock(side_effect=RuntimeError('capture failed')))
  self.assertIsNone(p.claim())
  self.assertEqual(p.next_check,10)
 def test_invalid_outcome_and_retry_are_rejected(self):
  for values in ({'trigger_on':'other'},{'recheck_ms':0},{'recheck_ms':True}):
   with self.assertRaises(ValueError):self.plugin(**values)
 def test_delete_invalidates_only_deleted_instance_after_persistence(self):
  a,b=self.plugin(),self.plugin()
  a.config.update(id='a',name='A');b.config.update(id='b',name='B')
  app=SimpleNamespace(_interval_plugins={'a':a,'b':b},app_settings={})
  old=a.generation
  with patch.object(runtime,'save_app_settings'):runtime.delete_interval_plugin(app,'a')
  self.assertEqual(list(app._interval_plugins),['b'])
  self.assertFalse(a.valid(old))
  self.assertTrue(b.config['enabled'])

 def test_deleted_or_disabled_inline_plugin_stops_before_next_action_and_resumes_parent(self):
  from macroflow.execution.player import MacroPlayer
  active=[True];events=[]
  def notice(text,*args):events.append(text);active[0]=False
  player=MacroPlayer(on_notice=notice);callback=player.on_action_start
  complete=Mock()
  player.handle_guard_hit({'_plugin_valid':lambda:active[0],'_plugin_complete':complete,
    'actions':[{'type':'notice','text':'first','duration_ms':1},{'type':'notice','text':'second','duration_ms':1}]})
  self.assertEqual(events,['first']);complete.assert_called_once_with(False)
  self.assertIs(player.on_action_start,callback)
