import unittest
from backtest_dual_thrust import run_backtest, summarize, build_equity_curve
from strategy_dual_thrust import calculate_levels

class DualThrustEngineTests(unittest.TestCase):
    def setUp(self):
        self.opens = [100, 100, 100, 100]
        self.highs = [102, 103, 103, 102]
        self.lows = [98, 97, 98, 96]
        self.closes = [101, 99, 102, 100]
        self.times = [1, 2, 3, 4]

    def test_levels_use_only_previous_bars_and_current_open(self):
        levels = calculate_levels(self.opens, self.highs, self.lows, self.closes, 2, lookback=2)
        self.assertAlmostEqual(levels.range_value, 4.0)
        self.assertAlmostEqual(levels.buy_track, 102.4)
        self.assertAlmostEqual(levels.sell_track, 97.2)
        # Mutating current high/low/close must not change the levels.
        highs = list(self.highs); lows = list(self.lows); closes = list(self.closes)
        highs[2], lows[2], closes[2] = 999, 1, 500
        changed = calculate_levels(self.opens, highs, lows, closes, 2, lookback=2)
        self.assertEqual(levels, changed)

    def test_ambiguous_candle_does_not_create_trade(self):
        highs = list(self.highs); lows = list(self.lows)
        highs[2], lows[2] = 103, 97
        lows[3] = 98
        trades = run_backtest(self.closes, highs, lows, self.opens, self.times, lookback=2)
        self.assertEqual(trades, [])

    def test_fees_and_slippage_are_applied(self):
        trades = run_backtest(self.closes, self.highs, self.lows, self.opens, self.times,
                              lookback=2, fee_bps=5, slippage_bps=2)
        resolved = [t for t in trades if t.pnl_pct is not None]
        self.assertTrue(resolved)
        for trade in resolved:
            self.assertGreater(trade.fees_pct, 0)
            self.assertGreater(trade.slippage_pct, 0)
            self.assertAlmostEqual(trade.pnl_pct, trade.gross_pnl_pct-trade.fees_pct)

    def test_horizon_closes_at_next_open(self):
        # A long triggers on bar 2; the next bar doesn't trigger either level.
        highs = [102, 103, 103, 101]
        lows = [98, 97, 98, 99]
        closes = [101, 99, 102, 100]
        trades = run_backtest(closes, highs, lows, self.opens, self.times, lookback=2, horizon=1)
        closed = [t for t in trades if t.exit_reason == "HORIZONTE"]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].exit_price, 100.0)

    def test_equity_curve_compounds_net_returns(self):
        trades = run_backtest(self.closes, self.highs, self.lows, self.opens, self.times,
                              lookback=2, fee_bps=0, slippage_bps=0)
        curve = build_equity_curve(trades, initial_equity=1000)
        resolved = [t for t in trades if t.pnl_pct is not None]
        self.assertEqual(len(curve), len(resolved))
        stats = summarize(trades)["total"]
        self.assertEqual(stats["resueltas"], len(resolved))
        if curve:
            self.assertGreaterEqual(curve[0]["equity"], 0)
            self.assertGreaterEqual(stats["max_drawdown_pct"], 0)

if __name__ == "__main__":
    unittest.main()
