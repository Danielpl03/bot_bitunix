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

    # --- Endpoints privados (requieren api_key/secret_key) ---

    def get_account(self, margin_coin: str = "USDT") -> dict | None:
        """Balance y estado de la cuenta de futuros para una moneda de margen."""
        data = self._private_get(ACCOUNT_ENDPOINT, {"marginCoin": margin_coin})
        return data[0] if data else None

    def get_pending_positions(self, symbol: str | None = None, position_id: str | None = None) -> list[dict]:
        """Posiciones abiertas actualmente, opcionalmente filtradas por símbolo o ID."""
        data = self._private_get(
            PENDING_POSITIONS_ENDPOINT,
            {"symbol": symbol, "positionId": position_id},
        )
        return data or []

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
