import unittest
from macroflow.ui.app import plugins_page as ui

class PluginPeriodTests(unittest.TestCase):
 def test_default_and_mixed_periods(self):
  self.assertEqual(ui.period_display(3600000),('1','0'))
  self.assertEqual(ui.period_ms('1','30'),5400000)
  self.assertEqual(ui.period_display(5400000),('1','30'))
  self.assertEqual(ui.period_ms('0','5'),300000)
 def test_invalid_period_is_rejected(self):
  for hours,minutes in (('0','0'),('-1','1'),('1','60'),('nan','0'),('','1'),('8761','0')):
   with self.assertRaises(ValueError):ui.period_ms(hours,minutes)
 def test_display_does_not_round_existing_subminute_periods(self):
  hours,minutes=ui.period_display(1500)
  self.assertEqual(ui.period_ms(hours,minutes),1500)
