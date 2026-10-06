"""
Backtest de la estrategia de `strategy.py` sobre un histórico de velas, con
simulación de la operación: entrada, stop loss y objetivo por ratio.

Módulo puro: recibe listas de precios y de timestamps (ya extraídas de las
velas) y no conoce Bitunix ni Telegram. Reutiliza `detect_signal` (entrada y
stop) y `take_profit_level` (objetivo) de `strategy.py`, así que cualquier
ajuste que le hagas a la estrategia se refleja automáticamente aquí.

Cómo se simula cada operación
-----------------------------
- Entrada: al cierre de la vela de la señal.
- A partir de la vela siguiente se recorre el histórico vela a vela, mirando
  los máximos y mínimos de cada una (no solo el cierre):
    * Si el precio toca el stop, la operación se corta ahí y la pérdida es
      exactamente la distancia entrada → stop (-1R).
    * Si toca el objetivo, la operación se cierra en el precio del objetivo.
- Se evalúan DOS escenarios de objetivo, de forma independiente, sobre las
  mismas señales (ver `TP_RATIOS`):
    * 0.5:1 → objetivo a 0.5 veces la distancia entrada → stop.
    * 1:1   → objetivo a 1 vez esa distancia.
- Si pasan `horizon` velas sin tocar ni stop ni objetivo, la operación se
  cierra al cierre de esa vela (el resultado puede ser positivo o negativo).
  Si todavía no existen esas velas y no se ha tocado nada, queda "pendiente".

Limitación conocida (velas OHLC): si en una misma vela el precio toca el stop
Y el objetivo, no hay forma de saber cuál fue primero con datos de vela. Se
asume el caso conservador: se cuenta como stop.

No incluye comisiones ni slippage.
"""

from __future__ import annotations

from dataclasses import dataclass

from strategy import detect_signal, take_profit_level

# Ratios beneficio:riesgo que se comparan. 0.5 = objetivo a media distancia
# del stop, 1.0 = objetivo a la misma distancia que el stop.
TP_RATIOS: tuple[float, ...] = (0.5, 1.0)

# Cómo terminó una operación simulada.
OUTCOME_TP = "TP"  # llegó al objetivo antes que al stop
OUTCOME_SL = "SL"  # tocó el stop (primero, o en la misma vela que el objetivo)
OUTCOME_HORIZON = "HORIZONTE"  # no tocó ninguno; se cerró al cierre de la vela `horizon`
OUTCOME_PENDING = "PENDIENTE"  # no tocó ninguno y aún no existen las `horizon` velas
OUTCOME_NA = "N/D"  # no simulable (stop igual al precio de entrada → riesgo 0)

RESOLVED_OUTCOMES = (OUTCOME_TP, OUTCOME_SL, OUTCOME_HORIZON)


def ratio_label(ratio: float) -> str:
    """0.5 -> '0.5:1', 1.0 -> '1:1'."""
    return f"{ratio:g}:1"


@dataclass
class ScenarioResult:
    ratio: float
    target: float | None  # precio del objetivo (None si no se pudo calcular)
    outcome: str  # OUTCOME_*
    exit_price: float | None  # precio de salida (None si está pendiente o N/D)
    pct_change: float | None  # % ajustado a la dirección (negativo = pérdida)
    r_multiple: float | None  # pct_change / risk_pct (stop = -1R, objetivo = +ratio R)

    @property
    def reached_price(self) -> float | None:
        """Precio en que se cumple el ratio, o None si no llegó (o llegó antes el stop)."""
        return self.target if self.outcome == OUTCOME_TP else None


@dataclass
class BacktestSignal:
    time: int  # timestamp de la vela de la señal (mismo formato que devuelve Bitunix)
    side: str  # "LONG" o "SHORT"
    close: float
    k: float
    d: float
    lower: float
    middle: float
    upper: float
    stop_loss: float  # mínimo local (LONG) o máximo local (SHORT) usado como stop
    risk_pct: float  # distancia entre el precio de entrada y el stop loss, en %
    scenarios: dict[float, ScenarioResult]  # resultado por ratio (claves = TP_RATIOS)


def _simulate_trade(
    side: str,
    stop: float,
    target: float,
    closes: list[float],
    highs: list[float],
    lows: list[float],
    signal_index: int,
    horizon: int,
) -> tuple[str, float | None]:
    """
    Recorre las velas posteriores a la señal y devuelve (outcome, precio de
    salida). Ver el docstring del módulo para las reglas.
    """
    last_index = len(closes) - 1
    end = min(signal_index + horizon, last_index)

    for j in range(signal_index + 1, end + 1):
        if side == "LONG":
            hit_stop = lows[j] <= stop
            hit_target = highs[j] >= target
        else:
            hit_stop = highs[j] >= stop
            hit_target = lows[j] <= target

        if hit_stop:  # incluye el caso "stop y objetivo en la misma vela" (conservador)
            return OUTCOME_SL, stop
        if hit_target:
            return OUTCOME_TP, target

    if signal_index + horizon <= last_index:
        return OUTCOME_HORIZON, closes[signal_index + horizon]
    return OUTCOME_PENDING, None


def _evaluate_scenario(
    side: str,
    entry: float,
    stop: float,
    risk_pct: float,
    ratio: float,
    closes: list[float],
    highs: list[float],
    lows: list[float],
    signal_index: int,
    horizon: int,
) -> ScenarioResult:
    if risk_pct <= 0:
        return ScenarioResult(ratio, None, OUTCOME_NA, None, None, None)

    target = take_profit_level(side, entry, stop, ratio)
    outcome, exit_price = _simulate_trade(
        side, stop, target, closes, highs, lows, signal_index, horizon
    )

    if exit_price is None:
        return ScenarioResult(ratio, target, outcome, None, None, None)

    raw_change = (exit_price - entry) / entry * 100
    pct_change = raw_change if side == "LONG" else -raw_change
    return ScenarioResult(ratio, target, outcome, exit_price, pct_change, pct_change / risk_pct)


def run_backtest(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    times: list[int],
    horizon: int = 5,
    oversold: float = 25.0,
    overbought: float = 75.0,
    lookback: int = 10,
    tp_ratios: tuple[float, ...] = TP_RATIOS,
) -> list[BacktestSignal]:
    """
    Recorre el histórico vela a vela, aplicando `detect_signal` en cada paso
    usando solo las velas conocidas hasta ese punto (igual que lo vería el
    bot en vivo, sin "mirar al futuro"). Por cada señal simula la operación
    hacia delante (stop + cada ratio de `tp_ratios`) y devuelve la lista de
    señales con el resultado de cada escenario.

    `horizon` es el máximo de velas que se mantiene una operación sin tocar
    stop ni objetivo; pasado ese punto se cierra al cierre de esa vela.

    Nota de rendimiento: cada paso recalcula los indicadores desde cero
    sobre el tramo de velas hasta ese punto, así que el costo crece
    aproximadamente con el cuadrado del número de velas. Para históricos
    muy largos (miles de velas) puede tardar varios segundos; no hace
    falta optimizarlo mientras el histórico se mantenga en unos pocos
    cientos de velas.
    """
    results: list[BacktestSignal] = []

    for i in range(len(closes)):
        signal = detect_signal(
            closes[: i + 1],
            highs[: i + 1],
            lows[: i + 1],
            oversold=oversold,
            overbought=overbought,
            lookback=lookback,
        )
        if signal is None:
            continue

        risk_pct = abs(signal.close - signal.stop_loss) / signal.close * 100

        scenarios = {
            ratio: _evaluate_scenario(
                signal.side,
                signal.close,
                signal.stop_loss,
                risk_pct,
                ratio,
                closes,
                highs,
                lows,
                i,
                horizon,
            )
            for ratio in tp_ratios
        }

        results.append(
            BacktestSignal(
                time=times[i],
                side=signal.side,
                close=signal.close,
                k=signal.k,
                d=signal.d,
                lower=signal.lower,
                middle=signal.middle,
                upper=signal.upper,
                stop_loss=signal.stop_loss,
                risk_pct=risk_pct,
                scenarios=scenarios,
            )
        )

    return results


def _scenario_stats(signals: list[BacktestSignal], ratio: float) -> dict:
    scenario_results = [s.scenarios[ratio] for s in signals]
    resolved = [r for r in scenario_results if r.outcome in RESOLVED_OUTCOMES]
    wins = [r for r in resolved if r.pct_change is not None and r.pct_change > 0]

    n = len(resolved)
    r_total = sum(r.r_multiple for r in resolved)
    pct_total = sum(r.pct_change for r in resolved)

    return {
        "resueltas": n,
        "tp": sum(1 for r in resolved if r.outcome == OUTCOME_TP),
        "sl": sum(1 for r in resolved if r.outcome == OUTCOME_SL),
        "horizonte": sum(1 for r in resolved if r.outcome == OUTCOME_HORIZON),
        "pendientes": sum(1 for r in scenario_results if r.outcome == OUTCOME_PENDING),
        "win_rate": (len(wins) / n * 100) if n else None,
        "r_total": r_total if n else None,
        "r_medio": (r_total / n) if n else None,
        "cambio_total_pct": pct_total if n else None,
        "cambio_medio_pct": (pct_total / n) if n else None,
    }


def best_scenario(group_stats: dict) -> float | None:
    """
    Ratio con mejor resultado dentro de un grupo (Total/LONG/SHORT), medido
    por R medio por operación resuelta. Devuelve None si no hay operaciones
    resueltas en ningún escenario.
    """
    candidates = [
        (ratio, st["r_medio"])
        for ratio, st in group_stats["escenarios"].items()
        if st["r_medio"] is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda c: c[1])[0]


def summarize(results: list[BacktestSignal], tp_ratios: tuple[float, ...] = TP_RATIOS) -> dict:
    """
    Resumen del backtest, separado en Total / LONG / SHORT. Cada grupo trae
    el total de señales, el riesgo medio hasta el stop y, por cada ratio de
    `tp_ratios`, cuántas operaciones terminaron en objetivo / stop / horizonte,
    win rate, R total y R medio, y cambio total y medio en %.

    Estructura: stats[grupo]["escenarios"][ratio] -> dict con esas métricas.
    """

    def _group(signals: list[BacktestSignal]) -> dict:
        avg_risk = sum(s.risk_pct for s in signals) / len(signals) if signals else None
        return {
            "total": len(signals),
            "riesgo_medio_pct": avg_risk,
            "escenarios": {ratio: _scenario_stats(signals, ratio) for ratio in tp_ratios},
        }

    return {
        "total": _group(results),
        "long": _group([s for s in results if s.side == "LONG"]),
        "short": _group([s for s in results if s.side == "SHORT"]),
    }
