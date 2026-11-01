import json
from ttr_live import TTRLiveStore


def test_global_performance_and_symbol_breakdown(tmp_path):
    store = TTRLiveStore(path=str(tmp_path / 'ttr.sqlite3'), backend='sqlite')
    store.subscribe('BTCUSDT', 1, {})
    store.subscribe('ETHUSDT', 1, {})
    btc = store.open_trade('BTCUSDT', 'buy', 1000, 100, None)
    store.update_trade(btc, status='CLOSED', exit_time=2000, exit_price=110,
                       exit_reason='REVERSAL_15M', realized_pnl_pct=10, remaining_pct=0)
    eth = store.open_trade('ETHUSDT', 'sell', 1000, 200, None)
    store.save_state('ETHUSDT', {'market': {'last_price': 190}})

    report = store.performance()
    assert report['overall']['total'] == 2
    assert report['overall']['closed'] == 1
    assert report['overall']['open'] == 1
    assert report['overall']['realized_pnl_pct'] == 10
    assert abs(report['overall']['floating_pnl_pct'] - 5) < 1e-9
    assert report['by_symbol']['BTCUSDT']['wins'] == 1
    assert report['by_symbol']['ETHUSDT']['open'] == 1
    assert len(store.all_history(10)) == 2


def test_health_incidents_are_persisted(tmp_path):
    store = TTRLiveStore(path=str(tmp_path / 'ttr.sqlite3'), backend='sqlite')
    store.set_health('telegram', 'TELEGRAM', 'RATE_LIMITED', 'RetryAfter 60s')
    row = store.health()[0]
    assert row['component'] == 'telegram'
    assert row['status'] == 'RATE_LIMITED'
    assert row['message'] == 'RetryAfter 60s'
    store.set_health('telegram', 'TELEGRAM', 'OK')
    row = store.health()[0]
    assert row['status'] == 'OK'
    assert row['message'] is None
