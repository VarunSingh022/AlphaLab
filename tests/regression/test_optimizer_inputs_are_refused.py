"""A portfolio optimizer refuses inconsistent inputs instead of weighting them.

Three ways of passing inconsistent arguments to
:mod:`alphalab.portfolio_optimizer.optimizer` produced a portfolio rather than
an error. A wrong allocation that looks like a right one is the worst failure
this module can have: nothing downstream can tell it apart from a deliberate
one, and the weights are what a caller trades.

* ``optimize_maximum_sharpe(("A", "B", "C"), (0.1, 0.2), cov_3x3)`` returned
  weights for all three assets. ``_matrix_vector_multiply`` iterates the
  *vector*, so the third expected return and the third covariance column were
  dropped and C came out at exactly zero weight -- an allocation decision made
  by a length mismatch.
* ``optimize_inverse_volatility`` read ``volatilities.get(symbol, 1.0)``. An
  asset absent from the mapping was sized as though its volatility were 1.0;
  beside 10%-vol assets that is a tenth of its proper weight.
* A covariance matrix of the wrong shape raised ``IndexError`` out of the
  inversion rather than this package's ``OptimizationError``, so a caller
  handling the documented error type did not catch it.

What is *not* changed, and why, is recorded here too. The inversion uses
Gauss-Jordan elimination without partial pivoting. That is the correct choice
for the input these functions document -- a covariance matrix is symmetric
positive semi-definite, and elimination without pivoting is backward stable on
symmetric positive-definite matrices. The tests below hold it to that: an
exactly singular matrix is refused, and a near-degenerate one still inverts
accurately.
"""

from collections.abc import Callable

import pytest

from alphalab.portfolio_optimizer import (
    optimize_equal_weight,
    optimize_inverse_volatility,
    optimize_maximum_sharpe,
    optimize_minimum_variance,
)
from alphalab.portfolio_optimizer.exceptions import OptimizationError

SYMBOLS = ("A", "B", "C")
COV_3 = (
    (0.040, 0.010, 0.005),
    (0.010, 0.090, 0.004),
    (0.005, 0.004, 0.020),
)


# ---------------------------------------------------------------------------
# The silent wrong answers
# ---------------------------------------------------------------------------


def test_too_few_expected_returns_is_refused_not_weighted() -> None:
    """The exact call that used to return a three-asset portfolio."""

    with pytest.raises(OptimizationError, match="2 expected returns for 3 symbols"):
        optimize_maximum_sharpe(SYMBOLS, (0.1, 0.2), COV_3)


def test_too_many_expected_returns_is_refused_with_the_domain_error() -> None:
    with pytest.raises(OptimizationError, match="4 expected returns for 3 symbols"):
        optimize_maximum_sharpe(SYMBOLS, (0.1, 0.2, 0.3, 0.4), COV_3)


def test_a_missing_volatility_is_refused_rather_than_defaulted_to_one() -> None:
    """1.0 beside a book of 10%-vol assets is a tenth of the proper weight."""

    with pytest.raises(OptimizationError, match="No volatility supplied for B, C"):
        optimize_inverse_volatility(SYMBOLS, {"A": 0.10})


def test_a_non_positive_volatility_is_still_refused() -> None:
    """The check that already existed, unchanged."""

    with pytest.raises(OptimizationError, match="Volatility for B must be > 0"):
        optimize_inverse_volatility(("A", "B"), {"A": 0.10, "B": 0.0})


@pytest.mark.parametrize(
    "matrix",
    [
        pytest.param(((0.04, 0.01), (0.01, 0.09)), id="too-few-rows"),
        pytest.param(
            ((0.04, 0.01, 0.0), (0.01, 0.09, 0.0), (0.0, 0.0, 0.02), (0.0, 0.0, 0.0)),
            id="too-many-rows",
        ),
    ],
)
def test_a_covariance_matrix_sized_to_other_symbols_is_refused(
    matrix: tuple[tuple[float, ...], ...],
) -> None:
    with pytest.raises(OptimizationError, match="rows for 3 symbols"):
        optimize_minimum_variance(SYMBOLS, matrix)


def test_a_ragged_covariance_matrix_is_refused_with_the_row_named() -> None:
    ragged = ((0.04, 0.01, 0.0), (0.01, 0.09), (0.0, 0.0, 0.02))

    with pytest.raises(OptimizationError, match="row 1 has 2 entries"):
        optimize_minimum_variance(SYMBOLS, ragged)


def test_the_refusal_is_the_packages_own_error_type_not_indexerror() -> None:
    """A caller handling OptimizationError used not to catch these at all."""

    calls: tuple[Callable[[], dict[str, float]], ...] = (
        lambda: optimize_minimum_variance(SYMBOLS, ((0.04, 0.01), (0.01, 0.09))),
        lambda: optimize_maximum_sharpe(SYMBOLS, (0.1, 0.2), COV_3),
        lambda: optimize_inverse_volatility(SYMBOLS, {"A": 0.1}),
    )
    for call in calls:
        with pytest.raises(OptimizationError):
            call()


def test_maximum_sharpe_checks_both_inputs_not_only_the_first() -> None:
    with pytest.raises(OptimizationError, match="rows for 3 symbols"):
        optimize_maximum_sharpe(SYMBOLS, (0.1, 0.2, 0.3), ((0.04, 0.01), (0.01, 0.09)))


# ---------------------------------------------------------------------------
# The correct inputs still work, unchanged
# ---------------------------------------------------------------------------


def test_consistent_inputs_are_unaffected() -> None:
    equal = optimize_equal_weight(SYMBOLS)
    assert equal == {
        "A": pytest.approx(1 / 3),
        "B": pytest.approx(1 / 3),
        "C": pytest.approx(1 / 3),
    }

    inverse = optimize_inverse_volatility(SYMBOLS, {"A": 0.10, "B": 0.20, "C": 0.40})
    assert inverse["A"] > inverse["B"] > inverse["C"]
    assert sum(inverse.values()) == pytest.approx(1.0)

    minimum = optimize_minimum_variance(SYMBOLS, COV_3)
    assert sum(minimum.values()) == pytest.approx(1.0)
    assert set(minimum) == set(SYMBOLS)

    tangency = optimize_maximum_sharpe(SYMBOLS, (0.08, 0.12, 0.05), COV_3)
    assert sum(abs(w) for w in tangency.values()) == pytest.approx(1.0)


def test_an_empty_universe_is_still_an_empty_portfolio_and_not_an_error() -> None:
    """Refusing this would break the documented empty case."""

    assert optimize_equal_weight(()) == {}
    assert optimize_inverse_volatility((), {}) == {}
    assert optimize_minimum_variance((), ()) == {}
    assert optimize_maximum_sharpe((), (), ()) == {}


# ---------------------------------------------------------------------------
# The inversion, held to what it claims
# ---------------------------------------------------------------------------


def test_a_singular_covariance_is_refused_rather_than_inverted() -> None:
    """Two share classes of one issuer. The duplicate row eliminates exactly."""

    duplicated = ((0.04, 0.04, 0.01), (0.04, 0.04, 0.01), (0.01, 0.01, 0.09))

    with pytest.raises(OptimizationError, match="Singular matrix"):
        optimize_minimum_variance(SYMBOLS, duplicated)


def test_a_near_degenerate_covariance_still_inverts_accurately() -> None:
    """Elimination without pivoting is backward stable on an SPD matrix."""

    from alphalab.portfolio_optimizer.optimizer import _invert_matrix

    epsilon = 1e-7
    cov = (
        (0.040000, 0.039996, 0.010000),
        (0.039996, 0.040000 + epsilon, 0.010000),
        (0.010000, 0.010000, 0.090000),
    )
    inverse = _invert_matrix(cov)

    residual = max(
        abs(sum(inverse[i][k] * cov[k][j] for k in range(3)) - (1.0 if i == j else 0.0))
        for i in range(3)
        for j in range(3)
    )
    assert residual < 1e-6, f"Sigma^-1 @ Sigma departs from the identity by {residual}"


def test_weights_are_permutation_stable() -> None:
    """The same assets in another order must get the same weights."""

    forward = optimize_minimum_variance(SYMBOLS, COV_3)
    order = (2, 0, 1)
    permuted_symbols = tuple(SYMBOLS[i] for i in order)
    permuted_cov = tuple(tuple(COV_3[i][j] for j in order) for i in order)

    backward = optimize_minimum_variance(permuted_symbols, permuted_cov)

    for symbol in SYMBOLS:
        assert forward[symbol] == pytest.approx(backward[symbol], abs=1e-12)
