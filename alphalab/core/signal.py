"""Trading signal domain model."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from alphalab.core.enums import Side
from alphalab.core.exceptions import DomainValidationError
from alphalab.core.ids import AssetId, SignalId, StrategyId, validate_uuid_id


@dataclass(frozen=True, slots=True)
class Signal:
    """Immutable strategy signal for a single asset.

    Attributes:
        signal_id: Unique signal identifier.
        strategy_id: Strategy that produced the signal.
        asset_id: Asset targeted by the signal.
        side: Direction implied by the signal.
        confidence: Confidence score in the inclusive range [0, 1].
        generated_at: Time the signal was generated. The timestamp must be timezone-aware.
        expires_at: Optional expiration time. When present, it must be after generated_at.
    """

    signal_id: SignalId
    strategy_id: StrategyId
    asset_id: AssetId
    side: Side
    confidence: Decimal
    generated_at: datetime
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_uuid_id(self.signal_id, "signal_id")
        validate_uuid_id(self.strategy_id, "strategy_id")
        validate_uuid_id(self.asset_id, "asset_id")
        if not isinstance(self.side, Side):
            raise DomainValidationError("side must be a Side")
        if self.confidence < Decimal("0") or self.confidence > Decimal("1"):
            raise DomainValidationError("confidence must be between 0 and 1")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise DomainValidationError("generated_at must be timezone-aware")
        if self.expires_at is not None:
            if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
                raise DomainValidationError("expires_at must be timezone-aware")
            if self.expires_at <= self.generated_at:
                raise DomainValidationError("expires_at must be after generated_at")
