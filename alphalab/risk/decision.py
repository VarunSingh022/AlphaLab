"""Immutable risk decision model generated after evaluation."""

from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.risk.exposure import ExposureStatus
from alphalab.risk.models import RiskViolation


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Deterministic output of a risk evaluation."""

    decision_id: str
    timestamp: float
    order_id: str
    approved: bool
    reason: str
    violations: tuple[RiskViolation, ...] = field(default_factory=tuple)
    required_margin: Decimal = Decimal("0.00")
    remaining_buying_power: Decimal = Decimal("0.00")
    exposure: ExposureStatus = field(default_factory=ExposureStatus)
