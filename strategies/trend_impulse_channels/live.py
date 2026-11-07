"""TIC paper-live: persisted indicator warmup/checkpoint and resumable trade ledger."""
from __future__ import annotations
import asyncio, json, time, logging
from ttr_live import STORE as DB
from .indicator import calculate_trend_impulse_channels, initialize_incremental_state, update_incremental_state
log=logging.getLogger(__name__)
TF_MS={'1m':60_000,'3m':180_000,'5m':300_000,'15m':900_000,'30m':1_800_000,'1h':3_600_000,'2h':7_200_000,'4h':14_400_000,'6h':21_600_000,'8h':28_800_000,'12h':43_200_000,'1d':86_400_000}
WARMUP_CANDLES=10000
RECENT_CANDLES=200

def entry_candle_is_eligible(bar_open_time:int, interval_ms:int, activation_time:int, expected_latest_open:int)->bool:
 """Only allow a fresh signal on the just-closed candle after activation."""
 return int(bar_open_time)==int(expected_latest_open) and int(bar_open_time)+int(interval_ms)>int(activation_time)

def breakout_entry_side(open_price, high, low, close, upper, lower):
 """TIC breakout: band penetration AND candle close in breakout direction."""
 if upper is not None and high>upper and close>open_price: return 'LONG'
 if lower is not None and low<lower and close<open_price: return 'SHORT'
 return None

class TICLiveStore:
 def __init__(self,db=DB):
  self.db=db
  if db.backend=='sqlite':
   ddl=[
    "CREATE TABLE IF NOT EXISTS tic_live_config(symbol TEXT NOT NULL, interval TEXT NOT NULL, chat_id INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, config TEXT NOT NULL, last_bar_time INTEGER, updated_at INTEGER NOT NULL, indicator_state TEXT NOT NULL DEFAULT '{}', last_indicator TEXT NOT NULL DEFAULT '{}', activation_time INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(symbol,interval))",
    "CREATE TABLE IF NOT EXISTS tic_live_trades(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT NOT NULL,interval TEXT NOT NULL,side TEXT NOT NULL,entry_time INTEGER NOT NULL,entry_price REAL NOT NULL,stop_price REAL NOT NULL,quantity REAL NOT NULL,remaining_qty REAL NOT NULL,tp1_pct REAL NOT NULL,tp1_time INTEGER,tp1_price REAL,tp2_target REAL,tp2_time INTEGER,tp2_price REAL,exit_time INTEGER,exit_price REAL,status TEXT NOT NULL,pnl_usdt REAL NOT NULL DEFAULT 0,payload TEXT NOT NULL DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS tic_live_events(id INTEGER PRIMARY KEY AUTOINCREMENT,symbol TEXT NOT NULL,interval TEXT NOT NULL,event_time INTEGER NOT NULL,event_type TEXT NOT NULL,side TEXT,price REAL,payload TEXT NOT NULL DEFAULT '{}')"]
  else:
   ddl=[
    "CREATE TABLE IF NOT EXISTS tic_live_config(symbol TEXT NOT NULL, interval TEXT NOT NULL, chat_id BIGINT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, config TEXT NOT NULL, last_bar_time BIGINT, updated_at BIGINT NOT NULL, indicator_state TEXT NOT NULL DEFAULT '{}', last_indicator TEXT NOT NULL DEFAULT '{}', activation_time BIGINT NOT NULL DEFAULT 0, PRIMARY KEY(symbol,interval))",
    "CREATE TABLE IF NOT EXISTS tic_live_trades(id BIGSERIAL PRIMARY KEY,symbol TEXT NOT NULL,interval TEXT NOT NULL,side TEXT NOT NULL,entry_time BIGINT NOT NULL,entry_price DOUBLE PRECISION NOT NULL,stop_price DOUBLE PRECISION NOT NULL,quantity DOUBLE PRECISION NOT NULL,remaining_qty DOUBLE PRECISION NOT NULL,tp1_pct DOUBLE PRECISION NOT NULL,tp1_time BIGINT,tp1_price DOUBLE PRECISION,tp2_target DOUBLE PRECISION,tp2_time BIGINT,tp2_price DOUBLE PRECISION,exit_time BIGINT,exit_price DOUBLE PRECISION,status TEXT NOT NULL,pnl_usdt DOUBLE PRECISION NOT NULL DEFAULT 0,payload TEXT NOT NULL DEFAULT '{}')",
    "CREATE TABLE IF NOT EXISTS tic_live_events(id BIGSERIAL PRIMARY KEY,symbol TEXT NOT NULL,interval TEXT NOT NULL,event_time BIGINT NOT NULL,event_type TEXT NOT NULL,side TEXT,price DOUBLE PRECISION,payload TEXT NOT NULL DEFAULT '{}')"]
  with db._db() as c:
   for q in ddl: db._execute(c,q)
   # Upgrade an existing V2.x database without dropping any trades/configuration.
   if db.backend=='sqlite':
    existing_cols={r['name'] for r in db._execute(c,'PRAGMA table_info(tic_live_config)').fetchall()}
    for col,typ in [('indicator_state',"TEXT NOT NULL DEFAULT '{}'"),('last_indicator',"TEXT NOT NULL DEFAULT '{}'"),('activation_time','INTEGER NOT NULL DEFAULT 0')]:
     if col not in existing_cols: db._execute(c,f'ALTER TABLE tic_live_config ADD COLUMN {col} {typ}')
    db._execute(c,'UPDATE tic_live_config SET activation_time=updated_at WHERE activation_time=0')
   else:
    for col,typ in [('indicator_state',"TEXT NOT NULL DEFAULT '{}'"),('last_indicator',"TEXT NOT NULL DEFAULT '{}'"),('activation_time','BIGINT NOT NULL DEFAULT 0')]:
     db._execute(c,f'ALTER TABLE tic_live_config ADD COLUMN IF NOT EXISTS {col} {typ}')
    db._execute(c,'UPDATE tic_live_config SET activation_time=updated_at WHERE activation_time=0')
 def subscribe(self,sym,tf,chat,cfg):
  now=int(time.time()*1000); width=TF_MS[tf]; baseline=(now//width)*width-width
  existing=self.get_config(sym,tf)
  old=json.loads(existing.get('config') or '{}') if existing else {}
  merged={**old,**cfg}
  for key in ('notify_open','notify_close','notify_tp','notify_stop'): merged.setdefault(key,True)
  with self.db._db() as c:
   self.db._execute(c,"INSERT INTO tic_live_config(symbol,interval,chat_id,enabled,config,last_bar_time,updated_at,indicator_state,last_indicator,activation_time) VALUES(?,?,?,1,?,?,?,'{}','{}',?) ON CONFLICT(symbol,interval) DO UPDATE SET chat_id=excluded.chat_id,enabled=1,config=excluded.config,updated_at=excluded.updated_at,activation_time=excluded.activation_time",(sym,tf,chat,json.dumps(merged),baseline,now,now))
 def unsubscribe(self,sym,tf=None):
  with self.db._db() as c:
   if tf:self.db._execute(c,'UPDATE tic_live_config SET enabled=0,updated_at=? WHERE symbol=? AND interval=?',(int(time.time()*1000),sym,tf))
   else:self.db._execute(c,'UPDATE tic_live_config SET enabled=0,updated_at=? WHERE symbol=?',(int(time.time()*1000),sym))
 def configs(self):
  with self.db._db() as c:return [dict(x) for x in self.db._execute(c,"SELECT * FROM tic_live_config c WHERE enabled=1 OR EXISTS(SELECT 1 FROM tic_live_trades t WHERE t.symbol=c.symbol AND t.interval=c.interval AND t.status='OPEN') ORDER BY symbol,interval").fetchall()]
 def get_config(self,sym,tf):
  with self.db._db() as c:
   x=self.db._execute(c,'SELECT * FROM tic_live_config WHERE symbol=? AND interval=?',(sym,tf)).fetchone();return dict(x) if x else None
 def open_trade(self,sym,tf,side,ts,entry,stop,qty,tpct,sl_candles=2):
  with self.db._db() as c:
   q='INSERT INTO tic_live_trades(symbol,interval,side,entry_time,entry_price,stop_price,quantity,remaining_qty,tp1_pct,status,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)';a=(sym,tf,side,ts,entry,stop,qty,qty,tpct,'OPEN',json.dumps({'sl_candles':int(sl_candles)}))
   if self.db.backend=='postgres':return self.db._execute(c,q+' RETURNING id',a).fetchone()['id']
   return self.db._execute(c,q,a).lastrowid
 def open_trades(self,sym,tf):
  with self.db._db() as c:return [dict(x) for x in self.db._execute(c,"SELECT * FROM tic_live_trades WHERE symbol=? AND interval=? AND status='OPEN' ORDER BY id",(sym,tf)).fetchall()]
 def update_trade(self,t):
  with self.db._db() as c:self.db._execute(c,'UPDATE tic_live_trades SET stop_price=?,remaining_qty=?,tp1_time=?,tp1_price=?,tp2_target=?,tp2_time=?,tp2_price=?,exit_time=?,exit_price=?,status=?,pnl_usdt=? WHERE id=?',(t['stop_price'],t['remaining_qty'],t.get('tp1_time'),t.get('tp1_price'),t.get('tp2_target'),t.get('tp2_time'),t.get('tp2_price'),t.get('exit_time'),t.get('exit_price'),t['status'],t['pnl_usdt'],t['id']))
 def checkpoint(self,sym,tf,ts,indicator_state,last_indicator,recent_bars):
  state=dict(indicator_state);state['recent_bars']=recent_bars[-RECENT_CANDLES:]
  with self.db._db() as c:self.db._execute(c,'UPDATE tic_live_config SET last_bar_time=?,indicator_state=?,last_indicator=?,updated_at=? WHERE symbol=? AND interval=?',(ts,json.dumps(state),json.dumps(last_indicator),int(time.time()*1000),sym,tf))
 def status(self):
  with self.db._db() as c:return [dict(x) for x in self.db._execute(c,'SELECT * FROM tic_live_config ORDER BY symbol,interval').fetchall()]
 def history(self,sym=None,tf=None,limit=100):
  q='SELECT * FROM tic_live_trades';a=[];w=[]
  if sym:w.append('symbol=?');a.append(sym)
  if tf:w.append('interval=?');a.append(tf)
  if w:q+=' WHERE '+' AND '.join(w)
  q+=' ORDER BY id DESC LIMIT ?';a.append(limit)
  with self.db._db() as c:return [dict(x) for x in self.db._execute(c,q,tuple(a)).fetchall()]
 def all_trades(self):
  with self.db._db() as c:return [dict(x) for x in self.db._execute(c,'SELECT * FROM tic_live_trades ORDER BY id').fetchall()]
 def event(self,sym,tf,ts,kind,side=None,price=None,payload=None):
  with self.db._db() as c:self.db._execute(c,'INSERT INTO tic_live_events(symbol,interval,event_time,event_type,side,price,payload) VALUES(?,?,?,?,?,?,?)',(sym,tf,ts,kind,side,price,json.dumps(payload or {})))
 def set_notification(self,sym,tf,category,enabled):
  keys={'open':'notify_open','close':'notify_close','tp':'notify_tp','stop':'notify_stop'}
  if category not in keys: raise ValueError('Categoría inválida')
  row=self.get_config(sym,tf)
  if not row: raise ValueError('TIC no está configurado para ese símbolo/temporalidad')
  cfg=json.loads(row.get('config') or '{}');cfg[keys[category]]=bool(enabled)
  with self.db._db() as c:self.db._execute(c,'UPDATE tic_live_config SET config=?,updated_at=? WHERE symbol=? AND interval=?',(json.dumps(cfg),int(time.time()*1000),sym,tf))
  return cfg
 def reset_all(self):
  with self.db._db() as c:
   for table in ('tic_live_events','tic_live_trades','tic_live_config'):
    self.db._execute(c,f'DELETE FROM {table}')
 def stats(self):
  rows=self.all_trades(); closed=[r for r in rows if r['status']=='CLOSED']; opened=[r for r in rows if r['status']=='OPEN']
  def metric(items):
   c=[x for x in items if x['status']=='CLOSED']; pnl=sum(float(x.get('pnl_usdt') or 0) for x in items); wins=sum(float(x.get('pnl_usdt') or 0)>0 for x in c)
   return {'total':len(items),'open':sum(x['status']=='OPEN' for x in items),'closed':len(c),'wins':wins,'win_rate':wins/len(c)*100 if c else 0,'pnl':pnl}
  by={}
  for r in rows:by.setdefault(r['symbol'],[]).append(r)
  return {'overall':metric(rows),'by_symbol':{k:metric(v) for k,v in sorted(by.items())}}
STORE=TICLiveStore()

async def monitor_subscription(context,cfg,client,store=STORE):
 sym,tf=cfg['symbol'],cfg['interval']; params=json.loads(cfg['config'] or '{}'); width=TF_MS[tf]
 now=int(time.time()*1000); closed_boundary=(now//width)*width-width
 last=int(cfg.get('last_bar_time') or 0)
 activation_time=int(cfg.get('activation_time') or cfg.get('updated_at') or 0)
 saved_state=json.loads(cfg.get('indicator_state') or '{}')
 # First activation: warm up once on a deep history and persist the recursive checkpoint.
 if not saved_state or saved_state.get('version')!=1:
  rows=await asyncio.to_thread(client.get_kline_history,sym,tf,total=WARMUP_CANDLES,end_time=closed_boundary)
  bars=[x for x in rows if int(x['time'])+width<=now]
  if len(bars)<202:return
  arrays=[[float(x[k]) for x in bars] for k in ('open','high','low','close')]
  state,series=initialize_incremental_state(*arrays,**{})
  latest={k:v[-1] for k,v in series.items() if v}
  latest['expected_last_bar_time']=closed_boundary;latest['data_current']=(int(bars[-1]['time'])==closed_boundary)
  await asyncio.to_thread(store.checkpoint,sym,tf,int(bars[-1]['time']),state,latest,bars[-RECENT_CANDLES:])
  log.info('TIC warmup completed %s %s: %s candles, checkpoint persisted',sym,tf,len(bars))
  return
 # Fetch only bars newer than checkpoint. If downtime is long, API pagination fills the gap (up to 10k).
 rows=await asyncio.to_thread(client.get_kline_history,sym,tf,total=WARMUP_CANDLES,start_time=last+1,end_time=closed_boundary)
 bars=[x for x in rows if int(x['time'])>last and int(x['time'])+width<=now]
 if not bars:return
 recent=list(saved_state.pop('recent_bars',[]) or [])
 if recent and int(recent[-1]['time'])!=last:
  log.warning('TIC checkpoint/cache mismatch %s %s; rebuilding warmup',sym,tf)
  cfg['indicator_state']='{}';return await monitor_subscription(context,cfg,client,store)
 # If the candle gap exceeds API retention, rebuild a deep checkpoint and skip offline entries.
 expected=last+width
 if int(bars[0]['time'])>expected:
  log.warning('TIC history gap %s %s: expected %s got %s; rebuilding warmup and skipping offline events',sym,tf,expected,bars[0]['time'])
  warm=await asyncio.to_thread(client.get_kline_history,sym,tf,total=WARMUP_CANDLES,end_time=closed_boundary)
  warm=[x for x in warm if int(x['time'])+width<=now]
  if len(warm)>=202:
   arrays=[[float(x[k]) for x in warm] for k in ('open','high','low','close')]
   rebuilt,series=initialize_incremental_state(*arrays)
   latest={k:v[-1] for k,v in series.items() if v}
   latest['expected_last_bar_time']=closed_boundary;latest['data_current']=(int(warm[-1]['time'])==closed_boundary)
   await asyncio.to_thread(store.checkpoint,sym,tf,int(warm[-1]['time']),rebuilt,latest,warm[-RECENT_CANDLES:])
  return
 # Process incremental indicator state candle by candle, persisting every closed bar.
 open_trades=await asyncio.to_thread(store.open_trades,sym,tf)
 for index,bar in enumerate(bars):
  one,state=update_incremental_state(saved_state,[float(bar['open'])],[float(bar['high'])],[float(bar['low'])],[float(bar['close'])])
  ts=int(bar['time']); o,h,l,c=map(float,(bar['open'],bar['high'],bar['low'],bar['close']))
  channel_change=int(one['bars_in_trend'][0] or 0)==1
  # Append candle before calculating an SL lookback.
  recent.append(bar);recent=recent[-RECENT_CANDLES:]
  open_trades=await asyncio.to_thread(store.open_trades,sym,tf)
  for t in open_trades:
   side=t['side']; stop=float(t['stop_price']); hit_sl=(l<=stop if side=='LONG' else h>=stop)
   if hit_sl:
    qty=float(t['remaining_qty']); signed=(stop-float(t['entry_price'])) if side=='LONG' else (float(t['entry_price'])-stop)
    t.update(remaining_qty=0,exit_time=ts,exit_price=stop,status='CLOSED',pnl_usdt=float(t['pnl_usdt'])+signed*qty)
    await asyncio.to_thread(store.update_trade,t);await asyncio.to_thread(store.event,sym,tf,ts,'SL',side,stop)
    await _notify(context,cfg,f'🛑 TIC {sym} {tf} · SL {side}\nPrecio {stop:.8g} · PnL {t["pnl_usdt"]:+.4f} USDT');continue
   if channel_change and t.get('tp1_time') is None and ts>int(t['entry_time']):
    qty=float(t['quantity'])*float(t['tp1_pct'])/100; px=c; signed=(px-float(t['entry_price'])) if side=='LONG' else (float(t['entry_price'])-px)
    t['pnl_usdt']=float(t['pnl_usdt'])+signed*qty;t['remaining_qty']=max(0,float(t['remaining_qty'])-qty);t['tp1_time']=ts;t['tp1_price']=px;t['stop_price']=float(t['entry_price'])
    t['tp2_target']=one['upper'][0] if side=='LONG' else one['lower'][0]
    await asyncio.to_thread(store.update_trade,t);await asyncio.to_thread(store.event,sym,tf,ts,'TP1',side,px,{'pct':t['tp1_pct'],'break_even':t['stop_price'],'tp2_target':t['tp2_target']})
    await _notify(context,cfg,f'🎯 TIC {sym} {tf} · TP1 {side}\nCierre {px:.8g} · {t["tp1_pct"]:g}% liquidado · SL a break-even {t["stop_price"]:.8g} · TP2 {t["tp2_target"]:.8g}')
   elif t.get('tp1_time') is not None and ts>int(t['tp1_time']) and t.get('tp2_target') is not None:
    target=float(t['tp2_target']);hit=(h>=target if side=='LONG' else l<=target)
    if hit:
     qty=float(t['remaining_qty']);signed=(target-float(t['entry_price'])) if side=='LONG' else (float(t['entry_price'])-target)
     t['pnl_usdt']=float(t['pnl_usdt'])+signed*qty;t.update(remaining_qty=0,tp2_time=ts,tp2_price=target,exit_time=ts,exit_price=target,status='CLOSED')
     await asyncio.to_thread(store.update_trade,t);await asyncio.to_thread(store.event,sym,tf,ts,'TP2',side,target)
     await _notify(context,cfg,f'🏁 TIC {sym} {tf} · TP2 · OPERACIÓN CERRADA\nPrecio {target:.8g} · PnL {t["pnl_usdt"]:+.4f} USDT')
  # Do not create entries for candles that closed while service was offline; only the newest closed candle is eligible.
  # Solo la vela cuyo cierre coincide con el último cierre teórico puede abrir
  # operaciones. La API puede devolver histórico retrasado; no lo confundimos
  # con la última vela cerrada. Además, el cierre debe ser posterior al alta.
  entry_eligible=entry_candle_is_eligible(ts,width,activation_time,closed_boundary)
  active=await asyncio.to_thread(store.open_trades,sym,tf)
  if cfg.get('enabled') and entry_eligible and not active and not channel_change and len(recent)>=int(params.get('sl_candles',2)):
   sln=max(1,min(200,int(params.get('sl_candles',2))))
   upper,lower=one['upper'][0],one['lower'][0]
   side=breakout_entry_side(o,h,l,c,upper,lower);stop=None
   if side=='LONG':stop=min(float(x['low']) for x in recent[-sln:])
   elif side=='SHORT':stop=max(float(x['high']) for x in recent[-sln:])
   if side:
    entry=c;dist=entry-stop if side=='LONG' else stop-entry;risk=float(params.get('risk_usdt',10))
    if dist>0 and risk>0:
     qty=risk/dist;tid=await asyncio.to_thread(store.open_trade,sym,tf,side,ts,entry,stop,qty,float(params['tp1_pct']),sln)
     await asyncio.to_thread(store.event,sym,tf,ts,'OPEN',side,entry,{'trade_id':tid,'sl_candles':sln})
     await _notify(context,cfg,f'📍 TIC {sym} {tf} · ENTRADA {side}\nPrecio {entry:.8g} · SL {stop:.8g} ({sln} velas) · TP1 {params["tp1_pct"]:g}% · cantidad {qty:.8g}')
  last_indicator={k:v[0] for k,v in one.items()}
  last_indicator['expected_last_bar_time']=closed_boundary
  last_indicator['data_current']=(ts==closed_boundary)
  await asyncio.to_thread(store.checkpoint,sym,tf,ts,state,last_indicator,recent)
  saved_state=state
async def _notify(context,cfg,msg):
 try:
  kind='notify_open' if msg.startswith('📍') else 'notify_tp' if msg.startswith('🎯') else 'notify_close' if msg.startswith('🏁') else 'notify_stop'
  try: prefs=json.loads(cfg.get('config') or '{}')
  except Exception: prefs={}
  if not prefs.get(kind,True): return
  await context.bot.send_message(chat_id=cfg['chat_id'],text=msg)
 except Exception:log.exception('TIC notification failed')

async def monitor_job(context):
 from bot import bitunix
 for cfg in await asyncio.to_thread(STORE.configs):
  try:await monitor_subscription(context,cfg,bitunix)
  except Exception:log.exception('TIC monitor error %s %s',cfg['symbol'],cfg['interval'])
