"""Gestión de riesgo reutilizable para cualquier estrategia de backtest.

Los cálculos de liquidación son una estimación conservadora, no una réplica
exacta del motor de Bitunix: faltan los tramos de mantenimiento específicos
por contrato, las comisiones y el precio de marca.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

DEFAULT_FIXED_RISK_USDT = 10.0
DEFAULT_RISK_PERCENT = 1.0
DEFAULT_INITIAL_CAPITAL_USDT = 1000.0
DEFAULT_SAFETY_BUFFER_PERCENT = 25.0
DEFAULT_MAX_LEVERAGE = 125
# Supuesto provisional para reservar espacio a margen de mantenimiento.
# Debe reemplazarse por el tier real del contrato cuando se obtenga de Bitunix.
ASSUMED_MAINTENANCE_MARGIN_RATE = 0.005


@dataclass(frozen=True)
class RiskConfig:
    mode: str = "fixed"  # fixed | percent
    fixed_risk_usdt: float = DEFAULT_FIXED_RISK_USDT
    risk_percent: float = DEFAULT_RISK_PERCENT
    initial_capital_usdt: float = DEFAULT_INITIAL_CAPITAL_USDT
    safety_buffer_percent: float = DEFAULT_SAFETY_BUFFER_PERCENT
    max_leverage: int = DEFAULT_MAX_LEVERAGE
    maintenance_margin_rate: float = ASSUMED_MAINTENANCE_MARGIN_RATE

    def __post_init__(self):
        if self.mode not in ("fixed", "percent"):
            raise ValueError("El modo de riesgo debe ser 'fixed' o 'percent'.")
        if self.fixed_risk_usdt <= 0 or self.risk_percent <= 0 or self.initial_capital_usdt <= 0:
            raise ValueError("Riesgo y capital deben ser mayores que cero.")
        if self.risk_percent > 100:
            raise ValueError("El riesgo porcentual no puede superar el 100 %.")
        if not 1 <= self.max_leverage <= 125:
            raise ValueError("El apalancamiento máximo debe estar entre 1x y 125x.")
        if not 0 <= self.safety_buffer_percent <= 1000:
            raise ValueError("El margen de seguridad debe estar entre 0 % y 1000 %.")

    @property
    def risk_amount_usdt(self) -> float:
        if self.mode == "fixed":
            return self.fixed_risk_usdt
        # Sin interés compuesto en esta versión: porcentaje del capital inicial.
        return self.initial_capital_usdt * self.risk_percent / 100.0

    @property
    def label(self) -> str:
        if self.mode == "fixed":
            return f"Fijo: {self.fixed_risk_usdt:g} USDT/operación"
        return (f"Porcentual: {self.risk_percent:g}% de {self.initial_capital_usdt:g} USDT "
                f"({self.risk_amount_usdt:g} USDT/operación; sin compuesto)")


@dataclass(frozen=True)
class RiskPosition:
    risk_usdt: float
    quantity: float
    notional_usdt: float
    stop_distance_percent: float
    safety_distance_percent: float
    leverage: int
    isolated_margin_usdt: float
    estimated_liquidation_distance_percent: float
    feasible: bool
    note: str


def calculate_position(entry: float, stop: float, config: RiskConfig) -> RiskPosition:
    """Calcula tamaño por riesgo y mayor apalancamiento estimado seguro.

    Aproximación: distancia de liquidación ~ 1/L - tasa de mantenimiento.
    Se exige que sea al menos distancia_stop * (1 + buffer). Se escoge el mayor
    apalancamiento entero que cumple, dentro del máximo configurado.
    """
    entry, stop = float(entry), float(stop)
    distance = abs(entry - stop)
    if entry <= 0 or distance <= 0:
        return RiskPosition(config.risk_amount_usdt, 0, 0, 0, 0, 1, 0, 0, False,
                            "Entrada o stop inválidos; operación no dimensionable.")
    risk_pct = distance / entry * 100.0
    risk_fraction = distance / entry
    quantity = config.risk_amount_usdt / distance
    notional = quantity * entry
    required_liq_distance = risk_fraction * (1 + config.safety_buffer_percent / 100.0)
    # Si ni siquiera 1x cumple la distancia estimada, no abrir esta operación.
    max_safe = 1.0 / (required_liq_distance + config.maintenance_margin_rate)
    leverage = min(config.max_leverage, int(math.floor(max_safe)))
    if leverage < 1:
        return RiskPosition(config.risk_amount_usdt, quantity, notional, risk_pct,
                            required_liq_distance * 100.0, 1, notional,
                            max(0.0, (1.0 - config.maintenance_margin_rate) * 100.0),
                            False, "La distancia al stop no permite mantener el margen de seguridad ni a 1x.")
    liq_distance_pct = (1.0 / leverage - config.maintenance_margin_rate) * 100.0
    safe = liq_distance_pct + 1e-9 >= required_liq_distance * 100.0
    margin = notional / leverage
    note = "Estimación; no incluye comisiones, slippage ni tier real de mantenimiento de Bitunix."
    if leverage == config.max_leverage and max_safe >= config.max_leverage:
        note = f"Limitado por el máximo configurado ({config.max_leverage}x); " + note
    return RiskPosition(config.risk_amount_usdt, quantity, notional, risk_pct,
                        required_liq_distance * 100.0, leverage, margin,
                        liq_distance_pct, safe, note)


def scenario_pnl_usdt(r_multiple: float | None, position: RiskPosition) -> float | None:
    """Convierte el resultado en R a USDT usando el riesgo monetario definido."""
    if r_multiple is None or not position.feasible:
        return None
    return float(r_multiple) * position.risk_usdt
