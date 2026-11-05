"""Independent RSI live-paper engine. Shares the TTR PostgreSQL/SQLite connection,
without sharing or overwriting TTR positions. No exchange orders are submitted.
"""
from __future__ import annotations
import asyncio, json, time, logging
from datetime import datetime, timezone
from indicators import rsi_indicator
from strategy_rsi import generate_rsi_signals
from ttr_live import STORE as DB

log=logging.getLogger(__name__)
TF_MS={'1m':60_000,'3m':180_000,'5m':300_000,'15m':900_000,'30m':1_800_000,'1h':3_600_000,'2h':7_200_000,'4h':14_400_000,'1d':86_400_000}

class RSILiveStore:
    def __init__(self, db=DB):
        self.db=db
        if db.backend=='sqlite':
            ddl=[
              "CREATE TABLE IF NOT EXISTS rsi_live_config (symbol TEXT NOT NULL, interval TEXT NOT NULL, chat_id INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, config TEXT NOT NULL, last_bar_time INTEGER, updated_at INTEGER NOT NULL, PRIMARY KEY(symbol,interval))",
              "CREATE TABLE IF NOT EXISTS rsi_live_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, interval TEXT NOT NULL, side TEXT NOT NULL, entry_time INTEGER NOT NULL, entry_price REAL NOT NULL, stop_price REAL NOT NULL, quantity REAL NOT NULL, risk_usdt REAL NOT NULL, status TEXT NOT NULL, exit_time INTEGER, exit_price REAL, exit_reason TEXT, pnl_usdt REAL NOT NULL DEFAULT 0, payload TEXT NOT NULL DEFAULT '{}')",
              "CREATE TABLE IF NOT EXISTS rsi_live_events (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, interval TEXT NOT NULL, event_time INTEGER NOT NULL, event_type TEXT NOT NULL, side TEXT, price REAL, payload TEXT NOT NULL)"
            ]
        else:
            ddl=[
              "CREATE TABLE IF NOT EXISTS rsi_live_config (symbol TEXT NOT NULL, interval TEXT NOT NULL, chat_id BIGINT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, config TEXT NOT NULL, last_bar_time BIGINT, updated_at BIGINT NOT NULL, PRIMARY KEY(symbol,interval))",
              "CREATE TABLE IF NOT EXISTS rsi_live_trades (id BIGSERIAL PRIMARY KEY, symbol TEXT NOT NULL, interval TEXT NOT NULL, side TEXT NOT NULL, entry_time BIGINT NOT NULL, entry_price DOUBLE PRECISION NOT NULL, stop_price DOUBLE PRECISION NOT NULL, quantity DOUBLE PRECISION NOT NULL, risk_usdt DOUBLE PRECISION NOT NULL, status TEXT NOT NULL, exit_time BIGINT, exit_price DOUBLE PRECISION, exit_reason TEXT, pnl_usdt DOUBLE PRECISION NOT NULL DEFAULT 0, payload TEXT NOT NULL DEFAULT '{}')",
              "CREATE TABLE IF NOT EXISTS rsi_live_events (id BIGSERIAL PRIMARY KEY, symbol TEXT NOT NULL, interval TEXT NOT NULL, event_time BIGINT NOT NULL, event_type TEXT NOT NULL, side TEXT, price DOUBLE PRECISION, payload TEXT NOT NULL)"
            ]
        with db._db() as c:
            for q in ddl: db._execute(c,q)
    def configs(self):
        with self.db._db() as c: return [dict(x) for x in self.db._execute(c,"SELECT c.* FROM rsi_live_config c WHERE c.enabled=1 OR EXISTS (SELECT 1 FROM rsi_live_trades t WHERE t.symbol=c.symbol AND t.interval=c.interval AND t.status='OPEN')").fetchall()]
    def get_config(self,symbol,interval):
        with self.db._db() as c:
            x=self.db._execute(c,'SELECT * FROM rsi_live_config WHERE symbol=? AND interval=?',(symbol,interval)).fetchone()
            return dict(x) if x else None
    def subscribe(self,symbol,interval,chat_id,config):
        now=int(time.time()*1000); width=TF_MS[interval]; baseline=(now//width)*width-width
        with self.db._db() as c:
            self.db._execute(c,'INSERT INTO rsi_live_config(symbol,interval,chat_id,enabled,config,last_bar_time,updated_at) VALUES(?,?,?,1,?,?,?) ON CONFLICT(symbol,interval) DO UPDATE SET chat_id=excluded.chat_id,enabled=1,config=excluded.config,last_bar_time=excluded.last_bar_time,updated_at=excluded.updated_at',(symbol,interval,chat_id,json.dumps(config),baseline,now))
    def unsubscribe(self,symbol,interval):
        with self.db._db() as c: self.db._execute(c,'UPDATE rsi_live_config SET enabled=0,updated_at=? WHERE symbol=? AND interval=?',(int(time.time()*1000),symbol,interval))
    def open_trades(self,symbol,interval):
        with self.db._db() as c: return [dict(x) for x in self.db._execute(c,"SELECT * FROM rsi_live_trades WHERE symbol=? AND interval=? AND status='OPEN' ORDER BY id",(symbol,interval)).fetchall()]
    def all_open(self):
        with self.db._db() as c: return [dict(x) for x in self.db._execute(c,"SELECT * FROM rsi_live_trades WHERE status='OPEN' ORDER BY id").fetchall()]
    def open_trade(self, symbol, interval, side, ts, entry, stop, qty, risk, payload):
        with self.db._db() as c:
            q='INSERT INTO rsi_live_trades(symbol,interval,side,entry_time,entry_price,stop_price,quantity,risk_usdt,status,payload) VALUES(?,?,?,?,?,?,?,?,?,?)'
            args=(symbol,interval,side,ts,entry,stop,qty,risk,'OPEN',json.dumps(payload))
            if self.db.backend=='postgres': return self.db._execute(c,q+' RETURNING id',args).fetchone()['id']
            return self.db._execute(c,q,args).lastrowid
    def close_trade(self,trade_id,ts,price,reason,pnl):
        with self.db._db() as c: self.db._execute(c,"UPDATE rsi_live_trades SET status='CLOSED',exit_time=?,exit_price=?,exit_reason=?,pnl_usdt=? WHERE id=? AND status='OPEN'",(ts,price,reason,pnl,trade_id))
    def event(self,symbol,interval,ts,etype,side,price,payload=None):
        with self.db._db() as c: self.db._execute(c,'INSERT INTO rsi_live_events(symbol,interval,event_time,event_type,side,price,payload) VALUES(?,?,?,?,?,?,?)',(symbol,interval,ts,etype,side,price,json.dumps(payload or {})))
    def commit_bar(self,symbol,interval,bar_time):
        with self.db._db() as c: self.db._execute(c,'UPDATE rsi_live_config SET last_bar_time=?,updated_at=? WHERE symbol=? AND interval=?',(bar_time,int(time.time()*1000),symbol,interval))
    def status(self):
        with self.db._db() as c: return [dict(x) for x in self.db._execute(c,'SELECT * FROM rsi_live_config ORDER BY symbol,interval').fetchall()]
    def history(self,symbol=None):
        with self.db._db() as c:
            if symbol: rows=self.db._execute(c,"SELECT * FROM rsi_live_trades WHERE symbol=? ORDER BY CASE WHEN status='OPEN' THEN 0 ELSE 1 END,id DESC LIMIT 200",(symbol,)).fetchall()
            else: rows=self.db._execute(c,"SELECT * FROM rsi_live_trades ORDER BY CASE WHEN status='OPEN' THEN 0 ELSE 1 END,id DESC LIMIT 200").fetchall()
            return [dict(x) for x in rows]

STORE=RSILiveStore()

async def _notify(context,chat_id,msg):
    try: await context.bot.send_message(chat_id=chat_id,text=msg)
    except Exception: log.exception('RSI live Telegram notification failed')

def _closed_bars(client,symbol,interval):
    rows=client.get_kline(symbol,interval,limit=250)
    now=int(time.time()*1000); width=TF_MS[interval]
    return [x for x in rows if int(x.get('time',0))+width<=now]

async def monitor_subscription(context,cfg,client,store=STORE,ws_feed=None,market_ticks=None,market_quote=None):
    symbol,interval=cfg['symbol'],cfg['interval']; params=json.loads(cfg['config'] or '{}')
    width=TF_MS[interval]; now=int(time.time()*1000)
    # Recover open positions from the durable ledger; all RSI positions are independent.
    opened=await asyncio.to_thread(store.open_trades,symbol,interval)
    ticks=market_ticks if market_ticks is not None else (ws_feed.drain(symbol) if ws_feed else [])
    ws_quote=market_quote if market_quote is not None else (ws_feed.get(symbol) if ws_feed else None)
    if ws_quote:
        price=float(ws_quote['price'])
    else:
        quote=await asyncio.to_thread(client.get_price,symbol)
        price=float((quote or {}).get('lastPrice') or (quote or {}).get('last') or 0)
    # Procesar cada trade tick recibido evita perder un toque intrabar del stop
    # entre dos ejecuciones del supervisor; REST queda como fallback.
    for t in opened:
        stop=float(t['stop_price']); hit_tick=None
        for tick in ticks:
            px=float(tick['price'])
            if (px<=stop if t['side']=='LONG' else px>=stop): hit_tick=tick; break
        if hit_tick is None and not ticks and price>0:
            if (price<=stop if t['side']=='LONG' else price>=stop): hit_tick={'price':price,'exchange_time':now}
        if hit_tick is not None:
            fill=float(hit_tick['price']); fill_time=int(hit_tick.get('exchange_time') or now)
            signed=(fill-float(t['entry_price'])) if t['side']=='LONG' else (float(t['entry_price'])-fill)
            pnl=signed*float(t['quantity'])
            await asyncio.to_thread(store.close_trade,t['id'],fill_time,fill,'SL',pnl)
            await asyncio.to_thread(store.event,symbol,interval,fill_time,'SL',t['side'],fill,{'trade_id':t['id'],'pnl_usdt':pnl})
            await _notify(context,cfg['chat_id'],f'🛑 RSI {symbol} {interval} · STOP {t["side"]}\nEntrada {float(t["entry_price"]):.8g} · stop ejecutado {fill:.8g} · PnL {pnl:+.4f} USDT')
    latest_boundary=(now//width)*width-width
    last=int(cfg.get('last_bar_time') or 0)
    if latest_boundary<=last: return
    bars=await asyncio.to_thread(_closed_bars,client,symbol,interval)
    if len(bars)<int(params.get('rsi_period',14))+3: return
    bars=bars[-220:]; ts=[int(x['time']) for x in bars]
    if ts[-1]<=last: return
    closes=[float(x['close']) for x in bars]; highs=[float(x['high']) for x in bars]; lows=[float(x['low']) for x in bars]
    volumes=[float(x.get('volume',0) or 0) for x in bars]
    signals=generate_rsi_signals(closes,highs,lows,rsi_period=int(params.get('rsi_period',14)),
        oversold=float(params.get('oversold',30)),overbought=float(params.get('overbought',70)),
        pivot_left=int(params.get('pivot_left',2)),pivot_right=int(params.get('pivot_right',2)),
        stop_buffer_pct=float(params.get('stop_buffer_pct',0)),volume=volumes)
    latest=len(bars)-1; sig=signals[latest]
    rsi=rsi_indicator(closes,rsi_period=int(params.get('rsi_period',14)),ma_type='None',ma_period=1)['rsi']
    # Exit is evaluated on a closed candle at market close; stop has already been checked intrabar.
    previous_rsi=rsi[latest-1] if latest>0 else None
    current_rsi=rsi[latest]
    for t in await asyncio.to_thread(store.open_trades,symbol,interval):
        hit_tp=(previous_rsi is not None and current_rsi is not None and
                ((t['side']=='LONG' and previous_rsi>=float(params.get('overbought',70)) and current_rsi<float(params.get('overbought',70))) or
                 (t['side']=='SHORT' and previous_rsi<=float(params.get('oversold',30)) and current_rsi>float(params.get('oversold',30)))))
        if hit_tp:
            exit_px=closes[latest]; signed=(exit_px-float(t['entry_price'])) if t['side']=='LONG' else (float(t['entry_price'])-exit_px)
            pnl=signed*float(t['quantity'])
            await asyncio.to_thread(store.close_trade,t['id'],ts[latest],exit_px,'TP_RSI_OPUESTO',pnl)
            await asyncio.to_thread(store.event,symbol,interval,ts[latest],'TP_RSI_OPUESTO',t['side'],exit_px,{'trade_id':t['id'],'rsi':rsi[latest],'pnl_usdt':pnl})
            await _notify(context,cfg['chat_id'],f'🎯 RSI {symbol} {interval} · TP RSI zona opuesta\n{t["side"]} @ {exit_px:.8g} · RSI {rsi[latest]:.2f} · PnL {pnl:+.4f} USDT')
    if sig and bool(cfg.get('enabled')):
        # A market paper entry using latest available quote; backtests use next candle open.
        entry=price if price>0 else closes[latest]; stop=float(sig.stop_loss)
        dist=(entry-stop) if sig.side=='LONG' else (stop-entry)
        risk=float(params.get('risk_usdt',10))
        if dist>0 and risk>0:
            qty=risk/dist
            payload={'rsi':sig.rsi,'extreme_start_index':sig.extreme_start_index,'pivot_used':sig.pivot_used,'engine':'RSI_EXTREME_V1'}
            tid=await asyncio.to_thread(store.open_trade,symbol,interval,sig.side,now,entry,stop,qty,risk,payload)
            await asyncio.to_thread(store.event,symbol,interval,now,'ENTRY',sig.side,entry,{'trade_id':tid,**payload,'stop':stop,'quantity':qty,'risk_usdt':risk})
            await _notify(context,cfg['chat_id'],f'📍 RSI {symbol} {interval} · ENTRADA {sig.side}\nRSI {sig.rsi:.2f} · entrada {entry:.8g} · stop {stop:.8g}\nRiesgo fijo {risk:.2f} USDT · cantidad {qty:.8g} · pivote: {"sí" if sig.pivot_used else "extremo del régimen"}')
    await asyncio.to_thread(store.commit_bar,symbol,interval,ts[-1])

async def monitor_job(context):
    from bot import bitunix
    from ttr_websocket import BitunixTickerFeed
    configs=await asyncio.to_thread(STORE.configs)
    feed=context.application.bot_data.get('rsi_ws_feed')
    if feed is None:
        feed=BitunixTickerFeed(stale_after=10,heartbeat_interval=20,heartbeat_timeout=10)
        context.application.bot_data['rsi_ws_feed']=feed
    active={r['symbol'] for r in configs}
    for sym in list(feed.tasks):
        if sym not in active: await feed.stop_symbol(sym)
    for sym in active: feed.ensure(sym)
    # One batch per symbol is shared with every timeframe subscription, so a
    # stop-touch tick is not consumed by the first timeframe and lost to others.
    market_batches={sym:feed.drain(sym) for sym in active}
    market_quotes={sym:feed.get(sym) for sym in active}
    for cfg in configs:
        try: await monitor_subscription(context,cfg,bitunix,ws_feed=feed,
                market_ticks=market_batches.get(cfg['symbol'],[]),market_quote=market_quotes.get(cfg['symbol']))
        except Exception: log.exception('RSI monitor error %s %s',cfg['symbol'],cfg['interval'])
