"""
Backtest de la estrategia de cruce del Keltner Channel (`strategy_keltner.py`).

Módulo puro, igual que `backtest.py`: recibe listas de precios y timestamps
y no conoce Bitunix ni Telegram. Reutiliza de `backtest.py` la simulación de
cada escenario de TP (`_evaluate_scenario`), los resultados (`ScenarioResult`,
`OUTCOME_*`) y las estadísticas por escenario, de modo que las reglas de
ejecución son exactamente las mismas que en la otra estrategia:

- Entrada al cierre de la vela del cruce; stop = mínimo (LONG) o máximo
  (SHORT) de esa vela.
- Desde la vela siguiente se recorre el histórico mirando máximos y mínimos.
  Si en una misma vela se toca stop y objetivo, cuenta como stop (conservador).
- El límite de velas (`horizon`) es opcional, con la misma semántica que en
  `backtest.py`. Sin límite, la operación solo termina al tocar objetivo o
  stop, o queda abierta (PENDIENTE) si el histórico se acaba antes.
- Sin comisiones ni slippage.

Escenarios de objetivo: 1:1, 1.5:1 y 2:1 (`KELTNER_TP_RATIOS`), evaluados de
forma independiente sobre las mismas señales.

Ratio máximo alcanzado
----------------------
Además de los escenarios fijos, cada señal registra `max_ratio`: hasta qué
múltiplo del riesgo (R = distancia entrada → stop) llegó el precio a favor
ANTES de tocar el stop. Se mide con el mejor máximo (LONG) o mínimo (SHORT)
de las velas posteriores a la entrada; la vela que toca el stop NO cuenta
(no se sabe si el extremo fue antes o después del stop). Es consistente con
los escenarios: un escenario de ratio r es TP exactamente cuando
`max_ratio >= r`.

Con eso, `ratio_curve()` calcula para una rejilla de ratios (0.25, 0.5, …)
qué porcentaje de operaciones habría llegado y cuál habría sido el R medio
con un TP fijo en cada uno, para ver cuál es el más rentable.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from backtest import (
    OUTCOME_HORIZON,
    OUTCOME_PENDING,
    OUTCOME_SL,
    RESOLVED_OUTCOMES,
    ScenarioResult,
    _evaluate_scenario,
    _scenario_stats,
    best_scenario,
    ratio_label,
)
from strategy_keltner import detect_signal

# Ratios beneficio:riesgo que se comparan en esta estrategia.
KELTNER_TP_RATIOS: tuple[float, ...] = (1.0, 1.5, 2.0)

# Rejilla de ratios para la curva de rentabilidad (hasta qué ratio se evalúa).
CURVE_STEP = 0.25
CURVE_MAX = 5.0

__all__ = [
    "KELTNER_TP_RATIOS",
    "KeltnerBacktestSignal",
    "run_backtest",
    "summarize",
    "ratio_curve",
    "best_curve_ratio",
    "best_scenario",
    "ratio_label",
]


@dataclass
class KeltnerBacktestSignal:
    time: int  # timestamp de la vela del cruce (mismo formato que devuelve Bitunix)
    side: str  # "LONG" o "SHORT"
    open: float
    close: float  # precio de entrada
    lower: float
    middle: float
    upper: float
    crossed_bands: tuple[str, ...]  # bandas que cruzó la vela en la dirección de la operación
    stop_loss: float  # mínimo (LONG) / máximo (SHORT) de la vela del cruce
    risk_pct: float  # distancia entrada → stop, en %
    scenarios: dict[float, ScenarioResult]  # resultado por ratio (claves = tp_ratios)
    # --- Ratio máximo alcanzado (None si el riesgo es 0 y no se puede medir) ---
    max_ratio: float | None  # múltiplo de R máximo a favor antes de tocar el stop (>= 0)
    max_ratio_price: float | None  # precio extremo que marcó ese máximo
    candles_to_max: int | None  # velas desde la entrada hasta marcar ese máximo (0 = no se movió a favor)
    trade_end: str  # cómo terminó el recorrido medido: OUTCOME_SL / OUTCOME_HORIZON / OUTCOME_PENDING / N/D
    horizon_r: float | None  # solo si trade_end == HORIZONTE: R de la salida al cierre de la vela horizonte


def _measure_max_ratio(
    side: str,
    entry: float,
    stop: float,
    closes: list[float],
    highs: list[float],
    lows: list[float],
    signal_index: int,
    horizon: int | None,
) -> tuple[float, float, int, str, float | None]:
    """
    Recorre las velas posteriores a la entrada hasta tocar el stop (o hasta
    el final / horizonte) y devuelve (max_ratio, precio_extremo,
    velas_hasta_el_máximo, trade_end, horizon_r). Mismas reglas de fin que
    `backtest._simulate_trade`.
    """
    risk = abs(entry - stop)
    last_index = len(closes) - 1
    end = last_index if horizon is None else min(signal_index + horizon, last_index)

    best_price = entry
    best_index = signal_index
    trade_end = None

    for j in range(signal_index + 1, end + 1):
        if side == "LONG":
            if lows[j] <= stop:
                trade_end = OUTCOME_SL
                break
            if highs[j] > best_price:
                best_price, best_index = highs[j], j
        else:
            if highs[j] >= stop:
                trade_end = OUTCOME_SL
                break
            if lows[j] < best_price:
                best_price, best_index = lows[j], j

    horizon_r = None
    if trade_end is None:
        if horizon is not None and signal_index + horizon <= last_index:
            trade_end = OUTCOME_HORIZON
            move = closes[signal_index + horizon] - entry
            horizon_r = (move if side == "LONG" else -move) / risk
        else:
            trade_end = OUTCOME_PENDING

    favorable = best_price - entry if side == "LONG" else entry - best_price
    return favorable / risk, best_price, best_index - signal_index, trade_end, horizon_r


def run_backtest(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    opens: list[float],
    times: list[int],
    horizon: int | None = None,
    tp_ratios: tuple[float, ...] = KELTNER_TP_RATIOS,
    ema_period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
    only_outer_bands: bool = False,
) -> list[KeltnerBacktestSignal]:
    """
    Recorre el histórico vela a vela aplicando `detect_signal` solo con las
    velas conocidas hasta ese punto (sin mirar al futuro). Por cada señal
    simula cada ratio de `tp_ratios` y mide el ratio máximo alcanzado.

    Igual que en `backtest.py`, cada paso recalcula los indicadores desde
    cero, así que el costo crece con el cuadrado del número de velas
    (aceptable para el tope de 1500 velas del bot).
    """
    results: list[KeltnerBacktestSignal] = []

    for i in range(len(closes)):
        signal = detect_signal(
            closes[: i + 1],
            highs[: i + 1],
            lows[: i + 1],
            opens[: i + 1],
            ema_period=ema_period,
            atr_period=atr_period,
            multiplier=multiplier,
            only_outer_bands=only_outer_bands,
        )
        if signal is None:
            continue

        risk_pct = abs(signal.close - signal.stop_loss) / signal.close * 100

        scenarios = {
            ratio: _evaluate_scenario(
                signal.side, signal.close, signal.stop_loss, risk_pct, ratio,
                closes, highs, lows, i, horizon,
            )
            for ratio in tp_ratios
        }

        if risk_pct > 0:
            max_ratio, max_price, bars_to_max, trade_end, horizon_r = _measure_max_ratio(
                signal.side, signal.close, signal.stop_loss, closes, highs, lows, i, horizon
            )
        else:  # riesgo 0: no se puede medir en múltiplos de R (igual que los escenarios, N/D)
            max_ratio = max_price = bars_to_max = horizon_r = None
            trade_end = "N/D"

        results.append(
            KeltnerBacktestSignal(
                time=times[i],
                side=signal.side,
                open=signal.open,
                close=signal.close,
                lower=signal.lower,
                middle=signal.middle,
                upper=signal.upper,
                crossed_bands=signal.crossed_bands,
                stop_loss=signal.stop_loss,
                risk_pct=risk_pct,
                scenarios=scenarios,
                max_ratio=max_ratio,
                max_ratio_price=max_price,
                candles_to_max=bars_to_max,
                trade_end=trade_end,
                horizon_r=horizon_r,
            )
        )

    return results


def _r_with_fixed_tp(sig: KeltnerBacktestSignal, ratio: float) -> float:
    """R que habría dado la operación con un TP fijo en `ratio` (solo para operaciones resueltas)."""
    if sig.max_ratio >= ratio:
        return ratio
    if sig.trade_end == OUTCOME_HORIZON:
        return sig.horizon_r
    return -1.0  # terminó en stop sin llegar al ratio


def ratio_curve(
    results: list[KeltnerBacktestSignal],
    step: float = CURVE_STEP,
    max_ratio: float = CURVE_MAX,
) -> list[dict]:
    """
    Para cada ratio de la rejilla (step, 2*step, … max_ratio) devuelve:
      {"ratio", "operaciones", "alcanzan", "pct_alcanzan", "r_total", "r_medio"}.

    Una operación cuenta para un ratio si ya está resuelta para ese ratio:
    terminó en stop o en el horizonte, o sigue abierta pero YA llegó a ese
    ratio (es un TP seguro). Las abiertas que aún no llegaron se excluyen,
    porque su máximo todavía puede crecer. Es el mismo criterio de
    `backtest._scenario_stats`, así que en 1:1, 1.5:1 y 2:1 la curva
    coincide con los escenarios.

    `r_medio` es el R medio por operación con un TP fijo en ese ratio: la
    forma de ver qué ratio habría sido el más rentable.
    """
    measured = [s for s in results if s.max_ratio is not None]
    curve = []
    k = 1
    while k * step <= max_ratio + 1e-9:
        ratio = round(k * step, 6)
        counted = [
            s for s in measured
            if s.trade_end in (OUTCOME_SL, OUTCOME_HORIZON) or s.max_ratio >= ratio
        ]
        if counted:
            reached = sum(1 for s in counted if s.max_ratio >= ratio)
            r_total = sum(_r_with_fixed_tp(s, ratio) for s in counted)
            curve.append(
                {
                    "ratio": ratio,
                    "operaciones": len(counted),
                    "alcanzan": reached,
                    "pct_alcanzan": reached / len(counted) * 100,
                    "r_total": r_total,
                    "r_medio": r_total / len(counted),
                }
            )
        k += 1
    return curve


def best_curve_ratio(curve: list[dict]) -> dict | None:
    """Fila de la curva con mayor R medio (en empate, el ratio más bajo). None si la curva está vacía."""
    if not curve:
        return None
    return max(curve, key=lambda row: (row["r_medio"], -row["ratio"]))


def summarize(
    results: list[KeltnerBacktestSignal],
    tp_ratios: tuple[float, ...] = KELTNER_TP_RATIOS,
) -> dict:
    """
    Resumen separado en Total / LONG / SHORT. Cada grupo trae:
      - total de señales y riesgo medio hasta el stop (%),
      - "escenarios"[ratio]: mismas métricas que `backtest.summarize`
        (TP/SL, win rate, R total y medio, cambio %),
      - "max_ratio": estadísticas del ratio máximo alcanzado (medio, mediana,
        máximo) sobre todas las operaciones medidas, y cuántas de ellas siguen
        abiertas (su máximo es provisional). La mediana es más fiable que la
        media: un stop muy pequeño puede dar máximos de decenas de R,
      - "curva": `ratio_curve` del grupo y "mejor_ratio": su mejor fila.
    """

    def _group(signals: list[KeltnerBacktestSignal]) -> dict:
        avg_risk = sum(s.risk_pct for s in signals) / len(signals) if signals else None
        measured = [s for s in signals if s.max_ratio is not None]
        values = [s.max_ratio for s in measured]
        curve = ratio_curve(signals)
        return {
            "total": len(signals),
            "riesgo_medio_pct": avg_risk,
            "escenarios": {ratio: _scenario_stats(signals, ratio) for ratio in tp_ratios},
            "max_ratio": {
                "medidas": len(measured),
                # abiertas: su máximo es un mínimo provisional (puede seguir creciendo)
                "abiertas": sum(1 for s in measured if s.trade_end == OUTCOME_PENDING),
                "medio": sum(values) / len(values) if values else None,
                "mediana": median(values) if values else None,
                "maximo": max(values) if values else None,
            },
            "curva": curve,
            "mejor_ratio": best_curve_ratio(curve),
        }

    return {
        "total": _group(results),
        "long": _group([s for s in results if s.side == "LONG"]),
        "short": _group([s for s in results if s.side == "SHORT"]),
    }
    