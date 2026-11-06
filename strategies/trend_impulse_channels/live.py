"""Paper Live persistente para Trend Impulse Channels. Nunca envía órdenes."""
from __future__ import annotations
import asyncio, json, time, logging
from ttr_live import STORE as DB
from .indicator import calculate_trend_impulse_channels
from .engine import _realize
log=logging.getLogger(__name__)
TF_MS={'1m':60_000,'3m':180_000,'5m':300_000,'15m':900_000,'30m':1_800_000,'1h':3_600_000,'2h':7_200_000,'4h':14_400_000,'6h':21_600_000,'8h':28_800_000,'12h':43_200_000,'1d':86_400_000}

class TICLiveStore:
 def __init__(self,db=DB):
  self.db=db
  if db.backend=='sqlite': ddl=[
   "CREATE TABLE IF NOT EXISTS tic_live_config(symbol TEXT NOT NULL, interval TEXT NOT NULL, chat_id INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, config TEXT NOT NULL, last_bar_time INTEGER, updated_at INTEGER NOT NULL, PRIMARY KEY(symbol,interval))",
   "CREATE TABLE IF NOT EXISTS tic_live_trades(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT NOT NULL,interval TEXT NOT NULL,side TEXT NOT NULL,entry_time INTEGER NOT NULL,entry_price REAL NOT NULL,stop_price REAL NOT NULL,quantity REAL NOT NULL,remaining_qty REAL NOT NULL,tp1_pct REAL NOT NULL,tp1_time INTEGER,tp1_price REAL,tp2_target REAL,tp2_time INTEGER,tp2_price REAL,exit_time INTEGER,exit_price REAL,status TEXT NOT NULL,pnl_usdt REAL NOT NULL DEFAULT 0,payload TEXT NOT NULL DEFAULT '{}')"]
  else: ddl=[
   "CREATE TABLE IF NOT EXISTS tic_live_config(symbol TEXT NOT NULL, interval TEXT NOT NULL, chat_id BIGINT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, config TEXT NOT NULL, last_bar_time BIGINT, updated_at BIGINT NOT NULL, PRIMARY KEY(symbol,interval))",
   "CREATE TABLE IF NOT EXISTS tic_live_trades(id BIGSERIAL PRIMARY KEY,symbol TEXT NOT NULL,interval TEXT NOT NULL,side TEXT NOT NULL,entry_time BIGINT NOT NULL,entry_price DOUBLE PRECISION NOT NULL,stop_price DOUBLE PRECISION NOT NULL,quantity DOUBLE PRECISION NOT NULL,remaining_qty DOUBLE PRECISION NOT NULL,tp1_pct DOUBLE PRECISION NOT NULL,tp1_time BIGINT,tp1_price DOUBLE PRECISION,tp2_target DOUBLE PRECISION,tp2_time BIGINT,tp2_price DOUBLE PRECISION,exit_time BIGINT,exit_price DOUBLE PRECISION,status TEXT NOT NULL,pnl_usdt DOUBLE PRECISION NOT NULL DEFAULT 0,payload TEXT NOT NULL DEFAULT '{}')"]
  with db._db() as c:
   for q in ddl: db._execute(c,q)
 def subscribe(self,sym,tf,chat,cfg):
  now=int(time.time()*1000); width=TF_MS[tf]; baseline=(now//width)*width-width
  with self.db._db() as c:self.db._execute(c,"INSERT INTO tic_live_config(symbol,interval,chat_id,enabled,config,last_bar_time,updated_at) VALUES(?,?,?,1,?,?,?) ON CONFLICT(symbol,interval) DO UPDATE SET chat_id=excluded.chat_id,enabled=1,config=excluded.config,updated_at=excluded.updated_at",(sym,tf,chat,json.dumps(cfg),baseline,now))
 def unsubscribe(self,sym,tf):
  with self.db._db() as c:self.db._execute(c,"UPDATE tic_live_config SET enabled=0,updated_at=? WHERE symbol=? AND interval=?",(int(time.time()*1000),sym,tf))
 def configs(self):
  with self.db._db() as c:return [dict(x) for x in self.db._execute(c,"SELECT * FROM tic_live_config WHERE enabled=1 OR EXISTS(SELECT 1 FROM tic_live_trades t WHERE t.symbol=tic_live_config.symbol AND t.interval=tic_live_config.interval AND t.status='OPEN')").fetchall()]
 def get_config(self,sym,tf):
  with self.db._db() as c:
   x=self.db._execute(c,'SELECT * FROM tic_live_config WHERE symbol=? AND interval=?',(sym,tf)).fetchone();return dict(x) if x else None
 def open_trade(self,sym,tf,side,ts,entry,stop,qty,tpct):
  with self.db._db() as c:
   q='INSERT INTO tic_live_trades(symbol,interval,side,entry_time,entry_price,stop_price,quantity,remaining_qty,tp1_pct,status) VALUES(?,?,?,?,?,?,?,?,?,?)';a=(sym,tf,side,ts,entry,stop,qty,qty,tpct,'OPEN')
   if self.db.backend=='postgres':return self.db._execute(c,q+' RETURNING id',a).fetchone()['id']
   return self.db._execute(c,q,a).lastrowid
 def open_trades(self,sym,tf):
  with self.db._db() as c:return [dict(x) for x in self.db._execute(c,"SELECT * FROM tic_live_trades WHERE symbol=? AND interval=? AND status='OPEN' ORDER BY id",(sym,tf)).fetchall()]
 def update_trade(self,t):
  with self.db._db() as c:self.db._execute(c,'UPDATE tic_live_trades SET remaining_qty=?,tp1_time=?,tp1_price=?,tp2_target=?,tp2_time=?,tp2_price=?,exit_time=?,exit_price=?,status=?,pnl_usdt=? WHERE id=?',(t['remaining_qty'],t.get('tp1_time'),t.get('tp1_price'),t.get('tp2_target'),t.get('tp2_time'),t.get('tp2_price'),t.get('exit_time'),t.get('exit_price'),t['status'],t['pnl_usdt'],t['id']))
 def commit(self,sym,tf,ts):
  with self.db._db() as c:self.db._execute(c,'UPDATE tic_live_config SET last_bar_time=?,updated_at=? WHERE symbol=? AND interval=?',(ts,int(time.time()*1000),sym,tf))
 def status(self):
  with self.db._db() as c:return [dict(x) for x in self.db._execute(c,'SELECT * FROM tic_live_config ORDER BY symbol,interval').fetchall()]
 def history(self,sym=None):
  with self.db._db() as c:
   q="SELECT * FROM tic_live_trades";a=()
   if sym:q+=' WHERE symbol=?';a=(sym,)
   q+=' ORDER BY id DESC LIMIT 100';return [dict(x) for x in self.db._execute(c,q,a).fetchall()]
STORE=TICLiveStore()

async def monitor_subscription(context,cfg,client,store=STORE):
 sym,tf=cfg['symbol'],cfg['interval']; params=json.loads(cfg['config'] or '{}'); width=TF_MS[tf]
 rows=await asyncio.to_thread(client.get_kline_history,sym,tf,450)
 now=int(time.time()*1000); bars=[x for x in rows if int(x['time'])+width<=now]
 if len(bars)<202:return
 last=int(cfg.get('last_bar_time') or 0); all_bars=list(bars); bars=[b for b in all_bars if int(b['time'])>last]
 if not bars:return
 # Calcular sobre al menos 200 velas previas para conservar ATR y estado del canal.
 ind=calculate_trend_impulse_channels([float(x['open']) for x in all_bars],[float(x['high']) for x in all_bars],[float(x['low']) for x in all_bars],[float(x['close']) for x in all_bars],**params)
 idx_by_time={int(x['time']):i for i,x in enumerate(all_bars)}
 for bar in bars:
  i=idx_by_time.get(int(bar['time']))
  if i is None or i<1:continue
  o,h,l,c=map(float,(bar['open'],bar['high'],bar['low'],bar['close'])); d=ind['direction'][i]; prev=ind['direction'][i-1]
  changed=d in (-1,1) and prev in (-1,1) and d!=prev
  channel_change=d in (-1,1) and d!=prev
  opened=await asyncio.to_thread(store.open_trades,sym,tf)
  for t in opened:
   if (l<=float(t['stop_price']) if t['side']=='LONG' else h>=float(t['stop_price'])):
    px=float(t['stop_price']); qty=float(t['remaining_qty']); signed=(px-float(t['entry_price'])) if t['side']=='LONG' else (float(t['entry_price'])-px)
    t.update(remaining_qty=0,exit_time=int(bar['time']),exit_price=px,status='CLOSED',pnl_usdt=float(t['pnl_usdt'])+signed*qty);await asyncio.to_thread(store.update_trade,t)
    await _notify(context,cfg['chat_id'],f'🛑 TIC {sym} {tf} · SL {t["side"]}\nPrecio {px:.8g} · PnL {t["pnl_usdt"]:+.4f} USDT');continue
   if changed and t.get('tp1_time') is None:
    qty=float(t['quantity'])*float(t['tp1_pct'])/100; px=c; signed=(px-float(t['entry_price'])) if t['side']=='LONG' else (float(t['entry_price'])-px)
    t['pnl_usdt']=float(t['pnl_usdt'])+signed*qty;t['remaining_qty']=max(0,float(t['remaining_qty'])-qty);t['tp1_time']=int(bar['time']);t['tp1_price']=px;t['tp2_target']=ind['upper'][i] if t['side']=='LONG' else ind['lower'][i]
    await asyncio.to_thread(store.update_trade,t);await _notify(context,cfg['chat_id'],f'🎯 TIC {sym} {tf} · TP1 {t["side"]}\nCierre {px:.8g} · {t["tp1_pct"]:g}% liquidado · objetivo TP2 {t["tp2_target"]:.8g}')
   if t.get('tp1_time') is not None and int(t['tp1_time']) < int(bar['time']) and t.get('tp2_target') is not None:
    target=float(t['tp2_target']); hit=h>=target if t['side']=='LONG' else l<=target
    if hit:
     qty=float(t['remaining_qty']);px=target;signed=(px-float(t['entry_price'])) if t['side']=='LONG' else (float(t['entry_price'])-px);t['pnl_usdt']=float(t['pnl_usdt'])+signed*qty;t.update(remaining_qty=0,tp2_time=int(bar['time']),tp2_price=px,exit_time=int(bar['time']),exit_price=px,status='CLOSED');await asyncio.to_thread(store.update_trade,t)
     await _notify(context,cfg['chat_id'],f'🏁 TIC {sym} {tf} · TP2 · OPERACIÓN CERRADA\nPrecio {px:.8g} · PnL {t["pnl_usdt"]:+.4f} USDT')
  opened=await asyncio.to_thread(store.open_trades,sym,tf)
  if cfg.get('enabled') and not opened and not channel_change and i>0:
   upper,lower=ind['upper'][i],ind['lower'][i]
   side=None;stop=None
   if upper is not None and h>upper and c>o:side='LONG';stop=min(l,float(all_bars[i-1]['low']))
   elif lower is not None and l<lower and c<o:side='SHORT';stop=max(h,float(all_bars[i-1]['high']))
   if side:
    entry=c;dist=entry-stop if side=='LONG' else stop-entry;risk=float(params.get('risk_usdt',10))
    if dist>0 and risk>0:
     qty=risk/dist;tid=await asyncio.to_thread(store.open_trade,sym,tf,side,int(bar['time']),entry,stop,qty,float(params['tp1_pct']))
     await _notify(context,cfg['chat_id'],f'📍 TIC {sym} {tf} · ENTRADA {side}\nPrecio {entry:.8g} · SL {stop:.8g} · TP1 {params["tp1_pct"]:g}% · cantidad {qty:.8g}')
  await asyncio.to_thread(store.commit,sym,tf,int(bar['time']))
async def _notify(context,chat,msg):
 try:await context.bot.send_message(chat_id=chat,text=msg)
 except Exception:log.exception('TIC notification failed')
async def monitor_job(context):
 from bot import bitunix
 for cfg in await asyncio.to_thread(STORE.configs):
  try:await monitor_subscription(context,cfg,bitunix)
  except Exception:log.exception('TIC monitor error %s %s',cfg['symbol'],cfg['interval'])
