"""Dual Thrust Breakout según la investigación suministrada."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class DualThrustLevels:
    range_value: float
    buy_track: float
    sell_track: float


def calculate_levels(opens: list[float], highs: list[float], lows: list[float], closes: list[float],
                     index: int, *, lookback: int = 4, k1: float = 0.6, k2: float = 0.7) -> DualThrustLevels | None:
    """Calcula niveles de la vela `index` usando exclusivamente las `lookback` velas previas."""
    if lookback < 1 or min(k1, k2) <= 0 or index < lookback:
        return None
    start = index - lookback
    prior_highs = highs[start:index]
    prior_lows = lows[start:index]
    prior_closes = closes[start:index]
    hh, ll = max(prior_highs), min(prior_lows)
    hc, lc = max(prior_closes), min(prior_closes)
    range_value = max(hh - lc, hc - ll)
    open_price = float(opens[index])
    return DualThrustLevels(range_value, open_price + k1 * range_value, open_price - k2 * range_value)
