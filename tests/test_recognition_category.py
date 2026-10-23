import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from macroflow.core.storage import _normalize_object, MODULE_CATEGORIES
from macroflow.ui.dialogs import module_objects as ui
from macroflow.execution.player import MacroPlayer
from macroflow.ui.app import plugins_page

class RecognitionCategoryTests(unittest.TestCase):
 def test_recognition_normalization_removes_actions_and_clicks(self):
  obj=_normalize_object({'category':'recognition','after_action':'click_match',
    'on_success_actions':[{'type':'key_press','vk':1}],'on_timeout_actions':[{'type':'delay','ms':1}],
    'blocking':True})
  self.assertEqual(obj['after_action'],'continue')
  for field in ('blocking','run_code_after_action','run_code_on_timeout'):
   self.assertFalse(obj[field])
  for field in ('on_success_actions','on_timeout_actions'):self.assertEqual(obj[field],[])
 def test_recognition_returns_result_without_clicking_or_running_module_code(self):
  for result in (None,{'center_x':1,'center_y':1}):
   p=MacroPlayer();p._match_recognition_module=Mock(return_value=result)
   p._after_module_success=Mock();p._module_result_route=Mock(return_value=('row',2))
   obj={'category':'recognition','recognize':'image','after_action':'click_match'}
   with patch('macroflow.execution.player.image.registered_module_object',return_value=obj):
    self.assertEqual(p._execute_image({'module_ref':True,'module_key':'module:x'},None),('row',2))
   self.assertIs(p._module_result_route.call_args.kwargs['succeeded'],result is not None)
   p._after_module_success.assert_not_called()
 def test_every_category_has_switch_menu_with_current_disabled(self):
  for category in MODULE_CATEGORIES:
   dialog=object.__new__(ui.TemplateRegionManagerDialog)
   dialog.objects={'x':{'category':category,'name':'X'}}
   view=Mock();view.key_for_iid.return_value='x'
   dialog._current_view=lambda:view
   tree=Mock();tree.identify_row.return_value='one'
   menu,sub=Mock(),Mock()
   with patch.object(ui.tk,'Menu',side_effect=[menu,sub]):
    dialog._show_module_context_menu(SimpleNamespace(widget=tree,y=1,x_root=1,y_root=1))
   menu.add_cascade.assert_called_once_with(label='切换分类',menu=sub)
   entries=sub.add_command.call_args_list
   self.assertEqual(len(entries),len(MODULE_CATEGORIES))
   self.assertEqual([e.kwargs['label'] for e in entries if e.kwargs['state']=='disabled'],[ui.CATEGORY_LABELS[category]])
 def test_switching_to_recognition_preserves_identity_and_clears_input_actions(self):
  dialog=object.__new__(ui.TemplateRegionManagerDialog)
  dialog.objects={'x':{'category':'switch','template':'test.png','after_action':'click_match'}}
  dialog._reload_trees=Mock()
  with patch.object(ui,'save_module_objects'):
   dialog._change_module_category('x','recognition')
  self.assertEqual(list(dialog.objects),['x'])
  self.assertEqual(dialog.objects['x']['after_action'],'continue')

class PluginDeletionTests(unittest.TestCase):
 def test_delete_requires_confirmation(self):
  for answer in (False,True):
   dialog=object.__new__(plugins_page.PluginSettingsDialog);dialog.destroy=Mock()
   app=Mock()
   with patch.object(plugins_page.messagebox,'askyesno',return_value=answer),patch('macroflow.ui.app.plugins_runtime.delete_interval_plugin') as delete:
    dialog.delete_plugin(app,'x')
   self.assertEqual(delete.call_count,int(answer));self.assertEqual(dialog.destroy.call_count,int(answer))


class CompactLayoutTests(unittest.TestCase):
 def test_long_combobox_text_gets_horizontal_scrollbar(self):
  from macroflow.ui.app.shell import attach_combobox_scrollbar
  combo=Mock()
  with patch('macroflow.ui.app.shell.ttk.Scrollbar') as factory:
   bar=attach_combobox_scrollbar(Mock(),combo)
  update=combo.configure.call_args.kwargs['xscrollcommand']
  update('0','0.4');bar.pack.assert_called_once_with(fill='x')
  update('0','1');bar.pack_forget.assert_called_once_with()
 def test_card_measures_checkbox_and_content_and_colors_enabled_state(self):
  card=object.__new__(plugins_page.PluginCard)
  card.title='定时插件';card.detail='每 1 h';card.status='待配置';card.hover=False
  for name in ('delete','configure','tag_lower'):setattr(card,name,Mock())
  card.checkbox=Mock();card.checkbox.winfo_reqwidth.return_value=24;card.checkbox.winfo_reqheight.return_value=18
  card.winfo_width=Mock(return_value=228);card.focus_get=Mock(return_value=None)
  card.cget=Mock(return_value=65)
  card.bbox=Mock(side_effect=lambda item:{1:(44,12,200,30),2:(12,36,200,48),3:(12,51,200,63)}[item])
  card.create_polygon=Mock(return_value=4)
  colors=[]
  with patch.object(plugins_page,'px',side_effect=lambda value:value):
   for enabled in (False,True):
    card.enabled=enabled;card.create_text=Mock(side_effect=[1,2,3]);card._draw()
    self.assertGreaterEqual(card.create_text.call_args_list[0].args[0],44)
    self.assertGreaterEqual(card.configure.call_args.kwargs['height'],75)
    colors.append(card.create_polygon.call_args.kwargs['fill'])
  self.assertNotEqual(*colors)
