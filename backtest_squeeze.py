"""Backtest Squeeze Bollinger + Keltner con entrada en la siguiente apertura."""
from __future__ import annotations
from dataclasses import dataclass
from strategy_squeeze import detect_squeeze_signal

TP, SL, HORIZON, PENDING = "TP", "SL", "HORIZONTE", "PENDIENTE"
TP_R = 2.0

@dataclass
class SqueezeTrade:
    time: int; entry_time: int; side: str; entry: float; stop_loss: float; target: float
    bb_lower: float; bb_middle: float; bb_upper: float; kc_lower: float; kc_middle: float; kc_upper: float
    momentum: float; risk_pct: float; target_r: float; outcome: str
    exit_time: int | None; exit_price: float | None; pnl_pct: float | None; r_multiple: float | None

def run_backtest(closes, highs, lows, opens, times, horizon=None, *, tp_r: float = TP_R,
                 bb_period: int = 20, bb_std: float = 2.0, kc_ema_period: int = 20,
                 atr_period: int = 20, kc_multiplier: float = 1.5, stop_lookback: int = 10,
                 momentum_period: int = 12):
    trades=[]; i=0; n=len(closes)
    while i < n-1:
        sig=detect_squeeze_signal(closes[:i+1], highs[:i+1], lows[:i+1], bb_period=bb_period,
            bb_std=bb_std, kc_ema_period=kc_ema_period, atr_period=atr_period,
            kc_multiplier=kc_multiplier, stop_lookback=stop_lookback, momentum_period=momentum_period)
        if sig is None:
            i += 1; continue
        entry_i=i+1; entry=float(opens[entry_i]); stop=float(sig.stop_loss)
        risk=abs(entry-stop)
        if risk <= 0 or (sig.side=="LONG" and stop>=entry) or (sig.side=="SHORT" and stop<=entry):
            i += 1; continue
        target=entry + (tp_r*risk if sig.side=="LONG" else -tp_r*risk)
        last=n-1; end=last if horizon is None else min(entry_i+horizon-1,last)
        outcome=PENDING; exit_time=exit_price=None
        for j in range(entry_i,end+1):
            hit_sl = lows[j] <= stop if sig.side=="LONG" else highs[j] >= stop
            hit_tp = highs[j] >= target if sig.side=="LONG" else lows[j] <= target
            if hit_sl: outcome,exit_time,exit_price=SL,int(times[j]),stop; break
            if hit_tp: outcome,exit_time,exit_price=TP,int(times[j]),target; break
        if exit_time is None and horizon is not None and entry_i+horizon-1 <= last:
            j=entry_i+horizon-1; outcome,exit_time,exit_price=HORIZON,int(times[j]),float(closes[j])
        pnl=None if exit_price is None else ((exit_price-entry) if sig.side=="LONG" else (entry-exit_price))/entry*100
        r_mult=None if exit_price is None else ((exit_price-entry) if sig.side=="LONG" else (entry-exit_price))/risk
        trades.append(SqueezeTrade(int(times[i]),int(times[entry_i]),sig.side,entry,stop,target,
            sig.bb_lower,sig.bb_middle,sig.bb_upper,sig.kc_lower,sig.kc_middle,sig.kc_upper,
            sig.momentum,risk/entry*100,abs(target-entry)/risk,outcome,exit_time,exit_price,pnl,r_mult))
        if exit_time is None: break
        # No solapar operaciones: volver a buscar señales desde la vela de salida.
        i=max(i+1, next((idx for idx,t in enumerate(times) if int(t)==exit_time),entry_i))
    return trades

def summarize(trades):
    def group(items):
        resolved=[t for t in items if t.r_multiple is not None]; wins=[t for t in resolved if t.r_multiple>0]; losses=[t for t in resolved if t.r_multiple<0]
        gp=sum(t.r_multiple for t in wins); gl=abs(sum(t.r_multiple for t in losses)); total=[]; cur=peak=dd=0.0
        for t in resolved:
            cur += t.r_multiple; peak=max(peak,cur); dd=max(dd,peak-cur)
        return {"total":len(items),"resueltas":len(resolved),"tp":sum(t.outcome==TP for t in items),"sl":sum(t.outcome==SL for t in items),"horizonte":sum(t.outcome==HORIZON for t in items),"pendientes":sum(t.outcome==PENDING for t in items),"win_rate":len(wins)/len(resolved)*100 if resolved else None,"r_total":sum(t.r_multiple for t in resolved) if resolved else None,"r_medio":sum(t.r_multiple for t in resolved)/len(resolved) if resolved else None,"profit_factor":gp/gl if gl else (float('inf') if gp else None),"max_drawdown_r":dd if resolved else None,"pnl_pct_total":sum(t.pnl_pct for t in resolved if t.pnl_pct is not None) if resolved else None}
    return {"total":group(trades),"long":group([t for t in trades if t.side=="LONG"]),"short":group([t for t in trades if t.side=="SHORT"])}
