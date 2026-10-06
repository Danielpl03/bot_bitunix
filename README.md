# Bot de Telegram para Bitunix

Bot que consulta precios y gestiona tu cuenta de futuros en Bitunix: saldo,
posiciones abiertas, apertura y cierre de posiciones. Además incluye una
estrategia de entrada basada en estocástico + Bandas de Bollinger en velas
de 4h, con alertas automáticas y backtest sobre histórico.

## Estructura del proyecto

```
bitunix_client.py   # Lógica de conexión a la API de Bitunix (sin Telegram)
indicators.py        # Cálculo de indicadores técnicos (estocástico, Bollinger)
strategy.py           # Reglas de entrada de la estrategia (usa indicators.py)
backtest.py           # Backtest de la estrategia sobre histórico (usa strategy.py)
xlsx_export.py         # Arma el Excel del backtest (usa backtest.py, sin Telegram)
bot.py               # Lógica de Telegram: comandos, confirmaciones y formato
requirements.txt
```

`bitunix_client.py` es independiente de Telegram a propósito: expone
una clase `BitunixClient` que se puede importar desde cualquier otro
servicio (una API propia con FastAPI/Flask, un script, una app) para
usarla como backend común.

```python
from bitunix_client import BitunixClient

client = BitunixClient(api_key="...", secret_key="...")
account = client.get_account("USDT")
```

`indicators.py`, `strategy.py` y `backtest.py` siguen la misma idea: son
módulos puros, sin ningún import de Telegram ni de Bitunix, que solo
reciben listas de precios ya extraídas de las velas. Se pueden reutilizar
o testear por separado de todo lo demás.

## Comandos disponibles

| Comando | Descripción |
|---|---|
| `/price [SIMBOLO...]` | Precios (tu watchlist guardada o símbolos dados) |
| `/watchlist` | Ver la watchlist actual |
| `/watchlist add SIMBOLO` | Añadir un símbolo a la watchlist |
| `/watchlist remove SIMBOLO` | Quitar un símbolo de la watchlist |
| `/watchlist reset` | Volver a la watchlist por defecto (BTC, ETH, SOL, BNB, XRP) |
| `/indicators SIMBOLO [INTERVALO]` | Estocástico (14,3,3) y Bandas de Bollinger (20, 2σ). Intervalo por defecto `1h` |
| `/alerts` | Ver las monedas vigiladas por la estrategia de 4h |
| `/alerts add SIMBOLO` | Vigilar un símbolo (recibirás alerta cuando dé señal) |
| `/alerts remove SIMBOLO` | Dejar de vigilar un símbolo |
| `/alerts reset` | Vaciar la lista de vigilancia |
| `/alerts check [SIMBOLO]` | Evaluar la estrategia *ahora mismo* (sin esperar el cierre de vela), sobre un símbolo o sobre toda la lista |
| `/backtest SIMBOLO (INTERVALO) (N_VELAS) (HORIZONTE)` | Backtest de la estrategia sobre histórico + Excel con el detalle |
| `/balance [MONEDA]` | Balance de la cuenta de futuros (default `USDT`) |
| `/positions [SIMBOLO]` | Posiciones abiertas |
| `/open SIMBOLO BUY\|SELL CANTIDAD [PRECIO]` | Abrir posición (pide confirmación) |
| `/close POSITION_ID` | Cerrar posición a mercado (pide confirmación) |

`/balance`, `/positions`, `/open`, `/close`, `/watchlist` y `/alerts`
requieren `TELEGRAM_ALLOWED_USER_ID` configurado para restringir quién
puede usarlos; `/balance`, `/positions`, `/open` y `/close` además
necesitan `BITUNIX_API_KEY` y `BITUNIX_API_SECRET`.

## ⚠️ Seguridad — leer antes de desplegar

Este bot puede mover tu dinero real. Ten en cuenta:

- **Restringe el acceso.** Configura `TELEGRAM_ALLOWED_USER_ID` con tu
  `user_id` numérico de Telegram (puedes obtenerlo hablando con
  [@userinfobot](https://t.me/userinfobot)). Sin esta variable,
  *cualquiera* que encuentre tu bot podría ver tu saldo y abrir/cerrar
  posiciones.
- **Permisos mínimos en la API Key.** Al crear la API Key en Bitunix
  (Cuenta → API Management), NO actives el permiso de retiro (withdraw).
  Solo necesitas permisos de lectura y trading de futuros.
- **`/open` y `/close` piden confirmación** con un botón antes de
  ejecutar la orden — aun así, revisa bien símbolo, lado y cantidad
  antes de confirmar.
- Nunca subas tu `BITUNIX_API_SECRET` ni tu `TELEGRAM_BOT_TOKEN` a un
  repositorio público; van siempre como variables de entorno.



## 1. Crear el bot en Telegram

1. Habla con [@BotFather](https://t.me/BotFather) en Telegram.
2. Envía `/newbot` y sigue las instrucciones.
3. Copia el token que te entrega (algo como `123456789:ABC-...`).

## 2. Instalar dependencias

```bash
python -m venv venv
source venv/bin/activate  # en Windows: venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` debe incluir el extra `job-queue` de
`python-telegram-bot` (además de `webhooks`, que ya usas para Render):

```
python-telegram-bot[webhooks,job-queue]==21.4
requests==2.32.3
```

Sin el extra `job-queue` el bot arranca igual, pero las alertas
automáticas de `/alerts` nunca se disparan solas (verás un aviso en los
logs); `/alerts check` sí funciona siempre porque es manual.

## 3. Configurar el token

```bash
export TELEGRAM_BOT_TOKEN="tu_token_aqui"   # en Windows: set TELEGRAM_BOT_TOKEN=tu_token_aqui
```

## 4. Ejecutar

```bash
python bot.py
```

## 5. Probar en Telegram

Busca tu bot por su username y envía:

- `/start` — mensaje de bienvenida
- `/price` — precios de una watchlist por defecto (BTC, ETH, SOL, BNB, XRP)
- `/price BTCUSDT` — precio de un par específico
- `/price BTCUSDT ETHUSDT` — varios pares a la vez
- `/indicators BTCUSDT` — estocástico y Bandas de Bollinger
- `/alerts check BTCUSDT` — evaluar la estrategia ahora mismo
- `/backtest BTCUSDT` — backtest rápido con los valores por defecto

### ⚠️ Si el deploy falla con `RuntimeError: There is no current event loop`

Render usa Python 3.14 por defecto en servicios nuevos, y
`python-telegram-bot` 21.4 aún no es compatible con los cambios de
`asyncio` en esa versión. El proyecto ya incluye un archivo
`.python-version` que fija Python 3.12, así que si vuelves a ver este
error:

1. Revisa que el archivo `.python-version` esté en la raíz del repo
   (no dentro de una subcarpeta) y contenga `3.12.7`.
2. En Render, ve a **Environment** → agrega la variable
   `PYTHON_VERSION` = `3.12.7` (esto tiene prioridad sobre el archivo
   y es la forma más segura de forzarlo).
3. Haz **Manual Deploy → Clear build cache & deploy** para que tome
   la nueva versión.

## 6. Desplegar en Render (gratis)

El bot detecta automáticamente el entorno: en local corre con *polling*,
y en Render cambia solo a modo *webhook* (usando la variable
`RENDER_EXTERNAL_URL` que Render inyecta sola). No hay que tocar código
para pasar de uno a otro.

### Pasos

1. **Sube el proyecto a GitHub** (repo público o privado, ambos sirven).

2. En [render.com](https://render.com), crea cuenta y luego:
   `New +` → `Web Service` → conecta tu repositorio.

3. Configura el servicio:
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
   - **Instance Type**: `Free`

4. En la sección **Environment Variables**, agrega:
   - `TELEGRAM_BOT_TOKEN` = tu token de @BotFather
   - `TELEGRAM_ALLOWED_USER_ID` = tu user_id numérico de Telegram (muy recomendado)
   - `BITUNIX_API_KEY` = tu API key de Bitunix (para comandos privados)
   - `BITUNIX_API_SECRET` = tu secret key de Bitunix (para comandos privados)

   (No necesitas definir `PORT` ni `RENDER_EXTERNAL_URL` — Render los
   pone automáticamente.)

5. Dale a **Create Web Service**. Render construye y despliega; revisa
   los *logs* y deberías ver `Modo webhook (Render) -> https://...`.

6. Prueba el bot en Telegram con `/start` y `/price`.

### ⚠️ Limitación del plan gratis: se "duerme"

Los Web Services gratis de Render se suspenden tras ~15 min sin
recibir ninguna petición HTTP, y tardan uno o dos minutos en
despertar cuando llega la siguiente. Para un bot esto significa que
el primer mensaje después de un rato de inactividad puede tardar en
responder — y que el job de `/alerts` (cada 4h) **no se ejecutará si
la instancia está dormida en ese momento**, así que te puedes perder
una revisión.

**Solución simple:** usa un servicio gratuito de monitoreo (por
ejemplo [UptimeRobot](https://uptimerobot.com)) para hacer un `GET`
a la URL pública de tu servicio (`https://tu-bot.onrender.com`) cada
5-10 minutos. Cualquier petición HTTP cuenta como actividad y evita
que se duerma — no hace falta que sea al endpoint del webhook. Esto
es especialmente importante ahora que usas `/alerts`, para que el bot
esté despierto en los cierres de vela de 4h (00:00, 04:00, 08:00,
12:00, 16:00, 20:00 UTC).

Si el "sleep" se vuelve un problema serio, la alternativa es pasar a
un plan pago de Render o a una VM propia.

## Watchlist de `/price`

La watchlist se guarda en `watchlist.json` (junto al bot) y se edita con
`/watchlist add|remove|reset`. En Render (plan free) ese archivo sobrevive
mientras la instancia siga "despierta" o duerma por inactividad, pero
**se pierde en cada redeploy** (el disco no es persistente entre builds).
Si quieres que sobreviva a los redeploys, la alternativa es guardarla en
una base de datos pequeña (por ejemplo SQLite en un disco persistente de
pago, o un servicio como Supabase/Upstash). Lo mismo aplica a
`alerts_watchlist.json` (ver más abajo).

## Indicadores técnicos (`/indicators`)

La API de Bitunix no expone indicadores ya calculados (estocástico,
Bollinger, RSI, etc.), solo el histórico de velas
(`GET /api/v1/futures/market/kline`, endpoint público). Por eso
`bitunix_client.py` solo añade `get_kline(...)` para traer esas velas, y
todo el cálculo vive en `indicators.py`:

- **Estocástico lento (14, 3, 3)** — el mismo ajuste por defecto que usa
  la mayoría de plataformas de gráficos, incluida la app de Bitunix.
- **Bandas de Bollinger (20, 2σ)** — media móvil simple de 20 velas ±2
  desviaciones típicas.

`/indicators BTCUSDT` usa el intervalo `1h` por defecto;
`/indicators BTCUSDT 15m` usa velas de 15 minutos. Intervalos válidos:
`1m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 3d 1w 1M`.

## Estrategia de 4h y alertas (`/alerts`)

`strategy.py` implementa la estrategia de entrada en velas de 4h
(estocástico + Bollinger). Reglas, evaluadas al cierre de cada vela:

- **LONG**: la línea rápida del estocástico (%K) cruza hacia arriba a la
  lenta (%D), y en las velas recientes anteriores al cruce %K estuvo en
  zona de sobreventa (no hace falta que el cruce en sí ocurra dentro de
  la zona). Además, el precio de cierre debe estar por debajo de la
  banda media de Bollinger y más cerca de la banda inferior que de la
  media.
- **SHORT**: exactamente lo contrario (cruce hacia abajo, sobrecompra
  reciente, precio por encima de la media y más cerca de la banda
  superior).

Filtros adicionales (aplican a LONG y SHORT, en alertas y en `/backtest`):

- **Vela a favor:** la vela que da la señal debe ir en la dirección de la
  operación — alcista (cierre > apertura) para LONG, bajista para SHORT.
- **Objetivo 0.5:1 antes de la banda media:** el nivel del TP de ratio 0.5:1
  (ver `take_profit_level`) debe quedar por debajo de la banda media de
  Bollinger del momento de la señal en un LONG, o por encima en un SHORT.
  Si el 0.5:1 ya cruzaría la media, no se entra.

`detect_signal()` ahora recibe también las aperturas (`opens`) para evaluar la vela.

Parámetros ajustables en `detect_signal()` (`strategy.py`): `oversold`/
`overbought` (25/75 por defecto — una aproximación a "cerca de la zona";
cámbialos a 20/80 si prefieres el umbral clásico) y `lookback` (10 velas,
cuánto mirar hacia atrás buscando la sobreventa/sobrecompra previa).

**Por ahora solo envía alertas — no abre operaciones.** El stop loss y los
objetivos por ratio (0.5:1 y 1:1, ver más abajo) ya se simulan en
`/backtest`, pero las alertas todavía no muestran el objetivo.

Cómo usarlo:

1. `/alerts add BTCUSDT` — añade símbolos a vigilar (se guardan en
   `alerts_watchlist.json`, lista separada de la watchlist de `/price`).
2. El bot revisa automáticamente cada símbolo vigilado al cierre de
   cada vela de 4h (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC) y te
   manda un mensaje a tu `TELEGRAM_ALLOWED_USER_ID` si hay señal. Esto
   requiere el extra `job-queue` instalado (ver sección 2) y
   `TELEGRAM_ALLOWED_USER_ID` configurado — sin esto el job no puede
   avisarte a nadie.
3. `/alerts check [SIMBOLO]` — evalúa la estrategia *ahora mismo*, sin
   esperar al cierre real de la vela. Si no hay señal, te muestra los
   valores actuales (%K, %D, precio, bandas) para que puedas verificar
   que el cálculo tiene sentido mientras pruebas la estrategia.

## Stop loss (mínimo/máximo local)

Cada señal que da `detect_signal()` (en `strategy.py`) ahora trae un
`stop_loss` calculado así:

- **LONG**: el `low` más bajo de las últimas `sl_lookback` velas
  (10 por defecto, incluida la vela de la señal) — el mínimo local que
  marcó el precio en el movimiento de caída que precedió a la entrada.
- **SHORT**: lo mismo pero con el `high` más alto (máximo local).

`sl_lookback` es independiente del `lookback` que exige al estocástico
haber estado en zona de sobrecompra/sobreventa — se pueden ajustar por
separado.

Esto aparece ahora en:
- Las alertas de `/alerts` (`check_alerts_job` y `/alerts check`), con el
  riesgo en % hasta ese stop.
- El resumen y el detalle de `/backtest`, incluyendo **riesgo medio** y
  **R-múltiplo medio** (`cambio_medio_pct ÷ riesgo_medio_pct` — cuántas
  veces el riesgo asumido se movió el precio a favor, la forma estándar
  de medir una estrategia que ya tiene un stop definido).
- El Excel de `/backtest` (columnas `Stop loss`, `Riesgo %` y
  `R-múltiplo` en la hoja "Señales").

**Lo que todavía falta — y por qué lo dejé fuera por ahora:** este stop
loss es un *nivel sugerido*, calculado localmente; no está todavía
conectado a `place_order()` en `bitunix_client.py` para que la posición
se abra con el stop ya puesto en Bitunix. La API sí soporta mandar un
`slPrice` al abrir la orden, pero no logré confirmar con certeza en la
documentación los valores exactos que esperan los campos `slTriggerType`
/ `slOrderType` — y prefiero no adivinarlos en una función que mueve
dinero real: si el campo está mal, el stop podría no quedar activado de
verdad y no te enterarías hasta que ya fuera tarde. Por ahora, usa el
`stop_loss` que te muestra la alerta como referencia para poner tú el
SL manualmente en Bitunix (o dime y lo investigamos a fondo para
automatizarlo con la certeza que amerita).

El stop ya se usa en `/backtest` para cortar la operación (ver la sección
siguiente). El nivel de objetivo por ratio se calcula con
`take_profit_level()` en `strategy.py`, pensada para reutilizarla cuando se
conecten las órdenes reales a Bitunix.

## Backtest (`/backtest`)

`/backtest SIMBOLO [INTERVALO] [N_VELAS] [HORIZONTE]` — por ejemplo
`/backtest BTCUSDT 4h 500 5` — recorre el histórico vela a vela aplicando
`detect_signal()` en cada paso (solo con lo que "se sabría" hasta ese
momento) y **simula cada operación completa**: entrada al cierre de la vela
de la señal y, desde la vela siguiente, stop loss y objetivo.

Valores por defecto: intervalo `4h`, 500 velas (~83 días), **sin límite de
velas por operación** (el HORIZONTE es opcional). Tope de 1500 velas por consulta para que no se dispare el tiempo de
respuesta.

### Cómo se simula cada operación

- **Stop loss:** si el precio toca el stop (mínimo local en LONG, máximo
  local en SHORT), la operación se corta ahí y la pérdida es exactamente la
  distancia entrada → stop (**-1R**).
- **Dos escenarios de objetivo**, simulados por separado sobre las mismas
  señales, para ver cuál da mejor resultado:
  - **0.5:1** → objetivo a 0.5 veces la distancia entrada → stop (+0.5R).
  - **1:1** → objetivo a 1 vez esa distancia (+1R).
- **Qué cuenta como "llegar":** se revisan los máximos y mínimos de cada
  vela, no solo el cierre. Si el objetivo se toca antes que el stop, la
  operación gana el ratio completo. Si el stop va primero (o en la misma
  vela que el objetivo), cuenta como stop.
- **Horizonte (opcional):** sin él, cada operación dura hasta tocar su
  objetivo o el stop; si el histórico se acaba antes, queda *pendiente*
  (aún abierta). Con él (`/backtest BTCUSDT 4h 500 5`) es el máximo de
  velas que se mantiene la operación: si pasan sin tocar ni stop ni
  objetivo, se cierra al cierre de esa vela (puede salir en positivo o
  negativo).
- **Misma vela, stop y objetivo:** con velas OHLC no se puede saber cuál fue
  primero; se asume el caso conservador (stop).
- No incluye comisiones ni slippage.

El resumen compara ambos escenarios para Total / LONG / SHORT (operaciones
que llegan al objetivo, cortadas por stop, win rate, R total, R medio,
cambio total en %) e indica cuál es mejor **por R medio por operación**.

`get_kline_history()` en `bitunix_client.py` es quien trae el histórico:
pagina hacia atrás en el tiempo en bloques de 200 velas (el máximo por
request de la API) hasta reunir las que se pidan.

### Excel con el detalle completo

Después del resumen en texto, `/backtest` adjunta un archivo `.xlsx`
(generado por `xlsx_export.py`, vía `openpyxl`) con tres hojas:

- **Resumen** — metadatos de la corrida, un bloque de métricas por escenario (0.5:1 y 1:1) con Total/LONG/SHORT, el mejor escenario y las notas del cálculo.
- **Señales** — una fila por cada señal: fecha, lado, precio, %K/%D, bandas, stop loss y riesgo %, y por cada escenario cinco columnas: nivel del **objetivo**, **"Llega a"** (el precio en que se cumple el ratio, o `no` si no llegó o el stop fue primero; `pendiente` si aún no hay velas para saberlo), **salida** (TP / SL / HORIZONTE / PENDIENTE), % de resultado y R. Con filtros automáticos en el encabezado.
- **Velas** — el histórico OHLC completo que se usó para el backtest, por si quieres revisarlo o graficarlo aparte.

Útil para ordenar/filtrar en Excel, graficar la curva de aciertos, o
cruzar las señales con tus propios criterios — cosas que un mensaje de
Telegram no permite.

## Próximos pasos sugeridos

- **Confirmar los campos exactos de `slPrice`/`slTriggerType` en
  `place_order`** para poder adjuntar el stop loss automáticamente al
  abrir la posición (ver sección "Stop loss" arriba).
- **Elegir el objetivo definitivo** a partir de los resultados del
  backtest (0.5:1 vs 1:1, o ajustar el ratio) y usar `take_profit_level()`
  para calcular el take-profit de las órdenes reales a Bitunix.
- ~~Backtest con salida real por stop~~ — hecho: el stop corta la operación
  y se comparan los objetivos 0.5:1 y 1:1.
- Pasar de "solo alerta" a abrir la operación automáticamente cuando hay
  señal (reutilizando `place_order`, ya soportado por `bitunix_client.py`).
- Historial de órdenes/trades (`get_history_orders`, `get_history_trades`).
