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

    4) La vela de la señal es alcista (cierre > apertura): va en la
       dirección de la operación.
    5) El objetivo de ratio 0.5:1 (ver `take_profit_level`) queda por
       debajo de la banda media de Bollinger del momento de la señal, es
       decir, el primer objetivo se alcanza antes de llegar a la media.

  SHORT: exactamente lo contrario (cruce hacia abajo, sobrecompra reciente,
  precio por encima de la media y más cerca de la banda superior que de
  la media, vela bajista, y objetivo 0.5:1 por encima de la banda media).

Los umbrales de sobreventa/sobrecompra y la ventana de "reciente" son
parámetros ajustables (`oversold`, `overbought`, `lookback`). Los valores
por defecto son una aproximación razonable a "cerca de la zona" — conviene
revisarlos contra velas pasadas donde tu estrategia sí habría entrado y
ajustarlos si hace falta.

Stop loss (mínimo/máximo local):

  LONG: mientras el precio cae va marcando mínimos cada vez más bajos;
  el stop se fija en el mínimo local de ese movimiento — el `low` más
  bajo dentro de las últimas `sl_lookback` velas (incluida la vela de
  la señal, por si el mínimo de todo el movimiento lo marcó justo esa
  vela con una mecha larga). Si el precio vuelve a caer hasta ahí,
  significa que el mínimo que motivó la entrada ya no se sostuvo.

  SHORT: exactamente lo contrario, con el `high` más alto de esas
  mismas velas.

`sl_lookback` es independiente de `lookback` (el de sobrecompra/
sobreventa) a propósito: la ventana en la que buscar el mínimo/máximo
local no tiene por qué coincidir con la ventana en la que se exige que
el estocástico haya estado en zona extrema.

Objetivo por ratio (`take_profit_level`):

  La distancia entrada → stop es el "1R" de la operación. Un ratio 0.5:1
  significa un objetivo a 0.5 veces esa distancia (a favor de la
  operación); un ratio 1:1, a 1 vez esa distancia. El backtest y, más
  adelante, las órdenes reales a Bitunix usan esta misma función para
  que ambos calculen exactamente el mismo nivel.
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
    stop_loss: float  # mínimo local (LONG) o máximo local (SHORT) de las últimas sl_lookback velas


def detect_signal(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    opens: list[float],
    oversold: float = 25.0,
    overbought: float = 75.0,
    lookback: int = 10,
    sl_lookback: int = 10,
    band_ratio: float = 0.5,
) -> Signal | None:
    """
    Evalúa la última vela cerrada (closes[-1] / highs[-1] / lows[-1] /
    opens[-1]) y devuelve un Signal si se cumplen las condiciones de LONG o
    SHORT, o None si no hay señal (incluido el caso de no tener aún velas
    suficientes para calcular los indicadores).

    `band_ratio` es el ratio del objetivo que debe quedar antes de la banda
    media de Bollinger (0.5 por defecto, el del primer escenario de TP).
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

    if len(lows) < sl_lookback or len(highs) < sl_lookback or not opens:
        return None  # no hay suficientes velas todavía para ubicar el mínimo/máximo local

    open_ = opens[-1]

    if (
        crossed_up
        and min(recent_k) <= oversold
        and close < middle
        and close < (lower + middle) / 2
        and close > open_  # vela alcista: va en la dirección del LONG
    ):
        stop_loss = min(lows[-sl_lookback:])
        # El objetivo 0.5:1 debe quedar antes de llegar a la banda media.
        if take_profit_level("LONG", close, stop_loss, band_ratio) < middle:
            return Signal("LONG", k_now, d_now, close, lower, middle, upper, stop_loss)

    if (
        crossed_down
        and max(recent_k) >= overbought
        and close > middle
        and close > (middle + upper) / 2
        and close < open_  # vela bajista: va en la dirección del SHORT
    ):
        stop_loss = max(highs[-sl_lookback:])
        if take_profit_level("SHORT", close, stop_loss, band_ratio) > middle:
            return Signal("SHORT", k_now, d_now, close, lower, middle, upper, stop_loss)

    return None


def take_profit_level(side: str, entry: float, stop_loss: float, ratio: float) -> float:
    """
    Precio del objetivo para un ratio beneficio:riesgo dado.

    El riesgo es la distancia entre la entrada y el stop. LONG: el objetivo
    queda por encima de la entrada; SHORT: por debajo. Ejemplo: LONG con
    entrada 100 y stop 98 (riesgo 2) → ratio 0.5 da 101, ratio 1 da 102.
    """
    risk = abs(entry - stop_loss)
    return entry + risk * ratio if side == "LONG" else entry - risk * ratio
