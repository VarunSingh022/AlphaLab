"""How often a rate compounds, which is part of the rate.

"5%" is not a number until the compounding is stated. Over one year, 5%
compounded annually grows 1.0500, semi-annually 1.050625, monthly 1.051162 and
continuously 1.051271. Those differ by more than a basis point at the short end
and by a great deal more over a decade, and nothing in the figure ``0.05``
distinguishes them.

So :class:`Compounding` is required wherever a rate becomes a discount factor,
and there is no default. It is the same position
:class:`alphalab.data.assets.RateSpec` already takes on ``day_count`` and
``quoted_in_percent``: the convention travels with the rate or the rate means
nothing.

One enum, two uses
------------------

A conventional bond's yield compounds at its coupon frequency -- a semi-annual
bond quotes a semi-annual yield -- so :class:`alphalab.macro.bond.Bond` uses
this enum for both facts rather than carrying two that must agree.
:attr:`Compounding.CONTINUOUS` is the member a coupon schedule cannot take, and
``Bond`` refuses it explicitly rather than silently reading it as something
else.
"""

from __future__ import annotations

import math
from enum import Enum, auto

from alphalab.conventions.exceptions import ConventionInputError

__all__ = ["Compounding", "compound_factor", "discount_factor"]


class Compounding(Enum):
    """How often interest is added to principal."""

    ANNUAL = auto()
    SEMI_ANNUAL = auto()
    QUARTERLY = auto()
    MONTHLY = auto()

    #: The limit as the period goes to zero. The convention of option pricing
    #: and of most curve mathematics, and not a coupon schedule.
    CONTINUOUS = auto()

    @property
    def periods_per_year(self) -> int | None:
        """Compounding periods a year, or ``None`` for continuous.

        ``None`` rather than a large number: continuous compounding is a limit
        and has no period count, and any integer stood in for it would be an
        approximation presented as the convention.
        """

        return {
            Compounding.ANNUAL: 1,
            Compounding.SEMI_ANNUAL: 2,
            Compounding.QUARTERLY: 4,
            Compounding.MONTHLY: 12,
        }.get(self)

    @property
    def is_periodic(self) -> bool:
        """Whether this compounding has a finite period count."""

        return self is not Compounding.CONTINUOUS


def compound_factor(rate: float, years: float, compounding: Compounding) -> float:
    """What one unit grows to over ``years`` at ``rate``, under ``compounding``.

    Raises:
        ConventionInputError: If ``years`` is negative, or the rate makes the
            per-period growth factor non-positive -- at which point a power of
            it is not a real number and the result would be either a complex
            value or a silent ``nan``.
    """

    if years < 0.0:
        raise ConventionInputError(
            f"years is {years}; growing over a negative period is discounting, which "
            ":func:`discount_factor` does and says so."
        )
    if compounding is Compounding.CONTINUOUS:
        return math.exp(rate * years)
    periods = compounding.periods_per_year
    assert periods is not None  # CONTINUOUS is handled above
    base = 1.0 + rate / periods
    if base <= 0.0:
        raise ConventionInputError(
            f"A rate of {rate} compounded {periods} times a year gives a per-period factor "
            f"of {base}, which has no real power. The rate is below -{periods * 100}%."
        )
    return float(base ** (periods * years))


def discount_factor(rate: float, years: float, compounding: Compounding) -> float:
    """What one unit received in ``years`` is worth now.

    The reciprocal of :func:`compound_factor`, and separate from it so that a
    caller reads which direction they asked for rather than inferring it from a
    sign.
    """

    return 1.0 / compound_factor(rate, years, compounding)
