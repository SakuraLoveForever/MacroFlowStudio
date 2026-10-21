import copy
import unittest
from unittest.mock import Mock, patch
from macroflow.ui.dialogs import module_objects as ui
from macroflow.core.models import END_CURRENT_SCRIPT_LABEL

class ModuleCopyTests(unittest.TestCase):
 def dialog(self,key='source',category='switch'):
  d=object.__new__(ui.TemplateRegionManagerDialog)
  d.objects={key:{'category':category,'name':'原模块','on_success_actions':[{'type':'delay','ms':1}]}}
  d._reload_trees=Mock();d._current_view=Mock()
  return d
 def test_copy_opens_same_editor_and_cancel_creates_nothing(self):
  d=self.dialog();before=copy.deepcopy(d.objects)
  with patch.object(ui,'TemplateRegionFormDialog') as form,patch.object(ui,'update_module_object') as save:
   form.return_value.show.return_value=None;d._copy_module('source')
  self.assertEqual(d.objects,before);save.assert_not_called()
  draft=form.call_args.kwargs['object_dict']
  self.assertEqual(draft['name'],'原模块 副本')
  self.assertIsNot(draft['on_success_actions'],d.objects['source']['on_success_actions'])
 def test_copy_save_always_uses_new_identity_even_if_editor_returns_source_key(self):
  d=self.dialog();before=copy.deepcopy(d.objects['source'])
  with patch.object(ui,'TemplateRegionFormDialog') as form,patch.object(ui,'update_module_object') as save:
   def show():
    draft=form.call_args.kwargs['object_dict'];draft['on_success_actions'][0]['ms']=99
    return ('source','source',{**draft,'name':'修改后的副本'})
   form.return_value.show.side_effect=show
   save.side_effect=lambda key,obj:{**d.objects,key:obj}
   d._copy_module('source')
  key,obj=save.call_args.args
  self.assertTrue(key.startswith('module:'));self.assertNotEqual(key,'source')
  self.assertEqual(d.objects['source'],before);self.assertEqual(obj['name'],'修改后的副本')
  d._current_view.return_value.select_key.assert_called_once_with(key)
 def test_copy_fixed_special_module_keeps_its_behavior(self):
  d=self.dialog(END_CURRENT_SCRIPT_LABEL,'special')
  with patch.object(ui,'TemplateRegionFormDialog') as form,patch.object(ui,'update_module_object') as save:
   form.return_value.show.return_value=('',END_CURRENT_SCRIPT_LABEL,{'category':'special','name':'副本','pure_action':True})
   d._copy_module(END_CURRENT_SCRIPT_LABEL)
  self.assertEqual(save.call_args.args[1]['special_action_type'],'end_current_script')
