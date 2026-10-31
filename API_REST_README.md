# API REST para Backtest Lab

Esta API REST se ejecuta en el mismo proyecto Python que el bot, pero como proceso de servicio web. Para uso personal en Render puedes cambiar el **Start Command** del Web Service de `python bot.py` a `python api_server.py`. El bot de Telegram dejará de arrancar en ese servicio mientras uses este comando. Los módulos y comandos del bot permanecen en el repositorio y puedes volver al comando anterior cuando quieras.

## Render

- Build Command: `pip install -r requirements.txt`
- Start Command: `python api_server.py`
- `PORT`: Render lo define automáticamente.
- `BACKTEST_API_KEY`: opcional, recomendado para que terceros no consuman tu API. Genera un secreto largo y aleatorio.

No es necesario configurar claves privadas de Bitunix para descargar velas públicas. La API no expone rutas para abrir/cerrar posiciones.

## Frontend

En el frontend Node.js configura:

- `BACKEND_URL=https://TU-SERVICIO-PYTHON.onrender.com`
- `BACKEND_API_KEY`: mismo valor que `BACKTEST_API_KEY` si activaste la protección.

## Rutas

- `GET /api/health`: estado y lista de estrategias.
- `POST /api/backtest`: recibe JSON con `strategy`, `symbol`, `interval`, `candles` o `startDate`/`endDate`, `horizon` y `params`.

Estrategias: `keltner`, `bollinger`, `rsi`, `reversion`, `squeeze`, `dual_thrust`.

Las fechas son UTC e inclusivas por día. La curva de capital es una estimación compuesta a partir del PnL porcentual de las operaciones que devuelven los motores; no implica tamaño de posición ni modela costes que la estrategia no simule.

## Local

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python api_server.py
```

La API queda en `http://localhost:8000`.


**Importante para las alertas programadas:** `requirements.txt` instala `python-telegram-bot` con los extras `webhooks` y `job-queue`. Si despliegas el bot en Render, usa `pip install -r requirements.txt` y verifica en los logs que se programen `check_alerts_1h` y `check_alerts_4h`; no debe aparecer `JobQueue no disponible`.

## Nueva estrategia: Trend Trader-Remastered (TTR)

La API admite `strategy: "trend_trader_remastered"`. El motor está en `backtest_trend_trader_remastered.py` y utiliza las velas OHLC de Bitunix. Parámetros en `params`:

- `pos_dir`: `Buy & Sell`, `Buy Only`, `Sell Only`.
- `psar_start` (0.02), `psar_increment` (0.02), `psar_max` (0.025).
- `frac_lb_high` (2), `frac_lb_low` (2).
- `sig_re_min_proximity` (0), `sig_re_buy_no_tp` (false), `sig_re_sell_no_tp` (false).
- `show_entry_buy`, `show_tp_buy`, `show_re_buy`, `show_exit_buy`, `show_entry_sell`, `show_tp_sell`, `show_re_sell`, `show_exit_sell` (los mismos valores predeterminados visibles en Pine).
- `fee_pct`, `slippage_pct` (costos por lado en porcentaje, predeterminados en 0).

Ejemplo de `params`:
```json
{"pos_dir":"Buy & Sell","psar_start":0.02,"psar_increment":0.02,"psar_max":0.025,"frac_lb_high":2,"frac_lb_low":2,"sig_re_min_proximity":0,"sig_re_buy_no_tp":false,"sig_re_sell_no_tp":false}
```

**Fidelidad:** ahora se incluyen los fuentes Pine entregados de `Tac`, `ToolsPos` y `ToolsFluent` en el módulo Python `ttr_pine.py`. Se portan el PSAR no estándar `Tac.sar`, la confirmación y ruptura de fractales BW, los filtros de engulfing, el cruce de lagging PSAR y las condiciones de TP/RE. Falta una prueba de paridad vela a vela contra una exportación de TradingView para validar la equivalencia de ejecución y los bordes temporales.

### Trend Trader-Remastered (TTR) — gestión de posición
Para `strategy: "trend_trader_remastered"`, `params` admite `enter_on_reentry` (parámetro legado; ignorado para preservar Pine), `tp_percentages` (lista, por ejemplo `[40,30,30]`), `fee_pct`, `slippage_pct` y los parámetros PSAR/fractales previos. La suma de `tp_percentages` no puede superar 100. Cada señal TP consume el siguiente porcentaje configurado en la simulación contable Python; esos porcentajes no forman parte de las bibliotecas Pine. La señal RE se registra como evento, pero no agrega exposición: `ToolsPos.action(RE)` solo guarda la acción. Una señal de cruce opuesto cierra el estado anterior y puede iniciar la dirección permitida. **El Pine no define stop-loss ni trailing stop**; el lagging PSAR solo se usa para entradas y reversiones.

## TTR Paper Trading en tiempo real (sin órdenes)

El bot incorpora un monitor de TTR que consulta precio cada 60 segundos y calcula el indicador únicamente con velas 15m cerradas. No envía órdenes a Bitunix.

Comandos Telegram (solo usuario autorizado):
- `/ttr_live start BTCUSDT [--dir Buy & Sell] [--tp 40,30,30] [--re-entry true|false]`
- `/ttr_live stop BTCUSDT`
- `/ttr_live list`
- `/ttr_status`
- `/ttr_history BTCUSDT`

Rutas de consulta:
- `GET /api/ttr/live/status`
- `GET /api/ttr/live/history/BTCUSDT`

Persistencia configurable: `TTR_DB_BACKEND=sqlite` usa SQLite en `/tmp/ttr_live.sqlite3` por defecto. Esto permite probar en Render Free sin Persistent Disk, pero es **efímero**: los datos pueden perderse en reinicios, redeploys o reemplazos de instancia. Para la persistencia definitiva usa Supabase PostgreSQL con `TTR_DB_BACKEND=postgres` y `DATABASE_URL` (o `SUPABASE_DB_URL`) igual a la URI PostgreSQL de Supabase. El esquema se crea automáticamente al iniciar; `supabase_ttr_schema.sql` contiene el DDL manual. La dependencia `psycopg[binary]` está en requirements.txt.

El monitor evalúa entradas/reversiones al cierre de velas 15m con la misma fórmula `Tac.sar`. No se vigila un stop intrabar porque el indicador Pine no define stop-loss. Con `--feed websocket`, el monitor consume trades para mostrar precio/estado; si el WebSocket queda inactivo usa REST como respaldo. La ejecución sigue siendo simulada.


## TTR Live: monitor de precio y estado

El indicador se calcula exclusivamente con velas 15m cerradas. En modo WebSocket, `--poll 5` configura únicamente el intervalo del respaldo REST; en modo `--feed rest`, configura el sondeo REST entre 2 y 30 segundos.

Ejemplo: `/ttr_live start BTCUSDT --dir Buy & Sell --tp 40,30,30 --re-entry false --poll 3`. Las reentradas siguen siendo opcionales mediante `--re-entry true|false`. `/ttr_status` muestra por cada suscripción activa precio consultado, intervalo de sondeo, PSAR y lagging PSAR de la última vela cerrada, hora de la vela, lado, entrada, exposición restante, PnL realizado y configuración TP/reentrada. `GET /api/ttr/live/status` devuelve las mismas configuraciones y estado estructurado.

El WebSocket/REST actualiza el precio de mercado, pero no activa cierres por stop; las señales TTR se evalúan al cierre 15m.


## TTR Live WebSocket / REST

`/ttr_live start BTCUSDT --feed websocket --poll 5 --re-entry false` usa el canal público `trade` de Bitunix para recibir precios ejecutados. Si no llegan mensajes durante `TTR_WS_STALE_SECONDS` (10 s por defecto), el monitor cambia automáticamente a REST con el intervalo `--poll`. Cuando el WebSocket vuelve a estar fresco, vuelve a priorizarlo. `--feed rest` mantiene el modo anterior sin WebSocket.

El supervisor del bot corre cada segundo, consume los eventos trade acumulados para actualizar la cotización; las señales se evalúan al cierre 15m. El PSAR se recalcula solo al cierre de la vela 15m. Esto sigue siendo paper trading: no se envían órdenes.
