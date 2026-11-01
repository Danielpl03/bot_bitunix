"""TTR paper-trading monitor: Pine-v6 signals on closed 15m candles.
No exchange orders are ever sent. State and event history are persisted in SQLite or PostgreSQL.
"""
from __future__ import annotations
import json, os, sqlite3, time, math, asyncio, logging
from datetime import datetime, timezone
from pathlib import Path

DATABASE_URL = os.environ.get('SUPABASE_DB_URL') or os.environ.get('DATABASE_URL')
DB_BACKEND = os.environ.get('TTR_DB_BACKEND', 'auto').strip().lower()
if DB_BACKEND == 'auto':
    DB_BACKEND = 'postgres' if DATABASE_URL else 'sqlite'
DB_PATH = os.environ.get('TTR_LIVE_DB', '/tmp/ttr_live.sqlite3')
TF_MS = 15 * 60 * 1000
PINE_ENGINE_VERSION = 2
DEFAULT_POLL_SECONDS = max(2, int(os.environ.get('TTR_POLL_SECONDS', '5')))

def resume_bar_boundary(now_ms=None):
    """Latest fully closed 15m candle; bars during downtime are deliberately skipped."""
    now_ms = int(time.time()*1000) if now_ms is None else int(now_ms)
    return (now_ms//TF_MS)*TF_MS-TF_MS


def restore_position_from_trade(row):
    """Rebuild a TTR position from the durable OPEN-trade ledger row."""
    try: payload=json.loads(row.get('payload') or '{}')
    except (TypeError, ValueError): payload={}
    saved=dict(payload.get('position') or {})
    saved.update({'side':row['side'],'trade_id':row['id'],'entry_time':int(row['entry_time']),
        'entry_price':float(row['entry_price']),'avg_entry':float(payload.get('avg_entry',saved.get('avg_entry',row['entry_price']))),
        'remaining_pct':float(row.get('remaining_pct',100) or 0),
        'realized_pnl_pct':float(row.get('realized_pnl_pct',0) or 0),
        'stop':row.get('stop_price'),'engine_version':payload.get('engine_version',PINE_ENGINE_VERSION)})
    for key,default in (('tp_count',0),('tp_marked_fractals',[]),('re_marked_fractals',[]),('last_tp',None),('last_re',None)):
        saved.setdefault(key,payload.get(key,default))
    return saved


def poll_seconds(config=None):
    config = config or {}
    return max(2, min(30, int(config.get('poll_seconds', DEFAULT_POLL_SECONDS))))

class TTRDatabaseError(RuntimeError):
    pass

class TTRLiveStore:
    """SQLite/PostgreSQL persistence adapter. SQLite on Render Free is ephemeral."""
    def __init__(self, path=None, backend=None, database_url=None):
        self.backend = (backend or DB_BACKEND).lower()
        self.path = path or DB_PATH
        self.database_url = database_url or DATABASE_URL
        if self.backend not in ('sqlite', 'postgres'):
            raise ValueError(f'Unsupported TTR_DB_BACKEND: {self.backend}')
        if self.backend == 'postgres' and not self.database_url:
            raise RuntimeError('TTR_DB_BACKEND=postgres requiere DATABASE_URL o SUPABASE_DB_URL')
        self._init_schema()

    def _db(self):
        if self.backend == 'sqlite':
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(self.path, timeout=20)
            c.row_factory = sqlite3.Row
            return c
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError('Falta psycopg[binary]; instala requirements.txt') from exc
        url = self.database_url
        if 'sslmode=' not in url:
            url += ('&' if '?' in url else '?') + 'sslmode=require'
        return psycopg.connect(url, row_factory=dict_row, connect_timeout=8, prepare_threshold=0)

    def _sql(self, query):
        return query.replace('?', '%s') if self.backend == 'postgres' else query

    def _execute(self, conn, query, params=()):
        return conn.execute(self._sql(query), params)

    def _init_schema(self):
        if self.backend == 'sqlite':
            ddl = [
                "CREATE TABLE IF NOT EXISTS ttr_live_config (symbol TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, config TEXT NOT NULL, last_bar_time INTEGER, state TEXT NOT NULL DEFAULT '{}', updated_at INTEGER NOT NULL)",
                "CREATE TABLE IF NOT EXISTS ttr_live_events (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, event_time INTEGER NOT NULL, event_type TEXT NOT NULL, side TEXT, price REAL, payload TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS ttr_live_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, side TEXT NOT NULL, entry_time INTEGER NOT NULL, entry_price REAL NOT NULL, exit_time INTEGER, exit_price REAL, exit_reason TEXT, status TEXT NOT NULL, stop_price REAL, realized_pnl_pct REAL NOT NULL DEFAULT 0, remaining_pct REAL NOT NULL DEFAULT 100, payload TEXT NOT NULL DEFAULT '{}')",
                "CREATE TABLE IF NOT EXISTS ttr_live_health (component TEXT PRIMARY KEY, category TEXT NOT NULL, status TEXT NOT NULL, message TEXT, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, occurrences INTEGER NOT NULL DEFAULT 1, last_success INTEGER)"
            ]
        else:
            ddl = [
                "CREATE TABLE IF NOT EXISTS ttr_live_config (symbol TEXT PRIMARY KEY, chat_id BIGINT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, config TEXT NOT NULL, last_bar_time BIGINT, state TEXT NOT NULL DEFAULT '{}', updated_at BIGINT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS ttr_live_events (id BIGSERIAL PRIMARY KEY, symbol TEXT NOT NULL, event_time BIGINT NOT NULL, event_type TEXT NOT NULL, side TEXT, price DOUBLE PRECISION, payload TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS ttr_live_trades (id BIGSERIAL PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL, entry_time BIGINT NOT NULL, entry_price DOUBLE PRECISION NOT NULL, exit_time BIGINT, exit_price DOUBLE PRECISION, exit_reason TEXT, status TEXT NOT NULL, stop_price DOUBLE PRECISION, realized_pnl_pct DOUBLE PRECISION NOT NULL DEFAULT 0, remaining_pct DOUBLE PRECISION NOT NULL DEFAULT 100, payload TEXT NOT NULL DEFAULT '{}')",
                "CREATE TABLE IF NOT EXISTS ttr_live_health (component TEXT PRIMARY KEY, category TEXT NOT NULL, status TEXT NOT NULL, message TEXT, first_seen BIGINT NOT NULL, last_seen BIGINT NOT NULL, occurrences BIGINT NOT NULL DEFAULT 1, last_success BIGINT)"
            ]
        with self._db() as c:
            for stmt in ddl:
                self._execute(c, stmt)

    def subscribe(self, symbol, chat_id, config):
        # Per-symbol Telegram preferences survive /start and default to enabled.
        existing = self.get_config(symbol)
        old_config = json.loads(existing.get('config') or '{}') if existing else {}
        for key in ('notify_stop', 'notify_tp', 'notify_open', 'notify_close'):
            config.setdefault(key, old_config.get(key, True))
        with self._db() as c:
            self._execute(c, 'INSERT INTO ttr_live_config(symbol,chat_id,enabled,config,last_bar_time,state,updated_at) VALUES(?,?,1,?,NULL,?,?) ON CONFLICT(symbol) DO UPDATE SET chat_id=excluded.chat_id,enabled=1,config=excluded.config,updated_at=excluded.updated_at', (symbol, chat_id, json.dumps(config), json.dumps({'position': None}), int(time.time()*1000)))

    def set_notification(self, symbol, notification, enabled):
        allowed = {'stop':'notify_stop', 'tp':'notify_tp', 'open':'notify_open', 'close':'notify_close'}
        if notification not in allowed:
            raise ValueError('Notificación inválida')
        row = self.get_config(symbol)
        if not row:
            raise ValueError(f'TTR no está configurado para {symbol}')
        config = json.loads(row.get('config') or '{}')
        config[allowed[notification]] = bool(enabled)
        with self._db() as c:
            self._execute(c, 'UPDATE ttr_live_config SET config=?,updated_at=? WHERE symbol=?', (json.dumps(config), int(time.time()*1000), symbol))
        return config

    def unsubscribe(self, symbol):
        with self._db() as c:
            self._execute(c, 'UPDATE ttr_live_config SET enabled=0,updated_at=? WHERE symbol=?', (int(time.time()*1000), symbol))

    def configs(self):
        with self._db() as c:
            return [dict(x) for x in self._execute(c, 'SELECT * FROM ttr_live_config WHERE enabled=1').fetchall()]

    def get_config(self, symbol):
        with self._db() as c:
            x = self._execute(c, 'SELECT * FROM ttr_live_config WHERE symbol=?', (symbol,)).fetchone()
            return dict(x) if x else None

    def open_trades(self, symbol):
        with self._db() as c:
            return [dict(x) for x in self._execute(c, "SELECT * FROM ttr_live_trades WHERE symbol=? AND status='OPEN' ORDER BY id DESC", (symbol,)).fetchall()]

    def save_state(self, symbol, state, last_bar_time=None):
        with self._db() as c:
            if last_bar_time is None:
                self._execute(c, 'UPDATE ttr_live_config SET state=?,updated_at=? WHERE symbol=?', (json.dumps(state), int(time.time()*1000), symbol))
            else:
                self._execute(c, 'UPDATE ttr_live_config SET state=?,last_bar_time=?,updated_at=? WHERE symbol=?', (json.dumps(state), last_bar_time, int(time.time()*1000), symbol))

    def event(self, symbol, etype, side, price, ts, payload=None):
        with self._db() as c:
            self._execute(c, 'INSERT INTO ttr_live_events(symbol,event_time,event_type,side,price,payload) VALUES(?,?,?,?,?,?)', (symbol, ts, etype, side, price, json.dumps(payload or {})))

    def open_trade(self, symbol, side, ts, price, stop):
        with self._db() as c:
            query = 'INSERT INTO ttr_live_trades(symbol,side,entry_time,entry_price,status,stop_price) VALUES(?,?,?,?,?,?)'
            if self.backend == 'postgres':
                return self._execute(c, query + ' RETURNING id', (symbol, side, ts, price, 'OPEN', stop)).fetchone()['id']
            return self._execute(c, query, (symbol, side, ts, price, 'OPEN', stop)).lastrowid

    def update_trade(self, trade_id, **fields):
        allowed = {'exit_time', 'exit_price', 'exit_reason', 'status', 'stop_price', 'realized_pnl_pct', 'remaining_pct', 'payload'}
        if not fields:
            return
        if set(fields) - allowed:
            raise ValueError('Campo no permitido en update_trade')
        with self._db() as c:
            self._execute(c, 'UPDATE ttr_live_trades SET ' + ','.join(f'{k}=?' for k in fields) + ' WHERE id=?', (*fields.values(), trade_id))

    def history(self, symbol, limit=50):
        with self._db() as c:
            return [dict(x) for x in self._execute(c, 'SELECT * FROM ttr_live_trades WHERE symbol=? ORDER BY id DESC LIMIT ?', (symbol, limit)).fetchall()]

    def events(self, symbol, limit=100):
        with self._db() as c:
            return [dict(x) for x in self._execute(c, 'SELECT * FROM ttr_live_events WHERE symbol=? ORDER BY id DESC LIMIT ?', (symbol, limit)).fetchall()]

    def status(self):
        with self._db() as c:
            return [dict(x) for x in self._execute(c, 'SELECT symbol,enabled,config,state,last_bar_time,updated_at FROM ttr_live_config ORDER BY symbol').fetchall()]

    def all_history(self, limit=100):
        with self._db() as c:
            return [dict(x) for x in self._execute(c, 'SELECT * FROM ttr_live_trades ORDER BY entry_time DESC,id DESC LIMIT ?', (limit,)).fetchall()]

    def performance(self):
        with self._db() as c:
            trades=[dict(x) for x in self._execute(c, 'SELECT * FROM ttr_live_trades ORDER BY entry_time').fetchall()]
            configs=[dict(x) for x in self._execute(c, 'SELECT symbol,state FROM ttr_live_config').fetchall()]
        market={}
        for row in configs:
            try: market[row['symbol']]=(json.loads(row.get('state') or '{}').get('market') or {}).get('last_price')
            except Exception: market[row['symbol']]=None
        def metrics(items):
            closed=[t for t in items if t['status']=='CLOSED']
            opened=[t for t in items if t['status']=='OPEN']
            pnls=[float(t.get('realized_pnl_pct') or 0) for t in closed]
            realized_values=[float(t.get('realized_pnl_pct') or 0) for t in items]
            wins=sum(x>0 for x in pnls); losses=sum(x<0 for x in pnls)
            gross_profit=sum(x for x in realized_values if x>0); gross_loss=abs(sum(x for x in realized_values if x<0))
            floating=[]
            for t in opened:
                px=market.get(t['symbol'])
                if px and t.get('entry_price'):
                    direction=1 if t['side']=='buy' else -1
                    floating.append((float(px)/float(t['entry_price'])-1)*100*direction*float(t.get('remaining_pct',100))/100)
            return {'total':len(items),'closed':len(closed),'open':len(opened),'wins':wins,'losses':losses,
                'win_rate':(wins/len(closed)*100 if closed else 0),'realized_pnl_pct':sum(realized_values),
                'gross_profit_pct':gross_profit,'gross_loss_pct':gross_loss,
                'profit_factor':(gross_profit/gross_loss if gross_loss else (float('inf') if gross_profit else 0)),
                'avg_pnl_pct':(sum(pnls)/len(closed) if closed else 0),
                'best_pct':max(pnls) if pnls else 0,'worst_pct':min(pnls) if pnls else 0,
                'floating_pnl_pct':sum(floating),'net_pnl_pct':sum(realized_values)+sum(floating),'open_marked':len(floating)}
        by_symbol={}
        for t in trades: by_symbol.setdefault(t['symbol'],[]).append(t)
        return {'overall':metrics(trades),'by_symbol':{k:metrics(v) for k,v in sorted(by_symbol.items())},'generated_at':int(time.time()*1000)}

    def set_health(self, component, category, status, message=None):
        now=int(time.time()*1000)
        with self._db() as c:
            if status=='OK':
                self._execute(c, 'INSERT INTO ttr_live_health(component,category,status,message,first_seen,last_seen,occurrences,last_success) VALUES(?,?,?,?,?,?,1,?) ON CONFLICT(component) DO UPDATE SET category=excluded.category,status=excluded.status,message=NULL,last_seen=excluded.last_seen,last_success=excluded.last_success', (component,category,status,None,now,now,now))
            else:
                self._execute(c, 'INSERT INTO ttr_live_health(component,category,status,message,first_seen,last_seen,occurrences,last_success) VALUES(?,?,?,?,?,?,1,NULL) ON CONFLICT(component) DO UPDATE SET category=excluded.category,status=excluded.status,message=excluded.message,last_seen=excluded.last_seen,occurrences=ttr_live_health.occurrences+1', (component,category,status,(message or '')[:500],now,now))

    def health(self):
        with self._db() as c:
            return [dict(x) for x in self._execute(c, 'SELECT * FROM ttr_live_health ORDER BY status DESC,component').fetchall()]
STORE=TTRLiveStore()

async def _health_transition(context, component, category, status, message=None):
    """Persist only state transitions; the supervisor runs every second."""
    cache=context.application.bot_data.setdefault('ttr_health_cache',{})
    signature=(category,status,message if status!='OK' else None)
    if cache.get(component)==signature: return
    await asyncio.to_thread(STORE.set_health,component,category,status,message)
    cache[component]=signature

def _closed_candles(client,symbol):
    """Fetch only the recent 15m history and discard the still-open candle."""
    now=int(time.time()*1000)
    rows=client.get_kline(symbol,'15m',limit=120)
    return [x for x in rows if int(x.get('time',0))+TF_MS <= now]


async def _safe_notify(context, chat_id, message, *, attempts=2):
    """Send serially and honor Telegram's bot-wide RetryAfter flood-control window."""
    data = context.application.bot_data
    lock = data.setdefault('ttr_notification_lock', asyncio.Lock())
    async with lock:
        blocked_until = float(data.get('ttr_telegram_blocked_until', 0) or 0)
        if time.monotonic() < blocked_until:
            return False
        for attempt in range(attempts):
            try:
                await asyncio.wait_for(context.bot.send_message(chat_id=chat_id, text=message), timeout=8)
                try: await asyncio.to_thread(STORE.set_health,'telegram','TELEGRAM','OK')
                except Exception: pass
                return True
            except Exception as exc:
                # python-telegram-bot raises telegram.error.RetryAfter and exposes
                # retry_after as seconds (int/float or timedelta depending on version).
                retry_after = getattr(exc, 'retry_after', None)
                if retry_after is not None:
                    if hasattr(retry_after, 'total_seconds'):
                        retry_after = retry_after.total_seconds()
                    delay = max(1.0, float(retry_after))
                    data['ttr_telegram_blocked_until'] = time.monotonic() + delay
                    try: await asyncio.to_thread(STORE.set_health,'telegram','TELEGRAM','RATE_LIMITED',f'RetryAfter {delay:.0f}s')
                    except Exception: pass
                    logging.getLogger(__name__).error(
                        'Telegram flood control: pausing all TTR notifications for %.0f seconds', delay)
                    return False
                try: await asyncio.to_thread(STORE.set_health,'telegram','TELEGRAM','ERROR',str(exc))
                except Exception: pass
                logging.getLogger(__name__).warning(
                    'TTR Telegram notification failed (attempt %s/%s) for chat %s: %s',
                    attempt + 1, attempts, chat_id, exc)
                if attempt + 1 < attempts:
                    await asyncio.sleep(1.0 * (attempt + 1))
        return False


def _notify_trade_event(context, chat_id, params, event, message):
    key = {'stop':'notify_stop', 'tp':'notify_tp', 'open':'notify_open', 'close':'notify_close'}.get(event)
    if key is None or bool(params.get(key, True)):
        _queue_notify(context, chat_id, message)


def _queue_notify(context, chat_id, message):
    """Queue notifications, dropping them while Telegram's global cooldown is active."""
    data = context.application.bot_data
    if time.monotonic() < float(data.get('ttr_telegram_blocked_until', 0) or 0):
        return
    tasks=data.setdefault('ttr_notification_tasks',set())
    task=asyncio.create_task(_safe_notify(context,chat_id,message))
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def monitor_symbol(context,symbol,client,store=STORE,ws_feed=None):
    async def db_call(method, *args, **kwargs):
        # PostgreSQL connection establishment and queries must not block PTB's event loop.
        try:
            return await asyncio.to_thread(getattr(store, method), *args, **kwargs)
        except Exception as exc:
            raise TTRDatabaseError(f'{method}: {exc}') from exc

    cfg=await db_call('get_config',symbol)
    if not cfg or not cfg['enabled']: return
    params=json.loads(cfg['config']); state=json.loads(cfg['state'] or '{}'); pos=state.get('position')
    # Reconcile the durable ledger once per process/symbol. This still repairs
    # crashes after a restart, without opening another PostgreSQL connection on
    # every one-second market-data tick.
    now=int(time.time()*1000)
    reconciled=context.application.bot_data.setdefault('ttr_reconciled_symbols',set())
    if symbol not in reconciled:
        open_rows=await db_call('open_trades',symbol)
        if open_rows:
            # Always reconcile the in-memory snapshot against the durable trade ledger.
            # This also repairs a crash between the trade commit and state snapshot.
            row=open_rows[0]
            payload=json.loads(row.get('payload') or '{}')
            if int(payload.get('engine_version',0)) != PINE_ENGINE_VERSION:
                # A legacy live-paper position was opened with the previous
                # approximate PSAR-stop engine. Do not silently treat it as a
                # Pine-v6 position; close its paper ledger at its entry reference.
                await db_call('update_trade',row['id'],exit_time=now,exit_price=row['entry_price'],
                    exit_reason='ENGINE_MIGRATION_RESET',status='CLOSED',
                    remaining_pct=0,stop_price=None)
                await db_call('event',symbol,'ENGINE_MIGRATION_RESET',row['side'],
                    row['entry_price'],now,{'from_engine':payload.get('engine_version',0),'to_engine':PINE_ENGINE_VERSION})
                pos=None
                state['position']=None
            else:
                pos=restore_position_from_trade(row)
                await db_call('update_trade',row['id'],stop_price=None)
                state['position']=pos
            # Resume at the current closed-candle boundary. No TP, RE, reversal
            # or entry signals from the service downtime are replayed.
            resume_bar=resume_bar_boundary()
            await db_call('save_state',symbol,state,last_bar_time=resume_bar)
            cfg['last_bar_time']=resume_bar
            await db_call('update_trade',row['id'],payload=json.dumps({'engine_version':PINE_ENGINE_VERSION,'position':pos,
                'avg_entry':pos['avg_entry'],'tp_count':pos['tp_count'],'tp_marked_fractals':pos['tp_marked_fractals'],
                're_marked_fractals':pos['re_marked_fractals'],'last_tp':pos['last_tp'],'last_re':pos['last_re']}))
        elif pos:
            # A stale config snapshot must not resurrect a trade already closed in SQL.
            pos=None
            state['position']=None
            await db_call('save_state',symbol,state)
        reconciled.add(symbol)
    now=int(time.time()*1000)
    last_quote=int((state.get('market') or {}).get('quote_time',0))
    market_changed=False
    ws_ticks=ws_feed.drain(symbol) if ws_feed and params.get('feed_mode','websocket')=='websocket' else []
    ws_quote=ws_ticks[-1] if ws_ticks else (ws_feed.get(symbol) if ws_feed and params.get('feed_mode','websocket')=='websocket' else None)
    if ws_quote:
        price=float(ws_quote['price']); quote_time=int(ws_quote['exchange_time']); source='websocket_trade'
        last_exchange=int((state.get('market') or {}).get('last_exchange_time',0))
        ws_ticks=[tick for tick in ws_ticks if int(tick['exchange_time'])>last_exchange]
        if not ws_ticks: price=None
    else:
        source='rest_fallback' if params.get('feed_mode','websocket')=='websocket' else 'rest'
        if last_quote and now-last_quote < poll_seconds(params)*1000:
            price=None
        else:
            ticker=await asyncio.to_thread(client.get_price, symbol)
            price=float((ticker or {}).get('lastPrice') or (ticker or {}).get('last') or 0) or None
            quote_time=now
    if price is not None and price>0:
        now=int(time.time()*1000); chat_id=cfg['chat_id']
        state.setdefault('market', {})
        state['market'].update({'last_price':price,'quote_time':now,'poll_seconds':poll_seconds(params),'source':source,'feed_health':ws_feed.health(symbol) if ws_feed else {'connected':False}})
        if source=='websocket_trade': state['market']['last_exchange_time']=quote_time
        market_changed=True
    else:
        # Still evaluate a newly closed 15m bar even if there was no new quote.
        price=None; chat_id=cfg['chat_id']
    # The Pine TTR indicator has no stop-loss or PSAR trailing stop.
    # Its PSAR lagging line is used only for entry/reversal cross signals.
    # Do not download 120 candles on every supervisor tick. A new 15m bar is
    # eligible only after the current interval has ended and its open-time is
    # newer than the last bar committed to PostgreSQL.
    now_ms=int(time.time()*1000)
    latest_closed_start=(now_ms//TF_MS)*TF_MS-TF_MS
    last_processed=int(cfg.get('last_bar_time') or 0)
    retry_after=int(state.get('candles_retry_after',0) or 0)
    if latest_closed_start<=last_processed or (not last_processed and now_ms<retry_after):
        if market_changed:
            await db_call('save_state',symbol,state)
        return
    candles=await asyncio.to_thread(_closed_candles,client,symbol)
    if len(candles)<20:
        # Avoid hammering REST while initial candle history is unavailable.
        state['candles_retry_after']=now_ms+60_000
        await db_call('save_state',symbol,state)
        return
    state.pop('candles_retry_after',None)
    bar=candles[-1]; bar_time=int(bar['time'])
    if bar_time<=last_processed:
        if market_changed:
            await db_call('save_state',symbol,state)
        return
    highs=[float(x['high']) for x in candles]; lows=[float(x['low']) for x in candles]
    closes=[float(x['close']) for x in candles]; opens=[float(x['open']) for x in candles]; times=[int(x['time']) for x in candles]
    from ttr_pine import sar as pine_sar, fractal_snapshots, cross_signal, ttr_bar_action
    sar_values, lag_values, fast_values = pine_sar(
        highs, lows, closes, float(params.get('psar_start',.02)),
        float(params.get('psar_increment',.02)), float(params.get('psar_max',.025)))
    i=len(candles)-1
    new_psar=float(sar_values[i]) if math.isfinite(sar_values[i]) else None
    lagging=float(lag_values[i]) if math.isfinite(lag_values[i]) else None
    fast=float(fast_values[i]) if math.isfinite(fast_values[i]) else None
    state['indicator']={'timeframe':'15m','candle_time':bar_time,'candle_close':closes[i],
        'psar':new_psar,'psar_lagging':lagging,'psar_fast':fast,
        'updated_at':now,'source':'Tac.sar Pine v6'}
    # Replay every unprocessed closed candle in order. This is important after a
    # worker restart: entry/reversal and TP/RE state must not jump straight to the
    # newest candle and silently lose intervening Pine signals.
    previous_bar=int(cfg.get('last_bar_time') or 0)
    if previous_bar:
        first_idx=next((k for k,t in enumerate(times) if int(t)>previous_bar),i)
    else:
        # A fresh subscription starts tracking from the latest completed bar;
        # do not backfill historical trades before the subscription existed.
        first_idx=i
    hh_by_bar,ll_by_bar=fractal_snapshots(
        highs,lows,opens,closes,times,
        hh_lookback=int(params.get('frac_lb_high',2)),
        ll_lookback=int(params.get('frac_lb_low',2)))
    tp_values=[float(x) for x in params.get('tp_percentages',[25,25,25,25])]
    for k in range(max(1,first_idx),i+1):
        cross=cross_signal(highs,lows,lag_values,k)
        allow_buy=params.get('pos_dir','Buy & Sell') in ('Buy & Sell','Buy Only')
        allow_sell=params.get('pos_dir','Buy & Sell') in ('Buy & Sell','Sell Only')
        desired='buy' if cross==1 and allow_buy else ('sell' if cross==-1 and allow_sell else None)
        exits_current=bool(pos and ((pos['side']=='buy' and cross==-1) or (pos['side']=='sell' and cross==1)))
        if exits_current:
            old_side=pos['side']
            raw=((closes[k]/pos['avg_entry']-1)*100)*(1 if old_side=='buy' else -1)
            realized=float(pos.get('realized_pnl_pct',0))+raw*float(pos.get('remaining_pct',100))/100
            await db_call('update_trade',pos['trade_id'],exit_time=int(times[k]),exit_price=float(closes[k]),
                exit_reason='REVERSAL_15M',status='CLOSED',realized_pnl_pct=realized,remaining_pct=0)
            await db_call('event',symbol,'REVERSAL_EXIT',old_side,float(closes[k]),int(times[k]),{})
            _notify_trade_event(context,chat_id,params,'close',f'🔒 TTR {symbol} · CIERRE por reversión\n{old_side.upper()} @ {closes[k]:.8g} · PnL realizado {realized:+.4f}%.')
            pos=None
        elif pos and desired==pos['side']:
            old_side=pos['side']
            raw=((closes[k]/pos['avg_entry']-1)*100)*(1 if old_side=='buy' else -1)
            realized=float(pos.get('realized_pnl_pct',0))+raw*float(pos.get('remaining_pct',100))/100
            await db_call('update_trade',pos['trade_id'],exit_time=int(times[k]),exit_price=float(closes[k]),
                exit_reason='PINE_ENTRY_RESET',status='CLOSED',realized_pnl_pct=realized,remaining_pct=0)
            await db_call('event',symbol,'ENTRY_RESET',old_side,float(closes[k]),int(times[k]),{})
            _notify_trade_event(context,chat_id,params,'close',f'🔒 TTR {symbol} · CIERRE por nueva señal\n{old_side.upper()} @ {closes[k]:.8g} · PnL realizado {realized:+.4f}%.')
            pos=None
        if desired:
            entry_price=float(lag_values[k]) if math.isfinite(lag_values[k]) else float(closes[k])
            tid=await db_call('open_trade',symbol,desired,int(times[k]),entry_price,None)
            pos={'side':desired,'trade_id':tid,'entry_time':int(times[k]),'entry_price':entry_price,
                 'avg_entry':entry_price,'remaining_pct':100.0,'realized_pnl_pct':0.0,'stop':None,
                 'tp_count':0,'tp_marked_fractals':[],'re_marked_fractals':[],'last_tp':None,'last_re':None,'engine_version':PINE_ENGINE_VERSION}
            await db_call('update_trade',tid,payload=json.dumps({'engine_version':PINE_ENGINE_VERSION,'position':pos}))
            await db_call('event',symbol,'ENTRY',desired,entry_price,int(times[k]),
                          {'signal':'PSAR_LAGGING_CROSS','psar_lagging':entry_price})
            _notify_trade_event(context,chat_id,params,'open',f'📍 TTR {symbol} · ENTRADA {desired.upper()}\\nVela 15m cerrada: {datetime.fromtimestamp(int(times[k])/1000,timezone.utc):%Y-%m-%d %H:%M UTC}\\nPrecio de referencia Pine: {entry_price:.8g} (lagging PSAR).')
            continue
        # Pine does not evaluate TP/RE on a candle with a posExit.
        if exits_current or not pos or int(times[k])<=int(pos.get('entry_time',0)):
            continue
        pos.setdefault('tp_count',0); pos.setdefault('tp_marked_fractals',[])
        pos.setdefault('re_marked_fractals',[]); pos.setdefault('last_tp',None); pos.setdefault('last_re',None)
        side=pos['side']
        action,fractal=ttr_bar_action(
            k,side=side,entry_price=float(pos['entry_price']),
            entry_time=int(pos['entry_time']),closes=closes,opens=opens,
            lagging=lag_values,times=times,hh_by_bar=hh_by_bar,ll_by_bar=ll_by_bar,
            frac_lb_high=int(params.get('frac_lb_high',2)),frac_lb_low=int(params.get('frac_lb_low',2)),
            sig_re_min_proximity=float(params.get('sig_re_min_proximity',0)),
            sig_re_buy_no_tp=bool(params.get('sig_re_buy_no_tp',False)),
            sig_re_sell_no_tp=bool(params.get('sig_re_sell_no_tp',False)),
            marked_tp=set(int(x) for x in pos['tp_marked_fractals']),
            marked_re=set(int(x) for x in pos['re_marked_fractals']),
            last_tp=pos.get('last_tp'),last_re=pos.get('last_re'))
        if not action:
            continue
        if action=='TP':
            marked=set(int(x) for x in pos['tp_marked_fractals']); marked.add(int(fractal.when))
            pos['tp_marked_fractals']=sorted(marked)
            slot=int(pos.get('tp_count',0))
            pct=min(tp_values[slot],float(pos.get('remaining_pct',100))) if slot<len(tp_values) else 0.0
            pos['last_tp']=float(closes[k]); pos['tp_count']=slot+1
            if pct>0:
                raw=((float(closes[k])/float(pos['avg_entry'])-1)*100)*(1 if side=='buy' else -1)
                pos['realized_pnl_pct']=float(pos.get('realized_pnl_pct',0))+raw*pct/100
                pos['remaining_pct']=max(0,float(pos.get('remaining_pct',100))-pct)
                await db_call('update_trade',pos['trade_id'],realized_pnl_pct=pos['realized_pnl_pct'],
                    remaining_pct=pos['remaining_pct'],payload=json.dumps({key:pos.get(key) for key in
                    ('engine_version','avg_entry','tp_count','tp_marked_fractals','re_marked_fractals','last_tp','last_re')}))
                await db_call('event',symbol,'TP',side,float(closes[k]),int(times[k]),
                    {'tp_number':slot+1,'percent_closed':pct,'remaining_pct':pos['remaining_pct'],'fractal_time':int(fractal.when)})
                _notify_trade_event(context,chat_id,params,'tp',f'🎯 TTR {symbol} · TP {slot+1}\\n{side.upper()} · vela {datetime.fromtimestamp(int(times[k])/1000,timezone.utc):%Y-%m-%d %H:%M UTC} · precio {closes[k]:.8g} · cierre simulado {pct:.2f}% · restante {pos["remaining_pct"]:.2f}%.')
            else:
                await db_call('event',symbol,'TP_SIGNAL_NO_CLOSE',side,float(closes[k]),int(times[k]),
                    {'tp_number':slot+1,'percent_closed':0,'remaining_pct':pos['remaining_pct'],'fractal_time':int(fractal.when)})
                _notify_trade_event(context,chat_id,params,'tp',f'🎯 TTR {symbol} · señal TP {slot+1} @ {closes[k]:.8g} (sin reducción adicional configurada).')
        else:
            marked=set(int(x) for x in pos['re_marked_fractals']); marked.add(int(fractal.when))
            pos['re_marked_fractals']=sorted(marked); pos['last_re']=float(closes[k])
            await db_call('event',symbol,'RE',side,float(closes[k]),int(times[k]),
                {'entry_enabled':False,'fractal_time':int(fractal.when)})
            _queue_notify(context,chat_id,f'↪️ TTR {symbol} · señal RE {side.upper()} @ {closes[k]:.8g} · RE solo informativa; Pine no aumenta exposición.')
        if pos:
            await db_call('update_trade',pos['trade_id'],payload=json.dumps({'engine_version':PINE_ENGINE_VERSION,'position':pos, **{key:pos.get(key) for key in
                ('avg_entry','tp_count','tp_marked_fractals','re_marked_fractals','last_tp','last_re')}}))
    state['position']=pos
    if pos:
        await db_call('update_trade',pos['trade_id'],payload=json.dumps({'engine_version':PINE_ENGINE_VERSION,'position':pos, **{key:pos.get(key) for key in ('avg_entry','tp_count','tp_marked_fractals','re_marked_fractals','last_tp','last_re')}}))
    await db_call('save_state',symbol,state,last_bar_time=bar_time)

async def monitor_job(context):
    from bot import bitunix
    from ttr_websocket import BitunixTickerFeed
    feed=context.application.bot_data.get('ttr_ws_feed')
    if feed is None:
        feed=BitunixTickerFeed(stale_after=float(os.environ.get('TTR_WS_STALE_SECONDS','10')))
        context.application.bot_data['ttr_ws_feed']=feed
    try:
        configs=await asyncio.to_thread(STORE.configs)
        await _health_transition(context,'database','DATABASE','OK')
    except Exception as exc:
        logging.getLogger(__name__).exception('TTR supervisor: database unavailable')
        context.application.bot_data['ttr_supervisor_last_error']=str(exc)[:300]
        return
    context.application.bot_data['ttr_supervisor_last_heartbeat']=time.time()
    await _health_transition(context,'worker','WORKER','OK')
    for cfg in configs:
        params=json.loads(cfg.get('config') or '{}')
        if params.get('feed_mode','websocket')=='websocket': feed.ensure(cfg['symbol'])
        errors=context.application.bot_data.setdefault('ttr_monitor_errors',{})
        try:
            await monitor_symbol(context,cfg['symbol'],bitunix,ws_feed=feed)
            errors.pop(cfg['symbol'],None)
            await _health_transition(context,f"symbol:{cfg['symbol']}",'MONITOR','OK')
            await _health_transition(context,f"bitunix:{cfg['symbol']}",'BITUNIX','OK')
            if feed and params.get('feed_mode','websocket')=='websocket':
                ws=feed.health(cfg['symbol'])
                await _health_transition(context,f"websocket:{cfg['symbol']}",'WEBSOCKET','OK' if ws.get('connected') and ws.get('fresh') else 'DEGRADED',None if ws.get('connected') and ws.get('fresh') else str(ws.get('last_error') or 'Sin datos recientes'))
        except Exception as exc:
            category='DATABASE' if isinstance(exc,TTRDatabaseError) else ('BITUNIX' if any(x in type(exc).__name__.lower()+' '+str(exc).lower() for x in ('bitunix','http','timeout','connection','request','exchange')) else 'MONITOR')
            component='database' if category=='DATABASE' else (f'bitunix:{cfg["symbol"]}' if category=='BITUNIX' else f'symbol:{cfg["symbol"]}')
            try: await _health_transition(context,component,category,'ERROR',str(exc))
            except Exception: logging.getLogger(__name__).exception('Could not persist TTR health incident')
            signature=f'{type(exc).__name__}: {exc}'
            # A persistent fault is logged/notified once, not once per polling tick.
            if errors.get(cfg['symbol']) != signature:
                import logging
                logging.getLogger(__name__).exception('TTR live monitor error %s',cfg['symbol'])
                _queue_notify(context,cfg['chat_id'],f'⚠️ TTR {cfg["symbol"]}: error en monitor: {str(exc)[:250]}')
                errors[cfg['symbol']]=signature
