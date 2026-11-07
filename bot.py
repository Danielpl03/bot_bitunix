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

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.request import HTTPXRequest
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
from indicators import bollinger_bands, stochastic_oscillator, rsi_indicator
from strategy import StrategyStatus, evaluate_conditions
from strategy_rsi import generate_rsi_signals
from xlsx_export import build_backtest_workbook
from backtest_kelner import (
    KELTNER_TP_RATIOS,
    run_backtest as run_keltner_backtest,
    summarize as summarize_keltner,
)
from xlsx_export_keltner import build_keltner_workbook
from backtest_reversion import run_backtest as run_reversion_backtest, summarize as summarize_reversion, TP as REVERSION_TP, SL as REVERSION_SL, HORIZON as REVERSION_HORIZON, PENDING as REVERSION_PENDING
from xlsx_export_reversion import build_reversion_workbook
from backtest_rsi import run_backtest as run_rsi_backtest, summarize as summarize_rsi, TP as RSI_TP, SL as RSI_SL, HORIZON as RSI_HORIZON, PENDING as RSI_PENDING
from xlsx_export_rsi import build_rsi_workbook
from backtest_squeeze import run_backtest as run_squeeze_backtest, summarize as summarize_squeeze, TP as SQUEEZE_TP, SL as SQUEEZE_SL, HORIZON as SQUEEZE_HORIZON, PENDING as SQUEEZE_PENDING
from xlsx_export_squeeze import build_squeeze_workbook
from backtest_dual_thrust import run_backtest as run_dual_thrust_backtest, summarize as summarize_dual_thrust, build_equity_curve as build_dual_thrust_equity_curve, PENDING as DUAL_THRUST_PENDING
from backtest_dual_thrust_multitimeframe import (load_multitimeframe_candles, run_backtest_multitimeframe, validate_timeframes)
from backtest_trend_trader_remastered import run_backtest as run_ttr_backtest
from xlsx_export_dual_thrust import build_dual_thrust_workbook
from xlsx_export_ttr import build_ttr_workbook
from ttr_live import (
    STORE as TTR_LIVE_STORE,
    monitor_job as ttr_live_monitor_job,
    supervisor_interval_seconds as ttr_supervisor_interval_seconds,
)
from rsi_live import STORE as RSI_LIVE_STORE, monitor_job as rsi_live_monitor_job
from backtest_common import (parse_backtest_request, load_backtest_klines, filter_signals_to_period, filter_signals_by_density, filter_signals_by_opposite_band, filter_signals_by_channel_width)
from risk_management import RiskConfig
from strategies.trend_impulse_channels import latest_trend_impulse_channels

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
    BotCommand("indicators", "RSI, Estocástico, Bollinger y Trend Impulse Channels (/indicators BTCUSDT 1h)"),
    BotCommand("alerts", "Suscripciones de alertas Keltner y RSI (add/remove/check)"),
    BotCommand("backtest", "Backtest Bollinger por velas o fechas"),
    BotCommand("backtest_keltner", "Backtest Keltner por velas o fechas"),
    BotCommand("backtest_reversion", "Backtest reversión Bollinger + Estocástico"),
    BotCommand("backtest_rsi", "Backtest RSI por zonas extremas"),
    BotCommand("backtest_tic", "Backtest Trend Impulse Channels Breakout"),
    BotCommand("tic_live", "Paper Live Trend Impulse Channels"),
    BotCommand("tic_stop", "Detener TIC Live sin borrar posiciones"),
    BotCommand("tic_status", "Estado y operaciones abiertas TIC"),
    BotCommand("tic_history", "Historial global o por símbolo TIC"),
    BotCommand("tic_stats", "Estadísticas globales y por símbolo TIC"),
    BotCommand("tic_notify", "Configurar notificaciones TIC"),
    BotCommand("tic_health", "Salud del monitor y checkpoints TIC"),
    BotCommand("tic_restart", "Borrar y reiniciar TIC Live desde cero"),
    BotCommand("backtest_squeeze", "Backtest Squeeze Bollinger + Keltner"),
    BotCommand("backtest_dual_thrust", "Backtest ruptura de rango Dual Thrust"),
    BotCommand("backtest_ttr", "Backtest TTR Pine v6 con TP parciales y reversiones"),
    BotCommand("ttr_live", "Paper trading TTR en tiempo real (sin órdenes)"),
    BotCommand("rsi_live", "Paper trading RSI multi-temporalidad (sin órdenes)"),
    BotCommand("rsi_status", "Estado e historial de operaciones RSI"),
    BotCommand("ttr_stop", "Detener TTR Live para un símbolo (/ttr_stop BTCUSDT)"),
    BotCommand("ttr_restart", "Borrar y reiniciar TTR Live desde cero (incluye históricos)"),
    BotCommand("ttr_notify", "Activar/desactivar avisos TTR por evento y símbolo"),
    BotCommand("ttr_status", "Estado de operaciones TTR en seguimiento"),
    BotCommand("ttr_history", "Historial global o por símbolo de TTR"),
    BotCommand("ttr_stats", "Rendimiento global y por símbolo de TTR"),
    BotCommand("ttr_health", "Salud y supervisión de TTR Live"),
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

# Suscripción independiente para la estrategia RSI por regímenes.
RSI_ALERTS_FILE = os.environ.get("RSI_ALERTS_FILE", "rsi_alerts_watchlist.json")
RSI_ALERT_STATE_FILE = os.environ.get("RSI_ALERT_STATE_FILE", "rsi_alert_state.json")


def _load_symbol_list(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list) and all(isinstance(s, str) for s in data):
                return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return []


def _save_symbol_list(path: str, symbols: list[str]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(symbols, f)
    except OSError:
        logger.exception("No se pudo guardar la watchlist %s", path)


rsi_alerts_watchlist: list[str] = _load_symbol_list(RSI_ALERTS_FILE)


def _load_rsi_alert_state() -> dict[str, int]:
    try:
        with open(RSI_ALERT_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {str(k): int(v) for k, v in data.items()}
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return {}


def _save_rsi_alert_state(state: dict[str, int]) -> None:
    try:
        with open(RSI_ALERT_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        logger.exception("No se pudo guardar el estado de alertas RSI")


rsi_alert_state: dict[str, int] = _load_rsi_alert_state()


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
        "/indicators SIMBOLO [INTERVALO] - RSI, media RSI, estocástico y Bandas de Bollinger\n"
        "/alerts - suscripciones de alertas Keltner y RSI (4h y 1h)\n"
        "/backtest SIMBOLO [INTERVALO] [N_VELAS] [HORIZONTE] - backtest por cantidad de velas\n"
        "/backtest SIMBOLO [INTERVALO] AAAA-MM-DD AAAA-MM-DD [HORIZONTE] - backtest por fechas UTC\n"
        "/backtest_keltner ... - backtest con Canal Keltner\n"
        "/backtest_reversion ... - reversión a la media con Bollinger + Estocástico\n"
        "/backtest_rsi ... - estrategia RSI extremo, stop estructural y TP en extremo opuesto\n"
        "/rsi_live add BTCUSDT 15m [RIESGO_USDT] - activar RSI Live paper\n"
        "/rsi_live remove BTCUSDT 15m | /rsi_live list\n"
        "/rsi_status [SIMBOLO] - operaciones RSI abiertas e historial\n"
        "/backtest_squeeze ... - ruptura Squeeze con Bollinger + Keltner\n"
        "/backtest_dual_thrust ... - ruptura de rango Dual Thrust\n"
        " /backtest_ttr SIMBOLO INTERVALO VELAS [--re-entry true|false] [--tp 25,25,25,25] [--dir Buy & Sell|Buy Only|Sell Only]\n\n"
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

    if args and args[0].lower() == "rsi":
        subargs = args[1:]
        if not subargs:
            if rsi_alerts_watchlist:
                message = "*Monedas suscritas a alertas RSI (1h y 4h):*\n" + "\n".join(
                    f"• `{symbol}`" for symbol in rsi_alerts_watchlist
                )
            else:
                message = "No hay monedas suscritas a la estrategia RSI.\nUsa `/alerts rsi add BTCUSDT`."
            message += "\n\nComandos: `/alerts rsi add SIMBOLO`, `/alerts rsi remove SIMBOLO`, `/alerts rsi reset`, `/alerts rsi check [SIMBOLO] [1h|4h]`."
            await update.message.reply_text(message, parse_mode="Markdown")
            return

        rsi_action = subargs[0].lower()
        if rsi_action == "reset":
            rsi_alerts_watchlist.clear()
            _save_symbol_list(RSI_ALERTS_FILE, rsi_alerts_watchlist)
            await update.message.reply_text("Watchlist de alertas RSI vaciada.")
            return
        if rsi_action == "check":
            rest = [x.lower() if x.lower() in ALERT_INTERVALS else x for x in subargs[1:]]
            intervals = [x for x in rest if x in ALERT_INTERVALS] or list(ALERT_INTERVALS)
            symbols = [_normalize_symbol(x) for x in rest if x not in ALERT_INTERVALS]
            symbols = symbols[:1] if symbols else list(rsi_alerts_watchlist)
            if not symbols:
                await update.message.reply_text("No hay monedas suscritas. Usa `/alerts rsi add BTCUSDT`.", parse_mode="Markdown")
                return
            for interval in intervals:
                lines = [f"📊 *Estado RSI — {interval}*"]
                for symbol in symbols:
                    try:
                        signal, candle_time, latest_rsi, latest_ma = _evaluate_rsi_alert_symbol(symbol, interval)
                        line = f"*{symbol}*: RSI `{latest_rsi:.2f}` / media `{latest_ma:.2f}`" if latest_ma is not None else f"*{symbol}*: RSI `{latest_rsi:.2f}` / media no disponible"
                        if signal:
                            line += f" — señal *{signal.side}* ({signal.signal_type})"
                        lines.append(line)
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Error comprobando RSI para %s (%s)", symbol, interval)
                        lines.append(f"⚠️ *{symbol}*: {_md_safe(exc)}")
                await _send_chunks(update.message.reply_text, lines)
            return
        if rsi_action in ("add", "remove", "del") and len(subargs) < 2:
            await update.message.reply_text(f"Uso: /alerts rsi {rsi_action} SIMBOLO")
            return
        if rsi_action == "add":
            symbol = _normalize_symbol(subargs[1])
            if symbol in rsi_alerts_watchlist:
                await update.message.reply_text(f"`{symbol}` ya está suscrita a RSI.", parse_mode="Markdown")
                return
            rsi_alerts_watchlist.append(symbol)
            _save_symbol_list(RSI_ALERTS_FILE, rsi_alerts_watchlist)
            await update.message.reply_text(f"✅ RSI vigilará `{symbol}` en 1h y 4h y avisará cuando cierre una vela con señal.", parse_mode="Markdown")
            return
        if rsi_action in ("remove", "del"):
            symbol = _normalize_symbol(subargs[1])
            if symbol not in rsi_alerts_watchlist:
                await update.message.reply_text(f"`{symbol}` no estaba suscrita a RSI.", parse_mode="Markdown")
                return
            rsi_alerts_watchlist.remove(symbol)
            _save_symbol_list(RSI_ALERTS_FILE, rsi_alerts_watchlist)
            await update.message.reply_text(f"🗑 RSI dejará de vigilar `{symbol}`.", parse_mode="Markdown")
            return
        await update.message.reply_text("Uso: /alerts rsi [add|remove|reset|check] [SIMBOLO] [1h|4h]")
        return

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


def _parse_backtest_args(raw_args: list[str]) -> tuple[list[str], RiskConfig, int | None, int | None, float | None, float | None, bool]:
    """Extrae opciones de riesgo y del filtro de señales, en cualquier orden.

    --riesgo fijo [USDT] | --riesgo porcentaje [PORCENTAJE] [CAPITAL_USDT]
    --filtro-rango VELAS_ANTERIORES LIMITE_SEÑALES
    --filtro-banda RATIO (distancia a banda contraria en múltiplos de R)
    --filtro-anchura PORCENTAJE (anchura mínima del canal respecto al precio de entrada)
    --solo-bandas-limite (solo Keltner: LONG cruza banda inferior; SHORT cruza superior)
    """
    positional: list[str] = []
    config = RiskConfig()
    window = limit = None
    band_ratio = None
    channel_width_pct = None
    only_outer_bands = False
    args = list(raw_args)
    i = 0
    while i < len(args):
        token = args[i].lower()
        if token == "--solo-bandas-limite":
            only_outer_bands = True
            i += 1
            continue
        if token == "--filtro-anchura":
            if i + 1 >= len(args):
                raise ValueError("Uso del filtro de anchura: --filtro-anchura 1.5.")
            try:
                channel_width_pct = float(args[i + 1])
            except ValueError as exc:
                raise ValueError("El porcentaje del filtro de anchura debe ser numérico; ejemplo: --filtro-anchura 1.5.") from exc
            if channel_width_pct <= 0:
                raise ValueError("El porcentaje mínimo de anchura debe ser mayor que cero.")
            i += 2
            continue
        if token == "--filtro-banda":
            if i + 1 >= len(args):
                raise ValueError("Uso del filtro de banda: --filtro-banda 2.")
            try:
                band_ratio = float(args[i + 1])
            except ValueError as exc:
                raise ValueError("El ratio del filtro de banda debe ser numérico; ejemplo: --filtro-banda 2.") from exc
            if band_ratio <= 0:
                raise ValueError("El ratio del filtro de banda debe ser mayor que cero.")
            i += 2
            continue
        if token == "--filtro-rango":
            if i + 2 >= len(args):
                raise ValueError("Uso del filtro: --filtro-rango VELAS_ANTERIORES LIMITE_SEÑALES.")
            try:
                window, limit = int(args[i + 1]), int(args[i + 2])
            except ValueError as exc:
                raise ValueError("El filtro requiere dos enteros: --filtro-rango 10 3.") from exc
            if window < 1 or limit < 1:
                raise ValueError("Las velas y el límite del filtro deben ser enteros positivos.")
            i += 3
            continue
        if token == "--riesgo":
            if i + 1 >= len(args):
                raise ValueError("Uso de riesgo: --riesgo fijo 10 o --riesgo porcentaje 1 1000.")
            mode = args[i + 1].lower()
            try:
                if mode in ("fijo", "fixed"):
                    amount = 10.0
                    consumed = 2
                    if i + 2 < len(args) and not args[i + 2].startswith("--"):
                        amount = float(args[i + 2]); consumed = 3
                    config = RiskConfig(mode="fixed", fixed_risk_usdt=amount)
                elif mode in ("porcentaje", "percent", "porcentual"):
                    pct, capital, consumed = 1.0, 1000.0, 2
                    if i + 2 < len(args) and not args[i + 2].startswith("--"):
                        pct = float(args[i + 2]); consumed = 3
                    if i + 3 < len(args) and not args[i + 3].startswith("--"):
                        capital = float(args[i + 3]); consumed = 4
                    config = RiskConfig(mode="percent", risk_percent=pct, initial_capital_usdt=capital)
                else:
                    raise ValueError
            except (ValueError, TypeError) as exc:
                raise ValueError("Opciones de riesgo inválidas. Usa --riesgo fijo 10 o --riesgo porcentaje 1 1000.") from exc
            i += consumed
            continue
        positional.append(args[i])
        i += 1
    return positional, config, window, limit, band_ratio, channel_width_pct, only_outer_bands


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
    try:
        args, risk_config, filter_window, filter_limit, band_ratio, channel_width_pct, only_outer_bands = _parse_backtest_args(context.args)
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return
    if only_outer_bands:
        await update.message.reply_text("⚠️ --solo-bandas-limite solo está disponible para /backtest_keltner.")
        return
    if not args:
        await update.message.reply_text(
            "Uso por cantidad: /backtest BTCUSDT 4h 500 [HORIZONTE]\n"
            "Uso por período: /backtest BTCUSDT 4h 2025-01-01 2025-06-30 [HORIZONTE]\n\n"
            f"Las fechas son UTC e incluyen ambos días. Máximo histórico por cantidad: {BACKTEST_MAX_CANDLES} velas.\n"
            "En modo fechas se descargan velas previas para calentar indicadores y posteriores para evaluar las últimas operaciones; "
            "solo se cuentan señales cuya entrada esté dentro del período.\n"
            "HORIZONTE es opcional: máximo de velas por operación.\n"
            "Riesgo opcional: --riesgo fijo 10 | --riesgo porcentaje 1 1000.\n"
            "Filtros opcionales: --filtro-rango 10 3; --filtro-banda 2 (espacio > 2R hasta banda contraria); --filtro-anchura 1.5 (canal mínimo del 1.5% del precio).\n"
            "Por defecto: 10 USDT por operación; margen aislado y buffer estimado del 25% más allá del stop.",
            parse_mode="Markdown",
        )
        return

    try:
        request = parse_backtest_request(args, normalize_symbol=_normalize_symbol, default_interval="4h", max_candles=BACKTEST_MAX_CANDLES)
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return
    symbol, interval, horizon = request.symbol, request.interval, request.horizon
    if interval not in VALID_INTERVALS:
        await update.message.reply_text(
            f"Intervalo `{interval}` no válido. Usa uno de: {' '.join(sorted(VALID_INTERVALS, key=len))}",
            parse_mode="Markdown",
        )
        return

    period_text = (
        f"período {datetime.fromtimestamp(request.start_ms / 1000, tz=timezone.utc):%Y-%m-%d} → "
        f"{datetime.fromtimestamp(request.end_ms / 1000, tz=timezone.utc):%Y-%m-%d} UTC"
        if request.is_date_range else f"hasta {request.candle_count} velas recientes"
    )
    await update.message.reply_text(
        f"Descargando histórico de `{symbol}` ({interval}, {period_text}) y corriendo el backtest…",
        parse_mode="Markdown",
    )
    try:
        klines, signal_start, signal_end = load_backtest_klines(
            bitunix, request, warmup_candles=max(100, filter_window or 0)
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error consultando histórico de Bitunix")
        await update.message.reply_text(f"⚠️ Error consultando histórico de Bitunix: {exc}")
        return
    if len(klines) < 40:
        await update.message.reply_text(
            f"Solo hay {len(klines)} velas de histórico para `{symbol}` en `{interval}` — no es suficiente.",
            parse_mode="Markdown",
        )
        return

    closes = [float(k["close"]) for k in klines]
    highs = [float(k["high"]) for k in klines]
    lows = [float(k["low"]) for k in klines]
    opens = [float(k["open"]) for k in klines]
    times = [k["time"] for k in klines]
    results = run_backtest(closes, highs, lows, opens, times, horizon=horizon)
    raw_results = results
    density_accepted, rejected_results = filter_signals_by_density(
        raw_results, times, filter_window, filter_limit
    )
    band_accepted, rejected_band_results = filter_signals_by_opposite_band(raw_results, band_ratio)
    band_accepted_times = {int(item.time) for item in band_accepted}
    width_accepted, rejected_width_results = filter_signals_by_channel_width(raw_results, channel_width_pct)
    width_accepted_times = {int(item.time) for item in width_accepted}
    results = [item for item in density_accepted if int(item.time) in band_accepted_times and int(item.time) in width_accepted_times]
    # Los filtros se calculan sobre todas las señales, antes de recortar el período.
    rejected_in_period = filter_signals_to_period(rejected_results, signal_start, signal_end)
    rejected_band_in_period = filter_signals_to_period(rejected_band_results, signal_start, signal_end)
    rejected_width_in_period = filter_signals_to_period(rejected_width_results, signal_start, signal_end)
    results = filter_signals_to_period(results, signal_start, signal_end)
    stats = summarize(results)

    first_date = datetime.fromtimestamp(times[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    last_date = datetime.fromtimestamp(times[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    requested = (
        f"período de señales {datetime.fromtimestamp(signal_start / 1000, tz=timezone.utc):%Y-%m-%d} → "
        f"{datetime.fromtimestamp(signal_end / 1000, tz=timezone.utc):%Y-%m-%d} UTC"
        if signal_start is not None else f"{len(klines)} velas ({first_date} → {last_date})"
    )
    header = (
        f"*Backtest Bollinger {symbol} — {interval}*\n"
        f"Gestión de riesgo: {risk_config.label}; margen aislado.\n"
        f"{requested}; velas cargadas {len(klines)} ({first_date} → {last_date}), "
        + (f"horizonte {horizon} velas" if horizon is not None else "sin límite de velas por operación")
        + "\n\n"
    )
    filter_description = (
        f"Filtro de agrupación: máximo {filter_limit} señales en las {filter_window} velas anteriores; "
        f"rechazadas en el período: {len(rejected_in_period)}.\n"
        if filter_window is not None else "Filtro de agrupación: desactivado.\n"
    )
    header += filter_description
    header += (f"Filtro de banda contraria: espacio > {band_ratio:g}R; rechazadas en el período: {len(rejected_band_in_period)}.\n" if band_ratio is not None else "Filtro de banda contraria: desactivado.\n")
    header += (f"Filtro de anchura del canal: mínimo {channel_width_pct:g}% del precio; rechazadas en el período: {len(rejected_width_in_period)}.\n" if channel_width_pct is not None else "Filtro de anchura del canal: desactivado.\n")
    if not results:
        await update.message.reply_text(header + "No quedaron señales operables en ese período con los parámetros actuales.", parse_mode="Markdown")
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
        outcomes = "  ".join(f"{ratio_label(ratio)} {_OUTCOME_ICON.get(r.scenarios[ratio].outcome, '·')}" for ratio in TP_RATIOS)
        recent_lines.append(f"{side_emoji} `{date}` {r.side} @ `{r.close:.4f}` SL `{r.stop_loss:.4f}` → {outcomes}")
    detail_header = f"_Últimas {len(recent)} de {len(results)} señales:_" if len(results) > len(recent) else "_Señales:_"
    detail_text = detail_header + "\n" + "\n".join(recent_lines) + "\n_✅ objetivo · ❌ stop · ➖ cerrada al horizonte · ⏳ aún abierta_"
    await update.message.reply_text(header + summary_text + "\n\n" + detail_text, parse_mode="Markdown")
    try:
        workbook_buffer = build_backtest_workbook(symbol, interval, horizon, klines, results, stats, risk_config, {"window": filter_window, "limit": filter_limit, "rejected": len(rejected_in_period), "band_ratio": band_ratio, "band_rejected": len(rejected_band_in_period), "channel_width_pct": channel_width_pct, "width_rejected": len(rejected_width_in_period), "only_outer_bands": only_outer_bands})
    except Exception:  # noqa: BLE001
        logger.exception("Error generando el Excel del backtest")
        await update.message.reply_text("⚠️ El resumen se calculó bien, pero no pude generar el Excel.")
        return
    filename = f"backtest_{symbol}_{interval}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.xlsx"
    await update.message.reply_document(document=workbook_buffer, filename=filename, caption=f"📊 Detalle: {len(results)} señales operables; rechazos por agrupación {len(rejected_in_period)}, por banda {len(rejected_band_in_period)}, por anchura {len(rejected_width_in_period)}; {len(klines)} velas de {symbol} ({interval}).")


async def backtest_reversion_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Backtest de reversión a la media: Bollinger + Estocástico."""
    raw_args = list(context.args)
    recovery_mode = any(token.lower() == "--recuperacion-banda" for token in raw_args)
    raw_args = [token for token in raw_args if token.lower() != "--recuperacion-banda"]
    try:
        args, risk_config, filter_window, filter_limit, band_ratio, channel_width_pct, only_outer_bands = _parse_backtest_args(raw_args)
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return
    if filter_window is not None or band_ratio is not None or channel_width_pct is not None or only_outer_bands:
        await update.message.reply_text(
            "⚠️ Los filtros de la estrategia anterior no se aplican a /backtest_reversion. "
            "Este backtest usa sus propias reglas de entrada y salida."
        )
        return
    if not args:
        await update.message.reply_text(
            "Estrategia de reversión a la media: Bollinger (20, 2) + Estocástico lento (14, 3, 3).\n\n"
            "Uso por cantidad: /backtest_reversion BTCUSDT 4h 500 [HORIZONTE]\n"
            "Uso por período: /backtest_reversion BTCUSDT 15m 2025-01-01 2025-01-31 [HORIZONTE]\n\n"
            "Modo original (predeterminado): LONG cierra bajo la banda inferior + cruce alcista de %K/%D con ambos < 20; "
            "SHORT cierra sobre la banda superior + cruce bajista con ambos > 80.\n"
            "Modo opcional --recuperacion-banda: exige toque/cierre fuera de la banda en las últimas 5 velas y que la vela de señal cierre de nuevo dentro del canal; mantiene el cruce estocástico extremo.\n"
            "Una sola posición simultánea por símbolo. Entrada a la apertura de la vela siguiente; stop en el extremo de las últimas 10 velas; "
            "objetivo en la banda media calculada en la vela de señal.\n"
            "HORIZONTE es opcional. Riesgo: --riesgo fijo 10 | --riesgo porcentaje 1 1000. "
            "No incluye comisiones, funding ni deslizamiento."
        )
        return
    try:
        request = parse_backtest_request(args, normalize_symbol=_normalize_symbol, default_interval="4h", max_candles=BACKTEST_MAX_CANDLES)
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return
    symbol, interval, horizon = request.symbol, request.interval, request.horizon
    if interval not in VALID_INTERVALS:
        await update.message.reply_text(f"Intervalo {interval} no válido. Usa uno de: {' '.join(sorted(VALID_INTERVALS, key=len))}")
        return
    period_text = (
        f"período {datetime.fromtimestamp(request.start_ms / 1000, tz=timezone.utc):%Y-%m-%d} → "
        f"{datetime.fromtimestamp(request.end_ms / 1000, tz=timezone.utc):%Y-%m-%d} UTC"
        if request.is_date_range else f"hasta {request.candle_count} velas recientes"
    )
    await update.message.reply_text(f"Descargando histórico de {symbol} ({interval}, {period_text}) y ejecutando reversión Bollinger + Estocástico…")
    try:
        klines, signal_start, signal_end = load_backtest_klines(
            bitunix, request, warmup_candles=100, followup_candles=200
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error consultando histórico para backtest de reversión")
        await update.message.reply_text(f"⚠️ Error consultando histórico de Bitunix: {exc}")
        return
    if len(klines) < 40:
        await update.message.reply_text(f"Solo hay {len(klines)} velas para {symbol} en {interval}; no es suficiente para el backtest.")
        return
    closes = [float(k["close"]) for k in klines]
    highs = [float(k["high"]) for k in klines]
    lows = [float(k["low"]) for k in klines]
    opens = [float(k["open"]) for k in klines]
    times = [int(k["time"]) for k in klines]
    try:
        all_trades = run_reversion_backtest(
            closes, highs, lows, opens, times, horizon=horizon,
            recovery_mode=recovery_mode, recovery_lookback=5,
        )
        trades = filter_signals_to_period(all_trades, signal_start, signal_end)
        stats = summarize_reversion(trades)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error calculando backtest de reversión")
        await update.message.reply_text(f"⚠️ Error calculando el backtest de reversión: {exc}")
        return
    first_date = datetime.fromtimestamp(times[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    last_date = datetime.fromtimestamp(times[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    period = (f"señales entre {datetime.fromtimestamp(signal_start / 1000, tz=timezone.utc):%Y-%m-%d} → "
              f"{datetime.fromtimestamp(signal_end / 1000, tz=timezone.utc):%Y-%m-%d} UTC"
              if signal_start is not None else f"{len(klines)} velas ({first_date} → {last_date})")
    variant_label = "Recuperación de banda (5 velas)" if recovery_mode else "Original (cierre fuera de banda)"
    header = (
        f"*Backtest Reversión Bollinger + Estocástico — {symbol} · {interval}*\n"
        f"Variante: {variant_label}.\nGestión de riesgo: {risk_config.label}.\n{period}; velas cargadas: {len(klines)}; "
        + (f"horizonte: {horizon} velas.\n" if horizon is not None else "sin límite de duración por operación.\n")
        + "Entrada: apertura de la vela siguiente; objetivo: banda media fijada en la señal; máximo una posición simultánea.\n"
        + "Costes de trading no incluidos.\n\n"
    )
    if not trades:
        await update.message.reply_text(header + "No se generaron operaciones válidas en el período con las reglas actuales.")
        return
    groups = []
    for label, key in (("TOTAL", "total"), ("LONG", "long"), ("SHORT", "short")):
        st = stats[key]
        wr = f"{st['win_rate']:.1f}%" if st["win_rate"] is not None else "N/D"
        avg = f"{st['r_medio']:+.2f}R" if st["r_medio"] is not None else "N/D"
        total_r = f"{st['r_total']:+.2f}R" if st["r_total"] is not None else "N/D"
        pf = "∞" if st["profit_factor"] == float("inf") else (f"{st['profit_factor']:.2f}" if st["profit_factor"] is not None else "N/D")
        dd = f"{st['max_drawdown_r']:.2f}R" if st["max_drawdown_r"] is not None else "N/D"
        groups.append(
            f"*{label}*: {st['total']} operaciones · {st['tp']} TP · {st['sl']} SL · "
            f"{st['horizonte']} horizonte · {st['pendientes']} pendientes\n"
            f"  Acierto: {wr} · R total: {total_r} · R medio: {avg}\n"
            f"  Profit factor: {pf} · drawdown máximo: {dd}"
        )
    recent = trades[-6:]
    recent_lines = []
    for t in recent:
        dt = datetime.fromtimestamp(t.entry_time / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        icon = "🟢" if t.side == "LONG" else "🔴"
        result = f"{t.r_multiple:+.2f}R" if t.r_multiple is not None else t.outcome
        recent_lines.append(f"{icon} `{dt}` {t.side} entrada `{t.entry:.6g}` · SL `{t.stop_loss:.6g}` · TP `{t.target:.6g}` · {result}")
    await update.message.reply_text(header + "\n\n".join(groups) + "\n\n_Últimas operaciones:_\n" + "\n".join(recent_lines), parse_mode="Markdown")
    try:
        workbook = build_reversion_workbook(
            symbol, interval, horizon, klines, trades, stats, risk_config,
            recovery_mode=recovery_mode,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Error generando Excel del backtest de reversión")
        await update.message.reply_text("⚠️ El backtest terminó, pero no se pudo generar el Excel.")
        return
    filename = f"backtest_reversion_{symbol}_{interval}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.xlsx"
    await update.message.reply_document(document=workbook, filename=filename,
        caption=f"📊 Reversión Bollinger + Estocástico ({variant_label}): {len(trades)} operaciones; {symbol} ({interval}).")


async def backtest_rsi_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Backtest RSI: giro en extremo, stop estructural y TP en extremo opuesto."""
    raw=list(context.args); params={"rsi_period":14,"oversold":30.0,"overbought":70.0,"pivot_left":2,"pivot_right":2,"stop_buffer_pct":0.0,"risk_usdt":10.0,"fee_pct":0.0}
    positional=[]; i=0
    try:
        while i<len(raw):
            tok=raw[i].lower()
            keys={"--rsi":"rsi_period","--oversold":"oversold","--overbought":"overbought","--pivot-left":"pivot_left","--pivot-right":"pivot_right","--stop-buffer":"stop_buffer_pct","--risk-usdt":"risk_usdt","--fee":"fee_pct"}
            if tok in keys:
                if i+1>=len(raw): raise ValueError(f"Falta el valor después de {raw[i]}")
                key=keys[tok]; val=float(raw[i+1]); params[key]=int(val) if key in ("rsi_period","pivot_left","pivot_right") else val; i+=2
            else: positional.append(raw[i]); i+=1
        request=parse_backtest_request(positional,normalize_symbol=_normalize_symbol,default_interval="4h",max_candles=BACKTEST_MAX_CANDLES)
        if params['risk_usdt']<=0 or params['rsi_period']<1 or params['pivot_left']<1 or params['pivot_right']<1: raise ValueError("Parámetros de riesgo o pivotes inválidos")
    except ValueError as exc:
        await update.message.reply_text("Uso: /backtest_rsi SIMBOLO INTERVALO VELAS [HORIZONTE] [--risk-usdt 10] [--rsi 14] [--oversold 30] [--overbought 70] [--pivot-left 2] [--pivot-right 2] [--stop-buffer 0] [--fee 0].\n"+str(exc)); return
    symbol,interval,horizon=request.symbol,request.interval,request.horizon
    if interval not in VALID_INTERVALS:
        await update.message.reply_text(f"Intervalo no válido: {interval}"); return
    await update.message.reply_text(f"Descargando histórico RSI de {symbol} ({interval})…")
    try:
        klines,signal_start,signal_end=load_backtest_klines(bitunix,request,warmup_candles=max(100,params['rsi_period']+20),followup_candles=100)
        closes=[float(k['close']) for k in klines]; highs=[float(k['high']) for k in klines]; lows=[float(k['low']) for k in klines]; opens=[float(k['open']) for k in klines]; times=[int(k['time']) for k in klines]; volumes=[float(k.get('volume',0) or 0) for k in klines]
        all_trades=run_rsi_backtest(closes,highs,lows,opens,times,horizon=horizon,volumes=volumes,**params)
        trades=filter_signals_to_period(all_trades,signal_start,signal_end); stats=summarize_rsi(trades)
    except Exception as exc:
        logger.exception("Error en backtest RSI"); await update.message.reply_text(f"Error en backtest RSI: {exc}"); return
    if not trades:
        await update.message.reply_text(f"RSI {symbol} {interval}: no se generaron operaciones válidas."); return
    st=stats['total']; wr=f"{st['win_rate']:.1f}%" if st['win_rate'] is not None else "N/D"; pnl=f"{st['pnl_usdt']:+.2f} USDT" if st['pnl_usdt'] is not None else "N/D"
    msg=(f"*Backtest RSI extremo — {symbol} · {interval}*\nRSI({params['rsi_period']}) Wilder/RMA · sobreventa {params['oversold']:g} · sobrecompra {params['overbought']:g}\n"
         f"Stop: pivote confirmado ({params['pivot_left']}/{params['pivot_right']}); si no existe, extremo desde entrada en zona. Riesgo fijo: {params['risk_usdt']:g} USDT/operación.\n"
         f"TP: cierre a mercado al alcanzar zona opuesta · Entrada: apertura siguiente · operaciones simultáneas permitidas.\n\n"
         f"Operaciones: {st['total']} · TP: {st['tp']} · SL: {st['sl']} · pendientes: {st['pendientes']}\nAcierto: {wr} · PnL neto: {pnl}")
    await update.message.reply_text(msg,parse_mode="Markdown")
    try:
        from xlsx_export_rsi import build_rsi_workbook
        wb=build_rsi_workbook(symbol,interval,horizon,klines,trades,stats,None,**params)
        await update.message.reply_document(document=wb,filename=f"backtest_rsi_{symbol}_{interval}_{datetime.now(timezone.utc):%Y%m%d_%H%M}.xlsx")
    except Exception:
        logger.exception("No se pudo exportar Excel RSI")


async def backtest_squeeze_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Backtest de compresión y ruptura Bollinger/Keltner."""
    try:
        args, risk_config, filter_window, filter_limit, band_ratio, channel_width_pct, only_outer_bands = _parse_backtest_args(context.args)
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return
    if filter_window is not None or band_ratio is not None or channel_width_pct is not None or only_outer_bands:
        await update.message.reply_text("⚠️ Los filtros genéricos no se aplican a /backtest_squeeze; la estrategia utiliza sus propias reglas.")
        return
    if not args:
        await update.message.reply_text(
            "Estrategia Squeeze: Bollinger (20, 2) + Keltner (EMA 20, ATR 20 × 1.5).\n\n"
            "Uso por cantidad: /backtest_squeeze BTCUSDT 1h 1000 [HORIZONTE]\n"
            "Uso por período: /backtest_squeeze BTCUSDT 4h 2025-01-01 2025-06-30 [HORIZONTE]\n\n"
            "Entrada: la vela anterior estaba comprimida (Bollinger dentro de Keltner), la actual libera la compresión, cierra fuera de ambas bandas y el momentum de 12 velas confirma la dirección.\n"
            "Entrada en apertura siguiente; stop en extremo de 10 velas; objetivo fijo 2R. Una posición simultánea.\n"
            "Fechas UTC. No incluye comisiones, funding ni deslizamiento. Riesgo opcional: --riesgo fijo 10 | --riesgo porcentaje 1 1000."
        )
        return
    try:
        request = parse_backtest_request(args, normalize_symbol=_normalize_symbol, default_interval="4h", max_candles=BACKTEST_MAX_CANDLES)
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return
    symbol, interval, horizon = request.symbol, request.interval, request.horizon
    if interval not in VALID_INTERVALS:
        await update.message.reply_text(f"Intervalo {interval} no válido. Usa uno de: {' '.join(sorted(VALID_INTERVALS, key=len))}")
        return
    period_text = (f"período {datetime.fromtimestamp(request.start_ms/1000, tz=timezone.utc):%Y-%m-%d} → {datetime.fromtimestamp(request.end_ms/1000, tz=timezone.utc):%Y-%m-%d} UTC" if request.is_date_range else f"hasta {request.candle_count} velas recientes")
    await update.message.reply_text(f"Descargando histórico de {symbol} ({interval}, {period_text}) y ejecutando Squeeze Bollinger + Keltner…")
    try:
        klines, signal_start, signal_end = load_backtest_klines(bitunix, request, warmup_candles=100, followup_candles=200)
    except Exception as exc:
        logger.exception("Error consultando histórico para backtest Squeeze")
        await update.message.reply_text(f"⚠️ Error consultando histórico de Bitunix: {exc}")
        return
    if len(klines) < 45:
        await update.message.reply_text(f"Solo hay {len(klines)} velas para {symbol} en {interval}; no es suficiente para el backtest.")
        return
    closes=[float(k["close"]) for k in klines]; highs=[float(k["high"]) for k in klines]
    lows=[float(k["low"]) for k in klines]; opens=[float(k["open"]) for k in klines]; times=[int(k["time"]) for k in klines]
    try:
        all_trades=run_squeeze_backtest(closes,highs,lows,opens,times,horizon=horizon)
        trades=filter_signals_to_period(all_trades,signal_start,signal_end); stats=summarize_squeeze(trades)
    except Exception as exc:
        logger.exception("Error calculando backtest Squeeze")
        await update.message.reply_text(f"⚠️ Error calculando el backtest Squeeze: {exc}")
        return
    first_date=datetime.fromtimestamp(times[0]/1000,tz=timezone.utc).strftime("%Y-%m-%d"); last_date=datetime.fromtimestamp(times[-1]/1000,tz=timezone.utc).strftime("%Y-%m-%d")
    period=(f"señales entre {datetime.fromtimestamp(signal_start/1000,tz=timezone.utc):%Y-%m-%d} → {datetime.fromtimestamp(signal_end/1000,tz=timezone.utc):%Y-%m-%d} UTC" if signal_start is not None else f"{len(klines)} velas ({first_date} → {last_date})")
    header=(f"*Backtest Squeeze Bollinger + Keltner — {symbol} · {interval}*\n" f"Gestión de riesgo: {risk_config.label}.\n{period}; velas cargadas: {len(klines)}; " + (f"horizonte: {horizon} velas.\n" if horizon is not None else "sin límite de duración por operación.\n") + "Entrada: apertura siguiente; stop swing 10 velas; objetivo fijo 2R.\nCostes de trading no incluidos.\n\n")
    if not trades:
        await update.message.reply_text(header + "No se generaron operaciones válidas. Puede ser normal si no hubo liberaciones de compresión que cumplieran todas las condiciones.")
        return
    groups=[]
    for label,key in (("TOTAL","total"),("LONG","long"),("SHORT","short")):
        st=stats[key]; wr=f"{st['win_rate']:.1f}%" if st['win_rate'] is not None else "N/D"; avg=f"{st['r_medio']:+.2f}R" if st['r_medio'] is not None else "N/D"; total_r=f"{st['r_total']:+.2f}R" if st['r_total'] is not None else "N/D"; pf="∞" if st['profit_factor']==float('inf') else (f"{st['profit_factor']:.2f}" if st['profit_factor'] is not None else "N/D"); dd=f"{st['max_drawdown_r']:.2f}R" if st['max_drawdown_r'] is not None else "N/D"
        groups.append(f"*{label}*: {st['total']} operaciones · {st['tp']} TP · {st['sl']} SL · {st['horizonte']} horizonte · {st['pendientes']} pendientes\nAcierto: {wr} · R total: {total_r} · R medio: {avg}\nProfit factor: {pf} · drawdown máximo: {dd}")
    recent=[]
    for t in trades[-6:]:
        dt=datetime.fromtimestamp(t.entry_time/1000,tz=timezone.utc).strftime("%Y-%m-%d %H:%M"); icon="🟢" if t.side=="LONG" else "🔴"; result=f"{t.r_multiple:+.2f}R" if t.r_multiple is not None else t.outcome
        recent.append(f"{icon} `{dt}` {t.side} entrada `{t.entry:.6g}` · SL `{t.stop_loss:.6g}` · TP `{t.target:.6g}` · {result}")
    await update.message.reply_text(header+"\n\n".join(groups)+"\n\n_Últimas operaciones:_\n"+"\n".join(recent),parse_mode="Markdown")
    try:
        workbook=build_squeeze_workbook(symbol,interval,horizon,klines,trades,stats,risk_config)
    except Exception:
        logger.exception("Error generando Excel Squeeze")
        await update.message.reply_text("⚠️ El backtest terminó, pero no se pudo generar el Excel.")
        return
    filename=f"backtest_squeeze_{symbol}_{interval}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.xlsx"
    await update.message.reply_document(document=workbook,filename=filename,caption=f"📊 Squeeze Bollinger + Keltner: {len(trades)} operaciones; {symbol} ({interval}).")


async def rsi_live_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/rsi_live add|remove|list SYMBOL TF [RISK_USDT]"""
    a=list(context.args)
    if not a or a[0].lower() in ('help','ayuda'):
        await update.message.reply_text("RSI Live (paper, sin órdenes reales):\n/rsi_live add BTCUSDT 15m [riesgo_USDT]\n/rsi_live remove BTCUSDT 15m\n/rsi_live list\n/rsi_status [BTCUSDT]"); return
    action=a[0].lower()
    if action=='list':
        rows=await asyncio.to_thread(RSI_LIVE_STORE.status)
        active=[r for r in rows if r['enabled']]
        await update.message.reply_text("RSI Live activo:\n"+("\n".join(f"{r['symbol']} · {r['interval']} · {json.loads(r['config']).get('risk_usdt',10):g} USDT riesgo" for r in active) if active else "Sin suscripciones")); return
    if action not in ('add','remove') or len(a)<3:
        await update.message.reply_text("Uso: /rsi_live add|remove SIMBOLO TEMPORALIDAD [RIESGO_USDT]"); return
    symbol=_normalize_symbol(a[1]); interval=a[2].lower()
    from rsi_live import TF_MS
    if interval not in TF_MS:
        await update.message.reply_text("Temporalidad admitida: "+", ".join(TF_MS)); return
    if action=='remove':
        await asyncio.to_thread(RSI_LIVE_STORE.unsubscribe,symbol,interval)
        await update.message.reply_text(f"RSI Live detenido para {symbol} {interval}. Las operaciones abiertas conservan su historial."); return
    try: risk=float(a[3]) if len(a)>3 else 10.0
    except ValueError: await update.message.reply_text("El riesgo debe ser un importe USDT válido."); return
    if risk<=0: await update.message.reply_text("El riesgo debe ser mayor que cero."); return
    cfg={'rsi_period':14,'oversold':30.0,'overbought':70.0,'pivot_left':2,'pivot_right':2,'stop_buffer_pct':0.0,'risk_usdt':risk}
    await asyncio.to_thread(RSI_LIVE_STORE.subscribe,symbol,interval,update.effective_chat.id,cfg)
    await update.message.reply_text(f"✅ RSI Live activado para {symbol} {interval}. Riesgo fijo {risk:g} USDT por operación. Solo paper trading; no se envían órdenes a Bitunix.")

async def rsi_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    symbol=_normalize_symbol(context.args[0]) if context.args else None
    rows=await asyncio.to_thread(RSI_LIVE_STORE.history,symbol)
    opened=[r for r in rows if r['status']=='OPEN']
    lines=["*RSI Live · operaciones*",f"Abiertas: {len(opened)}"]
    for t in opened[:20]: lines.append(f"{t['symbol']} {t['interval']} {t['side']} · entrada {t['entry_price']:.8g} · SL {t['stop_price']:.8g} · riesgo {t['risk_usdt']:.2f} USDT")
    await update.message.reply_text("\n".join(lines),parse_mode="Markdown")


async def ttr_live_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Suscribe, actualiza o detiene el paper trading TTR de un símbolo."""
    if not _is_authorized(update):
        await update.message.reply_text("No autorizado.")
        return
    args = list(context.args)
    if not args:
        await update.message.reply_text(
            "Uso:\n"
            '/ttr_live start BTCUSDT [--dir "Buy & Sell"|"Buy Only"|"Sell Only"] '
            "[--tp 40,30,30] [--re-entry true|false] [--feed websocket|rest] [--poll 5]\n"
            "/ttr_live stop BTCUSDT\n/ttr_live list\n"
            "Si vuelves a ejecutar start, solo se modifican los parámetros indicados; "
            "los demás conservan su valor. El estado y las operaciones abiertas se conservan.\n"
            "Solo alertas y simulación; nunca envía órdenes."
        )
        return

    action = args[0].lower()
    if action == "list":
        rows = TTR_LIVE_STORE.status()
        await update.message.reply_text(
            "Seguimientos TTR:\n" + (
                "\n".join(f"{r['symbol']}: {'ACTIVO' if r['enabled'] else 'DETENIDO'}" for r in rows)
                if rows else "Ninguno."
            )
        )
        return
    if len(args) < 2:
        await update.message.reply_text("Indica el símbolo.")
        return

    symbol = _normalize_symbol(args[1])
    if action == "stop":
        await asyncio.to_thread(TTR_LIVE_STORE.unsubscribe, symbol)
        feed = context.application.bot_data.get("ttr_ws_feed")
        if feed is not None:
            try:
                await feed.stop_symbol(symbol)
            except Exception:
                logger.exception("No se pudo cerrar WebSocket TTR de %s", symbol)
        context.application.bot_data.setdefault("ttr_reconciled_symbols", set()).discard(symbol)
        await update.message.reply_text(
            f"⏹ Seguimiento TTR detenido para {symbol}. "
            "Se cierra su WebSocket; el historial y las posiciones registradas se conservan."
        )
        return
    if action != "start":
        await update.message.reply_text("Acción inválida: usa start, stop o list.")
        return

    # Start is also an in-place configuration update. Omitted options keep their
    # previous values, avoiding accidental resets when changing one parameter.
    existing = await asyncio.to_thread(TTR_LIVE_STORE.get_config, symbol)
    try:
        previous = json.loads(existing.get("config") or "{}") if existing else {}
    except (TypeError, ValueError):
        previous = {}
    defaults = {
        "interval": "15m", "monitor_interval": "5s", "poll_seconds": 5,
        "feed_mode": "websocket", "pos_dir": "Buy & Sell",
        "tp_percentages": [25, 25, 25, 25], "enter_on_reentry": False,
        "psar_start": .02, "psar_increment": .02, "psar_max": .025,
        "paper_only": True,
    }
    config = {**defaults, **previous}
    raw_opts = {"--dir": None, "--tp": None, "--re-entry": None, "--poll": None, "--feed": None}
    i = 2
    while i < len(args):
        key = args[i]
        if key not in raw_opts or i + 1 >= len(args):
            await update.message.reply_text(f"Parámetro inválido o sin valor: {key}")
            return
        if key == "--dir":
            # Telegram splits command arguments on spaces; accept both
            # --dir "Buy & Sell" and --dir Buy & Sell.
            values = []
            i += 1
            while i < len(args) and not args[i].startswith("--"):
                values.append(args[i].strip('"\''))
                i += 1
            raw_opts[key] = " ".join(values)
            if not raw_opts[key]:
                await update.message.reply_text('Falta el valor de --dir. Ejemplo: --dir "Buy & Sell".')
                return
            continue
        raw_opts[key] = args[i + 1].strip('"\'')
        i += 2

    direction = raw_opts["--dir"]
    if direction is not None:
        direction = {"Both": "Buy & Sell", "BuyOnly": "Buy Only", "SellOnly": "Sell Only"}.get(direction, direction)
        if direction not in ("Buy & Sell", "Buy Only", "Sell Only"):
            await update.message.reply_text('Dirección inválida. Usa --dir "Buy & Sell", "Buy Only" o "Sell Only".')
            return
        config["pos_dir"] = direction

    if raw_opts["--re-entry"] is not None:
        value = raw_opts["--re-entry"].lower()
        if value not in ("true", "false"):
            await update.message.reply_text("--re-entry admite true o false.")
            return
        config["enter_on_reentry"] = value == "true"

    if raw_opts["--tp"] is not None:
        try:
            tps = [float(x.strip()) for x in raw_opts["--tp"].split(",") if x.strip()]
            if not tps or any(x <= 0 or x > 100 for x in tps) or sum(tps) > 100:
                raise ValueError
            config["tp_percentages"] = tps
        except (TypeError, ValueError):
            await update.message.reply_text("TP inválido; ejemplo: --tp 40,30,30 (suma <=100).")
            return

    if raw_opts["--feed"] is not None:
        feed_mode = raw_opts["--feed"].lower()
        if feed_mode not in ("websocket", "rest"):
            await update.message.reply_text("--feed debe ser websocket o rest.")
            return
        config["feed_mode"] = feed_mode

    if raw_opts["--poll"] is not None:
        try:
            poll = int(raw_opts["--poll"])
            if not 2 <= poll <= 30:
                raise ValueError
            config["poll_seconds"] = poll
        except (TypeError, ValueError):
            await update.message.reply_text("--poll debe estar entre 2 y 30 segundos.")
            return

    config["monitor_interval"] = f"{int(config.get('poll_seconds', 5))}s"
    config["paper_only"] = True
    await asyncio.to_thread(TTR_LIVE_STORE.subscribe, symbol, update.effective_chat.id, config)
    context.application.bot_data.setdefault("ttr_reconciled_symbols", set()).discard(symbol)

    # If feed mode changed to REST, stop the now-unneeded socket. If it changed
    # back to WebSocket, the next supervisor tick will ensure a fresh connection.
    feed = context.application.bot_data.get("ttr_ws_feed")
    if feed is not None:
        if config["feed_mode"] == "rest":
            await feed.stop_symbol(symbol)
        else:
            feed.resume_symbol(symbol)

    confirmation = (
        f"✅ TTR paper trading {'actualizado' if existing else 'activado'} para {symbol}.\n"
        f"Indicador: 15m cerrado · feed: {config['feed_mode'].upper()} "
        f"(REST de respaldo; sondeo {config['poll_seconds']}s).\n"
        f"Dirección: {config['pos_dir']} · RE: {'activado' if config['enter_on_reentry'] else 'desactivado'} "
        f"· TP contables: {config['tp_percentages']}.\n"
        "Se conservan el estado y las operaciones abiertas. No se enviarán órdenes reales."
    )
    try:
        await update.message.reply_text(confirmation)
    except Exception:
        logger.warning("TTR %s guardado, pero falló el envío de confirmación Telegram", symbol, exc_info=True)


async def ttr_stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Detiene TTR Live para un símbolo sin borrar historial ni posiciones."""
    if not _is_authorized(update):
        await update.message.reply_text("No autorizado.")
        return
    if not context.args:
        await update.message.reply_text("Uso: /ttr_stop BTCUSDT")
        return

    symbol = _normalize_symbol(context.args[0])
    await asyncio.to_thread(TTR_LIVE_STORE.unsubscribe, symbol)
    feed = context.application.bot_data.get("ttr_ws_feed")
    if feed is not None:
        try:
            await feed.stop_symbol(symbol)
        except Exception:
            logger.exception("No se pudo cerrar WebSocket TTR de %s", symbol)
    context.application.bot_data.setdefault("ttr_reconciled_symbols", set()).discard(symbol)
    await update.message.reply_text(
        f"⏹ Seguimiento TTR detenido para {symbol}. "
        "Se cierra su WebSocket; el historial y las posiciones registradas se conservan."
    )


async def ttr_restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Destructive reset of TTR Live only; requires an explicit confirmation token."""
    if not _is_authorized(update):
        await update.message.reply_text("No autorizado.")
        return
    if len(context.args) != 1 or context.args[0].upper() != "CONFIRMAR":
        await update.message.reply_text(
            "⚠️ Este comando elimina TODAS las suscripciones TTR, estados, operaciones abiertas y cerradas, "
            "eventos e históricos, además de los registros de salud. No afecta a otras estrategias ni a órdenes "
            "de Bitunix.\n\nPara confirmar el borrado irreversible, ejecuta: /ttr_restart CONFIRMAR"
        )
        return

    # Close each symbol socket without globally killing the feed object, so a
    # later /ttr_live start can resume new subscriptions in this same process.
    feed = context.application.bot_data.get("ttr_ws_feed")
    symbols = [row["symbol"] for row in await asyncio.to_thread(TTR_LIVE_STORE.status)]
    if feed is not None:
        for symbol in symbols:
            try:
                await feed.stop_symbol(symbol)
            except Exception:
                logger.exception("No se pudo detener WebSocket TTR de %s durante el reinicio", symbol)

    await asyncio.to_thread(TTR_LIVE_STORE.reset_all)
    data = context.application.bot_data
    data["ttr_reconciled_symbols"] = set()
    data["ttr_health_cache"] = {}
    data.pop("ttr_supervisor_last_heartbeat", None)
    await update.message.reply_text(
        "♻️ TTR Live reiniciado desde cero. Se eliminaron suscripciones, estados, posiciones, "
        "operaciones e históricos, eventos y registros de salud. No se modificó ninguna otra estrategia "
        "ni se enviaron órdenes. Puedes volver a suscribir símbolos con /ttr_live start BTCUSDT."
    )


async def ttr_notify_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Configure independent per-symbol Telegram event notifications."""
    if not _is_authorized(update):
        await update.message.reply_text("No autorizado."); return
    args=list(context.args)
    if not args:
        await update.message.reply_text(
            "Uso: /ttr_notify BTCUSDT [open|close|tp|stop] [on|off]\n"
            "Ejemplo: /ttr_notify BTCUSDT tp off\n"
            "Sin categoría muestra las preferencias actuales."); return
    symbol=_normalize_symbol(args[0])
    row=TTR_LIVE_STORE.get_config(symbol)
    if not row:
        await update.message.reply_text(f"TTR no está configurado para {symbol}."); return
    labels={'open':'notify_open','close':'notify_close','tp':'notify_tp','stop':'notify_stop'}
    cfg=json.loads(row.get('config') or '{}')
    if len(args)==1:
        lines=[f"🔔 Notificaciones TTR · {symbol}"]
        for key,label in [('open','Apertura'),('close','Cierre'),('tp','Take profit'),('stop','Actualización de stop')]:
            lines.append(f"{label}: {'ACTIVADA' if cfg.get(labels[key],True) else 'DESACTIVADA'}")
        lines.append("Configúralas por separado con /ttr_notify SIMBOLO categoría on|off.")
        await update.message.reply_text("\n".join(lines)); return
    if len(args)!=3 or args[1].lower() not in labels or args[2].lower() not in ('on','off'):
        await update.message.reply_text("Parámetros inválidos. Categorías: open, close, tp, stop; valores: on, off."); return
    category=args[1].lower(); enabled=args[2].lower()=='on'
    await asyncio.to_thread(TTR_LIVE_STORE.set_notification,symbol,category,enabled)
    await update.message.reply_text(f"🔔 Notificación {category} para {symbol}: {'ACTIVADA' if enabled else 'DESACTIVADA'}.")


async def ttr_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await update.message.reply_text("No autorizado."); return
    rows=[r for r in TTR_LIVE_STORE.status() if r['enabled']]
    if not rows:
        await update.message.reply_text("No hay seguimientos TTR activos."); return
    lines=["📡 *Seguimientos TTR activos*", ""]
    for r in rows:
        state=json.loads(r.get('state') or '{}'); pos=state.get('position'); market=state.get('market') or {}; ind=state.get('indicator') or {}; cfg=json.loads(r.get('config') or '{}')
        lines.append(f"*{r['symbol']}* · {'🟢 '+pos['side'].upper() if pos else '⚪ SIN POSICIÓN'}")
        feed=context.application.bot_data.get('ttr_ws_feed'); health=feed.health(r['symbol']) if feed else (market.get('feed_health') or {}); lines.append(f"Precio actual: `{market.get('last_price','N/D')}` · fuente: `{market.get('source','N/D')}` · REST: `{market.get('poll_seconds',cfg.get('poll_seconds',5))}s`")
        lines.append(f"WebSocket: `{'CONECTADO' if health.get('connected') else 'DESCONECTADO'}` · datos recientes: `{health.get('fresh',False)}` · antigüedad: `{health.get('last_message_age_seconds','N/D')}s` · modo: `{cfg.get('feed_mode','websocket')}`")
        lines.append(f"Indicador: `15m` · PSAR: `{ind.get('psar','N/D')}` · Lagging PSAR: `{ind.get('psar_lagging','N/D')}`")
        lines.append(f"Última vela cerrada: `{datetime.fromtimestamp(ind['candle_time']/1000,timezone.utc):%Y-%m-%d %H:%M UTC}`" if ind.get('candle_time') else "Última vela cerrada: `calculando` ")
        if pos:
            lines.append(f"Entrada: `{pos.get('entry_price','N/D')}` · stop: `no definido por Pine` · restante: `{float(pos.get('remaining_pct',100)):.2f}% · PnL realizado: {float(pos.get('realized_pnl_pct',0)):+.4f}%`")
        lines.append(f"Dirección: `{cfg.get('pos_dir','Buy & Sell')}` · RE: `solo señal Pine; no añade exposición` · TP: `{','.join(map(str,cfg.get('tp_percentages',[])))}`")
        lines.append("")
    await update.message.reply_text("\n".join(lines),parse_mode="Markdown")


async def ttr_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update): await update.message.reply_text("No autorizado."); return
    symbol=_normalize_symbol(context.args[0]) if context.args else None
    rows=TTR_LIVE_STORE.history(symbol,30) if symbol else TTR_LIVE_STORE.all_history(50)
    if not rows:
        await update.message.reply_text(f"Sin operaciones TTR registradas{(' para '+symbol) if symbol else ''}."); return
    title=f"Historial TTR · {symbol}" if symbol else "Historial global TTR · últimos 50 registros"
    lines=[]
    for r in rows:
        entry=datetime.fromtimestamp(r['entry_time']/1000,timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        status='🟢 ABIERTA' if r['status']=='OPEN' else f"CERRADA ({r.get('exit_reason') or 'N/D'})"
        exit_info=(f" · salida {float(r['exit_price']):.8g}" if r.get('exit_price') is not None else '')
        lines.append(f"{r['symbol']} #{r['id']} · {r['side'].upper()} · {entry} · entrada {float(r['entry_price']):.8g}{exit_info} · {status} · PnL realizado {float(r.get('realized_pnl_pct') or 0):+.4f}%")
    # Telegram limits a message to 4096 chars; send compact pages.
    page=title+'\n'+'\n'.join(lines)
    for offset in range(0,len(page),3800): await update.message.reply_text(page[offset:offset+3800])


async def ttr_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update): await update.message.reply_text("No autorizado."); return
    data=await asyncio.to_thread(TTR_LIVE_STORE.performance)
    overall=data['overall']
    def fmt(label,m):
        pf='∞' if m['profit_factor']==float('inf') else f"{m['profit_factor']:.2f}"
        return (f"*{label}* · {m['total']} ops ({m['closed']} cerradas / {m['open']} abiertas)\n"
            f"Acierto: {m['win_rate']:.1f}% ({m['wins']}G/{m['losses']}P) · PF: {pf}\n"
            f"PnL realizado acumulado: {m['realized_pnl_pct']:+.4f}% · media/cerrada: {m['avg_pnl_pct']:+.4f}%\n"
            f"Mejor: {m['best_pct']:+.4f}% · peor: {m['worst_pct']:+.4f}%\n"
            f"PnL flotante estimado: {m['floating_pnl_pct']:+.4f}% ({m['open_marked']}/{m['open']} abiertas con precio)\nPnL neto realizado + flotante: {m['net_pnl_pct']:+.4f}%")
    lines=['📊 *Rendimiento TTR Live · acumulado*','',fmt('TOTAL GENERAL',overall),'','*DESGLOSE POR SÍMBOLO*']
    if data['by_symbol']:
        for symbol,m in data['by_symbol'].items(): lines.extend(['',fmt(symbol,m)])
    else: lines.append('Todavía no hay operaciones registradas.')
    lines.extend(['','_PnL porcentual ponderado por el porcentaje de posición cerrado. El flotante es una estimación sobre el último precio guardado; no incluye comisiones ni financiación._'])
    message='\n'.join(lines)
    for offset in range(0,len(message),3800): await update.message.reply_text(message[offset:offset+3800],parse_mode='Markdown')


async def ttr_health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update): await update.message.reply_text("No autorizado."); return
    try: rows=await asyncio.to_thread(TTR_LIVE_STORE.health)
    except Exception as exc:
        await update.message.reply_text(f"🔴 PostgreSQL/SQLite no disponible: {str(exc)[:250]}"); return
    heartbeat=context.application.bot_data.get('ttr_supervisor_last_heartbeat')
    age=(time.time()-heartbeat) if heartbeat else None
    lines=['🩺 *Supervisión TTR Live*',f"Supervisor: {'🟢 activo' if age is not None and age<15 else '🔴 sin heartbeat reciente'}"+(f" · hace {age:.1f}s" if age is not None else ''),'']
    if not rows: lines.append('Sin incidentes registrados.')
    for r in rows:
        icon='🟢' if r['status']=='OK' else ('🟠' if r['status'] in ('RATE_LIMITED','DEGRADED') else '🔴')
        lines.append(f"{icon} *{r['component']}* · {r['category']} · {r['status']} · ocurrencias {r['occurrences']}")
        if r.get('message'): lines.append(f"  {str(r['message'])[:220]}")
    lines.extend(['','Render: el estado de cuota/horas gratuitas no es consultable desde el proceso del bot. Revísalo en el dashboard de Render; si el servicio está suspendido, el heartbeat dejará de actualizarse.'])
    await update.message.reply_text('\n'.join(lines))


async def backtest_ttr_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Backtest TTR con port de Pine v6, TP parciales contables y reversiones."""
    raw=list(context.args)
    if not raw:
        await update.message.reply_text("Uso: /backtest_ttr BTCUSDT 1h 1000 [--re-entry true|false] [--tp 25,25,25,25] [--dir Buy & Sell|Buy Only|Sell Only] [--fee 0.05] [--slippage 0.02]\nLos TP son porcentajes de la posición restante por cada señal TP; la suma debe ser <=100. La señal de cruce opuesto invierte la dirección; TTR no define stop-loss.")
        return
    opts={"--re-entry":"false","--tp":"25,25,25,25","--dir":"Buy & Sell","--fee":"0","--slippage":"0"}; positional=[]; i=0
    try:
        while i<len(raw):
            if raw[i] in opts:
                if i+1>=len(raw): raise ValueError(f"Falta valor para {raw[i]}")
                opts[raw[i]]=raw[i+1]; i+=2
            else: positional.append(raw[i]); i+=1
        request=parse_backtest_request(positional,normalize_symbol=_normalize_symbol,default_interval="1h",max_candles=BACKTEST_MAX_CANDLES)
        if request.interval not in VALID_INTERVALS: raise ValueError("Intervalo no válido.")
        tp=[float(x.strip()) for x in opts["--tp"].split(",") if x.strip()]
        if not tp or sum(tp)>100 or any(x<=0 or x>100 for x in tp): raise ValueError("--tp debe ser una lista, p. ej. 40,30,30; suma máxima 100.")
        if opts["--re-entry"].lower() not in ("true","false"): raise ValueError("--re-entry admite true o false.")
        direction_alias={"BuyOnly":"Buy Only","SellOnly":"Sell Only","Both":"Buy & Sell"}
        opts["--dir"]=direction_alias.get(opts["--dir"],opts["--dir"])
        fee=float(opts["--fee"]); slip=float(opts["--slippage"])
    except (ValueError,TypeError) as exc:
        await update.message.reply_text(f"⚠️ {exc}"); return
    await update.message.reply_text(f"Descargando histórico de {request.symbol} ({request.interval}) y ejecutando TTR…")
    try:
        klines, signal_start, signal_end=load_backtest_klines(bitunix,request,warmup_candles=100,followup_candles=200)
        if len(klines)<40: raise ValueError(f"Solo se encontraron {len(klines)} velas.")
        result=run_ttr_backtest([float(k["close"]) for k in klines],[float(k["high"]) for k in klines],[float(k["low"]) for k in klines],[float(k["open"]) for k in klines],[int(k["time"]) for k in klines],
            pos_dir=opts["--dir"],enter_on_reentry=opts["--re-entry"].lower()=="true",tp_percentages=tp,fee_pct=fee,slippage_pct=slip)
        trades=[t for t in result["trades"] if signal_start<=t["entry_time"]<=signal_end]
        # Recalcular métricas con el mismo subconjunto de operaciones que se muestra/exporta.
        wins=[t for t in trades if float(t.get('pnl_pct') or 0)>0]
        losses=[t for t in trades if float(t.get('pnl_pct') or 0)<0]
        gross_win=sum(float(t['pnl_pct']) for t in wins)
        gross_loss=abs(sum(float(t['pnl_pct']) for t in losses))
        equity=100.0
        for t in sorted(trades,key=lambda x:x.get('exit_time') or x.get('entry_time') or 0):
            equity*=max(0.0,1.0+float(t.get('pnl_pct') or 0)/100.0)
        win_rate=len(wins)/len(trades)*100 if trades else 0.0
        pf=gross_win/gross_loss if gross_loss else None
        result_for_export={**result,'summary':{**result['summary'],'trades':len(trades),'wins':len(wins),'losses':len(losses),'win_rate_pct':win_rate,'net_pnl_pct_compounded':(equity/100.0-1)*100,'profit_factor':pf},'events':[e for e in result.get('events',[]) if signal_start<=e.get('time',0)<=signal_end]}
        msg=(f"📊 TTR · {request.symbol} · {request.interval}\nOperaciones: {len(trades)} · Acierto: {win_rate:.2f}%\n"
             f"PnL compuesto: {(equity/100.0-1)*100:+.4f}% · Profit factor: {pf if pf is not None else 'N/D'}\n"
             f"Dirección: {opts['--dir']} · RE auto-add (no Pine): desactivado · TP: {','.join(map(str,tp))}%\nSin stop-loss: el Pine original no contiene stop ni trailing stop.\n"
             "El PnL de cada operación incluye los cierres parciales TP realizados antes de una reversión o fin de datos. El Excel adjunto desglosa cada cierre parcial.\n"
             "Port Python de Tac.sar y Tac.BWFractals según los fuentes Pine entregados; aún requiere comparación de señales vela por vela con TradingView.")
        await update.message.reply_text(msg)
        if trades:
            lines=[]
            for t in trades[-8:]:
                pieces=t.get('partial_exits') or []
                tp_pnl=sum(float(x.get('pnl_pct_position') or 0) for x in pieces if x.get('reason')=='TP')
                lines.append(f"{t['side'].upper()} {datetime.fromtimestamp(t['entry_time']/1000,tz=timezone.utc):%Y-%m-%d %H:%M} · entrada {t['entry_price']:.6g} · salida {t['exit_price']:.6g} · {t['exit_reason']} · TP acumulado {tp_pnl:+.4f}% · PnL total {t['pnl_pct']:+.4f}%")
            await update.message.reply_text("Últimas operaciones (PnL con 4 decimales):\n"+"\n".join(lines))
        try:
            workbook=build_ttr_workbook(request.symbol,request.interval,klines,result_for_export,trades,result.get('parameters',{}))
            filename=f"backtest_ttr_{request.symbol}_{request.interval}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.xlsx"
            await update.message.reply_document(document=workbook,filename=filename,caption=f"📊 Excel TTR: {len(trades)} operaciones, cierres TP parciales, reversiones, señales y curva de capital.")
        except Exception:
            logger.exception("Error generando/enviando Excel TTR")
            await update.message.reply_text("⚠️ El resumen se calculó, pero no pude generar o enviar el Excel TTR.")
    except Exception as exc:
        logger.exception("Error en backtest TTR")
        await update.message.reply_text(f"⚠️ No se pudo ejecutar TTR: {exc}")


async def backtest_dual_thrust_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Backtest Dual Thrust con ruptura intrabar y reversión ante señal opuesta."""
    raw = list(context.args)
    lookback, k1, k2 = 4, 0.6, 0.7
    fee_bps, slippage_bps = 0.0, 0.0
    execution_interval = None
    positional = []
    i = 0
    try:
        while i < len(raw):
            token = raw[i].lower()
            if token in ("--n", "--k1", "--k2", "--fee-bps", "--slippage-bps", "--subintervalo"):
                if i + 1 >= len(raw):
                    raise ValueError(f"Falta el valor de {token}.")
                value = raw[i + 1]
                if token == "--n": lookback = int(value)
                elif token == "--k1": k1 = float(value)
                elif token == "--k2": k2 = float(value)
                elif token == "--fee-bps": fee_bps = float(value)
                elif token == "--slippage-bps": slippage_bps = float(value)
                else: execution_interval = value
                i += 2
            else:
                positional.append(raw[i]); i += 1
        if "--riesgo" in [x.lower() for x in positional]:
            raise ValueError("La variante base de Dual Thrust no define stop-loss; por eso no admite --riesgo para dimensionar posiciones. Se informa PnL porcentual.")
        args, risk_config, filter_window, filter_limit, band_ratio, channel_width_pct, only_outer_bands = _parse_backtest_args(positional)
        if filter_window is not None or band_ratio is not None or channel_width_pct is not None or only_outer_bands:
            raise ValueError("Dual Thrust no utiliza los filtros genéricos de otras estrategias.")
        if lookback < 2 or lookback > 10:
            raise ValueError("La ventana --n debe estar entre 2 y 10 según la investigación.")
        if fee_bps < 0 or slippage_bps < 0:
            raise ValueError("--fee-bps y --slippage-bps deben ser valores no negativos.")
        if not 0.3 <= k1 <= 1.8 or not 0.3 <= k2 <= 1.8:
            raise ValueError("K1 y K2 deben estar entre 0.3 y 1.8 según el rango de investigación.")
    except (ValueError, TypeError) as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return
    if not args:
        await update.message.reply_text(
            "Dual Thrust Breakout (según la investigación).\n\n"
            "Uso por cantidad: /backtest_dual_thrust BTCUSDT 4h 1000 [HORIZONTE] [--subintervalo 1h] [--n 4] [--k1 0.6] [--k2 0.7] [--fee-bps 5] [--slippage-bps 2]\n"
            "Uso por fechas: /backtest_dual_thrust BTCUSDT 4h 2025-01-01 2025-06-30 [HORIZONTE] [--subintervalo 1h] [--n 4] [--k1 0.6] [--k2 0.7] [--fee-bps 5] [--slippage-bps 2]\n\n"
            "Range = max(HH−LC, HC−LL), calculado con las N velas anteriores. BuyTrack = Open + K1×Range; SellTrack = Open − K2×Range. "
            "Rupturas intrabar; una ruptura contraria cierra e invierte la posición.\n"
            "Valores iniciales: N=4, K1=0.6, K2=0.7; son valores de partida dentro de los rangos investigados, no parámetros óptimos demostrados.\n"
            "--subintervalo permite ejecutar con velas inferiores (p. ej. 4h --subintervalo 1h); las reglas y niveles siguen calculándose en la temporalidad objetivo. Si ambos niveles se tocan dentro de una misma subvela, esa subvela continúa siendo ambigua. "
            "--fee-bps y --slippage-bps se aplican por lado; por defecto son 0 porque la tarifa real depende de tu cuenta. No incluye financiación. El stop ATR de 1.5×ATR(14) queda desactivado en la variante base."
        )
        return
    try:
        request = parse_backtest_request(args, normalize_symbol=_normalize_symbol, default_interval="4h", max_candles=BACKTEST_MAX_CANDLES)
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return
    symbol, interval, horizon = request.symbol, request.interval, request.horizon
    if interval not in VALID_INTERVALS:
        await update.message.reply_text(f"Intervalo {interval} no válido. Usa uno de: {' '.join(sorted(VALID_INTERVALS, key=len))}")
        return
    if execution_interval is not None:
        if execution_interval not in VALID_INTERVALS:
            await update.message.reply_text(f"Subintervalo {execution_interval} no válido. Usa uno de: {' '.join(sorted(VALID_INTERVALS, key=len))}")
            return
        try:
            validate_timeframes(interval, execution_interval)
        except ValueError as exc:
            await update.message.reply_text(f"⚠️ {exc}")
            return
    period_text = (f"período {datetime.fromtimestamp(request.start_ms/1000, tz=timezone.utc):%Y-%m-%d} → {datetime.fromtimestamp(request.end_ms/1000, tz=timezone.utc):%Y-%m-%d} UTC" if request.is_date_range else f"hasta {request.candle_count} velas recientes")
    execution_text = f", ejecución intrabar con {execution_interval}" if execution_interval else ""
    await update.message.reply_text(f"Descargando histórico de {symbol} ({interval}{execution_text}, {period_text}) y ejecutando Dual Thrust…")
    try:
        if execution_interval:
            klines, execution_klines, signal_start, signal_end = load_multitimeframe_candles(
                bitunix, request, execution_interval, lookback=lookback,
                warmup_candles=max(lookback + 5, 20), followup_candles=200)
        else:
            klines, signal_start, signal_end = load_backtest_klines(bitunix, request, warmup_candles=max(lookback + 5, 20), followup_candles=200)
            execution_klines = None
    except Exception as exc:
        logger.exception("Error consultando histórico para Dual Thrust")
        await update.message.reply_text(f"⚠️ Error consultando histórico de Bitunix: {exc}")
        return
    if len(klines) < lookback + 2:
        await update.message.reply_text(f"Solo hay {len(klines)} velas objetivo completas para {symbol} en {interval}; no es suficiente para Dual Thrust.")
        return
    closes=[float(k["close"]) for k in klines]; highs=[float(k["high"]) for k in klines]
    lows=[float(k["low"]) for k in klines]; opens=[float(k["open"]) for k in klines]; times=[int(k["time"]) for k in klines]
    try:
        if execution_klines is not None:
            all_trades = run_backtest_multitimeframe(klines, execution_klines, interval, execution_interval,
                horizon=horizon, lookback=lookback, k1=k1, k2=k2, fee_bps=fee_bps, slippage_bps=slippage_bps)
        else:
            all_trades = run_dual_thrust_backtest(closes, highs, lows, opens, times, horizon=horizon, lookback=lookback, k1=k1, k2=k2, fee_bps=fee_bps, slippage_bps=slippage_bps)
        trades = filter_signals_to_period(all_trades, signal_start, signal_end)
        stats = summarize_dual_thrust(trades)
    except Exception as exc:
        logger.exception("Error calculando backtest Dual Thrust")
        await update.message.reply_text(f"⚠️ Error calculando el backtest Dual Thrust: {exc}")
        return
    first_date=datetime.fromtimestamp(times[0]/1000,tz=timezone.utc).strftime("%Y-%m-%d"); last_date=datetime.fromtimestamp(times[-1]/1000,tz=timezone.utc).strftime("%Y-%m-%d")
    period=(f"señales entre {datetime.fromtimestamp(signal_start/1000,tz=timezone.utc):%Y-%m-%d} → {datetime.fromtimestamp(signal_end/1000,tz=timezone.utc):%Y-%m-%d} UTC" if signal_start is not None else f"{len(klines)} velas ({first_date} → {last_date})")
    header=(f"*Backtest Dual Thrust Breakout — {symbol} · {interval}*\n" f"Parámetros: N={lookback}, K1={k1:g}, K2={k2:g}.\n{period}; velas cargadas: {len(klines)}; " + (f"horizonte externo: {horizon} velas.\n" if horizon is not None else "sin horizonte máximo.\n") + f"Entrada por ruptura intrabar; reversión ante ruptura opuesta.\n" + (f"Resolución de ejecución: subvelas {execution_interval}; niveles calculados en {interval}. Si ambos extremos se tocan en una misma subvela, sigue siendo ambigua.\n" if execution_interval else "Resolución de ejecución: OHLC de la vela objetivo (modo base).\n") + f"Comisión: {fee_bps:g} bps/lado; deslizamiento: {slippage_bps:g} bps/lado; financiación no incluida.\nSin stop-loss en la variante base.\n\n")
    groups=[]
    for label,key in (("TOTAL","total"),("LONG","long"),("SHORT","short")):
        st=stats[key]; wr=f"{st['win_rate']:.1f}%" if st['win_rate'] is not None else "N/D"; pnl=f"{st['pnl_pct_total']:+.2f}%" if st['pnl_pct_total'] is not None else "N/D"; avg=f"{st['pnl_pct_medio']:+.2f}%" if st['pnl_pct_medio'] is not None else "N/D"; pf="∞" if st['profit_factor']==float('inf') else (f"{st['profit_factor']:.2f}" if st['profit_factor'] is not None else "N/D"); dd=f"{st['max_drawdown_pct']:.2f}%" if st['max_drawdown_pct'] is not None else "N/D"
        comp=f"{st['pnl_pct_compuesto']:+.2f}%" if st['pnl_pct_compuesto'] is not None else "N/D"; costs=f"{st['costes_pct']:.2f}%" if st['costes_pct'] is not None else "N/D"; slipcost=f"{st['deslizamiento_pct']:.2f}%" if st['deslizamiento_pct'] is not None else "N/D"
        groups.append(f"*{label}*: {st['total']} operaciones · {st['reversiones']} reversiones · {st['horizonte']} horizonte · {st['pendientes']} pendientes\nAcierto: {wr} · PnL neto sumado: {pnl} · compuesto: {comp} · comisiones: {costs} · deslizamiento estimado: {slipcost}\nPromedio: {avg} · Profit factor: {pf} · drawdown máx. sobre capital: {dd}")
    recent=[]
    for t in trades[-6:]:
        dt = datetime.fromtimestamp(t.entry_time / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        icon = "🟢" if t.side == "LONG" else "🔴"
        if t.exit_price is None:
            exit_text = "— (posición abierta)"
        else:
            exit_text = f"`{t.exit_price:.6g}`"
        result = f"{t.pnl_pct:+.2f}%" if t.pnl_pct is not None else t.exit_reason
        recent.append(f"{icon} `{dt}` {t.side} entrada `{t.entry:.6g}` · salida {exit_text} · {result}")
    await update.message.reply_text(header+"\n\n".join(groups)+("\n\n_Últimas operaciones:_\n"+"\n".join(recent) if recent else "\n\nNo se generaron operaciones."),parse_mode="Markdown")
    try:
        workbook=build_dual_thrust_workbook(symbol,interval,horizon,klines,trades,stats,lookback,k1,k2,fee_bps=fee_bps,slippage_bps=slippage_bps, equity_curve=build_dual_thrust_equity_curve(trades), execution_interval=execution_interval)
    except Exception:
        logger.exception("Error generando Excel Dual Thrust")
        await update.message.reply_text("⚠️ El backtest terminó, pero no se pudo generar el Excel.")
        return
    execution_suffix=f"_exec{execution_interval}" if execution_interval else ""
    filename=f"backtest_dual_thrust_{symbol}_{interval}{execution_suffix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.xlsx"
    await update.message.reply_document(document=workbook,filename=filename,caption=f"📊 Dual Thrust: {len(trades)} operaciones; {symbol} ({interval}{f' / ejecución {execution_interval}' if execution_interval else ''}).")


async def backtest_keltner_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ejecuta Keltner usando la misma capa genérica de selección de período."""
    try:
        args, risk_config, filter_window, filter_limit, band_ratio, channel_width_pct, only_outer_bands = _parse_backtest_args(context.args)
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return
    if not args:
        await update.message.reply_text(
            "Uso por cantidad: /backtest_keltner BTCUSDT 4h 500 [HORIZONTE]\n"
            "Uso por período: /backtest_keltner BTCUSDT 4h 2025-01-01 2025-06-30 [HORIZONTE]\n\n"
            "Las fechas son UTC e incluyen ambos días. Se cargan velas previas/posteriores para evitar truncar indicadores y operaciones.\n"
            "El Excel incluye resumen, señales, velas, curva de ratios y gestión de riesgo.\n"
            "Riesgo opcional: --riesgo fijo 10 | --riesgo porcentaje 1 1000.\n"
            "Filtros opcionales: --filtro-rango 10 3; --filtro-banda 2 (espacio > 2R hasta banda contraria); --filtro-anchura 1.5 (canal mínimo del 1.5% del precio); --solo-bandas-limite (LONG solo cruza banda inferior y SHORT solo cruza superior). Todos desactivados por defecto.",
        )
        return
    try:
        request = parse_backtest_request(args, normalize_symbol=_normalize_symbol, default_interval="4h", max_candles=BACKTEST_MAX_CANDLES)
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return
    symbol, interval, horizon = request.symbol, request.interval, request.horizon
    if interval not in VALID_INTERVALS:
        await update.message.reply_text(f"Intervalo {interval} no válido. Usa uno de: {' '.join(sorted(VALID_INTERVALS, key=len))}")
        return
    period_text = (
        f"período {datetime.fromtimestamp(request.start_ms / 1000, tz=timezone.utc):%Y-%m-%d} → "
        f"{datetime.fromtimestamp(request.end_ms / 1000, tz=timezone.utc):%Y-%m-%d} UTC"
        if request.is_date_range else f"hasta {request.candle_count} velas recientes"
    )
    await update.message.reply_text(f"Descargando histórico de {symbol} ({interval}, {period_text}) y ejecutando Keltner…")
    try:
        klines, signal_start, signal_end = load_backtest_klines(
            bitunix, request, warmup_candles=max(100, filter_window or 0)
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error consultando histórico para backtest Keltner")
        await update.message.reply_text(f"⚠️ Error consultando histórico de Bitunix: {exc}")
        return
    if len(klines) < 40:
        await update.message.reply_text(f"Solo hay {len(klines)} velas para {symbol} en {interval}; no es suficiente para el backtest.")
        return
    closes = [float(k["close"]) for k in klines]
    highs = [float(k["high"]) for k in klines]
    lows = [float(k["low"]) for k in klines]
    opens = [float(k["open"]) for k in klines]
    times = [int(k["time"]) for k in klines]
    try:
        results = run_keltner_backtest(
            closes, highs, lows, opens, times, horizon=horizon,
            only_outer_bands=only_outer_bands,
        )
        raw_results = results
        density_accepted, rejected_results = filter_signals_by_density(
            raw_results, times, filter_window, filter_limit
        )
        band_accepted, rejected_band_results = filter_signals_by_opposite_band(raw_results, band_ratio)
        band_accepted_times = {int(item.time) for item in band_accepted}
        width_accepted, rejected_width_results = filter_signals_by_channel_width(raw_results, channel_width_pct)
        width_accepted_times = {int(item.time) for item in width_accepted}
        results = [item for item in density_accepted if int(item.time) in band_accepted_times and int(item.time) in width_accepted_times]
        rejected_in_period = filter_signals_to_period(rejected_results, signal_start, signal_end)
        rejected_band_in_period = filter_signals_to_period(rejected_band_results, signal_start, signal_end)
        rejected_width_in_period = filter_signals_to_period(rejected_width_results, signal_start, signal_end)
        results = filter_signals_to_period(results, signal_start, signal_end)
        stats = summarize_keltner(results)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error ejecutando backtest Keltner")
        await update.message.reply_text(f"⚠️ Error calculando el backtest Keltner: {exc}")
        return
    first_date = datetime.fromtimestamp(times[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    last_date = datetime.fromtimestamp(times[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    requested = (
        f"señales entre {datetime.fromtimestamp(signal_start / 1000, tz=timezone.utc):%Y-%m-%d} → "
        f"{datetime.fromtimestamp(signal_end / 1000, tz=timezone.utc):%Y-%m-%d} UTC"
        if signal_start is not None else f"{len(klines)} velas ({first_date} → {last_date})"
    )
    header = f"*Backtest Keltner {symbol} — {interval}*\nGestión de riesgo: {risk_config.label}; margen aislado.\n{requested}; velas cargadas {len(klines)} ({first_date} → {last_date}), " + (f"horizonte {horizon} velas" if horizon is not None else "sin límite por operación")
    filter_description = (
        f"\nFiltro de agrupación: máximo {filter_limit} señales en las {filter_window} velas anteriores; "
        f"rechazadas en el período: {len(rejected_in_period)}."
        if filter_window is not None else "\nFiltro de agrupación: desactivado."
    )
    header += filter_description
    header += (f"Filtro de banda contraria: espacio > {band_ratio:g}R; rechazadas en el período: {len(rejected_band_in_period)}.\n" if band_ratio is not None else "Filtro de banda contraria: desactivado.\n")
    header += (f"Filtro de anchura del canal: mínimo {channel_width_pct:g}% del precio; rechazadas en el período: {len(rejected_width_in_period)}.\n" if channel_width_pct is not None else "Filtro de anchura del canal: desactivado.\n")
    header += ("Entradas en bandas límite: ACTIVADO (LONG cruza inferior; SHORT cruza superior).\n" if only_outer_bands else "Entradas en bandas límite: desactivado (se permiten cruces de banda límite o media).\n")
    if not results:
        await update.message.reply_text(header + "\nNo quedaron señales operables dentro del período con los parámetros actuales.", parse_mode="Markdown")
        return
    blocks = []
    for label, key in (("Total", "total"), ("LONG", "long"), ("SHORT", "short")):
        group = stats[key]
        if not group["total"]:
            blocks.append(f"*{label}*: sin señales")
            continue
        lines = [f"*{label}*: {group['total']} señales · riesgo medio {group['riesgo_medio_pct']:.2f}%"]
        for ratio in KELTNER_TP_RATIOS:
            sc = group["escenarios"][ratio]
            wr = f"{sc['win_rate']:.1f}%" if sc["win_rate"] is not None else "N/D"
            if sc["r_medio"] is None:
                lines.append(f"  TP {ratio_label(ratio)}: sin operaciones resueltas")
            else:
                lines.append(f"  TP {ratio_label(ratio)}: {sc['tp']} TP · {sc['sl']} SL · {sc['horizonte']} horizonte · {sc['pendientes']} pendientes · acierto {wr} · R medio {sc['r_medio']:+.2f}R")
        best = group["mejor_ratio"]
        lines.append(f"  Ratio más rentable de la curva: {best['ratio']:g}:1 (R medio {best['r_medio']:+.2f}R)" if best else "  Curva sin operaciones evaluables")
        mr = group["max_ratio"]
        if mr["mediana"] is not None:
            lines.append(f"  Ratio máximo alcanzado: mediana {mr['mediana']:.2f}R · media {mr['medio']:.2f}R")
        blocks.append("\n".join(lines))
    await update.message.reply_text(header + "\n\n" + "\n\n".join(blocks), parse_mode="Markdown")
    try:
        workbook_buffer = build_keltner_workbook(symbol, interval, horizon, klines, results, stats, risk_config, {"window": filter_window, "limit": filter_limit, "rejected": len(rejected_in_period), "band_ratio": band_ratio, "band_rejected": len(rejected_band_in_period), "channel_width_pct": channel_width_pct, "width_rejected": len(rejected_width_in_period)})
    except Exception:  # noqa: BLE001
        logger.exception("Error generando Excel del backtest Keltner")
        await update.message.reply_text("⚠️ El backtest terminó, pero no se pudo generar el Excel.")
        return
    filename = f"backtest_keltner_{symbol}_{interval}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.xlsx"
    await update.message.reply_document(document=workbook_buffer, filename=filename, caption=f"📊 Keltner: {len(results)} señales operables; rechazos por agrupación {len(rejected_in_period)}, por banda {len(rejected_band_in_period)}, por anchura {len(rejected_width_in_period)}; {len(klines)} velas de {symbol} ({interval}).")



# --- Watchlist de alertas: monedas vigiladas por la estrategia (4h y 1h) ---

async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject_unauthorized(update)
        return

    args = context.args

    # Redirige los subcomandos RSI al manejador que ya implementa su lógica.
    # Así funcionan /alerts rsi add BTCUSDT, remove, reset y check.
    if args and args[0].lower() == "rsi":
        await watchlist_command(update, context)
        return

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
        # Evita consultar símbolos ficticios cuando se mezcla el orden de los subcomandos RSI.
        if len(args) > 1 and args[1].lower() in {"rsi", "add", "remove", "del", "reset"}:
            await update.message.reply_text(
                "Para RSI usa `/alerts rsi add BTCUSDT` o `/alerts rsi check BTCUSDT 1h`. "
                "El formato `/alerts check rsi add BTCUSDT` no es válido.",
                parse_mode="Markdown",
            )
            return
        # /alerts check [SIMBOLO] [4h|1h]; los argumentos pueden ir en cualquier orden
        rest = [x.lower() if x.lower() in ALERT_INTERVALS else x for x in args[1:]]
        intervals = [x for x in rest if x in ALERT_INTERVALS] or list(ALERT_INTERVALS)
        symbol_args = [x for x in rest if x not in ALERT_INTERVALS]
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


_INTERVAL_MILLISECONDS = {"1h": 60 * 60 * 1000, "4h": 4 * 60 * 60 * 1000}


def _evaluate_rsi_alert_history(symbol: str, interval: str = "4h"):
    """Calcula RSI para todas las velas cerradas y devuelve cada resultado con su timestamp."""
    klines = bitunix.get_kline(symbol, interval=interval, limit=150)
    if not klines:
        raise ValueError("Bitunix no devolvió velas")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    duration_ms = _INTERVAL_MILLISECONDS.get(interval)
    if duration_ms is None:
        raise ValueError(f"intervalo RSI no válido: {interval}")
    closed = []
    for candle in klines:
        raw_stamp = candle.get("time")
        if raw_stamp is None:
            continue
        stamp = int(raw_stamp)
        if stamp < 100_000_000_000:
            stamp *= 1000
        if stamp + duration_ms <= now_ms:
            # La copia evita modificar los objetos que mantiene el cliente.
            item = dict(candle)
            item["time"] = stamp
            closed.append(item)
    closed.sort(key=lambda candle: int(candle["time"]))
    if len(closed) < 40:
        raise ValueError("no hay suficientes velas cerradas para calcular RSI")

    closes = [float(k["close"]) for k in closed]
    highs = [float(k["high"]) for k in closed]
    lows = [float(k["low"]) for k in closed]
    signals = generate_rsi_signals(
        closes, highs, lows, rsi_period=14, oversold=30, overbought=70,
        pivot_left=2, pivot_right=2, stop_buffer_pct=0,
    )
    # Las alertas genéricas no mantienen el ledger ni conocen si hubo SL;
    # por eso solo publican la entrada inicial, nunca candidatos de reentrada.
    signals = [sig if sig is None or sig.signal_type == "RSI_TURN" else None for sig in signals]
    indicator = rsi_indicator(closes, rsi_period=14, ma_type="SMA", ma_period=14)
    rows = []
    for i, candle in enumerate(closed):
        value = indicator["rsi"][i]
        average = indicator["ma"][i]
        if value is None:
            continue
        rows.append((int(candle["time"]), signals[i], float(value), float(average) if average is not None else None))
    if not rows:
        raise ValueError("RSI aún no está disponible")
    return rows


def _evaluate_rsi_alert_symbol(symbol: str, interval: str = "4h"):
    """Evalúa la última vela cerrada, útil para el comando manual /alerts rsi check."""
    candle_time, signal, latest_rsi, latest_ma = _evaluate_rsi_alert_history(symbol, interval)[-1]
    return signal, candle_time, latest_rsi, latest_ma


def _format_rsi_signal_message(symbol: str, interval: str, signal) -> str:
    side_label = "🟢 LONG" if signal.side == "LONG" else "🔴 SHORT"
    stop_desc = "debajo del mínimo local" if signal.side == "LONG" else "encima del máximo local"
    risk_pct = abs(signal.signal_close - signal.stop_loss) / signal.signal_close * 100 if signal.signal_close else 0
    return (
        f"🔔 *Señal RSI — {symbol}* ({side_label})\n\n"
        f"Vela de {interval} cerrada. Tipo: *{signal.signal_type}*.\n"
        f"RSI: `{signal.rsi:.2f}` · sin filtro de media RSI\n"
        f"Cierre de señal: `{signal.signal_close:.6g}`\n"
        f"Stop sugerido ({stop_desc}): `{signal.stop_loss:.6g}` (riesgo aprox. {risk_pct:.2f}%).\n"
        f"TP: al salir de la zona extrema opuesta (a mercado). Entrada de referencia: apertura de la vela siguiente."
    )


async def _check_rsi_alerts_job(context: ContextTypes.DEFAULT_TYPE, interval: str, send) -> None:
    """Envía un resumen del estado RSI en cada ejecución y alertas separadas al detectar entradas.

    El resumen incluye incluso cuando no hay señal, para que el usuario pueda ver que
    la tarea se ejecutó, qué vela cerrada se evaluó y por qué no hubo entrada.
    """
    if not rsi_alerts_watchlist:
        return
    state_changed = False
    status_blocks: list[str] = []
    for symbol in list(rsi_alerts_watchlist):
        state_key = f"{symbol}|{interval}"
        try:
            history = _evaluate_rsi_alert_history(symbol, interval)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error evaluando estado RSI para %s (%s); se reintentará", symbol, interval)
            status_blocks.append(f"⚠️ *{symbol}* · {interval}: error consultando velas ({_md_safe(exc)})")
            continue

        candle_time, latest_signal, latest_rsi, latest_ma = history[-1]
        candle_label = datetime.fromtimestamp(candle_time / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        previous_time = rsi_alert_state.get(state_key)
        if previous_time is None:
            # En el primer ciclo no enviamos señales históricas, pero sí mostramos el estado actual.
            new_rows = []
            rsi_alert_state[state_key] = candle_time
            state_changed = True
        else:
            new_rows = [row for row in history if row[0] > previous_time]

        # Estado descriptivo, independientemente de que se produzca una entrada.
        if latest_rsi <= 30:
            regime = "sobreventa / régimen LONG"
        elif latest_rsi >= 70:
            regime = "sobrecompra / régimen SHORT"
        elif latest_ma is not None and latest_rsi > latest_ma:
            regime = "RSI por encima de su media"
        elif latest_ma is not None and latest_rsi < latest_ma:
            regime = "RSI por debajo de su media"
        else:
            regime = "sin régimen confirmado"
        status = (
            f"📈 *Estado RSI — {symbol} · {interval}*\n"
            f"Vela cerrada: `{candle_label}`\n"
            f"RSI(14): `{latest_rsi:.2f}` · Media SMA(14): `{latest_ma:.2f}`\n"
            f"Estado: *{regime}*\n"
            f"Entrada en esta vela: {'*' + latest_signal.side + ' — ' + latest_signal.signal_type + '*' if latest_signal else 'no'}"
        )
        status_blocks.append(status)
        logger.info(
            "RSI_STATUS symbol=%s interval=%s candle_utc=%s rsi=%.4f ma=%s regime=%s signal=%s previous_state=%s",
            symbol, interval, candle_label, latest_rsi,
            f"{latest_ma:.4f}" if latest_ma is not None else "n/a", regime,
            f"{latest_signal.side}:{latest_signal.signal_type}" if latest_signal else "none",
            previous_time,
        )

        for row_time, signal, row_rsi, row_ma in new_rows:
            if signal is not None:
                await _send_chunks(send, [_format_rsi_signal_message(symbol, interval, signal)])
            rsi_alert_state[state_key] = row_time
            state_changed = True

    if state_changed:
        _save_rsi_alert_state(rsi_alert_state)
    if status_blocks:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        await _send_chunks(send, [f"📊 *Resumen del estado RSI {interval}* — {now}"] + status_blocks)


async def check_alerts_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Se ejecuta al cierre de cada vela del plazo indicado en `job.data`
    ("4h" o "1h"). Para cada símbolo de alerts_watchlist: avisa a
    ALLOWED_USER_ID si hay señal y, haya o no, envía después un resumen con
    el estado de cada condición de la estrategia.
    """
    interval = context.job.data if context.job is not None and context.job.data else "4h"

    if not alerts_watchlist and not rsi_alerts_watchlist:
        return
    if ALLOWED_USER_ID is None:
        logger.warning("check_alerts_job: TELEGRAM_ALLOWED_USER_ID no está definido, no se puede avisar a nadie.")
        return

    send = lambda **kw: context.bot.send_message(chat_id=ALLOWED_USER_ID, **kw)  # noqa: E731

    # Estrategia clásica ya suscrita mediante /alerts add.
    if alerts_watchlist:
        blocks, signals = _build_status_blocks(list(alerts_watchlist), interval)
        for symbol, signal in signals:
            await _send_chunks(send, [_format_signal_message(symbol, signal, interval)])
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        header = f"📊 *Resumen {interval}* — {now}"
        await _send_chunks(send, [header] + blocks)

    # Estrategia RSI por regímenes, suscripción independiente.
    await _check_rsi_alerts_job(context, interval, send)


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
        # TIC tiene estado recursivo: no basta con ATR(200). El valor trend
        # depende de la secuencia completa desde el inicio del histórico.
        # Cargar hasta 10.000 velas reduce el error de reiniciar el estado
        # demasiado cerca de la vela actual y permite comparar con TradingView.
        klines = bitunix.get_kline_history(symbol, interval=interval, total=10000)
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

    volumes = [float(k.get("volume", 0) or 0) for k in klines]
    try:
        k_value, d_value = stochastic_oscillator(closes, highs, lows)
        lower, middle, upper = bollinger_bands(closes)
        rsi_data = rsi_indicator(closes, rsi_period=14, ma_type="SMA", ma_period=14)
        tic = latest_trend_impulse_channels(None, highs, lows, closes)
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return

    if tic["atr"] is None or tic["trend"] is None:
        await update.message.reply_text(
            f"Trend Impulse Channels necesita al menos 200 velas cerradas para ATR(200); "
            f"Bitunix devolvió {len(klines)}. Prueba de nuevo cuando haya más histórico."
        )
        return

    rsi_value = rsi_data["rsi"][-1]
    rsi_ma_value = rsi_data["ma"][-1]
    last_close = closes[-1]
    stoch_emoji = "🟢" if k_value < 20 else "🔴" if k_value > 80 else "⚪"
    if last_close > upper:
        bb_position = "por encima de la banda superior 📈"
    elif last_close < lower:
        bb_position = "por debajo de la banda inferior 📉"
    else:
        bb_position = "dentro de las bandas"

    rsi_emoji = "🔴" if rsi_value is not None and rsi_value >= 70 else "🟢" if rsi_value is not None and rsi_value <= 30 else "⚪"
    message = (
        f"*{symbol}* — {interval}\n\n"
        f"*RSI (14, Wilder/RMA)*\n"
        f"  RSI: `{rsi_value:.2f}` {rsi_emoji}\n"
        f"  Media RSI (SMA 14): `{rsi_ma_value:.2f}`\n"
        f"  Niveles: sobrecompra 70 · centro 50 · sobreventa 30\n\n"
        f"*Estocástico (14, 3, 3)*\n"
        f"  %K: `{k_value:.2f}`  %D: `{d_value:.2f}` {stoch_emoji}\n\n"
        f"*Bandas de Bollinger (20, 2σ)*\n"
        f"  Superior: `{upper:.4f}`\n"
        f"  Media: `{middle:.4f}`\n"
        f"  Inferior: `{lower:.4f}`\n"
        f"  Precio actual: `{last_close:.4f}` — {bb_position}\n\n"
        f"*Trend Impulse Channels (Zeiierman)*\n"
        f"  Dirección: `{'ALCISTA' if tic['direction'] == 1 else 'BAJISTA' if tic['direction'] == -1 else 'NEUTRAL' if tic['direction'] == 0 else 'CALENTANDO'}`\n"
        f"  Trend: `{tic['trend']:.4f}`\n"
        f"  Canal superior: `{tic['upper']:.4f}`\n"
        f"  Canal inferior: `{tic['lower']:.4f}`\n"
        f"  ATR(200): `{tic['atr']:.4f}`\n"
        f"  Parámetros: `2.86 / -0.034 / 2.02 / 0`\n"
        f"  Retest inferior: `{'SÍ' if tic['lower_retest'] else 'NO'}` · retest superior: `{'SÍ' if tic['upper_retest'] else 'NO'}`\n"
        f"  Nuevo paso: `{'ALCISTA' if tic['bullish_step'] else 'BAJISTA' if tic['bearish_step'] else 'NO'}`"
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
    """Registra comandos sin hacer que un fallo temporal de Telegram impida arrancar."""
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
        logger.info("Comandos registrados en el menú de Telegram.")
    except Exception:
        logger.warning("No se pudieron registrar los comandos en Telegram durante el arranque; el bot continuará y se podrá reintentar en otro despliegue.", exc_info=True)


async def _post_stop(application: Application) -> None:
    """Stop long-lived TTR WebSockets while the asyncio loop is still alive."""
    for feed_key,label in (('ttr_ws_feed','TTR'),('rsi_ws_feed','RSI')):
        feed = application.bot_data.get(feed_key)
        if feed is not None:
            try:
                await feed.stop()
                logger.info('Conexiones WebSocket %s cerradas ordenadamente.',label)
            except Exception:
                logger.exception('Error cerrando las conexiones WebSocket %s.',label)


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

    telegram_request = HTTPXRequest(
        connection_pool_size=20,
        connect_timeout=20,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=15,
    )
    application = (
        Application.builder()
        .token(token)
        .request(telegram_request)
        .get_updates_request(HTTPXRequest(connect_timeout=20, read_timeout=30, write_timeout=30, pool_timeout=15))
        .post_init(_post_init)
        .post_stop(_post_stop)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("price", price))
    application.add_handler(CommandHandler("watchlist", watchlist_command))
    application.add_handler(CommandHandler("indicators", indicators_command))
    application.add_handler(CommandHandler("alerts", alerts_command))
    application.add_handler(CommandHandler("backtest", backtest_command))
    application.add_handler(CommandHandler("backtest_keltner", backtest_keltner_command))
    application.add_handler(CommandHandler("backtest_reversion", backtest_reversion_command))
    application.add_handler(CommandHandler("backtest_rsi", backtest_rsi_command))
    application.add_handler(CommandHandler("backtest_tic", backtest_tic_command))
    application.add_handler(CommandHandler("tic_live", tic_live_command))
    application.add_handler(CommandHandler("tic_status", tic_status_command))
    application.add_handler(CommandHandler("tic_stop", tic_stop_command))
    application.add_handler(CommandHandler("tic_history", tic_history_command))
    application.add_handler(CommandHandler("tic_stats", tic_stats_command))
    application.add_handler(CommandHandler("tic_notify", tic_notify_command))
    application.add_handler(CommandHandler("tic_health", tic_health_command))
    application.add_handler(CommandHandler("tic_restart", tic_restart_command))
    application.add_handler(CommandHandler("backtest_squeeze", backtest_squeeze_command))
    application.add_handler(CommandHandler("backtest_dual_thrust", backtest_dual_thrust_command))
    application.add_handler(CommandHandler("backtest_ttr", backtest_ttr_command))
    application.add_handler(CommandHandler("rsi_live", rsi_live_command))
    application.add_handler(CommandHandler("rsi_status", rsi_status_command))
    application.add_handler(CommandHandler("ttr_live", ttr_live_command))
    application.add_handler(CommandHandler("ttr_stop", ttr_stop_command))
    application.add_handler(CommandHandler("ttr_restart", ttr_restart_command))
    application.add_handler(CommandHandler("ttr_notify", ttr_notify_command))
    application.add_handler(CommandHandler("ttr_status", ttr_status_command))
    application.add_handler(CommandHandler("ttr_history", ttr_history_command))
    application.add_handler(CommandHandler("ttr_stats", ttr_stats_command))
    application.add_handler(CommandHandler("ttr_health", ttr_health_command))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("positions", positions))
    application.add_handler(CommandHandler("open", open_position))
    application.add_handler(CommandHandler("close", close_position))
    application.add_handler(CallbackQueryHandler(handle_open_confirmation, pattern="^(confirm|cancel)_open$"))
    application.add_handler(CallbackQueryHandler(handle_close_confirmation, pattern="^(confirm|cancel)_close$"))
    application.add_error_handler(_error_handler)

    if application.job_queue is not None:
        supervisor_interval = ttr_supervisor_interval_seconds()
        application.job_queue.run_repeating(
            ttr_live_monitor_job, interval=supervisor_interval, first=1, name="ttr_live_monitor",
            # Keep one sequential worker: concurrent runs could process the same
            # candle twice or race while persisting an open paper position.
            job_kwargs={
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": max(30, supervisor_interval * 2),
            },
        )
        application.job_queue.run_repeating(rsi_live_monitor_job, interval=10, first=3, name="rsi_live_monitor", job_kwargs={"max_instances":1,"coalesce":True,"misfire_grace_time":30})
        from strategies.trend_impulse_channels.live import monitor_job as tic_live_monitor_job
        application.job_queue.run_repeating(tic_live_monitor_job, interval=10, first=5, name="tic_live_monitor", job_kwargs={"max_instances":1,"coalesce":True,"misfire_grace_time":30})
        logger.info("Monitor RSI paper trading activo (supervisor cada 10 segundos; sin órdenes reales).")
        logger.info(
            "Monitor TTR paper trading: WebSocket prioritario, REST fallback; "
            "supervisor cada %s segundos (sin solapamientos).",
            supervisor_interval,
        )
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


async def backtest_tic_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Backtest Trend Impulse Channels breakout con TP1 configurable."""
    from strategies.trend_impulse_channels.engine import run_backtest, summarize
    from strategies.trend_impulse_channels.xlsx_export import build_workbook
    raw=list(context.args); positional=[]; opts={'tp1_pct':50.0,'risk_usdt':10.0,'fee_pct':0.0,'sl_candles':2,'excel':False}; keys={'--tp1':'tp1_pct','--tp1-pct':'tp1_pct','--risk-usdt':'risk_usdt','--fee':'fee_pct','--sl-candles':'sl_candles','--sl-lookback':'sl_candles'}; i=0
    try:
        while i<len(raw):
            if raw[i].lower()=='--excel':
                if i+1<len(raw) and raw[i+1].lower() in ('true','false','on','off','yes','no'):
                    opts['excel']=raw[i+1].lower() in ('true','on','yes');i+=2
                else: opts['excel']=True;i+=1
            elif raw[i].lower()=='--no-excel': opts['excel']=False;i+=1
            elif raw[i].lower() in keys:
                if i+1>=len(raw):raise ValueError(f'Falta valor para {raw[i]}')
                opts[keys[raw[i].lower()]]=float(raw[i+1]);i+=2
            else:positional.append(raw[i]);i+=1
        req=parse_backtest_request(positional,normalize_symbol=_normalize_symbol,default_interval='1h',max_candles=BACKTEST_MAX_CANDLES)
        if not 0<opts['tp1_pct']<100 or opts['risk_usdt']<=0 or opts['fee_pct']<0 or not 1<=int(opts['sl_candles'])<=200 or int(opts['sl_candles'])!=opts['sl_candles']:raise ValueError('TP1 debe estar entre 0 y 100; riesgo > 0; comisión >= 0; --sl-candles entre 1 y 200')
        if req.interval not in VALID_INTERVALS:raise ValueError(f'Intervalo no válido: {req.interval}')
    except ValueError as exc:
        await update.message.reply_text('Uso: /backtest_tic SIMBOLO INTERVALO VELAS [--tp1 50] [--risk-usdt 10] [--fee 0] [--sl-candles 2] [--excel true]\n'+str(exc));return
    await update.message.reply_text(f'Descargando histórico Trend Impulse Breakout de {req.symbol} ({req.interval})…')
    try:
        if req.is_date_range:
            # El canal es recursivo y no converge solo con 200 velas de ATR.
            # Se usa un calentamiento largo antes del período solicitado.
            klines,signal_start,signal_end=load_backtest_klines(
                bitunix,req,max_range_candles=10000,warmup_candles=9000,followup_candles=10
            )
        else:
            # En modo N velas calculamos el indicador sobre hasta 10.000 velas
            # históricas, pero solo contamos operaciones dentro de las últimas
            # N velas solicitadas.
            klines=bitunix.get_kline_history(req.symbol,interval=req.interval,total=10000)
            if not klines:
                raise ValueError('Bitunix no devolvió histórico para este símbolo/intervalo.')
            signal_start=int(klines[max(0,len(klines)-(req.candle_count or 500))]['time'])
            signal_end=int(klines[-1]['time'])
        o=[float(x['open']) for x in klines];h=[float(x['high']) for x in klines];l=[float(x['low']) for x in klines];c=[float(x['close']) for x in klines];ts=[int(x['time']) for x in klines]
        trades,ind=run_backtest(o,h,l,c,ts,tp1_pct=opts['tp1_pct'],risk_usdt=opts['risk_usdt'],fee_pct=opts['fee_pct'],sl_candles=int(opts['sl_candles']))
        trades=[t for t in trades if signal_start is None or signal_start<=t.signal_time<=signal_end]
        st=summarize(trades)
        await update.message.reply_text(f"""Trend Impulse Breakout · {req.symbol} {req.interval}\nTP1: {opts['tp1_pct']:g}% · TP2: resto en banda nueva · SL: extremo de {int(opts['sl_candles'])} velas · Riesgo: {opts['risk_usdt']:g} USDT\nOperaciones: {st['total']} · TP1: {st['tp1']} · TP2: {st['tp2']} · SL: {st['sl']} · abiertas: {st['open']}\nPnL: {st['pnl_usdt']:+.4f} USDT""")
        if opts['excel']:
            wb=build_workbook(req.symbol,req.interval,klines,trades,tp1_pct=opts['tp1_pct'],risk_usdt=opts['risk_usdt'],fee_pct=opts['fee_pct'],indicator=ind,sl_candles=int(opts['sl_candles']))
            await update.message.reply_document(document=wb,filename=f'backtest_tic_{req.symbol}_{req.interval}_{datetime.now(timezone.utc):%Y%m%d_%H%M}.xlsx')
        else:
            await update.message.reply_text('Excel omitido (opción por defecto). Añade --excel true para recibir el informe.')
    except Exception as exc:
        logger.exception('Error backtest TIC');await update.message.reply_text(f'Error en backtest Trend Impulse: {exc}')

async def tic_live_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Paper Live TIC: add|remove|stop SYMBOL TF [TP1_PCT] [RISK_USDT] [SL_CANDLES]."""
    if not _is_authorized(update):
        await update.message.reply_text("No autorizado."); return
    from strategies.trend_impulse_channels.live import STORE, TF_MS
    a=list(context.args)
    if not a or a[0].lower()=='help':
        await update.message.reply_text('TIC Live (paper, sin órdenes reales):\n/tic_live add BTCUSDT 1h 50 [10] [2]\n/tic_live list\n/tic_live stop BTCUSDT [1h]\n/tic_stop BTCUSDT [1h]\n/tic_status [BTCUSDT]\n/tic_history [BTCUSDT]\n/tic_stats\n/tic_notify BTCUSDT 1h [open|close|tp|stop] [on|off]\n/tic_health\n/tic_restart CONFIRMAR'); return
    action=a[0].lower()
    if action=='list':
        rows=await asyncio.to_thread(STORE.status)
        await update.message.reply_text('\n'.join(f"{x['symbol']} {x['interval']} · {'ACTIVO' if x['enabled'] else 'DETENIDO'} · {json.loads(x['config']).get('tp1_pct',50):g}% TP1 · SL {json.loads(x['config']).get('sl_candles',2)} velas · última vela {x.get('last_bar_time') or 'calentando'}" for x in rows) or 'No hay suscripciones TIC.');return
    if action in ('stop','remove'):
        if len(a)<2: await update.message.reply_text('Uso: /tic_live stop SIMBOLO [TF]');return
        sym=_normalize_symbol(a[1]);tf=a[2].lower() if len(a)>2 else None
        if tf and tf not in TF_MS: await update.message.reply_text('Temporalidad no soportada.');return
        await asyncio.to_thread(STORE.unsubscribe,sym,tf)
        await update.message.reply_text(f'⏹ TIC detenido para {sym}'+(f' {tf}' if tf else '')+'. Las posiciones abiertas se siguen supervisando hasta cerrarse; historial y checkpoint conservados.');return
    if action!='add' or len(a)<3:
        await update.message.reply_text('Uso: /tic_live add SIMBOLO TF [TP1_PCT] [RIESGO_USDT] [VELAS_SL]');return
    sym=_normalize_symbol(a[1]);tf=a[2].lower()
    if tf not in TF_MS: await update.message.reply_text('Temporalidad no soportada.');return
    try: tpct=float(a[3]) if len(a)>3 else 50.0; risk=float(a[4]) if len(a)>4 else 10.0; sln=int(a[5]) if len(a)>5 else 2
    except ValueError: await update.message.reply_text('TP1, riesgo y velas SL deben ser números válidos.');return
    if not 0<tpct<100 or risk<=0 or not 1<=sln<=200: await update.message.reply_text('TP1 debe estar entre 0 y 100, riesgo > 0 y velas SL entre 1 y 200.');return
    existing=await asyncio.to_thread(STORE.get_config,sym,tf)
    await asyncio.to_thread(STORE.subscribe,sym,tf,update.effective_chat.id,{'tp1_pct':tpct,'risk_usdt':risk,'sl_candles':sln})
    await update.message.reply_text(f"✅ TIC Live {'actualizado' if existing else 'activado'}: {sym} {tf}\nTP1 {tpct:g}% · TP2 resto · riesgo {risk:g} USDT · SL {sln} velas. Se conservan operaciones abiertas y calentamiento persistido. Paper trading.")

async def tic_stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update): await update.message.reply_text('No autorizado.'); return
    from strategies.trend_impulse_channels.live import STORE, TF_MS
    if not context.args: await update.message.reply_text('Uso: /tic_stop SIMBOLO [TF]'); return
    sym=_normalize_symbol(context.args[0]);tf=context.args[1].lower() if len(context.args)>1 else None
    if tf and tf not in TF_MS: await update.message.reply_text('Temporalidad no soportada.'); return
    await asyncio.to_thread(STORE.unsubscribe,sym,tf)
    await update.message.reply_text(f'⏹ TIC detenido para {sym}'+(f' {tf}' if tf else '')+'. Las posiciones abiertas continúan bajo supervisión; no se borró ningún dato.')

async def tic_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update): await update.message.reply_text('No autorizado.'); return
    from strategies.trend_impulse_channels.live import STORE
    sym=_normalize_symbol(context.args[0]) if context.args else None
    rows=await asyncio.to_thread(STORE.status); rows=[r for r in rows if not sym or r['symbol']==sym]
    if not rows: await update.message.reply_text('No hay configuraciones TIC.'); return
    lines=['📡 TIC Live · estado']
    for r in rows:
        cfg=json.loads(r.get('config') or '{}'); ind=json.loads(r.get('last_indicator') or '{}'); trades=await asyncio.to_thread(STORE.open_trades,r['symbol'],r['interval'])
        lines.append(f"{r['symbol']} {r['interval']} · {'ACTIVO' if r['enabled'] else ('SUPERVISANDO POSICIONES' if trades else 'DETENIDO')} · abiertas {len(trades)}")
        lines.append(f"TP1 {cfg.get('tp1_pct',50):g}% · riesgo {cfg.get('risk_usdt',10):g} USDT · SL {cfg.get('sl_candles',2)} velas")
        lines.append(f"Trend {ind.get('trend','N/D')} · Upper {ind.get('upper','N/D')} · Lower {ind.get('lower','N/D')} · dirección {ind.get('direction','N/D')}")
        lines.append(f"Última vela UTC: {r.get('last_bar_time') or 'calentando'}")
        for t in trades: lines.append(f"  #{t['id']} {t['side']} entrada {t['entry_price']:.8g} · SL {t['stop_price']:.8g} · restante {t['remaining_qty']:.8g}")
    await update.message.reply_text('\n'.join(lines))

async def tic_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update): await update.message.reply_text('No autorizado.'); return
    from strategies.trend_impulse_channels.live import STORE
    sym=_normalize_symbol(context.args[0]) if context.args else None; rows=await asyncio.to_thread(STORE.history,sym,None,100)
    if not rows: await update.message.reply_text('No hay operaciones TIC en el historial.'); return
    lines=['📚 TIC · historial']
    for t in rows[:40]: lines.append(f"#{t['id']} {t['symbol']} {t['interval']} {t['side']} {t['status']} · entrada {t['entry_price']:.8g} · salida {t.get('exit_price') or '—'} · PnL {t['pnl_usdt']:+.4f} USDT")
    await update.message.reply_text('\n'.join(lines))

async def tic_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update): await update.message.reply_text('No autorizado.'); return
    from strategies.trend_impulse_channels.live import STORE
    data=await asyncio.to_thread(STORE.stats); o=data['overall']; lines=['📊 TIC · estadísticas globales',f"Operaciones {o['total']} · abiertas {o['open']} · cerradas {o['closed']}",f"Acierto {o['win_rate']:.2f}% · ganadoras {o['wins']} · PnL {o['pnl']:+.4f} USDT"]
    for sym,m in data['by_symbol'].items(): lines.append(f"{sym}: {m['total']} ops · {m['win_rate']:.1f}% acierto · {m['pnl']:+.4f} USDT · abiertas {m['open']}")
    await update.message.reply_text('\n'.join(lines))

async def tic_notify_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update): await update.message.reply_text('No autorizado.'); return
    from strategies.trend_impulse_channels.live import STORE
    a=list(context.args)
    if len(a)<2: await update.message.reply_text('Uso: /tic_notify SIMBOLO TF [open|close|tp|stop] [on|off]'); return
    sym=_normalize_symbol(a[0]);tf=a[1].lower();row=await asyncio.to_thread(STORE.get_config,sym,tf)
    if not row: await update.message.reply_text('TIC no está configurado para ese símbolo/temporalidad.'); return
    prefs=json.loads(row.get('config') or '{}')
    if len(a)==2:
        await update.message.reply_text('Notificaciones TIC '+sym+' '+tf+'\n'+'\n'.join(f"{k}: {'ACTIVADA' if prefs.get(v,True) else 'DESACTIVADA'}" for k,v in [('open','notify_open'),('close','notify_close'),('tp','notify_tp'),('stop','notify_stop')]));return
    if len(a)!=4 or a[2].lower() not in ('open','close','tp','stop') or a[3].lower() not in ('on','off'): await update.message.reply_text('Categorías: open, close, tp, stop; valores: on, off.');return
    await asyncio.to_thread(STORE.set_notification,sym,tf,a[2].lower(),a[3].lower()=='on')
    await update.message.reply_text(f"Notificación {a[2].lower()} para {sym} {tf}: {a[3].upper()}")

async def tic_health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update): await update.message.reply_text('No autorizado.'); return
    from strategies.trend_impulse_channels.live import STORE
    try:
        rows=await asyncio.to_thread(STORE.status); configs=await asyncio.to_thread(STORE.configs); opened=0
        for r in configs: opened += len(await asyncio.to_thread(STORE.open_trades,r['symbol'],r['interval']))
        warm=sum(bool(json.loads(r.get('indicator_state') or '{}').get('version')==1) for r in rows)
        await update.message.reply_text(f"TIC health: OK\nConfiguraciones: {len(rows)} · monitorizables: {len(configs)} · operaciones abiertas: {opened}\nCheckpoints de indicador: {warm}/{len(rows)}\nBase de datos: {STORE.db.backend.upper()}\nCalentamiento inicial: hasta 10.000 velas; estado incremental persistido.")
    except Exception as exc: await update.message.reply_text(f'TIC health ERROR: {exc}')

async def tic_restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update): await update.message.reply_text('No autorizado.'); return
    if len(context.args)!=1 or context.args[0].upper()!='CONFIRMAR':
        await update.message.reply_text('⚠️ Elimina todas las suscripciones TIC, checkpoints, posiciones, operaciones e históricos TIC. No afecta TTR ni RSI. Confirma con /tic_restart CONFIRMAR'); return
    from strategies.trend_impulse_channels.live import STORE
    await asyncio.to_thread(STORE.reset_all)
    await update.message.reply_text('♻️ TIC Live reiniciado desde cero: se eliminaron configuraciones, checkpoints, operaciones, eventos e históricos TIC. TTR y RSI no se modificaron.')


if __name__ == "__main__":
    main()
