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
    