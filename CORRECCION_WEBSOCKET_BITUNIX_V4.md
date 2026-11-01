# Corrección WebSocket Bitunix Futures — V4

## Diagnóstico

El feed anterior combinaba los PING/PONG de control RFC6455 de la librería `websockets` con un ping JSON de Bitunix que solo se enviaba cuando `recv()` permanecía 25 segundos sin recibir ningún frame. Si el canal `trade` estaba activo, ese timeout no vencía y el heartbeat JSON documentado no se enviaba de forma periódica.

La documentación oficial de Bitunix Futures especifica el heartbeat JSON:

```json
{"op":"ping","ping":1732519687}
```

Y la respuesta incluye `op: "ping"` y el campo `pong`. También documenta un máximo de cinco mensajes por segundo por conexión, contando PING/PONG y mensajes JSON.

## Cambios

- Desactivados los PING/PONG de control de la librería (`ping_interval=None`) para evitar mantener dos mecanismos de heartbeat simultáneos.
- Añadido heartbeat JSON Bitunix periódico, independiente de la llegada de trades, cada 20 segundos.
- Cada ping debe recibir respuesta en un máximo de 10 segundos. Si no llega, se fuerza la reconexión y queda registrado `Bitunix JSON heartbeat timeout`.
- El lector procesa los mensajes de mercado y las respuestas de heartbeat desde una única tarea `recv()`.
- Se registran `last_ping_at`, `last_pong_at` y `heartbeat_rtt_ms` para diagnóstico.
- Añadido jitter de reconexión para evitar que varios símbolos vuelvan a conectar al mismo instante.
- Se conserva el cache de trades, el buffer, la reconexión progresiva, REST fallback y el cierre ordenado.
- No se modifican las reglas de estrategia, gestión de riesgo, entradas, salidas, SL ni TP.

## Parámetros

`BitunixTickerFeed` permite configurar `heartbeat_interval` y `heartbeat_timeout`; por defecto son 20 y 10 segundos. El intervalo mínimo aceptado es 5 segundos y el timeout mínimo 2 segundos.

## Verificación

Las pruebas unitarias cubren la detección del pong JSON, el envío periódico del ping sin depender de los trades, el cache, la cola de ticks y el cierre ordenado. La prueba automatizada no sustituye una validación en vivo contra Bitunix/Render.

Variables de entorno opcionales:

- `TTR_WS_STALE_SECONDS=10`
- `TTR_WS_HEARTBEAT_INTERVAL_SECONDS=20`
- `TTR_WS_HEARTBEAT_TIMEOUT_SECONDS=10`

Si una variable contiene un valor no numérico, el sistema registra una advertencia y utiliza el valor predeterminado.

## Cambios adicionales V4.1 — control por símbolo

- `/ttr_live stop SIMBOLO` desactiva la suscripción persistente y cierra solamente el WebSocket de ese símbolo; no afecta a los demás.
- Un símbolo detenido queda bloqueado frente a un `ensure()` tardío del supervisor; solo un nuevo `/ttr_live start SIMBOLO` lo habilita expresamente.
- El supervisor limpia conexiones de símbolos desactivados o configurados en modo REST.
- Repetir `/ttr_live start SIMBOLO` actualiza únicamente las opciones indicadas; las omitidas conservan sus valores actuales. Se conserva el estado de estrategia, `last_bar_time`, historial y posiciones abiertas.
- `/ttr_health` envía texto plano. Los componentes y mensajes dinámicos ya no se interpretan como Markdown, evitando `BadRequest: Can't parse entities` cuando aparecen `_`, `*`, `[` u otros caracteres especiales.
