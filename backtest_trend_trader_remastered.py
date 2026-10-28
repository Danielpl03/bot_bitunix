"""Python backtest port of visible TTR rules; external Pine libraries remain approximate."""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Optional
import math

@dataclass
class Trade:
    side: str
    entry_index: int
    entry_time: int
    entry_price: float
    avg_entry_price: Optional[float] = None
    exit_index: Optional[int] = None
    exit_time: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl_pct: Optional[float] = None
    bars_held: Optional[int] = None
    partial_exits: list = field(default_factory=list)
    remaining_pct: float = 100.0
    stop_price: Optional[float] = None


def _psar(highs, lows, start=.02, inc=.02, maximum=.025):
    n=len(highs); sar=[math.nan]*n
    if n < 3: return sar
    bull=(highs[1]+lows[1]) >= (highs[0]+lows[0]); af=float(start); ep=highs[0] if bull else lows[0]; sar[0]=lows[0] if bull else highs[0]
    for i in range(1,n):
        prev=sar[i-1]; cur=prev+af*(ep-prev)
        if bull:
            cur=min(cur,lows[i-1],lows[i-2] if i>1 else lows[i-1])
            if lows[i]<cur: bull=False; cur=ep; ep=lows[i]; af=start
            elif highs[i]>ep: ep=highs[i]; af=min(maximum,af+inc)
        else:
            cur=max(cur,highs[i-1],highs[i-2] if i>1 else highs[i-1])
            if highs[i]>cur: bull=True; cur=ep; ep=highs[i]; af=start
            elif lows[i]<ep: ep=lows[i]; af=min(maximum,af+inc)
        sar[i]=cur
    return sar


def _fractals(highs,lows,lb_high=2,lb_low=2):
    n=len(highs); hh=[False]*n; ll=[False]*n
    for i in range(lb_high,n-lb_high):
        if all(highs[i]>highs[j] for j in range(i-lb_high,i)) and all(highs[i]>=highs[j] for j in range(i+1,i+lb_high+1)): hh[i+lb_high]=True
    for i in range(lb_low,n-lb_low):
        if all(lows[i]<lows[j] for j in range(i-lb_low,i)) and all(lows[i]<=lows[j] for j in range(i+1,i+lb_low+1)): ll[i+lb_low]=True
    return hh,ll


def run_backtest(closes, highs, lows, opens, times, *, psar_start=.02, psar_increment=.02, psar_max=.025,
                 frac_lb_high=2, frac_lb_low=2, sig_re_min_proximity=0.0, sig_re_buy_no_tp=False, sig_re_sell_no_tp=False,
                 pos_dir='Buy & Sell', enter_on_reentry=True, tp_percentages=(25,25,25,25),
                 show_entry_buy=True, show_tp_buy=True, show_re_buy=True, show_exit_buy=False,
                 show_entry_sell=True, show_tp_sell=True, show_re_sell=True, show_exit_sell=False,
                 frac_src_high=None, frac_src_low=None, initial_capital=100.0, fee_pct=0.0, slippage_pct=0.0):
    n=len(closes)
    if not (len(highs)==len(lows)==len(opens)==len(times)==n): raise ValueError('OHLCV y tiempos deben tener la misma longitud.')
    if n<10: raise ValueError('Se necesitan al menos 10 velas para TTR.')
    if pos_dir not in ('Buy & Sell','Buy Only','Sell Only'): raise ValueError('pos_dir debe ser Buy & Sell, Buy Only o Sell Only.')
    if min(psar_start,psar_increment,psar_max)<=0 or frac_lb_high<1 or frac_lb_low<1: raise ValueError('Parámetros PSAR/fractales inválidos.')
    tps=[float(x) for x in tp_percentages]
    if not tps or any(not math.isfinite(x) or x<=0 or x>100 for x in tps): raise ValueError('Cada porcentaje TP debe estar entre 0 y 100, sin incluir 0.')
    if sum(tps)>100.000001: raise ValueError('La suma de porcentajes TP no puede superar 100%.')
    if fee_pct<0 or slippage_pct<0: raise ValueError('Comisión y deslizamiento no pueden ser negativos.')
    fh=list(map(float,frac_src_high if frac_src_high is not None else highs)); fl=list(map(float,frac_src_low if frac_src_low is not None else lows))
    if len(fh)!=n or len(fl)!=n: raise ValueError('Las fuentes de fractales deben tener la misma longitud que las velas.')
    sar=_psar(highs,lows,psar_start,psar_increment,psar_max); lag=[math.nan]+sar[:-1]; frac_h,frac_l=_fractals(fh,fl,frac_lb_high,frac_lb_low)
    allow_buy=pos_dir in ('Buy & Sell','Buy Only'); allow_sell=pos_dir in ('Buy & Sell','Sell Only')
    events=[]; trades=[]; current=None; last_tp=None; last_re=None; last_tp_pct=0.0; marks_tp=set(); marks_re=set(); eq= float(initial_capital); curve=[{'time':int(times[0]),'equity':eq}]; realized_pnl=0.0

    def close_piece(trade, pct, price, i, reason):
        nonlocal realized_pnl, eq
        pct=min(float(pct),trade.remaining_pct)
        if pct<=1e-9:return
        direction=1 if trade.side=='buy' else -1
        # Use the weighted average cost of the currently open exposure. Keep entry_price
        # immutable as the original signal price for reporting; re-entries only change avg_entry_price.
        cost = trade.avg_entry_price if trade.avg_entry_price is not None else trade.entry_price
        raw=(price/cost-1)*100*direction
        net=raw-fee_pct*2-slippage_pct*2
        weighted=net*pct/100.0
        realized_pnl += weighted
        eq *= max(0.0,1.0+weighted/100.0)
        trade.remaining_pct=max(0.0,trade.remaining_pct-pct)
        trade.partial_exits.append({'index':i,'time':int(times[i]),'price':float(price),'percent':pct,'reason':reason,'pnl_pct_position':weighted})
        events.append({'type':reason,'side':trade.side,'index':i,'time':int(times[i]),'price':float(price),'percent_closed':pct,'remaining_pct':trade.remaining_pct})
        curve.append({'time':int(times[i]),'equity':eq})

    def close_all(trade, price, i, reason):
        if trade.remaining_pct>1e-8: close_piece(trade,trade.remaining_pct,price,i,reason)
        trade.exit_index=i; trade.exit_time=int(times[i]); trade.exit_price=float(price); trade.exit_reason=reason; trade.bars_held=i-trade.entry_index
        trade.pnl_pct=sum(x['pnl_pct_position'] for x in trade.partial_exits)
        trades.append(trade)

    for i in range(2,n):
        if not math.isfinite(lag[i]) or not math.isfinite(lag[i-1]): continue
        # Stop is the lagging PSAR value established at the prior close; use OHLC to model a stop touch.
        if current and current.remaining_pct>0 and current.stop_price is not None:
            hit=(current.side=='buy' and lows[i]<=current.stop_price) or (current.side=='sell' and highs[i]>=current.stop_price)
            if hit:
                stop_fill=min(opens[i],current.stop_price) if current.side=='buy' else max(opens[i],current.stop_price)
                close_all(current,stop_fill,i,'SL_PSAR'); events.append({'type':'SL','side':current.side,'index':i,'time':int(times[i]),'price':float(stop_fill)})
                current=None; last_tp=last_re=None; last_tp_pct=0.0; marks_tp.clear(); marks_re.clear()
        cross_high=highs[i]>lag[i] and highs[i-1]<=lag[i-1]; cross_low=lows[i]<lag[i] and lows[i-1]>=lag[i-1]
        lag_cross=1 if lag[i-1]>highs[i-1] and cross_high else (-1 if lag[i-1]<lows[i-1] and cross_low else 0)
        enter='buy' if allow_buy and lag_cross==1 else ('sell' if allow_sell and lag_cross==-1 else None)
        if current and ((current.side=='buy' and lag_cross==-1) or (current.side=='sell' and lag_cross==1)):
            old_side=current.side; close_all(current,float(closes[i]),i,'REVERSAL'); events.append({'type':'EXIT','side':old_side,'index':i,'time':int(times[i]),'price':float(closes[i])})
            current=None; last_tp=last_re=None; last_tp_pct=0.0; marks_tp.clear(); marks_re.clear()
        if enter:
            current=Trade(enter,i,int(times[i]),float(closes[i]),avg_entry_price=float(closes[i]),stop_price=float(lag[i]))
            last_tp=last_re=None; last_tp_pct=0.0; marks_tp.clear(); marks_re.clear()
            events.append({'type':'ENTRY','side':enter,'index':i,'time':int(times[i]),'price':float(closes[i]),'psar_lagging':float(lag[i]),'stop_price':float(lag[i]),'entry_kind':'MAIN'})
        if not current: continue
        side=current.side; proximity=abs(closes[i]-lag[i])/abs(lag[i])*100 if lag[i] else 0
        up_bo=closes[i-1]>opens[i-1] and closes[i]<=opens[i]; down_bo=closes[i-1]<opens[i-1] and closes[i]>=opens[i]
        action=None; candidate=None
        if up_bo:
            candidates=[j for j in range(max(0,i-frac_lb_high*3),i) if frac_h[j] and j>=current.entry_index and closes[i]>fh[j] and j not in marks_tp and j not in marks_re]
            if side=='buy' and closes[i]>current.entry_price and candidates and (last_tp is None or closes[i]>last_tp): action='TP'; candidate=candidates[-1]
            elif side=='sell' and candidates and proximity>sig_re_min_proximity and (last_tp is not None or sig_re_sell_no_tp) and (last_re is None or closes[i]>last_re): action='RE'; candidate=candidates[-1]
        if down_bo and action is None:
            candidates=[j for j in range(max(0,i-frac_lb_low*3),i) if frac_l[j] and j>=current.entry_index and closes[i]<fl[j] and j not in marks_tp and j not in marks_re]
            if side=='sell' and closes[i]<current.entry_price and candidates and (last_tp is None or closes[i]<last_tp): action='TP'; candidate=candidates[-1]
            elif side=='buy' and candidates and proximity>sig_re_min_proximity and (last_tp is not None or sig_re_buy_no_tp) and (last_re is None or closes[i]<last_re): action='RE'; candidate=candidates[-1]
        if action and candidate is not None:
            (marks_tp if action=='TP' else marks_re).add(candidate)
            if action=='TP':
                last_tp=float(closes[i]); slot=len([x for x in current.partial_exits if x['reason']=='TP'])
                if slot<len(tps):
                    last_tp_pct=tps[slot]
                    close_piece(current,tps[slot],float(closes[i]),i,'TP')
                else: events.append({'type':'TP_SIGNAL_NO_CLOSE','side':side,'index':i,'time':int(times[i]),'price':float(closes[i]),'reason':'No queda porcentaje TP configurado'})
            else:
                last_re=float(closes[i]); events.append({'type':'RE','side':side,'index':i,'time':int(times[i]),'price':float(closes[i]),'entry_enabled':bool(enter_on_reentry)})
                if enter_on_reentry:
                    # Re-entry restores the last TP tranche (or the first configured TP size).
                    add_pct=min(last_tp_pct or tps[0],100.0-current.remaining_pct)
                    if add_pct>0:
                        old_qty=current.remaining_pct; new_qty=old_qty+add_pct
                        old_avg = current.avg_entry_price if current.avg_entry_price is not None else current.entry_price
                        current.avg_entry_price=((old_avg*old_qty)+(float(closes[i])*add_pct))/new_qty if new_qty else old_avg
                        current.remaining_pct=new_qty
                        current.partial_exits.append({'index':i,'time':int(times[i]),'price':float(closes[i]),'percent':add_pct,'reason':'RE_ADD','pnl_pct_position':0.0})
                    events.append({'type':'RE_ENTRY','side':side,'index':i,'time':int(times[i]),'price':float(closes[i]),'percent_added':add_pct,'remaining_pct':current.remaining_pct})
        # Trailing stop: update on every candle close for the next candle, never retroactively.
        current.stop_price=float(lag[i])
        if current.remaining_pct<=1e-8:
            current.exit_index=i; current.exit_time=int(times[i]); current.exit_price=float(closes[i]); current.exit_reason='TP_FULL'; current.bars_held=i-current.entry_index
            current.pnl_pct=sum(x['pnl_pct_position'] for x in current.partial_exits); trades.append(current); current=None; last_tp=last_re=None; marks_tp.clear(); marks_re.clear()
    if current:
        close_all(current,float(closes[-1]),n-1,'END_OF_DATA')
    closed=[t for t in trades if t.pnl_pct is not None]; wins=[t for t in closed if t.pnl_pct>0]; losses=[t for t in closed if t.pnl_pct<0]
    gross_win=sum(t.pnl_pct for t in wins); gross_loss=abs(sum(t.pnl_pct for t in losses))
    summary={'trades':len(closed),'wins':len(wins),'losses':len(losses),'win_rate_pct':len(wins)/len(closed)*100 if closed else 0.0,
      'net_pnl_pct_compounded':(eq/initial_capital-1)*100 if initial_capital else 0.0,'profit_factor':gross_win/gross_loss if gross_loss else (None if not gross_win else None),
      'avg_pnl_pct':sum(t.pnl_pct for t in closed)/len(closed) if closed else 0.0,'max_drawdown_pct':_max_drawdown(curve)}
    visible=[]
    for e in events:
        typ=e['type']; side=e['side']
        keep=(typ in ('ENTRY','RE_ENTRY') and ((side=='buy' and show_entry_buy) or (side=='sell' and show_entry_sell))) or (typ in ('EXIT','SL') and ((side=='buy' and show_exit_buy) or (side=='sell' and show_exit_sell))) or (typ=='TP' and ((side=='buy' and show_tp_buy) or (side=='sell' and show_tp_sell))) or (typ=='RE' and ((side=='buy' and show_re_buy) or (side=='sell' and show_re_sell)))
        if keep: visible.append(e)
    return {'strategy':'trend_trader_remastered','summary':summary,'trades':[asdict(t) for t in trades],'events':visible,'equity':curve,'indicators':{'psar':sar,'psar_lagging':lag},
      'parameters':{'pos_dir':pos_dir,'psar_start':psar_start,'psar_increment':psar_increment,'psar_max':psar_max,'frac_lb_high':frac_lb_high,'frac_lb_low':frac_lb_low,'sig_re_min_proximity':sig_re_min_proximity,'sig_re_buy_no_tp':sig_re_buy_no_tp,'sig_re_sell_no_tp':sig_re_sell_no_tp,'enter_on_reentry':enter_on_reentry,'tp_percentages':tps,'stop_mode':'trailing_lagging_psar','fee_pct':fee_pct,'slippage_pct':slippage_pct},
      'note':'PSAR trailing stop se actualiza al cierre y se aplica desde la vela siguiente. Las señales de TP/fractales siguen siendo una aproximación de las bibliotecas Pine externas. La reentrada restaura el último tramo TP cerrado (o el primer porcentaje TP) hasta un máximo del 100% de tamaño abierto.'}

def _max_drawdown(points):
    peak=points[0]['equity'] if points else 100.0; worst=0.0
    for p in points:
        peak=max(peak,p['equity'])
        if peak: worst=min(worst,(p['equity']/peak-1)*100)
    return abs(worst)
