"""Backtest de reversión a la media con Bollinger + Estocástico."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from strategy_reversion import detect_reversion_signal

TP = "TP"
SL = "SL"
HORIZON = "HORIZONTE"
PENDING = "PENDIENTE"

@dataclass
class ReversionTrade:
    time: int                 # timestamp de la vela de señal
    entry_time: int           # timestamp de la vela donde se ejecuta (siguiente open)
    side: str
    entry: float
    stop_loss: float
    target: float             # banda media de Bollinger calculada en la vela de señal
    k: float
    d: float
    lower: float
    middle: float
    upper: float
    risk_pct: float
    target_r: float
    outcome: str
    exit_time: int | None
    exit_price: float | None
    pnl_pct: float | None
    r_multiple: float | None


def _simulate(side, entry, stop, target, opens, highs, lows, closes, times, entry_i, horizon):
    last = len(closes) - 1
    end = last if horizon is None else min(entry_i + horizon - 1, last)
    for j in range(entry_i, end + 1):
        if side == "LONG":
            hit_sl, hit_tp = lows[j] <= stop, highs[j] >= target
        else:
            hit_sl, hit_tp = highs[j] >= stop, lows[j] <= target
        # Si una misma vela toca ambos niveles, se adopta la hipótesis conservadora: stop primero.
        if hit_sl:
            return SL, int(times[j]), stop
        if hit_tp:
            return TP, int(times[j]), target
    if horizon is not None and entry_i + horizon - 1 <= last:
        j = entry_i + horizon - 1
        return HORIZON, int(times[j]), closes[j]
    return PENDING, None, None


def run_backtest(closes: list[float], highs: list[float], lows: list[float], opens: list[float], times: list[int],
                 horizon: int | None = None, *, bb_period: int = 20, bb_std: float = 2.0,
                 k_period: int = 14, smooth_k: int = 3, d_period: int = 3,
                 oversold: float = 20.0, overbought: float = 80.0, sl_lookback: int = 10) -> list[ReversionTrade]:
    trades = []
    # Una sola posición simultánea por símbolo: no se abre otra hasta que la anterior cierre.
    time_to_index = {int(t): i for i, t in enumerate(times)}
    blocked_until_signal_index = -1
    for i in range(len(closes) - 1):
        if i < blocked_until_signal_index:
            continue
        sig = detect_reversion_signal(
            closes[:i+1], highs[:i+1], lows[:i+1], bb_period=bb_period, bb_std=bb_std,
            k_period=k_period, smooth_k=smooth_k, d_period=d_period,
            oversold=oversold, overbought=overbought, sl_lookback=sl_lookback,
        )
        if sig is None:
            continue
        entry_i = i + 1
        entry = float(opens[entry_i])
        stop, target = float(sig.stop_loss), float(sig.target)
        # No entrar si la apertura siguiente ya invalidó la estructura o pasó el objetivo.
        if sig.side == "LONG" and not (stop < entry < target):
            continue
        if sig.side == "SHORT" and not (target < entry < stop):
            continue
        risk_distance = abs(entry - stop)
        risk_pct = risk_distance / entry * 100.0
        target_r = abs(target - entry) / risk_distance
        outcome, exit_time, exit_price = _simulate(
            sig.side, entry, stop, target, opens, highs, lows, closes, times, entry_i, horizon
        )
        if exit_price is None:
            pnl_pct = r_multiple = None
        else:
            signed = (exit_price - entry) if sig.side == "LONG" else (entry - exit_price)
            pnl_pct = signed / entry * 100.0
            r_multiple = signed / risk_distance
        trades.append(ReversionTrade(
            time=int(times[i]), entry_time=int(times[entry_i]), side=sig.side, entry=entry,
            stop_loss=stop, target=target, k=sig.k, d=sig.d, lower=sig.lower,
            middle=sig.middle, upper=sig.upper, risk_pct=risk_pct, target_r=target_r,
            outcome=outcome, exit_time=exit_time, exit_price=exit_price,
            pnl_pct=pnl_pct, r_multiple=r_multiple,
        ))
        if exit_time is None:
            blocked_until_signal_index = len(closes)
        else:
            # Una nueva señal puede confirmarse en la vela de salida, pero su entrada
            # será en la apertura siguiente; por eso se desbloquea al índice de salida.
            blocked_until_signal_index = time_to_index.get(int(exit_time), entry_i)
    return trades


def summarize(trades: list[ReversionTrade]) -> dict:
    def group(items):
        resolved = [t for t in items if t.r_multiple is not None]
        wins = [t for t in resolved if t.r_multiple > 0]
        losses = [t for t in resolved if t.r_multiple < 0]
        gross_profit = sum(t.r_multiple for t in wins)
        gross_loss = abs(sum(t.r_multiple for t in losses))
        rs = [t.r_multiple for t in resolved]
        cumulative, peak, max_dd = 0.0, 0.0, 0.0
        for r in rs:
            cumulative += r
            peak = max(peak, cumulative)
            max_dd = max(max_dd, peak - cumulative)
        return {
            "total": len(items), "resueltas": len(resolved), "tp": sum(t.outcome == TP for t in items),
            "sl": sum(t.outcome == SL for t in items), "horizonte": sum(t.outcome == HORIZON for t in items),
            "pendientes": sum(t.outcome == PENDING for t in items),
            "win_rate": len(wins) / len(resolved) * 100 if resolved else None,
            "r_total": sum(rs) if rs else None, "r_medio": sum(rs) / len(rs) if rs else None,
            "profit_factor": gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit else None),
            "max_drawdown_r": max_dd if resolved else None,
            "pnl_pct_total": sum(t.pnl_pct for t in resolved if t.pnl_pct is not None) if resolved else None,
        }
    return {"total": group(trades), "long": group([t for t in trades if t.side == "LONG"]),
            "short": group([t for t in trades if t.side == "SHORT"])}
