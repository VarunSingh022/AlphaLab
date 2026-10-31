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

* Options: American, European and -- since v4.2.0 (ledger FUT-016) -- Bermudan
  exercise in declared windows; cash or physical settlement; a deliverable of one
  instrument or a basket of several, each in any declared quantity per contract
  (an adjusted contract delivering 150 units against a multiplier of 100
  included), with a cash component per contract if the contract has one; a
  fractional delivery settled in cash in lieu, by a declared rule; a
  consideration -- the strike and the cash component -- in a declared currency
  other than the deliverable's, converted at the exercise instant by the run's
  own rates; adjusted terms from a declared instant, such as an exchange's
  adjustment for a split or a merger; an automatic-exercise threshold, or none.
* Futures: cash settlement at the final price, or physical delivery of one
  deliverable at the final price.

Refused, each where it would be needed: a basket or a cash component valued
against one underlying value (each component's value is stated instead); a
fractional delivery a contract declares no rule for; a split of a deliverable a
held contract declares no adjustment for. A contract another rule governs is a
further term, declared, not a branch for a market.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Any

from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.options.enums import ExerciseStyle, OptionType
from alphalab.options.expiration import SettlementStyle

__all__ = [
    "AutomaticExercise",
    "CashInLieu",
    "ContractTerms",
    "Deliverable",
    "FutureTerms",
    "OptionTerms",
    "TermsAdjustment",
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


def _non_negative(value: Any, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < _ZERO:
        raise InstrumentInputError(f"{name} is a finite Decimal of zero or more, got {value!r}.")


def _basket(deliverable: Deliverable, basket: Any, where: str) -> None:
    if not isinstance(basket, tuple) or not all(isinstance(d, Deliverable) for d in basket):
        raise InstrumentInputError(f"{where}'s basket is a tuple of Deliverables, got {basket!r}.")
    named = [deliverable.asset_id, *(d.asset_id for d in basket)]
    if len(set(named)) != len(named):
        raise InstrumentInputError(
            f"{where} names {sorted(named)}: a deliverable appears once, with all its units."
        )


@dataclass(frozen=True, slots=True)
class CashInLieu:
    """How a fraction of a deliverable unit is settled: in cash, instead of delivered.

    A contract adjusted by a reverse split can deliver 33.33 shares a contract;
    no book can hold a third of a share that trades in whole ones. The whole
    units -- whole lots of the deliverable's declared lot grid, or whole units
    when the run trades them -- are delivered, and the fraction is paid in cash
    at ``price`` per unit, or, with no price, at the last price the run observed
    for the deliverable at the delivery's instant (refused when it has none).

    Attributes:
        price: The price per unit the fraction is settled at, in the
            deliverable's currency, as the contract's adjustment states it; or
            ``None`` for the last observed price.
    """

    price: Decimal | None = None

    def __post_init__(self) -> None:
        if self.price is not None:
            _positive(self.price, "A cash-in-lieu price")


@dataclass(frozen=True, slots=True)
class TermsAdjustment:
    """What a contract delivers from ``effective_at`` on, as its exchange adjusted it.

    A corporate action on the deliverable changes the contract without changing
    what it is: a two-for-one split makes each contract deliver twice the units
    at the same total strike; a merger replaces the deliverable with the
    acquirer's shares and cash. The adjusted terms are a published fact with an
    effective instant, so they are declared on the contract, not computed by the
    engine, and take effect only from that instant -- an instruction before it
    reads the terms before it. :meth:`OptionTerms.split_adjusted` writes the
    adjustment a split implies.

    Attributes:
        effective_at: The first instant the adjusted terms apply.
        strike: The strike from then on.
        deliverable: The (first) deliverable from then on.
        basket: Any further deliverables from then on.
        cash_per_contract: The cash component per contract from then on.
        reason: The caller's description, not interpreted.
    """

    effective_at: float
    strike: Decimal
    deliverable: Deliverable
    basket: tuple[Deliverable, ...] = ()
    cash_per_contract: Decimal = _ZERO
    reason: str = ""

    def __post_init__(self) -> None:
        _instant(self.effective_at, "An adjustment's effective_at")
        _positive(self.strike, "An adjusted strike")
        if not isinstance(self.deliverable, Deliverable):
            raise InstrumentInputError(f"deliverable is a Deliverable, got {self.deliverable!r}.")
        _basket(self.deliverable, self.basket, "An adjustment")
        _non_negative(self.cash_per_contract, "An adjusted cash component")


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
        basket: Further deliverables, delivered with ``deliverable`` per
            contract -- an adjusted contract after a merger, a basket option
            (v4.2.0, FUT-016). Empty for a single deliverable.
        cash_per_contract: A cash component of the deliverable package per
            contract, in the consideration currency: received by a call's
            holder, delivered by a put's. Zero when there is none.
        consideration_currency: The currency the strike and the cash component
            are paid in, when it is not the deliverable's; converted at the
            exercise instant by the run's rates. ``None``: the deliverable's own,
            as every contract before v4.2.0.
        consideration_rate: For a quanto contract, the conversion rate the
            contract fixes -- units of the deliverables' currency per unit of the
            consideration currency -- used instead of the run's spot rate; ``None``
            to convert at the run's rate at the exercise instant. Only with a
            ``consideration_currency``.
        fractions: How a fraction of a deliverable unit is settled, or ``None``
            to refuse a fractional delivery.
        exercise_windows: For a ``BERMUDAN`` contract, the ``(start, end)``
            windows it may be exercised in, inclusive, in order and not
            overlapping, the last ending at ``expires_at``; empty for every
            other style.
        adjustments: The contract's adjusted terms, in effective order -- see
            :class:`TermsAdjustment`.
    """

    option_type: OptionType
    strike: Decimal
    exercise_style: ExerciseStyle
    settlement: SettlementStyle
    deliverable: Deliverable
    expires_at: float
    automatic_exercise: AutomaticExercise | None
    exercisable_from: float | None = None
    basket: tuple[Deliverable, ...] = ()
    cash_per_contract: Decimal = _ZERO
    consideration_currency: str | None = None
    fractions: CashInLieu | None = None
    exercise_windows: tuple[tuple[float, float], ...] = ()
    consideration_rate: Decimal | None = None
    adjustments: tuple[TermsAdjustment, ...] = ()

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
        _basket(self.deliverable, self.basket, "An option")
        _non_negative(self.cash_per_contract, "A cash component per contract")
        if self.consideration_currency is not None and (
            not isinstance(self.consideration_currency, str)
            or not self.consideration_currency.strip()
        ):
            raise InstrumentInputError(
                f"consideration_currency is a currency code or None, got "
                f"{self.consideration_currency!r}."
            )
        if self.fractions is not None and not isinstance(self.fractions, CashInLieu):
            raise InstrumentInputError(
                f"fractions is a CashInLieu or None, got {self.fractions!r}."
            )
        if self.consideration_rate is not None:
            _positive(self.consideration_rate, "A quanto contract's fixed rate")
            if self.consideration_currency is None:
                raise InstrumentInputError(
                    "A fixed consideration rate converts a consideration_currency; the contract "
                    "names none."
                )
        self._require_windows()
        self._require_adjustments()

    def _require_windows(self) -> None:
        windows = self.exercise_windows
        if self.exercise_style is not ExerciseStyle.BERMUDAN:
            if windows:
                raise InstrumentInputError(
                    f"A {self.exercise_style.name} option has no exercise windows; they are a "
                    "Bermudan contract's term."
                )
            return
        if not isinstance(windows, tuple) or not windows:
            raise InstrumentInputError(
                "A Bermudan option declares the windows it may be exercised in; it has none."
            )
        previous_end = -math.inf
        for window in windows:
            if not isinstance(window, tuple) or len(window) != 2:
                raise InstrumentInputError(f"An exercise window is (start, end), got {window!r}.")
            start, end = window
            _instant(start, "An exercise window's start")
            _instant(end, "An exercise window's end")
            if not previous_end < start <= end:
                raise InstrumentInputError(
                    f"Exercise windows are in order and do not overlap; {window!r} follows one "
                    f"ending at {previous_end!r}."
                )
            previous_end = end
        if previous_end != self.expires_at:
            raise InstrumentInputError(
                f"A Bermudan option's last window ends when it expires, at {self.expires_at!r}, "
                f"not at {previous_end!r}."
            )

    def _require_adjustments(self) -> None:
        if not isinstance(self.adjustments, tuple) or not all(
            isinstance(a, TermsAdjustment) for a in self.adjustments
        ):
            raise InstrumentInputError(
                f"adjustments is a tuple of TermsAdjustments, got {self.adjustments!r}."
            )
        instants = [a.effective_at for a in self.adjustments]
        if instants != sorted(set(instants)):
            raise InstrumentInputError(
                f"A contract's adjustments take effect one at a time, in order; got {instants}."
            )
        if instants and instants[-1] > self.expires_at:
            raise InstrumentInputError(
                f"An adjustment effective at {instants[-1]!r} follows the contract's expiry at "
                f"{self.expires_at!r}."
            )

    @property
    def deliverables(self) -> tuple[Deliverable, ...]:
        """Everything one contract delivers: the deliverable, then the basket."""

        return (self.deliverable, *self.basket)

    @property
    def is_single(self) -> bool:
        """Whether the contract delivers one instrument and nothing else, in its own currency.

        The shape every contract had before v4.2.0, booked exactly as it was.
        """

        return (
            not self.basket
            and self.cash_per_contract == _ZERO
            and self.consideration_currency is None
            and self.fractions is None
        )

    def effective(self, at: float) -> OptionTerms:
        """The terms in force at ``at``: the latest adjustment effective by then, or these."""

        applied = None
        for adjustment in self.adjustments:
            if adjustment.effective_at <= at:
                applied = adjustment
        if applied is None:
            return self
        return replace(
            self,
            strike=applied.strike,
            deliverable=applied.deliverable,
            basket=applied.basket,
            cash_per_contract=applied.cash_per_contract,
            adjustments=(),
        )

    def split_adjusted(self, asset_id: str, ratio: Decimal, at: float) -> OptionTerms:
        """These terms with the adjustment a ``ratio`` split of ``asset_id`` at ``at`` implies.

        Each contract delivers ``ratio`` times the units of the split instrument
        and the same everything else -- the same total strike for a package
        worth the same: the adjustment that preserves the contract's economics
        for any ratio, a non-integer one included, without changing how many
        contracts are held.

        Raises:
            InstrumentInputError: If the terms in force at ``at`` do not deliver
                ``asset_id``, or the ratio is not positive.
        """

        _positive(ratio, "A split ratio")
        current = self.effective(at)
        if asset_id not in {d.asset_id for d in current.deliverables}:
            raise InstrumentInputError(
                f"The contract does not deliver {asset_id} at {at!r}; a split of it adjusts "
                "nothing here."
            )

        def scaled(d: Deliverable) -> Deliverable:
            return Deliverable(d.asset_id, d.units * ratio) if d.asset_id == asset_id else d

        adjustment = TermsAdjustment(
            effective_at=at,
            strike=current.strike,
            deliverable=scaled(current.deliverable),
            basket=tuple(scaled(d) for d in current.basket),
            cash_per_contract=current.cash_per_contract,
            reason=f"{ratio} split of {asset_id}",
        )
        return replace(self, adjustments=(*self.adjustments, adjustment))

    def may_exercise(self, at: float, last_trade_at: float) -> bool:
        """Whether an instruction to exercise may take effect at ``at``."""

        if self.exercise_style is ExerciseStyle.BERMUDAN:
            return any(start <= at <= end for start, end in self.exercise_windows)
        start, end = self.exercise_window(last_trade_at)
        return start <= at <= end

    def package_value(
        self, values: Mapping[str, Decimal], multipliers: Mapping[str, Decimal]
    ) -> Decimal:
        """What one contract's deliverable package is worth, in the deliverables' terms.

        Each deliverable's ``units x value x multiplier``, plus the cash
        component. ``values`` and ``multipliers`` are by asset.
        """

        total = self.cash_per_contract
        for d in self.deliverables:
            total += d.units * values[d.asset_id] * multipliers[d.asset_id]
        return total

    def basket_intrinsic(
        self,
        values: Mapping[str, Decimal],
        multipliers: Mapping[str, Decimal],
        multiplier: Decimal,
    ) -> Decimal:
        """Intrinsic value per unit of price of a contract on a package, never negative.

        ``package_value / multiplier`` against the strike: the single-deliverable
        :meth:`intrinsic` generalised to a basket and a cash component.
        """

        per_price_unit = self.package_value(values, multipliers) / multiplier
        if self.option_type is OptionType.CALL:
            return max(per_price_unit - self.strike, _ZERO)
        return max(self.strike - per_price_unit, _ZERO)

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

        if self.basket or self.cash_per_contract != _ZERO:
            raise InstrumentInputError(
                "A contract delivering a basket or a cash component is valued by each "
                "component's value (basket_intrinsic), not by one underlying value."
            )
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
    out: dict[str, Any] = {
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
    # The v4.2.0 terms (FUT-016) are written only when declared, so the terms of a
    # contract that declares none are written exactly as before.
    if terms.basket:
        out["basket"] = [_deliverable_out(d) for d in terms.basket]
    if terms.cash_per_contract != _ZERO:
        out["cash_per_contract"] = str(terms.cash_per_contract)
    if terms.consideration_currency is not None:
        out["consideration_currency"] = terms.consideration_currency
    if terms.fractions is not None:
        price = terms.fractions.price
        out["fractions"] = {"price": None if price is None else str(price)}
    if terms.consideration_rate is not None:
        out["consideration_rate"] = str(terms.consideration_rate)
    if terms.exercise_windows:
        out["exercise_windows"] = [[start, end] for start, end in terms.exercise_windows]
    if terms.adjustments:
        out["adjustments"] = [
            {
                "effective_at": a.effective_at,
                "strike": str(a.strike),
                "deliverable": _deliverable_out(a.deliverable),
                "basket": [_deliverable_out(d) for d in a.basket],
                "cash_per_contract": str(a.cash_per_contract),
                "reason": a.reason,
            }
            for a in terms.adjustments
        ]
    return out


def _deliverable_in(value: Any) -> Deliverable | None:
    if value is None:
        return None
    return Deliverable(str(value["asset_id"]), Decimal(value["units"]))


def _basket_in(value: Any) -> tuple[Deliverable, ...]:
    out = []
    for item in value:
        deliverable = _deliverable_in(item)
        if deliverable is None:
            raise InstrumentInputError("A basket's deliverable is never null.")
        out.append(deliverable)
    return tuple(out)


def _adjustment_in(value: Any) -> TermsAdjustment:
    deliverable = _deliverable_in(value["deliverable"])
    if deliverable is None:
        raise InstrumentInputError("An adjustment's deliverable is never null.")
    return TermsAdjustment(
        effective_at=float(value["effective_at"]),
        strike=Decimal(value["strike"]),
        deliverable=deliverable,
        basket=_basket_in(value["basket"]),
        cash_per_contract=Decimal(value["cash_per_contract"]),
        reason=str(value["reason"]),
    )


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
            fractions = value.get("fractions")
            return OptionTerms(
                option_type=OptionType[value["option_type"]],
                strike=Decimal(value["strike"]),
                exercise_style=ExerciseStyle[value["exercise_style"]],
                settlement=SettlementStyle[value["settlement"]],
                deliverable=deliverable,
                expires_at=float(value["expires_at"]),
                automatic_exercise=None if rule is None else AutomaticExercise(Decimal(rule)),
                exercisable_from=None if start is None else float(start),
                basket=_basket_in(value.get("basket", ())),
                cash_per_contract=Decimal(value.get("cash_per_contract", "0")),
                consideration_currency=value.get("consideration_currency"),
                fractions=None
                if fractions is None
                else CashInLieu(
                    None if fractions["price"] is None else Decimal(fractions["price"])
                ),
                exercise_windows=tuple(
                    (float(start), float(end)) for start, end in value.get("exercise_windows", ())
                ),
                adjustments=tuple(_adjustment_in(a) for a in value.get("adjustments", ())),
                consideration_rate=(
                    None
                    if value.get("consideration_rate") is None
                    else Decimal(value["consideration_rate"])
                ),
            )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise InstrumentInputError(f"{where} are not contract terms: {exc}.") from exc
    raise InstrumentInputError(f"{where} are of kind {value.get('kind')!r}, not option or future.")
