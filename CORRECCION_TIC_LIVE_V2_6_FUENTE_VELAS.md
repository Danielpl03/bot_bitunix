# TIC Live V2.6 — Corrección de retraso en la fuente de velas

## Problema
La versión anterior enviaba `end_time` a Bitunix usando como límite el timestamp de apertura de la última vela cerrada. En el endpoint de velas, ese límite podía excluir la vela esperada y dejar el checkpoint/indicador una vela más atrás. El comando `/tic_status` reflejaba correctamente el checkpoint antiguo, pero la obtención de datos impedía ponerse al día.

## Cambio
- TIC Live solicita el histórico más reciente sin enviar `endTime`.
- Filtra localmente las velas por timestamp de apertura y por cierre efectivo (`time + intervalo <= ahora`).
- En actualizaciones incrementales, aplica el cursor `start_time=last_bar_time+1`, pero no limita el extremo reciente mediante `endTime`.
- El calentamiento inicial y la reconstrucción por huecos usan el mismo criterio.
- Se mantiene el bloqueo de entradas retroactivas: solo la última vela cerrada esperada puede generar una nueva entrada, y debe haber cerrado después de activar la estrategia.
- No se modifican las reglas de TTR, RSI ni las reglas de entrada/salida de TIC.

## Pruebas
Se añadieron pruebas para verificar que la consulta no envía `end_time`, que selecciona la última vela cerrada disponible y que descarta la vela todavía en formación.
