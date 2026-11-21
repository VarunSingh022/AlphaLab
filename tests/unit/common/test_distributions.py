"""The distributions, held to exact oracles and published tables (ADR-0058, M2).

Every reference here is computed, not remembered. Each oracle is a finite closed
form, summed in 60-digit ``Decimal`` or in exact ``Fraction``:

- Student's t for even degrees of freedom;
- chi-squared for even degrees of freedom;
- the gamma distribution for an integer shape;
- the Poisson and binomial distributions;
- the beta distribution for integer parameters, and F through it.

A remembered table value is a reference to the digits the table prints and no
further: writing this module found a remembered t quantile wrong in its
eleventh digit. The functions are class-3 values (they go through the host's
``exp``, ``log`` and ``lgamma``), so they are held to a few units in the last
place, never to equality.
"""

from __future__ import annotations

import math
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from alphalab.common import distributions as dist
from alphalab.common.exceptions import AlphaLabValidationError, ConvergenceError

#: The oracles' working precision, set locally in each: the suite shares one
#: ambient decimal context, and the engine's own tests rest on it.
PRECISION = 80

EPSILON = 2.0**-52

#: How far a class-3 value may be from the exact one, relative to it, where nothing
#: larger than a few units of rounding enters it.
RELATIVE = 32 * EPSILON


def _bound(*terms: float) -> float:
    """The relative error a prefix ``exp(sum t_i)`` carries, from its error analysis.

    Each term -- a ``log Γ``, an ``a log x`` -- is correct to a unit in its last
    place, so the exponent's absolute error is about ``ε Σ|t_i|`` and the
    prefix's relative error the same. Large parameters make large terms: ``log
    Γ(101) ≈ 364`` costs about eighty units in the last place. The bound adds the
    continued fraction's own rounding (ADR-0058 section 4).
    """

    return EPSILON * (32 + 2 * sum(abs(term) for term in terms))


def _beta_bound(x: float, a: float, b: float) -> float:
    return _bound(
        math.lgamma(a + b), math.lgamma(a), math.lgamma(b), a * math.log(x), b * math.log1p(-x)
    )


def _gamma_bound(a: float, y: float) -> float:
    return _bound(a * math.log(y), y, math.lgamma(a))


def _close(computed: float, exact: Decimal | Fraction, relative: float = RELATIVE) -> bool:
    target = float(exact)
    return abs(computed - target) <= relative * max(abs(target), 1e-300)


# --------------------------------------------------------------------------- #
# Exact oracles
# --------------------------------------------------------------------------- #


def _t_cdf_even(t: float, nu: int) -> Decimal:
    """Student's t CDF for even ``nu``: ``1/2 + x/2 * sum_{j<nu/2} c_j (1 - x²)^j``."""

    with localcontext() as context:
        context.prec = PRECISION
        value = Decimal(t)
        x = value / (Decimal(nu) + value * value).sqrt()
        one_minus, term, total = 1 - x * x, Decimal(1), Decimal(0)
        for j in range(nu // 2):
            if j:
                term = term * one_minus * (2 * j - 1) / (2 * j)
            total += term
        return Decimal(1) / 2 + x / 2 * total


def _erlang_sf(x: float, k: int, scale: float = 1.0) -> Decimal:
    """``P(Gamma(k, scale) > x) = exp(-y) sum_{j<k} y^j / j!``, ``y = x / scale``."""

    with localcontext() as context:
        context.prec = PRECISION
        y = Decimal(x) / Decimal(scale)
        term, total = Decimal(1), Decimal(0)
        for j in range(k):
            if j:
                term = term * y / j
            total += term
        return (-y).exp() * total


def _erlang_cdf(x: float, k: int, scale: float = 1.0) -> Decimal:
    """``P(Gamma(k, scale) <= x) = exp(-y) sum_{j>=k} y^j / j!``, summed directly.

    Not one minus :func:`_erlang_sf`: near 0 that difference keeps no more digits
    than the precision has beyond the leading nines.
    """

    with localcontext() as context:
        context.prec = PRECISION
        y = Decimal(x) / Decimal(scale)
        term = y**k / math.factorial(k)
        total, j = Decimal(0), k
        while total == 0 or term > total * Decimal(10) ** -70:
            total += term
            j += 1
            term = term * y / j
        return (-y).exp() * total


def _poisson_cdf(k: int, mu: float) -> Decimal:
    return _erlang_sf(mu, k + 1)


def _binomial_cdf(k: int, n: int, p: Fraction) -> Fraction:
    return sum((math.comb(n, j) * p**j * (1 - p) ** (n - j) for j in range(k + 1)), Fraction(0))


def _beta_cdf_integer(x: Fraction, a: int, b: int) -> Fraction:
    """``I_x(a, b) = sum_{j=a}^{a+b-1} C(a+b-1, j) x^j (1-x)^(a+b-1-j)``, integer ``a, b``."""

    n = a + b - 1
    return sum((math.comb(n, j) * x**j * (1 - x) ** (n - j) for j in range(a, n + 1)), Fraction(0))


# --------------------------------------------------------------------------- #
# Against the oracles
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("nu", [2, 4, 10, 30])
@pytest.mark.parametrize("t", [-6.0, -2.5, -0.3, 0.0, 0.7, 1.96, 4.0, 12.0])
def test_students_t_is_the_even_degrees_closed_form(t: float, nu: int) -> None:
    exact = _t_cdf_even(t, nu)
    bound = _beta_bound(nu / (nu + t * t), nu / 2, 0.5) if t else RELATIVE

    assert _close(dist.student_t_cdf(t, nu), exact, bound)
    assert _close(dist.student_t_sf(t, nu), 1 - exact, bound)


@pytest.mark.parametrize("k", [1, 2, 5, 15])
@pytest.mark.parametrize("x", [0.01, 0.5, 3.0, 11.0, 40.0])
def test_chi_squared_and_gamma_are_the_erlang_closed_form(x: float, k: int) -> None:
    """``chi2(2k)`` is ``Gamma(k, 2)``: ``P(chi2_2k > x) = P(Poisson(x/2) <= k - 1)``."""

    survival, lower = _erlang_sf(x, k, 2.0), _erlang_cdf(x, k, 2.0)
    bound = _gamma_bound(k, x / 2)

    assert _close(dist.chi_squared_sf(x, 2 * k), survival, bound)
    assert _close(dist.chi_squared_cdf(x, 2 * k), lower, bound)
    assert _close(dist.gamma_sf(x, k, 2.0), survival, bound)
    assert _close(dist.regularized_gamma_q(k, x / 2.0), survival, bound)
    assert _close(dist.regularized_gamma_p(k, x / 2.0), lower, bound)


@pytest.mark.parametrize(("k", "mu"), [(0, 0.5), (3, 2.0), (10, 9.5), (25, 40.0), (80, 60.0)])
def test_poisson_is_its_finite_sum(k: int, mu: float) -> None:
    exact = _poisson_cdf(k, mu)
    bound = _gamma_bound(k + 1, mu)

    assert _close(dist.poisson_cdf(k, mu), exact, bound)
    assert _close(dist.poisson_sf(k, mu), _erlang_cdf(mu, k + 1), bound)
    assert _close(
        dist.poisson_pmf(k, mu),
        Decimal(mu) ** k * (-Decimal(mu)).exp() / math.factorial(k),
        _bound(k * math.log(mu), mu, math.lgamma(k + 1)),
    )


@pytest.mark.parametrize(
    ("k", "n", "p"), [(0, 5, "1/3"), (3, 10, "1/2"), (7, 20, "1/4"), (60, 100, "3/5")]
)
def test_binomial_is_its_exact_sum(k: int, n: int, p: str) -> None:
    probability = Fraction(p)
    exact = _binomial_cdf(k, n, probability)
    bound = _beta_bound(float(probability), k + 1, n - k)

    assert _close(dist.binomial_cdf(k, n, float(probability)), exact, bound)
    assert _close(dist.binomial_sf(k, n, float(probability)), 1 - exact, bound)


@pytest.mark.parametrize(("a", "b"), [(1, 1), (2, 3), (5, 2), (8, 8), (30, 4)])
@pytest.mark.parametrize("x", ["1/10", "1/3", "1/2", "7/8"])
def test_beta_is_its_exact_sum_for_integer_parameters(x: str, a: int, b: int) -> None:
    point = Fraction(x)
    exact = _beta_cdf_integer(point, a, b)
    bound = _beta_bound(float(point), a, b)

    assert _close(dist.beta_cdf(float(point), a, b), exact, bound)
    assert _close(dist.beta_sf(float(point), a, b), 1 - exact, bound)
    assert _close(dist.regularized_incomplete_beta(float(point), a, b), exact, bound)


@pytest.mark.parametrize(("d1", "d2"), [(2, 2), (4, 6), (10, 20)])
@pytest.mark.parametrize("x", [0.25, 1.0, 3.5])
def test_f_is_the_beta_it_maps_to(x: float, d1: int, d2: int) -> None:
    """``d1 X / (d1 X + d2) ~ Beta(d1/2, d2/2)`` for ``X ~ F(d1, d2)``."""

    point = Fraction(d1) * Fraction(x) / (Fraction(d1) * Fraction(x) + d2)
    exact = _beta_cdf_integer(point, d1 // 2, d2 // 2)
    bound = _beta_bound(float(point), d1 / 2, d2 / 2)

    assert _close(dist.f_cdf(x, d1, d2), exact, bound)
    assert _close(dist.f_sf(x, d1, d2), 1 - exact, bound)


def test_closed_forms_for_low_degrees_of_freedom() -> None:
    for x in (-3.0, -0.5, 0.25, 2.0, 50.0):
        assert dist.student_t_cdf(x, 1) == pytest.approx(0.5 + math.atan(x) / math.pi, rel=RELATIVE)
        assert dist.student_t_cdf(x, 2) == pytest.approx(
            0.5 + x / (2.0 * math.sqrt(2.0 + x * x)), rel=RELATIVE
        )
    # Each upper-tail quantile is checked through a survival function written so
    # that nothing cancels: tan(pi (p - 1/2)) near pi/2 would itself carry a
    # relative error of order 1e-13, larger than the quantile's own.
    for p in (0.6, 0.975, 0.999, 0.9999999):
        upper = 1 - p
        q1 = dist.student_t_quantile(p, 1)
        assert math.atan(1 / q1) / math.pi == pytest.approx(upper, rel=RELATIVE)
        q2 = dist.student_t_quantile(p, 2)
        root = math.sqrt(2 + q2 * q2)
        assert 1 / (root * (root + q2)) == pytest.approx(upper, rel=RELATIVE)
        assert math.exp(-dist.chi_squared_quantile(p, 2) / 2) == pytest.approx(upper, rel=RELATIVE)
        assert 1 / (1 + dist.f_quantile(p, 2, 2)) == pytest.approx(upper, rel=RELATIVE)


# --------------------------------------------------------------------------- #
# Quantiles: inverse identities against the exact CDFs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("nu", [2, 4, 10, 30])
@pytest.mark.parametrize("p", [1e-9, 0.01, 0.3, 0.5, 0.8, 0.975, 0.999999])
def test_a_t_quantile_is_where_the_exact_cdf_reaches_p(p: float, nu: int) -> None:
    quantile = dist.student_t_quantile(p, nu)

    reached = _t_cdf_even(quantile, nu)
    tail = min(p, 1 - p)
    assert abs(float(reached) - p) <= 64 * 2.0**-52 * max(tail, 2.0**-52) + 2.0**-53


@pytest.mark.parametrize("k", [1, 3, 10])
@pytest.mark.parametrize("p", [1e-12, 0.05, 0.5, 0.95, 0.9999999])
def test_a_chi_squared_quantile_is_where_the_exact_survival_reaches_1_minus_p(
    p: float, k: int
) -> None:
    quantile = dist.chi_squared_quantile(p, 2 * k)

    survival = float(_erlang_sf(quantile, k, 2.0))
    assert survival == pytest.approx(1 - p, rel=64 * 2.0**-52, abs=64 * 2.0**-52 * p)


@pytest.mark.parametrize(("a", "b"), [(2, 3), (8, 8), (30, 4)])
@pytest.mark.parametrize("p", [1e-6, 0.2, 0.5, 0.9, 0.999])
def test_a_beta_quantile_is_where_the_exact_cdf_reaches_p(p: float, a: int, b: int) -> None:
    quantile = dist.beta_quantile(p, a, b)

    reached = float(_beta_cdf_integer(Fraction(quantile), a, b))
    assert reached == pytest.approx(p, rel=64 * 2.0**-52, abs=64 * 2.0**-52 * (1 - p))


@pytest.mark.parametrize(
    ("p", "n", "prob"), [(0.0, 10, 0.3), (0.5, 10, 0.5), (0.9, 40, 0.2), (1.0, 7, 0.4)]
)
def test_a_binomial_quantile_is_the_least_k_reaching_p(p: float, n: int, prob: float) -> None:
    k = dist.binomial_quantile(p, n, prob)
    exact = Fraction(prob)

    assert _binomial_cdf(k, n, exact) >= Fraction(p) - Fraction(1, 10**12)
    assert k == 0 or _binomial_cdf(k - 1, n, exact) < Fraction(p)


@pytest.mark.parametrize(("p", "mu"), [(0.0, 3.0), (0.5, 3.0), (0.99, 12.5), (0.999999, 0.7)])
def test_a_poisson_quantile_is_the_least_k_reaching_p(p: float, mu: float) -> None:
    k = dist.poisson_quantile(p, mu)

    assert float(_poisson_cdf(k, mu)) >= p - 1e-12
    assert k == 0 or float(_poisson_cdf(k - 1, mu)) < p


# --------------------------------------------------------------------------- #
# Published tables, to the digits they print
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("p", "nu", "printed"),
    [
        (0.975, 10, "2.228"),
        (0.975, 30, "2.042"),
        (0.995, 5, "4.032"),
        (0.95, 20, "1.725"),
        (0.9995, 60, "3.460"),
    ],
)
def test_students_t_matches_the_printed_table(p: float, nu: int, printed: str) -> None:
    assert f"{dist.student_t_quantile(p, nu):.3f}" == printed


@pytest.mark.parametrize(
    ("p", "nu", "printed"),
    [
        (0.95, 10, "18.307"),
        (0.99, 5, "15.086"),
        (0.975, 1, "5.024"),
        (0.05, 20, "10.851"),
        (0.95, 100, "124.342"),
    ],
)
def test_chi_squared_matches_the_printed_table(p: float, nu: int, printed: str) -> None:
    assert f"{dist.chi_squared_quantile(p, nu):.3f}" == printed


@pytest.mark.parametrize(
    ("p", "d1", "d2", "printed"),
    [(0.95, 5, 10, "3.33"), (0.99, 2, 20, "5.85"), (0.95, 1, 10, "4.96"), (0.95, 10, 10, "2.98")],
)
def test_f_matches_the_printed_table(p: float, d1: int, d2: int, printed: str) -> None:
    assert f"{dist.f_quantile(p, d1, d2):.2f}" == printed


# --------------------------------------------------------------------------- #
# Tails, properties and refusals
# --------------------------------------------------------------------------- #


def test_a_deep_tail_keeps_its_digits() -> None:
    """A survival function is computed directly: ``1e-30`` is not ``1 - 0.999...``."""

    for t in (30.0, 100.0, 1000.0):
        exact = 1 - _t_cdf_even(t, 4)
        assert _close(dist.student_t_sf(t, 4), exact, _beta_bound(4 / (4 + t * t), 2, 0.5))
    assert _close(dist.chi_squared_sf(150.0, 10), _erlang_sf(150.0, 5, 2.0), _gamma_bound(5, 75.0))
    assert dist.chi_squared_sf(150.0, 10) < 1e-25
    assert dist.normal_sf(10.0) == pytest.approx(7.6198530241605e-24, rel=1e-12)


def test_the_cdf_rises_and_meets_its_survival_function() -> None:
    points = [x / 4 for x in range(-40, 41)]
    for nu in (1, 3.5, 12):
        values = [dist.student_t_cdf(x, nu) for x in points]
        assert values == sorted(values)
        for x in points:
            assert dist.student_t_cdf(x, nu) + dist.student_t_sf(x, nu) == pytest.approx(
                1.0, abs=4 * 2.0**-52
            )
            assert dist.student_t_cdf(-x, nu) == pytest.approx(
                dist.student_t_sf(x, nu), abs=4 * 2.0**-52
            )


@pytest.mark.parametrize(
    "call",
    [
        lambda: dist.student_t_cdf(1.0, 0.0),
        lambda: dist.student_t_cdf(math.nan, 3),
        lambda: dist.student_t_quantile(1.0, 3),
        lambda: dist.chi_squared_quantile(1.0, 3),
        lambda: dist.gamma_cdf(1.0, -1.0, 1.0),
        lambda: dist.beta_cdf(0.5, 0.0, 1.0),
        lambda: dist.regularized_gamma_p(1.0, -0.5),
        lambda: dist.regularized_incomplete_beta(1.5, 1.0, 1.0),
        lambda: dist.binomial_cdf(-1, 5, 0.5),
        lambda: dist.binomial_cdf(1, 5, 1.5),
        lambda: dist.poisson_cdf(True, 1.0),  # a bool is refused as a count
        lambda: dist.poisson_quantile(1.0, 2.0),
    ],
)
def test_a_value_outside_a_distributions_domain_is_refused(call: object) -> None:
    assert callable(call)
    with pytest.raises(AlphaLabValidationError):
        call()


def test_a_series_that_cannot_converge_in_its_limit_is_refused_not_approximated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dist, "ITERATION_LIMIT", 3)

    with pytest.raises(ConvergenceError, match="incomplete gamma") as refused:
        dist.regularized_gamma_p(50.0, 40.0)
    assert refused.value.limit == 3 and refused.value.tolerance == dist.RELATIVE_TOLERANCE
    with pytest.raises(ConvergenceError, match="incomplete beta"):
        dist.regularized_incomplete_beta(0.4, 40.0, 50.0)
