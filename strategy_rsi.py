"""RSI extremo -> primer giro, con stop estructural y salida por extremo opuesto.

Reglas compatibles con el RSI estándar de TradingView: close, RMA/Wilder 14.
Las señales se evalúan al cierre de vela; la ejecución de backtest es en la
apertura siguiente. Este módulo no usa MA RSI ni divergencias.
"""
from __future__ import annotations
from dataclasses import dataclass
from indicators import rsi_indicator

@dataclass(frozen=True)
class RSISignal:
    side: str                       # LONG / SHORT
    signal_close: float
    rsi: float
    stop_loss: float
    extreme_start_index: int
    signal_index: int
    signal_type: str = 'RSI_TURN'
    pivot_used: bool = False
    rsi_ma: float | None = None     # compatibilidad de informes antiguos
    target: float | None = None    # no existe objetivo fijo: salida RSI a mercado


def _confirmed_pivot(values: list[float], i: int, start: int, left: int, right: int, side: str):
    """Devuelve el pivote más reciente confirmado y formado desde start."""
    last = i - right
    for p in range(last, max(start + left, left) - 1, -1):
        w = values[p-left:p+right+1]
        v = values[p]
        if len(w) == left + right + 1 and ((v == min(w) and sum(x == v for x in w) == 1) if side == 'LONG'
                                           else (v == max(w) and sum(x == v for x in w) == 1)):
            return float(v)
    return None


def generate_rsi_signals(closes, highs, lows, *, rsi_period=14, oversold=30.0,
                         overbought=70.0, pivot_left=2, pivot_right=2,
                         stop_buffer_pct=0.0, volume=None, **_ignored):
    """Señales de primer giro tras entrar en extremo; fallback al extremo del régimen.

    LONG: entra RSI <= oversold, espera RSI[i] > RSI[i-1]. Stop bajo el mínimo
    desde la entrada en sobreventa, salvo que exista pivote low confirmado
    posterior al inicio del régimen (en cuyo caso se usa ese pivote).
    SHORT: espejo con sobrecompra y máximo.
    """
    n=len(closes)
    if not (len(highs)==len(lows)==n): raise ValueError('OHLC debe tener igual longitud')
    if rsi_period < 1 or pivot_left < 1 or pivot_right < 1: raise ValueError('Períodos inválidos')
    if not 0 <= oversold < overbought <= 100: raise ValueError('Niveles RSI inválidos')
    if stop_buffer_pct < 0: raise ValueError('stop_buffer_pct debe ser >= 0')
    ind=rsi_indicator(list(map(float,closes)),rsi_period=rsi_period,ma_type='None',ma_period=1,volume=volume)
    rsis=ind['rsi']; out=[None]*n
    regime=None; regime_start=None; regime_extreme=None; fired=False
    for i,r in enumerate(rsis):
        if r is None: continue
        prev=rsis[i-1] if i else None
        # Entrar en un nuevo extremo abre un nuevo ciclo y reinicia el extremo.
        if r <= oversold and (regime != 'LONG'):
            regime='LONG'; regime_start=i; regime_extreme=float(lows[i]); fired=False
        elif r >= overbought and (regime != 'SHORT'):
            regime='SHORT'; regime_start=i; regime_extreme=float(highs[i]); fired=False
        elif regime == 'LONG':
            regime_extreme=min(float(regime_extreme),float(lows[i]))
        elif regime == 'SHORT':
            regime_extreme=max(float(regime_extreme),float(highs[i]))
        if prev is None or regime is None or fired: continue
        turned = (regime=='LONG' and r>prev) or (regime=='SHORT' and r<prev)
        if not turned: continue
        side=regime
        pivot=_confirmed_pivot(lows if side=='LONG' else highs,i,regime_start,pivot_left,pivot_right,side)
        raw_stop=pivot if pivot is not None else float(regime_extreme)
        buf=stop_buffer_pct/100.0
        stop=raw_stop*(1-buf if side=='LONG' else 1+buf)
        px=float(closes[i])
        if (side=='LONG' and stop>=px) or (side=='SHORT' and stop<=px):
            # invalid stop: no trade; keep regime armed only until first turn by design
            fired=True
            continue
        out[i]=RSISignal(side,px,float(r),float(stop),int(regime_start),i,'RSI_TURN',pivot is not None)
        fired=True
    return out


def detect_rsi_signal(closes, highs, lows, **kwargs):
    signals=generate_rsi_signals(closes,highs,lows,**kwargs)
    return signals[-1] if signals else None
