"""Core structural definitions for Portfolio instances."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Portfolio:
    """Immutable master record of a managed portfolio entity."""

    portfolio_id: str
    name: str
    base_currency: str
    created_at: float
    is_active: bool = True
