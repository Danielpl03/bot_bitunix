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

    Devuelve (%K, %D), ambos en el rango 0-100.
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

    return smoothed_k[-1], d_values[-1]


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
