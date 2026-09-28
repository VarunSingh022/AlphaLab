"""Immutable risk decision model generated after evaluation."""

from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.risk.exposure import ExposureStatus
from alphalab.risk.models import RiskViolation


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Deterministic output of a risk evaluation.

    Attributes:
        violations: The limits this order would breach -- why it was refused.
        required_margin: The base-currency value of the part of the order that
            grows a position: what it commits of the account's buying power.
            Zero for a reducing order.
        remaining_buying_power: What is left after this order, if approved.
        breaches: The account-level limits (drawdown, daily loss) the account
            is in breach of when the order is judged, **whether or not the order
            was refused**. A reducing order is approved during a breach -- it
            is how a breach is closed -- and the decision still says the
            account is in one.
    """

    decision_id: str
    timestamp: float
    order_id: str
    approved: bool
    reason: str
    violations: tuple[RiskViolation, ...] = field(default_factory=tuple)
    required_margin: Decimal = Decimal("0.00")
    remaining_buying_power: Decimal = Decimal("0.00")
    exposure: ExposureStatus = field(default_factory=ExposureStatus)
    breaches: tuple[RiskViolation, ...] = field(default_factory=tuple)
