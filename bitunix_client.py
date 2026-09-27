"""
Cliente de la API pública/privada de Bitunix Futures.

Este módulo NO conoce nada de Telegram: es la capa de acceso a Bitunix
que se puede reutilizar desde cualquier consumidor (bot de Telegram,
una API propia, un script, una app web, etc.).

Documentación oficial:
    https://www.bitunix.com/api-docs/futures/common/introduction.html
"""

from __future__ import annotations

import requests

BITUNIX_BASE_URL = "https://fapi.bitunix.com"
TICKERS_ENDPOINT = f"{BITUNIX_BASE_URL}/api/v1/futures/market/tickers"

# Watchlist por defecto usada cuando no se especifican símbolos
DEFAULT_WATCHLIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]


class BitunixAPIError(Exception):
    """Error devuelto por la API de Bitunix (code != 0) o fallo de red."""


class BitunixClient:
    """
    Cliente mínimo para los endpoints públicos de Bitunix Futures.

    Más adelante, cuando se agreguen endpoints privados (balance,
    posiciones, órdenes), este cliente se puede extender recibiendo
    api_key/secret_key en el constructor y agregando la firma HMAC
    requerida (ver /common/sign.html en la documentación oficial).
    """

    def __init__(self, api_key: str | None = None, secret_key: str | None = None, timeout: int = 10):
        self.api_key = api_key
        self.secret_key = secret_key
        self.timeout = timeout
        self.session = requests.Session()

    # --- Endpoints públicos (no requieren autenticación) ---

    def get_tickers(self, symbols: list[str] | None = None) -> list[dict]:
        """
        Consulta el endpoint público de tickers.
        Si no se pasan symbols, la API devuelve todos los pares disponibles.
        """
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
        """Devuelve el ticker de un único símbolo, o None si no existe."""
        tickers = self.get_tickers([symbol])
        return tickers[0] if tickers else None

    def get_watchlist(self, symbols: list[str] | None = None) -> list[dict]:
        """Devuelve los tickers de la watchlist dada, o la watchlist por defecto."""
        return self.get_tickers(symbols or DEFAULT_WATCHLIST)

    # --- Aquí se agregarán a futuro los endpoints privados ---
    # def get_balance(self) -> dict: ...
    # def get_positions(self) -> list[dict]: ...
    # def place_order(self, ...): ...
