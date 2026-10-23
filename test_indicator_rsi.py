"""Pruebas del RSI y de las medias móviles sobre el RSI."""
import unittest

from indicators import moving_average_series, rsi_indicator, rsi_series


class RSITests(unittest.TestCase):
    def test_rsi_series_is_aligned_and_warms_up(self):
        values = [float(x) for x in range(20)]
        rsi = rsi_series(values, 14)
        self.assertEqual(len(rsi), len(values))
        self.assertTrue(all(x is None for x in rsi[:14]))
        self.assertTrue(all(x == 100.0 for x in rsi[14:]))

    def test_falling_series_rsi_is_zero(self):
        values = [float(30 - x) for x in range(20)]
        rsi = rsi_series(values, 14)
        self.assertTrue(all(x == 0.0 for x in rsi[14:]))

    def test_invalid_period_rejected(self):
        with self.assertRaises(ValueError):
            rsi_series([1, 2, 3], 0)

    def test_none_ma_returns_missing_values(self):
        result = moving_average_series([None, 10.0, 20.0], 2, "None")
        self.assertEqual(result, [None, None, None])

    def test_sma_of_rsi_and_bollinger_bands(self):
        source = [100 + ((i % 7) - 3) * (i + 1) for i in range(60)]
        result = rsi_indicator(source, rsi_period=14, ma_type="SMA + Bollinger Bands", ma_period=5)
        self.assertEqual(len(result["rsi"]), len(source))
        self.assertIsNotNone(result["ma"][-1])
        self.assertIsNotNone(result["bb_upper"][-1])
        self.assertIsNotNone(result["bb_lower"][-1])
        self.assertGreaterEqual(result["bb_upper"][-1], result["bb_lower"][-1])

    def test_vwma_requires_volume(self):
        with self.assertRaises(ValueError):
            moving_average_series([None, 10.0], 1, "VWMA")


if __name__ == "__main__":
    unittest.main()
