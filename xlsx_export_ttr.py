"""Excel export for Trend Trader-Remastered backtests."""
from datetime import datetime, timezone
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

DARK = PatternFill("solid", fgColor="1F2937")
WHITE = Font(bold=True, color="FFFFFF")
BOLD = Font(bold=True)
TITLE = Font(bold=True, size=14)

def _dt(ms):
    return datetime.fromtimestamp(int(ms)/1000, timezone.utc).replace(tzinfo=None) if ms is not None else None

def build_ttr_workbook(symbol, interval, klines, result, trades, params=None):
    params = params or result.get('parameters', {})
    wb = Workbook()
    ws = wb.active; ws.title = "Resumen"
    ws["A1"] = f"Trend Trader-Remastered — {symbol} {interval}"; ws["A1"].font = TITLE; ws.merge_cells("A1:D1")
    summary = result.get('summary', {})
    wins = sum((t.get('pnl_pct') or 0) > 0 for t in trades)
    losses = sum((t.get('pnl_pct') or 0) < 0 for t in trades)
    gross_win = sum(t.get('pnl_pct') or 0 for t in trades if (t.get('pnl_pct') or 0) > 0)
    gross_loss = abs(sum(t.get('pnl_pct') or 0 for t in trades if (t.get('pnl_pct') or 0) < 0))
    equity = 100.0
    for t in sorted(trades, key=lambda x: x.get('exit_time') or x.get('entry_time') or 0):
        equity *= max(0.0, 1.0 + float(t.get('pnl_pct') or 0)/100.0)
    first = _dt(klines[0]['time']) if klines else None; last = _dt(klines[-1]['time']) if klines else None
    meta = [
        ("Símbolo", symbol), ("Temporalidad", interval),
        ("Período UTC", f"{first:%Y-%m-%d %H:%M} → {last:%Y-%m-%d %H:%M}" if first and last else "N/D"),
        ("Velas", len(klines)), ("Operaciones cerradas en el período", len(trades)),
        ("Dirección", params.get('pos_dir', 'Buy & Sell')),
        ("Entrar en reentrada", params.get('enter_on_reentry', True)),
        ("Porcentajes TP", ", ".join(map(str, params.get('tp_percentages', [])))),
        ("Comisión por lado (%)", params.get('fee_pct', 0)), ("Deslizamiento por lado (%)", params.get('slippage_pct', 0)),
        ("Stop", "Trailing lagging PSAR actualizado al cierre, efectivo desde la vela siguiente"),
        ("Acierto (%)", wins / len(trades) * 100 if trades else 0),
        ("Ganadoras", wins), ("Perdedoras", losses),
        ("PnL neto sumado (% del tamaño inicial)", sum(float(t.get('pnl_pct') or 0) for t in trades)),
        ("PnL compuesto (%)", (equity - 1) * 100),
        ("Profit factor", gross_win / gross_loss if gross_loss else None),
        ("Limitación", result.get('note', '')), 
    ]
    row = 3
    for key, value in meta:
        ws.cell(row, 1, key).font = BOLD; ws.cell(row, 2, value); row += 1
    ws.column_dimensions['A'].width = 42; ws.column_dimensions['B'].width = 90

    op = wb.create_sheet("Operaciones")
    headers = ["Entrada UTC", "Salida UTC", "Lado", "Precio entrada señal", "Precio medio entrada", "Precio salida", "Motivo salida", "PnL neto (% inicial)", "Tamaño restante (%)", "Velas", "PnL TP acumulado (% inicial)", "PnL stop/reversión (% inicial)", "Número TP", "Número reentradas"]
    for c,h in enumerate(headers,1): op.cell(1,c,h).fill=DARK; op.cell(1,c).font=WHITE
    for r,t in enumerate(trades,2):
        pieces=t.get('partial_exits') or []
        tp_pnl=sum(float(x.get('pnl_pct_position') or 0) for x in pieces if x.get('reason')=='TP')
        final_pnl=sum(float(x.get('pnl_pct_position') or 0) for x in pieces if x.get('reason') not in ('TP','RE_ADD'))
        vals=[_dt(t.get('entry_time')),_dt(t.get('exit_time')),t.get('side'),t.get('entry_price'),t.get('avg_entry_price'),t.get('exit_price'),t.get('exit_reason'),t.get('pnl_pct'),t.get('remaining_pct'),t.get('bars_held'),tp_pnl,final_pnl,sum(x.get('reason')=='TP' for x in pieces),sum(x.get('reason')=='RE_ADD' for x in pieces)]
        for c,v in enumerate(vals,1): op.cell(r,c,v)
        op.cell(r,1).number_format=op.cell(r,2).number_format="yyyy-mm-dd hh:mm"
        for c in (8,11,12): op.cell(r,c).number_format="0.0000"
    op.freeze_panes="A2"; op.auto_filter.ref=op.dimensions
    for c in range(1,len(headers)+1): op.column_dimensions[get_column_letter(c)].width=22

    px=wb.create_sheet("Cierres parciales")
    headers=["N.º operación", "Fecha UTC", "Lado", "Tipo", "Precio ejecución", "% tamaño inicial", "PnL neto (% inicial)", "Exposición restante (%)"]
    for c,h in enumerate(headers,1): px.cell(1,c,h).fill=DARK; px.cell(1,c).font=WHITE
    rr=2
    for no,t in enumerate(trades,1):
        for piece in t.get('partial_exits') or []:
            vals=[no,_dt(piece.get('time')),t.get('side'),piece.get('reason'),piece.get('price'),piece.get('percent'),piece.get('pnl_pct_position'),piece.get('remaining_pct')]
            for c,v in enumerate(vals,1): px.cell(rr,c,v)
            px.cell(rr,2).number_format="yyyy-mm-dd hh:mm"; px.cell(rr,7).number_format="0.0000"; rr+=1
    px.freeze_panes="A2"; px.auto_filter.ref=px.dimensions
    for c in range(1,len(headers)+1): px.column_dimensions[get_column_letter(c)].width=24

    ev=wb.create_sheet("Señales")
    headers=["Fecha UTC","Tipo","Lado","Precio","% cerrado","% añadido","Stop PSAR"]
    for c,h in enumerate(headers,1): ev.cell(1,c,h).fill=DARK; ev.cell(1,c).font=WHITE
    for r,e in enumerate(result.get('events',[]),2):
        vals=[_dt(e.get('time')),e.get('type'),e.get('side'),e.get('price'),e.get('percent_closed'),e.get('percent_added'),e.get('stop_price',e.get('psar_lagging'))]
        for c,v in enumerate(vals,1): ev.cell(r,c,v)
        ev.cell(r,1).number_format="yyyy-mm-dd hh:mm"
    ev.freeze_panes="A2"; ev.auto_filter.ref=ev.dimensions
    for c in range(1,len(headers)+1): ev.column_dimensions[get_column_letter(c)].width=22

    curve=wb.create_sheet("Curva de capital")
    headers=["N.º operación","Salida UTC","Lado","PnL neto %","Capital compuesto"]
    for c,h in enumerate(headers,1): curve.cell(1,c,h).fill=DARK; curve.cell(1,c).font=WHITE
    eq=100.0
    for r,t in enumerate(sorted(trades,key=lambda x:x.get('exit_time') or x.get('entry_time') or 0),2):
        pnl=float(t.get('pnl_pct') or 0); eq*=max(0,1+pnl/100)
        vals=[r-1,_dt(t.get('exit_time')),t.get('side'),pnl,eq]
        for c,v in enumerate(vals,1): curve.cell(r,c,v)
        curve.cell(r,2).number_format="yyyy-mm-dd hh:mm"
    curve.freeze_panes="A2"; curve.auto_filter.ref=curve.dimensions
    for c in range(1,len(headers)+1): curve.column_dimensions[get_column_letter(c)].width=24
    out=BytesIO(); wb.save(out); out.seek(0); return out
