# Bot de Telegram para Bitunix — Fase 1 (precios)

Bot básico que consulta precios de pares de futuros en Bitunix mediante
el endpoint público `GET /api/v1/futures/market/tickers` (no requiere
API key, ya que es información de mercado pública).

## Estructura del proyecto

```
bitunix_client.py   # Lógica de conexión a la API de Bitunix (sin Telegram)
bot.py               # Lógica de Telegram: comandos y formato de mensajes
requirements.txt
```

`bitunix_client.py` es independiente de Telegram a propósito: expone
una clase `BitunixClient` que se puede importar desde cualquier otro
servicio (una API propia con FastAPI/Flask, un script, una app) para
usarla como backend común, sin arrastrar dependencias de Telegram.

```python
from bitunix_client import BitunixClient

client = BitunixClient()
ticker = client.get_price("BTCUSDT")
```

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

Cuando quieras avanzar hacia la gestión de cuenta (saldo, posiciones,
órdenes), necesitarás:

1. Crear tu API Key y Secret Key en Bitunix
   (Cuenta → API Management).
2. Implementar la firma de solicitudes privadas según
   `https://www.bitunix.com/api-docs/futures/common/sign.html`
   (usa HMAC con tu Secret Key, más `api-key`, `nonce` y `timestamp`
   en las cabeceras).
3. **Nunca** subas tu Secret Key a un repositorio ni la compartas;
   guárdala como variable de entorno igual que el token de Telegram.
4. A partir de ahí se pueden agregar comandos como `/balance`,
   `/positions`, `/open` y `/close`.
