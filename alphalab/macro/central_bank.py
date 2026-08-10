"""Central bank policy rate decisions."""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.macro.enums import PolicyAction
from alphalab.macro.exceptions import MacroInputError


@dataclass(frozen=True, slots=True)
class CentralBankEvent:
    """A single central bank policy rate decision.

    Attributes:
        bank_id: Identifier of the central bank, e.g. "FED", "ECB", "RBI".
        event_date: Unix timestamp of the decision.
        action: Whether the bank hiked, cut, or held its policy rate.
        rate_before: Policy rate immediately before this decision.
        rate_after: Policy rate immediately after this decision.
        next_meeting_date: Unix timestamp of the next scheduled decision, if
            announced.
    """

    bank_id: str
    event_date: float
    action: PolicyAction
    rate_before: Decimal
    rate_after: Decimal
    next_meeting_date: float | None = None

    def __post_init__(self) -> None:
        if self.action is PolicyAction.HIKE and self.rate_after <= self.rate_before:
            raise MacroInputError(
                f"action is HIKE but rate_after ({self.rate_after}) is not greater "
                f"than rate_before ({self.rate_before})."
            )
        if self.action is PolicyAction.CUT and self.rate_after >= self.rate_before:
            raise MacroInputError(
                f"action is CUT but rate_after ({self.rate_after}) is not less "
                f"than rate_before ({self.rate_before})."
            )
        if self.action is PolicyAction.HOLD and self.rate_after != self.rate_before:
            raise MacroInputError(
                f"action is HOLD but rate_after ({self.rate_after}) differs from "
                f"rate_before ({self.rate_before})."
            )


def rate_change_bps(event: CentralBankEvent) -> int:
    """Computes the rate change in basis points (positive for a hike, negative for a cut)."""
    return int((event.rate_after - event.rate_before) * Decimal("10000"))
