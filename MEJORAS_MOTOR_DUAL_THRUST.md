# Mejoras del motor Dual Thrust

## Cambios
- Las bandas se calculan con las N velas previas y la apertura de la vela actual; las velas actuales no intervienen en HH/LL/HC/LC.
- Las velas que tocan simultáneamente BuyTrack y SellTrack no generan una señal porque el OHLC no determina el orden intrabar.
- El vencimiento de `HORIZONTE` se procesa en la apertura de la vela correspondiente incluso si esta es ambigua.
- Se añadieron `--fee-bps` y `--slippage-bps`, aplicados por lado. La comisión se descuenta del PnL y el deslizamiento adverso modifica los precios de ejecución.
- Se calcula PnL bruto (con slippage), comisión, PnL neto, retorno compuesto y drawdown sobre una curva de capital.
- El Excel incorpora la hoja `Curva de capital` y detalla costes por operación.
- Las posiciones abiertas se conservan como pendientes; no se cuentan como retorno realizado ni se marcan a mercado.
- Se añadieron cinco pruebas automatizadas para niveles sin lookahead, ambigüedad OHLC, costes, horizonte y curva de capital.

## Ejemplo

`/backtest_dual_thrust BTCUSDT 4h 1000 --fee-bps 5 --slippage-bps 2`

Ambos valores son puntos básicos por lado (1 bp = 0,01%). El ejemplo representa un escenario hipotético, no las tarifas reales de Bitunix. Por defecto los costes están en cero; configura los de tu cuenta.

## Limitaciones conocidas
- No se incluye funding ni spread histórico; el slippage es un supuesto constante.
- La curva de capital supone teóricamente asignar el 100% del capital a cada operación, sin apalancamiento. No es una simulación del margen real.
- Con velas OHLC no se puede reconstruir la secuencia intrabar ni las colas de ejecución.
- La comparación fuera de muestra debe realizarse con períodos UTC separados, sin reajustar parámetros mirando el tramo reservado.
