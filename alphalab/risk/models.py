"""Risk-specific data structures.

The proposed-order request and its side enum are the canonical
``alphalab.core.order_request.OrderRequest`` / ``alphalab.core.enums.Side`` --
imported directly by the risk engine, no longer redefined here.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RiskViolation:
    """Immutable record of a breached risk limit."""

    rule: str
    description: str
    severity: str
    current_value: Decimal
    allowed_value: Decimal
