import unittest

from strategies.trend_impulse_channels import (
    calculate_trend_impulse_channels,
    latest_trend_impulse_channels,
)


class TrendImpulseChannelsTests(unittest.TestCase):
    def test_output_series_are_aligned_and_atr_warms_up_after_200_bars(self):
        closes = [100.0] * 205
        highs = [101.0] * 205
        lows = [99.0] * 205
        result = calculate_trend_impulse_channels(None, highs, lows, closes)
        for key in ("atr", "trend", "upper", "lower", "direction", "lower_retest", "upper_retest"):
            self.assertEqual(len(result[key]), len(closes))
        self.assertTrue(all(x is None for x in result["atr"][:199]))
        self.assertAlmostEqual(result["atr"][199], 2.0)
        # Pine reinicializa trend mientras ATR es na. En la primera vela
        # con ATR válido también entra por `if na(trend)`, así que direction
        # permanece neutral y trend toma el close de esa vela.
        self.assertEqual(result["direction"][198], 0)
        self.assertEqual(result["direction"][199], 0)
        self.assertAlmostEqual(result["trend"][199], 100.0)

    def test_default_negative_max_step_is_preserved_exactly(self):
        closes = [100.0] * 202
        highs = [101.0] * 202
        lows = [99.0] * 202
        result = calculate_trend_impulse_channels(None, highs, lows, closes)
        self.assertAlmostEqual(result["max_step"][199], -0.068)
        self.assertAlmostEqual(result["step_size"][200], -0.068)
        # Con closes constantes no hay flip; dir sigue neutral, por lo que
        # Pine no aplica el step negativo a trend.
        self.assertAlmostEqual(result["trend"][200], 100.0)
        self.assertAlmostEqual(result["upper"][200], 104.04)
        self.assertAlmostEqual(result["lower"][200], 95.96)

    def test_hold_bars_blocks_immediate_flip(self):
        closes = [100.0] * 200 + [110.0, 90.0, 90.0]
        highs = [x + 1 for x in closes]
        lows = [x - 1 for x in closes]
        result = calculate_trend_impulse_channels(None, highs, lows, closes, hold_bars=2)
        self.assertEqual(result["direction"][200], 1)
        self.assertEqual(result["direction"][201], 1)
        self.assertEqual(result["extension"][200], 2)

    def test_latest_helper_returns_last_values(self):
        closes = [100.0] * 200
        highs = [101.0] * 200
        lows = [99.0] * 200
        latest = latest_trend_impulse_channels(None, highs, lows, closes)
        self.assertEqual(latest["direction"], 0)
        self.assertAlmostEqual(latest["trend"], 100.0)
        self.assertAlmostEqual(latest["atr"], 2.0)

    def test_invalid_ohlc_lengths_raise(self):
        with self.assertRaises(ValueError):
            calculate_trend_impulse_channels(None, [2, 3], [1], [1, 2])


if __name__ == "__main__":
    unittest.main()
