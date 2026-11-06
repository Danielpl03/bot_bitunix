import unittest
from unittest.mock import patch
from strategies.trend_impulse_channels.engine import run_backtest

class BreakoutEngineTests(unittest.TestCase):
 def fake(self,dirs,uppers=None,lowers=None,bars=None):
  n=len(dirs);uppers=uppers or [10.0]*n;lowers=lowers or [2.0]*n
  bars=bars if bars is not None else list(range(2,n+2))
  return {'direction':dirs,'upper':uppers,'lower':lowers,'atr':[1.0]*n,'trend':[5.0]*n,'bars_in_trend':bars,'extension':[0]*n,'trend_step':[False]*n}
 def test_long_breakout_enters_at_signal_close_and_uses_two_bar_low(self):
  dirs=[1,1,1,1]; fake=self.fake(dirs)
  with patch('strategies.trend_impulse_channels.engine.calculate_trend_impulse_channels',return_value=fake):
   trades,_=run_backtest([5,6,7,8],[6,11,9,9],[4,3,5,6],[5,10.5,8,8],[0,1,2,3],risk_usdt=10)
  self.assertEqual(len(trades),1);self.assertEqual(trades[0].side,'LONG');self.assertEqual(trades[0].entry,10.5);self.assertEqual(trades[0].stop_loss,3)
 def test_same_candle_channel_flip_cannot_open(self):
  # El nuevo canal reinicia el contador aunque la dirección se conserve.
  fake=self.fake([1,1,1,1],bars=[0,1,2,3])
  with patch('strategies.trend_impulse_channels.engine.calculate_trend_impulse_channels',return_value=fake):
   trades,_=run_backtest([5,6,7,8],[6,11,9,9],[4,3,5,6],[5,10.5,8,8],[0,1,2,3])
  self.assertEqual(trades,[])
 def test_short_mirror_and_configurable_tp1(self):
  fake=self.fake([-1,-1,-1,-1],uppers=[10]*4,lowers=[2]*4)
  with patch('strategies.trend_impulse_channels.engine.calculate_trend_impulse_channels',return_value=fake):
   trades,_=run_backtest([5,4,3,2],[6,5,4,3],[4,1,2,1],[5,3,2.5,1.5],[0,1,2,3],tp1_pct=35)
  self.assertEqual(len(trades),1);self.assertEqual(trades[0].side,'SHORT');self.assertEqual(trades[0].tp1_pct,35);self.assertEqual(trades[0].stop_loss,6)
 def test_tp1_requires_flip_after_entry_and_tp2_is_rest(self):
  fake=self.fake([1,1,1,1],uppers=[10,10,12,12],lowers=[0,0,-2,-2],bars=[0,2,1,2])
  with patch('strategies.trend_impulse_channels.engine.calculate_trend_impulse_channels',return_value=fake):
   trades,_=run_backtest([5,6,7,13],[6,11,11,13],[4,5,5,11],[5,10.5,10,12],[0,1,2,3],tp1_pct=40)
  self.assertEqual(len(trades),1);self.assertEqual(trades[0].tp1_time,2);self.assertEqual(trades[0].tp1_price,10);self.assertEqual(trades[0].tp2_target,12);self.assertEqual(trades[0].outcome,'TP2');self.assertEqual(trades[0].stop_loss,trades[0].entry)

 def test_same_direction_channel_reset_blocks_breakout_entry(self):
  fake=self.fake([1,1,1],bars=[0,1,2])
  with patch('strategies.trend_impulse_channels.engine.calculate_trend_impulse_channels',return_value=fake):
   trades,_=run_backtest([5,6,7],[6,12,9],[4,5,5],[5,11,8],[0,1,2])
  self.assertEqual(trades,[])

 def test_tp1_same_direction_reset_moves_remaining_stop_to_break_even(self):
  fake=self.fake([1,1,1,1],uppers=[10,10,12,12],lowers=[0,0,-2,-2],bars=[0,2,1,2])
  with patch('strategies.trend_impulse_channels.engine.calculate_trend_impulse_channels',return_value=fake):
   trades,_=run_backtest([5,6,7,10],[6,11,11,11],[4,5,5,10.6],[5,10.5,10,10.9],[0,1,2,3],tp1_pct=40)
  self.assertEqual(len(trades),1)
  t=trades[0]
  self.assertEqual(t.tp1_time,2)
  self.assertEqual(t.stop_loss,t.entry)
  self.assertEqual(t.initial_stop_loss,4.0)
  self.assertEqual(t.remaining_qty,t.quantity*0.6)
  self.assertEqual(t.outcome,'OPEN')

 def test_reject_invalid_tp1(self):
  with self.assertRaises(ValueError):run_backtest([],[],[],[],[],tp1_pct=100)

if __name__=='__main__':unittest.main()
