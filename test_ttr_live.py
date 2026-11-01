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


def test_resubscribe_preserves_live_state_and_last_bar(tmp_path):
    store=TTRLiveStore(str(tmp_path/'preserve.sqlite'))
    store.subscribe('BTCUSDT',123,{'tp_percentages':[25,25,25,25]})
    state={'position':{'side':'sell','trade_id':1,'remaining_pct':75,'stop':101}}
    store.save_state('BTCUSDT',state,123456)
    store.subscribe('BTCUSDT',999,{'tp_percentages':[40,30,30]})
    cfg=store.get_config('BTCUSDT')
    assert json.loads(cfg['state'])==state
    assert cfg['last_bar_time']==123456
    assert cfg['chat_id']==999


def test_open_trade_ledger_can_restore_position(tmp_path):
    store=TTRLiveStore(str(tmp_path/'restore.sqlite'))
    store.subscribe('ETHUSDT',456,{'tp_percentages':[25,25,25,25]})
    tid=store.open_trade('ETHUSDT','sell',900000,2500,2510)
    store.update_trade(tid,remaining_pct=75,realized_pnl_pct=0.12,
                       payload=json.dumps({'tp_count':1,'avg_entry':2500,'tp_marked_fractals':[8]}))
    rows=store.open_trades('ETHUSDT')
    assert len(rows)==1
    assert rows[0]['id']==tid and rows[0]['status']=='OPEN'
    assert rows[0]['remaining_pct']==75
    assert json.loads(rows[0]['payload'])['tp_count']==1


def test_ttr_live_imports_pine_bar_action():
    from ttr_live import monitor_symbol
    from ttr_pine import ttr_bar_action
    assert callable(monitor_symbol)
    assert callable(ttr_bar_action)


def test_telegram_retry_after_sets_global_cooldown():
    import asyncio
    from types import SimpleNamespace
    from ttr_live import _safe_notify

    class RetryAfterLike(Exception):
        retry_after = 3600

    class Bot:
        async def send_message(self, **kwargs):
            raise RetryAfterLike('flood control')

    data = {}
    context = SimpleNamespace(application=SimpleNamespace(bot_data=data), bot=Bot())
    result = asyncio.run(_safe_notify(context, 123, 'test'))
    assert result is False
    assert data['ttr_telegram_blocked_until'] > 0


def test_queue_notify_drops_messages_during_telegram_cooldown():
    import asyncio, time
    from types import SimpleNamespace
    from ttr_live import _queue_notify

    class Bot:
        async def send_message(self, **kwargs):
            raise AssertionError('must not send during cooldown')

    data = {'ttr_telegram_blocked_until': time.monotonic() + 3600}
    context = SimpleNamespace(application=SimpleNamespace(bot_data=data), bot=Bot())
    _queue_notify(context, 123, 'test')
    assert 'ttr_notification_tasks' not in data


def test_notification_preferences_are_independent_and_survive_resubscribe(tmp_path):
    store=TTRLiveStore(str(tmp_path/'notify.sqlite'))
    store.subscribe('BTCUSDT',123,{})
    store.set_notification('BTCUSDT','tp',False)
    store.set_notification('BTCUSDT','open',False)
    cfg=json.loads(store.get_config('BTCUSDT')['config'])
    assert cfg['notify_tp'] is False
    assert cfg['notify_open'] is False
    assert cfg['notify_close'] is True
    assert cfg['notify_stop'] is True
    # Re-starting the subscription must not reset saved preferences.
    store.subscribe('BTCUSDT',456,{'feed_mode':'rest'})
    cfg=json.loads(store.get_config('BTCUSDT')['config'])
    assert cfg['notify_tp'] is False and cfg['notify_open'] is False
    assert cfg['notify_close'] is True and cfg['notify_stop'] is True


def test_open_trade_snapshot_restores_full_position_and_skips_downtime_bars(tmp_path):
    from ttr_live import restore_position_from_trade, resume_bar_boundary, TF_MS
    store=TTRLiveStore(str(tmp_path/'resume.sqlite'))
    store.subscribe('ETHUSDT',456,{})
    tid=store.open_trade('ETHUSDT','sell',900000,2500,None)
    position={'side':'sell','trade_id':tid,'entry_time':900000,'entry_price':2500.0,
        'avg_entry':2498.0,'remaining_pct':75.0,'realized_pnl_pct':0.15,'stop':None,
        'tp_count':1,'tp_marked_fractals':[810000],'re_marked_fractals':[720000],
        'last_tp':2480.0,'last_re':2490.0,'engine_version':2}
    store.update_trade(tid,remaining_pct=75,realized_pnl_pct=.15,
        payload=json.dumps({'engine_version':2,'position':position,'avg_entry':2498,
            'tp_count':1,'tp_marked_fractals':[810000],'re_marked_fractals':[720000],
            'last_tp':2480,'last_re':2490}))
    restored=restore_position_from_trade(store.open_trades('ETHUSDT')[0])
    assert restored['trade_id']==tid and restored['side']=='sell'
    assert restored['avg_entry']==2498.0 and restored['remaining_pct']==75.0
    assert restored['tp_count']==1 and restored['tp_marked_fractals']==[810000]
    # At 10:17, the 10:00-10:15 candle is the newest closed candle. Resuming
    # from that boundary prevents replaying any candles during downtime.
    assert resume_bar_boundary(10*60*60*1000+17*60*1000)==10*60*60*1000


def test_trade_event_notification_flags_are_independent(monkeypatch):
    import ttr_live
    sent=[]
    monkeypatch.setattr(ttr_live,'_queue_notify',lambda context,chat_id,message: sent.append(message))
    params={'notify_open':False,'notify_close':True,'notify_tp':False,'notify_stop':True}
    for event in ('open','close','tp','stop'):
        ttr_live._notify_trade_event(None,1,params,event,event)
    assert sent==['close','stop']


def test_resubscribe_updates_config_without_resetting_position_or_bar(tmp_path):
    store = TTRLiveStore(str(tmp_path/'update_config.sqlite'))
    store.subscribe('BTCUSDT', 123, {'feed_mode': 'websocket', 'poll_seconds': 5,
                                    'tp_percentages': [25, 25, 25, 25]})
    state = {'position': {'side': 'buy', 'trade_id': 7, 'remaining_pct': 50}}
    store.save_state('BTCUSDT', state, 987654)
    store.unsubscribe('BTCUSDT')
    store.subscribe('BTCUSDT', 123, {'feed_mode': 'rest', 'poll_seconds': 3,
                                    'tp_percentages': [40, 30, 30]})
    row = store.get_config('BTCUSDT')
    cfg = json.loads(row['config'])
    assert row['enabled'] == 1
    assert cfg['feed_mode'] == 'rest'
    assert cfg['poll_seconds'] == 3
    assert cfg['tp_percentages'] == [40, 30, 30]
    assert json.loads(row['state']) == state
    assert row['last_bar_time'] == 987654
