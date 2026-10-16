"""Motor de backtesting Dual Thrust con reversión de posición en rupturas opuestas."""
from __future__ import annotations
from dataclasses import dataclass
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


def run_backtest(closes, highs, lows, opens, times, horizon=None, *, lookback=4, k1=0.6, k2=0.7):
    n = len(closes)
    if not (len(highs) == len(lows) == len(opens) == len(times) == n):
        raise ValueError("Las series OHLC y tiempos deben tener la misma longitud.")
    if lookback < 1 or k1 <= 0 or k2 <= 0:
        raise ValueError("N, K1 y K2 deben ser mayores que cero.")
    trades = []
    position = None
    for i in range(lookback, n):
        levels = calculate_levels(opens, highs, lows, closes, i, lookback=lookback, k1=k1, k2=k2)
        if levels is None:
            continue
        hit_long = float(highs[i]) >= levels.buy_track
        hit_short = float(lows[i]) <= levels.sell_track
        # Con OHLC no puede conocerse el orden intrabar cuando ambos niveles se tocan.
        # Se omite la señal ambigua para no inventar una secuencia favorable.
        if hit_long and hit_short:
            continue
        triggered_side = "LONG" if hit_long else ("SHORT" if hit_short else None)

        # El horizonte es un límite externo opcional, no una regla del sistema original.
        if position is not None and horizon is not None and i - position["entry_index"] >= horizon:
            _close_trade(trades, position, int(times[i]), float(opens[i]), HORIZON, i)
            position = None

        if triggered_side is None:
            continue
        if position is not None and position["side"] == triggered_side:
            continue

        fill = levels.buy_track if triggered_side == "LONG" else levels.sell_track
        if position is not None:
            _close_trade(trades, position, int(times[i]), float(fill), REVERSAL, i)
        position = {
            "time": int(times[i]), "entry_time": int(times[i]), "entry_index": i,
            "side": triggered_side, "entry": float(fill), "range_value": levels.range_value,
            "buy_track": levels.buy_track, "sell_track": levels.sell_track,
        }

    if position is not None:
        trades.append(DualThrustTrade(
            time=position["time"], entry_time=position["entry_time"], exit_time=None,
            side=position["side"], entry=position["entry"], exit_price=None,
            exit_reason=PENDING, range_value=position["range_value"],
            buy_track_entry=position["buy_track"], sell_track_entry=position["sell_track"],
            bars_held=None, pnl_pct=None,
        ))
    return trades


def _close_trade(trades, position, exit_time, exit_price, reason, exit_index):
    entry = position["entry"]
    pnl = ((exit_price - entry) if position["side"] == "LONG" else (entry - exit_price)) / entry * 100.0
    trades.append(DualThrustTrade(
        time=position["time"], entry_time=position["entry_time"], exit_time=exit_time,
        side=position["side"], entry=entry, exit_price=exit_price, exit_reason=reason,
        range_value=position["range_value"], buy_track_entry=position["buy_track"],
        sell_track_entry=position["sell_track"], bars_held=exit_index-position["entry_index"],
        pnl_pct=pnl,
    ))


def summarize(trades):
    def group(items):
        resolved = [t for t in items if t.pnl_pct is not None]
        wins = [t for t in resolved if t.pnl_pct > 0]
        losses = [t for t in resolved if t.pnl_pct < 0]
        gross_profit = sum(t.pnl_pct for t in wins)
        gross_loss = abs(sum(t.pnl_pct for t in losses))
        equity = peak = dd = 0.0
        for t in resolved:
            equity += t.pnl_pct
            peak = max(peak, equity)
            dd = max(dd, peak-equity)
        return {
            "total": len(items), "resueltas": len(resolved),
            "reversiones": sum(t.exit_reason == REVERSAL for t in items),
            "horizonte": sum(t.exit_reason == HORIZON for t in items),
            "pendientes": sum(t.exit_reason == PENDING for t in items),
            "win_rate": len(wins)/len(resolved)*100 if resolved else None,
            "pnl_pct_total": sum(t.pnl_pct for t in resolved) if resolved else None,
            "pnl_pct_medio": sum(t.pnl_pct for t in resolved)/len(resolved) if resolved else None,
            "profit_factor": gross_profit/gross_loss if gross_loss else (float("inf") if gross_profit else None),
            "max_drawdown_pct": dd if resolved else None,
        }
    return {"total": group(trades), "long": group([t for t in trades if t.side == "LONG"]),
            "short": group([t for t in trades if t.side == "SHORT"])}
