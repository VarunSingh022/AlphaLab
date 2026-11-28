"""Curves under a required, named interpolation (v4.3, ADR-0065 section 2).

A discount curve is its pillars -- dates with discount factors -- in a stated
currency, read on a stated day count's time axis from its anchor, the date
whose discount factor is one. Between pillars it is read by its
:class:`Interpolation`, which is required: no curve exists without one. The
interpolation changes every forward read off the curve, and a library that
chose one silently would hide a market assumption -- BDY-014's reason, kept as
the constraint. Beyond its last pillar a read is refused, unless the curve
states how it extends (:class:`Extrapolation`).

The methods, exactly
--------------------
A date's time ``t`` is its year fraction from the anchor under the curve's day
count, and ``P(t)`` its discount factor. A pillar reads back its own discount
factor, bit for bit.

* ``LOG_LINEAR_DISCOUNT``: ``ln P`` is linear in ``t`` between neighbouring
  pillars, from ``P(0) = 1`` at the anchor, so each segment's instantaneous
  forward rate is constant: piecewise-flat forwards.
* ``LINEAR_ZERO``: the continuously compounded zero rate ``z(t) = -ln P(t) / t``
  is linear in ``t`` between neighbouring pillars, and flat at the first
  pillar's rate from the anchor to it.

When a curve states its extrapolation:

* ``FLAT_FORWARD``: beyond the last pillar, the last segment's continuously
  compounded forward rate, ``(ln P(t[n-1]) - ln P(t[n])) / (t[n] - t[n-1])``,
  continues. Under log-linear interpolation that is the last segment extended.
* ``FLAT_ZERO``: beyond the last pillar, its zero rate continues.

A projection curve is a curve too
---------------------------------
An index's projection curve holds pseudo-discount factors whose only use is the
forwards read off them (:meth:`DiscountCurve.forward_rate`). Discounting at one
curve while projecting from another -- OIS discounting -- is a choice the
researcher states by passing both (ADR-0065 section 2). Nothing here pairs them.

Identity
--------
:attr:`DiscountCurve.curve_id` is SHA-256 over :func:`canonical_curve_key`: the
name, currency, anchor, day count, interpolation and extrapolation, and each
pillar's date with its discount factor's shortest ``repr``. A curve whose
pillars were computed -- by a bootstrap, or by a shift -- names that
computation's identity in place of the discount factors. A solved discount
factor passes through ``exp`` and ``log``, numerical class 3 (ADR-0058 section
3), and two hosts that solved the same problem may differ in its last bits, so
it is recorded beside the identity, never in it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Final

from alphalab.conventions.daycount import DayCount, DayCountTerms, required_terms, year_fraction
from alphalab.conventions.rates import Compounding
from alphalab.rates._checks import (
    accrual,
    require_currency,
    require_day,
    require_finite,
    require_line,
)
from alphalab.rates.exceptions import CurveRangeError, RatesInputError

__all__ = [
    "CURVE_KEY_SCHEME",
    "SHIFT_KEY_SCHEME",
    "DiscountCurve",
    "Extrapolation",
    "Interpolation",
    "Pillar",
    "canonical_curve_key",
]

#: The scheme of :func:`canonical_curve_key`; a change to the key is a new scheme.
CURVE_KEY_SCHEME: Final = "alphalab.rates.curve.v1"
#: The scheme of a parallel shift's identity (:meth:`DiscountCurve.shifted`).
SHIFT_KEY_SCHEME: Final = "alphalab.rates.shift.v1"

_DIGEST: Final = re.compile(r"[0-9a-f]{64}")


class Interpolation(Enum):
    """How a curve is read between its pillars: required, never a default (ADR-0065 section 2)."""

    #: ``ln P`` linear in time between neighbouring pillars, from one at the
    #: anchor: each segment's instantaneous forward rate is constant.
    LOG_LINEAR_DISCOUNT = "log_linear_discount"
    #: The continuously compounded zero rate linear in time between
    #: neighbouring pillars, and flat at the first pillar's from the anchor.
    LINEAR_ZERO = "linear_zero"


class Extrapolation(Enum):
    """How a curve that states one extends beyond its last pillar."""

    #: The last segment's continuously compounded forward rate continues.
    FLAT_FORWARD = "flat_forward"
    #: The last pillar's continuously compounded zero rate continues.
    FLAT_ZERO = "flat_zero"


def _axis(basis: object) -> DayCount:
    if not isinstance(basis, DayCount):
        raise RatesInputError(f"A curve's day count must be a DayCount, got {basis!r}.")
    if required_terms(basis):
        raise RatesInputError(
            f"{basis.name} needs an instrument's terms ({', '.join(sorted(required_terms(basis)))}"
            "), so it cannot be a curve's time axis: a curve's time is the same for every "
            "instrument read off it."
        )
    return basis


@dataclass(frozen=True, slots=True)
class Pillar:
    """A date on a curve and its discount factor there.

    Raises:
        RatesInputError: If ``day`` is not a date, or ``discount`` is not a
            finite positive number.
    """

    day: date
    discount: float

    def __post_init__(self) -> None:
        require_day(self.day, "A pillar's day")
        discount = require_finite(self.discount, f"The discount factor at {self.day}")
        if discount <= 0.0:
            raise RatesInputError(
                f"The discount factor at {self.day} is {discount!r}; a discount factor is "
                "positive, and a curve that reached zero has no rate beyond it."
            )
        object.__setattr__(self, "discount", discount)


class _Grid:
    """A curve's pillars on its time axis, and the reading rule: shared with the bootstrap."""

    __slots__ = ("discounts", "extrapolation", "interpolation", "logs", "times")

    def __init__(
        self,
        times: list[float],
        discounts: list[float],
        interpolation: Interpolation,
        extrapolation: Extrapolation | None,
    ) -> None:
        self.times = times
        self.discounts = discounts
        self.logs = [math.log(value) for value in discounts]
        self.interpolation = interpolation
        self.extrapolation = extrapolation

    def with_trial(self, time: float, log: float) -> _Grid:
        """These pillars and one more after them, at ``time`` with ``ln P = log``."""

        trial = _Grid.__new__(_Grid)
        trial.times = [*self.times, time]
        trial.discounts = [*self.discounts, math.exp(log)]
        trial.logs = [*self.logs, log]
        trial.interpolation = self.interpolation
        trial.extrapolation = self.extrapolation
        return trial

    def discount(self, time: float) -> float | None:
        """``P(time)``, or ``None`` beyond the last pillar when no extrapolation is stated."""

        if time == 0.0:
            return 1.0
        times = self.times
        index = bisect_left(times, time)
        if index < len(times) and times[index] == time:
            return self.discounts[index]
        log = self._beyond(time) if index == len(times) else self._between(index, time)
        return None if log is None else math.exp(log)

    def _between(self, index: int, time: float) -> float:
        later, log_later = self.times[index], self.logs[index]
        if index == 0:
            # Log-linear from P(0) = 1, or the first zero rate held flat: the same line.
            return log_later * (time / later)
        earlier, log_earlier = self.times[index - 1], self.logs[index - 1]
        weight = (time - earlier) / (later - earlier)
        if self.interpolation is Interpolation.LOG_LINEAR_DISCOUNT:
            return log_earlier + weight * (log_later - log_earlier)
        zero_earlier, zero_later = -log_earlier / earlier, -log_later / later
        return -(zero_earlier + weight * (zero_later - zero_earlier)) * time

    def _beyond(self, time: float) -> float | None:
        last, log_last = self.times[-1], self.logs[-1]
        if self.extrapolation is None:
            return None
        if self.extrapolation is Extrapolation.FLAT_ZERO:
            return log_last * (time / last)
        earlier, log_earlier = (
            (self.times[-2], self.logs[-2]) if len(self.times) > 1 else (0.0, 0.0)
        )
        forward = (log_earlier - log_last) / (last - earlier)
        return log_last - forward * (time - last)


@dataclass(frozen=True, slots=True)
class DiscountCurve:
    """Discount factors at pillars, read between them by a named interpolation (ADR-0065 s. 2).

    Attributes:
        name: What the curve is, as the researcher names it: ``"USD-SOFR"``,
            ``"EUR-EURIBOR-6M"``. Part of its identity.
        currency: The ISO 4217 code its discount factors are in.
        anchor: The curve's date. Its discount factor is one there, and no
            date before it is read.
        day_count: The time axis: a date's time is its year fraction from the
            anchor under this basis. A basis that needs an instrument's terms
            (ACT/ACT ICMA, ACT/365L, 30E/360 ISDA, BUS/252) is refused.
        pillars: At least one, after the anchor, in order of their dates and
            of their times.
        interpolation: How the curve is read between pillars. Required.
        extrapolation: How it extends beyond its last pillar. ``None``, the
            default, refuses a read there.
        derivation_id: The identity of the computation the pillars are the
            result of, when they were computed rather than stated: a
            bootstrap's (:func:`~alphalab.rates.bootstrap.bootstrap_curve`)
            or a shift's (:meth:`shifted`). The curve's identity covers it in
            place of the discount factors.

    Raises:
        RatesInputError: If a field is malformed: a name or currency that is
            not one, an anchor that is not a date, a time axis that needs an
            instrument's terms, no pillar, a pillar not after the one before
            it in date or in time, or a derivation that is not a SHA-256
            digest.
    """

    name: str
    currency: str
    anchor: date
    day_count: DayCount
    pillars: tuple[Pillar, ...]
    interpolation: Interpolation
    extrapolation: Extrapolation | None = None
    derivation_id: str | None = None
    _grid: _Grid = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        require_line(self.name, "A curve's name")
        require_currency(self.currency, "A curve's currency")
        require_day(self.anchor, "A curve's anchor")
        _axis(self.day_count)
        if not isinstance(self.interpolation, Interpolation):
            raise RatesInputError(
                f"interpolation must be an Interpolation, got {self.interpolation!r}: a curve "
                "is read between its pillars by a method it names, never by a default."
            )
        if self.extrapolation is not None and not isinstance(self.extrapolation, Extrapolation):
            raise RatesInputError(
                f"extrapolation must be an Extrapolation or None, got {self.extrapolation!r}."
            )
        if self.derivation_id is not None and (
            not isinstance(self.derivation_id, str) or not _DIGEST.fullmatch(self.derivation_id)
        ):
            raise RatesInputError(
                f"derivation_id is a SHA-256 digest in hexadecimal, got {self.derivation_id!r}."
            )
        pillars = tuple(self.pillars)
        if not pillars or not all(isinstance(pillar, Pillar) for pillar in pillars):
            raise RatesInputError("A curve needs at least one Pillar, and its pillars are Pillars.")
        times: list[float] = []
        previous_day, previous_time = self.anchor, 0.0
        for pillar in pillars:
            if pillar.day <= previous_day:
                raise RatesInputError(
                    f"The pillar at {pillar.day} is not after {previous_day}: a curve's pillars "
                    "follow its anchor in order, one to a date."
                )
            time = year_fraction(self.anchor, pillar.day, self.day_count)
            if time <= previous_time:
                raise RatesInputError(
                    f"The pillar at {pillar.day} is {time!r} years from the anchor under "
                    f"{self.day_count.name}, no later than the one before it: two pillars at "
                    "one time on the curve's axis cannot both hold."
                )
            times.append(time)
            previous_day, previous_time = pillar.day, time
        object.__setattr__(self, "pillars", pillars)
        object.__setattr__(
            self,
            "_grid",
            _Grid(
                times,
                [pillar.discount for pillar in pillars],
                self.interpolation,
                self.extrapolation,
            ),
        )

    @property
    def curve_id(self) -> str:
        """SHA-256 over :func:`canonical_curve_key`."""

        return hashlib.sha256(canonical_curve_key(self).encode("utf-8")).hexdigest()

    @property
    def last_pillar(self) -> date:
        """The last date the curve is read at without extrapolation."""

        return self.pillars[-1].day

    def time(self, day: date) -> float:
        """``day``'s time on the curve's axis: its year fraction from the anchor.

        Raises:
            CurveRangeError: If ``day`` is before the anchor.
        """

        require_day(day, "A date read off a curve")
        if day < self.anchor:
            raise CurveRangeError(
                f"{day} is before the curve's anchor {self.anchor}: a curve is read from its "
                "date on."
            )
        return year_fraction(self.anchor, day, self.day_count)

    def discount(self, day: date) -> float:
        """The discount factor at ``day``: one at the anchor, a pillar's own at a pillar.

        Raises:
            CurveRangeError: If ``day`` is before the anchor, or beyond the last
                pillar on a curve that states no extrapolation.
        """

        value = self._grid.discount(self.time(day))
        if value is None:
            raise CurveRangeError(
                f"{day} is beyond the last pillar of {self.name!r}, {self.last_pillar}, and the "
                "curve states no extrapolation: a read beyond it is refused, never assumed."
            )
        return value

    def zero_rate(self, day: date, compounding: Compounding) -> float:
        """The zero rate to ``day`` under ``compounding``, on the curve's time axis.

        Raises:
            CurveRangeError: If ``day`` is the anchor, where no time has passed
                and no rate is defined, or is outside the curve.
            RatesInputError: If ``compounding`` is not a :class:`Compounding`.
        """

        if not isinstance(compounding, Compounding):
            raise RatesInputError(f"compounding must be a Compounding, got {compounding!r}.")
        time = self.time(day)
        if time == 0.0:
            raise CurveRangeError(
                f"{day} is no time from the anchor on the curve's axis; a zero rate needs time."
            )
        discount = self.discount(day)
        if compounding is Compounding.CONTINUOUS:
            return -math.log(discount) / time
        periods = compounding.periods_per_year
        assert periods is not None  # CONTINUOUS is handled above
        return periods * (float(discount ** (-1.0 / (periods * time))) - 1.0)

    def forward_rate(
        self, start: date, end: date, basis: DayCount, *, terms: DayCountTerms | None = None
    ) -> float:
        """The simple rate over ``[start, end]`` the curve implies, accrued under ``basis``.

        ``(P(start) / P(end) - 1) / tau``, where ``tau`` is the period's year
        fraction under ``basis`` and ``terms``: the rate a deposit or an FRA
        over the period would be quoted at on this curve.

        Raises:
            RatesInputError: If ``end`` is not after ``start``, or the basis
                cannot measure the period.
            CurveRangeError: If either date is outside the curve.
        """

        require_day(start, "A forward's start")
        require_day(end, "A forward's end")
        if end <= start:
            raise RatesInputError(f"A forward runs from {start} to a later date, not to {end}.")
        if not isinstance(basis, DayCount):
            raise RatesInputError(f"A forward's basis must be a DayCount, got {basis!r}.")
        tau = accrual(start, end, basis, terms)
        return (self.discount(start) / self.discount(end) - 1.0) / tau

    def shifted(self, amount: float) -> DiscountCurve:
        """This curve with every continuously compounded zero rate moved by ``amount``.

        Each pillar's discount factor is multiplied by ``exp(-amount * t)``.
        Under both interpolations, and both extrapolations, that moves the zero
        rate at every date by exactly ``amount``: a parallel shift, the bump a
        curve's sensitivities are measured by. The shifted curve's identity
        names this curve's and the amount.

        Raises:
            RatesInputError: If ``amount`` is not a finite number.
        """

        amount = require_finite(amount, "A shift")
        key = f"{SHIFT_KEY_SCHEME}\ncurve={self.curve_id}\namount={amount!r}"
        derivation = hashlib.sha256(key.encode("utf-8")).hexdigest()
        moved = tuple(
            Pillar(pillar.day, math.exp(log - amount * time))
            for pillar, time, log in zip(
                self.pillars, self._grid.times, self._grid.logs, strict=True
            )
        )
        return DiscountCurve(
            self.name,
            self.currency,
            self.anchor,
            self.day_count,
            moved,
            self.interpolation,
            self.extrapolation,
            derivation,
        )


def canonical_curve_key(curve: DiscountCurve) -> str:
    """The text a curve's identity is the digest of; see the module docstring."""

    extends = "refused" if curve.extrapolation is None else curve.extrapolation.value
    lines = [
        CURVE_KEY_SCHEME,
        f"name={json.dumps(curve.name, ensure_ascii=False)}",
        f"currency={curve.currency}",
        f"anchor={curve.anchor.isoformat()}",
        f"day_count={curve.day_count.name}",
        f"interpolation={curve.interpolation.value}",
        f"extrapolation={extends}",
    ]
    if curve.derivation_id is None:
        lines += [
            f"pillar={pillar.day.isoformat()}|{pillar.discount!r}" for pillar in curve.pillars
        ]
    else:
        lines.append(f"derivation={curve.derivation_id}")
        lines += [f"pillar={pillar.day.isoformat()}" for pillar in curve.pillars]
    return "\n".join(lines)
