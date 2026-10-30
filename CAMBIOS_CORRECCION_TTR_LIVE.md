# Correcciones TTR Live

Se conservaron las reglas de trading, los parámetros PSAR, la gestión TP/RE, el ledger de operaciones y la persistencia SQLite/PostgreSQL.

## Cambios

- HTTPXRequest de Telegram con pool y timeouts explícitos para requests y getUpdates.
- La confirmación de `/ttr_live start` ya no revierte ni interrumpe la suscripción si Telegram tiene un timeout.
- El registro de comandos de Telegram durante el arranque es tolerante a fallos transitorios.
- Las notificaciones TTR se envían en tareas en segundo plano, con timeout de 8 segundos y un reintento; no bloquean el monitor.
- Las llamadas síncronas a Bitunix (precio y velas) y las operaciones de PostgreSQL/SQLite dentro del monitor se ejecutan mediante `asyncio.to_thread`.
- Las velas 15m solo se descargan cuando puede existir una vela cerrada nueva. Si la API todavía no devuelve 20 velas, se espera 60 segundos antes de reintentar.
- La reconciliación del ledger se hace una vez por símbolo y proceso; se invalida al volver a suscribir el símbolo. Esto conserva la recuperación tras reinicios y evita consultar el ledger en cada tick.
- No se persiste el snapshot de mercado en PostgreSQL cuando no cambió, reduciendo escrituras por segundo.
- APScheduler mantiene una sola instancia del monitor, con `coalesce` y tolerancia de misfire.

## Validación

- `python -m py_compile bot.py ttr_live.py ttr_websocket.py`
- `pytest -q`: 37 pruebas aprobadas.

No se ha realizado una prueba de conectividad real contra Telegram, Bitunix ni la instancia PostgreSQL de producción. Después del deploy, validar `/ttr_live start BTCUSDT`, `/ttr_status` y los logs del monitor.
