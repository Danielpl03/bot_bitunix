"""Iván Scherman 3 bearish candles: deterministic Pine-v6 style engine.

Signals are evaluated on closed OHLC bars. Live mode consumes the same
bar-transition function as the historical backtester.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
import math

@dataclass
class Position:
    entry_index: int
    entry_time: int
    entry_price: float
    stop_price: float | None
    bars_held: int = 0

def sma(values, length):
    out=[None]*len(values)
    if length<=0: raise ValueError("La longitud SMA debe ser positiva.")
    acc=0.0
    for i,v in enumerate(values):
        acc+=float(v)
        if i>=length: acc-=float(values[i-length])
        if i>=length-1: out[i]=acc/length
    return out

def atr(highs,lows,closes,length):
    """TradingView ta.atr = RMA(True Range), seeded with an SMA."""
    tr=[None]*len(closes)
    for i in range(len(closes)):
        prev=closes[i-1] if i else closes[i]
        tr[i]=max(highs[i]-lows[i],abs(highs[i]-prev),abs(lows[i]-prev))
    out=[None]*len(tr); seed=[]
    value=None
    for i,x in enumerate(tr):
        if value is None:
            seed.append(x)
            if len(seed)==length:
                value=sum(seed)/length; out[i]=value
        else:
            value=(value*(length-1)+x)/length; out[i]=value
    return out

def _validate(closes,highs,lows,opens,times,params):
    n=len(closes)
    if not n or not all(len(x)==n for x in (highs,lows,opens,times)):
        raise ValueError("OHLC y tiempos deben tener la misma longitud y no estar vacíos.")
    for name,default in (("sma_long",200),("bearish_bars",3),("sma_tp",7),("atr_length",14),("stop_lookback",2)):
        if int(params.get(name,default))<1: raise ValueError(f"{name} debe ser mayor que cero.")
    if int(params.get("sma_tp",7))<2: raise ValueError("La SMA take profit debe ser >= 2.")
    if params.get("bearish_mode","decreasing") not in ("decreasing","red"): raise ValueError("bearish_mode inválido.")
    if params.get("exit_mode","close") not in ("close","limit"): raise ValueError("exit_mode inválido.")
    if params.get("stop_type","none") not in ("none","percent","atr","lowest_close"): raise ValueError("stop_type inválido.")
    if float(params.get("stop_pct",10))<=0 or float(params.get("atr_mult",4))<=0: raise ValueError("Parámetros de stop deben ser positivos.")
    if float(params.get("commission_pct",.05))<0: raise ValueError("La comisión no puede ser negativa.")

def run_backtest(closes,highs,lows,opens,times,**kwargs):
    p={"sma_long":200,"require_rising":True,"require_above":True,"bearish_mode":"decreasing",
       "bearish_bars":3,"sma_tp":7,"exit_mode":"close","max_bars":0,"stop_type":"none",
       "stop_pct":10.0,"atr_length":14,"atr_mult":4.0,"stop_lookback":2,
       "commission_pct":.05,"start_time":0}
    p.update(kwargs); _validate(closes,highs,lows,opens,times,p)
    c=list(map(float,closes)); h=list(map(float,highs)); l=list(map(float,lows)); o=list(map(float,opens)); ts=list(map(int,times)); n=len(c)
    long_sma=sma(c,int(p["sma_long"])); tp_sma=sma(c,int(p["sma_tp"])); av=atr(h,l,c,int(p["atr_length"]))
    streak=[0]*n
    for i in range(n):
        bearish=(c[i]<c[i-1]) if p["bearish_mode"]=="decreasing" and i else ((c[i]<o[i]) if p["bearish_mode"]=="red" else False)
        streak[i]=(streak[i-1]+1 if bearish else 0) if i else (1 if bearish else 0)
    pos=None; trades=[]; events=[]; equity=[{"time":ts[0],"equity":100.0}]; eq=1.0
    for i in range(n):
        exit_px=None; reason=None
        if pos:
            pos.bars_held=i-pos.entry_index
            st=pos.stop_price
            if p["stop_type"] in ("percent","atr") and st is not None and l[i]<=st:
                exit_px=min(o[i],st); reason="STOP"
            elif p["exit_mode"]=="limit" and i>0 and tp_sma[i-1] is not None and h[i]>=tp_sma[i-1]:
                exit_px=max(o[i],tp_sma[i-1]); reason="TP"
            elif p["exit_mode"]=="close" and tp_sma[i] is not None and c[i]>tp_sma[i]:
                exit_px=c[i]; reason="TP"
            elif p["stop_type"]=="lowest_close" and st is not None and c[i]<st:
                exit_px=c[i]; reason="STOP"
            elif int(p["max_bars"])>0 and i-pos.entry_index>=int(p["max_bars"]):
                exit_px=c[i]; reason="TIME"
        if pos and reason:
            raw=exit_px/pos.entry_price-1
            net=raw-2*float(p["commission_pct"])/100
            eq*=max(0,1+net); equity.append({"time":ts[i],"equity":eq*100})
            trade={"side":"LONG","entry_index":pos.entry_index,"entry_time":pos.entry_time,
                   "entry_price":pos.entry_price,"exit_index":i,"exit_time":ts[i],"exit_price":exit_px,
                   "exit_reason":reason,"pnl_pct":net*100,"bars_held":i-pos.entry_index,
                   "stop_price":pos.stop_price}
            trades.append(trade); events.append({"type":"EXIT","time":ts[i],"price":exit_px,"reason":reason,"pnl_pct":net*100})
            pos=None
        # Pine: do not enter on same candle as an exit.
        if pos is None and reason is None and i>0 and ts[i]>=int(p["start_time"]):
            ready=long_sma[i] is not None and long_sma[i-1] is not None and tp_sma[i] is not None
            trend=ready and (not p["require_rising"] or long_sma[i]>long_sma[i-1]) and (not p["require_above"] or c[i]>long_sma[i])
            if trend and streak[i]>=int(p["bearish_bars"]):
                stop=None
                stype=p["stop_type"]
                if stype=="percent": stop=c[i]*(1-float(p["stop_pct"])/100)
                elif stype=="atr": stop=(c[i]-float(p["atr_mult"])*av[i]) if av[i] is not None else None
                elif stype=="lowest_close":
                    j=max(0,i-int(p["stop_lookback"])+1); stop=min(l[j:i+1])
                pos=Position(i,ts[i],c[i],stop)
                events.append({"type":"ENTRY","time":ts[i],"price":c[i],"streak":streak[i],"stop_price":stop,"tp_target":tp_sma[i]})
    if pos:
        raw=c[-1]/pos.entry_price-1; net=raw-2*float(p["commission_pct"])/100
        trades.append({"side":"LONG","entry_index":pos.entry_index,"entry_time":pos.entry_time,
            "entry_price":pos.entry_price,"exit_index":None,"exit_time":None,"exit_price":None,
            "exit_reason":"OPEN","pnl_pct":None,"unrealized_pnl_pct":net*100,"bars_held":n-1-pos.entry_index,
            "stop_price":pos.stop_price})
    closed=[t for t in trades if t["pnl_pct"] is not None]
    wins=[t for t in closed if t["pnl_pct"]>0]; losses=[t for t in closed if t["pnl_pct"]<0]
    gp=sum(t["pnl_pct"] for t in wins); gl=abs(sum(t["pnl_pct"] for t in losses))
    summary={"total":len(trades),"closed":len(closed),"open":len(trades)-len(closed),
        "wins":len(wins),"losses":len(losses),"win_rate_pct":len(wins)/len(closed)*100 if closed else 0,
        "net_pnl_pct_compounded":(eq-1)*100,"profit_factor":gp/gl if gl else (None if not gp else math.inf),
        "avg_pnl_pct":sum(t["pnl_pct"] for t in closed)/len(closed) if closed else 0,
        "best_pct":max((t["pnl_pct"] for t in closed),default=0),
        "worst_pct":min((t["pnl_pct"] for t in closed),default=0)}
    return {"strategy":"ivan_scherman_3_bearish","summary":summary,"trades":trades,"events":events,
        "equity":equity,"indicators":{"sma_long":long_sma,"sma_tp":tp_sma,"atr":av,"bearish_streak":streak},
        "parameters":p,"note":"Ejecución al cierre de vela. Stops porcentual/ATR y TP límite usan OHLC intrabar con prioridad stop antes que TP. No envía órdenes reales."}

def process_closed_bar(candles, params, position=None):
    """Live adapter: run shared engine over rolling history; return latest-bar events."""
    r=run_backtest([float(x["close"]) for x in candles],[float(x["high"]) for x in candles],
        [float(x["low"]) for x in candles],[float(x["open"]) for x in candles],
        [int(x["time"]) for x in candles],**params)
    last=int(candles[-1]["time"])
    events=[e for e in r["events"] if int(e["time"])==last]
    return r,events
