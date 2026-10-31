"""Faithful Python port of TTR Pine v6 and tac/ToolsPos library logic."""
from __future__ import annotations
from dataclasses import dataclass
import math

@dataclass
class Fractal:
    price: float
    when: int
    index: int
    mark_when: int
    mark_index: int
    bo_price: float | None = None
    bo_time: int | None = None
    bo_index: int | None = None

def sar(highs, lows, closes, start=.02, inc=.02, maximum=.025):
    """Tac.sar(): returns (result, lagging, fast), not TradingView ta.sar()."""
    n=len(highs); result=[math.nan]*n; lag=[math.nan]*n; fast=[math.nan]*n
    value=af=extreme=None; below=False
    for i in range(n):
        first=False
        if i==1:
            if closes[i]>closes[i-1]:
                below=True; extreme=float(highs[i]); value=float(lows[i-1])
            else:
                below=False; extreme=float(lows[i]); value=float(highs[i-1])
            first=True; af=float(start)
        if value is None: continue
        value += af*(extreme-value)
        stage0=value
        if below:
            if value>lows[i]:
                first=True; below=False; value=max(float(highs[i]),extreme)
                extreme=float(lows[i]); af=float(start)
        else:
            if value<highs[i]:
                first=True; below=True; value=min(float(lows[i]),extreme)
                extreme=float(highs[i]); af=float(start)
        if not first:
            if below and highs[i]>extreme:
                extreme=float(highs[i]); af=min(af+inc,maximum)
            elif not below and lows[i]<extreme:
                extreme=float(lows[i]); af=min(af+inc,maximum)
        if below:
            value=min(value,float(lows[i-1]))
            if i>1: value=min(value,float(lows[i-2]))
        else:
            value=max(value,float(highs[i-1]))
            if i>1: value=max(value,float(highs[i-2]))
        result[i]=value
        lag[i]=stage0 if i>1 and first else value
        fast[i]=lag[i]+af*(extreme-lag[i]) if math.isfinite(lag[i]) else math.nan
    return result,lag,fast

def _is_high(s,i):
    return i>=4 and s[i-4]<s[i-2] and s[i-3]<=s[i-2] and s[i-2]>=s[i-1] and s[i-2]>s[i]
def _is_low(s,i):
    return i>=4 and s[i-4]>s[i-2] and s[i-3]>=s[i-2] and s[i-2]<=s[i-1] and s[i-2]<s[i]

def fractal_snapshots(highs,lows,opens,closes,times,*,src_high=None,src_low=None,hh_lookback=2,ll_lookback=2,hh_start=None,ll_start=None):
    """Build latest HH/LL candidate arrays per bar, including Pine's breakout delay."""
    sh=list(map(float,src_high if src_high is not None else highs))
    sl=list(map(float,src_low if src_low is not None else lows))
    hh=[]; ll=[]; last_high=None; last_low=None
    hstart=int(times[0] if hh_start is None and times else hh_start or 0)
    lstart=int(times[0] if ll_start is None and times else ll_start or 0)
    hs=[]; ls=[]
    for i in range(len(times)):
        is_h=_is_high(sh,i); is_l=_is_low(sl,i); mark_h=mark_l=0
        high_value=sh[i-2] if is_h else None
        low_value=sl[i-2] if is_l else None
        if is_h:
            mark_h=2 if last_high is not None and high_value>last_high else (1 if last_high is not None and high_value<last_high else 0)
            last_high=high_value
        if is_l:
            mark_l=-2 if last_low is not None and low_value<last_low else (-1 if last_low is not None and low_value>last_low else 0)
            last_low=low_value
        # Tac creates one UDT object per confirmed candle. If high and low
        # fractals confirm together, its price field prefers the high source
        # and the same object reference is pushed to both collections.
        if is_h or is_l:
            f=Fractal(float(high_value if is_h else low_value),int(times[i-2]),i-2,int(times[i]),i)
            if mark_h==2: hh.append(f)
            if mark_l==-2: ll.append(f)
        if i>0:
            if hh_lookback>0 and hh:
                # Pine scans newest to oldest; all eligible unbroken fractals can be
                # broken by the same previous candle.
                for f in list(reversed(hh)):
                    if f.bo_price is None and f.when>=hstart and closes[i-1]>opens[i-1] and closes[i-1]>f.price:
                        f.bo_price=float(closes[i-1]); f.bo_time=int(times[i-1]); f.bo_index=i-1
                        hh.insert(hh.index(f),f)  # exact reference insertion used by tac
            if ll_lookback>0 and ll:
                for f in list(reversed(ll)):
                    if f.bo_price is None and f.when>=lstart and closes[i-1]<opens[i-1] and closes[i-1]<f.price:
                        f.bo_price=float(closes[i-1]); f.bo_time=int(times[i-1]); f.bo_index=i-1
                        ll.insert(ll.index(f),f)
        hs.append(list(hh)); ls.append(list(ll))
    return hs,ls

def cross_signal(highs,lows,lagging,i):
    """Pine psarLagCross using ta.crossover(high, lagging)/ta.crossunder(low, lagging)."""
    if i<1 or not (math.isfinite(lagging[i]) and math.isfinite(lagging[i-1])): return 0
    cross_high=highs[i]>lagging[i] and highs[i-1]<=lagging[i-1]
    cross_low=lows[i]<lagging[i] and lows[i-1]>=lagging[i-1]
    if lagging[i-1]>highs[i-1] and cross_high: return 1
    if lagging[i-1]<lows[i-1] and cross_low: return -1
    return 0

def ttr_bar_action(i, *, side, entry_price, entry_time, closes, opens, lagging, times,
                   hh_by_bar,ll_by_bar,frac_lb_high=2,frac_lb_low=2,
                   sig_re_min_proximity=0.0,sig_re_buy_no_tp=False,sig_re_sell_no_tp=False,
                   marked_tp=None,marked_re=None,last_tp=None,last_re=None):
    """Return (TP|RE, fractal) matching the indicator's current-bar decision."""
    if i<1: return None,None
    marked_tp=marked_tp or set(); marked_re=marked_re or set()
    proximity=(round(abs(closes[i]-lagging[i])/lagging[i]*100,2)
               if math.isfinite(lagging[i]) and lagging[i]!=0 else math.nan)
    up=closes[i-1]>opens[i-1] and closes[i]<=opens[i]
    down=closes[i-1]<opens[i-1] and closes[i]>=opens[i]
    if up:
        for f in reversed(hh_by_bar[i][-frac_lb_high:]):
            if f.when<entry_time or f.bo_price is None or f.bo_time<entry_time: continue
            if side=='buy' and closes[i]>entry_price and f.when not in marked_tp and (last_tp is None or closes[i]>last_tp):
                return 'TP',f
            if side=='sell' and proximity>sig_re_min_proximity and f.when not in marked_re and (last_tp is not None or sig_re_sell_no_tp) and (last_re is None or closes[i]>last_re):
                return 'RE',f
    if down:
        for f in reversed(ll_by_bar[i][-frac_lb_low:]):
            if f.when<entry_time or f.bo_price is None or f.bo_time<entry_time: continue
            if side=='sell' and closes[i]<entry_price and f.when not in marked_tp and (last_tp is None or closes[i]<last_tp):
                return 'TP',f
            if side=='buy' and proximity>sig_re_min_proximity and f.when not in marked_re and (last_tp is not None or sig_re_buy_no_tp) and (last_re is None or closes[i]<last_re):
                return 'RE',f
    return None,None
