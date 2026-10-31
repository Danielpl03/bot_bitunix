"""TTR Pine v6 backtest port; execution and accounting assumptions are isolated here."""
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



def _psar(highs,lows,start=.02,inc=.02,maximum=.025,closes=None):
    from ttr_pine import sar
    if closes is None:
        raise ValueError('Tac.sar requiere la serie close; no se puede reconstruir fielmente solo con high/low.')
    return sar(highs,lows,closes,start,inc,maximum)[0]

def _fractals(highs,lows,lb_high=2,lb_low=2):
    # Compatibility helper: BW fractals use a fixed 5-bar shape; lb controls
    # how many confirmed HH/LL structures the signal engine scans.
    from ttr_pine import _is_high, _is_low
    n=len(highs); hh=[False]*n; ll=[False]*n
    for i in range(n):
        if _is_high(highs,i): hh[i]=True
        if _is_low(lows,i): ll[i]=True
    return hh,ll


def run_backtest(closes, highs, lows, opens, times, *, psar_start=.02, psar_increment=.02, psar_max=.025,
                 frac_lb_high=2, frac_lb_low=2, sig_re_min_proximity=0.0, sig_re_buy_no_tp=False, sig_re_sell_no_tp=False,
                 pos_dir='Buy & Sell', enter_on_reentry=False, tp_percentages=(25,25,25,25),
                 show_entry_buy=True, show_tp_buy=True, show_re_buy=True, show_exit_buy=False,
                 show_entry_sell=True, show_tp_sell=True, show_re_sell=True, show_exit_sell=False,
                 frac_src_high=None, frac_src_low=None, initial_capital=100.0, fee_pct=0.0, slippage_pct=0.0):
    from ttr_pine import sar as pine_sar, fractal_snapshots, cross_signal, ttr_bar_action
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

    sar_values, lag, fast = pine_sar(highs,lows,closes,psar_start,psar_increment,psar_max)
    hh_by_bar,ll_by_bar=fractal_snapshots(highs,lows,opens,closes,times,src_high=fh,src_low=fl,
                                           hh_lookback=frac_lb_high,ll_lookback=frac_lb_low)
    allow_buy=pos_dir in ('Buy & Sell','Buy Only'); allow_sell=pos_dir in ('Buy & Sell','Sell Only')
    events=[]; trades=[]; current=None; accounting_closed=False; marks_tp=set(); marks_re=set(); last_tp=None; last_re=None
    eq=float(initial_capital); curve=[{'time':int(times[0]),'equity':eq}]; realized_pnl=0.0
    tp_count=0; last_tp_pct=0.0

    def close_piece(trade,pct,price,i,reason):
        nonlocal eq,realized_pnl
        pct=min(float(pct),trade.remaining_pct)
        if pct<=1e-9:return
        direction=1 if trade.side=='buy' else -1
        cost=trade.avg_entry_price if trade.avg_entry_price is not None else trade.entry_price
        raw=(price/cost-1)*100*direction
        net=raw-fee_pct*2-slippage_pct*2
        weighted=net*pct/100.0
        realized_pnl+=weighted; eq*=max(0.0,1.0+weighted/100.0)
        trade.remaining_pct=max(0.0,trade.remaining_pct-pct)
        trade.partial_exits.append({'index':i,'time':int(times[i]),'price':float(price),'percent':pct,'reason':reason,'pnl_pct_position':weighted})
        events.append({'type':reason,'side':trade.side,'index':i,'time':int(times[i]),'price':float(price),'percent_closed':pct,'remaining_pct':trade.remaining_pct})
        curve.append({'time':int(times[i]),'equity':eq})

    def finish_trade(trade,price,i,reason):
        if trade.remaining_pct>1e-8: close_piece(trade,trade.remaining_pct,price,i,reason)
        trade.exit_index=i; trade.exit_time=int(times[i]); trade.exit_price=float(price)
        trade.exit_reason=reason; trade.bars_held=i-trade.entry_index
        trade.pnl_pct=sum(x['pnl_pct_position'] for x in trade.partial_exits)
        trades.append(trade)

    for i in range(1,n):
        cross=cross_signal(highs,lows,lag,i)
        desired='buy' if cross==1 and allow_buy else ('sell' if cross==-1 and allow_sell else None)
        # Pine evaluates posExit before posEnter. An opposite cross exits and can
        # enter the new direction on this same candle.
        if current and ((current.side=='buy' and cross==-1) or (current.side=='sell' and cross==1)):
            old=current.side
            if not accounting_closed:
                finish_trade(current,float(closes[i]),i,'REVERSAL')
            events.append({'type':'EXIT','side':old,'index':i,'time':int(times[i]),'price':float(closes[i])})
            current=None; accounting_closed=False; marks_tp.clear(); marks_re.clear(); last_tp=last_re=None; tp_count=0; last_tp_pct=0.0
        elif current and desired==current.side:
            old=current.side
            if not accounting_closed:
                finish_trade(current,float(closes[i]),i,'PINE_ENTRY_RESET')
            events.append({'type':'EXIT','side':old,'index':i,'time':int(times[i]),'price':float(closes[i]),'reason':'PINE_ENTRY_RESET'})
            current=None; accounting_closed=False; marks_tp.clear(); marks_re.clear(); last_tp=last_re=None; tp_count=0; last_tp_pct=0.0
        if desired:
            entry=float(lag[i])
            if not math.isfinite(entry) or entry<=0: continue
            current=Trade(desired,i,int(times[i]),entry,avg_entry_price=entry); accounting_closed=False
            marks_tp.clear(); marks_re.clear(); last_tp=last_re=None; tp_count=0; last_tp_pct=0.0
            events.append({'type':'ENTRY','side':desired,'index':i,'time':int(times[i]),'price':entry,
                           'psar_lagging':entry,'entry_kind':'MAIN'})
            continue
        if current is None: continue
        action,fractal=ttr_bar_action(i,side=current.side,entry_price=current.entry_price,
            entry_time=current.entry_time,closes=closes,opens=opens,lagging=lag,times=times,
            hh_by_bar=hh_by_bar,ll_by_bar=ll_by_bar,frac_lb_high=frac_lb_high,frac_lb_low=frac_lb_low,
            sig_re_min_proximity=sig_re_min_proximity,sig_re_buy_no_tp=sig_re_buy_no_tp,
            sig_re_sell_no_tp=sig_re_sell_no_tp,marked_tp=marks_tp,marked_re=marks_re,
            last_tp=last_tp,last_re=last_re)
        if not action: continue
        price=float(closes[i])
        if action=='TP':
            marks_tp.add(fractal.when); last_tp=price
            pct=tps[tp_count] if tp_count<len(tps) else 0.0
            tp_count+=1; last_tp_pct=pct
            if pct>0 and current.remaining_pct>1e-8:
                close_piece(current,pct,price,i,'TP')
            else:
                events.append({'type':'TP_SIGNAL_NO_CLOSE','side':current.side,'index':i,'time':int(times[i]),'price':price,'fractal_index':fractal.index})
            if current.remaining_pct<=1e-8 and not accounting_closed:
                # The Python accounting exposure is fully reduced, but Pine keeps
                # PosInfo active until an opposite PSAR cross. Keep evaluating signals.
                current.exit_index=i; current.exit_time=int(times[i]); current.exit_price=price
                current.exit_reason='TP_FULL'; current.bars_held=i-current.entry_index
                current.pnl_pct=sum(x['pnl_pct_position'] for x in current.partial_exits)
                trades.append(current); accounting_closed=True
        else:
            marks_re.add(fractal.when); last_re=price
            events.append({'type':'RE','side':current.side,'index':i,'time':int(times[i]),'price':price,
                           'entry_enabled':False,'fractal_index':fractal.index})
            # Pine ToolsPos.action(RE) records a signal only; it does not add
            # exposure or change the original position price.


    if current and not accounting_closed:
        finish_trade(current,float(closes[-1]),n-1,'END_OF_DATA')
    closed=[t for t in trades if t.pnl_pct is not None]; wins=[t for t in closed if t.pnl_pct>0]; losses=[t for t in closed if t.pnl_pct<0]
    gross_win=sum(t.pnl_pct for t in wins); gross_loss=abs(sum(t.pnl_pct for t in losses))
    summary={'trades':len(closed),'wins':len(wins),'losses':len(losses),'win_rate_pct':len(wins)/len(closed)*100 if closed else 0.0,
      'net_pnl_pct_compounded':(eq/initial_capital-1)*100 if initial_capital else 0.0,
      'profit_factor':gross_win/gross_loss if gross_loss else None,
      'avg_pnl_pct':sum(t.pnl_pct for t in closed)/len(closed) if closed else 0.0,'max_drawdown_pct':_max_drawdown(curve)}
    visible=[]
    for e in events:
        typ=e['type']; side=e['side']
        keep=(typ in ('ENTRY','RE_ENTRY') and ((side=='buy' and show_entry_buy) or (side=='sell' and show_entry_sell))) or (typ=='EXIT' and ((side=='buy' and show_exit_buy) or (side=='sell' and show_exit_sell))) or (typ in ('TP','TP_SIGNAL_NO_CLOSE') and ((side=='buy' and show_tp_buy) or (side=='sell' and show_tp_sell))) or (typ=='RE' and ((side=='buy' and show_re_buy) or (side=='sell' and show_re_sell)))
        if keep: visible.append(e)
    return {'strategy':'trend_trader_remastered','summary':summary,'trades':[asdict(t) for t in trades],
      'events':visible,'equity':curve,'indicators':{'psar':sar_values,'psar_lagging':lag,'psar_fast':fast},
      'parameters':{'pos_dir':pos_dir,'psar_start':psar_start,'psar_increment':psar_increment,'psar_max':psar_max,
        'frac_lb_high':frac_lb_high,'frac_lb_low':frac_lb_low,'sig_re_min_proximity':sig_re_min_proximity,
        'sig_re_buy_no_tp':sig_re_buy_no_tp,'sig_re_sell_no_tp':sig_re_sell_no_tp,
        'enter_on_reentry':False,'tp_percentages':tps,'stop_mode':'none_pine_indicator',
        'fee_pct':fee_pct,'slippage_pct':slippage_pct},
      'note':'Port de Tac.sar y Tac.BWFractals del Pine v6. El indicador no define stop-loss ni trailing stop: la inversión de dirección ocurre por cruce opuesto del lagging PSAR. TP/RE son señales; los porcentajes TP y el cierre contable son extensiones del backtester Python. enter_on_reentry se conserva por compatibilidad y no agrega exposición.'}


def _max_drawdown(points):
    peak=points[0]['equity'] if points else 100.0
    worst=0.0
    for point in points:
        peak=max(peak,point['equity'])
        if peak:
            worst=min(worst,(point['equity']/peak-1)*100)
    return abs(worst)
