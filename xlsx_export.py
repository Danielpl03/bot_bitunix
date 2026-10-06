"""
Exporta resultados de backtest a un archivo .xlsx en memoria.

Módulo puro (no conoce Telegram ni Bitunix): recibe los datos ya
calculados por backtest.py y arma un Workbook de openpyxl. Pensado para
adjuntar el resultado como documento descargable desde el bot de
Telegram, pero reutilizable desde cualquier otro consumidor.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from backtest import (
    OUTCOME_NA,
    OUTCOME_PENDING,
    OUTCOME_SL,
    OUTCOME_TP,
    TP_RATIOS,
    BacktestSignal,
    best_scenario,
    ratio_label,
)

FONT_NAME = "Arial"
HEADER_FILL = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
HEADER_FONT = Font(name=FONT_NAME, bold=True, color="FFFFFF")
TITLE_FONT = Font(name=FONT_NAME, bold=True, size=14)
LABEL_FONT = Font(name=FONT_NAME, bold=True)
LONG_FILL = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
SHORT_FILL = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
SECTION_FILL = PatternFill(start_color="E5E7EB", end_color="E5E7EB", fill_type="solid")
TP_FILL = LONG_FILL  # verde: llegó al objetivo
SL_FILL = SHORT_FILL  # rojo: cortada por stop


def _ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None)


def _style_header_row(ws, row: int, n_cols: int) -> None:
    for col in range(1, n_cols + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")


def _autosize(ws, widths: list[int]) -> None:
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width


def _write_summary_sheet(
    ws,
    symbol: str,
    interval: str,
    horizon: int,
    n_candles: int,
    first_date: datetime,
    last_date: datetime,
    stats: dict,
) -> None:
    ws.title = "Resumen"

    ws["A1"] = f"Backtest {symbol} — {interval}"
    ws["A1"].font = TITLE_FONT
    ws.merge_cells("A1:D1")

    meta = [
        ("Símbolo", symbol),
        ("Intervalo", interval),
        ("Horizonte máx. (velas)", horizon),
        ("Velas analizadas", n_candles),
        ("Desde", first_date.strftime("%Y-%m-%d %H:%M")),
        ("Hasta", last_date.strftime("%Y-%m-%d %H:%M")),
        ("Generado (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")),
    ]
    row = 3
    for label, value in meta:
        ws.cell(row=row, column=1, value=label).font = LABEL_FONT
        ws.cell(row=row, column=2, value=value)
        row += 1

    row += 1
    headers = ["Métrica", "Total", "LONG", "SHORT"]
    for col, text in enumerate(headers, start=1):
        ws.cell(row=row, column=col, value=text)
    _style_header_row(ws, row, len(headers))
    row += 1

    groups = (("total", 2), ("long", 3), ("short", 4))

    def _put(r: int, col: int, value, fmt: str) -> None:
        cell = ws.cell(row=r, column=col)
        if value is None:
            cell.value = "—"
        elif fmt == "pct":
            cell.value = round(value, 2)
            cell.number_format = '0.00"%"'
        elif fmt == "pct_signed":
            cell.value = round(value, 2)
            cell.number_format = '+0.00"%";-0.00"%"'
        elif fmt == "r":
            cell.value = round(value, 2)
            cell.number_format = '+0.00"R";-0.00"R"'
        else:
            cell.value = value

    # Métricas generales (no dependen del ratio)
    for label, key, fmt in (
        ("Señales totales", "total", "int"),
        ("Riesgo medio (entrada → stop loss)", "riesgo_medio_pct", "pct"),
    ):
        ws.cell(row=row, column=1, value=label).font = LABEL_FONT
        for group, col in groups:
            _put(row, col, stats[group][key], fmt)
        row += 1

    # Un bloque por escenario de objetivo
    scenario_rows = [
        ("Operaciones resueltas (objetivo, stop u horizonte)", "resueltas", "int"),
        ("Llegan al objetivo antes que al stop", "tp", "int"),
        ("Cortadas por el stop (pérdida = -1R)", "sl", "int"),
        ("Cerradas al llegar al horizonte", "horizonte", "int"),
        ("Pendientes (aún sin resolver)", "pendientes", "int"),
        ("Win rate (resultado > 0)", "win_rate", "pct"),
        ("R total", "r_total", "r"),
        ("R medio por operación", "r_medio", "r"),
        ("Cambio total", "cambio_total_pct", "pct_signed"),
        ("Cambio medio por operación", "cambio_medio_pct", "pct_signed"),
    ]
    for ratio in TP_RATIOS:
        for col in range(1, 5):
            ws.cell(row=row, column=col).fill = SECTION_FILL
        ws.cell(row=row, column=1, value=f"Escenario objetivo {ratio_label(ratio)}").font = LABEL_FONT
        row += 1
        for label, key, fmt in scenario_rows:
            ws.cell(row=row, column=1, value=label).font = LABEL_FONT
            for group, col in groups:
                _put(row, col, stats[group]["escenarios"][ratio][key], fmt)
            row += 1

    # Comparación final
    for col in range(1, 5):
        ws.cell(row=row, column=col).fill = SECTION_FILL
    ws.cell(row=row, column=1, value="Mejor escenario").font = LABEL_FONT
    row += 1
    ws.cell(row=row, column=1, value="Por R medio por operación").font = LABEL_FONT
    for group, col in groups:
        best = best_scenario(stats[group])
        ws.cell(row=row, column=col, value=ratio_label(best) if best is not None else "—")
    row += 2

    notes = [
        "Notas del cálculo:",
        "• Entrada al cierre de la vela de la señal; se recorren las velas siguientes con sus máximos y mínimos.",
        "• Si el precio toca el stop, la operación se corta ahí: pérdida = distancia entrada → stop (-1R).",
        "• Objetivo 0.5:1 = 0.5 × la distancia entrada → stop; 1:1 = 1 × esa distancia. Cada escenario se simula por separado.",
        "• Si en una misma vela se tocan stop y objetivo, se cuenta como stop (no se puede saber el orden con velas).",
        f"• Si pasan {horizon} velas sin tocar stop ni objetivo, se cierra al cierre de esa vela.",
        "• No incluye comisiones ni slippage.",
    ]
    for i, text in enumerate(notes):
        cell = ws.cell(row=row + i, column=1, value=text)
        if i == 0:
            cell.font = LABEL_FONT

    _autosize(ws, [52, 12, 12, 12])
    ws.freeze_panes = "A1"


def _write_signals_sheet(ws, results: list[BacktestSignal]) -> None:
    ws.title = "Señales"

    # (encabezado, ancho, formato) — las columnas por escenario se añaden después
    columns: list[tuple[str, int, str]] = [
        ("Fecha (UTC)", 17, "date"),
        ("Lado", 7, ""),
        ("Precio cierre", 13, "price"),
        ("%K", 9, ""),
        ("%D", 9, ""),
        ("Banda inferior", 13, "price"),
        ("Banda media", 13, "price"),
        ("Banda superior", 13, "price"),
        ("Stop loss", 13, "price"),
        ("Riesgo %", 10, "pct"),
    ]
    for ratio in TP_RATIOS:
        label = ratio_label(ratio)
        columns += [
            (f"Objetivo {label}", 14, "price"),
            (f"Llega a {label}", 14, "price"),
            (f"Salida {label}", 12, ""),
            (f"% resultado {label}", 16, "pct_signed"),
            (f"R {label}", 10, "r"),
        ]

    for col, (text, _, _) in enumerate(columns, start=1):
        ws.cell(row=1, column=col, value=text)
    _style_header_row(ws, 1, len(columns))

    for i, r in enumerate(results, start=2):
        values: list = [
            _ms_to_dt(r.time),
            r.side,
            r.close,
            r.k,
            r.d,
            r.lower,
            r.middle,
            r.upper,
            r.stop_loss,
            round(r.risk_pct, 2),
        ]
        outcomes: list[str] = []
        for ratio in TP_RATIOS:
            sc = r.scenarios[ratio]
            outcomes.append(sc.outcome)
            # "Llega a": precio en que se cumple el ratio; "no" si no llegó o
            # el stop se tocó primero; "pendiente" si aún no hay velas para saberlo.
            if sc.reached_price is not None:
                reached = sc.reached_price
            elif sc.outcome == OUTCOME_PENDING:
                reached = "pendiente"
            elif sc.outcome == OUTCOME_NA:
                reached = "N/D"
            else:
                reached = "no"
            values += [
                sc.target,
                reached,
                sc.outcome,
                round(sc.pct_change, 2) if sc.pct_change is not None else None,
                round(sc.r_multiple, 2) if sc.r_multiple is not None else None,
            ]

        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=i, column=col, value=value)
            fmt = columns[col - 1][2]
            if fmt == "date":
                cell.number_format = "yyyy-mm-dd hh:mm"
            elif fmt == "price" and isinstance(value, (int, float)):
                cell.number_format = "0.0000"
            elif fmt == "pct" and value is not None:
                cell.number_format = '0.00"%"'
            elif fmt == "pct_signed" and value is not None:
                cell.number_format = '+0.00"%";-0.00"%"'
            elif fmt == "r" and value is not None:
                cell.number_format = '+0.00"R";-0.00"R"'

        ws.cell(row=i, column=2).fill = LONG_FILL if r.side == "LONG" else SHORT_FILL

        # Colorear "Llega a" y "Salida" de cada escenario según cómo terminó
        for idx, outcome in enumerate(outcomes):
            base_col = 11 + idx * 5  # primera columna de este escenario
            fill = TP_FILL if outcome == OUTCOME_TP else SL_FILL if outcome == OUTCOME_SL else None
            if fill is not None:
                ws.cell(row=i, column=base_col + 1).fill = fill
                ws.cell(row=i, column=base_col + 2).fill = fill

    _autosize(ws, [width for _, width, _ in columns])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{len(results) + 1}"


def _write_candles_sheet(ws, klines: list[dict]) -> None:
    ws.title = "Velas"
    headers = ["Fecha (UTC)", "Open", "High", "Low", "Close"]
    for col, text in enumerate(headers, start=1):
        ws.cell(row=1, column=col, value=text)
    _style_header_row(ws, 1, len(headers))

    for i, k in enumerate(klines, start=2):
        values = [
            _ms_to_dt(int(k["time"])),
            float(k.get("open", 0)),
            float(k["high"]),
            float(k["low"]),
            float(k["close"]),
        ]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=i, column=col, value=value)
            cell.number_format = "yyyy-mm-dd hh:mm" if col == 1 else "0.0000"

    _autosize(ws, [17, 13, 13, 13, 13])
    ws.freeze_panes = "A2"


def build_backtest_workbook(
    symbol: str,
    interval: str,
    horizon: int,
    klines: list[dict],
    results: list[BacktestSignal],
    stats: dict,
) -> BytesIO:
    """
    Arma el workbook completo del backtest con 3 hojas (Resumen, Señales,
    Velas) y lo devuelve como BytesIO, listo para adjuntar como documento.
    """
    first_date = _ms_to_dt(klines[0]["time"])
    last_date = _ms_to_dt(klines[-1]["time"])

    wb = Workbook()
    summary_ws = wb.active
    _write_summary_sheet(summary_ws, symbol, interval, horizon, len(klines), first_date, last_date, stats)

    signals_ws = wb.create_sheet("Señales")
    _write_signals_sheet(signals_ws, results)

    candles_ws = wb.create_sheet("Velas")
    _write_candles_sheet(candles_ws, klines)

    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.font is None or cell.font.name != FONT_NAME:
                    existing = cell.font
                    cell.font = Font(
                        name=FONT_NAME,
                        bold=existing.bold if existing else False,
                        color=existing.color if existing else None,
                        size=existing.size if existing else 11,
                    )

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
