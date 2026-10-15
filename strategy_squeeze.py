"""Estrategia Squeeze: compresión Bollinger/Keltner y ruptura con impulso."""
from __future__ import annotations
from dataclasses import dataclass
from indicators import bollinger_bands, _ema_series, _atr_series

@dataclass(frozen=True)
class SqueezeSignal:
    side: str
    signal_close: float
    bb_lower: float
    bb_middle: float
    bb_upper: float
    kc_lower: float
    kc_middle: float
    kc_upper: float
    momentum: float
    stop_loss: float

def detect_squeeze_signal(closes: list[float], highs: list[float], lows: list[float], *,
                          bb_period: int = 20, bb_std: float = 2.0,
                          kc_ema_period: int = 20, atr_period: int = 20,
                          kc_multiplier: float = 1.5, stop_lookback: int = 10,
                          momentum_period: int = 12) -> SqueezeSignal | None:
    """Señal al cierre actual: compresión en vela previa, liberación actual,
    ruptura de Bollinger y momentum direccional. Solo usa datos disponibles."""
    minimum = max(bb_period, kc_ema_period, atr_period, stop_lookback, momentum_period + 1) + 1
    if len(closes) < minimum:
        return None
    try:
        bb_prev = bollinger_bands(closes[:-1], bb_period, bb_std)
        bb = bollinger_bands(closes, bb_period, bb_std)
        ema_series = _ema_series(closes, kc_ema_period)
        atr_series = _atr_series(closes, highs, lows, atr_period)
    except (ValueError, IndexError):
        return None
    # Alinear EMA/ATR con sus índices originales.
    prev_i = len(closes) - 2
    cur_i = len(closes) - 1
    ema_prev = ema_series[prev_i - kc_ema_period + 1]
    ema_now = ema_series[cur_i - kc_ema_period + 1]
    atr_prev = atr_series[prev_i - atr_period + 1]
    atr_now = atr_series[cur_i - atr_period + 1]
    bb_l_prev, bb_m_prev, bb_u_prev = bb_prev
    bb_l, bb_m, bb_u = bb
    kc_l_prev, kc_u_prev = ema_prev - kc_multiplier * atr_prev, ema_prev + kc_multiplier * atr_prev
    kc_l, kc_u = ema_now - kc_multiplier * atr_now, ema_now + kc_multiplier * atr_now
    was_squeezed = bb_l_prev > kc_l_prev and bb_u_prev < kc_u_prev
    is_squeezed = bb_l > kc_l and bb_u < kc_u
    released = was_squeezed and not is_squeezed
    if not released:
        return None
    momentum = closes[-1] - closes[-1 - momentum_period]
    stop_long = min(lows[-stop_lookback:])
    stop_short = max(highs[-stop_lookback:])
    # Requer ruptura del extremo Bollinger y confirmación direccional del momentum.
    if closes[-1] > bb_u and closes[-1] > kc_u and momentum > 0 and stop_long < closes[-1]:
        return SqueezeSignal("LONG", closes[-1], bb_l, bb_m, bb_u, kc_l, ema_now, kc_u, momentum, stop_long)
    if closes[-1] < bb_l and closes[-1] < kc_l and momentum < 0 and stop_short > closes[-1]:
        return SqueezeSignal("SHORT", closes[-1], bb_l, bb_m, bb_u, kc_l, ema_now, kc_u, momentum, stop_short)
    return None
