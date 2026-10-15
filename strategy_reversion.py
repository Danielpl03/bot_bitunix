"""Estrategia de reversión a la media: Bollinger + Estocástico.

Señal evaluada al cierre de la vela actual. La ejecución del backtest se hace
al open de la siguiente vela para evitar asumir una ejecución retrospectiva al
cierre de la vela que confirma la señal.
"""
from __future__ import annotations

from dataclasses import dataclass
from indicators import bollinger_bands, stochastic_series


@dataclass(frozen=True)
class ReversionSignal:
    side: str
    signal_close: float
    k: float
    d: float
    lower: float
    middle: float
    upper: float
    stop_loss: float
    target: float


def detect_reversion_signal(
    closes: list[float], highs: list[float], lows: list[float],
    *, bb_period: int = 20, bb_std: float = 2.0,
    k_period: int = 14, smooth_k: int = 3, d_period: int = 3,
    oversold: float = 20.0, overbought: float = 80.0,
    sl_lookback: int = 10,
) -> ReversionSignal | None:
    """Detecta el cierre que confirma la entrada, sin utilizar velas futuras."""
    min_stoch = k_period + smooth_k + d_period - 2
    if len(closes) < max(bb_period, min_stoch + 1, sl_lookback):
        return None
    try:
        lower, middle, upper = bollinger_bands(closes, bb_period, bb_std)
        ks, ds = stochastic_series(closes, highs, lows, k_period, smooth_k, d_period)
    except ValueError:
        return None
    if len(ks) < 2 or len(ds) < 2:
        return None

    k_prev, k_now = ks[-2], ks[-1]
    d_prev, d_now = ds[-2], ds[-1]
    close = closes[-1]
    stop_long = min(lows[-sl_lookback:])
    stop_short = max(highs[-sl_lookback:])

    # LONG: cierre fuera de la banda inferior + cruce alcista con K y D en sobreventa.
    if close < lower and k_prev <= d_prev and k_now > d_now and k_now < oversold and d_now < oversold:
        if stop_long < close and middle > close:
            return ReversionSignal("LONG", close, k_now, d_now, lower, middle, upper, stop_long, middle)

    # SHORT: cierre fuera de la banda superior + cruce bajista con K y D en sobrecompra.
    if close > upper and k_prev >= d_prev and k_now < d_now and k_now > overbought and d_now > overbought:
        if stop_short > close and middle < close:
            return ReversionSignal("SHORT", close, k_now, d_now, lower, middle, upper, stop_short, middle)
    return None
