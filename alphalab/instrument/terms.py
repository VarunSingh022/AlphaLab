"""What a derivative contract says happens at exercise, assignment, expiry and delivery (FUT-013).

:class:`~alphalab.conventions.economics.InstrumentEconomics` says what one unit
of a contract is worth and how it is paid for; ``InstrumentRecord.last_trade_at``
says when it stops trading. Neither says what the contract *does* -- whether an
option is exercised into the underlying or into cash, against which instrument,
how many units, when it may be exercised, and what the clearing house does with
it at expiry. Until v4.1.1 that was the caller's arithmetic: a contract settled at
a price the caller computed (FUT-012), and nothing could be delivered.

:class:`OptionTerms` and :class:`FutureTerms` are the declaration the execution
path reads instead. They ride on the
:class:`~alphalab.instrument.record.InstrumentRecord` beside ``economics`` and,
like it, outside the identity key: terms describe a contract without changing
what it is.

Nothing is inferred from a country, an exchange or a currency
--------------------------------------------------------------

An equity option in one market is American and physically settled, an index
option on the same venue European and cash-settled, a single-stock option in
another market European and cash-settled, a weekly contract on a third expires at
a different local time on a different weekday. None of that is a property of
the market's name, so every term is **declared**: the exercise style, the
settlement style, the deliverable and its quantity per contract, the expiry
instant, and the automatic-exercise rule the clearing house applies. Nothing has
a default that could stand in for a convention; a term the path needs and the
contract does not declare is refused where it is needed.

Where the instants come from
----------------------------

Every instant here is a UTC instant, as every instant in AlphaLab is. Which
instant a market's "expiry at the close on the third Friday" is depends on its
calendar and time zone, which :class:`~alphalab.data.calendar.MarketCalendar`
owns; :func:`alphalab.runtime.calendars.session_close` and
:func:`~alphalab.runtime.calendars.settles_at` turn a local date and a declared
:class:`~alphalab.conventions.settlement.SettlementRule` into the instant, so a
market whose sessions, holidays and daylight-saving rules differ needs a
different calendar, not different code.

What is supported, and what is refused
---------------------------------------

* Options: American and European exercise; cash or physical settlement; one
  deliverable instrument, in any declared quantity per contract (an adjusted
  contract delivering 150 units against a multiplier of 100 included); an
  automatic-exercise threshold, or none.
* Futures: cash settlement at the final price, or physical delivery of one
  deliverable at the final price.

Not representable, and so not supported: a deliverable of several instruments
or with cash in lieu, a Bermudan or other windowed exercise style, a contract
whose strike is in another currency than the deliverable is paid in. Each would
be a further term, declared, not a branch for a market.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.options.enums import ExerciseStyle, OptionType
from alphalab.options.expiration import SettlementStyle

__all__ = [
    "AutomaticExercise",
    "ContractTerms",
    "Deliverable",
    "FutureTerms",
    "OptionTerms",
    "terms_from_primitives",
    "terms_to_primitives",
]

_ZERO = Decimal("0")


def _instant(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise InstrumentInputError(f"{name} is an instant in Unix seconds, got {value!r}.")


def _positive(value: Any, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= _ZERO:
        raise InstrumentInputError(f"{name} is a positive finite Decimal, got {value!r}.")


@dataclass(frozen=True, slots=True)
class Deliverable:
    """What one contract delivers: ``units`` of the instrument ``asset_id``.

    The underlying's own :class:`~alphalab.instrument.record.InstrumentRecord`
    says what that instrument is -- its currency, its economics, its lot grid.
    Here is only how much of it one contract moves, which is the contract's term
    and frequently not its multiplier once a corporate action has adjusted it.
    """

    asset_id: str
    units: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise InstrumentInputError("A deliverable names the instrument it delivers.")
        _positive(self.units, "A deliverable's units per contract")


@dataclass(frozen=True, slots=True)
class AutomaticExercise:
    """The clearing house's rule at expiry: exercise what is in the money by ``threshold``.

    At expiry every long position whose intrinsic value per unit of price is at
    least ``threshold`` -- and above zero: at the money is never exercised -- is
    exercised, and the short positions are assigned by the same rule, which
    stands in for the holders on the other side. A threshold of zero exercises
    everything in the money. A contract with no rule exercises nothing at expiry
    that was not instructed.
    """

    threshold: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.threshold, Decimal) or not self.threshold.is_finite():
            raise InstrumentInputError(
                f"An exercise threshold is a finite Decimal, got {self.threshold!r}."
            )
        if self.threshold < _ZERO:
            raise InstrumentInputError(
                f"An exercise threshold of {self.threshold} would exercise options out of the "
                "money; it is zero or more."
            )

    def exercises(self, intrinsic: Decimal) -> bool:
        """Whether an option ``intrinsic`` in the money per unit of price is exercised."""

        return intrinsic > _ZERO and intrinsic >= self.threshold


@dataclass(frozen=True, slots=True)
class OptionTerms:
    """An option's exercise, settlement and delivery terms.

    Attributes:
        option_type: Call or put.
        strike: The strike, per unit of price -- the price the contract is
            quoted in, so ``strike x multiplier`` is what one contract pays or
            receives against delivery.
        exercise_style: ``AMERICAN`` -- any instant from ``exercisable_from`` to
            ``expires_at`` -- or ``EUROPEAN`` -- from the last trading instant to
            ``expires_at`` only.
        settlement: ``PHYSICAL`` -- the deliverable moves against the strike --
            or ``CASH`` -- the intrinsic value is paid.
        deliverable: What one contract delivers, or is cash-settled against.
        expires_at: The last instant the contract may be exercised, at or after
            its last trading instant: exercise notices are commonly accepted
            after trading has stopped.
        automatic_exercise: The clearing house's rule at expiry, or ``None``.
        exercisable_from: For an American contract, the first instant it may be
            exercised; ``None`` means from whenever it is held. Refused for a
            European contract, whose window is fixed.
    """

    option_type: OptionType
    strike: Decimal
    exercise_style: ExerciseStyle
    settlement: SettlementStyle
    deliverable: Deliverable
    expires_at: float
    automatic_exercise: AutomaticExercise | None
    exercisable_from: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.option_type, OptionType):
            raise InstrumentInputError(f"option_type is an OptionType, got {self.option_type!r}.")
        _positive(self.strike, "A strike")
        if not isinstance(self.exercise_style, ExerciseStyle):
            raise InstrumentInputError(
                f"exercise_style is an ExerciseStyle, got {self.exercise_style!r}."
            )
        if not isinstance(self.settlement, SettlementStyle):
            raise InstrumentInputError(f"settlement is a SettlementStyle, got {self.settlement!r}.")
        if not isinstance(self.deliverable, Deliverable):
            raise InstrumentInputError(f"deliverable is a Deliverable, got {self.deliverable!r}.")
        _instant(self.expires_at, "expires_at")
        if self.automatic_exercise is not None and not isinstance(
            self.automatic_exercise, AutomaticExercise
        ):
            raise InstrumentInputError(
                f"automatic_exercise is an AutomaticExercise or None, got "
                f"{self.automatic_exercise!r}."
            )
        if self.exercisable_from is not None:
            if self.exercise_style is not ExerciseStyle.AMERICAN:
                raise InstrumentInputError(
                    f"A {self.exercise_style.name} option is exercised at expiry only; an "
                    "exercisable_from is an American contract's term."
                )
            _instant(self.exercisable_from, "exercisable_from")
            if self.exercisable_from > self.expires_at:
                raise InstrumentInputError(
                    f"An option exercisable from {self.exercisable_from!r} expires at "
                    f"{self.expires_at!r}: it could never be exercised."
                )

    def exercise_window(self, last_trade_at: float) -> tuple[float, float]:
        """The instants an instruction to exercise may take effect in, inclusive."""

        if self.exercise_style is ExerciseStyle.EUROPEAN:
            return last_trade_at, self.expires_at
        start = -math.inf if self.exercisable_from is None else self.exercisable_from
        return start, self.expires_at

    def intrinsic(
        self, underlying_value: Decimal, multiplier: Decimal, deliverable_multiplier: Decimal
    ) -> Decimal:
        """Intrinsic value per unit of the contract's price, never negative.

        One contract delivers ``units`` of the deliverable, each worth its quoted
        ``underlying_value`` times the deliverable's own multiplier, against
        ``strike x multiplier``; per unit of price that is
        ``max(S x units x m_deliverable / m - K, 0)`` for a call and its mirror for
        a put. For a share option delivering as many shares as its multiplier,
        and for an option on one future with the future's multiplier, it is
        ``max(S - K, 0)``.
        """

        per_price_unit = (
            underlying_value * self.deliverable.units * deliverable_multiplier / multiplier
        )
        if self.option_type is OptionType.CALL:
            return max(per_price_unit - self.strike, _ZERO)
        return max(self.strike - per_price_unit, _ZERO)


@dataclass(frozen=True, slots=True)
class FutureTerms:
    """A future's settlement at expiry.

    Attributes:
        settlement: ``CASH`` -- closed at the final settlement price, as every
            future was before v4.1.1 -- or ``PHYSICAL`` -- closed there, and the
            deliverable then moves against the final price.
        deliverable: What one contract delivers; required for ``PHYSICAL`` and
            refused for ``CASH``.
    """

    settlement: SettlementStyle
    deliverable: Deliverable | None

    def __post_init__(self) -> None:
        if not isinstance(self.settlement, SettlementStyle):
            raise InstrumentInputError(f"settlement is a SettlementStyle, got {self.settlement!r}.")
        if self.settlement is SettlementStyle.PHYSICAL and self.deliverable is None:
            raise InstrumentInputError(
                "A physically settled future names its deliverable; nothing can be delivered "
                "without one."
            )
        if self.settlement is SettlementStyle.CASH and self.deliverable is not None:
            raise InstrumentInputError(
                "A cash-settled future delivers nothing; a deliverable would never move."
            )
        if self.deliverable is not None and not isinstance(self.deliverable, Deliverable):
            raise InstrumentInputError(f"deliverable is a Deliverable, got {self.deliverable!r}.")


type ContractTerms = OptionTerms | FutureTerms


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def _deliverable_out(deliverable: Deliverable | None) -> dict[str, str] | None:
    if deliverable is None:
        return None
    return {"asset_id": deliverable.asset_id, "units": str(deliverable.units)}


def terms_to_primitives(terms: ContractTerms | None) -> dict[str, Any] | None:
    """The terms as plain primitives, enum members by name, amounts as text."""

    if terms is None:
        return None
    if isinstance(terms, FutureTerms):
        return {
            "kind": "future",
            "settlement": terms.settlement.name,
            "deliverable": _deliverable_out(terms.deliverable),
        }
    rule = terms.automatic_exercise
    return {
        "kind": "option",
        "option_type": terms.option_type.name,
        "strike": str(terms.strike),
        "exercise_style": terms.exercise_style.name,
        "settlement": terms.settlement.name,
        "deliverable": _deliverable_out(terms.deliverable),
        "expires_at": terms.expires_at,
        "automatic_exercise": None if rule is None else str(rule.threshold),
        "exercisable_from": terms.exercisable_from,
    }


def _deliverable_in(value: Any) -> Deliverable | None:
    if value is None:
        return None
    return Deliverable(str(value["asset_id"]), Decimal(value["units"]))


def terms_from_primitives(value: Any, where: str = "terms") -> ContractTerms | None:
    """Read terms :func:`terms_to_primitives` wrote.

    Raises:
        InstrumentInputError: If the value is not one of the two shapes, names an
            enum member this build does not have, or states terms that do not
            validate.
    """

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise InstrumentInputError(f"{where} are not contract terms: {value!r}.")
    try:
        kind = value["kind"]
        if kind == "future":
            return FutureTerms(
                SettlementStyle[value["settlement"]], _deliverable_in(value["deliverable"])
            )
        if kind == "option":
            rule = value["automatic_exercise"]
            start = value["exercisable_from"]
            deliverable = _deliverable_in(value["deliverable"])
            if deliverable is None:
                raise InstrumentInputError(f"{where} declare an option with no deliverable.")
            return OptionTerms(
                option_type=OptionType[value["option_type"]],
                strike=Decimal(value["strike"]),
                exercise_style=ExerciseStyle[value["exercise_style"]],
                settlement=SettlementStyle[value["settlement"]],
                deliverable=deliverable,
                expires_at=float(value["expires_at"]),
                automatic_exercise=None if rule is None else AutomaticExercise(Decimal(rule)),
                exercisable_from=None if start is None else float(start),
            )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise InstrumentInputError(f"{where} are not contract terms: {exc}.") from exc
    raise InstrumentInputError(f"{where} are of kind {value.get('kind')!r}, not option or future.")
