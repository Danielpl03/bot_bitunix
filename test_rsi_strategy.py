import unittest
from unittest.mock import patch
from strategy_rsi import generate_rsi_signals
from backtest_rsi import run_backtest, summarize, TP, SL

class RSIStrategyTests(unittest.TestCase):
    def test_first_rsi_uptick_after_oversold_enters_long(self):
        rsis=[None,28,25,26,29,35,72]
        closes=[100,99,98,99,100,101,110]; highs=[101,100,99,100,101,102,111]; lows=[99,98,95,96,98,100,109]
        with patch('strategy_rsi.rsi_indicator',return_value={'rsi':rsis,'ma':[None]*len(rsis)}):
            sigs=generate_rsi_signals(closes,highs,lows,pivot_left=2,pivot_right=2)
        self.assertEqual(sigs[3].side,'LONG')
        self.assertEqual(sigs[3].stop_loss,95.0) # fallback: min since first oversold bar
        self.assertIsNone(sigs[4]) # only first turn in this extreme episode

    def test_first_rsi_downtick_after_overbought_enters_short(self):
        rsis=[None,72,75,74,68,30]
        closes=[100]*len(rsis); highs=[101,102,105,104,103,100]; lows=[99,98,99,98,97,95]
        with patch('strategy_rsi.rsi_indicator',return_value={'rsi':rsis,'ma':[None]*len(rsis)}):
            sigs=generate_rsi_signals(closes,highs,lows)
        self.assertEqual(sigs[3].side,'SHORT')
        self.assertEqual(sigs[3].stop_loss,105.0)

    def test_pivot_is_used_when_confirmed_since_extreme(self):
        rsis=[None,28,25,24,26,30]
        closes=[110,108,107,106,108,109]; highs=[111,109,108,107,109,110]; lows=[109,105,103,104,106,107]
        with patch('strategy_rsi.rsi_indicator',return_value={'rsi':rsis,'ma':[None]*len(rsis)}):
            sigs=generate_rsi_signals(closes,highs,lows,pivot_left=1,pivot_right=1)
        self.assertTrue(sigs[4].pivot_used)
        self.assertEqual(sigs[4].stop_loss,103.0)

    def test_backtest_uses_fixed_risk_and_opposite_rsi_zone_tp(self):
        rsis=[None,28,25,26,35,50,75,68]
        closes=[100,99,98,99,101,105,110,112]
        highs=[101,100,99,100,102,106,111,113]
        lows=[99,98,95,96,100,104,109,111]
        opens=[100,99,98,99,100,104,109,111]
        times=list(range(len(rsis)))
        with patch('backtest_rsi.generate_rsi_signals') as mock_sig, patch('backtest_rsi.rsi_indicator',return_value={'rsi':rsis,'ma':[None]*len(rsis)}):
            from strategy_rsi import RSISignal
            sigs=[None]*len(rsis); sigs[3]=RSISignal('LONG',99,26,95,1,3,pivot_used=False)
            mock_sig.return_value=sigs
            trades=run_backtest(closes,highs,lows,opens,times,rsi_period=1,risk_usdt=20)
        self.assertEqual(len(trades),1)
        self.assertEqual(trades[0].entry,100)
        self.assertEqual(trades[0].quantity,4.0)
        self.assertEqual(trades[0].outcome,TP)
        self.assertEqual(trades[0].exit_price,112.0)

    def test_long_tp_only_when_rsi_exits_overbought(self):
        rsis=[None,28,25,26,75,72,68]
        closes=[100,99,98,99,105,107,106]
        highs=[101,100,99,100,106,108,107]
        lows=[99,98,95,96,104,106,105]
        opens=[100,99,98,99,104,106,107]
        times=list(range(len(rsis)))
        from strategy_rsi import RSISignal
        sigs=[None]*len(rsis); sigs[3]=RSISignal('LONG',99,26,95,1,3)
        with patch('backtest_rsi.generate_rsi_signals',return_value=sigs), patch('backtest_rsi.rsi_indicator',return_value={'rsi':rsis,'ma':[None]*len(rsis)}):
            trades=run_backtest(closes,highs,lows,opens,times,rsi_period=1)
        self.assertEqual(trades[0].exit_time,6)  # no exit at 75 or 72; exit only after crossing below 70
        self.assertEqual(trades[0].outcome,TP)

    def test_no_fixed_price_target(self):
        rsis=[None,28,26,27]; closes=[100,99,98,99]; highs=[101,100,99,100]; lows=[99,98,95,96]
        with patch('strategy_rsi.rsi_indicator',return_value={'rsi':rsis,'ma':[None]*len(rsis)}):
            sigs=generate_rsi_signals(closes,highs,lows)
        self.assertIsNone(sigs[3].target)


    def test_reentry_candidate_on_second_rsi_turn_without_reentering_extreme(self):
        rsis=[None,28,25,26,27,24,26,28]
        closes=[100,99,98,99,100,97,99,101]
        highs=[101,100,99,100,101,98,100,102]
        lows=[99,98,95,96,97,93,95,98]
        with patch('strategy_rsi.rsi_indicator',return_value={'rsi':rsis,'ma':[None]*len(rsis)}):
            sigs=generate_rsi_signals(closes,highs,lows,pivot_left=2,pivot_right=2)
        self.assertEqual(sigs[3].signal_type,'RSI_TURN')
        self.assertEqual(sigs[6].signal_type,'RSI_REENTRY')
        self.assertEqual(sigs[6].side,'LONG')

    def test_bullish_divergence_generates_reentry_candidate(self):
        rsis=[None,28,25,26,27,28,29]
        closes=[100,99,98,99,98,97,99]
        highs=[101,100,99,100,99,98,100]
        lows=[99,98,95,96,94,90,93]
        with patch('strategy_rsi.rsi_indicator',return_value={'rsi':rsis,'ma':[None]*len(rsis)}):
            sigs=generate_rsi_signals(closes,highs,lows,pivot_left=1,pivot_right=1)
        self.assertEqual(sigs[3].signal_type,'RSI_TURN')
        self.assertEqual(sigs[6].signal_type,'RSI_DIVERGENCE')
        self.assertEqual(sigs[6].stop_loss,90.0)


    def test_reentry_is_only_taken_after_previous_trade_hits_stop(self):
        from strategy_rsi import RSISignal
        rsis=[None,28,26,25,27,35,72,68]
        closes=[100,99,98,97,99,101,106,105]
        highs=[101,100,99,98,100,102,107,106]
        lows=[99,98,97,94,98,100,105,104]
        opens=[100,99,98,100,98,99,102,106]
        times=list(range(len(rsis)))
        sigs=[None]*len(rsis)
        sigs[2]=RSISignal('LONG',98,26,95,1,2,'RSI_TURN')
        sigs[4]=RSISignal('LONG',99,27,92,1,4,'RSI_REENTRY')
        with patch('backtest_rsi.generate_rsi_signals',return_value=sigs), patch(
            'backtest_rsi.rsi_indicator',return_value={'rsi':rsis,'ma':[None]*len(rsis)}):
            trades=run_backtest(closes,highs,lows,opens,times,rsi_period=1,risk_usdt=10)
        self.assertEqual(len(trades),2)
        self.assertEqual(trades[0].outcome,SL)
        self.assertEqual(trades[1].signal_type,'RSI_REENTRY')
        self.assertEqual(trades[1].outcome,TP)

    def test_summary_groups(self):
        self.assertEqual(set(summarize([])),{'total','long','short'})

if __name__=='__main__': unittest.main()
