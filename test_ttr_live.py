import json
from ttr_live import TTRLiveStore

def test_live_store_persists_subscription_trade_and_events(tmp_path):
    store=TTRLiveStore(str(tmp_path/'live.sqlite'))
    store.subscribe('BTCUSDT',123,{'interval':'15m','tp_percentages':[40,30,30]})
    cfg=store.get_config('BTCUSDT')
    assert cfg and cfg['enabled']==1
    state={'position':{'side':'buy','trade_id':1,'stop':100}}
    store.save_state('BTCUSDT',state,900000)
    assert json.loads(store.get_config('BTCUSDT')['state'])==state
    trade_id=store.open_trade('BTCUSDT','buy',900000,105,100)
    store.update_trade(trade_id,stop_price=101)
    store.event('BTCUSDT','ENTRY','buy',105,900000,{'signal':'test'})
    assert store.history('BTCUSDT')[0]['stop_price']==101
    assert store.events('BTCUSDT')[0]['event_type']=='ENTRY'
    store.unsubscribe('BTCUSDT')
    assert store.configs()==[]


def test_poll_interval_is_configurable_and_bounded():
    from ttr_live import poll_seconds
    assert poll_seconds({'poll_seconds': 3}) == 3
    assert poll_seconds({'poll_seconds': 1}) == 2
    assert poll_seconds({'poll_seconds': 90}) == 30
    assert poll_seconds({}) >= 2

def test_status_contains_indicator_and_live_quote_state(tmp_path):
    store=TTRLiveStore(str(tmp_path/'live.sqlite'))
    store.subscribe('ETHUSDT',456,{'interval':'15m','poll_seconds':4,'enter_on_reentry':True})
    state={'position':{'side':'sell','entry_price':2500,'stop':2510,'remaining_pct':60},
           'market':{'last_price':2490,'quote_time':123456,'poll_seconds':4},
           'indicator':{'timeframe':'15m','psar':2512,'psar_lagging':2515,'candle_time':120000}}
    store.save_state('ETHUSDT',state,120000)
    row=store.status()[0]
    saved=json.loads(row['state'])
    assert saved['market']['last_price']==2490
    assert saved['indicator']['psar']==2512
    assert saved['position']['stop']==2510

def test_sqlite_store_creates_missing_parent_directory(tmp_path):
    db_path = tmp_path / 'nested' / 'state' / 'live.sqlite'
    store = TTRLiveStore(str(db_path))
    store.subscribe('BTCUSDT', 1, {})
    assert db_path.exists()
    assert store.get_config('BTCUSDT')['symbol'] == 'BTCUSDT'
