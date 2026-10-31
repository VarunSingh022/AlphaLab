"""What borrowing an instrument to sell it short costs, and how much is available (ledger FUT-018).

A short sale borrows the units it sells, and a lender states how many it can
lend and at what fee -- per instrument, changing from day to day, a hard-to-borrow
name dearer and scarcer than general collateral. Each is a fact a lender
publishes, so it is **declared**, per instrument, on its
:class:`~alphalab.instrument.record.InstrumentRecord` as a dated series: each
:class:`BorrowAvailability` takes effect at its own instant and stays in force
until the next, so a run reads at any instant only what was in effect by then --
nothing is assumed available that was not declared, and nothing later is seen.

What the execution path does with it: an order that would leave the book short
more than is available in effect at its instant is refused before it is placed,
and a :class:`~alphalab.backtesting.schedule.ScheduledBorrowFee` accrues the fee
in effect on every short position for the period it names. An instrument
declaring no borrow terms is shorted as before v4.2.0 -- when the run allows
shorting -- and costs no fee.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Any

from alphalab.conventions.daycount import DayCount
from alphalab.instrument.exceptions import InstrumentInputError

__all__ = [
    "BorrowAvailability",
    "BorrowTerms",
    "borrow_from_primitives",
    "borrow_to_primitives",
]

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class BorrowAvailability:
    """What a lender offers from one instant until the next entry.

    Attributes:
        effective_at: When it takes effect, in Unix seconds.
        available: How many units the book may be short in all, or ``None`` when
            the lender states no limit -- general collateral, declared as such.
            Zero is a name nothing can be borrowed in.
        annual_rate: The borrow fee, a fraction of the borrowed value a year;
            at least zero.
    """

    effective_at: float
    available: Decimal | None
    annual_rate: Decimal

    def __post_init__(self) -> None:
        if (
            isinstance(self.effective_at, bool)
            or not isinstance(self.effective_at, int | float)
            or not math.isfinite(self.effective_at)
        ):
            raise InstrumentInputError(
                f"A borrow entry takes effect at an instant, got {self.effective_at!r}."
            )
        if self.available is not None and (
            not isinstance(self.available, Decimal)
            or not self.available.is_finite()
            or self.available < _ZERO
        ):
            raise InstrumentInputError(
                f"Borrow availability is a non-negative Decimal or None, got {self.available!r}."
            )
        if (
            not isinstance(self.annual_rate, Decimal)
            or not self.annual_rate.is_finite()
            or self.annual_rate < _ZERO
        ):
            raise InstrumentInputError(
                f"A borrow fee is a non-negative Decimal, got {self.annual_rate!r}."
            )


@dataclass(frozen=True, slots=True)
class BorrowTerms:
    """An instrument's borrow, as a dated series of what a lender offers.

    Attributes:
        entries: At least one, strictly increasing in ``effective_at``.
        day_count: How a fee's period is turned into a fraction of a year.
    """

    entries: tuple[BorrowAvailability, ...]
    day_count: DayCount

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        if not entries or not all(isinstance(e, BorrowAvailability) for e in entries):
            raise InstrumentInputError("Borrow terms declare at least one BorrowAvailability.")
        if any(b.effective_at <= a.effective_at for a, b in pairwise(entries)):
            raise InstrumentInputError(
                "Borrow entries take effect in strictly increasing order: two in force at one "
                "instant would be two answers to what is available."
            )
        if not isinstance(self.day_count, DayCount):
            raise InstrumentInputError(f"day_count is a DayCount, got {self.day_count!r}.")
        object.__setattr__(self, "entries", entries)

    def in_effect(self, at: float) -> BorrowAvailability | None:
        """The entry in force at ``at``, or ``None`` before the first takes effect."""

        found = None
        for entry in self.entries:
            if entry.effective_at > at:
                break
            found = entry
        return found


def borrow_to_primitives(terms: BorrowTerms | None) -> dict[str, Any] | None:
    """The terms as plain primitives: amounts as text, the day count by name."""

    if terms is None:
        return None
    return {
        "day_count": terms.day_count.name,
        "entries": [
            {
                "effective_at": entry.effective_at,
                "available": None if entry.available is None else str(entry.available),
                "annual_rate": str(entry.annual_rate),
            }
            for entry in terms.entries
        ],
    }


def borrow_from_primitives(value: Any, where: str = "borrow") -> BorrowTerms | None:
    """Read terms :func:`borrow_to_primitives` wrote.

    Raises:
        InstrumentInputError: If the value is not that shape or does not validate.
    """

    if value is None:
        return None
    try:
        return BorrowTerms(
            tuple(
                BorrowAvailability(
                    float(entry["effective_at"]),
                    None if entry["available"] is None else Decimal(entry["available"]),
                    Decimal(entry["annual_rate"]),
                )
                for entry in value["entries"]
            ),
            DayCount[value["day_count"]],
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise InstrumentInputError(f"{where} is not borrow terms: {exc}.") from exc
