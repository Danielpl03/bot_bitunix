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
