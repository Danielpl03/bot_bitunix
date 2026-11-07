import unittest
from strategies.trend_impulse_channels.live import entry_candle_is_eligible, breakout_entry_side

class TestTICLiveEntryActivation(unittest.TestCase):
    def test_previous_closed_candle_never_opens_after_activation(self):
        width = 3_600_000
        activation = 1_794_052_800_000 + 10_000
        previous = 1_794_052_800_000 - width
        expected = 1_794_052_800_000
        self.assertFalse(entry_candle_is_eligible(previous, width, activation, expected))

    def test_current_candle_must_close_after_activation(self):
        width = 3_600_000
        candle = 1_794_052_800_000
        self.assertTrue(entry_candle_is_eligible(candle, width, candle - 1, candle))
        self.assertFalse(entry_candle_is_eligible(candle, width, candle + width + 1, candle))

    def test_lower_band_break_with_bullish_close_is_not_short(self):
        self.assertIsNone(breakout_entry_side(100, 101, 95, 100.5, 110, 96))

    def test_lower_band_break_with_bearish_close_is_short(self):
        self.assertEqual(breakout_entry_side(100, 101, 95, 99, 110, 96), 'SHORT')

    def test_stale_api_candle_cannot_create_entry(self):
        width = 3_600_000
        activation = 1_794_052_000_000
        stale = 1_794_052_800_000 - width
        expected = 1_794_052_800_000
        self.assertFalse(entry_candle_is_eligible(stale, width, activation, expected))

if __name__ == '__main__':
    unittest.main()
