"""Adaptive Gauss-Kronrod quadrature (v4.3, ADR-0058).

The rules are held to their defining property in exact arithmetic: the
15-point Kronrod rule integrates every polynomial of degree 22 or less, and the
embedded 7-point Gauss rule every one of degree 13 or less, so their constants
are checked in 60-digit ``Decimal`` against the moments of ``[-1, 1]``. The
integrator is held to closed forms -- smooth, oscillatory, with an integrable
singularity at an endpoint -- within the tolerance it reports meeting, and
to a pinned value for the cross-host identity CI checks.
"""

from __future__ import annotations

import math
from decimal import Decimal, localcontext

import pytest

from alphalab.common.exceptions import AlphaLabValidationError, ConvergenceError
from alphalab.common.quadrature import (
    GAUSS_WEIGHTS,
    KRONROD_NODES,
    KRONROD_WEIGHTS,
    Integral,
    integrate,
)
from alphalab.common.statistics import normal_cdf

TOLERANCE = 1e-12
LIMIT = 500


def _moment(nodes: list[Decimal], weights: list[Decimal], centre: Decimal, degree: int) -> Decimal:
    total = centre * (Decimal(0) ** degree if degree else Decimal(1))
    for node, weight in zip(nodes, weights, strict=True):
        total += weight * (node**degree + (-node) ** degree)
    return total


def _exact_moment(degree: int) -> Decimal:
    return Decimal(0) if degree % 2 else Decimal(2) / Decimal(degree + 1)


def test_the_kronrod_rule_is_exact_to_degree_22_and_not_beyond() -> None:
    with localcontext() as context:
        context.prec = 60
        nodes = [Decimal(text) for text in KRONROD_NODES[:7]]
        weights = [Decimal(text) for text in KRONROD_WEIGHTS[:7]]
        centre = Decimal(KRONROD_WEIGHTS[7])

        for degree in range(23):
            error = _moment(nodes, weights, centre, degree) - _exact_moment(degree)
            # The constants carry 33 digits.
            assert abs(error) < Decimal("1e-26"), degree
        assert abs(_moment(nodes, weights, centre, 24) - _exact_moment(24)) > Decimal("1e-10")


def test_the_gauss_rule_is_exact_to_degree_13_and_not_beyond() -> None:
    with localcontext() as context:
        context.prec = 60
        nodes = [Decimal(KRONROD_NODES[position]) for position in (1, 3, 5)]
        weights = [Decimal(text) for text in GAUSS_WEIGHTS[:3]]
        centre = Decimal(GAUSS_WEIGHTS[3])

        for degree in range(14):
            error = _moment(nodes, weights, centre, degree) - _exact_moment(degree)
            assert abs(error) < Decimal("1e-30"), degree
        assert abs(_moment(nodes, weights, centre, 14) - _exact_moment(14)) > Decimal("1e-6")


@pytest.mark.parametrize(
    ("name", "function", "lower", "upper", "exact"),
    [
        ("a polynomial", lambda x: 3.0 * x**4 - x + 2.0, -1.0, 2.0, 3.0 * 33 / 5 - 1.5 + 6.0),
        ("exp", math.exp, 0.0, 1.0, math.e - 1.0),
        ("an oscillation", lambda x: math.sin(50.0 * x), 0.0, 1.0, (1.0 - math.cos(50.0)) / 50.0),
        ("a peak", lambda x: 1.0 / (1e-4 + x * x), -1.0, 1.0, 2.0 * 100.0 * math.atan(100.0)),
        ("1 / sqrt(x)", lambda x: 1.0 / math.sqrt(x), 0.0, 1.0, 2.0),
        ("log(x)", math.log, 0.0, 1.0, -1.0),
        (
            "the normal density",
            lambda x: math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi),
            -1.0,
            2.0,
            normal_cdf(2.0) - normal_cdf(-1.0),
        ),
    ],
)
def test_an_integral_meets_its_closed_form_within_the_tolerance(
    name: str, function: object, lower: float, upper: float, exact: float
) -> None:
    result = integrate(
        function,  # type: ignore[arg-type]
        lower,
        upper,
        absolute_tolerance=TOLERANCE,
        relative_tolerance=TOLERANCE,
        max_subdivisions=LIMIT,
    )

    assert result.error_estimate <= result.convergence.tolerance
    assert result.convergence.tolerance == max(TOLERANCE, TOLERANCE * abs(result.value))
    # The estimate is conservative here; the closed form's own rounding is a few eps.
    assert abs(result.value - exact) <= result.error_estimate + 8 * math.ulp(exact), name


def test_reversed_limits_negate_the_integral() -> None:
    forward = integrate(
        math.exp, 0.0, 1.0, absolute_tolerance=1e-12, relative_tolerance=0.0, max_subdivisions=10
    )
    backward = integrate(
        math.exp, 1.0, 0.0, absolute_tolerance=1e-12, relative_tolerance=0.0, max_subdivisions=10
    )

    assert backward.value == -forward.value
    assert backward.error_estimate == forward.error_estimate


def test_an_empty_interval_integrates_to_zero_without_a_call() -> None:
    def never(x: float) -> float:
        raise AssertionError("called")

    result = integrate(
        never, 2.0, 2.0, absolute_tolerance=0.0, relative_tolerance=1e-9, max_subdivisions=1
    )

    assert (result.value, result.error_estimate, result.evaluations) == (0.0, 0.0, 0)


def test_the_endpoints_are_never_evaluated() -> None:
    seen: list[float] = []

    def recording(x: float) -> float:
        seen.append(x)
        return 1.0 / math.sqrt(x)

    result = integrate(
        recording,
        0.0,
        1.0,
        absolute_tolerance=1e-12,
        relative_tolerance=0.0,
        max_subdivisions=LIMIT,
    )

    assert all(0.0 < x < 1.0 for x in seen)
    assert len(seen) == result.evaluations
    assert result.value == pytest.approx(2.0, abs=1e-11)


def test_a_singularity_double_precision_cannot_resolve_stops_short_of_the_endpoint() -> None:
    """Near 1 the floats are 1.1e-16 apart; ``1 / sqrt(1 - x)`` to 1e-12 needs finer."""

    seen: list[float] = []

    def recording(x: float) -> float:
        seen.append(x)
        return 1.0 / math.sqrt(1.0 - x)

    with pytest.raises(ConvergenceError, match="cannot halve further"):
        integrate(
            recording,
            0.0,
            1.0,
            absolute_tolerance=1e-12,
            relative_tolerance=0.0,
            max_subdivisions=LIMIT,
        )
    assert all(0.0 < x < 1.0 for x in seen)


def test_an_interval_too_narrow_for_the_rule_is_refused() -> None:
    with pytest.raises(AlphaLabValidationError, match="too narrow"):
        integrate(
            math.exp,
            1.0,
            math.nextafter(1.0, 2.0),
            absolute_tolerance=1e-9,
            relative_tolerance=0.0,
            max_subdivisions=1,
        )


def test_a_smooth_integrand_needs_no_halving() -> None:
    result = integrate(
        math.cos, 0.0, 1.0, absolute_tolerance=1e-12, relative_tolerance=0.0, max_subdivisions=LIMIT
    )

    assert result.convergence.iterations == 0
    assert result.intervals == 1 and result.evaluations == 15


def test_an_integral_short_of_its_tolerance_raises() -> None:
    with pytest.raises(ConvergenceError) as raised:
        integrate(
            lambda x: 1.0 / math.sqrt(x),
            0.0,
            1.0,
            absolute_tolerance=1e-14,
            relative_tolerance=0.0,
            max_subdivisions=5,
        )

    assert raised.value.limit == 5
    assert raised.value.reached > raised.value.tolerance


def test_a_relative_tolerance_alone_cannot_accept_a_vanishing_integral() -> None:
    # sin over a full period integrates to zero; a relative bound on zero is zero.
    with pytest.raises(ConvergenceError):
        integrate(
            math.sin,
            0.0,
            2.0 * math.pi,
            absolute_tolerance=0.0,
            relative_tolerance=1e-10,
            max_subdivisions=20,
        )


def test_an_exactly_zero_integrand_meets_a_relative_tolerance() -> None:
    result = integrate(
        lambda x: 0.0,
        0.0,
        1.0,
        absolute_tolerance=0.0,
        relative_tolerance=1e-10,
        max_subdivisions=1,
    )

    assert (result.value, result.error_estimate) == (0.0, 0.0)
    assert result.convergence.tolerance == 1e-10


@pytest.mark.parametrize("value", [math.nan, math.inf, "1"])
def test_an_integrand_returning_a_non_number_is_refused(value: object) -> None:
    with pytest.raises(AlphaLabValidationError, match="integrand returned"):
        integrate(
            lambda x: value,  # type: ignore[arg-type,return-value]
            0.0,
            1.0,
            absolute_tolerance=1e-9,
            relative_tolerance=0.0,
            max_subdivisions=5,
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {"lower": math.inf},
        {"upper": math.nan},
        {"absolute_tolerance": -1e-9},
        {"relative_tolerance": math.inf},
        {"absolute_tolerance": 0.0, "relative_tolerance": 0.0},
        {"max_subdivisions": 0},
        {"max_subdivisions": True},
        {"max_subdivisions": 2.0},
    ],
)
def test_arguments_out_of_range_are_refused(arguments: dict[str, object]) -> None:
    given: dict[str, object] = {
        "lower": 0.0,
        "upper": 1.0,
        "absolute_tolerance": 1e-9,
        "relative_tolerance": 0.0,
        "max_subdivisions": 5,
    }
    given.update(arguments)
    with pytest.raises(AlphaLabValidationError):
        integrate(math.exp, given.pop("lower"), given.pop("upper"), **given)  # type: ignore[arg-type]


def test_the_tolerances_and_limit_are_required() -> None:
    with pytest.raises(TypeError):
        integrate(math.exp, 0.0, 1.0)  # type: ignore[call-arg]


#: Computed under CPython 3.12.4, 3.13.18 and 3.14.10 on macOS arm64, identical
#: in every bit; CI holds it on Linux x86-64 as well. The integrand is class 2.
PINNED_INTEGRAL: tuple[str, str, int] = ("0x1.3fc176b7a8560p+0", "0x1.95e1856db89a0p-46", 105)


def test_the_pinned_integral_is_the_same_on_every_host() -> None:
    result = integrate(
        lambda x: 1.0 / (1.0 + x * x),
        0.0,
        3.0,
        absolute_tolerance=1e-13,
        relative_tolerance=0.0,
        max_subdivisions=LIMIT,
    )

    assert (result.value.hex(), result.error_estimate.hex(), result.evaluations) == PINNED_INTEGRAL
    assert isinstance(result, Integral)
