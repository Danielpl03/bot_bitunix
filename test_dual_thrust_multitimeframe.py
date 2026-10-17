import unittest
from backtest_dual_thrust_multitimeframe import (
    aggregate_candles, run_backtest_multitimeframe, validate_timeframes,
)

HOUR = 3_600_000


def candle(time, open_, high, low, close):
    return {"time": time, "open": open_, "high": high, "low": low, "close": close}


class MultiTimeframeTests(unittest.TestCase):
    def test_aggregates_four_hour_candle(self):
        raw = [
            candle(0, 100, 102, 99, 101),
            candle(HOUR, 101, 104, 100, 103),
            candle(2*HOUR, 103, 105, 101, 102),
            candle(3*HOUR, 102, 103, 98, 100),
        ]
        result = aggregate_candles(raw, "4h", "1h")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["time"], 0)
        self.assertEqual(result[0]["open"], 100)
        self.assertEqual(result[0]["high"], 105)
        self.assertEqual(result[0]["low"], 98)
        self.assertEqual(result[0]["close"], 100)

    def test_incomplete_target_candle_is_discarded(self):
        raw = [candle(0, 100, 101, 99, 100), candle(HOUR, 100, 101, 99, 100),
               candle(2*HOUR, 100, 101, 99, 100)]
        self.assertEqual(aggregate_candles(raw, "4h", "1h"), [])

    def test_rejects_execution_interval_not_dividing_target(self):
        with self.assertRaises(ValueError):
            validate_timeframes("1h", "4h")

    def test_lower_candles_resolve_opposite_breaks_in_one_target_bar(self):
        # Two completed 4h history bars establish BuyTrack=101.2 and SellTrack=98.6.
        targets = [
            {"time": 0, "open": 100, "high": 101, "low": 99, "close": 100},
            {"time": 4*HOUR, "open": 100, "high": 101, "low": 99, "close": 100},
            {"time": 8*HOUR, "open": 100, "high": 102, "low": 98, "close": 99},
        ]
        execution = []
        # First subcandle crosses upper level, second crosses lower level without
        # touching the upper level: order is known at 1h resolution.
        execution += [candle(8*HOUR, 100, 102, 99.5, 101)]
        execution += [candle(9*HOUR, 100.4, 100.5, 98.0, 99)]
        execution += [candle(10*HOUR, 99, 100, 99, 99.5)]
        execution += [candle(11*HOUR, 99.5, 100, 99, 99.5)]
        trades = run_backtest_multitimeframe(targets, execution, "4h", "1h", lookback=2)
        resolved = [t for t in trades if t.pnl_pct is not None]
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].side, "LONG")
        self.assertEqual(resolved[0].exit_reason, "REVERSIÓN")
        self.assertEqual(resolved[0].entry_time, 8*HOUR)
        self.assertEqual(resolved[0].exit_time, 9*HOUR)
        self.assertEqual(trades[-1].side, "SHORT")
        self.assertEqual(trades[-1].exit_reason, "PENDIENTE")


if __name__ == "__main__":
    unittest.main()
