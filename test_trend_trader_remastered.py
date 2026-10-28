import math
from backtest_trend_trader_remastered import run_backtest, _psar

def sample(n=200):
    closes=[100+math.sin(i/5)*4+i*.01 for i in range(n)]
    opens=[c-math.sin(i)*.25 for i,c in enumerate(closes)]
    highs=[max(c,o)+.6 for c,o in zip(closes,opens)]
    lows=[min(c,o)-.6 for c,o in zip(closes,opens)]
    times=[i*3600000 for i in range(n)]
    return closes,highs,lows,opens,times

def test_runs_and_returns_core_shapes():
    r=run_backtest(*sample())
    assert r['strategy']=='trend_trader_remastered'
    assert 'trades' in r and 'events' in r and 'equity' in r
    assert r['summary']['trades'] == len(r['trades'])

def test_direction_validation():
    args=sample()
    try: run_backtest(*args,pos_dir='none')
    except ValueError: return
    assert False, 'Expected ValueError'

def test_optional_inputs_accepted():
    r=run_backtest(*sample(),pos_dir='Buy Only',sig_re_buy_no_tp=True,frac_lb_high=3,frac_lb_low=3)
    assert r['parameters']['pos_dir']=='Buy Only'

def test_tp_percentages_validation():
    args=sample()
    try:
        run_backtest(*args,tp_percentages=[60,50])
    except ValueError as exc:
        assert '100%' in str(exc)
    else:
        assert False, 'TP allocation over 100% should fail'

def test_reentry_and_trailing_stop_parameters_are_reported():
    r=run_backtest(*sample(),enter_on_reentry=False,tp_percentages=[40,30,30])
    assert r['parameters']['enter_on_reentry'] is False
    assert r['parameters']['tp_percentages']==[40.0,30.0,30.0]
    assert r['parameters']['stop_mode']=='trailing_lagging_psar'
