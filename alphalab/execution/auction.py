"""What a venue's reopening auction did, as the run held it (FUT-027, FUT-033).

Kept on :class:`~alphalab.execution.state.ExecutionState`, one per instrument,
so that a run knows an instrument's auction was held -- and at what price, for
what volume -- however little market history it keeps, and a resumed run knows
it too.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

__all__ = ["AuctionReopening"]


@dataclass(frozen=True, slots=True)
class AuctionReopening:
    """An instrument's last reopening auction, as the run held it (FUT-027, FUT-033).

    Recorded at the instrument's first event after a halt reopening by auction
    ends -- whether or not anything crossed -- so a later event is never taken
    for the first, however little market history the run keeps; persisted, so
    a resumed run knows it too.

    Attributes:
        halt_end: The end of the halt it reopened.
        at: The event it was held at.
        price: The price it crossed at, or ``None`` if nothing crossed.
        matched: The volume the venue's book matched at ``price``, its own
            orders and the run's -- ``None`` for an auction read from the print,
            which states no volume.
        imbalance: What the side with more interest at ``price`` had left
            unmatched, signed (buying positive) -- ``None`` likewise.
        extended_to: While the auction is extended (its price was outside the
            band in force): the instant it is held again, and until which
            nothing in the instrument trades. ``None`` once it has been held.
        reason: Why nothing crossed, when nothing did.
    """

    halt_end: float
    at: float
    price: Decimal | None = None
    matched: Decimal | None = None
    imbalance: Decimal | None = None
    extended_to: float | None = None
    reason: str = ""
