"""
Backtest simple de la estrategia de `strategy.py` sobre un histórico de velas.

Módulo puro: recibe listas de precios y de timestamps (ya extraídas de las
velas) y no conoce Bitunix ni Telegram. Reutiliza `detect_signal` para no
duplicar la lógica de entrada — así que cualquier ajuste que le hagas a la
estrategia en `strategy.py` se refleja automáticamente aquí.

Importante — todavía no hay reglas de salida definidas, así que esto NO
simula una operación completa (entrada + salida con stop/take-profit).
Lo que hace es más simple: por cada señal, mira cuánto se movió el precio
`horizon` velas después y lo ajusta a la dirección de la señal (positivo =
el precio se movió a favor). Es una forma rápida de ver si la dirección
de las señales suele acertar, no un backtest de rentabilidad real. Cuando
definamos las salidas, esto se puede reemplazar por una simulación con
entrada y salida de verdad.
"""

from __future__ import annotations

from dataclasses import dataclass

from strategy import detect_signal


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
    horizon_close: float | None  # precio `horizon` velas después (None si aún no hay esas velas)
    pct_change: float | None  # % de cambio ajustado a la dirección (positivo = a favor de la señal)


def run_backtest(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    times: list[int],
    horizon: int = 5,
    oversold: float = 25.0,
    overbought: float = 75.0,
    lookback: int = 10,
) -> list[BacktestSignal]:
    """
    Recorre el histórico vela a vela, aplicando `detect_signal` en cada paso
    usando solo las velas conocidas hasta ese punto (igual que lo vería el
    bot en vivo, sin "mirar al futuro"). Devuelve la lista de señales
    encontradas, cada una con el precio `horizon` velas después (si ya
    existe en el histórico dado) y el % de cambio correspondiente.

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

        horizon_index = i + horizon
        if horizon_index < len(closes):
            horizon_close = closes[horizon_index]
            raw_change = (horizon_close - signal.close) / signal.close * 100
            pct_change = raw_change if signal.side == "LONG" else -raw_change
        else:
            horizon_close = None
            pct_change = None

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
                horizon_close=horizon_close,
                pct_change=pct_change,
            )
        )

    return results


def summarize(results: list[BacktestSignal]) -> dict:
    """
    Resumen rápido del backtest: total de señales, cuántas ya tienen
    resultado (pasaron `horizon` velas desde que se dieron), tasa de
    acierto (pct_change > 0) y cambio medio — todo separado también por
    LONG y por SHORT para poder comparar.
    """

    def _stats(signals: list[BacktestSignal]) -> dict:
        with_result = [s for s in signals if s.pct_change is not None]
        wins = [s for s in with_result if s.pct_change > 0]
        avg = sum(s.pct_change for s in with_result) / len(with_result) if with_result else None
        return {
            "total": len(signals),
            "con_resultado": len(with_result),
            "aciertos": len(wins),
            "win_rate": (len(wins) / len(with_result) * 100) if with_result else None,
            "cambio_medio_pct": avg,
        }

    return {
        "total": _stats(results),
        "long": _stats([s for s in results if s.side == "LONG"]),
        "short": _stats([s for s in results if s.side == "SHORT"]),
    }
