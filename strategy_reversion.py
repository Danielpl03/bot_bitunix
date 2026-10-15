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
    recovery_mode: bool = False,
    recovery_lookback: int = 5,
) -> ReversionSignal | None:
    """Detecta una señal al cierre, sin utilizar velas futuras.

    recovery_mode=False conserva la regla original (cierre fuera de la banda).
    recovery_mode=True exige que el precio haya tocado/cruzado el extremo del
    canal durante las últimas `recovery_lookback` velas y que la vela actual
    cierre de nuevo dentro de las bandas, con cruce estocástico confirmado.
    """
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

    if recovery_mode:
        # La banda se recalcula en cada vela del lookback; no se compara el precio
        # histórico contra la banda actual, para evitar una condición inconsistente.
        lookback = min(max(1, recovery_lookback), len(closes))
        touched_lower = touched_upper = False
        for j in range(len(closes) - lookback, len(closes)):
            try:
                lower_j, _, upper_j = bollinger_bands(closes[:j + 1], bb_period, bb_std)
            except ValueError:
                continue
            touched_lower = touched_lower or lows[j] <= lower_j or closes[j] < lower_j
            touched_upper = touched_upper or highs[j] >= upper_j or closes[j] > upper_j

        # LONG: extremo inferior reciente, cierre actual de vuelta sobre la banda
        # inferior y cruce alcista del estocástico con ambas líneas en sobreventa.
        if (touched_lower and close > lower and k_prev <= d_prev and k_now > d_now
                and k_now < oversold and d_now < oversold):
            if stop_long < close and middle > close:
                return ReversionSignal("LONG", close, k_now, d_now, lower, middle, upper, stop_long, middle)

        # SHORT: extremo superior reciente, cierre actual de vuelta bajo la banda
        # superior y cruce bajista con ambas líneas en sobrecompra.
        if (touched_upper and close < upper and k_prev >= d_prev and k_now < d_now
                and k_now > overbought and d_now > overbought):
            if stop_short > close and middle < close:
                return ReversionSignal("SHORT", close, k_now, d_now, lower, middle, upper, stop_short, middle)
    else:
        # Modo original: cierre fuera de la banda + cruce estocástico extremo.
        if close < lower and k_prev <= d_prev and k_now > d_now and k_now < oversold and d_now < oversold:
            if stop_long < close and middle > close:
                return ReversionSignal("LONG", close, k_now, d_now, lower, middle, upper, stop_long, middle)

        if close > upper and k_prev >= d_prev and k_now < d_now and k_now > overbought and d_now > overbought:
            if stop_short > close and middle < close:
                return ReversionSignal("SHORT", close, k_now, d_now, lower, middle, upper, stop_short, middle)
    return None
