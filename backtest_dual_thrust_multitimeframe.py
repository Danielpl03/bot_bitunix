"""Agregación OHLC y ejecución intrabar para Dual Thrust con velas inferiores."""
from __future__ import annotations
from math import isfinite

from backtest_common import INTERVAL_MS
from backtest_dual_thrust import (
    DualThrustTrade, PENDING, REVERSAL, HORIZON,
    _adverse_fill, _close_trade,
)
from strategy_dual_thrust import calculate_levels


def validate_timeframes(target_interval: str, execution_interval: str) -> tuple[int, int, int]:
    """Valida temporalidades y devuelve (duración objetivo, ejecución, ratio)."""
    if target_interval not in INTERVAL_MS or execution_interval not in INTERVAL_MS:
        raise ValueError("La agregación multitemporal solo admite intervalos con duración fija.")
    target_ms, execution_ms = INTERVAL_MS[target_interval], INTERVAL_MS[execution_interval]
    if execution_ms >= target_ms:
        raise ValueError("El --subintervalo debe ser menor que la temporalidad del backtest.")
    if target_ms % execution_ms:
        raise ValueError(f"{execution_interval} no divide exactamente a {target_interval}.")
    return target_ms, execution_ms, target_ms // execution_ms


def aggregate_candles(execution_candles: list[dict], target_interval: str,
                      execution_interval: str, *, require_complete: bool = True) -> list[dict]:
    """Agrega velas inferiores a barras superiores alineadas a epoch UTC.

    Por defecto solo devuelve barras con todas las subvelas esperadas y con
    timestamps consecutivos, para no convertir huecos de datos en velas válidas.
    """
    target_ms, execution_ms, ratio = validate_timeframes(target_interval, execution_interval)
    groups: dict[int, list[dict]] = {}
    for candle in execution_candles:
        try:
            timestamp = int(candle["time"])
            if timestamp < 0:
                continue
            bucket = (timestamp // target_ms) * target_ms
            groups.setdefault(bucket, []).append(candle)
        except (KeyError, TypeError, ValueError):
            continue

    result = []
    for bucket in sorted(groups):
        group = sorted(groups[bucket], key=lambda c: int(c["time"]))
        # Deduplicar por timestamp sin cambiar el orden cronológico.
        unique = {int(c["time"]): c for c in group}
        group = [unique[t] for t in sorted(unique)]
        expected_times = [bucket + j * execution_ms for j in range(ratio)]
        actual_times = [int(c["time"]) for c in group]
        if require_complete and actual_times != expected_times:
            continue
        if not group:
            continue
        try:
            o = float(group[0]["open"])
            h = max(float(c["high"]) for c in group)
            l = min(float(c["low"]) for c in group)
            c = float(group[-1]["close"])
            if min(o, h, l, c) <= 0 or h < max(o, c) or l > min(o, c):
                continue
        except (KeyError, TypeError, ValueError):
            continue
        result.append({"time": bucket, "open": o, "high": h, "low": l, "close": c,
                       "source_candles": len(group), "expected_source_candles": ratio})
    return result


def run_backtest_multitimeframe(target_candles: list[dict], execution_candles: list[dict],
                                target_interval: str, execution_interval: str,
                                horizon=None, *, lookback=4, k1=0.6, k2=0.7,
                                fee_bps=0.0, slippage_bps=0.0):
    """Ejecuta reglas y niveles en temporalidad objetivo, usando subvelas para fills.

    Las bandas se calculan una vez por vela objetivo y solo con las N velas
    objetivo anteriores. Las subvelas se recorren cronológicamente para resolver
    qué extremo se tocó primero, salvo que ambos niveles se toquen en la misma
    subvela, caso que sigue siendo ambiguo con esos datos.
    """
    target_ms, execution_ms, ratio = validate_timeframes(target_interval, execution_interval)
    if lookback < 1 or k1 <= 0 or k2 <= 0:
        raise ValueError("N, K1 y K2 deben ser mayores que cero.")
    if horizon is not None and horizon < 1:
        raise ValueError("El horizonte debe ser un entero positivo.")
    if not isfinite(fee_bps) or fee_bps < 0 or not isfinite(slippage_bps) or slippage_bps < 0:
        raise ValueError("Comisiones y deslizamiento deben ser números finitos no negativos.")

    targets = sorted(target_candles, key=lambda c: int(c["time"]))
    execution = sorted(execution_candles, key=lambda c: int(c["time"]))
    # Solo se usan subvelas que pertenecen a barras objetivo completas y agregadas.
    valid_buckets = {int(c["time"]) for c in targets}
    intrabars: dict[int, list[dict]] = {t: [] for t in valid_buckets}
    for candle in execution:
        ts = int(candle["time"])
        bucket = (ts // target_ms) * target_ms
        if bucket in intrabars and bucket <= ts < bucket + target_ms:
            intrabars[bucket].append(candle)
    for bucket in intrabars:
        intrabars[bucket].sort(key=lambda c: int(c["time"]))

    opens = [float(c["open"]) for c in targets]
    highs = [float(c["high"]) for c in targets]
    lows = [float(c["low"]) for c in targets]
    closes = [float(c["close"]) for c in targets]
    times = [int(c["time"]) for c in targets]
    fee_rate, slip_rate = fee_bps / 10_000.0, slippage_bps / 10_000.0
    trades = []
    position = None

    for i in range(lookback, len(targets)):
        levels = calculate_levels(opens, highs, lows, closes, i, lookback=lookback, k1=k1, k2=k2)
        if levels is None:
            continue
        target_time = times[i]
        # El horizonte se mide en velas objetivo, no en subvelas.
        if position is not None and horizon is not None and i - position["target_index"] >= horizon:
            raw_exit = opens[i]
            exit_fill = _adverse_fill(raw_exit, position["side"], is_entry=False, slip=slip_rate)
            _close_trade(trades, position, target_time, exit_fill, HORIZON, i, fee_rate, slip_rate)
            position = None

        # Si no hay subvelas para esta barra, no inventamos una secuencia intrabar.
        for sub in intrabars.get(target_time, []):
            sub_open, sub_high, sub_low = float(sub["open"]), float(sub["high"]), float(sub["low"])
            hit_long = sub_high >= levels.buy_track
            hit_short = sub_low <= levels.sell_track
            if hit_long and hit_short:
                continue  # Ambigüedad solo en esta subvela, no en toda la vela objetivo.
            side = "LONG" if hit_long else ("SHORT" if hit_short else None)
            if side is None or (position is not None and position["side"] == side):
                continue

            level = levels.buy_track if side == "LONG" else levels.sell_track
            # Si la subvela abre ya más allá del stop, se ejecuta en su apertura (gap), no al nivel.
            if side == "LONG":
                raw_fill = max(level, sub_open)
            else:
                raw_fill = min(level, sub_open)
            fill_time = int(sub["time"])
            if position is not None:
                exit_fill = _adverse_fill(raw_fill, position["side"], is_entry=False, slip=slip_rate)
                _close_trade(trades, position, fill_time, exit_fill, REVERSAL, i, fee_rate, slip_rate)
                position = None
            entry_fill = _adverse_fill(raw_fill, side, is_entry=True, slip=slip_rate)
            position = {
                "time": fill_time, "entry_time": fill_time, "entry_index": i,
                "target_index": i, "side": side, "entry": entry_fill,
                "range_value": levels.range_value, "buy_track": levels.buy_track,
                "sell_track": levels.sell_track,
            }

    if position is not None:
        trades.append(DualThrustTrade(
            time=position["time"], entry_time=position["entry_time"], exit_time=None,
            side=position["side"], entry=position["entry"], exit_price=None,
            exit_reason=PENDING, range_value=position["range_value"],
            buy_track_entry=position["buy_track"], sell_track_entry=position["sell_track"],
            bars_held=None, pnl_pct=None, gross_pnl_pct=None, fees_pct=None, slippage_pct=None,
        ))
    return trades


def load_multitimeframe_candles(client, request, execution_interval: str, *, lookback=4,
                                warmup_candles=20, followup_candles=200,
                                max_target_candles=10000):
    """Descarga subvelas y devuelve (objetivo agregado, subvelas, inicio, fin)."""
    target_ms, execution_ms, ratio = validate_timeframes(request.interval, execution_interval)
    if request.is_date_range:
        bars_in_period = (request.end_ms - request.start_ms) // target_ms + 1
        required_target = bars_in_period + warmup_candles + followup_candles
        if required_target > max_target_candles:
            raise ValueError(
                f"El período requiere aproximadamente {required_target} velas objetivo con calentamiento/seguimiento; "
                f"el máximo es {max_target_candles}. Reduce el rango de fechas."
            )
        fetch_start = (request.start_ms // target_ms) * target_ms - warmup_candles * target_ms
        fetch_end = ((request.end_ms // target_ms) + 1) * target_ms + followup_candles * target_ms - execution_ms
        total_source = ((fetch_end - fetch_start) // execution_ms) + 1
        if total_source > max_target_candles * ratio:
            raise ValueError("El rango solicitado excede el máximo de datos para la temporalidad de ejecución.")
        source = client.get_kline_history(request.symbol, interval=execution_interval,
                                          total=total_source, end_time=fetch_end, start_time=fetch_start)
        targets = aggregate_candles(source, request.interval, execution_interval, require_complete=True)
        return targets, source, request.start_ms, request.end_ms

    target_count = request.candle_count or 500
    if target_count > max_target_candles:
        raise ValueError(f"Máximo de {max_target_candles} velas objetivo por backtest.")
    # Pedimos un grupo adicional para compensar la vela objetivo más reciente,
    # que normalmente todavía está abierta y se descarta por incompleta.
    source = client.get_kline_history(request.symbol, interval=execution_interval,
                                      total=(target_count + 1) * ratio)
    targets = aggregate_candles(source, request.interval, execution_interval, require_complete=True)
    if len(targets) > target_count:
        targets = targets[-target_count:]
        keep_times = {int(c["time"]) for c in targets}
        source = [c for c in source if (int(c["time"]) // target_ms) * target_ms in keep_times]
    return targets, source, None, None
