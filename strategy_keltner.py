"""
Estrategia de cruce de bandas del Keltner Channel.

Módulo puro (igual que `strategy.py`): no conoce Bitunix ni Telegram, solo
recibe listas de precios ordenadas de la vela más antigua a la más reciente
y decide si al cierre de la última vela hay señal de entrada.

Reglas
------

  LONG
    La última vela cruza de abajo hacia arriba la banda INFERIOR o la
    MEDIA: abre por debajo (o justo en) la banda y cierra por encima.
    Cruzar de abajo hacia arriba la banda SUPERIOR no genera operación.

  SHORT
    Lo contrario: la vela cruza de arriba hacia abajo la banda SUPERIOR o
    la MEDIA (abre por encima o en la banda y cierra por debajo). Cruzar
    hacia abajo la banda inferior no genera operación.

  Entrada: al cierre de la vela del cruce.

  Stop loss: el mínimo de esa misma vela (LONG) o su máximo (SHORT).

  Take profit: no forma parte de la estrategia; el backtest lo evalúa a
  niveles fijos por ratio RR con `take_profit_level` (se reutiliza la de
  `strategy.py`, para que backtest y órdenes reales calculen el mismo nivel).

Definición de "cruce"
---------------------

Se usa el cuerpo de la propia vela, con el valor de la banda en esa vela:
  cruce al alza de la banda B:  apertura <= B  y  cierre > B
  cruce a la baja de la banda B: apertura >= B  y  cierre < B
Las mechas no cuentan: una vela que solo toca la banda con la mecha y
cierra del mismo lado no es un cruce.

Si una misma vela cruza varias bandas a la vez (p. ej. la inferior y la
media), es una única señal; `Signal.crossed_bands` dice cuáles cruzó.
"""

from __future__ import annotations

from dataclasses import dataclass

from indicators import keltner_series
from strategy import Condition  # misma estructura de condición que usa el resumen del bot

# Bandas cuyo cruce abre operación en cada dirección.
LONG_BANDS: tuple[str, ...] = ("lower", "middle")
SHORT_BANDS: tuple[str, ...] = ("upper", "middle")


@dataclass
class KeltnerSignal:
    side: str  # "LONG" o "SHORT"
    open: float
    close: float  # precio de entrada
    lower: float  # bandas de Keltner en la vela de la señal
    middle: float
    upper: float
    stop_loss: float  # mínimo (LONG) o máximo (SHORT) de la vela del cruce
    crossed_bands: tuple[str, ...]  # bandas que cruzó esa vela en la dirección de la operación


@dataclass
class KeltnerStatus:
    """Estado de la estrategia en la última vela, para los resúmenes del bot."""

    close: float
    lower: float
    middle: float
    upper: float
    long: list[Condition]
    short: list[Condition]
    signal: KeltnerSignal | None

    def met(self, side: str) -> int:
        return sum(c.ok for c in (self.long if side == "LONG" else self.short))


def _crossed_up(open_: float, close: float, band: float) -> bool:
    return open_ <= band and close > band


def _crossed_down(open_: float, close: float, band: float) -> bool:
    return open_ >= band and close < band


def evaluate_conditions(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    opens: list[float],
    ema_period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
) -> KeltnerStatus | None:
    """
    Evalúa la última vela cerrada. Devuelve el estado de cada condición de
    LONG y de SHORT y la `KeltnerSignal` si hay cruce válido, o None si aún
    no hay velas suficientes para calcular el Keltner Channel.
    """
    try:
        lower_s, middle_s, upper_s = keltner_series(
            closes, highs, lows, ema_period, atr_period, multiplier
        )
    except ValueError:
        return None
    if not opens:
        return None

    lower, middle, upper = lower_s[-1], middle_s[-1], upper_s[-1]
    open_, close = opens[-1], closes[-1]
    bands = {"lower": lower, "middle": middle, "upper": upper}

    up_crossed = tuple(name for name, v in bands.items() if _crossed_up(open_, close, v))
    down_crossed = tuple(name for name, v in bands.items() if _crossed_down(open_, close, v))

    long_ok = any(b in up_crossed for b in LONG_BANDS)
    short_ok = any(b in down_crossed for b in SHORT_BANDS)

    candle = f"apertura {open_:.6g} → cierre {close:.6g}"
    long_conditions = [
        Condition(
            "Cruce al alza de banda inferior o media",
            long_ok,
            f"{candle} · inf {lower:.6g} · media {middle:.6g}",
        ),
    ]
    short_conditions = [
        Condition(
            "Cruce a la baja de banda superior o media",
            short_ok,
            f"{candle} · sup {upper:.6g} · media {middle:.6g}",
        ),
    ]

    signal = None
    if long_ok:
        signal = KeltnerSignal("LONG", open_, close, lower, middle, upper, lows[-1], up_crossed)
    elif short_ok:  # no puede coincidir con LONG: uno exige cierre > apertura y el otro lo contrario
        signal = KeltnerSignal("SHORT", open_, close, lower, middle, upper, highs[-1], down_crossed)

    return KeltnerStatus(close, lower, middle, upper, long_conditions, short_conditions, signal)


def detect_signal(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    opens: list[float],
    ema_period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
) -> KeltnerSignal | None:
    """
    Devuelve la señal de la última vela cerrada (closes[-1] / opens[-1] /
    highs[-1] / lows[-1]) o None si no hay cruce válido (o faltan velas).
    Se apoya en `evaluate_conditions`, así que señal y resumen coinciden.
    """
    status = evaluate_conditions(closes, highs, lows, opens, ema_period, atr_period, multiplier)
    return status.signal if status is not None else None
    