"""Trend Impulse Channels (Zeiierman) — port fiel de Pine Script v6.

El módulo solo calcula el indicador: no contiene reglas de entrada/salida ni
motor de breakout. Todas las series devueltas están alineadas con OHLC y usan
None donde Pine todavía devolvería na.

Fuente: © Zeiierman. Pine Script publicado bajo CC BY-NC-SA 4.0:
https://creativecommons.org/licenses/by-nc-sa/4.0/
https://www.tradingview.com/script/ (Trend Impulse Channels)
"""
from __future__ import annotations

from typing import Any

DEFAULTS = {
    "flip_mult": 2.86,
    "max_step_atr": -0.034,
    "band_mult": 2.02,
    "hold_bars": 0,
    "atr_period": 200,
    "step_base_mult": 2.52,
    "step_growth_per_bar": 0.0093,
    "show_retest_signals": True,
    "trend_filter": True,
    "show_trend_step_signals": False,
}


def _atr_rma_series(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float | None]:
    """ta.atr(period): TR seguido de RMA de Wilder, semilla SMA."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period:
        return out
    tr = [float(highs[0]) - float(lows[0])]
    for i in range(1, n):
        h, low, prev_close = float(highs[i]), float(lows[i]), float(closes[i - 1])
        tr.append(max(h - low, abs(h - prev_close), abs(low - prev_close)))
    value = sum(tr[:period]) / period
    out[period - 1] = value
    for i in range(period, n):
        value = (value * (period - 1) + tr[i]) / period
        out[i] = value
    return out


def calculate_trend_impulse_channels(
    opens: list[float] | None,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    flip_mult: float = DEFAULTS["flip_mult"],
    max_step_atr: float = DEFAULTS["max_step_atr"],
    band_mult: float = DEFAULTS["band_mult"],
    hold_bars: int = DEFAULTS["hold_bars"],
    atr_period: int = DEFAULTS["atr_period"],
    step_base_mult: float = DEFAULTS["step_base_mult"],
    step_growth_per_bar: float = DEFAULTS["step_growth_per_bar"],
    show_retest_signals: bool = DEFAULTS["show_retest_signals"],
    trend_filter: bool = DEFAULTS["trend_filter"],
    show_trend_step_signals: bool = DEFAULTS["show_trend_step_signals"],
) -> dict[str, list[Any]]:
    """Calcula cada serie del indicador en orden cronológico (antigua→nueva).

    Se conserva la expresión Pine `math.min(stepBase +
    0.0093 * barsInTrend * atr, maxStep)`, incluso cuando `max_step_atr` es
    negativo. También se replica el reinicio recurrente `if na(trend)`, que
    ocurre en todas las velas del calentamiento ATR, no solo en la primera.
    """
    n = len(closes)
    if not (len(highs) == len(lows) == n) or (opens is not None and len(opens) != n):
        raise ValueError("OHLC debe tener igual longitud")
    if n == 0:
        return {key: [] for key in (
            "atr", "trigger", "step_base", "max_step", "step_size", "trend", "direction",
            "trend_direction", "bars_in_trend", "extension", "upper", "lower", "trend_step",
            "lower_retest", "upper_retest", "bullish_step", "bearish_step",
        )}
    if atr_period < 1 or hold_bars < 0:
        raise ValueError("atr_period debe ser >= 1 y hold_bars >= 0")
    if flip_mult < 0 or band_mult < 0:
        raise ValueError("flip_mult y band_mult deben ser >= 0")

    c = list(map(float, closes)); h = list(map(float, highs)); lo = list(map(float, lows))
    atrs = _atr_rma_series(h, lo, c, atr_period)
    keys = ("trigger", "step_base", "max_step", "step_size", "trend", "direction", "bars_in_trend",
            "extension", "upper", "lower", "trend_direction", "trend_step", "lower_retest", "upper_retest",
            "bullish_step", "bearish_step")
    out: dict[str, list[Any]] = {key: [None] * n for key in keys}
    out["atr"] = atrs

    trend: float | None = None
    direction = 0
    bars_in_trend = 0
    extension = 0
    previous_low: float | None = None
    previous_upper: float | None = None
    previous_high: float | None = None
    previous_lower: float | None = None
    previous_trend: float | None = None

    for i in range(n):
        atr = atrs[i]
        trigger = atr * flip_mult if atr is not None else None
        step_base = atr * step_base_mult if atr is not None else None
        max_step = atr * max_step_atr if atr is not None else None
        step_size = (min(step_base + step_growth_per_bar * bars_in_trend * atr, max_step)
                     if atr is not None and step_base is not None and max_step is not None else None)
        out["trigger"][i] = trigger
        out["step_base"][i] = step_base
        out["max_step"][i] = max_step
        out["step_size"][i] = step_size

        # Pine calcula startLong/startShort con nz(trend), que sustituye na por 0.
        trend_for_signal = trend if trend is not None else 0.0
        start_long = trigger is not None and c[i] > trend_for_signal + trigger
        start_short = trigger is not None and c[i] < trend_for_signal - trigger
        flip = (start_long or start_short) and bars_in_trend >= 0

        # Pine usa `if na(trend)` en CADA vela, no solamente en la primera.
        # Mientras ATR(200) sea na, la rama `else` convierte trend en na;
        # por tanto Pine vuelve a inicializar trend/dir/bars/extension en la
        # siguiente vela. La primera vela con ATR válido también inicializa
        # trend al close y NO evalúa flip en esa misma vela.
        if trend is None:
            trend = c[i]
            direction = 0
            bars_in_trend = 0
            extension = 0
        elif flip and extension <= 0:
            trend = c[i]
            direction = 1 if start_long else -1
            bars_in_trend = 1
            extension = hold_bars
        else:
            # En Pine, una operación aritmética con na produce na. Esto importa
            # durante el calentamiento ATR de 200 barras.
            if trend is None or step_size is None:
                trend = None
            else:
                trend = trend + (step_size if direction == 1 else -step_size if direction == -1 else 0.0)
            bars_in_trend += 1
            extension = max(extension - 1, 0)

        out["trend"][i] = trend
        out["direction"][i] = direction
        out["trend_direction"][i] = direction
        out["bars_in_trend"][i] = bars_in_trend
        out["extension"][i] = extension

        upper = trend + atr * band_mult if trend is not None and atr is not None else None
        lower = trend - atr * band_mult if trend is not None and atr is not None else None
        out["upper"][i] = upper
        out["lower"][i] = lower

        trend_step = (trend is not None and previous_trend is not None and direction != 0
                      and trend != previous_trend
                      and ((trend > previous_trend and direction == 1)
                           or (trend < previous_trend and direction == -1)))
        out["trend_step"][i] = bool(trend_step)
        out["bullish_step"][i] = bool(trend_step and direction == 1 and show_trend_step_signals)
        out["bearish_step"][i] = bool(trend_step and direction == -1 and show_trend_step_signals)

        crossunder = (lower is not None and previous_lower is not None and previous_low is not None
                      and lo[i] < lower and previous_low >= previous_lower)
        crossover = (upper is not None and previous_upper is not None and previous_high is not None
                     and h[i] > upper and previous_high <= previous_upper)
        if trend_filter:
            crossunder = bool(crossunder and direction == 1)
            crossover = bool(crossover and direction == -1)
        out["lower_retest"][i] = bool(crossunder and show_retest_signals)
        out["upper_retest"][i] = bool(crossover and show_retest_signals)

        previous_trend = trend
        previous_low, previous_upper = lo[i], upper
        previous_high, previous_lower = h[i], lower

    return out


def latest_trend_impulse_channels(*args, **kwargs) -> dict[str, Any]:
    """Devuelve los valores de la última vela, además de la serie completa."""
    series = calculate_trend_impulse_channels(*args, **kwargs)
    return {key: (values[-1] if values else None) for key, values in series.items()}


def initialize_incremental_state(opens, highs, lows, closes, **params):
    """Warm-up once from historical candles and return a compact recursive checkpoint."""
    series = calculate_trend_impulse_channels(opens, highs, lows, closes, **params)
    if not closes:
        return {}, series
    period = int(params.get('atr_period', DEFAULTS['atr_period']))
    # Reconstruct the Wilder seed/previous close. After warmup only the latest RMA is needed.
    tr_values = []
    for i in range(len(closes)):
        tr = float(highs[i])-float(lows[i]) if i == 0 else max(
            float(highs[i])-float(lows[i]),
            abs(float(highs[i])-float(closes[i-1])),
            abs(float(lows[i])-float(closes[i-1])))
        tr_values.append(tr)
    valid = [x for x in series['atr'] if x is not None]
    state = {
        'version': 1, 'count': len(closes), 'prev_close': float(closes[-1]),
        'atr_count': min(len(tr_values), period),
        'atr_seed_sum': sum(tr_values[:period]) if len(tr_values) < period else 0.0,
        'atr': float(valid[-1]) if valid else None,
        'trend': series['trend'][-1], 'direction': int(series['direction'][-1] or 0),
        'bars_in_trend': int(series['bars_in_trend'][-1] or 0),
        'extension': int(series['extension'][-1] or 0),
        'prev_high': float(highs[-1]), 'prev_low': float(lows[-1]),
        'prev_upper': series['upper'][-1], 'prev_lower': series['lower'][-1],
        'prev_trend': series['trend'][-1],
        'params': {k: params[k] for k in params if k in DEFAULTS},
    }
    return state, series


def update_incremental_state(state, opens, highs, lows, closes, **overrides):
    """Append closed candles using the persisted ATR-RMA and Pine recursive trend state."""
    if not state or state.get('version') != 1:
        raise ValueError('Estado TIC incremental inexistente o incompatible')
    p = dict(state.get('params') or {}); p.update(overrides)
    flip_mult=float(p.get('flip_mult',DEFAULTS['flip_mult'])); max_step_atr=float(p.get('max_step_atr',DEFAULTS['max_step_atr']))
    band_mult=float(p.get('band_mult',DEFAULTS['band_mult'])); hold_bars=int(p.get('hold_bars',DEFAULTS['hold_bars']))
    period=int(p.get('atr_period',DEFAULTS['atr_period'])); step_base_mult=float(p.get('step_base_mult',DEFAULTS['step_base_mult']))
    growth=float(p.get('step_growth_per_bar',DEFAULTS['step_growth_per_bar']))
    n=len(closes)
    if not all(len(x)==n for x in (opens,highs,lows)): raise ValueError('OHLC incremental debe tener igual longitud')
    out={k:[] for k in ('atr','trigger','step_base','max_step','step_size','trend','direction','trend_direction','bars_in_trend','extension','upper','lower','trend_step','lower_retest','upper_retest','bullish_step','bearish_step')}
    for j in range(n):
        o,h,lo,c=map(float,(opens[j],highs[j],lows[j],closes[j]))
        prev_close=state.get('prev_close')
        tr=(h-lo) if prev_close is None else max(h-lo,abs(h-float(prev_close)),abs(lo-float(prev_close)))
        atr_count=int(state.get('atr_count',0)); atr=state.get('atr')
        if atr is None:
            atr_count += 1
            seed=float(state.get('atr_seed_sum',0.0))+tr
            state['atr_seed_sum']=seed
            if atr_count>=period:
                atr=seed/period; state['atr_seed_sum']=0.0
        else:
            atr=(float(atr)*(period-1)+tr)/period
        state['atr_count']=atr_count; state['atr']=atr
        trigger=atr*flip_mult if atr is not None else None
        step_base=atr*step_base_mult if atr is not None else None
        max_step=atr*max_step_atr if atr is not None else None
        step_size=min(step_base+growth*int(state.get('bars_in_trend',0))*atr,max_step) if atr is not None else None
        trend=state.get('trend'); bars=int(state.get('bars_in_trend',0)); direction=int(state.get('direction',0)); extension=int(state.get('extension',0))
        trend_for_signal=float(trend) if trend is not None else 0.0
        start_long=trigger is not None and c>trend_for_signal+trigger
        start_short=trigger is not None and c<trend_for_signal-trigger
        flip=(start_long or start_short) and bars>=0
        if trend is None:
            trend=c; direction=0; bars=0; extension=0
        elif flip and extension<=0:
            trend=c; direction=1 if start_long else -1; bars=1; extension=hold_bars
        else:
            trend=(float(trend)+(step_size if direction==1 else -step_size if direction==-1 else 0.0)) if step_size is not None else None
            bars+=1; extension=max(extension-1,0)
        upper=trend+atr*band_mult if trend is not None and atr is not None else None
        lower=trend-atr*band_mult if trend is not None and atr is not None else None
        prev_trend=state.get('prev_trend'); prev_upper=state.get('prev_upper'); prev_lower=state.get('prev_lower')
        prev_high=state.get('prev_high'); prev_low=state.get('prev_low')
        trend_step=bool(trend is not None and prev_trend is not None and direction!=0 and trend!=prev_trend and ((trend>prev_trend and direction==1) or (trend<prev_trend and direction==-1)))
        crossunder=bool(lower is not None and prev_lower is not None and prev_low is not None and lo<lower and float(prev_low)>=float(prev_lower))
        crossover=bool(upper is not None and prev_upper is not None and prev_high is not None and h>upper and float(prev_high)<=float(prev_upper))
        if bool(p.get('trend_filter',DEFAULTS['trend_filter'])):
            crossunder=crossunder and direction==1; crossover=crossover and direction==-1
        vals={'atr':atr,'trigger':trigger,'step_base':step_base,'max_step':max_step,'step_size':step_size,'trend':trend,'direction':direction,'trend_direction':direction,'bars_in_trend':bars,'extension':extension,'upper':upper,'lower':lower,'trend_step':trend_step,'lower_retest':bool(crossunder and p.get('show_retest_signals',True)),'upper_retest':bool(crossover and p.get('show_retest_signals',True)),'bullish_step':bool(trend_step and direction==1 and p.get('show_trend_step_signals',False)),'bearish_step':bool(trend_step and direction==-1 and p.get('show_trend_step_signals',False))}
        for k,v in vals.items(): out[k].append(v)
        state.update({'prev_close':c,'trend':trend,'direction':direction,'bars_in_trend':bars,'extension':extension,'prev_high':h,'prev_low':lo,'prev_upper':upper,'prev_lower':lower,'prev_trend':trend,'count':int(state.get('count',0))+1})
    state['params']={**p,'atr_period':period}
    return out,state
