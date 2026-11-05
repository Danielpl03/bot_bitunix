# Corrección TP RSI por zona extrema

## Regla
- LONG: cerrar al precio de cierre de la primera vela cerrada con RSI >= sobrecompra.
- SHORT: cerrar al precio de cierre de la primera vela cerrada con RSI <= sobreventa.
- No esperar a que el RSI salga de la zona extrema.
- El SL mantiene su comprobación intrabar y tiene prioridad si se toca dentro de la misma vela.

## Reproducción del backtest ETHUSDT 1h
Con el Excel entregado:
- Operación #5 (LONG): cierre 2026-09-11 13:00 UTC, precio 2628.61, RSI 82.402085.
- Operación #6 (SHORT): señal 2026-09-11 14:00 UTC, RSI 75.336545; ejecución en la apertura siguiente, 2026-09-11 15:00 UTC, precio 2606.53.

La entrada #6 ya estaba en el precio correcto en el Excel original; el defecto era el TP tardío de la operación #5.
