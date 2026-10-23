import unittest
from unittest.mock import patch
from strategy_rsi import generate_rsi_signals, RSISignal
from backtest_rsi import run_backtest, summarize

class RSIStrategyTests(unittest.TestCase):
    def test_mode_switch_and_signal_types(self):
        # Sobrecompra -> giro bajista SHORT; luego sobreventa -> giro alcista LONG.
        rsis = [None, 72, 68, 64, 29, 32, 38, 52, 54]
        mas  = [None, 65, 66, 65, 45, 30, 40, 50, 51]
        indicator = {"rsi": rsis, "ma": mas}
        closes = [100.0] * len(rsis)
        highs = [102.0] * len(rsis); lows = [98.0] * len(rsis)
        with patch("strategy_rsi.rsi_indicator", return_value=indicator):
            signals = generate_rsi_signals(closes, highs, lows, rsi_period=1, ma_period=1, sl_lookback=2)
        self.assertEqual(signals[2].side, "SHORT")
        self.assertEqual(signals[2].signal_type, "RSI_TURN")
        self.assertEqual(signals[5].side, "LONG")
        self.assertEqual(signals[5].signal_type, "RSI_TURN")
        # Cruce alcista adicional con RSI por debajo de 55.
        self.assertEqual(signals[7].side, "LONG")
        self.assertEqual(signals[7].signal_type, "MA_CROSS")

    def test_multiple_trades_can_overlap(self):
        signals = [None, RSISignal("LONG", 100, 32, 30, 95, 110, "RSI_TURN"),
                   RSISignal("LONG", 101, 36, 34, 96, 111, "MA_CROSS"), None]
        closes = [100, 100, 101, 102]
        highs = [101, 101, 102, 103]; lows = [99, 99, 100, 101]
        opens = [100, 100, 101, 102]; times = [0, 1, 2, 3]
        with patch("backtest_rsi.generate_rsi_signals", return_value=signals):
            trades = run_backtest(closes, highs, lows, opens, times, horizon=None)
        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0].entry_time, 2)
        self.assertEqual(trades[1].entry_time, 3)


    def test_optional_skip_first_ma_cross(self):
        rsis = [None, 28, 32, 40, 42, 41, 43, 42, 44]
        mas  = [None, 35, 30, 35, 41, 42, 42, 43, 43]
        closes = [100.0] * len(rsis)
        highs = [102.0] * len(rsis); lows = [98.0] * len(rsis)
        with patch("strategy_rsi.rsi_indicator", return_value={"rsi": rsis, "ma": mas}):
            signals = generate_rsi_signals(closes, highs, lows, sl_lookback=2, ignore_first_ma_cross=True)
        # La primera entrada es el giro RSI; primer cruce válido se descarta y segundo se acepta.
        ma_signals = [s for s in signals if s and s.signal_type == "MA_CROSS"]
        self.assertEqual(len(ma_signals), 1)
        self.assertEqual(ma_signals[0].rsi, 44)

    def test_reentering_same_extreme_resets_initial_turn(self):
        rsis = [None, 72, 68, 66, 69, 71, 69]
        mas =  [None, 65, 66, 65, 65, 65, 65]
        closes = [100.0] * len(rsis)
        highs = [102.0] * len(rsis); lows = [98.0] * len(rsis)
        with patch("strategy_rsi.rsi_indicator", return_value={"rsi": rsis, "ma": mas}):
            signals = generate_rsi_signals(closes, highs, lows, sl_lookback=2)
        self.assertEqual(signals[2].signal_type, "RSI_TURN")
        # El RSI reingresa a sobrecompra en la vela 5; el giro bajista posterior reinicia.
        self.assertEqual(signals[6].signal_type, "RSI_TURN")

    def test_pivot_stop_mode_uses_confirmed_pivot(self):
        rsis = [None, 28, 32, 40, 42]
        mas =  [None, 35, 30, 41, 41]
        closes = [100.0, 100.0, 101.0, 102.0, 103.0]
        highs = [102, 102, 103, 104, 105]
        lows =  [99, 97, 98, 96, 98]
        with patch("strategy_rsi.rsi_indicator", return_value={"rsi": rsis, "ma": mas}):
            signals = generate_rsi_signals(closes, highs, lows, sl_lookback=2, stop_mode="pivot", pivot_left=1, pivot_right=1)
        # Pivot low 96 en la vela 3 queda confirmado en vela 4.
        self.assertAlmostEqual(signals[4].stop_loss, 96.0 * 0.999)

    def test_summary_still_has_side_groups(self):
        self.assertEqual(set(summarize([])), {"total", "long", "short"})

if __name__ == "__main__": unittest.main()
