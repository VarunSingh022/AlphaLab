"""Return metrics and calculations.

Each function returns ``None`` where its statistic is undefined, rather than
``0.0``: a total return over a non-positive starting capital, a CAGR over no
elapsed time, an average of no returns, a volatility of fewer than two. See
:mod:`alphalab.analytics.metrics` for why.

One power, rounded once (v4.1, ledger LIM-005)
----------------------------------------------
A CAGR and a geometric return raise a ratio to a fractional power. Until v4.1
that was ``math.pow``, which is the host's ``libm``, and IEEE 754 does not
require ``pow`` to be correctly rounded: measured on macOS arm64, it differed
from the correctly rounded power in the last bit for 409 of 300,000 inputs. A
run compiles a CAGR into its report, the report into its record, and the record
into its ``result_id`` -- so one run could carry one identity on a developer's
Mac and another on a Linux server. :func:`_power` computes the power to sixty
significant digits in :mod:`decimal`, which is the same software on every host,
and rounds that once to a double: the correctly rounded value, everywhere. It
is the only fractional power a run's record holds; every other float a run
records comes from ``+ - * /``, ``sqrt`` and ``fsum``, which IEEE 754 and
CPython define exactly.
"""

import math
from decimal import Context, Decimal, Overflow

from alphalab.common.statistics import sample_variance

#: Sixty digits, with room for any exponent a double's power can reach: rounding
#: a result held to sixty digits to a double's fifty-three bits is the correct
#: rounding of the exact power except where the exact value lies within about
#: 1e-44 of a midpoint between two doubles.
_POWER = Context(prec=60, Emax=1_000_000, Emin=-1_000_000)


def _power(base: float, exponent: float) -> float:
    """``base ** exponent`` for a non-negative ``base``, correctly rounded, on every host.

    Raises:
        OverflowError: If the power is beyond the largest double, as
            ``math.pow`` raises.
    """

    try:
        result = float(_POWER.power(Decimal(base), Decimal(exponent)))
    except Overflow:
        result = math.inf
    if math.isinf(result):
        raise OverflowError(f"{base!r} ** {exponent!r} is beyond the largest double")
    return result


def total_return(start_value: Decimal, end_value: Decimal) -> float | None:
    """The total return between two capital marks, or ``None`` if none is defined."""

    if start_value <= Decimal("0"):
        return None
    return float((end_value - start_value) / start_value)


def cagr(start_value: Decimal, end_value: Decimal, years: float) -> float | None:
    """Compound annual growth rate, or ``None`` if none is defined.

    Undefined for a non-positive starting capital or a non-positive period. An
    ending capital of zero is a total loss and its CAGR is ``-1.0``: until v3.10
    it was reported as ``0.0``, which reads as "flat".
    """

    if start_value <= Decimal("0") or years <= 0.0 or not math.isfinite(years):
        return None
    if end_value <= Decimal("0"):
        return -1.0
    try:
        return _power(float(end_value / start_value), 1.0 / years) - 1.0
    except OverflowError:
        # Compounding a gain over a span of seconds is a number no float holds:
        # not representable, and therefore not reported.
        return None


def arithmetic_return(returns: tuple[float, ...]) -> float | None:
    """The simple average return, or ``None`` over no returns."""

    if not returns:
        return None
    return sum(returns) / len(returns)


def geometric_return(returns: tuple[float, ...]) -> float | None:
    """The geometric average return, or ``None`` over no returns.

    A return below ``-1`` (a loss of more than everything, which a leveraged or
    short book can post) has no real geometric mean and is ``None`` too.
    """

    if not returns:
        return None

    product = 1.0
    for r in returns:
        product *= 1.0 + r

    if product < 0.0:
        return None
    return _power(product, 1.0 / len(returns)) - 1.0


def annualized_volatility(returns: tuple[float, ...], periods: float) -> float | None:
    """Annualized standard deviation of returns, or ``None`` over fewer than two.

    The dispersion is `alphalab.common.statistics.sample_variance`, which is the
    one unbiased estimator in the repository rather than a fourth copy of it.
    ``periods`` -- return periods per year -- is required; it defaulted to 252.
    """

    if not math.isfinite(periods) or periods <= 0:
        raise ValueError(f"periods per year must be a positive finite number, got {periods!r}.")
    if len(returns) < 2:
        return None

    return math.sqrt(sample_variance(returns)) * math.sqrt(periods)
