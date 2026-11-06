from io import BytesIO
from datetime import datetime, timezone
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from .engine import summarize
from .indicator import calculate_trend_impulse_channels

DARK=PatternFill('solid',fgColor='1F2937'); WHITE=Font(bold=True,color='FFFFFF')
def build_workbook(symbol,interval,klines,trades,*,tp1_pct,risk_usdt,fee_pct=0,indicator_params=None,indicator=None):
    wb=Workbook(); ws=wb.active; ws.title='Resumen'; s=summarize(trades)
    ws['A1']=f'Trend Impulse Breakout — {symbol} {interval}'; ws['A1'].font=Font(bold=True,size=14)
    meta=[('TP1 cierre posición %',tp1_pct),('TP2','Resto en banda del canal nuevo'),('Riesgo USDT',risk_usdt),('Comisión por lado %',fee_pct),('Entrada','Apertura de vela siguiente'),('SL','Extremo entre vela de ruptura y anterior'),('Regla ruptura','High > upper / Low < lower; vela direccional; sin flip de canal')]
    for r,(k,v) in enumerate(meta,3): ws.cell(r,1,k); ws.cell(r,2,v)
    row=13
    for c,h in enumerate(['Métrica','Valor'],1): ws.cell(row,c,h).fill=DARK; ws.cell(row,c).font=WHITE
    for r,(k,v) in enumerate(s.items(),row+1): ws.cell(r,1,k); ws.cell(r,2,v)
    op=wb.create_sheet('Operaciones'); headers=['Señal UTC','Entrada UTC','Lado','Entrada','SL','TP1 %','TP1 UTC','TP1 precio','TP2 objetivo','TP2 UTC','TP2 precio','Cierre UTC','Cierre precio','Resultado','Cantidad','Restante','PnL USDT','PnL %']
    for c,h in enumerate(headers,1): op.cell(1,c,h).fill=DARK; op.cell(1,c).font=WHITE
    for r,t in enumerate(trades,2):
        vals=[t.signal_time,t.entry_time,t.side,t.entry,t.stop_loss,t.tp1_pct,t.tp1_time,t.tp1_price,t.tp2_target,t.tp2_time,t.tp2_price,t.exit_time,t.exit_price,t.outcome,t.quantity,t.remaining_qty,t.pnl_usdt,t.pnl_pct]
        for c,v in enumerate(vals,1):
            if c in (1,2,7,10,12) and v: v=datetime.fromtimestamp(v/1000,timezone.utc).replace(tzinfo=None)
            op.cell(r,c,v)
    op.freeze_panes='A2'; op.auto_filter.ref=op.dimensions
    for c in range(1,len(headers)+1): op.column_dimensions[get_column_letter(c)].width=18
    if indicator is None:
        indicator=calculate_trend_impulse_channels([float(k['open']) for k in klines],[float(k['high']) for k in klines],[float(k['low']) for k in klines],[float(k['close']) for k in klines],**(indicator_params or {}))
    sh=wb.create_sheet('Velas'); heads=['Hora UTC','Open','High','Low','Close','ATR','Trend','Direction','Upper','Lower','Bars in trend','Extension','Trend step','Cambio canal','Ruptura LONG','Ruptura SHORT']
    for c,h in enumerate(heads,1): sh.cell(1,c,h).fill=DARK; sh.cell(1,c).font=WHITE
    prev=None
    for i,k in enumerate(klines):
        d=indicator['direction'][i]; change=d in (-1,1) and prev is not None and d!=prev
        upper,lower=indicator['upper'][i],indicator['lower'][i]
        vals=[datetime.fromtimestamp(int(k['time'])/1000,timezone.utc).replace(tzinfo=None),float(k['open']),float(k['high']),float(k['low']),float(k['close']),indicator['atr'][i],indicator['trend'][i],d,upper,lower,indicator['bars_in_trend'][i],indicator['extension'][i],indicator['trend_step'][i],change,bool(upper is not None and float(k['high'])>upper and float(k['close'])>float(k['open']) and not change),bool(lower is not None and float(k['low'])<lower and float(k['close'])<float(k['open']) and not change)]
        for c,v in enumerate(vals,1): sh.cell(i+2,c,v)
        prev=d
    sh.freeze_panes='A2'; sh.auto_filter.ref=sh.dimensions
    for c in range(1,len(heads)+1): sh.column_dimensions[get_column_letter(c)].width=18
    out=BytesIO(); wb.save(out); out.seek(0); return out
