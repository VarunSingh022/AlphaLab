"""Published critical values and p-values, held to their tables (v4.3, ADR-0074 section 6).

* MacKinnon (2010)'s response surfaces reproduce the paper's own worked example
  and the textbook asymptotic values. The four rows where a widely used
  transcription differs from the paper are pinned to the paper.
* MacKinnon (1994)'s approximations agree with the 2010 asymptotic critical
  values, and their two branches meet.
* KPSS's table is its published one, interpolated within it and bounded beyond.
"""

from __future__ import annotations

import pytest

from alphalab.econometrics.critical_values import (
    LEVELS,
    Deterministic,
    PBound,
    kpss_critical_values,
    kpss_p_value,
    mackinnon_critical_values,
    mackinnon_p_value,
)
from alphalab.econometrics.exceptions import EconometricsInputError

TABULATED = [
    (deterministic, variables)
    for deterministic in (
        Deterministic.CONSTANT,
        Deterministic.CONSTANT_TREND,
        Deterministic.CONSTANT_QUADRATIC_TREND,
    )
    for variables in range(1, 13)
] + [(Deterministic.NONE, 1)]


def test_the_papers_worked_example() -> None:
    """MacKinnon (2010), the appendix's example: the EG test with trend, N = 5, T = 100."""

    values = mackinnon_critical_values(Deterministic.CONSTANT_TREND, 5, 100).values

    assert round(values[0.05], 5) == -4.89111


@pytest.mark.parametrize(
    ("deterministic", "expected"),
    [
        (Deterministic.NONE, (-2.566, -1.941, -1.617)),
        (Deterministic.CONSTANT, (-3.430, -2.862, -2.567)),
        (Deterministic.CONSTANT_TREND, (-3.959, -3.410, -3.127)),
    ],
)
def test_the_asymptotic_dickey_fuller_values(
    deterministic: Deterministic, expected: tuple[float, float, float]
) -> None:
    """The textbook asymptotic Dickey-Fuller critical values, to three decimals."""

    values = mackinnon_critical_values(deterministic, 1, None).values

    assert tuple(round(values[level], 3) for level in LEVELS) == expected


@pytest.mark.parametrize(
    ("deterministic", "variables", "level", "coefficients"),
    [
        # A widely used transcription prints -33.527 for β2.
        (Deterministic.CONSTANT, 2, 0.01, (-3.89644, -10.9519, -22.527, 0.0)),
        # It prints -8.5632 for β1.
        (Deterministic.CONSTANT, 3, 0.05, (-3.74066, -8.5631, -10.852, 27.982)),
        # It prints -14.0405 for β1.
        (Deterministic.CONSTANT, 5, 0.05, (-4.41519, -14.0406, -12.575, 0.0)),
        # It repeats N = 6's β2 and β3 (-5.104, 27.877).
        (Deterministic.CONSTANT, 7, 0.10, (-4.69648, -15.7315, -6.922, 67.721)),
    ],
)
def test_the_rows_a_widely_used_transcription_gets_wrong_are_the_papers(
    deterministic: Deterministic,
    variables: int,
    level: float,
    coefficients: tuple[float, float, float, float],
) -> None:
    """Read back through the surface at T = 1, 2 and 3, which fix all four coefficients."""

    at = [mackinnon_critical_values(deterministic, variables, t).values[level] for t in (1, 2, 3)]
    asymptotic = mackinnon_critical_values(deterministic, variables, None).values[level]
    b0, b1, b2, b3 = coefficients

    assert asymptotic == b0
    for t, value in zip((1, 2, 3), at, strict=True):
        u = 1.0 / t
        assert value == pytest.approx(b0 + b1 * u + b2 * u * u + b3 * u * u * u, abs=1e-9)


@pytest.mark.parametrize(("deterministic", "variables"), TABULATED)
def test_every_surface_orders_its_levels_and_falls_with_n(
    deterministic: Deterministic, variables: int
) -> None:
    """A transcription error in β∞ breaks one of these orderings."""

    values = mackinnon_critical_values(deterministic, variables, None).values
    assert values[0.01] < values[0.05] < values[0.10] < 0.0
    if deterministic is not Deterministic.NONE and variables > 1:
        fewer = mackinnon_critical_values(deterministic, variables - 1, None).values
        assert all(values[level] < fewer[level] for level in LEVELS)
    for t in (20, 50, 100, 500):
        finite = mackinnon_critical_values(deterministic, variables, t).values
        assert finite[0.01] < finite[0.05] < finite[0.10]


@pytest.mark.parametrize(("deterministic", "variables"), [(d, n) for d, n in TABULATED if n <= 6])
def test_the_1994_p_values_agree_with_the_2010_critical_values(
    deterministic: Deterministic, variables: int
) -> None:
    """At each asymptotic critical value, MacKinnon (1994)'s p-value is that level within 0.6%."""

    values = mackinnon_critical_values(deterministic, variables, None).values
    for level in LEVELS:
        p = mackinnon_p_value(values[level], deterministic, variables)
        assert p.bound is None
        assert p.value == pytest.approx(level, rel=0.006)


@pytest.mark.parametrize(("deterministic", "variables"), [(d, n) for d, n in TABULATED if n <= 6])
def test_the_two_approximations_meet_at_tau_star(
    deterministic: Deterministic, variables: int
) -> None:
    """Either side of the cut-off between the branches, the p-value moves by under 0.004."""

    from alphalab.econometrics.critical_values import _TAU_STAR

    star = _TAU_STAR[deterministic][variables - 1]
    below = mackinnon_p_value(star, deterministic, variables).value
    above = mackinnon_p_value(star + 1e-9, deterministic, variables).value

    assert abs(above - below) < 0.004


def test_a_statistic_beyond_the_approximations_range_is_a_bound() -> None:
    inside = mackinnon_p_value(-18.0, Deterministic.CONSTANT, 1)
    below = mackinnon_p_value(-30.0, Deterministic.CONSTANT, 1)
    above = mackinnon_p_value(5.0, Deterministic.CONSTANT, 1)

    assert inside.bound is None
    assert below.bound is PBound.AT_MOST and below.value < 1e-20
    assert above.bound is PBound.AT_LEAST and above.value > 0.99
    unbounded = mackinnon_p_value(50.0, Deterministic.NONE, 1)
    assert unbounded.bound is None, "the no-constant case for N = 1 has no upper end"


@pytest.mark.parametrize(
    ("make", "match"),
    [
        (lambda: mackinnon_critical_values(Deterministic.NONE, 2, None), "publishes no NONE"),
        (lambda: mackinnon_critical_values(Deterministic.CONSTANT, 13, None), "1 to 12 series"),
        (lambda: mackinnon_critical_values(Deterministic.CONSTANT, 1, 0), "positive count"),
        (lambda: mackinnon_p_value(-2.0, Deterministic.CONSTANT, 7), "1 to 6 series"),
        (lambda: mackinnon_p_value(float("nan"), Deterministic.CONSTANT, 1), "finite"),
        (lambda: kpss_critical_values(Deterministic.NONE), "level stationarity"),
        (lambda: kpss_p_value(-0.1, Deterministic.CONSTANT), "non-negative"),
    ],
)
def test_what_no_table_publishes_is_refused(make: object, match: str) -> None:
    with pytest.raises(EconometricsInputError, match=match):
        make()  # type: ignore[operator]


def test_kpss_is_its_published_table_interpolated_within_and_bounded_beyond() -> None:
    level = kpss_critical_values(Deterministic.CONSTANT).values
    trend = kpss_critical_values(Deterministic.CONSTANT_TREND).values

    assert dict(level) == {0.01: 0.739, 0.025: 0.574, 0.05: 0.463, 0.10: 0.347}
    assert dict(trend) == {0.01: 0.216, 0.025: 0.176, 0.05: 0.146, 0.10: 0.119}
    assert kpss_p_value(0.463, Deterministic.CONSTANT).value == 0.05
    between = kpss_p_value(0.5, Deterministic.CONSTANT)
    assert between.bound is None
    assert between.value == pytest.approx(0.05 - (0.037 / 0.111) * 0.025)
    assert kpss_p_value(2.0, Deterministic.CONSTANT).bound is PBound.AT_MOST
    assert kpss_p_value(0.0, Deterministic.CONSTANT_TREND).bound is PBound.AT_LEAST
