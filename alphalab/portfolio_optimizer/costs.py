"""Deterministic transaction cost estimation models."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CostModel:
    commission_rate: float
    slippage_rate: float
    spread_rate: float
    market_impact_rate: float
    fixed_exchange_fee: float


@dataclass(frozen=True, slots=True)
class TransactionCostEstimate:
    portfolio_id: str
    total_trade_value: float
    estimated_commission: float
    estimated_slippage: float
    estimated_market_impact: float
    total_estimated_cost: float
