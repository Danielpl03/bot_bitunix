# TTR Pine v6 parity port

## Fuente de verdad

El módulo `ttr_pine.py` es una traducción de las fuentes incluidas por el usuario:
- `pine_reference/ttr.pine` (indicador Trend Trader-Remastered)
- `pine_reference/tac.pine` (`Tac.sar`, `Tac.BWFractals`)
- `pine_reference/toolsPos.pine` (`PosInfo`, `PosAction`)
- `pine_reference/toolsFluent.pine` (contenedor auxiliar usado por Tac)

## Reglas portadas

- `Tac.sar` no es `ta.sar()` de TradingView. Se porta su inicialización en `bar_index == 1`, el cálculo por etapas y sus tres salidas: `result`, `lagging` y `fast`.
- La entrada/reversión usa el cruce de `high`/`low` contra el `lagging` de la vela actual y anterior, incluyendo las condiciones adicionales `lagging[1] > high[1]` o `lagging[1] < low[1]`.
- El precio de referencia guardado por `ToolsPos.enter` es el `psar_lagging` de la vela de señal, no el cierre.
- BW fractals usan el patrón fijo de cinco velas con pivote en `i-2`, comparación contra el fractal anterior del mismo tipo, y confirmación en la vela actual.
- `Tac.BWFractals.eval` registra el breakout usando `close[1]`/`open[1]`, por lo que queda disponible un bar después del breakout. Se conservan los arrays HH/LL y el `boTime`.
- TP/RE requieren las velas engulfing definidas por `upBoEligible`/`downBoEligible`, examinan únicamente los últimos `fracLbHigh`/`fracLbLow` HH/LL y respetan el momento de entrada, las marcas consumidas y los últimos TP/RE.
- `ToolsPos.action(RE)` registra una acción; no añade contratos ni cambia el precio de entrada.
- Un TP que lleva la exposición contable a 0% no resetea `PosInfo`; el indicador sigue activo hasta el cruce opuesto o una nueva entrada, como Pine.

## Diferencias de ejecución que siguen siendo explícitas

- El Pine es un indicador, no una estrategia de TradingView que envíe órdenes. Los fills, comisiones, slippage y porcentajes TP del backtest son convenciones de simulación Python.
- El Pine no define stop-loss ni trailing stop. Se eliminó el stop PSAR intrabar inventado en la versión Python anterior. El lagging PSAR solo genera entradas y reversiones.
- `--re-entry` / `enter_on_reentry` se conserva por compatibilidad, pero no agrega exposición en el port fiel. RE se informa como señal.
- En el backtest, las señales se evalúan al cierre de cada vela. Las reversiones se contabilizan al cierre; la entrada principal usa el valor lagging PSAR que Pine guarda en `PosInfo`.
- El módulo live sigue siendo **paper trading**. No se enviarán órdenes reales a Bitunix.

## Validación ejecutada

- `python -m py_compile bot.py ttr_live.py backtest_trend_trader_remastered.py ttr_pine.py xlsx_export_ttr.py`
- `pytest -q`: 41 pruebas aprobadas después de añadir la prueba de regresión de fractales.
- No se ha ejecutado una comparación directa de todas las velas contra TradingView. Para afirmar paridad total se necesita exportar desde TradingView las series `psar`, `psar_lagging`, `psar_fast` y las señales BUY/SELL/TP/RE de un mismo símbolo, temporalidad, rango y configuración, y compararlas con el JSON Python.
