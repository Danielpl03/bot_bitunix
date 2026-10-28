"""Backtest engine for a Python port of Trend Trader-Remastered (TTR).

The Pine script delegates PSAR/fractals/position bookkeeping to three external
TradingView libraries. This module implements the visible rules and explicit
approximations for those library internals; results should be validated against
TradingView before being treated as tick-for-tick identical.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional
import math

@dataclass
class Trade:
    side: str
    entry_index: int
    entry_time: int
    entry_price: float
    exit_index: Optional[int] = None
    exit_time: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl_pct: Optional[float] = None
    bars_held: Optional[int] = None


def _psar(highs, lows, start=.02, inc=.02, maximum=.025):
    n=len(highs); sar=[math.nan]*n
    if n < 3: return sar
    bull = (highs[1]+lows[1]) >= (highs[0]+lows[0])
    af=float(start); ep=highs[0] if bull else lows[0]; sar[0]=lows[0] if bull else highs[0]
    for i in range(1,n):
        prev=sar[i-1]
        cur=prev+af*(ep-prev)
        if bull:
            cur=min(cur, lows[i-1], lows[i-2] if i>1 else lows[i-1])
            if lows[i] < cur:
                bull=False; cur=ep; ep=lows[i]; af=start
            elif highs[i] > ep:
                ep=highs[i]; af=min(maximum,af+inc)
        else:
            cur=max(cur, highs[i-1], highs[i-2] if i>1 else highs[i-1])
            if highs[i] > cur:
                bull=True; cur=ep; ep=highs[i]; af=start
            elif lows[i] < ep:
                ep=lows[i]; af=min(maximum,af+inc)
        sar[i]=cur
    return sar


def _fractals(highs,lows,lb_high=2,lb_low=2):
    """Confirmed Bill Williams pivots; a pivot is usable after right-side bars close."""
    n=len(highs); hh=[False]*n; ll=[False]*n
    for i in range(lb_high, n-lb_high):
        h=highs[i]
        if all(h>highs[j] for j in range(i-lb_high,i)) and all(h>=highs[j] for j in range(i+1,i+lb_high+1)):
            hh[i+lb_high]=True
    for i in range(lb_low, n-lb_low):
        l=lows[i]
        if all(l<lows[j] for j in range(i-lb_low,i)) and all(l<=lows[j] for j in range(i+1,i+lb_low+1)):
            ll[i+lb_low]=True
    return hh,ll


def run_backtest(closes, highs, lows, opens, times, *, psar_start=.02, psar_increment=.02, psar_max=.025,
                 frac_lb_high=2, frac_lb_low=2, sig_re_min_proximity=0.0,
                 sig_re_buy_no_tp=False, sig_re_sell_no_tp=False, pos_dir='Buy & Sell',
                 show_entry_buy=True, show_tp_buy=True, show_re_buy=True, show_exit_buy=False,
                 show_entry_sell=True, show_tp_sell=True, show_re_sell=True, show_exit_sell=False,
                 frac_src_high=None, frac_src_low=None, initial_capital=100.0, fee_pct=0.0, slippage_pct=0.0):
    n=len(closes)
    if not (len(highs)==len(lows)==len(opens)==len(times)==n): raise ValueError('OHLCV y tiempos deben tener la misma longitud.')
    if n<10: raise ValueError('Se necesitan al menos 10 velas para TTR.')
    if pos_dir not in ('Buy & Sell','Buy Only','Sell Only'): raise ValueError('pos_dir debe ser Buy & Sell, Buy Only o Sell Only.')
    if min(psar_start,psar_increment,psar_max)<=0 or frac_lb_high<1 or frac_lb_low<1: raise ValueError('Parámetros PSAR/fractales inválidos.')
    fh=list(map(float, frac_src_high if frac_src_high is not None else highs)); fl=list(map(float, frac_src_low if frac_src_low is not None else lows))
    sar=_psar(highs,lows,psar_start,psar_increment,psar_max)
    sar_lag=[math.nan]+sar[:-1]
    frac_h,frac_l=_fractals(fh,fl,frac_lb_high,frac_lb_low)
    allow_buy=pos_dir in ('Buy & Sell','Buy Only'); allow_sell=pos_dir in ('Buy & Sell','Sell Only')
    events=[]; trades=[]; current=None; last_tp=None; last_re=None; marks_tp=set(); marks_re=set(); equity=float(initial_capital); curve=[]
    for i in range(2,n):
        if not math.isfinite(sar_lag[i]) or not math.isfinite(sar_lag[i-1]): continue
        cross_high=highs[i]>sar_lag[i] and highs[i-1]<=sar_lag[i-1]
        cross_low=lows[i]<sar_lag[i] and lows[i-1]>=sar_lag[i-1]
        lag_cross=1 if sar_lag[i-1]>highs[i-1] and cross_high else (-1 if sar_lag[i-1]<lows[i-1] and cross_low else 0)
        enter='buy' if allow_buy and lag_cross==1 else ('sell' if allow_sell and lag_cross==-1 else None)
        exit_side=('buy' if current and current.side=='buy' and lag_cross==-1 else ('sell' if current and current.side=='sell' and lag_cross==1 else None))
        if exit_side:
            current.exit_index=i; current.exit_time=int(times[i]); current.exit_price=float(closes[i]); current.exit_reason='EXIT'; current.bars_held=i-current.entry_index
            direction=1 if current.side=='buy' else -1
            current.pnl_pct=(current.exit_price/current.entry_price-1)*100*direction-fee_pct*2-slippage_pct*2
            trades.append(current); events.append({'type':'EXIT','side':current.side,'index':i,'time':int(times[i]),'price':float(closes[i])})
            current=None; last_tp=None; last_re=None; marks_tp.clear(); marks_re.clear()
        if enter:
            if current:
                current.exit_index=i; current.exit_time=int(times[i]); current.exit_price=float(closes[i]); current.exit_reason='REVERSAL'; current.bars_held=i-current.entry_index
                direction=1 if current.side=='buy' else -1
                current.pnl_pct=(current.exit_price/current.entry_price-1)*100*direction-fee_pct*2-slippage_pct*2
                trades.append(current)
            current=Trade(enter,i,int(times[i]),float(closes[i])); last_tp=None; last_re=None; marks_tp.clear(); marks_re.clear()
            events.append({'type':'ENTRY','side':enter,'index':i,'time':int(times[i]),'price':float(closes[i]),'psar_lagging':sar_lag[i]})
        if not current: continue
        side=current.side
        proximity=abs(closes[i]-sar_lag[i])/abs(sar_lag[i])*100 if sar_lag[i] else 0
        up_bo=closes[i-1]>opens[i-1] and closes[i]<=opens[i]
        down_bo=closes[i-1]<opens[i-1] and closes[i]>=opens[i]
        # The published Pine loops over the most recent N fractals and requires a
        # breakout (boPrice/boTime). Use confirmed pivot + close breakout as proxy.
        action=None; action_price=None
        if up_bo:
            candidates=[j for j in range(max(0,i-frac_lb_high*3),i) if frac_h[j] and j>=current.entry_index and closes[i]>fh[j] and j not in marks_tp and j not in marks_re]
            if side=='buy' and closes[i]>current.entry_price and candidates and (last_tp is None or closes[i]>last_tp): action='TP'; action_price=closes[i]
            elif side=='sell' and candidates and proximity>sig_re_min_proximity and (last_tp is not None or sig_re_sell_no_tp) and (last_re is None or closes[i]>last_re): action='RE'; action_price=closes[i]
            if candidates and action: (marks_tp if action=='TP' else marks_re).add(candidates[-1])
        if down_bo and action is None:
            candidates=[j for j in range(max(0,i-frac_lb_low*3),i) if frac_l[j] and j>=current.entry_index and closes[i]<fl[j] and j not in marks_tp and j not in marks_re]
            if side=='sell' and closes[i]<current.entry_price and candidates and (last_tp is None or closes[i]<last_tp): action='TP'; action_price=closes[i]
            elif side=='buy' and candidates and proximity>sig_re_min_proximity and (last_tp is not None or sig_re_buy_no_tp) and (last_re is None or closes[i]<last_re): action='RE'; action_price=closes[i]
            if candidates and action: (marks_tp if action=='TP' else marks_re).add(candidates[-1])
        if action:
            if action=='TP': last_tp=action_price
            else: last_re=action_price
            events.append({'type':action,'side':side,'index':i,'time':int(times[i]),'price':float(action_price)})
        direction=1 if side=='buy' else -1
        unreal=(closes[i]/current.entry_price-1)*100*direction
        curve.append({'time':int(times[i]),'equity':equity*(1+unreal/100)})
    if current:
        i=n-1; current.exit_index=i; current.exit_time=int(times[i]); current.exit_price=float(closes[i]); current.exit_reason='END_OF_DATA'; current.bars_held=i-current.entry_index
        direction=1 if current.side=='buy' else -1
        current.pnl_pct=(current.exit_price/current.entry_price-1)*100*direction-fee_pct*2-slippage_pct*2
        trades.append(current)
    closed=[t for t in trades if t.pnl_pct is not None]; wins=[t for t in closed if t.pnl_pct>0]; losses=[t for t in closed if t.pnl_pct<0]
    gross_win=sum(t.pnl_pct for t in wins); gross_loss=abs(sum(t.pnl_pct for t in losses))
    eq=100.0; equity_curve=[{'time':None,'equity':eq}]
    for t in closed:
        eq*=1+t.pnl_pct/100; equity_curve.append({'time':t.exit_time,'equity':eq})
    # expose signal toggles as event filters, mirroring Pine visibility inputs
    visible=[]
    for e in events:
        typ=e['type']; side=e['side']
        keep = (typ=='ENTRY' and ((side=='buy' and show_entry_buy) or (side=='sell' and show_entry_sell))) or \
               (typ=='EXIT' and ((side=='buy' and show_exit_buy) or (side=='sell' and show_exit_sell))) or \
               (typ=='TP' and ((side=='buy' and show_tp_buy) or (side=='sell' and show_tp_sell))) or \
               (typ=='RE' and ((side=='buy' and show_re_buy) or (side=='sell' and show_re_sell)))
        if keep: visible.append(e)
    summary={'trades':len(closed),'wins':len(wins),'losses':len(losses),'win_rate_pct':len(wins)/len(closed)*100 if closed else 0.0,
             'net_pnl_pct_compounded':(eq-1)*100,'profit_factor':gross_win/gross_loss if gross_loss else None,
             'avg_pnl_pct':sum(t.pnl_pct for t in closed)/len(closed) if closed else 0.0,'max_drawdown_pct':_max_drawdown(equity_curve)}
    return {'strategy':'trend_trader_remastered','summary':summary,'trades':[asdict(t) for t in trades], 'events':visible,
            'equity':equity_curve,'indicators':{'psar':sar,'psar_lagging':sar_lag},
            'parameters':{'pos_dir':pos_dir,'psar_start':psar_start,'psar_increment':psar_increment,'psar_max':psar_max,'frac_lb_high':frac_lb_high,'frac_lb_low':frac_lb_low,'sig_re_min_proximity':sig_re_min_proximity,'sig_re_buy_no_tp':sig_re_buy_no_tp,'sig_re_sell_no_tp':sig_re_sell_no_tp},
            'note':'Port Python de las reglas visibles del Pine. Los detalles internos de Tac.sar, Tac.BWFractals, ToolsPos.PosInfo y ToolsPos.PosAction requieren validar las librerías originales; los fractales/PSAR pueden diferir de TradingView.'}

def _max_drawdown(points):
    peak=points[0]['equity'] if points else 100.0; worst=0.0
    for p in points:
        peak=max(peak,p['equity'])
        if peak: worst=min(worst,(p['equity']/peak-1)*100)
    return abs(worst)
