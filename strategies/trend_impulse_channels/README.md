# Trend Impulse Channels (Zeiierman)

Port Python del indicador Pine Script v6 proporcionado por el usuario. Esta
carpeta queda aislada de las estrategias existentes. Por ahora contiene solo
el indicador y sus pruebas: **no define entradas, salidas, SL, TP ni backtest
breakout**, porque esas reglas se acordarán posteriormente.

## API

```python
from strategies.trend_impulse_channels import calculate_trend_impulse_channels

values = calculate_trend_impulse_channels(
    opens, highs, lows, closes,
    flip_mult=2.86,
    max_step_atr=-0.034,
    band_mult=2.02,
    hold_bars=0,
)

# Series alineadas con las velas:
values["trend"][-1]
values["upper"][-1]
values["lower"][-1]
values["direction"][-1]       # 1 alcista, -1 bajista, 0 neutral
values["atr"][-1]
values["lower_retest"][-1]
values["upper_retest"][-1]
values["trend_step"][-1]
```

El indicador también devuelve `trigger`, `step_base`, `max_step`, `step_size`,
`bars_in_trend`, `extension`, `trend_direction`, `bullish_step` y
`bearish_step`. Los valores no disponibles durante el calentamiento ATR son
`None`, equivalentes a `na` de Pine.

## Parámetros Pine por defecto

| Pine | Python | Default |
|---|---|---:|
| `flipMult` | `flip_mult` | 2.86 |
| `maxStepAtr` | `max_step_atr` | -0.034 |
| `bandMult` | `band_mult` | 2.02 |
| `holdBars` | `hold_bars` | 0 |
| `ta.atr(200)` | `atr_period` | 200 (fijo en el Pine compartido) |

Se conserva literalmente la fórmula `math.min(stepBase + 0.0093 * barsInTrend * atr, maxStep)`, incluso con `maxStepAtr` negativo. No se “corrige” el signo porque eso cambiaría el indicador fuente.

## Licencia y atribución

Código derivado de Trend Impulse Channels de Zeiierman, compartido bajo
CC BY-NC-SA 4.0. La adaptación mantiene atribución y se distribuye bajo la
misma licencia no comercial/compartir igual:
https://creativecommons.org/licenses/by-nc-sa/4.0/

## Motor Breakout (backtest y paper Live)

El motor se encuentra en `engine.py`, el paper Live persistente en `live.py` y el exportador en `xlsx_export.py`.

- LONG: `high > upper` y vela alcista (`close > open`). SHORT: `low < lower` y vela bajista (`close < open`). No se filtra por dirección del canal.
- La vela de ruptura no puede ser también la vela de cambio de dirección del canal.
- Entrada al cierre de la vela de ruptura.
- SL configurable mediante `sl_candles` (1–200): LONG usa el mínimo de las últimas N velas, incluida la vela de ruptura; SHORT usa el máximo de esas mismas N velas. El valor predeterminado es 2, que conserva el comportamiento anterior.
- TP1: al cierre de la primera vela posterior a la entrada que cambia la dirección del canal; liquida `tp1_pct`.
- TP2: liquida el resto cuando el precio alcanza la banda superior (LONG) o inferior (SHORT) del canal nuevo capturado en TP1.
- Si SL y otro evento coinciden dentro de una vela, SL tiene prioridad en el backtest OHLC.

Comandos Telegram:

```text
/backtest_tic ETHUSDT 1h 1000 --tp1 50 --risk-usdt 10 --fee 0 --sl-candles 5
/tic_live add ETHUSDT 1h 50 10 5
/tic_live remove ETHUSDT 1h
/tic_live list
/tic_status [ETHUSDT]
```

El porcentaje TP1 y el número de velas para el SL se configuran individualmente en cada backtest o suscripción Live. `--sl-candles` admite 1–200 y por defecto es 2. En Live el formato es `/tic_live add SIMBOLO TF TP1_PCT RIESGO_USDT VELAS_SL`. Live es paper trading y no envía órdenes a Bitunix.


## Paridad con TradingView (corrección V2.1)

El indicador es recursivo: `trend` depende del estado acumulado de velas anteriores.
La implementación replica ahora el `if na(trend)` del Pine en cada vela mientras
la serie siga siendo `na`, incluida la primera vela con ATR(200) válido.

Para reducir diferencias por un punto de inicio distinto, `/indicators` carga hasta
10.000 velas y `/backtest_tic` calcula sobre hasta 10.000 velas históricas; en
backtests por fechas reserva hasta 9.000 velas anteriores como calentamiento.
La coincidencia exacta con TradingView requiere además que ambos usen el mismo
mercado, fuente de OHLC, vela inicial, zona horaria y velas cerradas. Si TradingView
tiene más histórico cargado que Bitunix, la línea `trend` puede seguir difiriendo
porque su estado no es un promedio que se estabilice solo con ATR(200).

**Nota Live:** el monitor Live todavía recalcula desde su ventana operativa de 450
velas. Para una paridad estricta en Live será necesario inicializar y persistir el
estado recursivo del indicador desde el histórico completo y actualizarlo vela a
vela; no se debe interpretar el cambio de esta versión como una garantía de
paridad histórica en Live.


### Correcciones de ejecución (V2.2)
- Se considera cambio/reinicio de canal cuando `bars_in_trend` vuelve a 1. Esto detecta también un nuevo canal que conserva la dirección anterior; comparar solamente `direction` no era suficiente.
- Una vela que reinicia el canal no puede abrir una operación, aunque su ruptura y su color cumplan los demás filtros.
- Al ejecutarse TP1 al cierre de una vela de reinicio de canal, el stop de la cantidad restante se mueve al precio de entrada (break-even). El nuevo stop se aplica desde la vela siguiente, no retroactivamente dentro de la vela que ejecutó TP1.
- El Excel distingue `SL inicial` y `SL actual`, para que el movimiento a break-even no oculte el stop de riesgo original.

### SL configurable (V2.3)
- Backtest: `--sl-candles N` (alias `--sl-lookback N`), rango 1–200; se incluyen la vela de ruptura y las N-1 velas inmediatamente anteriores.
- Live: `/tic_live add SYMBOL TF TP1_PCT RISK_USDT N`; el valor se guarda en la configuración de esa suscripción.
- LONG: SL en el menor `low` de la ventana. SHORT: SL en el mayor `high` de la ventana.
- Predeterminado: 2 velas, equivalente al stop anterior.
- El Excel registra el parámetro en Resumen y en cada fila de Operaciones.


## TIC Live: comandos, recuperación y calentamiento persistente

Comandos equivalentes a la supervisión TTR:

- `/tic_live add BTCUSDT 1h 50 10 5` activa/actualiza una suscripción (TP1 %, riesgo USDT, velas SL).
- `/tic_live list` lista las suscripciones.
- `/tic_stop BTCUSDT [1h]` o `/tic_live stop BTCUSDT [1h]` detiene nuevas entradas. Si hay operaciones abiertas, la suscripción permanece en supervisión hasta cerrarlas.
- `/tic_status [BTCUSDT]` muestra estado, posiciones y último valor del indicador.
- `/tic_history [BTCUSDT]` consulta operaciones.
- `/tic_stats` muestra estadísticas globales y por símbolo.
- `/tic_notify BTCUSDT 1h [open|close|tp|stop] [on|off]` configura notificaciones por evento.
- `/tic_health` comprueba base de datos, monitor y checkpoints.
- `/tic_restart CONFIRMAR` elimina únicamente configuraciones, checkpoints, operaciones y eventos TIC.

### Calentamiento incremental

La primera activación descarga hasta 10.000 velas cerradas, calcula el indicador una sola vez y persiste un checkpoint compacto (ATR Wilder/RMA, cierre previo, línea de tendencia, dirección, contador de tendencia, extensión y bandas previas), junto con las últimas 200 velas para el SL configurable. Cada vela cerrada posterior actualiza ese checkpoint de forma incremental y lo guarda junto al cursor temporal. Tras un redespliegue se recupera el checkpoint y se procesan las velas cerradas pendientes; las entradas retroactivas se omiten, pero las operaciones ya abiertas siguen procesándose para detectar SL/TP. Si el hueco supera el histórico recuperable, se reconstruye el calentamiento y se registra una advertencia, sin abrir operaciones retroactivas.

Para persistencia entre despliegues de Render, configura `DATABASE_URL` o `SUPABASE_DB_URL` para usar PostgreSQL persistente. SQLite en almacenamiento efímero no garantiza conservar operaciones/checkpoints.

### Excel de backtesting

El Excel está desactivado por defecto. Ejemplo: `/backtest_tic ETHUSDT 1h 1000 --tp1 50`. Para solicitar el archivo: añade `--excel true`. También se admite `--no-excel`. El resumen de resultados se envía siempre por Telegram.


## Corrección Live: activación y sincronización de velas (V2.5)

- Cada alta/reactivación registra `activation_time` en la base de datos. Las señales de velas cuyo cierre ocurrió antes de ese instante no pueden abrir operaciones. El checkpoint y las operaciones abiertas se conservan.
- Una entrada nueva solo puede originarse en la vela cuyo timestamp de apertura coincide con el último cierre teórico de la temporalidad. Si Bitunix entrega velas retrasadas, el monitor no interpreta la última vela recibida como si fuera la última vela cerrada y bloquea entradas retroactivas.
- `/tic_status` identifica el timestamp de apertura de la última vela cerrada procesada y advierte si la API todavía no ha entregado la vela que acaba de cerrar.
- La regla de breakout queda explícita: LONG requiere perforación de banda superior y cierre alcista; SHORT requiere perforación de banda inferior y cierre bajista. Una vela alcista que perfore la banda inferior no puede abrir SHORT.
- La migración añade `activation_time` sin borrar configuraciones, checkpoints, operaciones ni eventos existentes.
