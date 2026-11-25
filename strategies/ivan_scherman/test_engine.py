import math
from strategies.ivan_scherman.engine import run_backtest, sma, atr

def test_sma_and_atr_shapes():
    assert sma([1,2,3,4],3)==[None,None,2.0,3.0]
    values=atr([2,3,4],[0,1,2],[1,2,3],2)
    assert len(values)==3 and values[0] is None and values[1] is not None

def test_three_decreasing_closes_entry_and_close_tp():
    closes=[100,102,104,106,108,110,109.8,109.6,109.4,111,112]
    opens=[x-.2 for x in closes]
    highs=[x+.5 for x in closes]; lows=[x-.5 for x in closes]
    times=[i*86400000 for i in range(len(closes))]
    r=run_backtest(closes,highs,lows,opens,times,sma_long=5,sma_tp=2,bearish_bars=3,
        require_rising=True,require_above=True,commission_pct=0)
    assert [e["type"] for e in r["events"]]==["ENTRY","EXIT"]
    assert r["events"][0]["time"]==times[8]
    assert r["events"][1]["reason"]=="TP"
    assert r["trades"][0]["pnl_pct"]>0

def test_percent_stop_has_priority_over_take_profit():
    closes=[100,102,104,106,108,110,109.8,109.6,109.4,111]
    opens=[x-.2 for x in closes]
    highs=[x+.5 for x in closes]; lows=[x-.5 for x in closes]
    # Force a wide-range bar after the entry to touch both the stop and TP.
    highs[9]=120; lows[9]=90
    times=[i*86400000 for i in range(len(closes))]
    r=run_backtest(closes,highs,lows,opens,times,sma_long=5,sma_tp=2,bearish_bars=3,
        stop_type="percent",stop_pct=5,commission_pct=0)
    assert r["events"][0]["type"]=="ENTRY"
    assert r["events"][1]["reason"]=="STOP"
