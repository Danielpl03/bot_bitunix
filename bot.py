"""
Bot de Telegram para gestionar tu cuenta de Bitunix.

Este módulo SOLO contiene la lógica de Telegram (comandos, formato de
mensajes, confirmaciones). Toda la comunicación con Bitunix vive en
bitunix_client.py, para poder reutilizarla desde otros servicios.

Modo de ejecución (se detecta automáticamente):
    - Local / sin RENDER_EXTERNAL_URL definida -> long polling.
    - Desplegado en Render (Web Service) -> modo webhook.

Requisitos:
    pip install -r requirements.txt

Configuración (variables de entorno):
    TELEGRAM_BOT_TOKEN        - token de @BotFather (obligatorio)
    TELEGRAM_ALLOWED_USER_ID  - tu user_id numérico de Telegram (MUY recomendado).
                                 Si se define, solo esa persona puede usar
                                 /balance, /positions, /open y /close.
    BITUNIX_API_KEY           - API key de Bitunix (necesaria para comandos privados)
    BITUNIX_API_SECRET        - Secret key de Bitunix (necesaria para comandos privados)
    PORT                      - puerto en el que escuchar (Render lo define solo)

Ejecución local:
    python bot.py
"""

import logging
import os

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bitunix_client import BitunixAPIError, BitunixClient

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Comandos que se registran en el botón "Menú" de Telegram (setMyCommands).
# El texto de cada 'description' es lo que Telegram muestra en esa lista.
BOT_COMMANDS = [
    BotCommand("start", "Ver ayuda y todos los comandos"),
    BotCommand("price", "Precio de monedas (/price BTCUSDT)"),
    BotCommand("balance", "Balance de tu cuenta de futuros (/balance USDT)"),
    BotCommand("positions", "Ver posiciones abiertas (/positions BTCUSDT)"),
    BotCommand("open", "Abrir posición (/open BTCUSDT BUY 0.01)"),
    BotCommand("close", "Cerrar posición (/close POSITION_ID)"),
]

# Instancia compartida del cliente de Bitunix
bitunix = BitunixClient(
    api_key=os.environ.get("BITUNIX_API_KEY"),
    secret_key=os.environ.get("BITUNIX_API_SECRET"),
)

# Si se define, solo este user_id de Telegram puede usar comandos sensibles.
_allowed_user_raw = os.environ.get("TELEGRAM_ALLOWED_USER_ID")
ALLOWED_USER_ID = int(_allowed_user_raw) if _allowed_user_raw else None


def _is_authorized(update: Update) -> bool:
    if ALLOWED_USER_ID is None:
        # No configurado: se permite a cualquiera. No recomendado en producción.
        return True
    return update.effective_user is not None and update.effective_user.id == ALLOWED_USER_ID


async def _reject_unauthorized(update: Update) -> None:
    await update.message.reply_text("⛔ No tienes permiso para usar este comando.")
    logger.warning(
        "Intento de acceso no autorizado de user_id=%s",
        update.effective_user.id if update.effective_user else "desconocido",
    )


def format_ticker(ticker: dict) -> str:
    symbol = ticker.get("symbol", "?")
    last_price = ticker.get("lastPrice", "N/A")
    high = ticker.get("high", "N/A")
    low = ticker.get("low", "N/A")
    return f"*{symbol}*\n  Precio: `{last_price}`\n  Máx 24h: `{high}`  Mín 24h: `{low}`"


def format_position(position: dict) -> str:
    return (
        f"*{position.get('symbol', '?')}* ({position.get('side', '?')})\n"
        f"  ID: `{position.get('positionId', '?')}`\n"
        f"  Cantidad: `{position.get('qty', '?')}`  Apalancamiento: `{position.get('leverage', '?')}x`\n"
        f"  Precio entrada: `{position.get('avgOpenPrice', '?')}`\n"
        f"  PnL no realizado: `{position.get('unrealizedPNL', '?')}`\n"
        f"  Liquidación: `{position.get('liqPrice', '?')}`"
    )


# --- Comandos básicos ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Bienvenido al bot de Bitunix.\n\n"
        "Consulta de precios:\n"
        "/price - watchlist por defecto\n"
        "/price BTCUSDT - un par específico\n\n"
        "Cuenta (requieren autorización):\n"
        "/balance [MONEDA] - balance de futuros (default USDT)\n"
        "/positions [SIMBOLO] - posiciones abiertas\n"
        "/open SIMBOLO BUY|SELL CANTIDAD [PRECIO] - abrir posición\n"
        "/close POSITION_ID - cerrar posición a mercado\n"
    )


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    symbols = [s.upper() for s in args] if args else None

    try:
        tickers = bitunix.get_watchlist(symbols)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error consultando Bitunix")
        await update.message.reply_text(f"⚠️ Error consultando la API de Bitunix: {exc}")
        return

    if not tickers:
        await update.message.reply_text(
            "No se encontraron datos para esos símbolos. Verifica que el par exista, ej: BTCUSDT."
        )
        return

    message = "\n\n".join(format_ticker(t) for t in tickers)
    await update.message.reply_text(message, parse_mode="Markdown")


# --- Comandos de cuenta (privados) ---

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    margin_coin = context.args[0].upper() if context.args else "USDT"

    try:
        account = bitunix.get_account(margin_coin)
    except (BitunixAPIError, Exception) as exc:  # noqa: BLE001
        logger.exception("Error consultando balance")
        await update.message.reply_text(f"⚠️ Error consultando el balance: {exc}")
        return

    if not account:
        await update.message.reply_text(f"No se encontró cuenta para la moneda {margin_coin}.")
        return

    message = (
        f"*Balance ({account.get('marginCoin')})*\n"
        f"Disponible: `{account.get('available')}`\n"
        f"Congelado (en órdenes): `{account.get('frozen')}`\n"
        f"En posiciones: `{account.get('margin')}`\n"
        f"Transferible: `{account.get('transfer')}`\n"
        f"PnL no realizado (cross): `{account.get('crossUnrealizedPNL')}`\n"
        f"PnL no realizado (isolation): `{account.get('isolationUnrealizedPNL')}`\n"
        f"Modo de posición: `{account.get('positionMode')}`"
    )
    await update.message.reply_text(message, parse_mode="Markdown")


async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    symbol = context.args[0].upper() if context.args else None

    try:
        open_positions = bitunix.get_pending_positions(symbol=symbol)
    except (BitunixAPIError, Exception) as exc:  # noqa: BLE001
        logger.exception("Error consultando posiciones")
        await update.message.reply_text(f"⚠️ Error consultando posiciones: {exc}")
        return

    if not open_positions:
        await update.message.reply_text("No tienes posiciones abiertas" + (f" en {symbol}." if symbol else "."))
        return

    message = "\n\n".join(format_position(p) for p in open_positions)
    await update.message.reply_text(message, parse_mode="Markdown")


# --- Abrir posición (con confirmación) ---

async def open_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "Uso: /open SIMBOLO BUY|SELL CANTIDAD [PRECIO]\n"
            "Ejemplo mercado: /open BTCUSDT BUY 0.01\n"
            "Ejemplo límite: /open BTCUSDT BUY 0.01 60000"
        )
        return

    symbol = args[0].upper()
    side = args[1].upper()
    qty = args[2]
    price = args[3] if len(args) > 3 else None

    if side not in ("BUY", "SELL"):
        await update.message.reply_text("El lado debe ser BUY o SELL.")
        return

    order_type = "LIMIT" if price else "MARKET"

    # Guardamos la orden propuesta para ejecutarla solo si el usuario confirma
    context.user_data["pending_order"] = {
        "symbol": symbol,
        "qty": qty,
        "side": side,
        "order_type": order_type,
        "price": price,
    }

    price_line = f"Precio: `{price}` (LIMIT)" if price else "Precio: mercado (MARKET)"
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirmar", callback_data="confirm_open"),
                InlineKeyboardButton("❌ Cancelar", callback_data="cancel_open"),
            ]
        ]
    )
    await update.message.reply_text(
        f"⚠️ Confirmar apertura de posición:\n\n"
        f"Símbolo: `{symbol}`\nLado: `{side}`\nCantidad: `{qty}`\n{price_line}",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def handle_open_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        await query.edit_message_text("⛔ No tienes permiso para confirmar esta operación.")
        return

    order = context.user_data.pop("pending_order", None)

    if query.data == "cancel_open" or not order:
        await query.edit_message_text("Operación cancelada.")
        return

    try:
        result = bitunix.place_order(
            symbol=order["symbol"],
            qty=order["qty"],
            side=order["side"],
            trade_side="OPEN",
            order_type=order["order_type"],
            price=order["price"],
        )
    except (BitunixAPIError, Exception) as exc:  # noqa: BLE001
        logger.exception("Error al colocar la orden")
        await query.edit_message_text(f"⚠️ Error al colocar la orden: {exc}")
        return

    order_id = result.get("orderId") if isinstance(result, dict) else result
    await query.edit_message_text(f"✅ Orden enviada correctamente.\nOrder ID: `{order_id}`", parse_mode="Markdown")


# --- Cerrar posición (con confirmación) ---

async def close_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    if not context.args:
        await update.message.reply_text(
            "Uso: /close POSITION_ID\n"
            "Usa /positions para ver los IDs de tus posiciones abiertas."
        )
        return

    position_id = context.args[0]
    context.user_data["pending_close"] = position_id

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirmar cierre", callback_data="confirm_close"),
                InlineKeyboardButton("❌ Cancelar", callback_data="cancel_close"),
            ]
        ]
    )
    await update.message.reply_text(
        f"⚠️ ¿Cerrar la posición `{position_id}` a precio de mercado?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def handle_close_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not _is_authorized(update):
        await query.edit_message_text("⛔ No tienes permiso para confirmar esta operación.")
        return

    position_id = context.user_data.pop("pending_close", None)

    if query.data == "cancel_close" or not position_id:
        await query.edit_message_text("Operación cancelada.")
        return

    try:
        result = bitunix.flash_close_position(position_id)
    except (BitunixAPIError, Exception) as exc:  # noqa: BLE001
        logger.exception("Error al cerrar la posición")
        await query.edit_message_text(f"⚠️ Error al cerrar la posición: {exc}")
        return

    await query.edit_message_text(f"✅ Posición `{position_id}` cerrada correctamente.", parse_mode="Markdown")


async def _post_init(application: Application) -> None:
    """Registra los comandos en Telegram (botón 'Menú' / lista al escribir '/')."""
    await application.bot.set_my_commands(BOT_COMMANDS)
    logger.info("Comandos registrados en el menú de Telegram.")


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Falta la variable de entorno TELEGRAM_BOT_TOKEN. "
            "Expórtala con tu token de @BotFather antes de ejecutar el bot."
        )

    if ALLOWED_USER_ID is None:
        logger.warning(
            "TELEGRAM_ALLOWED_USER_ID no está definido: cualquier persona que "
            "encuentre este bot podrá ver tu balance y operar con tu cuenta. "
            "Configúralo antes de usar comandos privados en producción."
        )

    application = Application.builder().token(token).post_init(_post_init).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("price", price))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("positions", positions))
    application.add_handler(CommandHandler("open", open_position))
    application.add_handler(CommandHandler("close", close_position))
    application.add_handler(CallbackQueryHandler(handle_open_confirmation, pattern="^(confirm|cancel)_open$"))
    application.add_handler(CallbackQueryHandler(handle_close_confirmation, pattern="^(confirm|cancel)_close$"))

    port = int(os.environ.get("PORT", 8443))
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
