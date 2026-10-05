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
from datetime import datetime, timedelta, timezone

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from backtest import run_backtest, summarize
from bitunix_client import DEFAULT_WATCHLIST, BitunixAPIError, BitunixClient
from indicators import bollinger_bands, stochastic_oscillator
from strategy import detect_signal

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
    BotCommand("alerts", "Monedas vigiladas por la estrategia de 4h (add/remove/list)"),
    BotCommand("backtest", "Backtest de la estrategia (/backtest BTCUSDT 4h 500)"),
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


# --- Watchlist de la estrategia de alertas (distinta de la de /price) ---
#
# Vacía por defecto: es una lista de "quiero que vigiles esta moneda con
# la estrategia", así que hay que darla de alta explícitamente con
# /alerts add, no reutiliza la watchlist de precios.
ALERTS_FILE = os.environ.get("ALERTS_FILE", "alerts_watchlist.json")


def _load_alerts_watchlist() -> list[str]:
    try:
        with open(ALERTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list) and all(isinstance(s, str) for s in data):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


def _save_alerts_watchlist(symbols: list[str]) -> None:
    try:
        with open(ALERTS_FILE, "w", encoding="utf-8") as f:
            json.dump(symbols, f)
    except OSError:
        logger.exception("No se pudo guardar la watchlist de alertas en disco")


alerts_watchlist: list[str] = _load_alerts_watchlist()


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
        "/indicators SIMBOLO [INTERVALO] - estocástico y Bandas de Bollinger\n"
        "/alerts - monedas vigiladas por la estrategia de 4h (add/remove)\n"
        "/backtest SIMBOLO [INTERVALO] [N_VELAS] [HORIZONTE] - backtest de la estrategia\n\n"
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


# --- Backtest de la estrategia sobre histórico ---

BACKTEST_MAX_CANDLES = 1500  # tope para que la respuesta no tarde demasiado


def _format_backtest_stats(label: str, stats: dict) -> str:
    if stats["total"] == 0:
        return f"*{label}*: sin señales"
    win_rate = f"{stats['win_rate']:.1f}%" if stats["win_rate"] is not None else "N/D"
    avg = f"{stats['cambio_medio_pct']:+.2f}%" if stats["cambio_medio_pct"] is not None else "N/D"
    return (
        f"*{label}*: {stats['total']} señales ({stats['con_resultado']} con resultado)\n"
        f"  Acierto: {win_rate}  ·  Cambio medio: {avg}"
    )


async def backtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: /backtest SIMBOLO [INTERVALO] [N_VELAS] [HORIZONTE]\n"
            "Ejemplo: /backtest BTCUSDT 4h 500 5\n\n"
            "INTERVALO por defecto `4h` (el de la estrategia).\n"
            f"N_VELAS por defecto 500, máximo {BACKTEST_MAX_CANDLES} — cuánto histórico traer.\n"
            "HORIZONTE por defecto 5 — a cuántas velas después de cada señal se mide "
            "si el precio se movió a favor.",
            parse_mode="Markdown",
        )
        return

    symbol = _normalize_symbol(args[0])
    interval = args[1] if len(args) > 1 else "4h"

    if interval not in VALID_INTERVALS:
        await update.message.reply_text(
            f"Intervalo `{interval}` no válido. Usa uno de: {' '.join(sorted(VALID_INTERVALS, key=len))}",
            parse_mode="Markdown",
        )
        return

    try:
        n_velas = int(args[2]) if len(args) > 2 else 500
        horizon = int(args[3]) if len(args) > 3 else 5
    except ValueError:
        await update.message.reply_text("N_VELAS y HORIZONTE deben ser números enteros.")
        return

    n_velas = max(50, min(n_velas, BACKTEST_MAX_CANDLES))
    horizon = max(1, horizon)

    await update.message.reply_text(
        f"Descargando histórico de `{symbol}` ({interval}, hasta {n_velas} velas) y corriendo el backtest…",
        parse_mode="Markdown",
    )

    try:
        klines = bitunix.get_kline_history(symbol, interval=interval, total=n_velas)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error consultando histórico de Bitunix")
        await update.message.reply_text(f"⚠️ Error consultando histórico de Bitunix: {exc}")
        return

    if len(klines) < 40:
        await update.message.reply_text(
            f"Solo hay {len(klines)} velas de histórico para `{symbol}` en `{interval}` — "
            "no es suficiente para un backtest útil.",
            parse_mode="Markdown",
        )
        return

    closes = [float(k["close"]) for k in klines]
    highs = [float(k["high"]) for k in klines]
    lows = [float(k["low"]) for k in klines]
    times = [k["time"] for k in klines]

    results = run_backtest(closes, highs, lows, times, horizon=horizon)
    stats = summarize(results)

    first_date = datetime.fromtimestamp(times[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    last_date = datetime.fromtimestamp(times[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    header = (
        f"*Backtest {symbol} — {interval}*\n"
        f"{len(klines)} velas ({first_date} → {last_date}), horizonte {horizon} velas\n\n"
    )

    if not results:
        await update.message.reply_text(
            header + "No se dio ninguna señal en ese período con los parámetros actuales.",
            parse_mode="Markdown",
        )
        return

    summary_text = "\n\n".join(
        _format_backtest_stats(label, stats[key])
        for label, key in (("Total", "total"), ("LONG", "long"), ("SHORT", "short"))
    )

    recent = results[-8:]
    recent_lines = []
    for r in recent:
        date = datetime.fromtimestamp(r.time / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        side_emoji = "🟢" if r.side == "LONG" else "🔴"
        change = f"{r.pct_change:+.2f}%" if r.pct_change is not None else "pendiente"
        recent_lines.append(f"{side_emoji} `{date}` {r.side} @ `{r.close:.4f}` → {change}")

    detail_header = (
        f"_Últimas {len(recent)} de {len(results)} señales:_"
        if len(results) > len(recent)
        else "_Señales:_"
    )
    detail_text = detail_header + "\n" + "\n".join(recent_lines)

    await update.message.reply_text(header + summary_text + "\n\n" + detail_text, parse_mode="Markdown")


# --- Watchlist de alertas: monedas vigiladas por la estrategia de 4h ---

async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    args = context.args

    if not args:
        if not alerts_watchlist:
            message = (
                "No estás vigilando ninguna moneda con la estrategia de 4h todavía.\n\n"
                "Uso:\n/alerts add SIMBOLO\n/alerts remove SIMBOLO"
            )
        else:
            message = "*Monedas vigiladas (estrategia de 4h):*\n" + "\n".join(
                f"• `{s}`" for s in alerts_watchlist
            )
            message += "\n\nUso:\n/alerts add SIMBOLO\n/alerts remove SIMBOLO\n/alerts reset — vacía la lista"
        await update.message.reply_text(message, parse_mode="Markdown")
        return

    action = args[0].lower()

    if action == "reset":
        alerts_watchlist.clear()
        _save_alerts_watchlist(alerts_watchlist)
        await update.message.reply_text("Watchlist de alertas vaciada.")
        return

    if action == "check":
        symbols = [_normalize_symbol(args[1])] if len(args) > 1 else alerts_watchlist
        if not symbols:
            await update.message.reply_text(
                "No hay ninguna moneda en la watchlist de alertas. Usa /alerts add SIMBOLO primero, "
                "o /alerts check SIMBOLO para probar uno suelto."
            )
            return

        lines = []
        for symbol in symbols:
            try:
                signal, values = _evaluate_alert_symbol(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error en /alerts check para %s", symbol)
                lines.append(f"⚠️ *{symbol}*: error consultando velas de 4h ({exc})")
                continue

            if signal is not None:
                lines.append(_format_signal_message(symbol, signal))
            else:
                lines.append(
                    f"⚪ *{symbol}* — sin señal ahora mismo\n"
                    f"  %K `{values['k']:.2f}`  %D `{values['d']:.2f}`  "
                    f"Precio `{values['close']:.4f}`\n"
                    f"  Bollinger: inf `{values['lower']:.4f}`  media `{values['middle']:.4f}`  "
                    f"sup `{values['upper']:.4f}`"
                )

        await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")
        return

    if action in ("add", "remove", "del") and len(args) < 2:
        await update.message.reply_text(f"Uso: /alerts {action} SIMBOLO")
        return

    if action == "add":
        symbol = _normalize_symbol(args[1])
        if symbol in alerts_watchlist:
            await update.message.reply_text(f"`{symbol}` ya se está vigilando.", parse_mode="Markdown")
            return
        alerts_watchlist.append(symbol)
        _save_alerts_watchlist(alerts_watchlist)
        await update.message.reply_text(
            f"✅ Empezaré a vigilar `{symbol}` cada 4h con la estrategia.", parse_mode="Markdown"
        )
        return

    if action in ("remove", "del"):
        symbol = _normalize_symbol(args[1])
        if symbol not in alerts_watchlist:
            await update.message.reply_text(f"`{symbol}` no estaba en la lista.", parse_mode="Markdown")
            return
        alerts_watchlist.remove(symbol)
        _save_alerts_watchlist(alerts_watchlist)
        await update.message.reply_text(f"🗑 Dejo de vigilar `{symbol}`.", parse_mode="Markdown")
        return

    await update.message.reply_text("Subcomando no reconocido. Usa: /alerts [add|remove|reset] [SIMBOLO]")


def _format_signal_message(symbol: str, signal) -> str:
    side_label = "🟢 LONG" if signal.side == "LONG" else "🔴 SHORT"
    return (
        f"🔔 *Señal de entrada — {symbol}* ({side_label})\n\n"
        f"Vela de 4h recién cerrada.\n"
        f"Estocástico: %K `{signal.k:.2f}`  %D `{signal.d:.2f}`\n"
        f"Bandas de Bollinger: inf `{signal.lower:.4f}`  media `{signal.middle:.4f}`  "
        f"sup `{signal.upper:.4f}`\n"
        f"Precio de cierre: `{signal.close:.4f}`"
    )


def _evaluate_alert_symbol(symbol: str) -> tuple[object | None, dict]:
    """
    Trae las últimas velas de 4h de `symbol` y evalúa la estrategia sobre
    la última vela cerrada. Devuelve (Signal o None, valores_actuales) —
    los valores_actuales se devuelven siempre (haya señal o no) para poder
    mostrarlos en /alerts check aunque no se haya disparado nada todavía.
    """
    klines = bitunix.get_kline(symbol, interval="4h", limit=100)
    closes = [float(k["close"]) for k in klines]
    highs = [float(k["high"]) for k in klines]
    lows = [float(k["low"]) for k in klines]

    signal = detect_signal(closes, highs, lows)
    k, d = stochastic_oscillator(closes, highs, lows)
    lower, middle, upper = bollinger_bands(closes)

    values = {"k": k, "d": d, "lower": lower, "middle": middle, "upper": upper, "close": closes[-1]}
    return signal, values


async def check_alerts_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Se ejecuta cada 4h (al cierre de vela). Revisa cada símbolo de
    alerts_watchlist y avisa a ALLOWED_USER_ID si la estrategia da señal."""
    if not alerts_watchlist:
        return
    if ALLOWED_USER_ID is None:
        logger.warning("check_alerts_job: TELEGRAM_ALLOWED_USER_ID no está definido, no se puede avisar a nadie.")
        return

    for symbol in alerts_watchlist:
        try:
            signal, _ = _evaluate_alert_symbol(symbol)
        except Exception:  # noqa: BLE001
            logger.exception("Error evaluando la estrategia para %s", symbol)
            continue

        if signal is not None:
            try:
                await context.bot.send_message(
                    chat_id=ALLOWED_USER_ID,
                    text=_format_signal_message(symbol, signal),
                    parse_mode="Markdown",
                )
            except Exception:  # noqa: BLE001
                logger.exception("Error enviando la alerta de %s", symbol)


def _next_4h_boundary_utc() -> datetime:
    """Próximo cierre de vela de 4h en UTC (00, 04, 08, 12, 16, 20h), con
    30s de margen para que Bitunix ya haya publicado la vela cerrada."""
    now = datetime.now(timezone.utc)
    next_hour = (now.hour // 4 + 1) * 4
    days_ahead = next_hour // 24
    hour = next_hour % 24
    boundary = (now + timedelta(days=days_ahead)).replace(hour=hour, minute=0, second=30, microsecond=0)
    return boundary


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
    application.add_handler(CommandHandler("alerts", alerts_command))
    application.add_handler(CommandHandler("backtest", backtest_command))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("positions", positions))
    application.add_handler(CommandHandler("open", open_position))
    application.add_handler(CommandHandler("close", close_position))
    application.add_handler(CallbackQueryHandler(handle_open_confirmation, pattern="^(confirm|cancel)_open$"))
    application.add_handler(CallbackQueryHandler(handle_close_confirmation, pattern="^(confirm|cancel)_close$"))

    if application.job_queue is not None:
        first_run = _next_4h_boundary_utc()
        application.job_queue.run_repeating(
            check_alerts_job,
            interval=timedelta(hours=4),
            first=first_run,
            name="check_alerts",
        )
        logger.info("Estrategia de alertas programada. Primera revisión: %s (UTC)", first_run.isoformat())
    else:
        logger.warning(
            "JobQueue no disponible: instala 'python-telegram-bot[job-queue]' para que "
            "/alerts funcione. Sin esto, la estrategia de 4h nunca se evaluará sola."
        )

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
