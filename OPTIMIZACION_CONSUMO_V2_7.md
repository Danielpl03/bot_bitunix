# Optimización de consumo V2.7

## Cambios

1. **Bitunix / TIC incremental:** `get_kline_history` con `start_time` ahora pagina hacia delante desde el checkpoint. En una revisión normal obtiene como máximo una página (200 velas) y normalmente solo una vela nueva. El histórico profundo de 10.000 velas queda reservado para el calentamiento inicial o reconstrucciones.
2. **Supabase / PostgreSQL:** TTR, TIC y RSI comparten el mismo `TTRLiveStore`; se añadió un pool PostgreSQL acotado y seguro para concurrencia. Valores configurables: `TTR_DB_POOL_MIN` (1 por defecto) y `TTR_DB_POOL_MAX` (4 por defecto, máximo 6). SQLite conserva su comportamiento anterior.
3. **Persistencia TTR:** las cotizaciones de mercado se mantienen en memoria y se guardan como máximo cada 60 segundos si no hay una vela nueva. Los cambios de operaciones, eventos, posiciones y checkpoints de velas siguen guardándose inmediatamente.
4. **Frecuencia de supervisión:** TIC Live y RSI Live pasan de 10 a 30 segundos. TTR conserva su frecuencia configurable y el WebSocket prioritario para la vigilancia intrabar.
5. **Logs:** los mensajes INFO de finalización de cada ciclo TTR pasan a DEBUG; errores y cambios de estado de salud se mantienen.

## Integridad de trading y datos

No se cambian las fórmulas de indicadores, las condiciones de entrada, los SL/TP, las reglas de cierre, los filtros, ni la gestión de posiciones de TTR, RSI o TIC. No se borran tablas, registros, operaciones, configuraciones ni checkpoints. Las migraciones de esquema existentes siguen siendo aditivas.

## Verificación

- Compilación Python de todos los módulos.
- Suite pytest: 102 pruebas correctas.
- Pruebas añadidas para paginación incremental hacia delante y reutilización del pool PostgreSQL (pool simulado).
- No se realizó conexión real a Bitunix ni a la base Supabase de producción desde este entorno.
