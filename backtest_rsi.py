"""Backtest RSI: entrada al open siguiente, SL estructural y TP al cierre al salir del extremo opuesto."""
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
    sigs=generate_rsi_signals(closes,highs,lows,rsi_period=rsi_period,oversold=oversold,overbought=overbought,
        pivot_left=pivot_left,pivot_right=pivot_right,stop_buffer_pct=stop_buffer_pct,volume=volumes)
    rsi=rsi_indicator(list(map(float,closes)),rsi_period=rsi_period,ma_type='None',ma_period=1,volume=volumes)['rsi']
    active=[]; pending=[]; completed=[]; last_sl_exit_idx=None; reentry_armed=False

    def finish(tr, outcome, idx, exit_price):
        signed=(exit_price-tr['entry']) if tr['signal'].side=='LONG' else (tr['entry']-exit_price)
        gross=signed*tr['quantity']; fees=(tr['entry']*tr['quantity']+exit_price*tr['quantity'])*fee_pct/100.0
        pnl=gross-fees
        completed.append(RSITrade(int(times[tr['signal_idx']]),int(times[tr['entry_idx']]),tr['signal'].side,
            tr['entry'],tr['stop'],None,tr['signal'].rsi,None,tr['dist']/tr['entry']*100 if tr['entry'] else 0,
            None,outcome,int(times[idx]),float(exit_price),signed/tr['entry']*100-2*fee_pct if tr['entry'] else None,
            pnl/risk_usdt,tr['signal'].signal_type,float(risk_usdt),tr['quantity'],pnl,tr['signal'].pivot_used))

    for i in range(n):
        # Señales de la vela anterior se ejecutan en la apertura de esta vela.
        for sig_idx,sig in pending:
            entry=float(opens[i]); stop=float(sig.stop_loss)
            dist=(entry-stop) if sig.side=='LONG' else (stop-entry)
            if dist<=0: continue
            active.append({'signal':sig,'signal_idx':sig_idx,'entry_idx':i,'entry':entry,'stop':stop,
                           'dist':dist,'quantity':float(risk_usdt)/dist})

        still=[]
        for tr in active:
            hit_sl=(float(lows[i])<=tr['stop']) if tr['signal'].side=='LONG' else (float(highs[i])>=tr['stop'])
            if hit_sl:
                finish(tr,SL,i,tr['stop'])
                last_sl_exit_idx=i; reentry_armed=True
                continue
            prev_rsi=rsi[i-1] if i>0 else None; curr_rsi=rsi[i]
            hit_tp=(prev_rsi is not None and curr_rsi is not None and
                ((tr['signal'].side=='LONG' and prev_rsi>=overbought and curr_rsi<overbought) or
                 (tr['signal'].side=='SHORT' and prev_rsi<=oversold and curr_rsi>oversold)))
            if hit_tp:
                finish(tr,TP,i,float(closes[i])); continue
            if horizon is not None and i>=tr['entry_idx']+int(horizon)-1:
                finish(tr,HORIZON,i,float(closes[i])); continue
            still.append(tr)
        active=still

        sig=sigs[i]
        if sig is None or i+1>=n: 
            pending=[]
            continue
        is_reentry=sig.signal_type in ('RSI_REENTRY','RSI_DIVERGENCE')
        if not is_reentry:
            # Un nuevo giro inicial inicia un ciclo independiente y consume
            # cualquier ventana de reentrada pendiente del régimen anterior.
            reentry_armed=False
        if is_reentry:
            # Solo se permite una reentrada por cada SL y nunca mientras quede
            # otra posición abierta en este símbolo/estrategia.
            if not reentry_armed or last_sl_exit_idx is None or last_sl_exit_idx>=i or active:
                pending=[]; continue
            reentry_armed=False
        pending=[(i,sig)]

    # Pendientes al final del histórico y operaciones sin salida quedan abiertas.
    for tr in active:
        completed.append(RSITrade(int(times[tr['signal_idx']]),int(times[tr['entry_idx']]),tr['signal'].side,
            tr['entry'],tr['stop'],None,tr['signal'].rsi,None,tr['dist']/tr['entry']*100 if tr['entry'] else 0,
            None,PENDING,None,None,None,None,tr['signal'].signal_type,float(risk_usdt),tr['quantity'],None,
            tr['signal'].pivot_used))
    return sorted(completed,key=lambda x:(x.entry_time,x.time))


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
