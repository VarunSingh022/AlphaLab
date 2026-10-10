"""Linear transaction costs in construction, and rounding its answer to lots (OFE-002).

The cost-bearing optimum is checked against brute force that shares no code
with the solver: the objective is linear on each orthant around the current
book, so the true optimum is the best of the exact optima of every orthant's
quadratic program (:func:`tests.unit.portfolio_optimizer.qp_reference.exact_optimum`),
compared by the true, costed objective in rational arithmetic.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Mapping, Sequence
from decimal import Decimal, localcontext
from fractions import Fraction

import pytest

from alphalab.analytics.risk_model import CovarianceMatrix
from alphalab.conventions.economics import CASH_EQUITY, InstrumentEconomics, SettlementModel
from alphalab.conventions.lot import LotSpecification
from alphalab.portfolio_optimizer import LinearCosts, LotRounding, round_to_lots
from alphalab.portfolio_optimizer.construction import (
    ConstraintSet,
    ConstructionProblem,
    ConstructionResult,
    ConstructionStatus,
    ExpectedReturns,
    ExposureRange,
    MeanVariance,
    SolverSettings,
    WeightBounds,
    construct,
)
from alphalab.portfolio_optimizer.exceptions import ConstructionInputError
from tests.unit.portfolio_optimizer.qp_reference import Row, exact_optimum

ASSETS = ("A", "B", "C")
ROWS = ((0.04, 0.01, 0.0), (0.01, 0.09, 0.02), (0.0, 0.02, 0.16))
SETTINGS = SolverSettings(1e-10, 1e-10, 500)
MU = ExpectedReturns({"A": 0.01, "B": 0.02, "C": 0.015}, "USD", "1M", "unit forecast")
COVARIANCE = CovarianceMatrix.from_rows(
    ASSETS, ROWS, currency="USD", period="1M", source="unit", observations=None
)


def _solve(
    objective: MeanVariance,
    constraints: ConstraintSet,
    covariance: CovarianceMatrix = COVARIANCE,
) -> ConstructionResult:
    return construct(ConstructionProblem(covariance, objective, constraints, SETTINGS))


def _weights(result: ConstructionResult) -> list[float]:
    assert result.weights is not None, result.diagnostics.detail
    return [result.weights[asset] for asset in sorted(result.weights)]


def _brute_force(
    rows: Sequence[Sequence[float]],
    aversion: float,
    mu: Sequence[float],
    current: Sequence[float],
    rates: Sequence[float],
    constraints: Sequence[Row],
) -> list[float]:
    """The costed optimum: the best exact orthant optimum by the true objective."""

    size = len(mu)
    hessian = [[aversion * value for value in row] for row in rows]
    best: tuple[Fraction, list[float]] | None = None
    for signs in itertools.product((1.0, -1.0), repeat=size):
        sides: list[Row] = [
            ([signs[i] if j == i else 0.0 for j in range(size)], signs[i] * current[i], False)
            for i in range(size)
        ]
        linear = [-mu[i] + rates[i] * signs[i] for i in range(size)]
        x = exact_optimum(hessian, linear, [*constraints, *sides])
        if x is None:
            continue
        exact = [Fraction(value) for value in x]
        value: Fraction = Fraction(0) + (
            sum(
                Fraction(1, 2) * exact[i] * Fraction(hessian[i][j]) * exact[j]
                for i in range(size)
                for j in range(size)
            )
            - sum(Fraction(mu[i]) * exact[i] for i in range(size))
            + sum(Fraction(rates[i]) * abs(exact[i] - Fraction(current[i])) for i in range(size))
        )
        if best is None or value < best[0]:
            best = (value, x)
    assert best is not None
    return best[1]


def _budget_row() -> Row:
    return ([1.0, 1.0, 1.0], 1.0, True)


def _bound_rows(lower: float, upper: float) -> list[Row]:
    return [
        *(([1.0 if i == j else 0.0 for j in range(3)], lower, False) for i in range(3)),
        *(([-1.0 if i == j else 0.0 for j in range(3)], -upper, False) for i in range(3)),
    ]


# --------------------------------------------------------------------------- #
# The optimum
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", range(40))
def test_the_costed_optimum_is_the_best_of_every_orthant(seed: int) -> None:
    rng = random.Random(seed)
    long_only = seed % 2 == 0
    mu = [rng.uniform(-0.01, 0.03) for _ in range(3)]
    # Current books on the bounds and off them: a held weight of exactly zero
    # under a long-only bound is the degenerate vertex a side switch must
    # survive.
    current = [rng.choice([0.0, 0.2, 0.6, rng.uniform(0.0, 0.6)]) for _ in range(3)]
    rates = [rng.choice([0.0, rng.uniform(0.0, 0.01), rng.uniform(0.0, 0.05)]) for _ in range(3)]
    aversion = rng.uniform(0.5, 6.0)
    bounds = WeightBounds.long_only(0.6) if long_only else WeightBounds.unbounded()
    costs = LinearCosts(
        dict(zip(ASSETS, current, strict=True)), dict(zip(ASSETS, rates, strict=True))
    )
    result = _solve(
        MeanVariance(
            ExpectedReturns(dict(zip(ASSETS, mu, strict=True)), "USD", "1M", "unit"),
            aversion,
            costs,
        ),
        ConstraintSet(ExposureRange.exactly(1.0), bounds),
    )
    reference = _brute_force(
        ROWS,
        aversion,
        mu,
        current,
        rates,
        [_budget_row(), *(_bound_rows(0.0, 0.6) if long_only else [])],
    )

    assert result.status is ConstructionStatus.OPTIMAL, result.diagnostics.detail
    assert _weights(result) == pytest.approx(reference, abs=1e-9)
    # These cases start on the wrong orthant -- the cost-free optimum trades
    # the other way -- so the certificate must send them across.
    if any(rates):
        switches = 1 if seed in (9, 17, 27) else 0
        assert f"after {switches} side switch(es)" in result.diagnostics.detail
    assert result.diagnostics.transaction_cost == pytest.approx(
        math.fsum(r * abs(w - c) for r, w, c in zip(rates, _weights(result), current, strict=True)),
        abs=1e-15,
    )


def test_costs_high_enough_leave_the_book_where_it_is() -> None:
    current = {"A": 0.5, "B": 0.3, "C": 0.2}
    result = _solve(
        MeanVariance(MU, 3.0, LinearCosts(current, {"A": 1.0, "B": 1.0, "C": 1.0})),
        ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.unbounded()),
    )

    assert _weights(result) == pytest.approx([0.5, 0.3, 0.2], abs=1e-12)
    assert result.diagnostics.transaction_cost == pytest.approx(0.0, abs=1e-12)
    held = [label for label in result.diagnostics.binding if label.startswith("no trade in")]
    assert len(held) >= 2  # the budget pins the third


def test_an_infeasible_current_book_is_traded_only_as_far_as_it_must_be() -> None:
    # Twelve percent over budget: the costed optimum sells what is cheapest to
    # sell, and only as much as the budget needs.
    current = {"A": 0.5, "B": 0.4, "C": 0.22}
    rates = {"A": 0.5, "B": 0.5, "C": 0.01}
    result = _solve(
        MeanVariance(MU, 3.0, LinearCosts(current, rates)),
        ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(1.0)),
    )

    assert _weights(result) == pytest.approx([0.5, 0.4, 0.1], abs=1e-12)
    assert result.diagnostics.transaction_cost == pytest.approx(0.01 * 0.12, abs=1e-15)


def test_turnover_falls_as_costs_rise() -> None:
    current = {"A": 0.2, "B": 0.2, "C": 0.6}
    turnover: list[float] = []
    for rate in (0.0, 0.0005, 0.001, 0.002, 0.004, 0.008, 0.016):
        result = _solve(
            MeanVariance(MU, 4.0, LinearCosts(current, dict.fromkeys(ASSETS, rate))),
            ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(1.0)),
        )
        weights = _weights(result)
        turnover.append(
            math.fsum(abs(w - current[a]) for a, w in zip(ASSETS, weights, strict=True))
        )

    assert all(later <= earlier + 1e-12 for earlier, later in itertools.pairwise(turnover))
    assert turnover[0] > 0.0
    assert turnover[-1] < turnover[0]


def test_zero_rates_are_the_cost_free_portfolio() -> None:
    constraints = ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(0.6))
    free = _solve(MeanVariance(MU, 2.0), constraints)
    zero = _solve(
        MeanVariance(
            MU, 2.0, LinearCosts({"A": 0.3, "B": 0.3, "C": 0.4}, dict.fromkeys(ASSETS, 0.0))
        ),
        constraints,
    )

    assert _weights(zero) == _weights(free)
    assert zero.diagnostics.transaction_cost == 0.0
    assert free.diagnostics.transaction_cost is None


def test_a_cost_free_objective_keeps_the_identity_it_had() -> None:
    plain = MeanVariance(MU, 2.0)
    costed = MeanVariance(MU, 2.0, LinearCosts({"A": 0.0}, {"A": 0.001}))

    assert plain.rendering() == [
        "objective=mean_variance",
        f"expected_returns={MU.returns_id}",
        "risk_aversion=2.0",
    ]
    assert costed.rendering()[:3] == plain.rendering()
    assert costed.rendering()[3:] == ["costs.current['A']=0.0", "costs.rate['A']=0.001"]


def test_a_volatility_cap_binds_the_costed_problem_at_its_effective_aversion() -> None:
    cap = 0.2
    current = [0.1, 0.1, 0.8]
    rates = [0.001, 0.001, 0.001]
    result = _solve(
        MeanVariance(
            MU,
            0.1,
            LinearCosts(dict(zip(ASSETS, current, strict=True)), dict.fromkeys(ASSETS, 0.001)),
        ),
        ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(1.0), max_volatility=cap),
    )
    weights = _weights(result)
    volatility = math.sqrt(
        math.fsum(weights[i] * ROWS[i][j] * weights[j] for i in range(3) for j in range(3))
    )
    effective = result.diagnostics.effective_risk_aversion

    assert result.status is ConstructionStatus.OPTIMAL, result.diagnostics.detail
    # Uncapped, this book would run at 26.6% volatility; the cap binds.
    assert volatility == pytest.approx(cap, rel=1e-8)
    assert effective is not None and effective > 0.1
    # The capped optimum is the costed optimum at the effective risk aversion.
    reference = _brute_force(
        ROWS,
        effective,
        [MU.values[a] for a in ASSETS],
        current,
        rates,
        [_budget_row(), *_bound_rows(0.0, 1.0)],
    )
    assert weights == pytest.approx(reference, abs=1e-8)


def test_the_same_costed_problem_gives_the_same_result() -> None:
    problem = ConstructionProblem(
        COVARIANCE,
        MeanVariance(
            MU, 3.0, LinearCosts({"A": 0.2, "B": 0.5, "C": 0.3}, {"A": 0.002, "B": 0.0, "C": 0.01})
        ),
        ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(1.0)),
        SETTINGS,
    )

    assert construct(problem).result_id == construct(problem).result_id


def test_linear_costs_refuse_what_they_cannot_mean() -> None:
    with pytest.raises(ConstructionInputError, match="trading does not pay"):
        LinearCosts({"A": 0.0}, {"A": -0.001})
    with pytest.raises(ConstructionInputError, match="same assets"):
        LinearCosts({"A": 0.0, "B": 0.0}, {"A": 0.001})
    with pytest.raises(ConstructionInputError):
        LinearCosts({"A": float("nan")}, {"A": 0.001})
    with pytest.raises(ConstructionInputError, match="LinearCosts"):
        MeanVariance(MU, 2.0, {"A": 0.001})  # type: ignore[arg-type]
    with pytest.raises(ConstructionInputError, match="exactly the universe"):
        _solve(
            MeanVariance(MU, 2.0, LinearCosts({"A": 0.0, "B": 0.0}, {"A": 0.0, "B": 0.0})),
            ConstraintSet(ExposureRange.exactly(1.0), WeightBounds.long_only(1.0)),
        )


# --------------------------------------------------------------------------- #
# Lots
# --------------------------------------------------------------------------- #

HUNDREDS = InstrumentEconomics(
    Decimal("1"),
    SettlementModel.CASH_EQUITY,
    lot=LotSpecification(Decimal("100"), Decimal("100")),
)
FUTURE = InstrumentEconomics(
    Decimal("50"),
    SettlementModel.FUTURES_VARIATION,
    lot=LotSpecification(Decimal("1"), Decimal("1")),
)


def _round(
    weights: Mapping[str, float],
    prices: Mapping[str, Decimal],
    economics: Mapping[str, InstrumentEconomics],
    capital: Decimal = Decimal("1000000"),
) -> LotRounding:
    return round_to_lots(
        weights, capital=capital, prices=prices, economics=economics, quantum=Decimal("0.000001")
    )


def test_weights_become_whole_lots_toward_zero() -> None:
    rounding = _round(
        {"A": 0.5, "B": 0.3, "F": 0.2},
        {"A": Decimal("33"), "B": Decimal("7.5"), "F": Decimal("4000")},
        {"A": HUNDREDS, "B": CASH_EQUITY, "F": FUTURE},
    )

    # 500,000 / 33 = 15,151.5... shares -> 15,100 in lots of 100.
    assert rounding.quantities["A"] == Decimal("15100")
    # 300,000 / 7.5 = 40,000 exactly: no lot, nothing to round.
    assert rounding.quantities["B"] == Decimal("40000.000000")
    # 200,000 / (4,000 * 50) = 1 contract.
    assert rounding.quantities["F"] == Decimal("1")
    assert rounding.notionals["A"] == Decimal("498300")
    assert rounding.weights["A"] == pytest.approx(0.4983)
    assert rounding.residuals["A"] == pytest.approx(0.5 - 0.4983)
    assert rounding.residuals["B"] == 0.0
    assert rounding.below_one_lot == ()
    assert rounding.residual_weight == pytest.approx(0.0017)


def test_a_short_rounds_toward_zero_too() -> None:
    rounding = _round({"A": -0.25}, {"A": Decimal("33")}, {"A": HUNDREDS})

    # -250,000 / 33 = -7,575.7... -> -7,500: a smaller short, never a larger one.
    assert rounding.quantities["A"] == Decimal("-7500")
    assert rounding.residuals["A"] < 0.0


def test_a_weight_below_one_lot_is_named_not_dropped() -> None:
    rounding = _round(
        {"A": 0.001, "B": 0.0}, {"A": Decimal("33"), "B": Decimal("10")}, {"A": HUNDREDS}
    )

    assert rounding.quantities == {"A": Decimal("0"), "B": Decimal("0")}
    assert rounding.below_one_lot == ("A",)
    assert rounding.residuals["A"] == 0.001


@pytest.mark.parametrize("seed", range(30))
def test_rounding_never_adds_exposure_and_misses_by_less_than_a_lot(seed: int) -> None:
    rng = random.Random(seed)
    capital = Decimal(rng.randint(10_000, 10_000_000))
    weights = {f"X{i}": rng.uniform(-0.4, 0.4) for i in range(6)}
    prices = {asset: Decimal(str(round(rng.uniform(0.5, 900.0), 2))) for asset in weights}
    lots = [Decimal("1"), Decimal("10"), Decimal("100"), Decimal("0.001")]
    economics = {
        asset: InstrumentEconomics(
            Decimal(rng.choice(["1", "10", "50"])),
            SettlementModel.CASH_EQUITY,
            lot=(lambda size: LotSpecification(size, size))(rng.choice(lots)),
        )
        for asset in weights
    }
    rounding = _round(weights, prices, economics, capital)

    for asset, weight in weights.items():
        target = Fraction(capital) * Fraction(repr(weight))
        unit = Fraction(prices[asset]) * Fraction(economics[asset].multiplier)
        lot = economics[asset].lot
        assert lot is not None
        notional = Fraction(rounding.notionals[asset])
        assert abs(notional) <= abs(target)
        assert notional * target >= 0
        assert (
            abs(target) - abs(notional) < Fraction(lot.lot_size) * unit + Fraction(1, 10**6) * unit
        )
        assert rounding.quantities[asset] % lot.lot_size == 0


def test_rounding_is_the_same_whatever_the_callers_decimal_context() -> None:
    args = (
        {"A": 0.123456789, "B": -0.2},
        {"A": Decimal("12.3456789"), "B": Decimal("0.987654321")},
        {"A": CASH_EQUITY, "B": HUNDREDS},
    )
    expected = _round(*args)
    for precision in (3, 5, 9):
        with localcontext() as context:
            context.prec = precision
            assert _round(*args) == expected


def test_a_weight_enters_as_it_is_written() -> None:
    rounding = _round({"A": 0.1}, {"A": Decimal("3")}, {"A": CASH_EQUITY})

    # 0.1 of 1,000,000 is 100,000 -- not 100,000.0000000000055511... -- so a
    # third of it is 33,333.333333 after the quantum, exactly.
    assert rounding.quantities["A"] == Decimal("33333.333333")


def test_lot_rounding_refuses_what_it_cannot_size() -> None:
    with pytest.raises(ConstructionInputError, match="no price"):
        _round({"A": 0.1}, {}, {"A": CASH_EQUITY})
    with pytest.raises(ConstructionInputError, match="no economics"):
        _round({"A": 0.1}, {"A": Decimal("3")}, {})
    with pytest.raises(ConstructionInputError, match="positive price"):
        _round({"A": 0.1}, {"A": Decimal("-3")}, {"A": CASH_EQUITY})
    with pytest.raises(ConstructionInputError, match="positive price"):
        _round({"A": 0.1}, {"A": Decimal("0")}, {"A": CASH_EQUITY})
    with pytest.raises(ConstructionInputError, match="not weighted"):
        _round({"A": 0.1}, {"A": Decimal("3"), "Z": Decimal("1")}, {"A": CASH_EQUITY})
    with pytest.raises(ConstructionInputError, match="finite"):
        _round({"A": float("inf")}, {"A": Decimal("3")}, {"A": CASH_EQUITY})
    with pytest.raises(ConstructionInputError, match="capital"):
        _round({"A": 0.1}, {"A": Decimal("3")}, {"A": CASH_EQUITY}, Decimal("0"))
    with pytest.raises(ConstructionInputError, match="quantum"):
        round_to_lots(
            {"A": 0.1},
            capital=Decimal("1"),
            prices={"A": Decimal("3")},
            economics={"A": CASH_EQUITY},
            quantum=Decimal("-1"),
        )
