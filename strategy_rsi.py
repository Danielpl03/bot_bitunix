"""Señales RSI con alternancia por zonas extremas y entradas escalonadas."""
from __future__ import annotations
from dataclasses import dataclass
from indicators import rsi_indicator

@dataclass(frozen=True)
class RSISignal:
    side: str
    signal_close: float
    rsi: float
    rsi_ma: float
    stop_loss: float
    target: float
    signal_type: str = "MA_CROSS"


def _confirmed_pivot(values: list[float], i: int, left: int, right: int, side: str) -> float | None:
    """Último pivote confirmado hasta i; un pivote requiere sus velas derechas."""
    last_candidate = i - right
    if last_candidate < left:
        return None
    for p in range(last_candidate, left - 1, -1):
        window = values[p-left:p+right+1]
        value = values[p]
        if side == "LONG":
            if value == min(window) and sum(v == value for v in window) == 1:
                return float(value)
        else:
            if value == max(window) and sum(v == value for v in window) == 1:
                return float(value)
    return None


def generate_rsi_signals(
    closes: list[float], highs: list[float], lows: list[float],
    *, rsi_period: int = 14, ma_type: str = "SMA", ma_period: int = 14,
    sl_lookback: int = 10, stop_mode: str = "lookback", pivot_left: int = 2, pivot_right: int = 2, pivot_buffer_pct: float = 0.1,
    oversold: float = 30.0, overbought: float = 70.0,
    long_ma_ceiling: float = 55.0, short_ma_floor: float = 45.0,
    ignore_first_ma_cross: bool = False,
    volume: list[float] | None = None,
) -> list[RSISignal | None]:
    """Señales al cierre de vela, sin usar información futura.

    stop_mode='pivot' usa el último pivote local confirmado; si aún no existe,
    cae al extremo de la ventana sl_lookback. El cruce de media puede ignorar
    opcionalmente el primer cruce de cada régimen. Reentrar en el mismo extremo
    tras haber salido de él reinicia los contadores, sin afectar operaciones abiertas.
    """
    n = len(closes)
    if n != len(highs) or n != len(lows): raise ValueError("Closes, highs y lows deben tener la misma longitud.")
    if sl_lookback < 1: raise ValueError("sl_lookback debe ser >= 1.")
    if stop_mode not in ("lookback", "pivot"): raise ValueError("stop_mode debe ser 'lookback' o 'pivot'.")
    if pivot_left < 1 or pivot_right < 1: raise ValueError("pivot_left y pivot_right deben ser >= 1.")
    if pivot_buffer_pct < 0: raise ValueError("pivot_buffer_pct debe ser >= 0.")
    if volume is not None and len(volume) != n: raise ValueError("Volume debe tener la misma longitud que closes.")
    ind = rsi_indicator(closes, rsi_period=rsi_period, ma_type=ma_type, ma_period=ma_period, volume=volume)
    rsis, mas = ind["rsi"], ind["ma"]
    signals: list[RSISignal | None] = [None] * n
    mode: str | None = None
    first_turn_used = False
    ma_cross_count = 0
    for i in range(1, n):
        rsi, prev_rsi = rsis[i], rsis[i-1]
        ma, prev_ma = mas[i], mas[i-1]
        if rsi is None: continue
        if prev_rsi is None:
            if rsi >= overbought: mode = "SHORT"
            elif rsi <= oversold: mode = "LONG"
            continue

        # Un evento de extremo ocurre al entrar en la zona, no en cada vela que permanece dentro.
        entered_overbought = rsi >= overbought and prev_rsi < overbought
        entered_oversold = rsi <= oversold and prev_rsi > oversold
        if entered_overbought:
            if mode == "SHORT":
                first_turn_used = False; ma_cross_count = 0
            else:
                first_turn_used = False; ma_cross_count = 0
            mode = "SHORT"
        elif entered_oversold:
            if mode == "LONG":
                first_turn_used = False; ma_cross_count = 0
            else:
                first_turn_used = False; ma_cross_count = 0
            mode = "LONG"
        if mode is None: continue

        side = None; signal_type = ""
        if mode == "LONG":
            if not first_turn_used and rsi > prev_rsi:
                side, signal_type, first_turn_used = "LONG", "RSI_TURN", True
            elif ma is not None and prev_ma is not None and prev_rsi <= prev_ma and rsi > ma and rsi < long_ma_ceiling:
                ma_cross_count += 1
                if not (ignore_first_ma_cross and ma_cross_count == 1): side, signal_type = "LONG", "MA_CROSS"
        else:
            if not first_turn_used and rsi < prev_rsi:
                side, signal_type, first_turn_used = "SHORT", "RSI_TURN", True
            elif ma is not None and prev_ma is not None and prev_rsi >= prev_ma and rsi < ma and rsi > short_ma_floor:
                ma_cross_count += 1
                if not (ignore_first_ma_cross and ma_cross_count == 1): side, signal_type = "SHORT", "MA_CROSS"
        if side is None: continue

        start = max(0, i - sl_lookback + 1)
        fallback = float(min(lows[start:i+1]) if side == "LONG" else max(highs[start:i+1]))
        stop = fallback
        if stop_mode == "pivot":
            piv = _confirmed_pivot(lows if side == "LONG" else highs, i, pivot_left, pivot_right, side)
            if piv is not None:
                buffer = pivot_buffer_pct / 100.0
                stop = piv * (1.0 - buffer if side == "LONG" else 1.0 + buffer)
        close = float(closes[i])
        risk = close - stop if side == "LONG" else stop - close
        if risk <= 0: continue
        target = close + 2*risk if side == "LONG" else close - 2*risk
        if target <= 0: continue
        signals[i] = RSISignal(side, close, float(rsi), float(ma) if ma is not None else float('nan'), stop, target, signal_type)
    return signals


def detect_rsi_signal(closes: list[float], highs: list[float], lows: list[float], **kwargs) -> RSISignal | None:
    """Compatibilidad: devuelve la señal más reciente de la serie."""
    signals = generate_rsi_signals(closes, highs, lows, **kwargs)
    return signals[-1] if signals else None
