"""API REST para Backtest Lab. Servicio independiente del polling/webhook de Telegram.

Inicio local: python api_server.py
Render: Start Command = python api_server.py (usa PORT de Render).
Opcional: BACKTEST_API_KEY para proteger el servicio con un secreto compartido.
"""
from __future__ import annotations
import json, math, os, traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from bitunix_client import BitunixClient
from backtest_common import parse_backtest_request, load_backtest_klines, filter_signals_to_period
from backtest import run_backtest as run_bollinger, summarize as summarize_bollinger
from backtest_kelner import run_backtest as run_keltner, summarize as summarize_keltner
from backtest_reversion import run_backtest as run_reversion, summarize as summarize_reversion
from backtest_rsi import run_backtest as run_rsi, summarize as summarize_rsi
from backtest_squeeze import run_backtest as run_squeeze, summarize as summarize_squeeze
from backtest_dual_thrust import run_backtest as run_dual, summarize as summarize_dual
from backtest_trend_trader_remastered import run_backtest as run_ttr

PORT = int(os.environ.get('PORT', '8000'))
API_KEY = os.environ.get('BACKTEST_API_KEY', '').strip()
MAX_CANDLES = 1500
VALID_INTERVALS = {'1m','5m','15m','30m','1h','2h','4h','6h','8h','12h','1d','3d','1w','1M'}
client = BitunixClient(api_key=os.environ.get('BITUNIX_API_KEY'), secret_key=os.environ.get('BITUNIX_API_SECRET'))

def normalize_symbol(raw):
    symbol = str(raw or 'BTCUSDT').upper().strip()
    if not symbol.endswith('USDT'): symbol += 'USDT'
    if not symbol.replace('USDT','').isalnum(): raise ValueError('Símbolo inválido.')
    return symbol

def clean(value):
    if is_dataclass(value): return clean(asdict(value))
    if isinstance(value, dict): return {str(k): clean(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value): return None
    return value

def stats_for_trades(trades):
    closed = [t for t in trades if t.get('pnl_pct') is not None]
    wins = [t for t in closed if t['pnl_pct'] > 0]
    losses = [t for t in closed if t['pnl_pct'] < 0]
    gross_win = sum(t['pnl_pct'] for t in wins)
    gross_loss = abs(sum(t['pnl_pct'] for t in losses))
    return {'total':len(trades),'resueltas':len(closed),'tp':sum(t.get('outcome')=='TP' for t in trades),
            'sl':sum(t.get('outcome')=='SL' for t in trades),'horizonte':sum(t.get('outcome')=='HORIZONTE' for t in trades),
            'pendientes':sum(t.get('outcome') in ('PENDIENTE','N/D') for t in trades),
            'win_rate':len(wins)/len(closed)*100 if closed else None,
            'pnl_pct_total':sum(t['pnl_pct'] for t in closed) if closed else None,
            'r_total':sum((t.get('r_multiple') or 0) for t in closed) if closed else None,
            'profit_factor':gross_win/gross_loss if gross_loss else (None if not gross_win else None)}

def equity_curve(trades):
    equity=100.0; points=[{'time':None,'equity':100.0}]
    ordered=sorted((t for t in trades if t.get('pnl_pct') is not None), key=lambda t:(t.get('exit_time') or t.get('entry_time') or t.get('time') or 0))
    for t in ordered:
        equity *= max(0.0, 1.0 + float(t['pnl_pct'])/100.0)
        points.append({'time':t.get('exit_time') or t.get('entry_time') or t.get('time'),'equity':equity})
    return points

def run(payload):
    strategy = str(payload.get('strategy','keltner')).lower()
    if strategy not in {'keltner','bollinger','rsi','reversion','squeeze','dual_thrust','trend_trader_remastered'}: raise ValueError('Estrategia no soportada.')
    symbol=normalize_symbol(payload.get('symbol','BTCUSDT')); interval=str(payload.get('interval','1h'))
    if interval not in VALID_INTERVALS: raise ValueError('Temporalidad no válida.')
    candles_count=max(100,min(int(payload.get('candles') or 500),MAX_CANDLES))
    start=payload.get('startDate'); end=payload.get('endDate')
    args=[symbol,interval,(str(start),str(end))] if start and end else [symbol,interval,str(candles_count)]
    if start and end: args=[symbol,interval,str(start),str(end)]
    request=parse_backtest_request(args, normalize_symbol=normalize_symbol, default_interval='1h', max_candles=MAX_CANDLES)
    horizon=payload.get('horizon'); horizon=int(horizon) if horizon not in (None,'',0,'0') else None
    if horizon is not None and not 1 <= horizon <= 10000: raise ValueError('El horizonte debe estar entre 1 y 10000 velas.')
    klines, signal_start, signal_end = load_backtest_klines(client, request, warmup_candles=100, followup_candles=200)
    if len(klines)<40: raise ValueError(f'Solo se encontraron {len(klines)} velas; no hay suficiente histórico.')
    closes=[float(k['close']) for k in klines]; highs=[float(k['high']) for k in klines]; lows=[float(k['low']) for k in klines]; opens=[float(k['open']) for k in klines]; times=[int(k['time']) for k in klines]
    p=payload.get('params') or {}
    if strategy=='trend_trader_remastered':
        result=run_ttr(closes,highs,lows,opens,times,
            psar_start=float(p.get('psar_start',0.02)), psar_increment=float(p.get('psar_increment',0.02)), psar_max=float(p.get('psar_max',0.025)),
            frac_lb_high=int(p.get('frac_lb_high',2)), frac_lb_low=int(p.get('frac_lb_low',2)),
            sig_re_min_proximity=float(p.get('sig_re_min_proximity',0)),
            sig_re_buy_no_tp=bool(p.get('sig_re_buy_no_tp',False)), sig_re_sell_no_tp=bool(p.get('sig_re_sell_no_tp',False)),
            pos_dir=str(p.get('pos_dir','Buy & Sell')),
            show_entry_buy=bool(p.get('show_entry_buy',True)), show_tp_buy=bool(p.get('show_tp_buy',True)),
            show_re_buy=bool(p.get('show_re_buy',True)), show_exit_buy=bool(p.get('show_exit_buy',False)),
            show_entry_sell=bool(p.get('show_entry_sell',True)), show_tp_sell=bool(p.get('show_tp_sell',True)),
            show_re_sell=bool(p.get('show_re_sell',True)), show_exit_sell=bool(p.get('show_exit_sell',False)),
            fee_pct=float(p.get('fee_pct',0)), slippage_pct=float(p.get('slippage_pct',0)))
        events=[e for e in result['events'] if signal_start <= e['time'] <= signal_end]
        trades=[t for t in result['trades'] if signal_start <= t['entry_time'] <= signal_end]
        return clean({'strategy':strategy,'symbol':symbol,'interval':interval,'candles':len(klines),
            'summary':{'total':result['summary']},'trades':trades,'events':events,'equity':result['equity'],
            'indicators':result['indicators'],'parameters':result['parameters'],'note':result['note']})
    if strategy=='keltner':
        all_results=run_keltner(closes,highs,lows,opens,times,horizon=horizon,ema_period=int(p.get('ema_period',20)),atr_period=int(p.get('atr_period',10)),multiplier=float(p.get('multiplier',2.0)),only_outer_bands=bool(p.get('only_outer_bands',False)))
        all_results=filter_signals_to_period(all_results,signal_start,signal_end)
        ratio=float(p.get('tp_r',1.5)); ratio=min(5.0,max(0.25,ratio)); summary=summarize_keltner(all_results); chosen=summary['total']['escenarios'].get(ratio)
        if chosen is None:
            from backtest_kelner import _scenario_stats
            chosen=_scenario_stats(all_results,ratio)
        trades=[]
        for x in all_results:
            sc=min(x.scenarios.items(),key=lambda kv:abs(kv[0]-ratio))[1]
            trades.append({'time':x.time,'entry_time':x.time,'exit_time':getattr(sc,'exit_time',None),'side':x.side,'entry':x.close,'exit_price':sc.exit_price,'stop_loss':x.stop_loss,'target':sc.target,'outcome':sc.outcome,'pnl_pct':sc.pct_change,'r_multiple':sc.r_multiple})
        s={**chosen,'total':len(all_results),'profit_factor':None}
    elif strategy=='bollinger':
        results=run_bollinger(closes,highs,lows,opens,times,horizon=horizon,oversold=float(p.get('oversold',25)),overbought=float(p.get('overbought',75)),lookback=int(p.get('lookback',10)))
        results=filter_signals_to_period(results,signal_start,signal_end); ratio=float(p.get('tp_r',1.0)); summary=summarize_bollinger(results); key=min(summary['total']['escenarios'],key=lambda x:abs(float(x)-ratio)); s={**summary['total']['escenarios'][key],'total':len(results)}; trades=[]
        for x in results:
            sc=x.scenarios[float(key) if float(key) in x.scenarios else key]
            trades.append({'time':x.time,'entry_time':x.time,'side':x.side,'entry':x.close,'exit_price':sc.exit_price,'stop_loss':x.stop_loss,'target':sc.target,'outcome':sc.outcome,'pnl_pct':sc.pct_change,'r_multiple':sc.r_multiple})
    else:
        if strategy=='rsi':
            results=run_rsi(closes,highs,lows,opens,times,horizon=horizon,rsi_period=int(p.get('rsi_period',14)),ma_type=str(p.get('ma_type','SMA')),ma_period=int(p.get('ma_period',14)),sl_lookback=int(p.get('sl_lookback',10)),stop_mode=str(p.get('stop_mode','lookback')),pivot_buffer_pct=float(p.get('pivot_buffer_pct',0.1)))
            summarizer=summarize_rsi
        elif strategy=='reversion':
            results=run_reversion(closes,highs,lows,opens,times,horizon=horizon,bb_period=int(p.get('bb_period',20)),bb_std=float(p.get('bb_std',2)),k_period=int(p.get('k_period',14)),smooth_k=int(p.get('smooth_k',3)),d_period=int(p.get('d_period',3)),oversold=float(p.get('oversold',20)),overbought=float(p.get('overbought',80)),sl_lookback=int(p.get('sl_lookback',10)))
            summarizer=summarize_reversion
        elif strategy=='squeeze':
            results=run_squeeze(closes,highs,lows,opens,times,horizon=horizon,tp_r=float(p.get('tp_r',2)),bb_period=int(p.get('bb_period',20)),bb_std=float(p.get('bb_std',2)),kc_ema_period=int(p.get('kc_ema_period',20)),atr_period=int(p.get('atr_period',20)),kc_multiplier=float(p.get('kc_multiplier',1.5)),stop_lookback=int(p.get('stop_lookback',10)),momentum_period=int(p.get('momentum_period',12)))
            summarizer=summarize_squeeze
        else:
            results=run_dual(closes,highs,lows,opens,times,horizon=horizon,lookback=int(p.get('lookback',4)),k1=float(p.get('k1',0.6)),k2=float(p.get('k2',0.7)),fee_bps=float(p.get('fee_bps',0)),slippage_bps=float(p.get('slippage_bps',0)))
            summarizer=summarize_dual
        results=filter_signals_to_period(results,signal_start,signal_end)
        trades=[clean(x) for x in results]
        # Normalizar las estrategias cuyos campos difieren
        for t in trades:
            if 'exit_reason' in t and 'outcome' not in t: t['outcome']=t['exit_reason']
        summary=summarizer(results); s=summary.get('total',{})
    for t in trades:
        if t.get('exit_time') is None and t.get('exit_price') is not None: t['exit_time']=t.get('entry_time')
    return clean({'strategy':strategy,'symbol':symbol,'interval':interval,'candles':len(klines),'summary':{'total':s},'trades':trades,'equity':equity_curve(trades),'note':'Backtest histórico. No incluye necesariamente todos los costes reales; no ejecuta órdenes.'})

class Handler(BaseHTTPRequestHandler):
    server_version='BacktestLabAPI/1.0'
    def log_message(self, fmt, *args): print('%s - %s' % (self.address_string(), fmt % args))
    def send_json(self, status, data):
        raw=json.dumps(clean(data),ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Access-Control-Allow-Origin','*'); self.send_header('Access-Control-Allow-Headers','Content-Type, X-API-Key'); self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS'); self.end_headers(); self.wfile.write(raw)
    def do_OPTIONS(self): self.send_json(204,{})
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/api/health': return self.send_json(200,{'ok':True,'service':'backtest-lab-python-api','strategies':['keltner','bollinger','rsi','reversion','squeeze','dual_thrust','trend_trader_remastered']})
        return self.send_json(404,{'error':'Ruta no encontrada.'})
    def do_POST(self):
        if urlparse(self.path).path!='/api/backtest': return self.send_json(404,{'error':'Ruta no encontrada.'})
        if API_KEY and self.headers.get('X-API-Key','') != API_KEY: return self.send_json(401,{'error':'API key inválida o ausente.'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length>100_000: return self.send_json(413,{'error':'Solicitud demasiado grande.'})
            payload=json.loads(self.rfile.read(length) or b'{}')
            return self.send_json(200,run(payload))
        except (ValueError, TypeError, KeyError) as e: return self.send_json(400,{'error':str(e)})
        except Exception as e:
            traceback.print_exc(); return self.send_json(502,{'error':'Error ejecutando el backtest o consultando Bitunix.','detail':str(e)[:500]})

if __name__=='__main__':
    print(f'Backtest Lab Python API escuchando en 0.0.0.0:{PORT}')
    ThreadingHTTPServer(('0.0.0.0',PORT),Handler).serve_forever()
