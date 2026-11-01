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
        buy_in_at: A recall's deadline (v4.2.0, ledger FUT-022): from this
            instant a short beyond ``available`` is bought in -- covered through
            the execution path at the market's prices, as far as the market
            allows -- or ``None``, a short already held beyond it is kept and
            only new shorts are refused. At or after ``effective_at``, the
            notice; never on an entry stating no limit.
        buy_in_premium: What the lender charges on a buy-in beyond its cost
            (phase 4, ledger FUT-035), a fraction of the bought-in value,
            booked as a fee with each fill of it; ``None`` charges none.
        buy_in_fee: A fixed penalty per buy-in order, in the instrument's
            currency, booked with its first fill; ``None`` charges none.
        allocations: The units located for each named strategy, out of
            ``available`` (FUT-035): what each may be short of its own; what
            is left is shared by every strategy, first come. Empty: the book's
            borrow, shared and netted across strategies, as before.
    """

    effective_at: float
    available: Decimal | None
    annual_rate: Decimal
    buy_in_at: float | None = None
    buy_in_premium: Decimal | None = None
    buy_in_fee: Decimal | None = None
    allocations: tuple[tuple[str, Decimal], ...] = ()

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
        if self.buy_in_at is not None:
            if (
                isinstance(self.buy_in_at, bool)
                or not isinstance(self.buy_in_at, int | float)
                or not math.isfinite(self.buy_in_at)
            ):
                raise InstrumentInputError(
                    f"A buy-in deadline is an instant, got {self.buy_in_at!r}."
                )
            if self.buy_in_at < self.effective_at:
                raise InstrumentInputError(
                    f"A recall's buy-in deadline {self.buy_in_at!r} is before its notice "
                    f"{self.effective_at!r}: a short is told before it is bought in."
                )
            if self.available is None:
                raise InstrumentInputError(
                    "A buy-in recalls a short beyond what is available; an entry stating no "
                    "limit recalls nothing."
                )
            object.__setattr__(self, "buy_in_at", float(self.buy_in_at))
        for name in ("buy_in_premium", "buy_in_fee"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, Decimal) or not value.is_finite() or value < _ZERO
            ):
                raise InstrumentInputError(
                    f"A buy-in's {name.removeprefix('buy_in_')} is a non-negative Decimal or "
                    f"None, got {value!r}."
                )
        allocations = tuple(self.allocations)
        if allocations:
            if self.available is None:
                raise InstrumentInputError(
                    "Borrow is located per strategy out of what is available; an entry stating "
                    "no limit has nothing to allocate."
                )
            names = [name for name, _ in allocations]
            if len(set(names)) != len(names) or not all(isinstance(n, str) and n for n in names):
                raise InstrumentInputError(
                    f"A borrow entry locates for each strategy once, got {names}."
                )
            for name, units in allocations:
                if not isinstance(units, Decimal) or not units.is_finite() or units < _ZERO:
                    raise InstrumentInputError(
                        f"The borrow located for {name} is a non-negative Decimal, got {units!r}."
                    )
            located = sum((units for _, units in allocations), _ZERO)
            if located > self.available:
                raise InstrumentInputError(
                    f"The borrow located for strategies, {located}, is more than the "
                    f"{self.available} available: a unit is lent to one of them, never two."
                )
            object.__setattr__(self, "allocations", tuple(sorted(allocations)))

    @property
    def shared(self) -> Decimal | None:
        """What is available beyond every strategy's own allocation, or ``None`` for no limit."""

        if self.available is None:
            return None
        return self.available - sum((units for _, units in self.allocations), _ZERO)

    def allocated(self, strategy_id: str) -> Decimal:
        """The units located for ``strategy_id``: zero for one the entry does not name."""

        for name, units in self.allocations:
            if name == strategy_id:
                return units
        return _ZERO


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

    @property
    def recalls(self) -> bool:
        """Whether any entry declares a buy-in deadline (FUT-022)."""

        return any(entry.buy_in_at is not None for entry in self.entries)

    @property
    def per_strategy(self) -> bool:
        """Whether any entry locates borrow per strategy (FUT-035)."""

        return any(entry.allocations for entry in self.entries)

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
                # Written only when declared, so terms without a recall keep
                # the form they had.
                **({} if entry.buy_in_at is None else {"buy_in_at": entry.buy_in_at}),
                # Phase 4 (FUT-035): likewise.
                **(
                    {}
                    if entry.buy_in_premium is None
                    else {"buy_in_premium": str(entry.buy_in_premium)}
                ),
                **({} if entry.buy_in_fee is None else {"buy_in_fee": str(entry.buy_in_fee)}),
                **(
                    {}
                    if not entry.allocations
                    else {"allocations": [[name, str(q)] for name, q in entry.allocations]}
                ),
            }
            for entry in terms.entries
        ],
    }


def _optional(value: Any) -> Decimal | None:
    return None if value is None else Decimal(value)


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
                    None if entry.get("buy_in_at") is None else float(entry["buy_in_at"]),
                    _optional(entry.get("buy_in_premium")),
                    _optional(entry.get("buy_in_fee")),
                    tuple((name, Decimal(q)) for name, q in entry.get("allocations", ())),
                )
                for entry in value["entries"]
            ),
            DayCount[value["day_count"]],
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise InstrumentInputError(f"{where} is not borrow terms: {exc}.") from exc
