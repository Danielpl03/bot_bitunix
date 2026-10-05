"""
Cliente de la API pública/privada de Bitunix Futures.

Este módulo NO conoce nada de Telegram: es la capa de acceso a Bitunix
que se puede reutilizar desde cualquier consumidor (bot de Telegram,
una API propia, un script, una app web, etc.).

Documentación oficial:
    https://www.bitunix.com/api-docs/futures/common/introduction.html
    https://openapidoc.bitunix.com/doc/common/sign.html  (firma)
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid

import requests

BITUNIX_BASE_URL = "https://fapi.bitunix.com"
TICKERS_ENDPOINT = f"{BITUNIX_BASE_URL}/api/v1/futures/market/tickers"
KLINE_ENDPOINT = f"{BITUNIX_BASE_URL}/api/v1/futures/market/kline"
ACCOUNT_ENDPOINT = f"{BITUNIX_BASE_URL}/api/v1/futures/account"
PENDING_POSITIONS_ENDPOINT = f"{BITUNIX_BASE_URL}/api/v1/futures/position/get_pending_positions"
PLACE_ORDER_ENDPOINT = f"{BITUNIX_BASE_URL}/api/v1/futures/trade/place_order"
FLASH_CLOSE_ENDPOINT = f"{BITUNIX_BASE_URL}/api/v1/futures/trade/flash_close_position"

# Watchlist por defecto usada cuando no se especifican símbolos
DEFAULT_WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]


class BitunixAPIError(Exception):
    """Error devuelto por la API de Bitunix (code != 0) o fallo de red."""


class BitunixClient:
    """
    Cliente para los endpoints públicos y privados de Bitunix Futures.

    Los endpoints privados (balance, posiciones, órdenes) requieren
    api_key y secret_key con permisos de futuros habilitados en
    Bitunix (Cuenta -> API Management).
    """

    def __init__(self, api_key: str | None = None, secret_key: str | None = None, timeout: int = 10):
        self.api_key = api_key
        self.secret_key = secret_key
        self.timeout = timeout
        self.session = requests.Session()

    # --- Firma de solicitudes privadas ---
    # Algoritmo oficial (doble SHA256):
    #   digest = SHA256(nonce + timestamp + api_key + queryParams + body)
    #   sign   = SHA256(digest + secret_key)
    # queryParams: pares clave+valor ordenados ascendentemente por clave,
    #              concatenados SIN separadores (ej: id=1, uid=200 -> "id1uid200")
    # body: JSON compacto (sin espacios), exactamente igual al que se envía

    @staticmethod
    def _sorted_query_string(params: dict) -> str:
        items = sorted((k, v) for k, v in params.items() if v is not None)
        return "".join(f"{k}{v}" for k, v in items)

    @staticmethod
    def _compact_body(payload: dict | None) -> str:
        if not payload:
            return ""
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

    def _sign(self, nonce: str, timestamp: str, query_string: str, body: str) -> str:
        if not self.api_key or not self.secret_key:
            raise BitunixAPIError(
                "Esta operación requiere api_key y secret_key configurados en BitunixClient."
            )
        digest_input = f"{nonce}{timestamp}{self.api_key}{query_string}{body}"
        digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
        sign_input = f"{digest}{self.secret_key}"
        return hashlib.sha256(sign_input.encode("utf-8")).hexdigest()

    def _private_headers(self, query_string: str, body: str) -> dict:
        nonce = uuid.uuid4().hex  # cadena aleatoria de 32 caracteres
        timestamp = str(int(time.time() * 1000))
        sign = self._sign(nonce, timestamp, query_string, body)
        return {
            "api-key": self.api_key,
            "nonce": nonce,
            "timestamp": timestamp,
            "sign": sign,
            "language": "es-ES",
            "Content-Type": "application/json",
        }

    def _private_get(self, url: str, params: dict | None = None) -> list | dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        query_string = self._sorted_query_string(params)
        headers = self._private_headers(query_string, "")
        response = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        return self._handle_response(response)

    def _private_post(self, url: str, payload: dict | None = None) -> list | dict:
        payload = {k: v for k, v in (payload or {}).items() if v is not None}
        body = self._compact_body(payload)
        headers = self._private_headers("", body)
        response = self.session.post(url, data=body.encode("utf-8"), headers=headers, timeout=self.timeout)
        return self._handle_response(response)

    @staticmethod
    def _handle_response(response: requests.Response):
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise BitunixAPIError(f"Bitunix API error ({payload.get('code')}): {payload.get('msg')}")
        return payload.get("data")

    # --- Endpoints públicos (no requieren autenticación) ---

    def get_tickers(self, symbols: list[str] | None = None) -> list[dict]:
        params = {}
        if symbols:
            params["symbols"] = ",".join(symbols)

        response = self.session.get(TICKERS_ENDPOINT, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()

        if payload.get("code") != 0:
            raise BitunixAPIError(f"Bitunix API error: {payload.get('msg')}")

        return payload.get("data", [])

    def get_price(self, symbol: str) -> dict | None:
        tickers = self.get_tickers([symbol])
        return tickers[0] if tickers else None

    def get_watchlist(self, symbols: list[str] | None = None) -> list[dict]:
        return self.get_tickers(symbols or DEFAULT_WATCHLIST)

    def get_kline(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 100,
        start_time: int | None = None,
        end_time: int | None = None,
        kline_type: str = "LAST_PRICE",
    ) -> list[dict]:
        """
        Histórico de velas (OHLC) de un símbolo. Endpoint público, no requiere
        api_key/secret_key. `interval` acepta: 1m 5m 15m 30m 1h 2h 4h 6h 8h
        12h 1d 3d 1w 1M. `limit` por defecto 100, máximo 200 (límite de la API).
        Devuelve las velas ordenadas de más antigua a más reciente, que es el
        orden que necesitan los cálculos de indicadores.
        """
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
            "startTime": start_time,
            "endTime": end_time,
            "type": kline_type,
        }
        params = {k: v for k, v in params.items() if v is not None}

        response = self.session.get(KLINE_ENDPOINT, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()

        if payload.get("code") != 0:
            raise BitunixAPIError(f"Bitunix API error: {payload.get('msg')}")

        data = payload.get("data", [])
        return sorted(data, key=lambda k: k.get("time", 0))

    def get_kline_history(
        self,
        symbol: str,
        interval: str = "4h",
        total: int = 500,
        kline_type: str = "LAST_PRICE",
        end_time: int | None = None,
    ) -> list[dict]:
        """
        Igual que `get_kline`, pero sin el límite de 200 velas por request:
        pagina hacia atrás en el tiempo (pidiendo trozos de 200 con `endTime`
        decreciente) hasta reunir `total` velas o hasta que la API deje de
        devolver histórico más antiguo para ese símbolo. Pensado para
        backtests, donde 200 velas suelen quedarse cortas.

        `end_time` opcional fija el punto más reciente desde el que mirar
        hacia atrás (ms, timestamp Unix); si no se da, parte de "ahora".

        Devuelve las velas ordenadas de más antigua a más reciente, sin
        duplicados, recortadas a como mucho `total`.
        """
        all_candles: dict[int, dict] = {}
        cursor_end_time = end_time

        while len(all_candles) < total:
            page = self.get_kline(
                symbol,
                interval=interval,
                limit=200,
                end_time=cursor_end_time,
                kline_type=kline_type,
            )
            if not page:
                break  # no hay más histórico disponible para este símbolo

            new_candles = 0
            for candle in page:
                t = candle.get("time")
                if t is not None and t not in all_candles:
                    all_candles[t] = candle
                    new_candles += 1

            if new_candles == 0:
                break  # ya no llegan velas nuevas: cortamos para no entrar en bucle

            oldest_time = min(c["time"] for c in page if c.get("time") is not None)
            cursor_end_time = oldest_time - 1

            if len(page) < 200:
                break  # la API devolvió menos del máximo: no queda más historial atrás

        candles = sorted(all_candles.values(), key=lambda c: c.get("time", 0))
        return candles[-total:] if len(candles) > total else candles

    # --- Endpoints privados (requieren api_key/secret_key) ---

    def get_account(self, margin_coin: str = "USDT") -> dict | None:
        """Balance y estado de la cuenta de futuros para una moneda de margen."""
        data = self._private_get(ACCOUNT_ENDPOINT, {"marginCoin": margin_coin})
        if not data:
            return None
        if isinstance(data, list):
            return data[0] if data else None
        return data  # la API a veces devuelve el objeto directo, sin envolver en lista

    def get_pending_positions(self, symbol: str | None = None, position_id: str | None = None) -> list[dict]:
        """Posiciones abiertas actualmente, opcionalmente filtradas por símbolo o ID."""
        data = self._private_get(
            PENDING_POSITIONS_ENDPOINT,
            {"symbol": symbol, "positionId": position_id},
        )
        if not data:
            return []
        return data if isinstance(data, list) else [data]

    def place_order(
        self,
        symbol: str,
        qty: str,
        side: str,
        trade_side: str = "OPEN",
        order_type: str = "MARKET",
        price: str | None = None,
        reduce_only: bool = False,
    ) -> dict:
        """
        Coloca una orden.

        side: 'BUY' o 'SELL'
        trade_side: 'OPEN' (abrir) o 'CLOSE' (cerrar)
        order_type: 'MARKET' o 'LIMIT' (si es LIMIT, price es obligatorio)
        """
        payload = {
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "tradeSide": trade_side,
            "orderType": order_type,
            "reduceOnly": reduce_only,
        }
        if order_type == "LIMIT":
            if not price:
                raise BitunixAPIError("Las órdenes LIMIT requieren un precio.")
            payload["price"] = price
            payload["effect"] = "GTC"

        return self._private_post(PLACE_ORDER_ENDPOINT, payload)

    def flash_close_position(self, position_id: str) -> dict:
        """Cierra una posición inmediatamente a precio de mercado."""
        return self._private_post(FLASH_CLOSE_ENDPOINT, {"positionId": position_id})
