import unittest
from unittest.mock import patch
from strategies.trend_impulse_channels.engine import run_backtest

class BreakoutEngineTests(unittest.TestCase):
 def fake(self,dirs,uppers=None,lowers=None):
  n=len(dirs);uppers=uppers or [10.0]*n;lowers=lowers or [2.0]*n
  return {'direction':dirs,'upper':uppers,'lower':lowers,'atr':[1.0]*n,'trend':[5.0]*n,'bars_in_trend':[1]*n,'extension':[0]*n,'trend_step':[False]*n}
 def test_long_breakout_enters_at_signal_close_and_uses_two_bar_low(self):
  dirs=[1,1,1,1]; fake=self.fake(dirs)
  with patch('strategies.trend_impulse_channels.engine.calculate_trend_impulse_channels',return_value=fake):
   trades,_=run_backtest([5,6,7,8],[6,11,9,9],[4,3,5,6],[5,10.5,8,8],[0,1,2,3],risk_usdt=10)
  self.assertEqual(len(trades),1);self.assertEqual(trades[0].side,'LONG');self.assertEqual(trades[0].entry,10.5);self.assertEqual(trades[0].stop_loss,3)
 def test_same_candle_channel_flip_cannot_open(self):
  fake=self.fake([1,-1,-1,-1])
  with patch('strategies.trend_impulse_channels.engine.calculate_trend_impulse_channels',return_value=fake):
   trades,_=run_backtest([5,6,7,8],[6,11,9,9],[4,3,5,6],[5,10.5,8,8],[0,1,2,3])
  self.assertEqual(trades,[])
 def test_short_mirror_and_configurable_tp1(self):
  fake=self.fake([-1,-1,-1,-1],uppers=[10]*4,lowers=[2]*4)
  with patch('strategies.trend_impulse_channels.engine.calculate_trend_impulse_channels',return_value=fake):
   trades,_=run_backtest([5,4,3,2],[6,5,4,3],[4,1,2,1],[5,3,2.5,1.5],[0,1,2,3],tp1_pct=35)
  self.assertEqual(len(trades),1);self.assertEqual(trades[0].side,'SHORT');self.assertEqual(trades[0].tp1_pct,35);self.assertEqual(trades[0].stop_loss,6)
 def test_tp1_requires_flip_after_entry_and_tp2_is_rest(self):
  fake=self.fake([1,1,-1,-1],uppers=[10,10,12,12],lowers=[0,0,-2,-2])
  with patch('strategies.trend_impulse_channels.engine.calculate_trend_impulse_channels',return_value=fake):
   trades,_=run_backtest([5,6,7,13],[6,11,11,13],[4,5,5,6],[5,10.5,10,12],[0,1,2,3],tp1_pct=40)
  self.assertEqual(len(trades),1);self.assertEqual(trades[0].tp1_time,2);self.assertEqual(trades[0].tp1_price,10);self.assertEqual(trades[0].tp2_target,12);self.assertEqual(trades[0].outcome,'TP2')
 def test_reject_invalid_tp1(self):
  with self.assertRaises(ValueError):run_backtest([],[],[],[],[],tp1_pct=100)

if __name__=='__main__':unittest.main()
