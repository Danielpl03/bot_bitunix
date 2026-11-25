"""Excel report for Iván Scherman backtests."""
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

def build_ivan_workbook(symbol,interval,candles,result):
    wb=Workbook(); ws=wb.active; ws.title="Resumen"
    for row in [
        ["Iván Scherman · Backtest",symbol,interval],
        ["Métrica","Valor"],
        *[[k,v] for k,v in result["summary"].items()],
        [],["Parámetro","Valor"],*[[k,str(v)] for k,v in result["parameters"].items()]
    ]: ws.append(row)
    tr=wb.create_sheet("Operaciones")
    cols=["side","entry_time","entry_price","exit_time","exit_price","exit_reason","pnl_pct","bars_held","stop_price"]
    tr.append(cols)
    for t in result["trades"]:tr.append([t.get(k) for k in cols])
    ev=wb.create_sheet("Señales"); ev.append(["type","time","price","reason","pnl_pct","streak","stop_price"])
    for e in result["events"]:ev.append([e.get(k) for k in ["type","time","price","reason","pnl_pct","streak","stop_price"]])
    eq=wb.create_sheet("Equity");eq.append(["time","equity"])
    for x in result["equity"]:eq.append([x.get("time"),x.get("equity")])
    for sh in wb.worksheets:
        sh.freeze_panes="A2";sh.auto_filter.ref=sh.dimensions
        for cell in sh[1]:
            cell.font=Font(bold=True,color="FFFFFF");cell.fill=PatternFill("solid",fgColor="26364A")
        for col in sh.columns:
            width=min(36,max(12,max((len(str(c.value or "")) for c in col),default=10)+2))
            sh.column_dimensions[get_column_letter(col[0].column)].width=width
    bio=BytesIO();wb.save(bio);bio.seek(0);return bio
