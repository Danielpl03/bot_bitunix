"""
Indicadores técnicos calculados a partir de velas (OHLC).

Este módulo no conoce nada de Bitunix ni de Telegram: recibe listas de
precios (ya extraídas de las velas) y devuelve los valores calculados.
Bitunix no expone los indicadores ya calculados por su API — solo el
histórico de velas (`BitunixClient.get_kline`) — así que el cálculo se
hace aquí con la fórmula estándar de cada indicador.

Todas las funciones esperan las listas ordenadas de la vela más antigua
a la más reciente (que es el orden que devuelve `BitunixClient.get_kline`).
"""

from __future__ import annotations


def _sma(values: list[float], period: int) -> float:
    return sum(values[-period:]) / period


def stochastic_series(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    k_period: int = 14,
    smooth_k: int = 3,
    d_period: int = 3,
) -> tuple[list[float], list[float]]:
    """
    Igual que `stochastic_oscillator`, pero devuelve la serie completa de
    %K y %D (ya suavizados) en vez de solo el último valor. Las series
    quedan alineadas en el tiempo entre sí (mismo largo; el último
    elemento de cada una corresponde a la misma vela) — la necesita
    cualquier cálculo que mire hacia atrás, como detectar cruces
    (ver strategy.py).
    """
    min_len = k_period + smooth_k + d_period - 2
    if len(closes) < min_len or len(highs) < min_len or len(lows) < min_len:
        raise ValueError(
            f"Se necesitan al menos {min_len} velas para calcular el estocástico "
            f"({k_period},{smooth_k},{d_period})."
        )

    raw_k = []
    for i in range(k_period - 1, len(closes)):
        window_high = max(highs[i - k_period + 1 : i + 1])
        window_low = min(lows[i - k_period + 1 : i + 1])
        if window_high == window_low:
            raw_k.append(50.0)  # rango plano: ni sobrecompra ni sobreventa
        else:
            raw_k.append((closes[i] - window_low) / (window_high - window_low) * 100)

    smoothed_k = [_sma(raw_k[: i + 1], smooth_k) for i in range(smooth_k - 1, len(raw_k))]
    d_values = [_sma(smoothed_k[: i + 1], d_period) for i in range(d_period - 1, len(smoothed_k))]
    k_aligned = smoothed_k[d_period - 1 :]  # recorta %K para que coincida en el tiempo con %D

    return k_aligned, d_values


def stochastic_oscillator(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    k_period: int = 14,
    smooth_k: int = 3,
    d_period: int = 3,
) -> tuple[float, float]:
    """
    Oscilador estocástico "lento" (el que usan por defecto la mayoría de
    plataformas, incluida la app de Bitunix): %K crudo suavizado con una
    media móvil de `smooth_k` periodos, y %D como media móvil de `d_period`
    periodos sobre ese %K ya suavizado.

    Devuelve solo el último (%K, %D). Para la serie completa usa
    `stochastic_series`.
    """
    k_series, d_series = stochastic_series(closes, highs, lows, k_period, smooth_k, d_period)
    return k_series[-1], d_series[-1]


def bollinger_bands(
    closes: list[float],
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[float, float, float]:
    """
    Bandas de Bollinger clásicas: media móvil simple de `period` velas y
    +/- `num_std` desviaciones típicas (poblacionales) de esa misma ventana.

    Devuelve (banda_inferior, media, banda_superior).
    """
    if len(closes) < period:
        raise ValueError(f"Se necesitan al menos {period} velas para calcular las Bandas de Bollinger.")

    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((c - middle) ** 2 for c in window) / period
    std = variance**0.5

    return middle - num_std * std, middle, middle + num_std * std


def _ema_series(values: list[float], period: int) -> list[float]:
    """
    Serie de la media móvil exponencial (EMA). Se siembra con la media simple
    de las primeras `period` velas. El resultado queda alineado al final de
    `values` y tiene `len(values) - period + 1` elementos.
    """
    alpha = 2 / (period + 1)
    ema = sum(values[:period]) / period
    out = [ema]
    for v in values[period:]:
        ema = alpha * v + (1 - alpha) * ema
        out.append(ema)
    return out


def _atr_series(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    period: int,
) -> list[float]:
    """
    Serie del ATR (Average True Range) con el suavizado de Wilder (RMA), el
    mismo que usan por defecto TradingView y la mayoría de plataformas. El
    rango verdadero de la primera vela es solo high - low. Queda alineada
    al final y tiene `len(closes) - period + 1` elementos.
    """
    true_ranges = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        true_ranges.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )

    atr = sum(true_ranges[:period]) / period
    out = [atr]
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period
        out.append(atr)
    return out


def keltner_series(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    ema_period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
) -> tuple[list[float], list[float], list[float]]:
    """
    Keltner Channel: banda media = EMA de `ema_period` cierres; bandas
    superior e inferior = media +/- `multiplier` x ATR de `atr_period`.
    Los valores por defecto (20, 10, 2) son los del Keltner Channel de
    TradingView.

    Devuelve las series completas (banda_inferior, media, banda_superior),
    alineadas en el tiempo entre sí (mismo largo; el último elemento de
    cada una corresponde a la última vela). Como en `stochastic_series`, se
    recortan las velas iniciales donde algún componente aún no se puede
    calcular, así que la serie es más corta que `closes`.
    """
    min_len = max(ema_period, atr_period)
    if len(closes) < min_len or len(highs) < min_len or len(lows) < min_len:
        raise ValueError(f"Se necesitan al menos {min_len} velas para calcular el Keltner Channel.")

    ema = _ema_series(closes, ema_period)
    atr = _atr_series(closes, highs, lows, atr_period)

    n = min(len(ema), len(atr))  # ambas están alineadas al final: nos quedamos con el tramo común
    ema, atr = ema[-n:], atr[-n:]

    lower = [m - multiplier * a for m, a in zip(ema, atr)]
    upper = [m + multiplier * a for m, a in zip(ema, atr)]
    return lower, ema, upper


def keltner_channel(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    ema_period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
) -> tuple[float, float, float]:
    """Último (banda_inferior, media, banda_superior). Serie completa: `keltner_series`."""
    lower, middle, upper = keltner_series(closes, highs, lows, ema_period, atr_period, multiplier)
    return lower[-1], middle[-1], upper[-1]
    

# --- RSI de TradingView (Pine Script v6) y medias sobre el RSI ---

RSI_MA_TYPES = ("None", "SMA", "SMA + Bollinger Bands", "EMA", "SMMA (RMA)", "WMA", "VWMA")


def _rma_aligned(values: list[float | None], period: int) -> list[float | None]:
    """RMA de Wilder estilo ta.rma: semilla SMA y luego recurrencia de Wilder."""
    if period < 1:
        raise ValueError("El período de RMA debe ser >= 1.")
    out: list[float | None] = [None] * len(values)
    valid: list[float] = []
    seed_index = None
    for i, value in enumerate(values):
        if value is None:
            continue
        valid.append(float(value))
        if len(valid) == period:
            seed_index = i
            seed = sum(valid[-period:]) / period
            out[i] = seed
            previous = seed
            break
    if seed_index is None:
        return out
    for i in range(seed_index + 1, len(values)):
        value = values[i]
        if value is None:
            out[i] = previous
        else:
            previous = (previous * (period - 1) + float(value)) / period
            out[i] = previous
    return out


def rsi_series(source: list[float], period: int = 14) -> list[float | None]:
    """
    Calcula RSI como el indicador Relative Strength Index de TradingView/Pine:
    change = ta.change(source); up/down suavizados con ta.rma (Wilder).

    Devuelve una serie del mismo largo que source, alineada por vela. Los
    primeros valores sin calentamiento son None. Con period=14, el primer
    RSI válido aparece tras disponer de 14 cambios (normalmente índice 14).
    """
    if period < 1:
        raise ValueError("RSI Length debe ser >= 1.")
    if not source:
        return []
    changes: list[float | None] = [None]
    for i in range(1, len(source)):
        changes.append(float(source[i]) - float(source[i - 1]))
    gains = [None if x is None else max(x, 0.0) for x in changes]
    losses = [None if x is None else max(-x, 0.0) for x in changes]
    avg_up = _rma_aligned(gains, period)
    avg_down = _rma_aligned(losses, period)
    out: list[float | None] = [None] * len(source)
    for i, (up, down) in enumerate(zip(avg_up, avg_down)):
        if up is None or down is None:
            continue
        # Mantiene el orden de condiciones del Pine de referencia.
        if down == 0:
            out[i] = 100.0
        elif up == 0:
            out[i] = 0.0
        else:
            out[i] = 100.0 - (100.0 / (1.0 + up / down))
    return out


def moving_average_series(
    values: list[float | None],
    period: int = 14,
    ma_type: str = "SMA",
    volume: list[float] | None = None,
) -> list[float | None]:
    """
    Media móvil alineada sobre una serie (por ejemplo, el RSI), siguiendo las
    opciones del Pine de referencia. Los valores iniciales no disponibles se
    representan como None. VWMA requiere volumen alineado con values.
    """
    if period < 1:
        raise ValueError("El período de la media móvil debe ser >= 1.")
    if ma_type not in RSI_MA_TYPES:
        raise ValueError(f"Tipo de media no válido: {ma_type}. Opciones: {', '.join(RSI_MA_TYPES)}")
    if ma_type == "None":
        return [None] * len(values)
    if ma_type == "VWMA":
        if volume is None or len(volume) != len(values):
            raise ValueError("VWMA requiere una serie de volumen del mismo largo que el RSI.")

    out: list[float | None] = [None] * len(values)
    valid_indices: list[int] = []
    valid_values: list[float] = []
    ema_previous: float | None = None
    rma_previous: float | None = None
    alpha = 2.0 / (period + 1.0)
    for i, value in enumerate(values):
        if value is None:
            continue
        valid_indices.append(i)
        valid_values.append(float(value))
        n = len(valid_values)
        if n < period:
            continue
        if ma_type in ("SMA", "SMA + Bollinger Bands"):
            out[i] = sum(valid_values[-period:]) / period
        elif ma_type == "WMA":
            window = valid_values[-period:]
            denom = period * (period + 1) / 2
            out[i] = sum(v * weight for weight, v in enumerate(window, 1)) / denom
        elif ma_type == "VWMA":
            # Los valores None al inicio del RSI no entran a la ventana válida.
            idx_window = valid_indices[-period:]
            weights = [float(volume[j]) for j in idx_window]
            denom = sum(weights)
            out[i] = (sum(float(values[j]) * w for j, w in zip(idx_window, weights)) / denom
                      if denom != 0 else None)
        elif ma_type == "EMA":
            if n == period:
                ema_previous = sum(valid_values[:period]) / period
            else:
                ema_previous = alpha * float(value) + (1 - alpha) * float(ema_previous)
            out[i] = ema_previous
        elif ma_type == "SMMA (RMA)":
            if n == period:
                rma_previous = sum(valid_values[:period]) / period
            else:
                rma_previous = (float(rma_previous) * (period - 1) + float(value)) / period
            out[i] = rma_previous
    return out


def rsi_indicator(
    source: list[float],
    rsi_period: int = 14,
    ma_type: str = "SMA",
    ma_period: int = 14,
    *,
    volume: list[float] | None = None,
    bb_mult: float = 2.0,
) -> dict[str, list[float | None] | float | str]:
    """
    Calcula RSI, su media configurable y, cuando se elige SMA + Bollinger
    Bands, bandas +/- desviación típica poblacional * bb_mult.

    Las series RSI/MA/bandas quedan alineadas con source. Incluye los niveles
    fijos de TradingView (70/50/30) en la respuesta.
    """
    if bb_mult < 0.001 or bb_mult > 50:
        raise ValueError("BB StdDev debe estar entre 0.001 y 50.")
    rsi = rsi_series(source, rsi_period)
    ma = moving_average_series(rsi, ma_period, ma_type, volume)
    upper: list[float | None] = [None] * len(rsi)
    lower: list[float | None] = [None] * len(rsi)
    if ma_type == "SMA + Bollinger Bands":
        valid: list[float] = []
        for i, value in enumerate(rsi):
            if value is None:
                continue
            valid.append(float(value))
            if len(valid) >= ma_period and ma[i] is not None:
                window = valid[-ma_period:]
                mean = sum(window) / ma_period
                variance = sum((x - mean) ** 2 for x in window) / ma_period
                stdev = variance ** 0.5
                upper[i] = float(ma[i]) + stdev * bb_mult
                lower[i] = float(ma[i]) - stdev * bb_mult
    return {
        "rsi": rsi,
        "ma": ma,
        "bb_upper": upper,
        "bb_lower": lower,
        "upper_level": 70.0,
        "middle_level": 50.0,
        "lower_level": 30.0,
        "ma_type": ma_type,
    }
