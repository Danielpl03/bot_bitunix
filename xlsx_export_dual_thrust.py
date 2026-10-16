"""Exportación Excel para Dual Thrust con costes y curva de capital."""
from datetime import datetime, timezone
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

DARK = PatternFill("solid", fgColor="1F2937")
WHITE = Font(bold=True, color="FFFFFF")
BOLD = Font(bold=True)
TITLE = Font(bold=True, size=14)


def build_dual_thrust_workbook(symbol, interval, horizon, klines, trades, stats, lookback, k1, k2,
                               *, fee_bps=0.0, slippage_bps=0.0, equity_curve=None):
    wb = Workbook(); ws = wb.active; ws.title = "Resumen"
    ws["A1"] = f"Dual Thrust Breakout — {symbol} {interval}"; ws["A1"].font = TITLE; ws.merge_cells("A1:D1")
    first = datetime.fromtimestamp(int(klines[0]["time"])/1000, timezone.utc)
    last = datetime.fromtimestamp(int(klines[-1]["time"])/1000, timezone.utc)
    meta = [
        ("Símbolo", symbol), ("Temporalidad", interval),
        ("Período de velas UTC", f"{first:%Y-%m-%d %H:%M} → {last:%Y-%m-%d %H:%M}"),
        ("Velas cargadas", len(klines)), ("Horizonte externo", horizon if horizon else "Sin límite"),
        ("Ventana histórica N", lookback), ("Coeficiente alcista K1", k1), ("Coeficiente bajista K2", k2),
        ("Comisión por lado (bps)", fee_bps), ("Deslizamiento por lado (bps)", slippage_bps),
        ("Financiación", "No incluida; requiere tasas históricas de funding"),
        ("Fórmula Range", "max(HH - LC, HC - LL) usando las N velas anteriores"),
        ("Niveles", "BuyTrack = Open actual + K1×Range; SellTrack = Open actual − K2×Range"),
        ("Ejecución", "Ruptura intrabar; reversión ante ruptura opuesta; costes aplicados a cada ejecución"),
        ("Barras ambiguas", "Si High alcanza BuyTrack y Low alcanza SellTrack, se ignora la señal de ruptura"),
        ("Capital / drawdown", "Curva teórica compuesta; supone 100% del capital por operación, sin apalancamiento; drawdown sobre máximos previos"),
        ("Posición pendiente", "Se excluye de retornos realizados y de la curva; no se marca a mercado"),
        ("Limitación", "OHLC no permite reconstruir la secuencia intrabar ni el libro de órdenes"),
    ]
    row = 3
    for key, value in meta:
        ws.cell(row, 1, key).font = BOLD; ws.cell(row, 2, value); row += 1
    row += 1
    for c, label in enumerate(["Métrica", "Total", "LONG", "SHORT"], 1):
        ws.cell(row, c, label).fill = DARK; ws.cell(row, c).font = WHITE
    metrics = [
        ("Operaciones", "total"), ("Resueltas", "resueltas"), ("Reversiones", "reversiones"),
        ("Cerradas por horizonte", "horizonte"), ("Pendientes", "pendientes"), ("Acierto %", "win_rate"),
        ("PnL bruto % (incluye slippage)", "pnl_pct_bruto"), ("Comisiones %", "costes_pct"), ("Deslizamiento estimado % (ya reflejado en bruto)", "deslizamiento_pct"),
        ("PnL neto % sumado", "pnl_pct_total"), ("PnL neto % compuesto", "pnl_pct_compuesto"),
        ("PnL neto % medio por operación", "pnl_pct_medio"), ("Profit factor (ratio)", "profit_factor"),
        ("Drawdown máximo % (curva compuesta)", "max_drawdown_pct"),
    ]
    for ri, (label, key) in enumerate(metrics, row+1):
        ws.cell(ri, 1, label)
        for col, group in enumerate(["total", "long", "short"], 2):
            val = stats[group][key]
            ws.cell(ri, col, "∞" if isinstance(val, float) and val == float("inf") else val)
    for col, width in enumerate([42, 27, 25, 25], 1): ws.column_dimensions[get_column_letter(col)].width = width

    op = wb.create_sheet("Operaciones")
    headers = ["Hora entrada UTC", "Hora salida UTC", "Lado", "Entrada ejecutada", "Salida ejecutada", "Motivo salida",
               "Range", "BuyTrack entrada", "SellTrack entrada", "Velas mantenida", "PnL bruto %", "Comisión %", "Deslizamiento estimado %", "PnL neto %"]
    for c, h in enumerate(headers, 1): op.cell(1, c, h).fill = DARK; op.cell(1, c).font = WHITE
    for r, t in enumerate(trades, 2):
        vals = [datetime.fromtimestamp(t.entry_time/1000, timezone.utc).replace(tzinfo=None),
                datetime.fromtimestamp(t.exit_time/1000, timezone.utc).replace(tzinfo=None) if t.exit_time else None,
                t.side, t.entry, t.exit_price, t.exit_reason, t.range_value, t.buy_track_entry, t.sell_track_entry,
                t.bars_held, t.gross_pnl_pct, t.fees_pct, t.slippage_pct, t.pnl_pct]
        for c, value in enumerate(vals, 1): op.cell(r, c, value)
    op.freeze_panes = "A2"; op.auto_filter.ref = op.dimensions
    for c in range(1, len(headers)+1): op.column_dimensions[get_column_letter(c)].width = 20
    for r in range(2, op.max_row+1):
        op.cell(r, 1).number_format = "yyyy-mm-dd hh:mm"; op.cell(r, 2).number_format = "yyyy-mm-dd hh:mm"

    curve_ws = wb.create_sheet("Curva de capital")
    curve_headers = ["N.º operación", "Hora salida UTC", "Lado", "PnL neto %", "Capital", "Máximo previo", "Drawdown %"]
    for c, h in enumerate(curve_headers, 1): curve_ws.cell(1, c, h).fill = DARK; curve_ws.cell(1, c).font = WHITE
    for r, point in enumerate(equity_curve or [], 2):
        vals = [point["trade_no"], datetime.fromtimestamp(point["exit_time"]/1000, timezone.utc).replace(tzinfo=None),
                point["side"], point["pnl_pct"], point["equity"], point["peak"], point["drawdown_pct"]]
        for c, value in enumerate(vals, 1): curve_ws.cell(r, c, value)
        curve_ws.cell(r, 2).number_format = "yyyy-mm-dd hh:mm"
    curve_ws.freeze_panes = "A2"; curve_ws.auto_filter.ref = curve_ws.dimensions
    for c, width in enumerate([16, 23, 12, 16, 20, 20, 18], 1): curve_ws.column_dimensions[get_column_letter(c)].width = width

    out = BytesIO(); wb.save(out); out.seek(0); return out
