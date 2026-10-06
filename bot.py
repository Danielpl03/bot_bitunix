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
import re
from datetime import datetime, timedelta, timezone

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from backtest import (
    OUTCOME_HORIZON,
    OUTCOME_PENDING,
    OUTCOME_SL,
    OUTCOME_TP,
    TP_RATIOS,
    best_scenario,
    ratio_label,
    run_backtest,
    summarize,
)
from bitunix_client import DEFAULT_WATCHLIST, BitunixAPIError, BitunixClient
from indicators import bollinger_bands, stochastic_oscillator
from strategy import StrategyStatus, evaluate_conditions
from xlsx_export import build_backtest_workbook

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
    BotCommand("alerts", "Monedas vigiladas por la estrategia en 4h y 1h (add/remove/check)"),
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
        "/alerts - monedas vigiladas por la estrategia en 4h y 1h (add/remove/check)\n"
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

    risk = f"{stats['riesgo_medio_pct']:.2f}%" if stats["riesgo_medio_pct"] is not None else "N/D"
    lines = [f"*{label}*: {stats['total']} señales  ·  riesgo medio a stop: {risk}"]

    for ratio in TP_RATIOS:
        sc = stats["escenarios"][ratio]
        if sc["resueltas"] == 0:
            lines.append(f"  Objetivo {ratio_label(ratio)}: sin operaciones resueltas")
            continue
        win_rate = f"{sc['win_rate']:.1f}%" if sc["win_rate"] is not None else "N/D"
        extras = ""
        if sc["horizonte"]:
            extras += f" · {sc['horizonte']} al horizonte"
        if sc["pendientes"]:
            extras += f" · {sc['pendientes']} abiertas"
        lines.append(
            f"  Objetivo {ratio_label(ratio)}: {sc['tp']} TP · {sc['sl']} SL{extras} · "
            f"acierto {win_rate}\n"
            f"    R total {sc['r_total']:+.2f}R · R medio {sc['r_medio']:+.2f}R "
            f"· cambio total {sc['cambio_total_pct']:+.2f}%"
        )

    best = best_scenario(stats)
    if best is not None:
        lines.append(f"  ➜ Mejor: {ratio_label(best)} (por R medio)")
    return "\n".join(lines)


_OUTCOME_ICON = {
    OUTCOME_TP: "✅",
    OUTCOME_SL: "❌",
    OUTCOME_HORIZON: "➖",
    OUTCOME_PENDING: "⏳",
}


async def backtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: /backtest SIMBOLO (INTERVALO) (N_VELAS) (HORIZONTE)\n"
            "Ejemplo: /backtest BTCUSDT 4h 500 (o con límite: /backtest BTCUSDT 4h 500 5)\n\n"
            "INTERVALO por defecto `4h` (el de la estrategia).\n"
            f"N_VELAS por defecto 500, máximo {BACKTEST_MAX_CANDLES} — cuánto histórico traer.\n"
            "HORIZONTE es opcional — máximo de velas que se mantiene cada operación; "
            "si no lo pones, la operación dura hasta tocar el objetivo o el stop. "
            "Con horizonte, si no toca ninguno, se cierra al cierre de esa vela.\n"
            "Se simulan dos objetivos (0.5:1 y 1:1 respecto a la distancia al stop) y se "
            "comparan; el stop corta la operación si el precio lo toca.",
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
        horizon = int(args[3]) if len(args) > 3 else None
    except ValueError:
        await update.message.reply_text("N_VELAS y HORIZONTE deben ser números enteros.")
        return

    n_velas = max(50, min(n_velas, BACKTEST_MAX_CANDLES))
    if horizon is not None:
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
    opens = [float(k["open"]) for k in klines]
    times = [k["time"] for k in klines]

    results = run_backtest(closes, highs, lows, opens, times, horizon=horizon)
    stats = summarize(results)

    first_date = datetime.fromtimestamp(times[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    last_date = datetime.fromtimestamp(times[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    header = (
        f"*Backtest {symbol} — {interval}*\n"
        f"{len(klines)} velas ({first_date} → {last_date}), "
        + (f"horizonte {horizon} velas" if horizon is not None else "sin límite de velas por operación")
        + "\n\n"
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
        outcomes = "  ".join(
            f"{ratio_label(ratio)} {_OUTCOME_ICON.get(r.scenarios[ratio].outcome, '·')}"
            for ratio in TP_RATIOS
        )
        recent_lines.append(
            f"{side_emoji} `{date}` {r.side} @ `{r.close:.4f}` SL `{r.stop_loss:.4f}` → {outcomes}"
        )

    detail_header = (
        f"_Últimas {len(recent)} de {len(results)} señales:_"
        if len(results) > len(recent)
        else "_Señales:_"
    )
    detail_text = detail_header + "\n" + "\n".join(recent_lines)
    detail_text += "\n_✅ objetivo · ❌ stop · ➖ cerrada al horizonte · ⏳ aún abierta_"

    await update.message.reply_text(header + summary_text + "\n\n" + detail_text, parse_mode="Markdown")

    try:
        workbook_buffer = build_backtest_workbook(symbol, interval, horizon, klines, results, stats)
    except Exception:  # noqa: BLE001
        logger.exception("Error generando el Excel del backtest")
        await update.message.reply_text(
            "⚠️ El resumen de arriba sí se calculó bien, pero no pude generar el Excel con el detalle."
        )
        return

    filename = f"backtest_{symbol}_{interval}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.xlsx"
    await update.message.reply_document(
        document=workbook_buffer,
        filename=filename,
        caption=f"📊 Detalle completo: {len(results)} señales y {len(klines)} velas de {symbol} ({interval}).",
    )


# --- Watchlist de alertas: monedas vigiladas por la estrategia (4h y 1h) ---

async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    args = context.args

    if not args:
        if not alerts_watchlist:
            message = (
                "No estás vigilando ninguna moneda con la estrategia todavía.\n\n"
                "Uso:\n/alerts add SIMBOLO\n/alerts remove SIMBOLO"
            )
        else:
            message = "*Monedas vigiladas (estrategia en 4h y 1h):*\n" + "\n".join(
                f"• `{s}`" for s in alerts_watchlist
            )
            message += (
                "\n\nUso:\n/alerts add SIMBOLO\n/alerts remove SIMBOLO\n/alerts reset — vacía la lista\n"
                "/alerts check [SIMBOLO] [4h|1h] — estado actual de cada condición"
            )
        await update.message.reply_text(message, parse_mode="Markdown")
        return

    action = args[0].lower()

    if action == "reset":
        alerts_watchlist.clear()
        _save_alerts_watchlist(alerts_watchlist)
        await update.message.reply_text("Watchlist de alertas vaciada.")
        return

    if action == "check":
        rest = [a.lower() if a.lower() in ALERT_INTERVALS else a for a in args[1:]]
        intervals = [a for a in rest if a in ALERT_INTERVALS] or list(ALERT_INTERVALS)
        symbol_args = [a for a in rest if a not in ALERT_INTERVALS]
        symbols = [_normalize_symbol(symbol_args[0])] if symbol_args else alerts_watchlist
        if not symbols:
            await update.message.reply_text(
                "No hay ninguna moneda en la watchlist de alertas. Usa /alerts add SIMBOLO primero, "
                "o /alerts check SIMBOLO para probar uno suelto."
            )
            return

        for interval in intervals:
            blocks, _ = _build_status_blocks(symbols, interval)
            await _send_chunks(update.message.reply_text, [f"📊 *Estado de la estrategia — {interval}*"] + blocks)
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

    risk = f"{stats['riesgo_medio_pct']:.2f}%" if stats["riesgo_medio_pct"] is not None else "N/D"
    lines = [f"*{label}*: {stats['total']} señales  ·  riesgo medio a stop: {risk}"]

    for ratio in TP_RATIOS:
        sc = stats["escenarios"][ratio]
        if sc["resueltas"] == 0:
            lines.append(f"  Objetivo {ratio_label(ratio)}: sin operaciones resueltas")
            continue
        win_rate = f"{sc['win_rate']:.1f}%" if sc["win_rate"] is not None else "N/D"
        extras = ""
        if sc["horizonte"]:
            extras += f" · {sc['horizonte']} al horizonte"
        if sc["pendientes"]:
            extras += f" · {sc['pendientes']} abiertas"
        lines.append(
            f"  Objetivo {ratio_label(ratio)}: {sc['tp']} TP · {sc['sl']} SL{extras} · "
            f"acierto {win_rate}\n"
            f"    R total {sc['r_total']:+.2f}R · R medio {sc['r_medio']:+.2f}R "
            f"· cambio total {sc['cambio_total_pct']:+.2f}%"
        )

    best = best_scenario(stats)
    if best is not None:
        lines.append(f"  ➜ Mejor: {ratio_label(best)} (por R medio)")
    return "\n".join(lines)


_OUTCOME_ICON = {
    OUTCOME_TP: "✅",
    OUTCOME_SL: "❌",
    OUTCOME_HORIZON: "➖",
    OUTCOME_PENDING: "⏳",
}


async def backtest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: /backtest SIMBOLO (INTERVALO) (N_VELAS) (HORIZONTE)\n"
            "Ejemplo: /backtest BTCUSDT 4h 500 (o con límite: /backtest BTCUSDT 4h 500 5)\n\n"
            "INTERVALO por defecto `4h` (el de la estrategia).\n"
            f"N_VELAS por defecto 500, máximo {BACKTEST_MAX_CANDLES} — cuánto histórico traer.\n"
            "HORIZONTE es opcional — máximo de velas que se mantiene cada operación; "
            "si no lo pones, la operación dura hasta tocar el objetivo o el stop. "
            "Con horizonte, si no toca ninguno, se cierra al cierre de esa vela.\n"
            "Se simulan dos objetivos (0.5:1 y 1:1 respecto a la distancia al stop) y se "
            "comparan; el stop corta la operación si el precio lo toca.",
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
        horizon = int(args[3]) if len(args) > 3 else None
    except ValueError:
        await update.message.reply_text("N_VELAS y HORIZONTE deben ser números enteros.")
        return

    n_velas = max(50, min(n_velas, BACKTEST_MAX_CANDLES))
    if horizon is not None:
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
    opens = [float(k["open"]) for k in klines]
    times = [k["time"] for k in klines]

    results = run_backtest(closes, highs, lows, opens, times, horizon=horizon)
    stats = summarize(results)

    first_date = datetime.fromtimestamp(times[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    last_date = datetime.fromtimestamp(times[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    header = (
        f"*Backtest {symbol} — {interval}*\n"
        f"{len(klines)} velas ({first_date} → {last_date}), "
        + (f"horizonte {horizon} velas" if horizon is not None else "sin límite de velas por operación")
        + "\n\n"
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
        outcomes = "  ".join(
            f"{ratio_label(ratio)} {_OUTCOME_ICON.get(r.scenarios[ratio].outcome, '·')}"
            for ratio in TP_RATIOS
        )
        recent_lines.append(
            f"{side_emoji} `{date}` {r.side} @ `{r.close:.4f}` SL `{r.stop_loss:.4f}` → {outcomes}"
        )

    detail_header = (
        f"_Últimas {len(recent)} de {len(results)} señales:_"
        if len(results) > len(recent)
        else "_Señales:_"
    )
    detail_text = detail_header + "\n" + "\n".join(recent_lines)
    detail_text += "\n_✅ objetivo · ❌ stop · ➖ cerrada al horizonte · ⏳ aún abierta_"

    await update.message.reply_text(header + summary_text + "\n\n" + detail_text, parse_mode="Markdown")

    try:
        workbook_buffer = build_backtest_workbook(symbol, interval, horizon, klines, results, stats)
    except Exception:  # noqa: BLE001
        logger.exception("Error generando el Excel del backtest")
        await update.message.reply_text(
            "⚠️ El resumen de arriba sí se calculó bien, pero no pude generar el Excel con el detalle."
        )
        return

    filename = f"backtest_{symbol}_{interval}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.xlsx"
    await update.message.reply_document(
        document=workbook_buffer,
        filename=filename,
        caption=f"📊 Detalle completo: {len(results)} señales y {len(klines)} velas de {symbol} ({interval}).",
    )

# --- Watchlist de alertas: monedas vigiladas por la estrategia (4h y 1h) ---

async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    args = context.args

    if not args:
        if not alerts_watchlist:
            message = (
                "No estás vigilando ninguna moneda con la estrategia todavía.\n\n"
                "Uso:\n/alerts add SIMBOLO\n/alerts remove SIMBOLO"
            )
        else:
            message = "*Monedas vigiladas (estrategia en 4h y 1h):*\n" + "\n".join(
                f"• `{s}`" for s in alerts_watchlist
            )
            message += (
                "\n\nUso:\n/alerts add SIMBOLO\n/alerts remove SIMBOLO\n/alerts reset — vacía la lista\n"
                "/alerts check [SIMBOLO] [4h|1h] — estado actual de cada condición"
            )
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
            f"✅ Empezaré a vigilar `{symbol}` en 4h y 1h con la estrategia.", parse_mode="Markdown"
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


ALERT_INTERVALS = ("4h", "1h")  # plazos en los que se evalúa la estrategia
TELEGRAM_MAX_CHARS = 3800  # Telegram admite 4096 por mensaje; dejamos margen


def _md_safe(text) -> str:
    """Quita caracteres que rompen el Markdown de Telegram (p. ej. en mensajes de error)."""
    return re.sub(r"[_*`\[\]]", " ", str(text))


def _format_signal_message(symbol: str, signal, interval: str = "4h") -> str:
    side_label = "🟢 LONG" if signal.side == "LONG" else "🔴 SHORT"
    risk_pct = abs(signal.close - signal.stop_loss) / signal.close * 100
    sl_label = "mínimo local" if signal.side == "LONG" else "máximo local"
    return (
        f"🔔 *Señal de entrada — {symbol}* ({side_label})\n\n"
        f"Vela de {interval} recién cerrada.\n"
        f"Estocástico: %K `{signal.k:.2f}`  %D `{signal.d:.2f}`\n"
        f"Bandas de Bollinger: inf `{signal.lower:.4f}`  media `{signal.middle:.4f}`  "
        f"sup `{signal.upper:.4f}`\n"
        f"Precio de cierre: `{signal.close:.4f}`\n"
        f"Stop loss sugerido ({sl_label}): `{signal.stop_loss:.4f}` (riesgo {risk_pct:.2f}%)"
    )


def _format_status(symbol: str, interval: str, status: StrategyStatus) -> str:
    """
    Resumen de una moneda: el lado más cercano a dar señal con cada condición
    (✅ cumple / ❌ no cumple, con sus valores) y una línea para el otro lado.
    """
    emoji = {"LONG": "🟢", "SHORT": "🔴"}
    side = status.leading_side()
    other_side = "SHORT" if side == "LONG" else "LONG"
    conditions = status.long if side == "LONG" else status.short
    other = status.short if side == "LONG" else status.long

    head = f"*{symbol}* · {interval} · cierre `{status.close:.6g}`"
    if status.signal is not None:
        head = f"🔔 {head} — *SEÑAL {status.signal.side}*"

    lines = [head, f"{emoji[side]} {side}: {status.met(side)}/{len(conditions)} condiciones"]
    for c in conditions:
        lines.append(f"  {'✅' if c.ok else '❌'} {c.label} — {c.detail}")

    other_ok = [c.label for c in other if c.ok]
    other_line = f"{emoji[other_side]} {other_side}: {status.met(other_side)}/{len(other)}"
    if other_ok:
        other_line += f" (cumple: {', '.join(other_ok)})"
    lines.append(other_line)
    return "\n".join(lines)


def _chunk_blocks(blocks: list[str], limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    """Agrupa bloques de texto en mensajes que no superen el límite de Telegram."""
    chunks: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > limit and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def _send_chunks(send, blocks: list[str]) -> None:
    """
    Envía los bloques en uno o varios mensajes. `send` es `reply_text` o
    `bot.send_message` (con chat_id ya fijado). Si Telegram rechaza el
    Markdown de un mensaje, lo reintenta como texto plano para no perderlo.
    """
    for chunk in _chunk_blocks(blocks):
        try:
            await send(text=chunk, parse_mode="Markdown")
        except Exception:  # noqa: BLE001
            logger.warning("Markdown rechazado por Telegram; reenviando como texto plano.", exc_info=True)
            try:
                await send(text=chunk)
            except Exception:  # noqa: BLE001
                logger.exception("Error enviando el mensaje de alertas")


def _evaluate_alert_symbol(symbol: str, interval: str = "4h") -> tuple[object | None, StrategyStatus]:
    """
    Trae las últimas velas de `symbol` en `interval` y evalúa la estrategia
    sobre la última vela cerrada. Devuelve (Signal o None, estado condición
    por condición) — el estado se devuelve siempre, haya señal o no.
    """
    klines = bitunix.get_kline(symbol, interval=interval, limit=100)
    closes = [float(k["close"]) for k in klines]
    highs = [float(k["high"]) for k in klines]
    lows = [float(k["low"]) for k in klines]
    opens = [float(k["open"]) for k in klines]

    status = evaluate_conditions(closes, highs, lows, opens)
    if status is None:
        raise ValueError("no hay suficientes velas para calcular los indicadores")
    return status.signal, status

def _build_status_blocks(symbols: list[str], interval: str) -> tuple[list[str], list[tuple[str, object]]]:
    """Evalúa cada símbolo y devuelve (bloques de texto del resumen, [(símbolo, Signal)] con señal)."""
    blocks: list[str] = []
    signals: list[tuple[str, object]] = []
    for symbol in symbols:
        try:
            signal, status = _evaluate_alert_symbol(symbol, interval)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error evaluando la estrategia para %s (%s)", symbol, interval)
            blocks.append(f"⚠️ *{symbol}* · {interval}: error consultando velas ({_md_safe(exc)})")
            continue
        blocks.append(_format_status(symbol, interval, status))
        if signal is not None:
            signals.append((symbol, signal))
    return blocks, signals


async def check_alerts_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Se ejecuta al cierre de cada vela del plazo indicado en `job.data`
    ("4h" o "1h"). Para cada símbolo de alerts_watchlist: avisa a
    ALLOWED_USER_ID si hay señal y, haya o no, envía después un resumen con
    el estado de cada condición de la estrategia.
    """
    interval = context.job.data if context.job is not None and context.job.data else "4h"

    if not alerts_watchlist:
        return
    if ALLOWED_USER_ID is None:
        logger.warning("check_alerts_job: TELEGRAM_ALLOWED_USER_ID no está definido, no se puede avisar a nadie.")
        return

    send = lambda **kw: context.bot.send_message(chat_id=ALLOWED_USER_ID, **kw)  # noqa: E731

    blocks, signals = _build_status_blocks(list(alerts_watchlist), interval)

    # 1) Alertas de señal, como antes (mensaje propio por moneda).
    for symbol, signal in signals:
        await _send_chunks(send, [_format_signal_message(symbol, signal, interval)])

    # 2) Resumen del estado de todas las monedas, haya señal o no.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = f"📊 *Resumen {interval}* — {now}"
    await _send_chunks(send, [header] + blocks)


def _next_boundary_utc(interval_hours: int) -> datetime:
    """Próximo cierre de vela (cada `interval_hours` horas, alineado a 00:00 UTC),
    con 30s de margen para que Bitunix ya haya publicado la vela cerrada."""
    now = datetime.now(timezone.utc)
    next_hour = (now.hour // interval_hours + 1) * interval_hours
    return (now.replace(minute=0, second=30, microsecond=0, hour=0)
            + timedelta(hours=next_hour))


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


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler global: registra cualquier excepción no capturada y, si puede
    identificar el chat de origen, le avisa al usuario en vez de dejarlo
    sin respuesta (como pasó con el error de Markdown en /backtest).
    """
    logger.exception("Excepción no manejada", exc_info=context.error)

    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Ocurrió un error inesperado procesando tu comando. Ya quedó registrado en los logs.",
            )
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo notificar el error al chat de origen")


async def _post_init(application: Application) -> None:
    """Registra los comandos en Telegram (botón 'Menú' / lista al escribir '/')."""
    await application.bot.set_my_commands(BOT_COMMANDS)
    logger.info("Comandos registrados en el menú de Telegram.")


def _enable_health_check() -> None:
    """
    En modo webhook, python-telegram-bot solo responde en la ruta del webhook
    (POST /webhook); cualquier otra URL, incluida la raíz "/", devuelve 404.
    Eso hace que monitores como UptimeRobot (y el Health Check de Render)
    vean el servicio como caído aunque el bot funcione bien.

    Esta función añade al servidor interno una ruta GET/HEAD "/" que responde
    200 "OK". Debe llamarse ANTES de `run_webhook`. Si en alguna versión
    futura de la librería la estructura interna cambia, no rompe el bot:
    solo deja un aviso en los logs y "/" seguirá devolviendo 404.
    """
    try:
        from telegram.ext._utils.webhookhandler import WebhookAppClass
        from tornado.web import RequestHandler

        class HealthHandler(RequestHandler):
            def get(self) -> None:
                self.set_status(200)
                self.write("OK")

            def head(self) -> None:
                self.set_status(200)

        original_init = WebhookAppClass.__init__

        def patched_init(self, *args, **kwargs) -> None:
            original_init(self, *args, **kwargs)
            self.add_handlers(r".*", [(r"/", HealthHandler)])

        WebhookAppClass.__init__ = patched_init
    except Exception:  # noqa: BLE001
        logger.warning("No se pudo activar el health check en '/'; seguirá devolviendo 404.", exc_info=True)


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
    application.add_error_handler(_error_handler)

    if application.job_queue is not None:
        for interval, hours in (("4h", 4), ("1h", 1)):
            first_run = _next_boundary_utc(hours)
            application.job_queue.run_repeating(
                check_alerts_job,
                interval=timedelta(hours=hours),
                first=first_run,
                name=f"check_alerts_{interval}",
                data=interval,
            )
            logger.info(
                "Estrategia de alertas %s programada. Primera revisión: %s (UTC)", interval, first_run.isoformat()
            )
    else:
        logger.warning(
            "JobQueue no disponible: instala 'python-telegram-bot[job-queue]' para que "
            "/alerts funcione. Sin esto, la estrategia nunca se evaluará sola."
        )

    port = int(os.environ.get("PORT", 8443))
    render_url = os.environ.get("RENDER_EXTERNAL_URL")

    if render_url:
        webhook_path = "webhook"
        webhook_url = f"{render_url.rstrip('/')}/{webhook_path}"
        logger.info("Modo webhook (Render) -> %s", webhook_url)
        _enable_health_check()
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
