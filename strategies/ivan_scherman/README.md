# Iván Scherman · 3 velas bajistas

Nueva estrategia añadida al proyecto TIC/TTR. Implementación Python de la lógica del Pine Script suministrado (`message.txt`).

## Reglas implementadas

- Solo posiciones LONG.
- Filtro de tendencia: SMA larga ascendente (opcional) y cierre por encima de SMA larga (opcional).
- Entrada al cierre cuando se acumulan N velas bajistas: `decreasing` exige cada cierre inferior al anterior; `red` exige cierre menor que apertura.
- Take profit: cierre por encima de SMA corta o toque de orden límite en la SMA de la vela anterior.
- Salidas opcionales por stop fijo %, ATR, mínimo de N mínimos por cierre, o tiempo.
- Comisión de ida y vuelta aplicada al resultado de cada operación.
- Prioridad intrabar igual al Pine: stop intrabar primero, después límite TP, después TP por cierre, stop por cierre y salida temporal.
- No se envían órdenes al exchange. Live es paper trading.

## Comandos Telegram

```text
/backtest_ivan BTCUSDT 1d 1000
/backtest_ivan BTCUSDT 1d 1000 --long 200 --bearish 3 --tp 7 --mode decreasing --exit close --stop none
/backtest_ivan BTCUSDT 1d 1000 --stop atr --atr-mult 4 --fee 0.05
/ivan_live start BTCUSDT
/ivan_live stop BTCUSDT
/ivan_live list
```

El backtest adjunta un Excel con resumen, operaciones, eventos y curva de equity. El live usa velas diarias cerradas para señales y cierres por vela; el stop porcentual/ATR y el TP límite se vigilan intrabar mediante WebSocket con fallback REST. Las suscripciones, trades y eventos se guardan en el mismo backend SQLite/PostgreSQL compartido por TTR, pero en tablas independientes `ivan_live_*`.

## API

`POST /api/backtest` admite `strategy: "ivan_scherman_3_bearish"` y exige `interval: "1d"`.

Ejemplo de `params`:

```json
{
  "sma_long": 200,
  "require_rising": true,
  "require_above": true,
  "bearish_mode": "decreasing",
  "bearish_bars": 3,
  "sma_tp": 7,
  "exit_mode": "close",
  "max_bars": 0,
  "stop_type": "none",
  "stop_pct": 10,
  "atr_length": 14,
  "atr_mult": 4,
  "stop_lookback": 2,
  "commission_pct": 0.05
}
```

- `GET /api/ivan/live/status`
- `GET /api/ivan/live/history`
- `GET /api/ivan/live/history/BTCUSDT`\n- `GET /api/ivan/live/stats`

## Notas de paridad

- El Pine declara uso de velas diarias; el backtest API y el comando Telegram rechazan otras temporalidades.
- Las SMA usan cierres. ATR usa True Range con suavizado RMA de TradingView.
- Las órdenes límite y stops intrabar se simulan con OHLC; si stop y TP se tocan en la misma vela, el stop tiene prioridad, como en el Pine.
- El precio de entrada se registra al cierre de la vela de señal.
- Las operaciones abiertas al final del histórico quedan como `OPEN`; no se fuerzan cierres artificiales.
- El monitor live calcula señales con velas diarias cerradas. Stop %/ATR y TP límite se supervisan intrabar con ticks WebSocket (fallback REST); los cierres por SMA, stop de mínimo por cierre y tiempo esperan el cierre diario.
- Los fills paper intrabar usan el precio de tick recibido; el TP límite usa el nivel SMA de referencia. No son ejecuciones reales ni garantizan el precio de un exchange.
- El script Pine es un indicador con simulación interna; esta integración es un motor de paper trading y backtesting, no una estrategia conectada a ejecución real.
