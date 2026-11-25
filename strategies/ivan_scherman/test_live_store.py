import os,tempfile
from ttr_live import TTRLiveStore
from ivan_live import IvanLiveStore

def test_ivan_store_persists_subscriptions_and_trade_history():
    fd,path=tempfile.mkstemp(suffix=".sqlite3");os.close(fd)
    try:
        db=TTRLiveStore(path=path,backend="sqlite",database_url=None)
        store=IvanLiveStore(db)
        store.subscribe("BTCUSDT",123,{"sma_long":200})
        cfg=store.get("BTCUSDT")
        assert cfg and cfg["enabled"]==1
        tid=store.open_trade("BTCUSDT",1000,50000,{"signal":"ENTRY"})
        assert len(store.open_rows("BTCUSDT"))==1
        store.close_trade(tid,2000,51000,"TP",2.0)
        assert store.history("BTCUSDT")[0]["status"]=="CLOSED"
        store.unsubscribe("BTCUSDT")
        assert store.get("BTCUSDT")["enabled"]==0
    finally:
        try:os.remove(path)
        except OSError:pass


def test_live_intrabar_stop_closes_from_websocket_tick():
    import asyncio,json,time
    from types import SimpleNamespace
    from ivan_live import monitor_symbol,TF_MS
    class Store:
        def __init__(self):
            self.row={"id":7,"symbol":"BTCUSDT","entry_time":1,"entry_price":100.0,
                      "payload":json.dumps({"stop_price":95.0,"tp_target":105.0})}
            self.closed=None
        def open_rows(self,symbol):return [self.row] if self.row else []
        def close_trade(self,*args):self.closed=args;self.row=None
        def event(self,*args):pass
    class Bot:
        async def send_message(self,**kwargs):pass
    now=int(time.time()*1000);last=(now//TF_MS)*TF_MS-TF_MS
    store=Store()
    cfg={"symbol":"BTCUSDT","chat_id":1,"last_bar_time":last,
         "config":json.dumps({"stop_type":"percent","exit_mode":"close","commission_pct":0})}
    asyncio.run(monitor_symbol(SimpleNamespace(bot=Bot()),cfg,None,store=store,
        market_ticks=[{"price":94.0,"exchange_time":now}]))
    assert store.closed is not None
    assert store.closed[3]=="STOP"
