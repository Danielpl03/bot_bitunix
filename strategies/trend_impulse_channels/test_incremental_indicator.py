import random
import unittest
from strategies.trend_impulse_channels.indicator import (
    calculate_trend_impulse_channels,
    initialize_incremental_state,
    update_incremental_state,
)

class IncrementalIndicatorTests(unittest.TestCase):
    def test_incremental_checkpoint_matches_full_calculation(self):
        random.seed(91)
        closes=[]; price=100.0
        for _ in range(650):
            price += random.uniform(-1.8,1.8); closes.append(price)
        opens=[x+random.uniform(-.4,.4) for x in closes]
        highs=[max(o,c)+random.uniform(.1,.8) for o,c in zip(opens,closes)]
        lows=[min(o,c)-random.uniform(.1,.8) for o,c in zip(opens,closes)]
        checkpoint, _ = initialize_incremental_state(opens[:420],highs[:420],lows[:420],closes[:420])
        inc, checkpoint = update_incremental_state(checkpoint,opens[420:],highs[420:],lows[420:],closes[420:])
        full=calculate_trend_impulse_channels(opens,highs,lows,closes)
        for key in ('atr','trend','direction','bars_in_trend','extension','upper','lower','trend_step'):
            for actual, expected in zip(inc[key],full[key][420:]):
                if expected is None: self.assertIsNone(actual)
                elif isinstance(expected,bool): self.assertEqual(actual,expected)
                else: self.assertAlmostEqual(float(actual),float(expected),places=9)

    def test_state_contains_only_compact_checkpoint_and_params(self):
        n=240; closes=[100+i*.1 for i in range(n)]; opens=closes[:]
        highs=[x+1 for x in closes]; lows=[x-1 for x in closes]
        state,_=initialize_incremental_state(opens,highs,lows,closes)
        self.assertEqual(state['version'],1)
        self.assertEqual(state['count'],n)
        self.assertNotIn('candles',state)

if __name__=='__main__': unittest.main()
