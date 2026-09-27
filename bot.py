"""
Bot de Telegram para gestionar tu cuenta de Bitunix.
Fase 1: consulta de precios.

Este módulo SOLO contiene la lógica de Telegram (comandos, formato de
mensajes). Toda la comunicación con Bitunix vive en bitunix_client.py,
para poder reutilizarla desde otros servicios (una API propia, una
app, un script) sin depender de Telegram.

Modo de ejecución (se detecta automáticamente):
    - Local / sin RENDER_EXTERNAL_URL definida -> long polling.
    - Desplegado en Render (Web Service) -> modo webhook, usando la
      URL pública que Render inyecta automáticamente en
      RENDER_EXTERNAL_URL.

Requisitos:
    pip install -r requirements.txt

Configuración:
    TELEGRAM_BOT_TOKEN - token que te da @BotFather (obligatorio)
    PORT               - puerto en el que escuchar (Render lo define solo)

Ejecución local:
    python bot.py
"""

import logging
import os

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from bitunix_client import BitunixAPIError, BitunixClient

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Instancia compartida del cliente de Bitunix (por ahora solo usa endpoints públicos)
bitunix = BitunixClient()


def format_ticker(ticker: dict) -> str:
    symbol = ticker.get("symbol", "?")
    last_price = ticker.get("lastPrice", "N/A")
    high = ticker.get("high", "N/A")
    low = ticker.get("low", "N/A")
    return f"*{symbol}*\n  Precio: `{last_price}`\n  Máx 24h: `{high}`  Mín 24h: `{low}`"


# --- Handlers de Telegram ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Bienvenido al bot de Bitunix.\n\n"
        "Comandos disponibles:\n"
        "/price - precios de tu watchlist por defecto\n"
        "/price BTCUSDT - precio de un par específico\n"
        "/price BTCUSDT ETHUSDT - precio de varios pares\n"
    )


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args  # símbolos pasados después del comando
    symbols = [s.upper() for s in args] if args else None

    try:
        tickers = bitunix.get_watchlist(symbols)
    except (BitunixAPIError, Exception) as exc:  # noqa: BLE001
        logger.exception("Error consultando Bitunix")
        await update.message.reply_text(f"⚠️ Error consultando la API de Bitunix: {exc}")
        return

    if not tickers:
        await update.message.reply_text(
            "No se encontraron datos para esos símbolos. "
            "Verifica que el par exista, ej: BTCUSDT."
        )
        return

    message = "\n\n".join(format_ticker(t) for t in tickers)
    await update.message.reply_text(message, parse_mode="Markdown")


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Falta la variable de entorno TELEGRAM_BOT_TOKEN. "
            "Expórtala con tu token de @BotFather antes de ejecutar el bot."
        )

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("price", price))

    port = int(os.environ.get("PORT", 8443))
    # Render inyecta automáticamente esta variable en los Web Services
    # con la URL pública del servicio, ej: https://mi-bot.onrender.com
    render_url = os.environ.get("RENDER_EXTERNAL_URL")

    if render_url:
        webhook_path = "webhook"
        webhook_url = f"{render_url.rstrip('/')}/{webhook_path}"
        logger.info("Modo webhook (Render) -> %s", webhook_url)
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=webhook_path,
            webhook_url=webhook_url,
        )
    else:
        logger.info("Modo polling (local). Esperando mensajes...")
        application.run_polling()


if __name__ == "__main__":
    main()
