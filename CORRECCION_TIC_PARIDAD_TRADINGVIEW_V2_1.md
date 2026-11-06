# Corrección TIC — Paridad Pine/TradingView V2.1

## Causa detectada

El port anterior solo inicializaba `trend` en la primera vela del dataset. El Pine
usa `if na(trend)` en cada vela: durante el calentamiento de ATR(200), la rama
`else` puede volver a dejar `trend` en `na`; en la siguiente vela Pine vuelve a
poner `trend := close`, `dir := 0`, `barsInTrend := 0` y `extension := 0`. La primera
vela con ATR válido también entra en esa rama y no evalúa un flip en esa vela.

La prueba anterior esperaba `direction=1` en esa primera vela válida y validaba
una evolución de `trend` que no corresponde al Pine original. Se corrigió el
algoritmo y sus expectativas.

## Histórico

El indicador es recursivo y no basta con cargar 200 velas para ATR. El backtest
por fechas usa hasta 9.000 velas de calentamiento y el modo por cantidad calcula
sobre hasta 10.000 velas, filtrando las operaciones al período pedido. `/indicators`
también consulta hasta 10.000 velas.

La igualdad absoluta exige el mismo punto inicial de cálculo y la misma fuente
OHLC que TradingView. El monitor Live aún usa una ventana de 450 velas; una
paridad estricta en Live requiere persistir el estado recursivo, trabajo separado
que no se declara completado en esta corrección.
