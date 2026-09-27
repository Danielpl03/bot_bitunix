# Bot de Telegram para Bitunix

Bot que consulta precios y gestiona tu cuenta de futuros en Bitunix:
saldo, posiciones abiertas, apertura y cierre de posiciones.

## Estructura del proyecto

```
bitunix_client.py   # Lógica de conexión a la API de Bitunix (sin Telegram)
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

## Comandos disponibles

| Comando | Descripción |
|---|---|
| `/price [SIMBOLO...]` | Precios (watchlist por defecto o símbolos dados) |
| `/balance [MONEDA]` | Balance de la cuenta de futuros (default `USDT`) |
| `/positions [SIMBOLO]` | Posiciones abiertas |
| `/open SIMBOLO BUY\|SELL CANTIDAD [PRECIO]` | Abrir posición (pide confirmación) |
| `/close POSITION_ID` | Cerrar posición a mercado (pide confirmación) |

`/balance`, `/positions`, `/open` y `/close` requieren `BITUNIX_API_KEY`
y `BITUNIX_API_SECRET` configurados, y (muy recomendado)
`TELEGRAM_ALLOWED_USER_ID` para restringir quién puede usarlos.

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
responder.

**Solución simple:** usa un servicio gratuito de monitoreo (por
ejemplo [UptimeRobot](https://uptimerobot.com)) para hacer un `GET`
a la URL pública de tu servicio (`https://tu-bot.onrender.com`) cada
5-10 minutos. Cualquier petición HTTP cuenta como actividad y evita
que se duerma — no hace falta que sea al endpoint del webhook.

Si más adelante el "sleep" se vuelve un problema serio (por ejemplo,
si agregas alertas de precio en tiempo real que necesitan estar
siempre corriendo), la alternativa es pasar a un plan pago de Render
o a una VM propia.

## Próximos pasos sugeridos

- Historial de órdenes/trades (`get_history_orders`, `get_history_trades`).
- Alertas de precio (requiere que el bot esté siempre corriendo, no solo
  reaccionando a webhooks — revisa la limitación de "sleep" de Render arriba).
- Take-profit / stop-loss al abrir posición (`tpPrice`, `slPrice` en
  `place_order` — ya soportado por la API, falta exponerlo como comando).
