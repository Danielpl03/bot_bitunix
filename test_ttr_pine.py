import math
from ttr_pine import sar, cross_signal, _is_high, _is_low

def test_tac_sar_initializes_on_bar_one_and_has_lagging_series():
    h=[11,12,13,12,11,10,9]
    l=[9,10,11,10,9,8,7]
    c=[10,11,12,11,10,9,8]
    result,lag,fast=sar(h,l,c)
    assert math.isnan(result[0]) and math.isnan(lag[0])
    assert all(math.isfinite(x) for x in result[1:])
    assert all(math.isfinite(x) for x in lag[1:])
    assert all(math.isfinite(x) for x in fast[1:])

def test_lagging_cross_requires_prior_lagging_side_and_current_cross():
    lag=[float('nan'),10,10,10]
    highs=[9,9,11,11]
    lows=[8,8,9,9]
    assert cross_signal(highs,lows,lag,2)==1

def test_bw_fractal_shape_is_fixed_five_bar():
    assert _is_high([1,2,4,3,2],4)
    assert _is_low([5,4,2,3,4],4)


def test_bw_fractal_is_confirmed_two_bars_after_pivot_and_breakout_one_bar_later():
    from ttr_pine import fractal_snapshots
    # High pivots at index 2 and 6; second is a higher high and confirms at 8.
    highs=[1,2,5,5,2, 5,6,6,4,3, 7,4]
    lows =[0,1,2,1,0, 0,1,2,1,0, 1,0]
    opens=[x-0.2 for x in highs]
    closes=[x+0.1 for x in highs]
    times=list(range(len(highs)))
    hh,ll=fractal_snapshots(highs,lows,opens,closes,times,hh_lookback=2,ll_lookback=2)
    # The pivot at 6 is only confirmed on bar 8 (BW's five-bar pattern).
    assert any(f.index==6 for f in hh[8])
    assert all(f.index!=6 for f in hh[7])
