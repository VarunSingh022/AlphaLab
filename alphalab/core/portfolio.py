"""Portfolio state domain model."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from alphalab.core.exceptions import DomainValidationError
from alphalab.core.ids import AccountId, PortfolioId, validate_uuid_id
from alphalab.core.position import Position


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """Immutable snapshot of portfolio-level state.

    Attributes:
        portfolio_id: Unique portfolio identifier.
        account_id: Account associated with the portfolio.
        base_currency: Three-letter uppercase ISO-style reporting currency.
        cash: Cash balance in base currency.
        equity: Non-negative total equity in base currency.
        realized_pnl: Realized profit and loss in base currency.
        unrealized_pnl: Unrealized profit and loss in base currency.
        updated_at: Time this snapshot was captured. The timestamp must be timezone-aware.
        positions: Immutable collection of position snapshots.
    """

    portfolio_id: PortfolioId
    account_id: AccountId
    base_currency: str
    cash: Decimal
    equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    updated_at: datetime
    positions: tuple[Position, ...] = ()

    def __post_init__(self) -> None:
        validate_uuid_id(self.portfolio_id, "portfolio_id")
        validate_uuid_id(self.account_id, "account_id")
        if len(self.base_currency) != 3 or not self.base_currency.isalpha():
            raise DomainValidationError("base_currency must be a three-letter code")
        if self.base_currency != self.base_currency.upper():
            raise DomainValidationError("base_currency must be uppercase")
        if self.equity < Decimal("0"):
            raise DomainValidationError("equity must be non-negative")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise DomainValidationError("updated_at must be timezone-aware")
        if not isinstance(self.positions, tuple):
            raise DomainValidationError("positions must be a tuple")
        asset_ids = set()
        for position in self.positions:
            if not isinstance(position, Position):
                raise DomainValidationError("positions must contain Position instances")
            if position.asset_id in asset_ids:
                raise DomainValidationError("positions cannot contain duplicate asset_id values")
            asset_ids.add(position.asset_id)
