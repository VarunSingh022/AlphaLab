"""Probability distributions and the special functions under them (ADR-0058, v4.3).

The one authority for a distribution's cumulative distribution function, its
survival function and its quantile -- Student's t, chi-squared, F, gamma, beta,
binomial and Poisson, beside the normal of :mod:`alphalab.common.statistics` --
and for the special functions they rest on: the log-gamma and log-beta
functions and the regularised incomplete gamma and beta functions.

Precision
---------
Every function computes to the precision of IEEE 754 binary64 by an algorithm
whose convergence criterion is a stated constant of this module, not a caller's
choice (ADR-0058 section 2): the continued fractions and series stop when a
term changes the result by less than :data:`RELATIVE_TOLERANCE`, and a quantile
when its bracket is that narrow relative to its value. An algorithm that reaches
:data:`ITERATION_LIMIT` without converging raises
:class:`~alphalab.common.exceptions.ConvergenceError`; nothing returns a best
effort.

These are class-3 values in ADR-0058's sense: they go through the host's
``exp``, ``log`` and ``lgamma``, so they may differ in the last bits on another
host and never enter an identity.

Accuracy near the tails
-----------------------
A survival function is computed directly, never as ``1 - cdf``: a p-value of
``1e-12`` computed as one minus ``0.999999999999`` keeps three digits. Each
function evaluates whichever of its regularised incomplete functions, or their
complements, is small at that point.

Algorithms: the incomplete gamma function by its series below ``x = a + 1`` and
by Legendre's continued fraction above it; the incomplete beta function by its
continued fraction, evaluated by the modified Lentz method on whichever side of
its symmetry converges (Press, Teukolsky, Vetterling and Flannery, *Numerical
Recipes*, sections 6.2 and 6.4). A quantile is found by Newton's method kept
inside a bracket that bisection narrows when a Newton step would leave it.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Callable
from typing import Final

from alphalab.common.exceptions import AlphaLabValidationError, ConvergenceError
from alphalab.common.statistics import normal_cdf, normal_quantile

__all__ = [
    "ITERATION_LIMIT",
    "RELATIVE_TOLERANCE",
    "beta_cdf",
    "beta_quantile",
    "beta_sf",
    "binomial_cdf",
    "binomial_pmf",
    "binomial_quantile",
    "binomial_sf",
    "chi_squared_cdf",
    "chi_squared_quantile",
    "chi_squared_sf",
    "f_cdf",
    "f_quantile",
    "f_sf",
    "gamma_cdf",
    "gamma_quantile",
    "gamma_sf",
    "log_beta",
    "log_gamma",
    "normal_cdf",
    "normal_quantile",
    "normal_sf",
    "poisson_cdf",
    "poisson_pmf",
    "poisson_quantile",
    "poisson_sf",
    "regularized_gamma_p",
    "regularized_gamma_q",
    "regularized_incomplete_beta",
    "student_t_cdf",
    "student_t_quantile",
    "student_t_sf",
]

#: When a series or continued fraction stops: a term moving the result by less
#: than this, relative to it. Four units in the last place of binary64.
RELATIVE_TOLERANCE: Final = 4.0 * sys.float_info.epsilon

#: How many terms a series, continued fraction or quantile search may take before
#: it is refused. Each converges in far fewer for parameters up to about 1e8 --
#: the continued fractions in about the square root of the larger parameter.
ITERATION_LIMIT: Final = 100_000

#: The modified Lentz method's floor for a vanishing denominator.
_TINY: Final = sys.float_info.min / sys.float_info.epsilon


def _require_finite(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AlphaLabValidationError(f"{name} is a number, not {value!r}.")
    if not math.isfinite(value):
        raise AlphaLabValidationError(f"{name} is finite, not {value!r}.")
    return float(value)


def _require_positive(value: float, name: str) -> float:
    number = _require_finite(value, name)
    if number <= 0.0:
        raise AlphaLabValidationError(f"{name} is positive, not {value!r}.")
    return number


def _require_probability(value: float, name: str = "A probability") -> float:
    number = _require_finite(value, name)
    if not 0.0 <= number <= 1.0:
        raise AlphaLabValidationError(f"{name} is in [0, 1], not {value!r}.")
    return number


def _require_count(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AlphaLabValidationError(f"{name} is a non-negative integer, not {value!r}.")
    return value


# --------------------------------------------------------------------------- #
# Special functions
# --------------------------------------------------------------------------- #


def log_gamma(x: float) -> float:
    """The natural logarithm of the gamma function, for ``x > 0``.

    The host's ``lgamma``; a class-3 value (ADR-0058).

    Raises:
        AlphaLabValidationError: If ``x`` is not a positive finite number.
    """

    return math.lgamma(_require_positive(x, "The gamma function's argument"))


def log_beta(a: float, b: float) -> float:
    """``log B(a, b) = log Γ(a) + log Γ(b) - log Γ(a + b)``, for ``a, b > 0``."""

    a = _require_positive(a, "a")
    b = _require_positive(b, "b")
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _gamma_prefix(a: float, x: float) -> float:
    """``x**a * exp(-x) / Γ(a)``, through logarithms: it neither overflows nor underflows early."""

    return math.exp(a * math.log(x) - x - math.lgamma(a))


def _gamma_series(a: float, x: float) -> float:
    """``P(a, x)`` by its series, for ``x < a + 1``."""

    term = total = 1.0 / a
    denominator = a
    for _ in range(ITERATION_LIMIT):
        denominator += 1.0
        term *= x / denominator
        total += term
        if abs(term) < abs(total) * RELATIVE_TOLERANCE:
            return total * _gamma_prefix(a, x)
    raise ConvergenceError(
        "the incomplete gamma function's series", RELATIVE_TOLERANCE, ITERATION_LIMIT, term / total
    )


def _gamma_continued_fraction(a: float, x: float) -> float:
    """``Q(a, x)`` by Legendre's continued fraction (modified Lentz), for ``x >= a + 1``."""

    b = x + 1.0 - a
    c = 1.0 / _TINY
    d = 1.0 / b
    fraction = d
    for index in range(1, ITERATION_LIMIT + 1):
        an = -index * (index - a)
        b += 2.0
        d = an * d + b
        if abs(d) < _TINY:
            d = _TINY
        c = b + an / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        step = d * c
        fraction *= step
        if abs(step - 1.0) < RELATIVE_TOLERANCE:
            return fraction * _gamma_prefix(a, x)
    raise ConvergenceError(
        "the incomplete gamma function's continued fraction",
        RELATIVE_TOLERANCE,
        ITERATION_LIMIT,
        abs(step - 1.0),
    )


def regularized_gamma_p(a: float, x: float) -> float:
    """The regularised lower incomplete gamma function ``P(a, x)``, for ``a > 0, x >= 0``.

    ``P(a, x)`` is the lower incomplete gamma integral over ``Γ(a)``: the
    chi-squared, gamma and Poisson distributions' common core. ``P(a, 0) = 0``.

    Raises:
        AlphaLabValidationError: If ``a`` is not positive or ``x`` is negative.
        ConvergenceError: Beyond the parameters it is stated for.
    """

    a = _require_positive(a, "a")
    x = _require_finite(x, "x")
    if x < 0.0:
        raise AlphaLabValidationError(f"The incomplete gamma function needs x >= 0, not {x!r}.")
    if x == 0.0:
        return 0.0
    if x < a + 1.0:
        return _gamma_series(a, x)
    return 1.0 - _gamma_continued_fraction(a, x)


def regularized_gamma_q(a: float, x: float) -> float:
    """The regularised upper incomplete gamma function ``Q(a, x) = 1 - P(a, x)``.

    Computed directly where it is small, never as one minus ``P``.

    Raises:
        AlphaLabValidationError: If ``a`` is not positive or ``x`` is negative.
        ConvergenceError: Beyond the parameters it is stated for.
    """

    a = _require_positive(a, "a")
    x = _require_finite(x, "x")
    if x < 0.0:
        raise AlphaLabValidationError(f"The incomplete gamma function needs x >= 0, not {x!r}.")
    if x == 0.0:
        return 1.0
    if x < a + 1.0:
        return 1.0 - _gamma_series(a, x)
    return _gamma_continued_fraction(a, x)


def _beta_continued_fraction(x: float, a: float, b: float) -> float:
    """The continued fraction for ``I_x(a, b)`` (modified Lentz), for ``x < (a+1)/(a+b+2)``."""

    total, plus, minus = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - total * x / plus
    if abs(d) < _TINY:
        d = _TINY
    d = 1.0 / d
    fraction = d
    for m in range(1, ITERATION_LIMIT + 1):
        twice = 2 * m
        coefficient = m * (b - m) * x / ((minus + twice) * (a + twice))
        d = 1.0 + coefficient * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + coefficient / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        fraction *= d * c
        coefficient = -(a + m) * (total + m) * x / ((a + twice) * (plus + twice))
        d = 1.0 + coefficient * d
        if abs(d) < _TINY:
            d = _TINY
        c = 1.0 + coefficient / c
        if abs(c) < _TINY:
            c = _TINY
        d = 1.0 / d
        step = d * c
        fraction *= step
        if abs(step - 1.0) < RELATIVE_TOLERANCE:
            return fraction
    raise ConvergenceError(
        "the incomplete beta function's continued fraction",
        RELATIVE_TOLERANCE,
        ITERATION_LIMIT,
        abs(step - 1.0),
    )


def _beta_prefix(x: float, a: float, b: float) -> float:
    """``x**a (1 - x)**b / B(a, b)``, through logarithms."""

    return math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )


def _incomplete_beta_pair(x: float, a: float, b: float) -> tuple[float, float]:
    """``(I_x(a, b), 1 - I_x(a, b))``, each computed where it is the small one."""

    if x == 0.0:
        return 0.0, 1.0
    if x == 1.0:
        return 1.0, 0.0
    prefix = _beta_prefix(x, a, b)
    if x < (a + 1.0) / (a + b + 2.0):
        lower = prefix * _beta_continued_fraction(x, a, b) / a
        return lower, 1.0 - lower
    upper = prefix * _beta_continued_fraction(1.0 - x, b, a) / b
    return 1.0 - upper, upper


def regularized_incomplete_beta(x: float, a: float, b: float) -> float:
    """The regularised incomplete beta function ``I_x(a, b)``, for ``0 <= x <= 1`` and ``a, b > 0``.

    The t, F, beta and binomial distributions' common core.

    Raises:
        AlphaLabValidationError: If ``x`` is outside ``[0, 1]`` or ``a`` or
            ``b`` is not positive.
        ConvergenceError: Beyond the parameters it is stated for.
    """

    x = _require_probability(x, "The incomplete beta function's x")
    a = _require_positive(a, "a")
    b = _require_positive(b, "b")
    return _incomplete_beta_pair(x, a, b)[0]


# --------------------------------------------------------------------------- #
# Quantiles: Newton's method inside a bracket that bisection keeps
# --------------------------------------------------------------------------- #


def _invert(
    distance: Callable[[float], float],
    density: Callable[[float], float],
    low: float,
    high: float,
    what: str,
) -> float:
    """The root in ``[low, high]`` of an increasing ``distance``, whose derivative is ``density``.

    ``distance(low) <= 0 <= distance(high)``. A Newton step that would leave
    the bracket is replaced by bisection, so the bracket always narrows and the
    search always ends. It ends when the bracket is narrower than the tolerance,
    or when a Newton step is: Newton's iterates may approach the root from one
    side, leaving the bracket's far end where it was, and once converged they
    move only by rounding.
    """

    x = 0.5 * (low + high)
    for _ in range(ITERATION_LIMIT):
        value = distance(x)
        if value == 0.0:
            return x
        if value < 0.0:
            low = x
        else:
            high = x
        if high - low <= RELATIVE_TOLERANCE * max(abs(low), abs(high), sys.float_info.min):
            return 0.5 * (low + high)
        slope = density(x)
        candidate = x - value / slope if slope > 0.0 and math.isfinite(slope) else math.nan
        if not low < candidate < high:
            candidate = 0.5 * (low + high)
        elif abs(candidate - x) <= RELATIVE_TOLERANCE * abs(candidate):
            return candidate
        x = candidate
    raise ConvergenceError(f"the {what} quantile", RELATIVE_TOLERANCE, ITERATION_LIMIT, high - low)


def _upper_bracket(
    cdf: Callable[[float], float],
    sf: Callable[[float], float],
    probability: float,
    start: float,
) -> float:
    """A point above the quantile: ``start``, doubled until it is passed.

    Above one half the survival function is tested against ``1 - probability``,
    so a quantile near 1 is bracketed on digits the CDF would have lost.
    """

    high = start
    upper = 1.0 - probability
    while (cdf(high) < probability) if probability <= 0.5 else (sf(high) > upper):
        high *= 2.0
        if not math.isfinite(high):
            raise ConvergenceError(
                "a quantile's bracket", RELATIVE_TOLERANCE, ITERATION_LIMIT, high
            )
    return high


def _quantile_of(
    probability: float,
    cdf: Callable[[float], float],
    sf: Callable[[float], float],
    density: Callable[[float], float],
    low: float,
    high: float,
    what: str,
) -> float:
    """Invert whichever of the CDF and survival function is accurate at ``probability``.

    Below one half the CDF is inverted; above it the survival function, at ``1 -
    probability``, so an upper-tail quantile keeps its digits.
    """

    if probability <= 0.5:
        return _invert(lambda x: cdf(x) - probability, density, low, high, what)
    upper = 1.0 - probability
    return _invert(lambda x: upper - sf(x), density, low, high, what)


# --------------------------------------------------------------------------- #
# The normal distribution, from common.statistics
# --------------------------------------------------------------------------- #


def normal_sf(x: float) -> float:
    """The standard normal survival function ``1 - Φ(x) = erfc(x / √2) / 2``.

    Computed by ``erfc``, so the upper tail keeps its digits -- ``normal_sf(10)``
    is ``7.6e-24``, where one minus a CDF near 1 would be 0.
    """

    return 0.5 * math.erfc(_require_finite(x, "x") / math.sqrt(2.0))


# --------------------------------------------------------------------------- #
# Student's t
# --------------------------------------------------------------------------- #


def _t_tail(x: float, df: float) -> float:
    """``P(T > |x|)`` for ``df`` degrees of freedom: ``I_{df/(df+x²)}(df/2, 1/2) / 2``."""

    if x == 0.0:
        return 0.5
    return 0.5 * _incomplete_beta_pair(df / (df + x * x), 0.5 * df, 0.5)[0]


def student_t_cdf(x: float, df: float) -> float:
    """Student's t cumulative distribution function with ``df > 0`` degrees of freedom."""

    x = _require_finite(x, "x")
    df = _require_positive(df, "The degrees of freedom")
    tail = _t_tail(x, df)
    return tail if x < 0.0 else 1.0 - tail


def student_t_sf(x: float, df: float) -> float:
    """Student's t survival function ``P(T > x)``, computed directly in the upper tail."""

    x = _require_finite(x, "x")
    df = _require_positive(df, "The degrees of freedom")
    tail = _t_tail(x, df)
    return tail if x > 0.0 else 1.0 - tail


def _t_density(x: float, df: float) -> float:
    return math.exp(
        math.lgamma(0.5 * (df + 1.0))
        - math.lgamma(0.5 * df)
        - 0.5 * math.log(df * math.pi)
        - 0.5 * (df + 1.0) * math.log1p(x * x / df)
    )


def student_t_quantile(probability: float, df: float) -> float:
    """Student's t quantile: the ``x`` with ``P(T <= x) = probability``, strictly inside (0, 1)."""

    probability = _require_probability(probability)
    df = _require_positive(df, "The degrees of freedom")
    if probability in (0.0, 1.0):
        raise AlphaLabValidationError(
            f"A t quantile is defined strictly inside (0, 1); got {probability!r}."
        )
    if probability == 0.5:
        return 0.0
    if probability < 0.5:
        return -student_t_quantile(1.0 - probability, df)
    high = _upper_bracket(
        lambda x: student_t_cdf(x, df), lambda x: student_t_sf(x, df), probability, 1.0
    )
    return _quantile_of(
        probability,
        lambda x: student_t_cdf(x, df),
        lambda x: student_t_sf(x, df),
        lambda x: _t_density(x, df),
        0.0,
        high,
        "Student's t",
    )


# --------------------------------------------------------------------------- #
# Gamma and chi-squared
# --------------------------------------------------------------------------- #


def gamma_cdf(x: float, shape: float, scale: float) -> float:
    """The gamma distribution's CDF, ``P(shape, x / scale)``; ``0`` for ``x <= 0``."""

    x = _require_finite(x, "x")
    shape = _require_positive(shape, "The shape")
    scale = _require_positive(scale, "The scale")
    return 0.0 if x <= 0.0 else regularized_gamma_p(shape, x / scale)


def gamma_sf(x: float, shape: float, scale: float) -> float:
    """The gamma distribution's survival function, ``Q(shape, x / scale)``; ``1`` for ``x <= 0``."""

    x = _require_finite(x, "x")
    shape = _require_positive(shape, "The shape")
    scale = _require_positive(scale, "The scale")
    return 1.0 if x <= 0.0 else regularized_gamma_q(shape, x / scale)


def _gamma_density(x: float, shape: float, scale: float) -> float:
    if x <= 0.0:
        return 0.0
    return math.exp(
        (shape - 1.0) * math.log(x / scale) - x / scale - math.lgamma(shape) - math.log(scale)
    )


def gamma_quantile(probability: float, shape: float, scale: float) -> float:
    """The gamma distribution's quantile, in ``[0, inf)``; refused at ``probability == 1``."""

    probability = _require_probability(probability)
    shape = _require_positive(shape, "The shape")
    scale = _require_positive(scale, "The scale")
    if probability == 0.0:
        return 0.0
    if probability == 1.0:
        raise AlphaLabValidationError("The gamma distribution's quantile at 1 is infinite.")
    high = _upper_bracket(
        lambda x: gamma_cdf(x, shape, scale),
        lambda x: gamma_sf(x, shape, scale),
        probability,
        shape * scale + scale,
    )
    return _quantile_of(
        probability,
        lambda x: gamma_cdf(x, shape, scale),
        lambda x: gamma_sf(x, shape, scale),
        lambda x: _gamma_density(x, shape, scale),
        0.0,
        high,
        "gamma distribution's",
    )


def chi_squared_cdf(x: float, df: float) -> float:
    """The chi-squared CDF with ``df > 0`` degrees of freedom: ``P(df/2, x/2)``."""

    return gamma_cdf(x, 0.5 * _require_positive(df, "The degrees of freedom"), 2.0)


def chi_squared_sf(x: float, df: float) -> float:
    """The chi-squared survival function: ``Q(df/2, x/2)``, a test's p-value."""

    return gamma_sf(x, 0.5 * _require_positive(df, "The degrees of freedom"), 2.0)


def chi_squared_quantile(probability: float, df: float) -> float:
    """The chi-squared quantile with ``df > 0`` degrees of freedom."""

    return gamma_quantile(probability, 0.5 * _require_positive(df, "The degrees of freedom"), 2.0)


# --------------------------------------------------------------------------- #
# Beta and F
# --------------------------------------------------------------------------- #


def beta_cdf(x: float, a: float, b: float) -> float:
    """The beta distribution's CDF, ``I_x(a, b)``; ``0`` below 0 and ``1`` above 1."""

    x = _require_finite(x, "x")
    a = _require_positive(a, "a")
    b = _require_positive(b, "b")
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return _incomplete_beta_pair(x, a, b)[0]


def beta_sf(x: float, a: float, b: float) -> float:
    """The beta distribution's survival function, ``1 - I_x(a, b)`` computed directly."""

    x = _require_finite(x, "x")
    a = _require_positive(a, "a")
    b = _require_positive(b, "b")
    if x <= 0.0:
        return 1.0
    if x >= 1.0:
        return 0.0
    return _incomplete_beta_pair(x, a, b)[1]


def _beta_density(x: float, a: float, b: float) -> float:
    if not 0.0 < x < 1.0:
        return 0.0
    return math.exp(
        (a - 1.0) * math.log(x)
        + (b - 1.0) * math.log1p(-x)
        - (math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b))
    )


def beta_quantile(probability: float, a: float, b: float) -> float:
    """The beta distribution's quantile, in ``[0, 1]``."""

    probability = _require_probability(probability)
    a = _require_positive(a, "a")
    b = _require_positive(b, "b")
    if probability in (0.0, 1.0):
        return probability
    return _quantile_of(
        probability,
        lambda x: beta_cdf(x, a, b),
        lambda x: beta_sf(x, a, b),
        lambda x: _beta_density(x, a, b),
        0.0,
        1.0,
        "beta distribution's",
    )


def _f_pair(x: float, d1: float, d2: float) -> tuple[float, float]:
    """``(CDF, SF)`` of F(d1, d2) at ``x > 0``, through ``I_{d1 x/(d1 x + d2)}(d1/2, d2/2)``."""

    scaled = d1 * x
    return _incomplete_beta_pair(scaled / (scaled + d2), 0.5 * d1, 0.5 * d2)


def f_cdf(x: float, d1: float, d2: float) -> float:
    """The F distribution's CDF with ``d1, d2 > 0`` degrees of freedom; ``0`` for ``x <= 0``."""

    x = _require_finite(x, "x")
    d1 = _require_positive(d1, "d1")
    d2 = _require_positive(d2, "d2")
    return 0.0 if x <= 0.0 else _f_pair(x, d1, d2)[0]


def f_sf(x: float, d1: float, d2: float) -> float:
    """The F distribution's survival function, a variance-ratio test's p-value."""

    x = _require_finite(x, "x")
    d1 = _require_positive(d1, "d1")
    d2 = _require_positive(d2, "d2")
    return 1.0 if x <= 0.0 else _f_pair(x, d1, d2)[1]


def _f_density(x: float, d1: float, d2: float) -> float:
    if x <= 0.0:
        return 0.0
    return math.exp(
        0.5 * d1 * math.log(d1 / d2)
        + (0.5 * d1 - 1.0) * math.log(x)
        - 0.5 * (d1 + d2) * math.log1p(d1 * x / d2)
        - (math.lgamma(0.5 * d1) + math.lgamma(0.5 * d2) - math.lgamma(0.5 * (d1 + d2)))
    )


def f_quantile(probability: float, d1: float, d2: float) -> float:
    """The F distribution's quantile, in ``[0, inf)``; refused at ``probability == 1``."""

    probability = _require_probability(probability)
    d1 = _require_positive(d1, "d1")
    d2 = _require_positive(d2, "d2")
    if probability == 0.0:
        return 0.0
    if probability == 1.0:
        raise AlphaLabValidationError("The F distribution's quantile at 1 is infinite.")
    high = _upper_bracket(lambda x: f_cdf(x, d1, d2), lambda x: f_sf(x, d1, d2), probability, 1.0)
    return _quantile_of(
        probability,
        lambda x: f_cdf(x, d1, d2),
        lambda x: f_sf(x, d1, d2),
        lambda x: _f_density(x, d1, d2),
        0.0,
        high,
        "F distribution's",
    )


# --------------------------------------------------------------------------- #
# Binomial and Poisson
# --------------------------------------------------------------------------- #


def _require_success_probability(p: float) -> float:
    return _require_probability(p, "A success probability")


def binomial_pmf(k: int, n: int, p: float) -> float:
    """``P(X = k)`` for ``X ~ Binomial(n, p)``, by logarithms, so a large ``n`` cannot overflow."""

    k = _require_count(k, "k")
    n = _require_count(n, "n")
    p = _require_success_probability(p)
    if k > n:
        return 0.0
    if p in (0.0, 1.0):
        return 1.0 if k == (0 if p == 0.0 else n) else 0.0
    return math.exp(
        math.lgamma(n + 1)
        - math.lgamma(k + 1)
        - math.lgamma(n - k + 1)
        + k * math.log(p)
        + (n - k) * math.log1p(-p)
    )


def binomial_cdf(k: int, n: int, p: float) -> float:
    """``P(X <= k)`` for ``X ~ Binomial(n, p)``: ``I_{1-p}(n - k, k + 1)``."""

    k = _require_count(k, "k")
    n = _require_count(n, "n")
    p = _require_success_probability(p)
    if k >= n:
        return 1.0
    if p == 0.0:
        return 1.0
    if p == 1.0:
        return 0.0
    return _incomplete_beta_pair(p, k + 1.0, float(n - k))[1]


def binomial_sf(k: int, n: int, p: float) -> float:
    """``P(X > k)`` for ``X ~ Binomial(n, p)``: ``I_p(k + 1, n - k)``, computed directly."""

    k = _require_count(k, "k")
    n = _require_count(n, "n")
    p = _require_success_probability(p)
    if k >= n:
        return 0.0
    if p == 0.0:
        return 0.0
    if p == 1.0:
        return 1.0
    return _incomplete_beta_pair(p, k + 1.0, float(n - k))[0]


def binomial_quantile(probability: float, n: int, p: float) -> int:
    """The smallest ``k`` with ``P(X <= k) >= probability``, for ``X ~ Binomial(n, p)``."""

    probability = _require_probability(probability)
    n = _require_count(n, "n")
    p = _require_success_probability(p)
    low, high = 0, n
    while low < high:
        middle = (low + high) // 2
        if binomial_cdf(middle, n, p) >= probability:
            high = middle
        else:
            low = middle + 1
    return low


def poisson_pmf(k: int, mu: float) -> float:
    """``P(X = k)`` for ``X ~ Poisson(mu)``, ``mu > 0``."""

    k = _require_count(k, "k")
    mu = _require_positive(mu, "The mean")
    return math.exp(k * math.log(mu) - mu - math.lgamma(k + 1))


def poisson_cdf(k: int, mu: float) -> float:
    """``P(X <= k)`` for ``X ~ Poisson(mu)``: ``Q(k + 1, mu)``."""

    k = _require_count(k, "k")
    mu = _require_positive(mu, "The mean")
    return regularized_gamma_q(k + 1.0, mu)


def poisson_sf(k: int, mu: float) -> float:
    """``P(X > k)`` for ``X ~ Poisson(mu)``: ``P(k + 1, mu)``, computed directly."""

    k = _require_count(k, "k")
    mu = _require_positive(mu, "The mean")
    return regularized_gamma_p(k + 1.0, mu)


def poisson_quantile(probability: float, mu: float) -> int:
    """The smallest ``k`` with ``P(X <= k) >= probability``, for ``X ~ Poisson(mu)``.

    Raises:
        AlphaLabValidationError: At ``probability == 1``, where no ``k`` will do.
    """

    probability = _require_probability(probability)
    mu = _require_positive(mu, "The mean")
    if probability == 1.0:
        raise AlphaLabValidationError("A Poisson quantile at 1 is infinite.")
    high = max(1, int(mu))
    while poisson_cdf(high, mu) < probability:
        high *= 2
    low = 0
    while low < high:
        middle = (low + high) // 2
        if poisson_cdf(middle, mu) >= probability:
            high = middle
        else:
            low = middle + 1
    return low
