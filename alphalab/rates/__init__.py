"""Rates: curves under a required, named interpolation, and what is bootstrapped and priced on them.

BDY-014 kept a curve bootstrapper out of AlphaLab for one reason: an
interpolation changes every forward read off a curve, and a library that chose
one silently would hide a market assumption. ADR-0065 reopens it on that
reason's terms. Every modelling choice here is the researcher's, stated and
recorded:

* **A curve** (:class:`DiscountCurve`) is its pillars, read between them by a
  named :class:`Interpolation`, which is required, and beyond them only by an
  :class:`Extrapolation` it states.
* **A bootstrap** (:func:`bootstrap_curve`) solves each pillar in turn from
  quotes the researcher supplies -- deposits, FRAs, futures with a stated
  convexity adjustment, par swaps and bonds -- to a stated tolerance, refuses a
  quote it cannot meet, and returns each quote's repricing. Given a discount
  curve, it builds a projection curve against it: OIS discounting, stated.
* **Instruments by declared terms** (:class:`SwapTerms`, :class:`FraTerms`):
  every leg's notional, day count, step, convention, calendars and, for a
  floating leg, its index, reset lag and fixing source, none defaulted. They
  are valued off the curves a caller supplies (:func:`value_swap`,
  :func:`value_fra`); a rate already fixed is read as supplied, never
  projected.
* **Identity.** A curve's, a bootstrap's and a valuation's identities cover
  their inputs, the method, the interpolation and the conventions, as ADR-0058
  gave estimates. A solved discount factor is class 3 and stays out of every
  identity.

The engine ships no curve and no quote (AC-22). The package imports
``alphalab.common`` and ``alphalab.conventions``, and nothing else in AlphaLab.
"""

from alphalab.rates.bootstrap import (
    BOOTSTRAP_KEY_SCHEME,
    FORWARD_SEARCH,
    CurveBootstrap,
    Repricing,
    bootstrap_curve,
)
from alphalab.rates.curve import (
    CURVE_KEY_SCHEME,
    SHIFT_KEY_SCHEME,
    DiscountCurve,
    Extrapolation,
    Interpolation,
    Pillar,
    canonical_curve_key,
)
from alphalab.rates.exceptions import BootstrapError, CurveRangeError, RatesError, RatesInputError
from alphalab.rates.pricing import (
    VALUATION_KEY_SCHEME,
    CouponPeriod,
    Fixings,
    Valuation,
    fra_settlement,
    leg_periods,
    swap_accrued,
    value_fra,
    value_swap,
)
from alphalab.rates.quotes import (
    BondQuote,
    DepositQuote,
    FraQuote,
    FutureQuote,
    RateQuote,
    SwapQuote,
)
from alphalab.rates.schedule import regular_schedule, tenor_months
from alphalab.rates.terms import (
    FixedLeg,
    FloatingLeg,
    FraTerms,
    LegSchedule,
    RatesTerms,
    SwapTerms,
    rates_terms_from_primitives,
    rates_terms_to_primitives,
    resolve_calendars,
)

__all__ = [
    "BOOTSTRAP_KEY_SCHEME",
    "CURVE_KEY_SCHEME",
    "FORWARD_SEARCH",
    "SHIFT_KEY_SCHEME",
    "VALUATION_KEY_SCHEME",
    "BondQuote",
    "BootstrapError",
    "CouponPeriod",
    "CurveBootstrap",
    "CurveRangeError",
    "DepositQuote",
    "DiscountCurve",
    "Extrapolation",
    "FixedLeg",
    "Fixings",
    "FloatingLeg",
    "FraQuote",
    "FraTerms",
    "FutureQuote",
    "Interpolation",
    "LegSchedule",
    "Pillar",
    "RateQuote",
    "RatesError",
    "RatesInputError",
    "RatesTerms",
    "Repricing",
    "SwapQuote",
    "SwapTerms",
    "Valuation",
    "bootstrap_curve",
    "canonical_curve_key",
    "fra_settlement",
    "leg_periods",
    "rates_terms_from_primitives",
    "rates_terms_to_primitives",
    "regular_schedule",
    "resolve_calendars",
    "swap_accrued",
    "tenor_months",
    "value_fra",
    "value_swap",
]
