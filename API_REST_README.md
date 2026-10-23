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
