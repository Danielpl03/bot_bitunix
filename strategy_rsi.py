"""RSI extremo -> giro inicial, reentradas tras SL y divergencias clásicas.

Las señales se evalúan al cierre de vela. La primera entrada mantiene la regla
original; después se generan candidatos de reentrada por un nuevo giro del RSI
o por divergencia regular precio/RSI. Backtest y motor live solo aceptan esos
candidatos cuando la operación anterior terminó por stop.
"""
from __future__ import annotations
from dataclasses import dataclass
from indicators import rsi_indicator

@dataclass(frozen=True)
class RSISignal:
    side: str
    signal_close: float
    rsi: float
    stop_loss: float
    extreme_start_index: int
    signal_index: int
    signal_type: str = 'RSI_TURN'
    pivot_used: bool = False
    rsi_ma: float | None = None
    target: float | None = None


def _confirmed_pivot_index(values: list[float], i: int, start: int, left: int, right: int, side: str):
    """Índice del pivote más reciente confirmado y formado desde start."""
    last = i - right
    for p in range(last, max(start + left, left) - 1, -1):
        w = values[p-left:p+right+1]
        v = values[p]
        is_pivot = (v == min(w) and sum(x == v for x in w) == 1) if side == 'LONG' else (v == max(w) and sum(x == v for x in w) == 1)
        if len(w) == left + right + 1 and is_pivot:
            return p
    return None


def _confirmed_pivot(values: list[float], i: int, start: int, left: int, right: int, side: str):
    p = _confirmed_pivot_index(values, i, start, left, right, side)
    return float(values[p]) if p is not None else None


def generate_rsi_signals(closes, highs, lows, *, rsi_period=14, oversold=30.0,
                         overbought=70.0, pivot_left=2, pivot_right=2,
                         stop_buffer_pct=0.0, volume=None, **_ignored):
    """Primera señal por giro extremo; candidatos posteriores por nuevo giro o divergencia.

    LONG inicial: RSI entra en sobreventa y gira al alza.
    Reentrada LONG: tras el primer giro, un nuevo mínimo local del RSI seguido
    de giro al alza, o divergencia alcista regular (precio lower-low, RSI higher-low).
    SHORT: comportamiento espejo. Las reentradas se filtran por SL en los motores.
    """
    n=len(closes)
    if not (len(highs)==len(lows)==n): raise ValueError('OHLC debe tener igual longitud')
    if rsi_period < 1 or pivot_left < 1 or pivot_right < 1: raise ValueError('Períodos inválidos')
    if not 0 <= oversold < overbought <= 100: raise ValueError('Niveles RSI inválidos')
    if stop_buffer_pct < 0: raise ValueError('stop_buffer_pct debe ser >= 0')
    ind=rsi_indicator(list(map(float,closes)),rsi_period=rsi_period,ma_type='None',ma_period=1,volume=volume)
    rsis=ind['rsi']; out=[None]*n
    regime=None; regime_start=None; regime_extreme=None
    initial_fired=False; setup_start=0; previous_price_pivot=None
    buf=stop_buffer_pct/100.0

    for i,r in enumerate(rsis):
        if r is None: continue
        prev=rsis[i-1] if i else None
        prevprev=rsis[i-2] if i>1 else None

        # Un nuevo extremo opuesto cambia el régimen. Repetir el mismo extremo
        # no reinicia el ciclo: permite buscar reentradas sin salir de la zona.
        if r <= oversold and regime != 'LONG':
            regime='LONG'; regime_start=i; regime_extreme=float(lows[i])
            initial_fired=False; setup_start=i; previous_price_pivot=None
        elif r >= overbought and regime != 'SHORT':
            regime='SHORT'; regime_start=i; regime_extreme=float(highs[i])
            initial_fired=False; setup_start=i; previous_price_pivot=None
        elif regime == 'LONG':
            regime_extreme=min(float(regime_extreme),float(lows[i]))
        elif regime == 'SHORT':
            regime_extreme=max(float(regime_extreme),float(highs[i]))

        if regime is None:
            continue
        side=regime
        price_values=lows if side=='LONG' else highs
        pivot_idx=_confirmed_pivot_index(price_values,i,regime_start,pivot_left,pivot_right,side)
        divergence=False
        divergence_pivot=None
        if pivot_idx is not None:
            pivot_rsi=rsis[pivot_idx]
            if previous_price_pivot is not None and pivot_rsi is not None:
                old_idx=previous_price_pivot
                old_rsi=rsis[old_idx]
                if old_rsi is not None:
                    if side=='LONG':
                        divergence=float(lows[pivot_idx]) < float(lows[old_idx]) and float(pivot_rsi) > float(old_rsi)
                    else:
                        divergence=float(highs[pivot_idx]) > float(highs[old_idx]) and float(pivot_rsi) < float(old_rsi)
                    divergence_pivot=pivot_idx if divergence else None
            previous_price_pivot=pivot_idx

        if prev is None:
            continue
        turned=(side=='LONG' and r>prev) or (side=='SHORT' and r<prev)
        local_turn=(prevprev is not None and ((side=='LONG' and prev<=prevprev and r>prev) or
                                               (side=='SHORT' and prev>=prevprev and r<prev)))

        signal_type=None; raw_stop=None; pivot_used=False
        if not initial_fired and turned:
            signal_type='RSI_TURN'
            # Mantener el stop estructural original para la primera entrada.
            pivot=_confirmed_pivot(price_values,i,regime_start,pivot_left,pivot_right,side)
            raw_stop=pivot if pivot is not None else float(regime_extreme)
            pivot_used=pivot is not None
        elif initial_fired and divergence and divergence_pivot is not None:
            signal_type='RSI_DIVERGENCE'
            raw_stop=float(price_values[divergence_pivot])
            pivot_used=True
        elif initial_fired and local_turn:
            signal_type='RSI_REENTRY'
            pivot=_confirmed_pivot(price_values,i,setup_start,pivot_left,pivot_right,side)
            raw_stop=pivot if pivot is not None else (min(map(float,lows[setup_start:i+1])) if side=='LONG'
                                                       else max(map(float,highs[setup_start:i+1])))
            pivot_used=pivot is not None

        if signal_type is None:
            continue
        stop=raw_stop*(1-buf if side=='LONG' else 1+buf)
        px=float(closes[i])
        if (side=='LONG' and stop>=px) or (side=='SHORT' and stop<=px):
            if not initial_fired:
                initial_fired=True
                setup_start=i+1
            continue
        out[i]=RSISignal(side,px,float(r),float(stop),int(regime_start),i,signal_type,pivot_used)
        initial_fired=True
        setup_start=i+1
    return out


def detect_rsi_signal(closes, highs, lows, **kwargs):
    signals=generate_rsi_signals(closes,highs,lows,**kwargs)
    return signals[-1] if signals else None
