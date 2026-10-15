"""Utilidades compartidas para backtests, independientes de la estrategia.

Formato de período: fechas UTC inclusivas (YYYY-MM-DD). Se añaden velas de
calentamiento antes del inicio y velas posteriores al final para que los
indicadores y el seguimiento de las últimas operaciones no queden truncados.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import re
from collections import deque

INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
    "3d": 259_200_000, "1w": 604_800_000,
}

@dataclass(frozen=True)
class BacktestRequest:
    symbol: str
    interval: str
    candle_count: int | None = 500
    horizon: int | None = None
    start_ms: int | None = None  # inicio solicitado, inclusivo
    end_ms: int | None = None    # fin solicitado, inclusivo

    @property
    def is_date_range(self) -> bool:
        return self.start_ms is not None and self.end_ms is not None

def parse_utc_date(value: str, *, end: bool = False) -> int:
    """Convierte YYYY-MM-DD a milisegundos UTC; end=True incluye todo ese día."""
    parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end:
        parsed += timedelta(days=1)
        parsed -= timedelta(milliseconds=1)
    return int(parsed.timestamp() * 1000)

def parse_backtest_request(args: list[str], *, normalize_symbol, default_interval: str, max_candles: int) -> BacktestRequest:
    """Acepta formato antiguo por velas o nuevo por fechas, para cualquier estrategia.

    Antiguo: SIMBOLO [INTERVALO] [N_VELAS] [HORIZONTE]
    Nuevo:   SIMBOLO [INTERVALO] YYYY-MM-DD YYYY-MM-DD [HORIZONTE]
    """
    if not args:
        raise ValueError("Faltan argumentos: indica al menos el símbolo.")
    symbol = normalize_symbol(args[0])
    interval = args[1] if len(args) > 1 else default_interval
    if len(args) >= 4 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", args[2]) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", args[3]):
        try:
            start_ms = parse_utc_date(args[2])
            end_ms = parse_utc_date(args[3], end=True)
        except ValueError as exc:
            raise ValueError("Las fechas deben ser válidas y usar YYYY-MM-DD.") from exc
        if start_ms > end_ms:
            raise ValueError("La fecha inicial debe ser anterior o igual a la fecha final.")
        horizon = int(args[4]) if len(args) > 4 else None
        if horizon is not None and horizon < 1:
            raise ValueError("El horizonte debe ser un entero positivo.")
        return BacktestRequest(symbol, interval, None, horizon, start_ms, end_ms)
    try:
        count = int(args[2]) if len(args) > 2 else 500
        horizon = int(args[3]) if len(args) > 3 else None
    except ValueError as exc:
        raise ValueError("N_VELAS/HORIZONTE deben ser enteros; para fechas usa YYYY-MM-DD YYYY-MM-DD.") from exc
    count = max(50, min(count, max_candles))
    if horizon is not None:
        horizon = max(1, horizon)
    return BacktestRequest(symbol, interval, count, horizon)

def load_backtest_klines(client, request: BacktestRequest, *, max_range_candles: int = 10000, warmup_candles: int = 100, followup_candles: int = 200) -> tuple[list[dict], int | None, int | None]:
    """Carga velas y devuelve (velas, inicio_señales, fin_señales).

    El backtest puede calcular indicadores con velas previas y evaluar las
    operaciones abiertas al final con velas posteriores, pero solo contabiliza
    señales cuya vela de entrada esté dentro del período solicitado.
    """
    if not request.is_date_range:
        return client.get_kline_history(request.symbol, interval=request.interval, total=request.candle_count or 500), None, None
    interval_ms = INTERVAL_MS.get(request.interval)
    if interval_ms is None:
        raise ValueError(f"No se conoce la duración del intervalo {request.interval}; añade su duración a backtest_common.py.")
    bars_in_period = (request.end_ms - request.start_ms) // interval_ms + 1
    required = bars_in_period + warmup_candles + followup_candles
    if required > max_range_candles:
        raise ValueError(
            f"El período requiere aproximadamente {required} velas incluyendo calentamiento y seguimiento; "
            f"el máximo por backtest es {max_range_candles}. Reduce el rango de fechas."
        )
    total = required
    fetch_start = request.start_ms - warmup_candles * interval_ms
    fetch_end = request.end_ms + followup_candles * interval_ms
    candles = client.get_kline_history(
        request.symbol, interval=request.interval, total=total,
        end_time=fetch_end, start_time=fetch_start,
    )
    return candles, request.start_ms, request.end_ms

def filter_signals_to_period(results, start_ms: int | None, end_ms: int | None):
    if start_ms is None or end_ms is None:
        return results
    return [result for result in results if start_ms <= int(result.time) <= end_ms]


def filter_signals_by_density(results, candle_times: list[int], window_candles: int | None = None,
                              max_signals: int | None = None):
    """Filtra entradas agrupadas, pero cada señal detectada cuenta para las siguientes.

    Para una señal en la vela i, se cuentan todas las señales detectadas en las
    X velas anteriores (i-X ... i-1). Si el conteo es >= max_signals, se rechaza
    la entrada actual. La señal actual se registra siempre, aunque se rechace,
    por lo que también limita señales futuras. Devuelve (aceptadas, rechazadas).
    """
    if window_candles is None or max_signals is None:
        return list(results), []
    if window_candles < 1 or max_signals < 1:
        raise ValueError("El filtro de agrupación requiere X velas y límite, ambos enteros positivos.")

    time_to_index = {int(t): i for i, t in enumerate(candle_times)}
    ordered = sorted(results, key=lambda item: int(item.time))
    recent_indices = deque()
    accepted, rejected = [], []
    for result in ordered:
        idx = time_to_index.get(int(result.time))
        if idx is None:
            # Si no coincide exactamente, no se puede calcular con seguridad.
            accepted.append(result)
            continue
        oldest_allowed = idx - window_candles
        while recent_indices and recent_indices[0] < oldest_allowed:
            recent_indices.popleft()
        if len(recent_indices) >= max_signals:
            rejected.append(result)
        else:
            accepted.append(result)
        # Toda señal detectada cuenta, incluso si fue rechazada como operación.
        recent_indices.append(idx)
    return accepted, rejected


def filter_signals_by_opposite_band(results, min_ratio: float | None = None):
    """Rechaza señales sin espacio suficiente hasta la banda contraria.

    LONG: upper - entrada debe ser estrictamente mayor que min_ratio * riesgo.
    SHORT: entrada - lower debe ser estrictamente mayor que min_ratio * riesgo.
    Devuelve (aceptadas, rechazadas). Solo usa bandas conocidas al entrar.
    """
    if min_ratio is None:
        return list(results), []
    if min_ratio <= 0:
        raise ValueError("El ratio del filtro de banda debe ser mayor que cero.")

    accepted, rejected = [], []
    for signal in results:
        entry = float(signal.close)
        risk = abs(entry - float(signal.stop_loss))
        lower = float(signal.lower)
        upper = float(signal.upper)
        if risk <= 0:
            rejected.append(signal)
            continue
        if signal.side == "LONG":
            room = upper - entry
        else:
            room = entry - lower
        if room > min_ratio * risk:
            accepted.append(signal)
        else:
            rejected.append(signal)
    return accepted, rejected



def filter_signals_by_channel_width(results, min_width_pct: float | None = None):
    """Rechaza señales cuyo canal (Upper-Lower) sea demasiado estrecho.

    Anchura porcentual = (upper - lower) / precio de entrada * 100.
    Se acepta cuando la anchura es mayor o igual al mínimo configurado.
    Devuelve (aceptadas, rechazadas).
    """
    if min_width_pct is None:
        return list(results), []
    if min_width_pct <= 0:
        raise ValueError("El porcentaje mínimo de anchura del canal debe ser mayor que cero.")

    accepted, rejected = [], []
    for signal in results:
        entry = float(signal.close)
        lower = float(signal.lower)
        upper = float(signal.upper)
        if entry <= 0 or upper < lower:
            rejected.append(signal)
            continue
        width_pct = (upper - lower) / entry * 100.0
        if width_pct >= min_width_pct:
            accepted.append(signal)
        else:
            rejected.append(signal)
    return accepted, rejected
