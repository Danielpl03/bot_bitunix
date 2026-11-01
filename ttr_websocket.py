"""Bitunix public trade WebSocket feed with protocol heartbeat and REST fallback cache."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Dict

WS_URL = "wss://fapi.bitunix.com/public/"
log = logging.getLogger(__name__)


class BitunixTickerFeed:
    def __init__(
        self,
        url: str = WS_URL,
        stale_after: float = 10.0,
        heartbeat_interval: float = 20.0,
        heartbeat_timeout: float = 10.0,
    ):
        self.url = url
        self.stale_after = stale_after
        self.heartbeat_interval = max(5.0, float(heartbeat_interval))
        self.heartbeat_timeout = max(2.0, float(heartbeat_timeout))
        self.latest: Dict[str, Dict[str, Any]] = {}
        self.pending: Dict[str, list] = {}
        self.status: Dict[str, Dict[str, Any]] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        self.connections: Dict[str, Any] = {}
        self._stopping = False
        self._stopped_symbols = set()

    def resume_symbol(self, symbol: str):
        """Explicitly allow a previously stopped symbol to reconnect."""
        self._stopped_symbols.discard(symbol)

    def ensure(self, symbol: str):
        if symbol in self._stopped_symbols or self._stopping:
            return
        task = self.tasks.get(symbol)
        if task is None or task.done():
            self.tasks[symbol] = asyncio.create_task(
                self._run(symbol), name=f"bitunix-ws-{symbol}"
            )

    def get(self, symbol: str):
        item = self.latest.get(symbol)
        if not item:
            return None
        age = time.time() - item["received_at"]
        if age > self.stale_after:
            return None
        return {**item, "age_seconds": round(age, 2)}

    def drain(self, symbol: str):
        events = self.pending.get(symbol, [])
        self.pending[symbol] = []
        return events

    def health(self, symbol: str):
        status = dict(self.status.get(symbol, {}))
        quote = self.latest.get(symbol)
        status["connected"] = bool(status.get("connected"))
        status["last_message_age_seconds"] = (
            round(time.time() - quote["received_at"], 2) if quote else None
        )
        status["fresh"] = self.get(symbol) is not None
        return status

    async def stop_symbol(self, symbol: str):
        """Stop only one market stream; it will restart on the next ensure()."""
        self._stopped_symbols.add(symbol)
        ws = self.connections.get(symbol)
        if ws is not None:
            try:
                await asyncio.wait_for(ws.close(), timeout=5)
            except Exception:
                log.debug("Error cerrando WebSocket de %s", symbol, exc_info=True)
        task = self.tasks.pop(symbol, None)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.connections.pop(symbol, None)
        # Never reuse a pre-stop quote when the symbol is subscribed again.
        self.latest.pop(symbol, None)
        self.pending.pop(symbol, None)
        self.status.setdefault(symbol, {}).update(
            {"connected": False, "state": "stopped", "stopped_at": time.time()}
        )

    async def stop(self):
        """Close sockets gracefully before cancelling their reader tasks."""
        self._stopping = True
        sockets = list(self.connections.values())
        if sockets:
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        *(ws.close() for ws in sockets), return_exceptions=True
                    ),
                    timeout=7,
                )
            except asyncio.TimeoutError:
                log.warning(
                    "Timeout cerrando conexiones WebSocket; se cancelarán las tareas restantes."
                )
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
        self.connections.clear()

    def _process_message(self, raw: str, symbol: str, pong_event: asyncio.Event):
        """Parse Bitunix frames, including its documented JSON ping response."""
        msg = json.loads(raw)
        if not isinstance(msg, dict):
            return

        # Bitunix Futures responds to {op: ping, ping: unix_seconds} with
        # {op: ping, pong: unix_seconds, ping: unix_seconds}.
        if msg.get("pong") is not None or msg.get("op") == "pong":
            status = self.status.setdefault(symbol, {})
            sent_at = status.get("heartbeat_sent_monotonic")
            if sent_at is not None:
                status["heartbeat_rtt_ms"] = round(
                    (time.monotonic() - sent_at) * 1000, 1
                )
            status["last_pong_at"] = time.time()
            pong_event.set()
            return
        if msg.get("op") in ("ping", "pong"):
            return
        if msg.get("ch") != "trade" or msg.get("symbol") != symbol:
            return

        data = msg.get("data") or {}
        if isinstance(data, list):
            price = data[-1].get("p") if data and isinstance(data[-1], dict) else None
        else:
            price = data.get("p") if isinstance(data, dict) else None
        if price is None:
            return

        price = float(price)
        if price <= 0:
            return
        exchange_time = int(msg.get("ts") or time.time() * 1000)
        old = self.latest.get(symbol)
        if old and exchange_time < old.get("exchange_time", 0):
            return
        event = {
            "price": price,
            "exchange_time": exchange_time,
            "received_at": time.time(),
            "source": "websocket_trade",
        }
        self.latest[symbol] = event
        queue = self.pending.setdefault(symbol, [])
        queue.append(event)
        if len(queue) > 5000:
            del queue[:-5000]
        status = self.status.setdefault(symbol, {})
        status.update(
            {
                "connected": True,
                "state": "connected",
                "last_message_at": time.time(),
                "last_trade_at": time.time(),
            }
        )

    async def _read_messages(
        self, ws: Any, symbol: str, pong_event: asyncio.Event
    ):
        while not self._stopping:
            raw = await ws.recv()
            status = self.status.setdefault(symbol, {})
            status["last_frame_at"] = time.time()
            self._process_message(raw, symbol, pong_event)

    async def _heartbeat(
        self, ws: Any, symbol: str, pong_event: asyncio.Event
    ):
        """Send the exchange's JSON heartbeat even while trades are flowing."""
        loop = asyncio.get_running_loop()
        next_ping = loop.time()
        while not self._stopping:
            await asyncio.sleep(max(0.0, next_ping - loop.time()))
            pong_event.clear()
            sent_monotonic = time.monotonic()
            ping_value = int(time.time())
            status = self.status.setdefault(symbol, {})
            status["heartbeat_sent_monotonic"] = sent_monotonic
            status["last_ping_at"] = time.time()
            await ws.send(json.dumps({"op": "ping", "ping": ping_value}))
            try:
                await asyncio.wait_for(
                    pong_event.wait(), timeout=self.heartbeat_timeout
                )
            except asyncio.TimeoutError as exc:
                raise ConnectionError(
                    f"Bitunix JSON heartbeat timeout: sin pong en {self.heartbeat_timeout:.0f}s"
                ) from exc
            next_ping += self.heartbeat_interval
            # Do not emit a burst of pings if the event loop was delayed.
            if next_ping < loop.time():
                next_ping = loop.time() + self.heartbeat_interval

    async def _run(self, symbol: str):
        try:
            import websockets
        except Exception as exc:
            self.status[symbol] = {
                "connected": False,
                "error": f"websockets dependency missing: {exc}",
            }
            return

        reconnect_delay = 1.0
        while not self._stopping and symbol not in self._stopped_symbols:
            ws = None
            connected_monotonic = None
            reader_task = None
            heartbeat_task = None
            try:
                self.status[symbol] = {
                    "connected": False,
                    "state": "connecting",
                    "last_error": None,
                }
                # Bitunix Futures documents a JSON {op: ping} heartbeat. Disable
                # RFC6455 control-frame pings to avoid running two heartbeat
                # protocols at once; the documented JSON heartbeat is handled below.
                async with websockets.connect(
                    self.url,
                    open_timeout=10,
                    ping_interval=None,
                    close_timeout=5,
                    max_queue=1000,
                ) as ws:
                    self.connections[symbol] = ws
                    await ws.send(
                        json.dumps(
                            {
                                "op": "subscribe",
                                "args": [{"symbol": symbol, "ch": "trade"}],
                            }
                        )
                    )
                    connected_monotonic = time.monotonic()
                    self.status[symbol] = {
                        "connected": True,
                        "state": "connected",
                        "connected_at": time.time(),
                        "last_error": None,
                        "heartbeat_interval_seconds": self.heartbeat_interval,
                    }
                    pong_event = asyncio.Event()
                    reader_task = asyncio.create_task(
                        self._read_messages(ws, symbol, pong_event),
                        name=f"bitunix-ws-reader-{symbol}",
                    )
                    heartbeat_task = asyncio.create_task(
                        self._heartbeat(ws, symbol, pong_event),
                        name=f"bitunix-ws-heartbeat-{symbol}",
                    )
                    done, pending = await asyncio.wait(
                        {reader_task, heartbeat_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    # Propagate the heartbeat timeout or reader disconnection.
                    for task in done:
                        task.result()
                    raise ConnectionError("Bitunix WebSocket reader terminó inesperadamente")

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stopping or symbol in self._stopped_symbols:
                    break
                uptime = (
                    time.monotonic() - connected_monotonic
                    if connected_monotonic is not None
                    else 0.0
                )
                if uptime >= 60:
                    reconnect_delay = 1.0
                jitter = random.uniform(0.1, 0.8)
                wait_seconds = min(reconnect_delay + jitter, 30.8)
                self.status[symbol] = {
                    **self.status.get(symbol, {}),
                    "connected": False,
                    "state": "reconnecting",
                    "last_error": str(exc)[:300],
                    "disconnected_at": time.time(),
                    "last_connection_seconds": round(uptime, 2),
                    "reconnect_in_seconds": round(wait_seconds, 2),
                }
                log.warning(
                    "Bitunix WS %s desconectado tras %.1fs; reconectando en %.1fs: %s",
                    symbol,
                    uptime,
                    wait_seconds,
                    exc,
                )
                await asyncio.sleep(wait_seconds)
                reconnect_delay = min(reconnect_delay * 2, 30.0)
            finally:
                for task in (reader_task, heartbeat_task):
                    if task is not None and not task.done():
                        task.cancel()
                remaining = [
                    task
                    for task in (reader_task, heartbeat_task)
                    if task is not None
                ]
                if remaining:
                    await asyncio.gather(*remaining, return_exceptions=True)
                if ws is not None and self.connections.get(symbol) is ws:
                    self.connections.pop(symbol, None)

        self.status.setdefault(symbol, {}).update(
            {"connected": False, "state": "stopped" if (self._stopping or symbol in self._stopped_symbols) else "disconnected"}
        )
