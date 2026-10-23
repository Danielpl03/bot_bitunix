"""Backtesting RSI por regímenes; permite varias operaciones simultáneas."""
from __future__ import annotations
from dataclasses import dataclass
from strategy_rsi import generate_rsi_signals

TP, SL, HORIZON, PENDING = "TP", "SL", "HORIZONTE", "PENDIENTE"

@dataclass
class RSITrade:
    time: int
    entry_time: int
    side: str
    entry: float
    stop_loss: float
    target: float
    rsi: float
    rsi_ma: float
    risk_pct: float
    target_r: float
    outcome: str
    exit_time: int | None
    exit_price: float | None
    pnl_pct: float | None
    r_multiple: float | None
    signal_type: str = "MA_CROSS"


def _simulate(side, stop, target, highs, lows, closes, times, entry_i, horizon):
    last = len(closes) - 1
    end = last if horizon is None else min(entry_i + horizon - 1, last)
    for j in range(entry_i, end + 1):
        hit_sl = lows[j] <= stop if side == "LONG" else highs[j] >= stop
        hit_tp = highs[j] >= target if side == "LONG" else lows[j] <= target
        if hit_sl: return SL, int(times[j]), float(stop)  # SL primero si OHLC no permite saber el orden
        if hit_tp: return TP, int(times[j]), float(target)
    if horizon is not None and entry_i + horizon - 1 <= last:
        j = entry_i + horizon - 1
        return HORIZON, int(times[j]), float(closes[j])
    return PENDING, None, None


def run_backtest(closes, highs, lows, opens, times, horizon=None, *, rsi_period=14,
                 ma_type="SMA", ma_period=14, sl_lookback=10, stop_mode="lookback",
                 pivot_left=2, pivot_right=2, pivot_buffer_pct=0.1, ignore_first_ma_cross=False,
                 use_extreme_filter=False, oversold=30.0, overbought=70.0, volumes=None):
    """Genera señales por vela y simula cada operación independientemente.

    use_extreme_filter se conserva por compatibilidad con el comando anterior;
    el sistema de regímenes extremos ahora es parte obligatoria de la estrategia.
    """
    if not (len(closes) == len(highs) == len(lows) == len(opens) == len(times)):
        raise ValueError("Todas las series OHLC y timestamps deben tener la misma longitud.")
    if volumes is not None and len(volumes) != len(closes):
        raise ValueError("volumes debe tener la misma longitud que las demás series.")
    signals = generate_rsi_signals(closes, highs, lows, rsi_period=rsi_period,
        ma_type=ma_type, ma_period=ma_period, sl_lookback=sl_lookback, stop_mode=stop_mode,
        pivot_left=pivot_left, pivot_right=pivot_right, pivot_buffer_pct=pivot_buffer_pct, ignore_first_ma_cross=ignore_first_ma_cross,
        oversold=oversold, overbought=overbought, volume=volumes)
    trades = []
    for i, sig in enumerate(signals[:-1]):
        if sig is None:
            continue
        entry_i = i + 1
        entry = float(opens[entry_i]); stop = float(sig.stop_loss)
        risk_distance = entry - stop if sig.side == "LONG" else stop - entry
        if risk_distance <= 0:
            continue
        target = entry + 2*risk_distance if sig.side == "LONG" else entry - 2*risk_distance
        if target <= 0:
            continue
        outcome, exit_time, exit_price = _simulate(sig.side, stop, target, highs, lows, closes, times, entry_i, horizon)
        if exit_price is None:
            pnl_pct = r_multiple = None
        else:
            signed = exit_price-entry if sig.side == "LONG" else entry-exit_price
            pnl_pct = signed / entry * 100 if entry else None
            r_multiple = signed / risk_distance
        trades.append(RSITrade(int(times[i]), int(times[entry_i]), sig.side, entry, stop, target,
            sig.rsi, sig.rsi_ma, risk_distance / entry * 100 if entry else 0, 2.0,
            outcome, exit_time, exit_price, pnl_pct, r_multiple, sig.signal_type))
    return sorted(trades, key=lambda t: (t.entry_time, t.time))


def summarize(trades):
    def group(items):
        resolved = [t for t in items if t.r_multiple is not None]
        wins = [t for t in resolved if t.r_multiple > 0]
        losses = [t for t in resolved if t.r_multiple < 0]
        gross_profit = sum(t.r_multiple for t in wins)
        gross_loss = abs(sum(t.r_multiple for t in losses))
        cumulative = peak = max_dd = 0.0
        for t in sorted(resolved, key=lambda x: x.exit_time or 0):
            cumulative += t.r_multiple; peak = max(peak, cumulative); max_dd = max(max_dd, peak-cumulative)
        return {"total":len(items), "resueltas":len(resolved), "tp":sum(t.outcome==TP for t in items),
            "sl":sum(t.outcome==SL for t in items), "horizonte":sum(t.outcome==HORIZON for t in items),
            "pendientes":sum(t.outcome==PENDING for t in items),
            "win_rate":len(wins)/len(resolved)*100 if resolved else None,
            "r_total":sum(t.r_multiple for t in resolved) if resolved else None,
            "r_medio":sum(t.r_multiple for t in resolved)/len(resolved) if resolved else None,
            "profit_factor":gross_profit/gross_loss if gross_loss else (float('inf') if gross_profit else None),
            "max_drawdown_r":max_dd if resolved else None,
            "pnl_pct_total":sum(t.pnl_pct for t in resolved if t.pnl_pct is not None) if resolved else None}
    return {"total":group(trades), "long":group([t for t in trades if t.side=="LONG"]), "short":group([t for t in trades if t.side=="SHORT"])}
