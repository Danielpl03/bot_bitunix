"""Motor Dual Thrust con ejecución conservadora, costes y curva de capital."""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from strategy_dual_thrust import calculate_levels

PENDING = "PENDIENTE"
REVERSAL = "REVERSIÓN"
HORIZON = "HORIZONTE"

@dataclass
class DualThrustTrade:
    time: int
    entry_time: int
    exit_time: int | None
    side: str
    entry: float
    exit_price: float | None
    exit_reason: str
    range_value: float
    buy_track_entry: float
    sell_track_entry: float
    bars_held: int | None
    pnl_pct: float | None
    r_multiple: float | None = None
    gross_pnl_pct: float | None = None
    fees_pct: float | None = None
    slippage_pct: float | None = None


def run_backtest(closes, highs, lows, opens, times, horizon=None, *, lookback=4, k1=0.6, k2=0.7,
                 fee_bps=0.0, slippage_bps=0.0):
    """Ejecuta Dual Thrust sin lookahead.

    Las bandas de cada vela usan exclusivamente las N velas anteriores y la
    apertura de la vela actual. Las entradas/salidas se activan por High/Low.
    Si ambos niveles se tocan en una vela, la señal de esa vela se ignora.
    Fee y slippage se expresan en puntos básicos por lado (1 bp = 0.01%).
    """
    n = len(closes)
    if not (len(highs) == len(lows) == len(opens) == len(times) == n):
        raise ValueError("Las series OHLC y tiempos deben tener la misma longitud.")
    if lookback < 1 or k1 <= 0 or k2 <= 0:
        raise ValueError("N, K1 y K2 deben ser mayores que cero.")
    if horizon is not None and horizon < 1:
        raise ValueError("El horizonte debe ser un entero positivo.")
    if not isfinite(fee_bps) or fee_bps < 0 or not isfinite(slippage_bps) or slippage_bps < 0:
        raise ValueError("Comisiones y deslizamiento deben ser números finitos no negativos.")
    fee_rate = fee_bps / 10_000.0
    slip_rate = slippage_bps / 10_000.0
    trades = []
    position = None

    for i in range(lookback, n):
        levels = calculate_levels(opens, highs, lows, closes, i, lookback=lookback, k1=k1, k2=k2)
        if levels is None:
            continue

        # El horizonte vence independientemente de que la vela sea ambigua.
        if position is not None and horizon is not None and i - position["entry_index"] >= horizon:
            raw_exit = float(opens[i])
            _close_trade(trades, position, int(times[i]), _adverse_fill(raw_exit, position["side"], is_entry=False, slip=slip_rate), HORIZON, i, fee_rate, slip_rate)
            position = None

        hit_long = float(highs[i]) >= levels.buy_track
        hit_short = float(lows[i]) <= levels.sell_track
        # OHLC no revela cuál nivel se alcanzó primero: no inventamos orden.
        if hit_long and hit_short:
            continue
        triggered_side = "LONG" if hit_long else ("SHORT" if hit_short else None)
        if triggered_side is None or (position is not None and position["side"] == triggered_side):
            continue

        raw_fill = levels.buy_track if triggered_side == "LONG" else levels.sell_track
        if position is not None:
            exit_fill = _adverse_fill(float(raw_fill), position["side"], is_entry=False, slip=slip_rate)
            _close_trade(trades, position, int(times[i]), exit_fill, REVERSAL, i, fee_rate, slip_rate)

        entry_fill = _adverse_fill(float(raw_fill), triggered_side, is_entry=True, slip=slip_rate)
        position = {
            "time": int(times[i]), "entry_time": int(times[i]), "entry_index": i,
            "side": triggered_side, "entry": entry_fill, "range_value": levels.range_value,
            "buy_track": levels.buy_track, "sell_track": levels.sell_track,
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


def _adverse_fill(price, side, *, is_entry, slip):
    """Aplica deslizamiento adverso al precio de ejecución."""
    if side == "LONG":
        return price * (1 + slip) if is_entry else price * (1 - slip)
    return price * (1 - slip) if is_entry else price * (1 + slip)


def _close_trade(trades, position, exit_time, exit_price, reason, exit_index, fee_rate, slip_rate):
    entry = position["entry"]
    if entry <= 0 or exit_price <= 0:
        raise ValueError("Los precios de ejecución deben ser positivos.")
    direction = 1 if position["side"] == "LONG" else -1
    gross_pct = direction * (exit_price - entry) / entry * 100.0
    fees_pct = fee_rate * (1.0 + exit_price / entry) * 100.0
    # Estimación contable de slippage de entrada y salida; ya está reflejado en gross_pct.
    slippage_pct = slip_rate * (1.0 + exit_price / entry) * 100.0
    net_pct = gross_pct - fees_pct
    trades.append(DualThrustTrade(
        time=position["time"], entry_time=position["entry_time"], exit_time=exit_time,
        side=position["side"], entry=entry, exit_price=exit_price, exit_reason=reason,
        range_value=position["range_value"], buy_track_entry=position["buy_track"],
        sell_track_entry=position["sell_track"], bars_held=exit_index-position["entry_index"],
        pnl_pct=net_pct, gross_pnl_pct=gross_pct, fees_pct=fees_pct, slippage_pct=slippage_pct,
    ))


def summarize(trades):
    """Resumen por operaciones cerradas, con rentabilidad compuesta y drawdown real."""
    def group(items):
        resolved = sorted((t for t in items if t.pnl_pct is not None), key=lambda t: (t.exit_time or 0, t.entry_time))
        wins = [t for t in resolved if t.pnl_pct > 0]
        losses = [t for t in resolved if t.pnl_pct < 0]
        gross_profit = sum(t.pnl_pct for t in wins)
        gross_loss = abs(sum(t.pnl_pct for t in losses))
        equity = peak = 1.0
        max_dd = 0.0
        for t in resolved:
            equity *= max(0.0, 1.0 + t.pnl_pct / 100.0)
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak * 100.0)
        sum_net = sum(t.pnl_pct for t in resolved)
        sum_gross = sum((t.gross_pnl_pct or 0.0) for t in resolved)
        sum_fees = sum((t.fees_pct or 0.0) for t in resolved)
        sum_slippage = sum((t.slippage_pct or 0.0) for t in resolved)
        return {
            "total": len(items), "resueltas": len(resolved),
            "reversiones": sum(t.exit_reason == REVERSAL for t in items),
            "horizonte": sum(t.exit_reason == HORIZON for t in items),
            "pendientes": sum(t.exit_reason == PENDING for t in items),
            "win_rate": len(wins)/len(resolved)*100 if resolved else None,
            "pnl_pct_total": sum_net if resolved else None,
            "pnl_pct_bruto": sum_gross if resolved else None,
            "costes_pct": sum_fees if resolved else None,
            "deslizamiento_pct": sum_slippage if resolved else None,
            "pnl_pct_compuesto": (equity - 1.0) * 100.0 if resolved else None,
            "pnl_pct_medio": sum_net/len(resolved) if resolved else None,
            "profit_factor": gross_profit/gross_loss if gross_loss else (float("inf") if gross_profit else None),
            "max_drawdown_pct": max_dd if resolved else None,
        }
    return {"total": group(trades), "long": group([t for t in trades if t.side == "LONG"]),
            "short": group([t for t in trades if t.side == "SHORT"])}


def build_equity_curve(trades, initial_equity=10000.0):
    """Devuelve puntos de capital por operación cerrada; posiciones abiertas se excluyen."""
    if initial_equity <= 0:
        raise ValueError("El capital inicial debe ser positivo.")
    resolved = sorted((t for t in trades if t.pnl_pct is not None), key=lambda t: (t.exit_time or 0, t.entry_time))
    equity = peak = float(initial_equity)
    curve = []
    for index, trade in enumerate(resolved, 1):
        equity *= max(0.0, 1.0 + trade.pnl_pct / 100.0)
        peak = max(peak, equity)
        dd = (peak-equity)/peak*100 if peak else 0.0
        curve.append({"trade_no": index, "exit_time": trade.exit_time, "side": trade.side,
                      "pnl_pct": trade.pnl_pct, "equity": equity, "peak": peak, "drawdown_pct": dd})
    return curve
