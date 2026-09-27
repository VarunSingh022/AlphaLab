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
    """What a rebalance is estimated to cost, by component.

    ``total_estimated_cost`` is commission, slippage, market impact and spread
    plus the fixed fee. ``estimated_spread`` arrived in v3.8: until then
    ``CostModel.spread_rate`` was read by nothing and every estimate left it
    out, understating the total by exactly that component. It is the last
    field, with a default of zero, so a record built positionally before v3.8
    still builds and still means what it meant.
    """

    portfolio_id: str
    total_trade_value: float
    estimated_commission: float
    estimated_slippage: float
    estimated_market_impact: float
    total_estimated_cost: float
    estimated_spread: float = 0.0
