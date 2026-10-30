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

**Limitación de fidelidad:** el Pine original importa `ToolsFluent`, `ToolsPos` y `Tac`; el código fuente de esas bibliotecas no viene incluido en el script compartido. El motor implementa PSAR, pivotes fractales confirmados, entradas/salidas por cruce del PSAR lagging y reglas visibles de TP/RE, pero algunas semánticas internas de `Tac.sar`, `Tac.BWFractals` y `ToolsPos.PosInfo/PosAction` se han aproximado. Para garantizar coincidencia vela a vela con TradingView habría que contrastar con el código de esas librerías o una exportación de señales de referencia.

### Trend Trader-Remastered (TTR) — gestión de posición
Para `strategy: "trend_trader_remastered"`, `params` admite `enter_on_reentry` (booleano), `tp_percentages` (lista, por ejemplo `[40,30,30]`), `fee_pct`, `slippage_pct` y los parámetros PSAR/fractales previos. La suma de `tp_percentages` no puede superar 100. Cada señal TP consume el siguiente porcentaje configurado; la reversión cierra el porcentaje restante y abre la dirección permitida. El stop sigue el lagging PSAR actualizado al cierre de cada vela y se aplica desde la siguiente. Los datos de fractales/TP/RE siguen siendo aproximados frente a las bibliotecas Pine privadas/publicadas externas.

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

Persistencia SQLite: por defecto `ttr_live.sqlite3` en el directorio de trabajo. En Render debe configurarse `TTR_LIVE_DB` a una ruta dentro de un Persistent Disk (por ejemplo `/var/data/ttr_live.sqlite3`) para conservar operaciones e historial tras reinicios/redeploys. Si no se monta disco persistente, el almacenamiento local puede perderse.

El stop se actualiza con PSAR actual al cierre 15m y queda activo desde la siguiente vela. El monitor de un minuto consulta el último precio de Bitunix; un toque del stop cierra la posición simulada y abre la contraria en el registro. La ejecución exacta entre ticks depende de la frecuencia de consulta y de la disponibilidad del endpoint público.


## TTR Live: monitor de precio y estado

El indicador se calcula exclusivamente con velas 15m cerradas. El monitor de precio consulta el ticker REST de Bitunix con una frecuencia configurable entre 2 y 30 segundos (por defecto 5s): `TTR_POLL_SECONDS=5` o `--poll 5` en `/ttr_live start`. La frecuencia de 2s es un sondeo REST rápido, no un flujo realmente instantáneo; para latencia inferior se recomienda integrar el WebSocket público de trades/ticker de Bitunix y conservar REST como reconciliación.

Ejemplo: `/ttr_live start BTCUSDT --dir Buy & Sell --tp 40,30,30 --re-entry false --poll 3`. Las reentradas siguen siendo opcionales mediante `--re-entry true|false`. `/ttr_status` muestra por cada suscripción activa precio consultado, intervalo de sondeo, PSAR y lagging PSAR de la última vela cerrada, hora de la vela, lado, entrada, stop, exposición restante, PnL realizado y configuración TP/reentrada. `GET /api/ttr/live/status` devuelve las mismas configuraciones y estado estructurado.

El sondeo REST tiene límites de latencia y de rate limit; el stop intrabar se detecta en el primer sondeo cuyo precio observado haya cruzado el nivel, no necesariamente en el instante exacto del toque.


## TTR Live WebSocket / REST

`/ttr_live start BTCUSDT --feed websocket --poll 5 --re-entry false` usa el canal público `trade` de Bitunix para recibir precios ejecutados. Si no llegan mensajes durante `TTR_WS_STALE_SECONDS` (10 s por defecto), el monitor cambia automáticamente a REST con el intervalo `--poll`. Cuando el WebSocket vuelve a estar fresco, vuelve a priorizarlo. `--feed rest` mantiene el modo anterior sin WebSocket.

El supervisor del bot corre cada segundo, consume los eventos trade acumulados y evalúa stops contra cada precio recibido. El PSAR se recalcula solo al cierre de la vela 15m. Esto sigue siendo paper trading: no se envían órdenes.
