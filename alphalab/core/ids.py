"""UUID-backed identifiers for core domain models."""

from typing import NewType
from uuid import UUID, uuid4

from alphalab.core.exceptions import DomainValidationError

AccountId = NewType("AccountId", str)
AssetId = NewType("AssetId", str)
EventId = NewType("EventId", str)
FillId = NewType("FillId", str)
OrderId = NewType("OrderId", str)
PortfolioId = NewType("PortfolioId", str)
PositionId = NewType("PositionId", str)
SignalId = NewType("SignalId", str)
StrategyId = NewType("StrategyId", str)
TradeId = NewType("TradeId", str)


def new_uuid() -> str:
    """Create a canonical UUID4 string.

    Returns:
        A lowercase UUID4 string suitable for storing in typed domain IDs.
    """

    return str(uuid4())


def new_account_id() -> AccountId:
    """Create a UUID-backed account identifier."""

    return AccountId(new_uuid())


def new_asset_id() -> AssetId:
    """Create a UUID-backed asset identifier."""

    return AssetId(new_uuid())


def new_event_id() -> EventId:
    """Create a UUID-backed event identifier."""

    return EventId(new_uuid())


def new_fill_id() -> FillId:
    """Create a UUID-backed fill identifier."""

    return FillId(new_uuid())


def new_order_id() -> OrderId:
    """Create a UUID-backed order identifier."""

    return OrderId(new_uuid())


def new_portfolio_id() -> PortfolioId:
    """Create a UUID-backed portfolio identifier."""

    return PortfolioId(new_uuid())


def new_position_id() -> PositionId:
    """Create a UUID-backed position identifier."""

    return PositionId(new_uuid())


def new_signal_id() -> SignalId:
    """Create a UUID-backed signal identifier."""

    return SignalId(new_uuid())


def new_strategy_id() -> StrategyId:
    """Create a UUID-backed strategy identifier."""

    return StrategyId(new_uuid())


def new_trade_id() -> TradeId:
    """Create a UUID-backed trade identifier."""

    return TradeId(new_uuid())


def validate_uuid_id(value: str, field_name: str) -> None:
    """Validate that a value is a UUID string.

    Args:
        value: Candidate UUID string.
        field_name: Name of the field being validated.

    Raises:
        DomainValidationError: If the value is not a non-empty UUID string.
    """

    if not isinstance(value, str) or not value:
        raise DomainValidationError(f"{field_name} must be a non-empty UUID string")

    try:
        UUID(value)
    except ValueError as exc:
        raise DomainValidationError(f"{field_name} must be a valid UUID string") from exc
