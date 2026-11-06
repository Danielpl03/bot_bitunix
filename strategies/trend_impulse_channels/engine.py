"""Motor breakout de Trend Impulse Channels. Sin órdenes reales."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
from .indicator import calculate_trend_impulse_channels

@dataclass
class TICTrade:
    signal_time:int; entry_time:int; side:str; entry:float; stop_loss:float
    tp1_time:int|None=None; tp1_price:float|None=None; tp1_pct:float=50.0
    tp2_target:float|None=None; tp2_time:int|None=None; tp2_price:float|None=None
    exit_time:int|None=None; exit_price:float|None=None; outcome:str='OPEN'
    quantity:float=0.0; remaining_qty:float=0.0; pnl_usdt:float=0.0
    pnl_pct:float|None=None; risk_usdt:float=10.0


def run_backtest(opens, highs, lows, closes, times, *, tp1_pct=50.0, risk_usdt=10.0,
                 fee_pct=0.0, indicator_params=None):
    n=len(closes)
    if not all(len(x)==n for x in (opens,highs,lows,times)): raise ValueError('OHLC y tiempos deben tener igual longitud')
    if not 0 < float(tp1_pct) < 100: raise ValueError('tp1_pct debe ser mayor que 0 y menor que 100')
    if risk_usdt<=0 or fee_pct<0: raise ValueError('Riesgo debe ser >0 y comisión >=0')
    p=indicator_params or {}
    ind=calculate_trend_impulse_channels(opens,highs,lows,closes,**p)
    trades=[]; active=None; pending=None
    prev_dir=None
    for i in range(n):
        # Las entradas se ejecutan al cierre de la propia vela de ruptura.
        # Gestión intrabar: SL tiene prioridad ante ambigüedad OHLC.
        if active is not None:
            hit_sl=(float(lows[i])<=active.stop_loss) if active.side=='LONG' else (float(highs[i])>=active.stop_loss)
            if hit_sl:
                _realize(active,active.remaining_qty,active.stop_loss,fee_pct)
                active.exit_time=int(times[i]); active.exit_price=active.stop_loss; active.outcome='SL'; active=None
            else:
                direction=ind['direction'][i]
                changed=(prev_dir is not None and direction in (-1,1) and prev_dir in (-1,1) and direction!=prev_dir)
                # TP1 al cierre de la vela que cambia el canal; nunca en la vela de entrada.
                if changed and active.tp1_time is None and i>0 and int(times[i])>active.entry_time:
                    part=active.quantity*active.tp1_pct/100.0
                    px=float(closes[i]); _realize(active,part,px,fee_pct)
                    active.remaining_qty=max(0.0,active.remaining_qty-part)
                    active.tp1_time=int(times[i]); active.tp1_price=px
                    active.tp2_target=ind['upper'][i] if active.side=='LONG' else ind['lower'][i]
                # TP2 en la banda extrema del canal nuevo capturado en TP1.
                if active is not None and active.tp1_time is not None and active.tp1_time < int(times[i]) and active.tp2_target is not None:
                    target=float(active.tp2_target)
                    hit=(float(highs[i])>=target) if active.side=='LONG' else (float(lows[i])<=target)
                    if hit:
                        px=target; _realize(active,active.remaining_qty,px,fee_pct)
                        active.tp2_time=int(times[i]); active.tp2_price=px
                        active.exit_time=int(times[i]); active.exit_price=px; active.outcome='TP2'; active=None
        # Detección de ruptura: la misma vela no puede cambiar el canal.
        direction=ind['direction'][i]
        changed_now=(prev_dir is not None and direction in (-1,1) and direction!=prev_dir)
        if active is None and pending is None and not changed_now and i>0:
            upper=ind['upper'][i]; lower=ind['lower'][i]
            if upper is not None and float(highs[i])>float(upper) and float(closes[i])>float(opens[i]):
                stop=min(float(lows[i]),float(lows[i-1])); entry=float(closes[i]); dist=entry-stop
                if dist>0:
                    qty=float(risk_usdt)/dist
                    active=TICTrade(int(times[i]),int(times[i]),'LONG',entry,stop,tp1_pct=float(tp1_pct),quantity=qty,remaining_qty=qty,risk_usdt=float(risk_usdt))
                    trades.append(active)
            elif lower is not None and float(lows[i])<float(lower) and float(closes[i])<float(opens[i]):
                stop=max(float(highs[i]),float(highs[i-1])); entry=float(closes[i]); dist=stop-entry
                if dist>0:
                    qty=float(risk_usdt)/dist
                    active=TICTrade(int(times[i]),int(times[i]),'SHORT',entry,stop,tp1_pct=float(tp1_pct),quantity=qty,remaining_qty=qty,risk_usdt=float(risk_usdt))
                    trades.append(active)
        prev_dir=direction
    return trades, ind


def _realize(trade, qty, price, fee_pct):
    qty=max(0.0,min(float(qty),trade.remaining_qty))
    signed=(price-trade.entry) if trade.side=='LONG' else (trade.entry-price)
    trade.pnl_usdt += signed*qty - (trade.entry*qty+price*qty)*fee_pct/100.0
    trade.remaining_qty=max(0.0,trade.remaining_qty-qty)
    if trade.quantity:
        trade.pnl_pct=trade.pnl_usdt/(trade.entry*trade.quantity)*100.0


def summarize(trades):
    resolved=[t for t in trades if t.outcome!='OPEN']
    wins=[t for t in resolved if t.pnl_usdt>0]
    return {'total':len(trades),'open':sum(t.outcome=='OPEN' for t in trades),'closed':len(resolved),
            'wins':len(wins),'win_rate':len(wins)/len(resolved)*100 if resolved else None,
            'pnl_usdt':sum(t.pnl_usdt for t in trades),'tp1':sum(t.tp1_time is not None for t in trades),
            'tp2':sum(t.outcome=='TP2' for t in trades),'sl':sum(t.outcome=='SL' for t in trades)}
