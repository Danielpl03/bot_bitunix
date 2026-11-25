"""Daily paper-live supervisor for Iván Scherman strategy; no exchange orders."""
from __future__ import annotations
import asyncio,json,time,logging
from datetime import datetime,timezone
from ttr_live import STORE as DB
from strategies.ivan_scherman.engine import run_backtest

log=logging.getLogger(__name__)
TF="1d"; TF_MS=86_400_000

class IvanLiveStore:
    def __init__(self,db=DB):
        self.db=db
        if db.backend=="sqlite":
            ddl=[
            "CREATE TABLE IF NOT EXISTS ivan_live_config(symbol TEXT PRIMARY KEY,chat_id INTEGER NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,config TEXT NOT NULL,last_bar_time INTEGER,updated_at INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS ivan_live_trades(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT NOT NULL,entry_time INTEGER NOT NULL,entry_price REAL NOT NULL,exit_time INTEGER,exit_price REAL,exit_reason TEXT,status TEXT NOT NULL,pnl_pct REAL,payload TEXT NOT NULL DEFAULT '{}')",
            "CREATE TABLE IF NOT EXISTS ivan_live_events(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT NOT NULL,event_time INTEGER NOT NULL,event_type TEXT NOT NULL,price REAL,payload TEXT NOT NULL)"]
        else:
            ddl=[
            "CREATE TABLE IF NOT EXISTS ivan_live_config(symbol TEXT PRIMARY KEY,chat_id BIGINT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,config TEXT NOT NULL,last_bar_time BIGINT,updated_at BIGINT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS ivan_live_trades(id BIGSERIAL PRIMARY KEY,symbol TEXT NOT NULL,entry_time BIGINT NOT NULL,entry_price DOUBLE PRECISION NOT NULL,exit_time BIGINT,exit_price DOUBLE PRECISION,exit_reason TEXT,status TEXT NOT NULL,pnl_pct DOUBLE PRECISION,payload TEXT NOT NULL DEFAULT '{}')",
            "CREATE TABLE IF NOT EXISTS ivan_live_events(id BIGSERIAL PRIMARY KEY,symbol TEXT NOT NULL,event_time BIGINT NOT NULL,event_type TEXT NOT NULL,price DOUBLE PRECISION,payload TEXT NOT NULL)"]
        with db._db() as c:
            for q in ddl: db._execute(c,q)
    def subscribe(self,symbol,chat_id,config):
        now=int(time.time()*1000); baseline=(now//TF_MS)*TF_MS-TF_MS
        with self.db._db() as c:
            self.db._execute(c,"INSERT INTO ivan_live_config(symbol,chat_id,enabled,config,last_bar_time,updated_at) VALUES(?,?,1,?,?,?) ON CONFLICT(symbol) DO UPDATE SET chat_id=excluded.chat_id,enabled=1,config=excluded.config,updated_at=excluded.updated_at",(symbol,chat_id,json.dumps(config),baseline,now))
    def unsubscribe(self,symbol):
        with self.db._db() as c:self.db._execute(c,"UPDATE ivan_live_config SET enabled=0,updated_at=? WHERE symbol=?",(int(time.time()*1000),symbol))
    def configs(self):
        with self.db._db() as c:return [dict(x) for x in self.db._execute(c,"SELECT * FROM ivan_live_config WHERE enabled=1 OR EXISTS(SELECT 1 FROM ivan_live_trades t WHERE t.symbol=ivan_live_config.symbol AND t.status='OPEN')").fetchall()]
    def get(self,symbol):
        with self.db._db() as c:
            x=self.db._execute(c,"SELECT * FROM ivan_live_config WHERE symbol=?",(symbol,)).fetchone()
            return dict(x) if x else None
    def checkpoint(self,symbol,bar_time):
        with self.db._db() as c:self.db._execute(c,"UPDATE ivan_live_config SET last_bar_time=?,updated_at=? WHERE symbol=?",(bar_time,int(time.time()*1000),symbol))
    def open_trade(self,symbol,ts,price,payload):
        with self.db._db() as c:
            q="INSERT INTO ivan_live_trades(symbol,entry_time,entry_price,status,payload) VALUES(?,?,?,'OPEN',?)"
            args=(symbol,ts,price,json.dumps(payload))
            if self.db.backend=="postgres":return self.db._execute(c,q+" RETURNING id",args).fetchone()["id"]
            return self.db._execute(c,q,args).lastrowid
    def close_trade(self,tid,ts,price,reason,pnl):
        with self.db._db() as c:self.db._execute(c,"UPDATE ivan_live_trades SET exit_time=?,exit_price=?,exit_reason=?,status='CLOSED',pnl_pct=? WHERE id=? AND status='OPEN'",(ts,price,reason,pnl,tid))
    def update_payload(self,tid,payload):
        with self.db._db() as c:self.db._execute(c,"UPDATE ivan_live_trades SET payload=? WHERE id=? AND status='OPEN'",(json.dumps(payload),tid))
    def open_rows(self,symbol):
        with self.db._db() as c:return [dict(x) for x in self.db._execute(c,"SELECT * FROM ivan_live_trades WHERE symbol=? AND status='OPEN' ORDER BY id DESC",(symbol,)).fetchall()]
    def event(self,symbol,ts,typ,price,payload):
        with self.db._db() as c:self.db._execute(c,"INSERT INTO ivan_live_events(symbol,event_time,event_type,price,payload) VALUES(?,?,?,?,?)",(symbol,ts,typ,price,json.dumps(payload)))
    def status(self):
        with self.db._db() as c:return [dict(x) for x in self.db._execute(c,"SELECT * FROM ivan_live_config ORDER BY symbol").fetchall()]
    def history(self,symbol=None):
        with self.db._db() as c:
            if symbol: rows=self.db._execute(c,"SELECT * FROM ivan_live_trades WHERE symbol=? ORDER BY id DESC LIMIT 200",(symbol,)).fetchall()
            else: rows=self.db._execute(c,"SELECT * FROM ivan_live_trades ORDER BY id DESC LIMIT 200").fetchall()
            return [dict(x) for x in rows]
    def events(self,symbol,limit=200):
        with self.db._db() as c:
            return [dict(x) for x in self.db._execute(c,"SELECT * FROM ivan_live_events WHERE symbol=? ORDER BY id DESC LIMIT ?",(symbol,limit)).fetchall()]
    def performance(self):
        with self.db._db() as c:
            rows=[dict(x) for x in self.db._execute(c,"SELECT * FROM ivan_live_trades ORDER BY entry_time,id").fetchall()]
        closed=[x for x in rows if x["status"]=="CLOSED"]
        pnls=[float(x.get("pnl_pct") or 0) for x in closed]
        wins=[x for x in pnls if x>0];losses=[x for x in pnls if x<0]
        gp=sum(wins);gl=abs(sum(losses));eq=1.0
        for x in sorted(closed,key=lambda r:r.get("exit_time") or 0):eq*=max(0,1+float(x.get("pnl_pct") or 0)/100)
        return {"total":len(rows),"closed":len(closed),"open":len(rows)-len(closed),
            "wins":len(wins),"losses":len(losses),"win_rate_pct":len(wins)/len(closed)*100 if closed else 0,
            "profit_factor":gp/gl if gl else (None if not gp else float("inf")),
            "realized_pnl_pct":sum(pnls),"compounded_pnl_pct":(eq-1)*100,
            "best_pct":max(pnls) if pnls else 0,"worst_pct":min(pnls) if pnls else 0}
STORE=IvanLiveStore()

def _closed_daily(client,symbol):
    # Bitunix caps each kline request at 200 rows. Page backwards so the
    # 200-period SMA has warm-up plus enough history to reconstruct positions.
    now=int(time.time()*1000)
    rows=list(client.get_kline(symbol,"1d",limit=200))
    for _ in range(2):
        if not rows: break
        earliest=min(int(x.get("time",0)) for x in rows)
        older=client.get_kline(symbol,"1d",limit=200,end_time=earliest-1)
        if not older: break
        rows=older+rows
    dedup={int(x["time"]):x for x in rows}
    return [dedup[k] for k in sorted(dedup) if k+TF_MS<=now]

async def monitor_symbol(context,cfg,client,store=STORE,ws_feed=None,market_ticks=None,market_quote=None):
    symbol=cfg["symbol"]; params=json.loads(cfg.get("config") or "{}")
    now=int(time.time()*1000); latest=(now//TF_MS)*TF_MS-TF_MS
    last=int(cfg.get("last_bar_time") or 0)
    # Intrabar paper fills for the Pine stop and limit modes. Signal/close-based
    # exits still wait for the daily candle to finish.
    opened=await asyncio.to_thread(store.open_rows,symbol)
    ticks=list(market_ticks or [])
    quote=market_quote
    if opened and not ticks and quote:
        ticks=[quote]
    if opened and not ticks and not quote:
        try:
            ticker=await asyncio.to_thread(client.get_price,symbol)
            px=float((ticker or {}).get("lastPrice") or (ticker or {}).get("last") or 0)
            if px>0:ticks=[{"price":px,"exchange_time":now}]
        except Exception:log.debug("REST quote fallback Iván %s failed",symbol,exc_info=True)
    if opened:
        for row in opened:
            try:payload=json.loads(row.get("payload") or "{}")
            except (TypeError,ValueError):payload={}
            stop=float(payload["stop_price"]) if payload.get("stop_price") is not None else None
            target=float(payload["tp_target"]) if payload.get("tp_target") is not None else None
            reason=None; fill=None; fill_time=now
            for tick in sorted(ticks,key=lambda x:int(x.get("exchange_time") or 0)):
                px=float(tick["price"])
                if params.get("stop_type") in ("percent","atr") and stop is not None and px<=stop:
                    reason="STOP";fill=min(px,stop);fill_time=int(tick.get("exchange_time") or now);break
                if params.get("exit_mode")=="limit" and target is not None and px>=target:
                    reason="TP";fill=target;fill_time=int(tick.get("exchange_time") or now);break
            if reason:
                pnl=(float(fill)/float(row["entry_price"])-1)*100-2*float(params.get("commission_pct",.05))
                await asyncio.to_thread(store.close_trade,row["id"],fill_time,float(fill),reason,pnl)
                await asyncio.to_thread(store.event,symbol,fill_time,reason,float(fill),{"trade_id":row["id"],"pnl_pct":pnl,"intrabar":True})
                await _notify(context,cfg["chat_id"],f"{'🛑' if reason=='STOP' else '🎯'} Iván {symbol} · {reason} intrabar\nPrecio {float(fill):.8g} · PnL {pnl:+.3f}%")
    if latest<=last:return
    bars=await asyncio.to_thread(_closed_daily,client,symbol)
    if len(bars)<max(int(params.get("sma_long",200))+2,50):return
    times=[int(x["time"]) for x in bars]
    if times[-1]<=last:return
    engine_params={k:v for k,v in params.items() if k!="subscription_start"}
    engine_params.setdefault("start_time",int(params.get("subscription_start",last+TF_MS)))
    result=run_backtest([float(x["close"]) for x in bars],[float(x["high"]) for x in bars],
        [float(x["low"]) for x in bars],[float(x["open"]) for x in bars],times,**engine_params)
    # Reconcile newly closed events against persisted paper ledger. A subscription
    # baseline prevents replaying signals that predate activation.
    for ev in result["events"]:
        ts=int(ev["time"])
        if ts<=last:continue
        if ev["type"]=="ENTRY":
            # A single LONG per symbol is allowed.
            if not await asyncio.to_thread(store.open_rows,symbol):
                tid=await asyncio.to_thread(store.open_trade,symbol,ts,float(ev["price"]),ev)
                await asyncio.to_thread(store.event,symbol,ts,"ENTRY",float(ev["price"]),{"trade_id":tid,**ev})
                await _notify(context,cfg["chat_id"],f"📍 Iván {symbol} · COMPRA LONG\nCierre diario: {float(ev['price']):.8g}")
        elif ev["type"]=="EXIT":
            opened=await asyncio.to_thread(store.open_rows,symbol)
            if opened:
                row=opened[-1]
                pnl=(float(ev["price"])/float(row["entry_price"])-1)*100
                # Apply round-trip fee consistently with backtest.
                pnl-=2*float(params.get("commission_pct",.05))
                await asyncio.to_thread(store.close_trade,row["id"],ts,float(ev["price"]),ev["reason"],pnl)
                await asyncio.to_thread(store.event,symbol,ts,"EXIT",float(ev["price"]),{"trade_id":row["id"],"reason":ev["reason"],"pnl_pct":pnl})
                await _notify(context,cfg["chat_id"],f"🔒 Iván {symbol} · SALIDA {ev['reason']}\nPrecio {float(ev['price']):.8g} · PnL {pnl:+.3f}%")
    # For the next forming daily candle, Pine's SMA[1] limit is the SMA
    # value of the latest fully closed candle.
    still_open=await asyncio.to_thread(store.open_rows,symbol)
    if still_open and result["indicators"]["sma_tp"][-1] is not None:
        row=still_open[-1]
        try:payload=json.loads(row.get("payload") or "{}")
        except (TypeError,ValueError):payload={}
        payload["tp_target"]=float(result["indicators"]["sma_tp"][-1])
        await asyncio.to_thread(store.update_payload,row["id"],payload)
    await asyncio.to_thread(store.checkpoint,symbol,times[-1])

async def _notify(context,chat_id,message):
    try: await context.bot.send_message(chat_id=chat_id,text=message)
    except Exception: log.exception("Iván live Telegram notification failed")

async def monitor_job(context):
    from bot import bitunix
    from ttr_websocket import BitunixTickerFeed
    configs=await asyncio.to_thread(STORE.configs)
    feed=context.application.bot_data.get("ivan_ws_feed")
    if feed is None:
        feed=BitunixTickerFeed(stale_after=10,heartbeat_interval=20,heartbeat_timeout=10)
        context.application.bot_data["ivan_ws_feed"]=feed
    active={r["symbol"] for r in configs}
    for sym in list(feed.tasks):
        if sym not in active:await feed.stop_symbol(sym)
    for sym in active:feed.ensure(sym)
    ticks_by_symbol={sym:feed.drain(sym) for sym in active}
    quotes_by_symbol={sym:feed.get(sym) for sym in active}
    for cfg in configs:
        try:
            await monitor_symbol(context,cfg,bitunix,ws_feed=feed,
                market_ticks=ticks_by_symbol.get(cfg["symbol"],[]),
                market_quote=quotes_by_symbol.get(cfg["symbol"]))
        except Exception:log.exception("Iván live monitor error %s",cfg["symbol"])
