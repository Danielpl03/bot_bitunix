"""Excel export for the RSI extreme-reversal strategy."""
from datetime import datetime, timezone
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from indicators import rsi_indicator

DARK=PatternFill('solid',fgColor='1F2937'); WHITE=Font(bold=True,color='FFFFFF'); BOLD=Font(bold=True)

def build_rsi_workbook(symbol,interval,horizon,klines,trades,stats,risk_config=None,*,rsi_period=14,oversold=30,overbought=70,pivot_left=2,pivot_right=2,stop_buffer_pct=0,risk_usdt=10,fee_pct=0,**_ignored):
    wb=Workbook(); ws=wb.active; ws.title='Resumen'
    ws['A1']=f'RSI Extremos — {symbol} {interval}'; ws['A1'].font=Font(bold=True,size=14)
    meta=[('RSI','Wilder/RMA'),('Periodo RSI',rsi_period),('Sobreventa',oversold),('Sobrecompra',overbought),('Stop','Pivote confirmado; fallback al extremo desde entrada en zona'),('Pivote izquierda/derecha',f'{pivot_left}/{pivot_right}'),('Colchón stop %',stop_buffer_pct),('Riesgo fijo USDT',risk_usdt),('Take profit','A mercado al alcanzar la zona extrema opuesta'),('Entrada','Apertura de vela siguiente'),('Horizonte',horizon if horizon is not None else 'Sin límite'),('Comisión por lado %',fee_pct)]
    for i,(k,v) in enumerate(meta,3): ws.cell(i,1,k).font=BOLD; ws.cell(i,2,v)
    row=3+len(meta)+2
    for c,h in enumerate(['Métrica','Total','LONG','SHORT'],1): ws.cell(row,c,h).fill=DARK; ws.cell(row,c).font=WHITE
    for j,key in enumerate(('total','long','short'),2):
        g=stats[key]
        for ri,(label,m) in enumerate([('Operaciones','total'),('Resueltas','resueltas'),('TP','tp'),('SL','sl'),('Pendientes','pendientes'),('Acierto %','win_rate'),('PnL USDT','pnl_usdt'),('R total','r_total'),('Profit factor','profit_factor')],row+1):
            ws.cell(ri,1,label); ws.cell(ri,j,g.get(m))
    for c,w in enumerate([30,24,24,24],1): ws.column_dimensions[get_column_letter(c)].width=w
    op=wb.create_sheet('Operaciones'); headers=['Hora señal UTC','Hora entrada UTC','Lado','Entrada','Stop','RSI señal','Tipo','Pivote confirmado','Resultado','Hora salida UTC','Precio salida','PnL %','R múltiple','Riesgo USDT','Cantidad','PnL USDT']
    for c,h in enumerate(headers,1): op.cell(1,c,h).fill=DARK; op.cell(1,c).font=WHITE
    for ri,t in enumerate(trades,2):
        vals=[datetime.fromtimestamp(t.time/1000,timezone.utc).replace(tzinfo=None),datetime.fromtimestamp(t.entry_time/1000,timezone.utc).replace(tzinfo=None),t.side,t.entry,t.stop_loss,t.rsi,t.signal_type,t.pivot_used,t.outcome,datetime.fromtimestamp(t.exit_time/1000,timezone.utc).replace(tzinfo=None) if t.exit_time else None,t.exit_price,t.pnl_pct,t.r_multiple,t.risk_usdt,t.quantity,t.pnl_usdt]
        for c,v in enumerate(vals,1): op.cell(ri,c,v)
    op.freeze_panes='A2'; op.auto_filter.ref=op.dimensions
    for c in range(1,len(headers)+1): op.column_dimensions[get_column_letter(c)].width=19
    candle=wb.create_sheet('Velas'); closes=[float(x['close']) for x in klines]; vols=[float(x.get('volume',0) or 0) for x in klines]
    rs=rsi_indicator(closes,rsi_period=rsi_period,ma_type='None',ma_period=1,volume=vols)['rsi']
    for c,h in enumerate(['Hora UTC','Open','High','Low','Close','RSI'],1): candle.cell(1,c,h).fill=DARK; candle.cell(1,c).font=WHITE
    for i,k in enumerate(klines,2):
        vals=[datetime.fromtimestamp(int(k['time'])/1000,timezone.utc).replace(tzinfo=None),float(k['open']),float(k['high']),float(k['low']),float(k['close']),rs[i-2]]
        for c,v in enumerate(vals,1): candle.cell(i,c,v)
    candle.freeze_panes='A2'; candle.auto_filter.ref=candle.dimensions
    for c,w in enumerate([22,16,16,16,16,14],1): candle.column_dimensions[get_column_letter(c)].width=w
    out=BytesIO(); wb.save(out); out.seek(0); return out
