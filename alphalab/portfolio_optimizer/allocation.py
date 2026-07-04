"""Immutable capital allocation models."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CapitalAllocation:
    """Immutable snapshot of portfolio capital structuring."""

    portfolio_id: str
    total_capital: float
    invested_capital: float
    cash_balance: float
    margin_used: float
    leverage_ratio: float
