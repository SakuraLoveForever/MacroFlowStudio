import unittest
from unittest.mock import Mock,patch
from macroflow.ui.app import shell

class BackgroundColorTests(unittest.TestCase):
 def test_sidebar_canvas_does_not_allow_theme_to_replace_background(self):
  with patch.object(shell.tk.Canvas,'__init__',return_value=None) as init,patch.object(shell.tk.Canvas,'bind'):
   shell.SidebarButton(Mock(),text='按钮',command=Mock())
  self.assertIs(init.call_args.kwargs.get('autostyle'),False)
  self.assertEqual(init.call_args.kwargs['background'],shell.COLOR_SIDEBAR)
 def test_short_sidebar_is_top_aligned_with_full_viewport_scrollregion(self):
  canvas=Mock();canvas.bbox.return_value=(0,0,350,900)
  canvas.winfo_width.return_value=350;canvas.winfo_height.return_value=1200
  shell.fit_sidebar_scrollregion(canvas)
  canvas.configure.assert_called_once_with(scrollregion=(0,0,350,1200))
  canvas.yview_moveto.assert_called_once_with(0)
 def test_long_sidebar_retains_scroll_position(self):
  canvas=Mock();canvas.bbox.return_value=(0,0,350,1500)
  canvas.winfo_width.return_value=350;canvas.winfo_height.return_value=1200
  shell.fit_sidebar_scrollregion(canvas)
  canvas.configure.assert_called_once_with(scrollregion=(0,0,350,1500))
  canvas.yview_moveto.assert_not_called()
