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
- SL LONG: mínimo de la vela de ruptura y la anterior. SL SHORT: máximo de ambas.
- TP1: al cierre de la primera vela posterior a la entrada que cambia la dirección del canal; liquida `tp1_pct`.
- TP2: liquida el resto cuando el precio alcanza la banda superior (LONG) o inferior (SHORT) del canal nuevo capturado en TP1.
- Si SL y otro evento coinciden dentro de una vela, SL tiene prioridad en el backtest OHLC.

Comandos Telegram:

```text
/backtest_tic ETHUSDT 1h 1000 --tp1 50 --risk-usdt 10 --fee 0
/tic_live add ETHUSDT 1h 50 10
/tic_live remove ETHUSDT 1h
/tic_live list
/tic_status [ETHUSDT]
```

El porcentaje TP1 se configura individualmente en cada backtest o suscripción Live. Live es paper trading y no envía órdenes a Bitunix.
