import time
from ttr_websocket import BitunixTickerFeed

def test_ws_cache_fresh_and_stale():
    feed=BitunixTickerFeed(stale_after=0.05)
    feed.latest['BTCUSDT']={'price':100.0,'exchange_time':int(time.time()*1000),'received_at':time.time(),'source':'websocket_trade'}
    assert feed.get('BTCUSDT')['price']==100.0
    time.sleep(0.06)
    assert feed.get('BTCUSDT') is None
    assert feed.health('BTCUSDT')['fresh'] is False

def test_ws_health_defaults_disconnected():
    feed=BitunixTickerFeed()
    health=feed.health('ETHUSDT')
    assert health['connected'] is False
    assert health['fresh'] is False


def test_ws_drain_returns_all_buffered_ticks_in_order():
    feed=BitunixTickerFeed()
    feed.pending['BTCUSDT']=[
        {'price':100.0,'exchange_time':1},
        {'price':99.0,'exchange_time':2},
        {'price':101.0,'exchange_time':3},
    ]
    ticks=feed.drain('BTCUSDT')
    assert [x['price'] for x in ticks]==[100.0,99.0,101.0]
    assert feed.drain('BTCUSDT')==[]


def test_stop_closes_active_websocket_connections():
    import asyncio

    class FakeSocket:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    async def scenario():
        feed=BitunixTickerFeed()
        socket=FakeSocket()
        feed.connections['BTCUSDT']=socket
        await feed.stop()
        assert socket.closed is True
        assert feed.connections == {}
        assert feed.tasks == {}

    asyncio.run(scenario())


def test_bitunix_json_pong_is_recognized_and_updates_health():
    import asyncio
    import json

    feed = BitunixTickerFeed()
    event = asyncio.Event()
    feed.status['BTCUSDT'] = {'heartbeat_sent_monotonic': time.monotonic() - 0.01}
    feed._process_message(
        json.dumps({'op': 'ping', 'pong': int(time.time()), 'ping': int(time.time())}),
        'BTCUSDT',
        event,
    )
    assert event.is_set()
    health = feed.health('BTCUSDT')
    assert health['last_pong_at'] is not None
    assert health['heartbeat_rtt_ms'] >= 0


def test_documented_json_heartbeat_is_sent_even_without_trade_frames():
    import asyncio
    import json

    class FakeSocket:
        def __init__(self, pong_event):
            self.pong_event = pong_event
            self.sent = []

        async def send(self, raw):
            self.sent.append(json.loads(raw))
            self.pong_event.set()

    async def scenario():
        feed = BitunixTickerFeed(heartbeat_interval=5, heartbeat_timeout=2)
        event = asyncio.Event()
        socket = FakeSocket(event)
        task = asyncio.create_task(feed._heartbeat(socket, 'BTCUSDT', event))
        await asyncio.sleep(0.03)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert socket.sent
        assert socket.sent[0]['op'] == 'ping'
        assert isinstance(socket.sent[0]['ping'], int)
        assert feed.health('BTCUSDT')['last_ping_at'] is not None

    asyncio.run(scenario())
