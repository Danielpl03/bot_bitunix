"""TTR paper-trading monitor: 15m closed-bar indicator + 1m price/stop watcher.
No exchange orders are ever sent. State and event history are persisted in SQLite.
"""
from __future__ import annotations
import json, os, sqlite3, time, math
from datetime import datetime, timezone
from pathlib import Path
from backtest_trend_trader_remastered import _psar

DB_PATH = os.environ.get('TTR_LIVE_DB', 'ttr_live.sqlite3')
TF_MS = 15 * 60 * 1000
DEFAULT_POLL_SECONDS = max(2, int(os.environ.get('TTR_POLL_SECONDS', '5')))

def poll_seconds(config=None):
    config = config or {}
    return max(2, min(30, int(config.get('poll_seconds', DEFAULT_POLL_SECONDS))))

class TTRLiveStore:
    def __init__(self, path=DB_PATH):
        self.path=path
        with self._db() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS ttr_live_config (
              symbol TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
              config TEXT NOT NULL, last_bar_time INTEGER, state TEXT NOT NULL DEFAULT '{}', updated_at INTEGER NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS ttr_live_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, event_time INTEGER NOT NULL,
              event_type TEXT NOT NULL, side TEXT, price REAL, payload TEXT NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS ttr_live_trades (
              id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, side TEXT NOT NULL,
              entry_time INTEGER NOT NULL, entry_price REAL NOT NULL, exit_time INTEGER, exit_price REAL,
              exit_reason TEXT, status TEXT NOT NULL, stop_price REAL, realized_pnl_pct REAL NOT NULL DEFAULT 0,
              remaining_pct REAL NOT NULL DEFAULT 100, payload TEXT NOT NULL DEFAULT '{}')''')
    def _db(self):
        c=sqlite3.connect(self.path, timeout=20); c.row_factory=sqlite3.Row; return c
    def subscribe(self,symbol,chat_id,config):
        with self._db() as c:
            c.execute('INSERT INTO ttr_live_config(symbol,chat_id,enabled,config,last_bar_time,state,updated_at) VALUES(?,?,1,?,NULL,?,?) ON CONFLICT(symbol) DO UPDATE SET chat_id=excluded.chat_id,enabled=1,config=excluded.config,updated_at=excluded.updated_at',
                      (symbol,chat_id,json.dumps(config),json.dumps({'position':None}),int(time.time()*1000)))
    def unsubscribe(self,symbol):
        with self._db() as c: c.execute('UPDATE ttr_live_config SET enabled=0,updated_at=? WHERE symbol=?',(int(time.time()*1000),symbol))
    def configs(self):
        with self._db() as c: return [dict(x) for x in c.execute('SELECT * FROM ttr_live_config WHERE enabled=1')]
    def get_config(self,symbol):
        with self._db() as c:
            x=c.execute('SELECT * FROM ttr_live_config WHERE symbol=?',(symbol,)).fetchone(); return dict(x) if x else None
    def save_state(self,symbol,state,last_bar_time=None):
        with self._db() as c:
            if last_bar_time is None: c.execute('UPDATE ttr_live_config SET state=?,updated_at=? WHERE symbol=?',(json.dumps(state),int(time.time()*1000),symbol))
            else: c.execute('UPDATE ttr_live_config SET state=?,last_bar_time=?,updated_at=? WHERE symbol=?',(json.dumps(state),last_bar_time,int(time.time()*1000),symbol))
    def event(self,symbol,etype,side,price,ts,payload=None):
        with self._db() as c: c.execute('INSERT INTO ttr_live_events(symbol,event_time,event_type,side,price,payload) VALUES(?,?,?,?,?,?)',(symbol,ts,etype,side,price,json.dumps(payload or {})))
    def open_trade(self,symbol,side,ts,price,stop):
        with self._db() as c:
            cur=c.execute('INSERT INTO ttr_live_trades(symbol,side,entry_time,entry_price,status,stop_price) VALUES(?,?,?,?,?,?)',(symbol,side,ts,price,'OPEN',stop)); return cur.lastrowid
    def update_trade(self,trade_id,**fields):
        if not fields:return
        with self._db() as c: c.execute('UPDATE ttr_live_trades SET '+','.join(f'{k}=?' for k in fields)+' WHERE id=?',(*fields.values(),trade_id))
    def history(self,symbol,limit=50):
        with self._db() as c: return [dict(x) for x in c.execute('SELECT * FROM ttr_live_trades WHERE symbol=? ORDER BY id DESC LIMIT ?',(symbol,limit))]
    def events(self,symbol,limit=100):
        with self._db() as c: return [dict(x) for x in c.execute('SELECT * FROM ttr_live_events WHERE symbol=? ORDER BY id DESC LIMIT ?',(symbol,limit))]
    def status(self):
        with self._db() as c: return [dict(x) for x in c.execute('SELECT symbol,enabled,config,state,last_bar_time,updated_at FROM ttr_live_config ORDER BY symbol')]

STORE=TTRLiveStore()

def _closed_candles(client,symbol):
    now=int(time.time()*1000); rows=client.get_kline(symbol,'15m',limit=120)
    return [x for x in rows if int(x.get('time',0))+TF_MS <= now]

def _notify(bot,chat_id,text):
    import asyncio
    return bot.send_message(chat_id=chat_id,text=text)

async def monitor_symbol(context,symbol,client,store=STORE,ws_feed=None):
    cfg=store.get_config(symbol)
    if not cfg or not cfg['enabled']: return
    params=json.loads(cfg['config']); state=json.loads(cfg['state'] or '{}'); pos=state.get('position')
    now=int(time.time()*1000)
    last_quote=int((state.get('market') or {}).get('quote_time',0))
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
            ticker=client.get_price(symbol)
            price=float((ticker or {}).get('lastPrice') or (ticker or {}).get('last') or 0) or None
            quote_time=now
    if price is not None and price>0:
        now=int(time.time()*1000); chat_id=cfg['chat_id']
        state.setdefault('market', {})
        state['market'].update({'last_price':price,'quote_time':now,'poll_seconds':poll_seconds(params),'source':source,'feed_health':ws_feed.health(symbol) if ws_feed else {'connected':False}})
        if source=='websocket_trade': state['market']['last_exchange_time']=quote_time
    else:
        # Still evaluate a newly closed 15m bar even if there was no new quote.
        price=None; chat_id=cfg['chat_id']
    # Check the fixed 15m PSAR stop every minute. Crossing closes and paper-reverses immediately.
    stop_ticks=ws_ticks if source=='websocket_trade' and ws_ticks else ([{'price':price,'exchange_time':now*1,'source':source}] if price is not None else [])
    for tick in stop_ticks:
        tick_price=float(tick['price']); tick_time=int(tick.get('exchange_time') or now)
        if not pos or pos.get('stop') is None: continue
        stop=float(pos['stop']); hit=(pos['side']=='buy' and tick_price<=stop) or (pos['side']=='sell' and tick_price>=stop)
        if hit:
            side=pos['side']; pnl=((tick_price/pos['avg_entry']-1)*100)*(1 if side=='buy' else -1)
            tid=pos['trade_id']; store.update_trade(tid,exit_time=tick_time,exit_price=tick_price,exit_reason='SL_PSAR_INTRABAR',status='CLOSED',realized_pnl_pct=float(pos.get('realized_pnl_pct',0))+pnl*float(pos.get('remaining_pct',100))/100,remaining_pct=0)
            store.event(symbol,'SL',side,tick_price,tick_time,{'stop':stop,'trigger':source,'pnl_pct':pnl})
            reverse='sell' if side=='buy' else 'buy'
            new_id=store.open_trade(symbol,reverse,tick_time,tick_price,None)
            pos={'side':reverse,'trade_id':new_id,'entry_time':tick_time,'entry_price':tick_price,'avg_entry':tick_price,'remaining_pct':100.0,'realized_pnl_pct':0.0,'stop':None}
            store.event(symbol,'REVERSAL_ENTRY',reverse,tick_price,tick_time,{'closed_trade_id':tid})
            await context.bot.send_message(chat_id=chat_id,text=f'🚨 TTR {symbol} · STOP/REVERSIÓN intrabar\nCierre {side.upper()} @ {tick_price:.8g} (PSAR {stop:.8g})\nPnL tramo abierto: {pnl:+.4f}%\nNueva operación simulada: {reverse.upper()} @ {tick_price:.8g}.')
            state['position']=pos; store.save_state(symbol,state)
    # Recalculate indicator only when a new 15m candle has closed.
    candles=_closed_candles(client,symbol)
    if len(candles)<20:
        store.save_state(symbol,state)
        return
    bar=candles[-1]; bar_time=int(bar['time'])
    if cfg.get('last_bar_time') is not None and bar_time<=int(cfg['last_bar_time']):
        store.save_state(symbol,state)
        return
    highs=[float(x['high']) for x in candles]; lows=[float(x['low']) for x in candles]
    closes=[float(x['close']) for x in candles]; opens=[float(x['open']) for x in candles]; times=[int(x['time']) for x in candles]
    sar=_psar(highs,lows,float(params.get('psar_start',.02)),float(params.get('psar_increment',.02)),float(params.get('psar_max',.025)))
    i=len(candles)-1; new_stop=float(sar[i]) if math.isfinite(sar[i]) else None
    lagging=float(sar[i-1]) if i>=1 and math.isfinite(sar[i-1]) else None
    state['indicator']={'timeframe':'15m','candle_time':bar_time,'candle_close':closes[i],
        'psar':new_stop,'psar_lagging':lagging,'psar_fast':None,
        'updated_at':now,'source':'last_closed_15m_candle'}
    # Stop ratchets only in the protective direction and becomes active after this candle closes.
    if pos and new_stop is not None:
        old=pos.get('stop')
        candidate=new_stop if old is None else (max(float(old),new_stop) if pos['side']=='buy' else min(float(old),new_stop))
        changed=old is None or not math.isclose(float(old),candidate,rel_tol=1e-12,abs_tol=1e-12)
        pos['stop']=candidate
        store.update_trade(pos['trade_id'],stop_price=pos['stop'])
        if changed:
            store.event(symbol,'STOP_UPDATE',pos['side'],pos['stop'],bar_time,{'indicator':'PSAR','activation':'next_15m_candle'})
            await context.bot.send_message(chat_id=chat_id,text=f'🛡 TTR {symbol} · Stop PSAR actualizado al cierre 15m: {pos["stop"]:.8g} ({pos["side"].upper()}).')
    # Main signal uses the Pine script's lagging-PSAR cross definition.
    if i>=2 and math.isfinite(sar[i-1]) and math.isfinite(sar[i-2]):
        lag_prev=float(sar[i-2]); lag_now=float(sar[i-1])
        cross_high=highs[i]>lag_now and highs[i-1]<=lag_prev
        cross_low=lows[i]<lag_now and lows[i-1]>=lag_prev
        cross=1 if lag_prev>highs[i-1] and cross_high else (-1 if lag_prev<lows[i-1] and cross_low else 0)
        allow_buy=params.get('pos_dir','Buy & Sell') in ('Buy & Sell','Buy Only')
        allow_sell=params.get('pos_dir','Buy & Sell') in ('Buy & Sell','Sell Only')
        desired='buy' if cross==1 and allow_buy else ('sell' if cross==-1 and allow_sell else None)
        if desired and (not pos or pos['side']!=desired):
            if pos:
                store.update_trade(pos['trade_id'],exit_time=bar_time,exit_price=closes[i],exit_reason='REVERSAL_15M',status='CLOSED',realized_pnl_pct=float(pos.get('realized_pnl_pct',0))+((closes[i]/pos['avg_entry']-1)*100*(1 if pos['side']=='buy' else -1))*float(pos.get('remaining_pct',100))/100,remaining_pct=0)
                store.event(symbol,'REVERSAL_EXIT',pos['side'],closes[i],bar_time,{})
            tid=store.open_trade(symbol,desired,bar_time,closes[i],new_stop)
            pos={'side':desired,'trade_id':tid,'entry_time':bar_time,'entry_price':closes[i],'avg_entry':closes[i],'remaining_pct':100.0,'realized_pnl_pct':0.0,'stop':new_stop}
            store.event(symbol,'ENTRY',desired,closes[i],bar_time,{'signal':'PSAR_LAGGING_CROSS','stop_psar':new_stop})
            await context.bot.send_message(chat_id=chat_id,text=f'📍 TTR {symbol} · ENTRADA {desired.upper()}\nVela 15m cerrada: {datetime.fromtimestamp(bar_time/1000,timezone.utc):%Y-%m-%d %H:%M UTC}\nPrecio: {closes[i]:.8g}\nStop PSAR: {new_stop:.8g} (se comprueba cada 1m).')
    # TP/RE are evaluated only on the newly closed 15m candle by the existing Python TTR engine.
    # Its event stream is used for signal classification; no exchange order is sent.
    if pos:
        try:
            from backtest_trend_trader_remastered import run_backtest
            result=run_backtest(closes,highs,lows,opens,times,
                psar_start=float(params.get('psar_start',.02)),psar_increment=float(params.get('psar_increment',.02)),psar_max=float(params.get('psar_max',.025)),
                frac_lb_high=int(params.get('frac_lb_high',2)),frac_lb_low=int(params.get('frac_lb_low',2)),
                pos_dir=params.get('pos_dir','Buy & Sell'),enter_on_reentry=bool(params.get('enter_on_reentry',False)),
                tp_percentages=params.get('tp_percentages',[25,25,25,25]))
            for ev in result.get('events',[]):
                if int(ev.get('time',-1))!=bar_time or ev.get('type') not in ('TP','RE'): continue
                if ev.get('type')=='TP' and pos:
                    tps=params.get('tp_percentages',[25,25,25,25]); done=100.0-float(pos.get('remaining_pct',100)); slot=min(int(done//max(float(tps[0]),1)),len(tps)-1)
                    pct=min(float(tps[slot]),float(pos.get('remaining_pct',100)))
                    raw=((float(ev['price'])/float(pos['avg_entry'])-1)*100)*(1 if pos['side']=='buy' else -1)
                    pos['realized_pnl_pct']=float(pos.get('realized_pnl_pct',0))+raw*pct/100
                    pos['remaining_pct']=max(0,float(pos.get('remaining_pct',100))-pct)
                    store.update_trade(pos['trade_id'],realized_pnl_pct=pos['realized_pnl_pct'],remaining_pct=pos['remaining_pct'])
                    store.event(symbol,'TP',pos['side'],float(ev['price']),bar_time,{'percent_closed':pct,'remaining_pct':pos['remaining_pct']})
                    await context.bot.send_message(chat_id=chat_id,text=f'🎯 TTR {symbol} · TP {slot+1}\n{pos["side"].upper()} · precio {float(ev["price"]):.8g} · cierre simulado {pct:.2f}% · restante {pos["remaining_pct"]:.2f}%.')
                elif ev.get('type')=='RE':
                    store.event(symbol,'RE',pos['side'],float(ev['price']),bar_time,{'entry_enabled':bool(params.get('enter_on_reentry',False))})
                    await context.bot.send_message(chat_id=chat_id,text=f'↪️ TTR {symbol} · señal RE {pos["side"].upper()} @ {float(ev["price"]):.8g} · entrada RE habilitada: {bool(params.get("enter_on_reentry",False))}.')
        except Exception as exc:
            import logging; logging.getLogger(__name__).exception('TTR TP/RE evaluation error %s',symbol)
    state['position']=pos
    store.save_state(symbol,state,last_bar_time=bar_time)

async def monitor_job(context):
    from bot import bitunix
    from ttr_websocket import BitunixTickerFeed
    feed=context.application.bot_data.get('ttr_ws_feed')
    if feed is None:
        feed=BitunixTickerFeed(stale_after=float(os.environ.get('TTR_WS_STALE_SECONDS','10')))
        context.application.bot_data['ttr_ws_feed']=feed
    for cfg in STORE.configs():
        params=json.loads(cfg.get('config') or '{}')
        if params.get('feed_mode','websocket')=='websocket': feed.ensure(cfg['symbol'])
        try: await monitor_symbol(context,cfg['symbol'],bitunix,ws_feed=feed)
        except Exception as exc:
            import logging; logging.getLogger(__name__).exception('TTR live monitor error %s',cfg['symbol'])
            await context.bot.send_message(chat_id=cfg['chat_id'],text=f'⚠️ TTR {cfg["symbol"]}: error en monitor: {str(exc)[:250]}')
