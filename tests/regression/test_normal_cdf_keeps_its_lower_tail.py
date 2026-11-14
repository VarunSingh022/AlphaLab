"""NUM-017: the normal CDF keeps its lower tail (v4.3).

Until v4.3 ``common.statistics.normal_cdf`` was the standard library's
``NormalDist.cdf``, ``(1 + erf(x / √2)) / 2``. Below 0 that adds one to an
``erf`` near minus one, and keeps only the digits the difference leaves. Its
relative error was ``4e-11`` at ``-5`` and two percent at ``-8``, and it returned
exactly 0 below about ``-8.3``, where ``Φ(-10)`` is ``7.6e-24``. The deflated and
probabilistic Sharpe ratios rest on it.

Below 0 it is now ``erfc(-x / √2) / 2``. At and above 0 it is the standard
library's form, value for value, so no value there moved. The reference here is
independent of both: the continued fraction for ``erfc``, summed in 50-digit
``Decimal``.
"""

from __future__ import annotations

import statistics
from decimal import Decimal, localcontext

import pytest

from alphalab.common.statistics import normal_cdf
from alphalab.research.sharpe_inference import probabilistic_sharpe_ratio

_PI = Decimal("3.14159265358979323846264338327950288419716939937510582097494")


def _phi_below_zero(x: float) -> float:
    """``Φ(x)`` for ``x < 0``, by the continued fraction ``e^-z² / √π / (z + 1/2 / (z + ...))``."""

    with localcontext() as context:
        context.prec = 50
        z = Decimal(-x) / Decimal(2).sqrt()
        fraction = z
        for k in range(600, 0, -1):
            fraction = z + Decimal(k) / 2 / fraction
        return float((-(z * z)).exp() / _PI.sqrt() / fraction / 2)


@pytest.mark.parametrize("x", [-1.0, -3.0, -5.0, -8.0, -9.0, -12.0, -20.0, -37.0])
def test_the_lower_tail_keeps_its_digits(x: float) -> None:
    exact = _phi_below_zero(x)

    assert normal_cdf(x) > 0.0
    assert normal_cdf(x) == pytest.approx(exact, rel=1e-13)


@pytest.mark.parametrize("x", [0.0, 0.25, 1.0, 1.96, 3.0, 8.0, 40.0])
def test_the_upper_half_is_the_standard_librarys_value_for_value(x: float) -> None:
    assert normal_cdf(x) == statistics.NormalDist().cdf(x)


def test_the_two_halves_meet_at_zero() -> None:
    assert normal_cdf(-0.0) == normal_cdf(0.0) == 0.5
    assert normal_cdf(-1e-300) == pytest.approx(0.5)
    assert normal_cdf(-5e-324) <= 0.5 <= normal_cdf(5e-324)


def test_a_sharpe_ratio_far_below_its_benchmark_is_a_small_probability_not_zero() -> None:
    """PSR is ``Φ(z)``: a series far below its benchmark sits deep in the lower tail."""

    returns = [0.001 * ((index % 7) - 3) - 0.004 for index in range(400)]

    probability = probabilistic_sharpe_ratio(returns, 0.0).probabilistic_sharpe

    assert 0.0 < probability < 1e-20
