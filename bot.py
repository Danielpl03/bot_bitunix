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

import json
import logging
import os

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from bitunix_client import DEFAULT_WATCHLIST, BitunixAPIError, BitunixClient
from indicators import bollinger_bands, stochastic_oscillator

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
    BotCommand("watchlist", "Ver o editar la watchlist de /price"),
    BotCommand("indicators", "Estocástico y Bandas de Bollinger (/indicators BTCUSDT 1h)"),
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


# --- Watchlist personalizable (usada por /price sin argumentos) ---
#
# Se guarda en un archivo JSON junto al bot. En Render (plan free) el
# disco es efectivo mientras la instancia sigue viva (sobrevive al
# "sleep"), pero se pierde en cada redeploy — si eso te molesta, la
# alternativa es moverla a una tabla en una base de datos.
WATCHLIST_FILE = os.environ.get("WATCHLIST_FILE", "watchlist.json")


def _load_watchlist() -> list[str]:
    try:
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list) and all(isinstance(s, str) for s in data):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return list(DEFAULT_WATCHLIST)


def _save_watchlist(symbols: list[str]) -> None:
    try:
        with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(symbols, f)
    except OSError:
        logger.exception("No se pudo guardar la watchlist en disco")


def _normalize_symbol(raw: str) -> str:
    symbol = raw.upper().strip()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    return symbol


# Estado en memoria, cargado al arrancar el bot.
watchlist: list[str] = _load_watchlist()


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_ticker(ticker: dict) -> str:
    symbol = ticker.get("symbol", "?")
    last_price = ticker.get("lastPrice", "N/A")
    high = ticker.get("high", "N/A")
    low = ticker.get("low", "N/A")
    return f"*{symbol}*\n  Precio: `{last_price}`\n  Máx 24h: `{high}`  Mín 24h: `{low}`"


def format_position(position: dict) -> str:
    symbol = position.get("symbol", "?")
    side = position.get("side", "?")
    unrealized_pnl = position.get("unrealizedPNL")
    margin = position.get("margin")

    pnl_value = _to_float(unrealized_pnl)
    margin_value = _to_float(margin)

    pnl_pct_str = ""
    if pnl_value is not None and margin_value:
        pnl_pct_str = f" ({pnl_value / margin_value * 100:+.2f}%)"

    pnl_emoji = "🟢" if (pnl_value is not None and pnl_value >= 0) else "🔴" if pnl_value is not None else "⚪"
    side_label = "⬆️ LONG" if side == "BUY" else "⬇️ SHORT" if side == "SELL" else side

    return (
        f"{pnl_emoji} *{symbol}* — {side_label}\n"
        f"  ID: `{position.get('positionId', '?')}`\n"
        f"  Cantidad: `{position.get('qty', '?')}`  Apalancamiento: `{position.get('leverage', '?')}x`\n"
        f"  Precio entrada: `{position.get('avgOpenPrice', '?')}`\n"
        f"  PnL no realizado: `{unrealized_pnl}`{pnl_pct_str}\n"
        f"  Liquidación: `{position.get('liqPrice', '?')}`"
    )


# --- Comandos básicos ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Bienvenido al bot de Bitunix.\n\n"
        "Consulta de precios:\n"
        "/price - tu watchlist\n"
        "/price BTCUSDT - un par específico\n"
        "/watchlist - ver o editar tu watchlist (add/remove/reset)\n"
        "/indicators SIMBOLO [INTERVALO] - estocástico y Bandas de Bollinger\n\n"
        "Cuenta (requieren autorización):\n"
        "/balance [MONEDA] - balance de futuros (default USDT)\n"
        "/positions [SIMBOLO] - posiciones abiertas\n"
        "/open SIMBOLO BUY|SELL CANTIDAD [PRECIO] - abrir posición\n"
        "/close POSITION_ID - cerrar posición a mercado\n"
    )


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    symbols = [_normalize_symbol(s) for s in args] if args else watchlist

    try:
        tickers = bitunix.get_tickers(symbols)
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


async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    args = context.args

    if not args:
        message = "*Watchlist actual:*\n" + "\n".join(f"• `{s}`" for s in watchlist)
        message += (
            "\n\nUso:\n"
            "/watchlist add SIMBOLO\n"
            "/watchlist remove SIMBOLO\n"
            "/watchlist reset — vuelve a la watchlist por defecto"
        )
        await update.message.reply_text(message, parse_mode="Markdown")
        return

    action = args[0].lower()

    if action == "reset":
        watchlist[:] = DEFAULT_WATCHLIST
        _save_watchlist(watchlist)
        await update.message.reply_text("Watchlist restaurada a los valores por defecto.")
        return

    if action in ("add", "remove", "del") and len(args) < 2:
        await update.message.reply_text(f"Uso: /watchlist {action} SIMBOLO")
        return

    if action == "add":
        symbol = _normalize_symbol(args[1])
        if symbol in watchlist:
            await update.message.reply_text(f"`{symbol}` ya está en la watchlist.", parse_mode="Markdown")
            return
        watchlist.append(symbol)
        _save_watchlist(watchlist)
        await update.message.reply_text(f"✅ `{symbol}` añadido a la watchlist.", parse_mode="Markdown")
        return

    if action in ("remove", "del"):
        symbol = _normalize_symbol(args[1])
        if symbol not in watchlist:
            await update.message.reply_text(f"`{symbol}` no está en la watchlist.", parse_mode="Markdown")
            return
        watchlist.remove(symbol)
        _save_watchlist(watchlist)
        await update.message.reply_text(f"🗑 `{symbol}` eliminado de la watchlist.", parse_mode="Markdown")
        return

    await update.message.reply_text(
        "Subcomando no reconocido. Usa: /watchlist [add|remove|reset] [SIMBOLO]"
    )


# --- Indicadores técnicos (calculados a partir del histórico de velas) ---

VALID_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}


async def indicators_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: /indicators SIMBOLO [INTERVALO]\n"
            "Ejemplo: /indicators BTCUSDT 1h\n"
            f"Intervalos válidos: {' '.join(sorted(VALID_INTERVALS, key=len))} (default 1h)"
        )
        return

    symbol = _normalize_symbol(args[0])
    interval = args[1] if len(args) > 1 else "1h"

    if interval not in VALID_INTERVALS:
        await update.message.reply_text(
            f"Intervalo `{interval}` no válido. Usa uno de: "
            f"{' '.join(sorted(VALID_INTERVALS, key=len))}",
            parse_mode="Markdown",
        )
        return

    try:
        # Bitunix no expone indicadores calculados por su API, solo el
        # histórico de velas — el estocástico y las Bandas de Bollinger se
        # calculan aquí a partir de ese histórico (ver indicators.py).
        klines = bitunix.get_kline(symbol, interval=interval, limit=100)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error consultando velas de Bitunix")
        await update.message.reply_text(f"⚠️ Error consultando velas de Bitunix: {exc}")
        return

    if len(klines) < 30:
        await update.message.reply_text(
            f"No hay suficientes velas de `{symbol}` en `{interval}` para calcular los indicadores.",
            parse_mode="Markdown",
        )
        return

    closes = [float(k["close"]) for k in klines]
    highs = [float(k["high"]) for k in klines]
    lows = [float(k["low"]) for k in klines]

    try:
        k_value, d_value = stochastic_oscillator(closes, highs, lows)
        lower, middle, upper = bollinger_bands(closes)
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return

    last_close = closes[-1]
    stoch_emoji = "🟢" if k_value < 20 else "🔴" if k_value > 80 else "⚪"
    if last_close > upper:
        bb_position = "por encima de la banda superior 📈"
    elif last_close < lower:
        bb_position = "por debajo de la banda inferior 📉"
    else:
        bb_position = "dentro de las bandas"

    message = (
        f"*{symbol}* — {interval}\n\n"
        f"*Estocástico (14, 3, 3)*\n"
        f"  %K: `{k_value:.2f}`  %D: `{d_value:.2f}` {stoch_emoji}\n\n"
        f"*Bandas de Bollinger (20, 2σ)*\n"
        f"  Superior: `{upper:.4f}`\n"
        f"  Media: `{middle:.4f}`\n"
        f"  Inferior: `{lower:.4f}`\n"
        f"  Precio actual: `{last_close:.4f}` — {bb_position}"
    )
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
    print(account)
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
    application.add_handler(CommandHandler("watchlist", watchlist_command))
    application.add_handler(CommandHandler("indicators", indicators_command))
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
