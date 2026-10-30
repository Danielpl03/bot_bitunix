"""Bitunix public ticker WebSocket feed with reconnect and in-memory latest-price cache."""
from __future__ import annotations
import asyncio, json, logging, time
from typing import Dict, Any

WS_URL = "wss://fapi.bitunix.com/public/"
log = logging.getLogger(__name__)

class BitunixTickerFeed:
    def __init__(self, url: str = WS_URL, stale_after: float = 10.0):
        self.url=url; self.stale_after=stale_after
        self.latest: Dict[str, Dict[str, Any]] = {}
        self.pending: Dict[str, list] = {}
        self.status: Dict[str, Dict[str, Any]] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self._stopping=False

    def ensure(self, symbol: str):
        task=self.tasks.get(symbol)
        if task is None or task.done():
            self.tasks[symbol]=asyncio.create_task(self._run(symbol), name=f"bitunix-ws-{symbol}")

    def get(self, symbol: str):
        item=self.latest.get(symbol)
        if not item: return None
        age=time.time()-item['received_at']
        if age > self.stale_after: return None
        return {**item, 'age_seconds': round(age, 2)}

    def drain(self, symbol: str):
        events=self.pending.get(symbol, [])
        self.pending[symbol]=[]
        return events

    def health(self, symbol: str):
        s=dict(self.status.get(symbol, {})); q=self.latest.get(symbol)
        s['connected']=bool(s.get('connected'))
        s['last_message_age_seconds']=round(time.time()-q['received_at'],2) if q else None
        s['fresh']=self.get(symbol) is not None
        return s

    async def stop(self):
        self._stopping=True
        for task in self.tasks.values(): task.cancel()
        if self.tasks: await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()

    async def _run(self, symbol: str):
        try:
            import websockets
        except Exception as exc:
            self.status[symbol]={'connected':False,'error':f'websockets dependency missing: {exc}'}
            return
        delay=1
        while not self._stopping:
            try:
                self.status[symbol]={'connected':False,'state':'connecting','last_error':None}
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=10, close_timeout=5, max_queue=1000) as ws:
                    await ws.send(json.dumps({'op':'subscribe','args':[{'symbol':symbol,'ch':'trade'}]}))
                    self.status[symbol]={'connected':True,'state':'connected','connected_at':time.time(),'last_error':None}
                    delay=1
                    while not self._stopping:
                        try:
                            raw=await asyncio.wait_for(ws.recv(), timeout=25)
                        except asyncio.TimeoutError:
                            await ws.send(json.dumps({'op':'ping','ping':int(time.time())}))
                            continue
                        msg=json.loads(raw)
                        if msg.get('op') in ('ping','pong') or 'pong' in msg: continue
                        if msg.get('ch') != 'trade' or msg.get('symbol') != symbol: continue
                        data=msg.get('data') or {}
                        # Official trade channel publishes individual executions as `p`.
                        if isinstance(data, list):
                            px=(data[-1].get('p') if data else None)
                        else:
                            px=data.get('p')
                        if px is None: continue
                        price=float(px)
                        if price <= 0: continue
                        ts=int(msg.get('ts') or time.time()*1000)
                        old=self.latest.get(symbol)
                        if old and ts < old.get('exchange_time',0): continue
                        event={'price':price,'exchange_time':ts,'received_at':time.time(),'source':'websocket_trade'}
                        self.latest[symbol]=event
                        queue=self.pending.setdefault(symbol, [])
                        queue.append(event)
                        # Bound memory if the consumer is temporarily delayed.
                        if len(queue)>5000: del queue[:-5000]
                        self.status[symbol].update({'connected':True,'state':'connected','last_message_at':time.time()})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status[symbol]={'connected':False,'state':'reconnecting','last_error':str(exc)[:300],'disconnected_at':time.time()}
                log.warning('Bitunix WS %s disconnected: %s',symbol,exc)
                await asyncio.sleep(delay)
                delay=min(delay*2,30)
