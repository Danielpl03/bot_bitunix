# Supervisión y estadísticas globales de TTR Live

## Nuevos comandos

- `/ttr_health`: muestra el último heartbeat del supervisor y los estados persistidos de worker, base de datos, Telegram, Bitunix, WebSocket y monitores por símbolo.
- `/ttr_stats`: muestra métricas acumuladas de todas las operaciones y un desglose por símbolo.
- `/ttr_history`: sin argumentos devuelve las últimas 50 operaciones globales; `/ttr_history BTCUSDT` conserva el historial filtrado por símbolo.

## Métricas

- Total de operaciones, abiertas y cerradas.
- Acierto de operaciones cerradas (ganadoras/perdedoras).
- PnL realizado acumulado, incluyendo TP parciales ya realizados en posiciones que siguen abiertas.
- Profit factor sobre PnL realizado; media, mejor y peor operación cerrada.
- PnL flotante estimado sobre el último precio guardado para el porcentaje de posición restante.
- PnL neto estimado = realizado + flotante.

Los porcentajes son ponderados por el porcentaje de posición contabilizado por TTR. No representan rentabilidad sobre el capital total de la cuenta y no incluyen comisiones, funding ni tamaño/apalancamiento de la posición. El flotante puede quedar sin marcar si no hay precio reciente guardado.

## Supervisión

Los incidentes se persisten en `ttr_live_health`. Los cambios de estado se escriben solo cuando hay transición para no generar escrituras cada segundo. Telegram `RetryAfter` se registra como `RATE_LIMITED`; los errores por símbolo se aíslan para que un símbolo no detenga el resto. Los fallos de DB se reflejan también en logs; si la propia DB no está disponible, no es posible persistir el incidente en esa DB.

Render no expone al proceso de la aplicación el contador de horas gratuitas ni permite detectar desde Python una suspensión externa mientras el proceso está detenido. `/ttr_health` informa del heartbeat más reciente; para cuota/suspensión se debe revisar el dashboard de Render. Un servicio suspendido no puede enviarse a sí mismo una alerta.

## Despliegue

Desplegar `bot.py`, `ttr_live.py` y `test_ttr_reporting.py` junto con el resto del proyecto. El esquema de salud se crea automáticamente en el backend configurado. En producción usar PostgreSQL (`TTR_DB_BACKEND=postgres` y `DATABASE_URL` o `SUPABASE_DB_URL`); SQLite en filesystem efímero de Render no garantiza persistencia entre reinicios.

TTR Live sigue siendo paper trading: no envía órdenes reales a Bitunix.

## Preferencias de notificaciones por símbolo

`/ttr_notify BTCUSDT` muestra las preferencias y `/ttr_notify BTCUSDT open|close|tp|stop on|off` cambia cada tipo de aviso independientemente. Las cuatro opciones se guardan en la configuración persistente del símbolo y sobreviven a `/ttr_live start` y a los reinicios del servicio. Por defecto están activadas para mantener el comportamiento anterior.

- `open`: nueva operación.
- `close`: cierre por reversión o reinicio de entrada.
- `tp`: señal TP y TP parcial registrado.
- `stop`: cambios del precio de stop.

**Importante sobre stop:** la versión Pine TTR que replica este proyecto no define stop-loss ni trailing stop; el campo `stop` permanece sin valor por fidelidad al indicador. La preferencia `stop` queda disponible para silenciar/activar avisos cuando una integración que realmente actualice el stop emita ese evento; no se inventa un stop ni se altera la lógica Pine.

## Recuperación tras reinicio o suspensión

Al iniciar el monitor, la fila `OPEN` de `ttr_live_trades` es la fuente de verdad para reconstruir la posición, incluyendo lado, trade ID, entrada, promedio, porcentaje restante, PnL realizado parcial, TP contados y fractales ya marcados. El snapshot completo se guarda también en el `payload` de la operación para que la recuperación no dependa exclusivamente de `ttr_live_config.state`.

Si el servicio estuvo detenido, al recuperar una operación abierta se mueve el checkpoint `last_bar_time` a la última vela de 15 minutos ya cerrada en el momento de reanudación. No se reproducen ni contabilizan señales, TP, RE, reversiones o entradas que hayan ocurrido durante la ausencia. Desde ese punto se vuelve a procesar normalmente cada nueva vela cerrada. El precio de mercado actual se refresca al reanudar; no se simula una salida retroactiva durante el downtime.
