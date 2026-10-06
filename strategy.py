"""
Estrategia de entrada (se usa en velas de 4h y de 1h; estocástico + Bandas de Bollinger).

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

`evaluate_conditions()` devuelve además el estado de cada condición (cumple
o no, con sus valores) para los resúmenes del bot; `detect_signal()` se
apoya en ella.

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


@dataclass
class Condition:
    label: str  # nombre corto de la condición, p. ej. "Vela alcista"
    ok: bool  # si se cumple ahora mismo
    detail: str  # valores concretos que explican el resultado


@dataclass
class StrategyStatus:
    """Estado de la estrategia en la última vela: valores y condición por condición."""

    close: float
    k: float
    d: float
    lower: float
    middle: float
    upper: float
    long: list[Condition]
    short: list[Condition]
    signal: Signal | None  # no es None solo si TODAS las condiciones de un lado se cumplen

    def met(self, side: str) -> int:
        return sum(c.ok for c in (self.long if side == "LONG" else self.short))

    def leading_side(self) -> str:
        """Lado que más condiciones cumple; en empate, el que indica el %K (<50 → LONG)."""
        long_met, short_met = self.met("LONG"), self.met("SHORT")
        if long_met != short_met:
            return "LONG" if long_met > short_met else "SHORT"
        return "LONG" if self.k < 50 else "SHORT"


def evaluate_conditions(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    opens: list[float],
    oversold: float = 25.0,
    overbought: float = 75.0,
    lookback: int = 10,
    sl_lookback: int = 10,
    band_ratio: float = 0.5,
) -> StrategyStatus | None:
    """
    Evalúa la última vela cerrada y devuelve el estado de CADA condición de
    LONG y de SHORT (cumple / no cumple, con los valores concretos), más la
    `Signal` si un lado las cumple todas. Devuelve None si aún no hay velas
    suficientes para calcular los indicadores.

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

    if len(lows) < sl_lookback or len(highs) < sl_lookback or not opens:
        return None  # no hay suficientes velas todavía para ubicar el mínimo/máximo local

    open_ = opens[-1]
    crossed_up = k_prev <= d_prev and k_now > d_now
    crossed_down = k_prev >= d_prev and k_now < d_now

    # Ventana de %K justo antes del cruce (sin incluir el valor del cruce).
    recent_k = k_series[-(lookback + 1) : -1]

    low_limit = (lower + middle) / 2  # punto medio entre banda inferior y media
    high_limit = (middle + upper) / 2  # punto medio entre banda media y superior
    stop_long = min(lows[-sl_lookback:])
    stop_short = max(highs[-sl_lookback:])
    tp_long = take_profit_level("LONG", close, stop_long, band_ratio)
    tp_short = take_profit_level("SHORT", close, stop_short, band_ratio)

    cross_detail = f"%K {k_prev:.1f}→{k_now:.1f} · %D {d_prev:.1f}→{d_now:.1f}"

    long_conditions = [
        Condition("Cruce de %K sobre %D", crossed_up, cross_detail),
        Condition(
            f"Sobreventa reciente (%K ≤ {oversold:g})",
            min(recent_k) <= oversold,
            f"mín. %K últimas {lookback} velas: {min(recent_k):.1f}",
        ),
        Condition(
            "Cierre cerca de la banda inferior",
            close < middle and close < low_limit,
            f"cierre {close:.6g} (debe ser < {low_limit:.6g})",
        ),
        Condition("Vela alcista", close > open_, f"apertura {open_:.6g} → cierre {close:.6g}"),
        Condition(
            f"TP {band_ratio:g}:1 antes de la banda media",
            tp_long < middle,
            f"TP {tp_long:.6g} (debe ser < media {middle:.6g})",
        ),
    ]
    short_conditions = [
        Condition("Cruce de %K bajo %D", crossed_down, cross_detail),
        Condition(
            f"Sobrecompra reciente (%K ≥ {overbought:g})",
            max(recent_k) >= overbought,
            f"máx. %K últimas {lookback} velas: {max(recent_k):.1f}",
        ),
        Condition(
            "Cierre cerca de la banda superior",
            close > middle and close > high_limit,
            f"cierre {close:.6g} (debe ser > {high_limit:.6g})",
        ),
        Condition("Vela bajista", close < open_, f"apertura {open_:.6g} → cierre {close:.6g}"),
        Condition(
            f"TP {band_ratio:g}:1 antes de la banda media",
            tp_short > middle,
            f"TP {tp_short:.6g} (debe ser > media {middle:.6g})",
        ),
    ]

    signal = None
    if all(c.ok for c in long_conditions):
        signal = Signal("LONG", k_now, d_now, close, lower, middle, upper, stop_long)
    elif all(c.ok for c in short_conditions):
        signal = Signal("SHORT", k_now, d_now, close, lower, middle, upper, stop_short)

    return StrategyStatus(close, k_now, d_now, lower, middle, upper, long_conditions, short_conditions, signal)


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
    suficientes para calcular los indicadores). Usa `evaluate_conditions`,
    así que la señal y el resumen de estado siempre coinciden.
    """
    status = evaluate_conditions(
        closes, highs, lows, opens, oversold, overbought, lookback, sl_lookback, band_ratio
    )
    return status.signal if status is not None else None


def take_profit_level(side: str, entry: float, stop_loss: float, ratio: float) -> float:
    """
    Precio del objetivo para un ratio beneficio:riesgo dado.

    El riesgo es la distancia entre la entrada y el stop. LONG: el objetivo
    queda por encima de la entrada; SHORT: por debajo. Ejemplo: LONG con
    entrada 100 y stop 98 (riesgo 2) → ratio 0.5 da 101, ratio 1 da 102.
    """
    risk = abs(entry - stop_loss)
    return entry + risk * ratio if side == "LONG" else entry - risk * ratio
  
