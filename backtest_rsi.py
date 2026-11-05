"""Backtest RSI: entry next open, fixed USDT risk, SL structural, TP by RSI opposite extreme."""
from __future__ import annotations
from dataclasses import dataclass
from strategy_rsi import generate_rsi_signals
from indicators import rsi_indicator

TP, SL, HORIZON, PENDING = 'TP_RSI_OPUESTO', 'SL', 'HORIZONTE', 'PENDIENTE'

@dataclass
class RSITrade:
    time:int; entry_time:int; side:str; entry:float; stop_loss:float; target:float|None
    rsi:float; rsi_ma:float|None; risk_pct:float; target_r:float|None
    outcome:str; exit_time:int|None; exit_price:float|None; pnl_pct:float|None
    r_multiple:float|None; signal_type:str='RSI_TURN'
    risk_usdt:float=10.0; quantity:float=0.0; pnl_usdt:float|None=None
    pivot_used:bool=False


def run_backtest(closes,highs,lows,opens,times,horizon=None,*,rsi_period=14,
                 oversold=30.0,overbought=70.0,pivot_left=2,pivot_right=2,
                 stop_buffer_pct=0.0,risk_usdt=10.0,fee_pct=0.0,volumes=None,**_ignored):
    n=len(closes)
    if not all(len(x)==n for x in (highs,lows,opens,times)): raise ValueError('Todas las series OHLC y tiempos deben coincidir')
    if n<rsi_period+2: return []
    if risk_usdt<=0: raise ValueError('risk_usdt debe ser > 0')
    if fee_pct<0: raise ValueError('fee_pct debe ser >= 0')
    sigs=generate_rsi_signals(closes,highs,lows,rsi_period=rsi_period,oversold=oversold,
        overbought=overbought,pivot_left=pivot_left,pivot_right=pivot_right,
        stop_buffer_pct=stop_buffer_pct,volume=volumes)
    rsi=rsi_indicator(list(map(float,closes)),rsi_period=rsi_period,ma_type='None',ma_period=1,volume=volumes)['rsi']
    trades=[]
    for i,sig in enumerate(sigs[:-1]):
        if sig is None: continue
        ei=i+1; entry=float(opens[ei]); stop=float(sig.stop_loss)
        dist=entry-stop if sig.side=='LONG' else stop-entry
        if dist<=0: continue
        qty=float(risk_usdt)/dist
        end=n-1 if horizon is None else min(n-1,ei+int(horizon)-1)
        outcome= PENDING; xt=None; xp=None
        for j in range(ei,end+1):
            # Stop intrabar tiene prioridad conservadora si la misma vela también confirma salida RSI.
            hit_sl=(float(lows[j])<=stop) if sig.side=='LONG' else (float(highs[j])>=stop)
            if hit_sl:
                outcome=SL; xt=int(times[j]); xp=stop; break
            prev_rsi=rsi[j-1] if j>0 else None; curr_rsi=rsi[j]
            # Long sale cuando RSI abandona sobrecompra; short cuando abandona sobreventa.
            hit_tp=(prev_rsi is not None and curr_rsi is not None and
                    ((sig.side=='LONG' and prev_rsi>=overbought and curr_rsi<overbought) or
                     (sig.side=='SHORT' and prev_rsi<=oversold and curr_rsi>oversold)))
            if hit_tp:
                outcome=TP; xt=int(times[j]); xp=float(closes[j]); break
        if outcome==PENDING and horizon is not None and ei+int(horizon)-1<=n-1:
            j=end; outcome=HORIZON; xt=int(times[j]); xp=float(closes[j])
        if xp is None:
            pnl_pct=r_mult=pnl_usdt=None
        else:
            signed=(xp-entry) if sig.side=='LONG' else (entry-xp)
            gross=signed*qty
            fees=(entry*qty+xp*qty)*fee_pct/100.0
            pnl_usdt=gross-fees
            pnl_pct=(signed/entry*100 if entry else None)-2*fee_pct
            r_mult=pnl_usdt/risk_usdt
        trades.append(RSITrade(int(times[i]),int(times[ei]),sig.side,entry,stop,None,
            sig.rsi,None,dist/entry*100 if entry else 0,None,outcome,xt,xp,pnl_pct,r_mult,
            sig.signal_type,float(risk_usdt),qty,pnl_usdt,sig.pivot_used))
    return sorted(trades,key=lambda x:(x.entry_time,x.time))


def summarize(trades):
    resolved=[t for t in trades if t.r_multiple is not None]
    wins=[t for t in resolved if t.pnl_usdt>0]; losses=[t for t in resolved if t.pnl_usdt<0]
    def group(items):
        done=[t for t in items if t.r_multiple is not None]
        w=[t for t in done if t.pnl_usdt>0]; l=[t for t in done if t.pnl_usdt<0]
        gp=sum(t.pnl_usdt for t in w); gl=abs(sum(t.pnl_usdt for t in l))
        return {'total':len(items),'resueltas':len(done),'tp':sum(t.outcome==TP for t in items),
          'sl':sum(t.outcome==SL for t in items),'horizonte':sum(t.outcome==HORIZON for t in items),
          'pendientes':sum(t.outcome==PENDING for t in items),'win_rate':len(w)/len(done)*100 if done else None,
          'pnl_usdt':sum(t.pnl_usdt for t in done) if done else None,
          'r_total':sum(t.r_multiple for t in done) if done else None,
          'profit_factor':gp/gl if gl else (float('inf') if gp else None)}
    return {'total':group(trades),'long':group([t for t in trades if t.side=='LONG']),
            'short':group([t for t in trades if t.side=='SHORT'])}
