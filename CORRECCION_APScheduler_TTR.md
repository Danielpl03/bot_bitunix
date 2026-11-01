# Ajuste del supervisor TTR en APScheduler

## Síntoma

El monitor estaba programado cada 1 segundo con `max_instances=1`. Como cada ejecución recorre secuencialmente los símbolos y realiza operaciones de red/base de datos, algunas ejecuciones tardaban más de un segundo. APScheduler omitía el siguiente disparo mientras la ejecución anterior seguía activa (`maximum number of running instances reached`).

## Cambio

- Intervalo predeterminado: 15 segundos.
- Variable de entorno opcional: `TTR_SUPERVISOR_INTERVAL_SECONDS`.
- Rango permitido: 5 a 60 segundos; valores inválidos vuelven a 15.
- `max_instances=1` se conserva deliberadamente para evitar carreras, doble procesamiento de velas o escrituras concurrentes de una misma posición.
- `coalesce=True` se conserva para que los disparos perdidos no se acumulen.
- El monitor sigue utilizando WebSocket para precios y procesa las velas cerradas de 15 minutos; aumentar el intervalo del supervisor no cambia el timeframe de la estrategia.

Si el log sigue mostrando ejecuciones omitidas, revisa cuánto tarda el ciclo completo y aumenta `TTR_SUPERVISOR_INTERVAL_SECONDS` (por ejemplo, a 20 o 30). No aumentes `max_instances` para ocultar el aviso.


## Diagnóstico y cierre WebSocket

- Se registra la duración de cada símbolo y del ciclo completo del supervisor.
- Al detener la aplicación, el hook `post_stop` cierra las conexiones WebSocket antes de cerrar el event loop.
- Las desconexiones causadas por el apagado deliberado no se registran como fallos ni disparan reconexiones.
- Se conserva la reconexión automática con backoff para desconexiones reales.
