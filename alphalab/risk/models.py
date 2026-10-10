"""Risk-specific data structures.

The proposed-order request and its side enum are the canonical
``alphalab.core.order_request.OrderRequest`` / ``alphalab.core.enums.Side`` --
imported directly by the risk engine, no longer redefined here.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from alphalab.risk.exceptions import RiskValidationError


class RiskSeverity(StrEnum):
    """How serious a breached limit is.

    A typed value since v3.10: the field was a free string, so ``"HIGH"`` and
    ``"High"`` were two severities and a typo was a third. The values are the
    strings the gate always wrote, so a recorded decision reads back unchanged.
    """

    #: A limit on one order or one position.
    HIGH = "HIGH"
    #: A limit on the account as a whole: margin, drawdown, daily loss.
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class RiskViolation:
    """Immutable record of a breached risk limit.

    ``severity`` is a :class:`RiskSeverity`. The string of one of its values is
    taken as that member -- a record written before v3.10 carries the string --
    and any other value is refused, so a misspelled severity cannot become a
    third, silent one.
    """

    rule: str
    description: str
    severity: RiskSeverity
    current_value: Decimal
    allowed_value: Decimal

    def __post_init__(self) -> None:
        if isinstance(self.severity, RiskSeverity):
            return
        try:
            member = RiskSeverity(self.severity)
        except ValueError:
            raise RiskValidationError(
                f"RiskViolation.severity must be one of "
                f"{[severity.value for severity in RiskSeverity]}, got {self.severity!r}."
            ) from None
        object.__setattr__(self, "severity", member)
