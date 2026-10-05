"""
Estrategia de entrada en velas de 4h (estocástico + Bandas de Bollinger).

Módulo puro: no conoce Bitunix ni Telegram, solo recibe listas de precios
(ya extraídas de las velas, ordenadas de más antigua a más reciente) y
decide si al cierre de la última vela hay señal de entrada.

Reglas (tal como las describiste):

  LONG
    1) La línea rápida del estocástico (%K) cruza hacia arriba a la lenta
       (%D) en el cierre de la vela actual.
    2) En las velas recientes anteriores a ese cruce, %K estuvo en zona de
       sobreventa o cerca de ella (no hace falta que llegara a estar
       estrictamente por debajo de 20).
    3) El precio de cierre está por debajo de la banda media de Bollinger
       y más cerca de la banda inferior que de la media.

  SHORT: exactamente lo contrario (cruce hacia abajo, sobrecompra reciente,
  precio por encima de la media y más cerca de la banda superior que de
  la media).

Los umbrales de sobreventa/sobrecompra y la ventana de "reciente" son
parámetros ajustables (`oversold`, `overbought`, `lookback`). Los valores
por defecto son una aproximación razonable a "cerca de la zona" — conviene
revisarlos contra velas pasadas donde tu estrategia sí habría entrado y
ajustarlos si hace falta.
"""

from __future__ import annotations

from dataclasses import dataclass

from indicators import bollinger_bands, stochastic_series


@dataclass
class Signal:
    side: str  # "LONG" o "SHORT"
    k: float
    d: float
    close: float
    lower: float
    middle: float
    upper: float


def detect_signal(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    oversold: float = 25.0,
    overbought: float = 75.0,
    lookback: int = 10,
) -> Signal | None:
    """
    Evalúa la última vela cerrada (closes[-1] / highs[-1] / lows[-1]) y
    devuelve un Signal si se cumplen las condiciones de LONG o SHORT, o
    None si no hay señal (incluido el caso de no tener aún velas
    suficientes para calcular los indicadores).
    """
    try:
        k_series, d_series = stochastic_series(closes, highs, lows)
    except ValueError:
        return None  # no hay suficiente historial todavía para el estocástico

    if len(k_series) < lookback + 2:
        return None  # no hay suficiente historial para mirar hacia atrás

    k_now, k_prev = k_series[-1], k_series[-2]
    d_now, d_prev = d_series[-1], d_series[-2]

    close = closes[-1]
    try:
        lower, middle, upper = bollinger_bands(closes)
    except ValueError:
        return None  # no hay suficiente historial todavía para Bollinger

    crossed_up = k_prev <= d_prev and k_now > d_now
    crossed_down = k_prev >= d_prev and k_now < d_now

    # Ventana de %K justo antes del cruce (sin incluir el valor del cruce).
    recent_k = k_series[-(lookback + 1) : -1]

    if (
        crossed_up
        and min(recent_k) <= oversold
        and close < middle
        and close < (lower + middle) / 2
    ):
        return Signal("LONG", k_now, d_now, close, lower, middle, upper)

    if (
        crossed_down
        and max(recent_k) >= overbought
        and close > middle
        and close > (middle + upper) / 2
    ):
        return Signal("SHORT", k_now, d_now, close, lower, middle, upper)

    return None
