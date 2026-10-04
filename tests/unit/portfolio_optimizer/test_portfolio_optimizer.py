"""Comprehensive tests validating strict portfolio allocation,
constraints, and matrix optimization."""

import dataclasses
import math

import pytest

from alphalab.portfolio_optimizer import (
    CapitalAllocation,
    ConstraintViolationError,
    CostModel,
    OptimizationError,
    Portfolio,
    PortfolioEngine,
    PortfolioEngineState,
    PortfolioMetrics,
    PortfolioValidationError,
    RebalanceTrigger,
    RiskConstraints,
    WeightConstraints,
    allocation_report,
    apply_weight_constraints,
    calculate_max_drawdown,
    check_schedule_rebalance,
    check_threshold_rebalance,
    expected_costs,
    optimize_equal_weight,
    optimize_inverse_volatility,
    optimize_maximum_sharpe,
    optimize_minimum_variance,
    portfolio_summary,
    validate_risk_constraints,
    weight_breakdown,
)
from alphalab.portfolio_optimizer.optimizer import _invert_matrix, _matrix_vector_multiply


@pytest.fixture
def base_state() -> PortfolioEngineState:
    return PortfolioEngine.initialize("PORT-ENG-01")


@pytest.fixture
def test_portfolio() -> Portfolio:
    return Portfolio("P-1", "Test Port", "USD", 1000.0)


# --- METRICS & MATH EVALUATOR TESTS (15 Assertions) ---


def test_calculate_max_drawdown() -> None:
    returns = [0.1, -0.2, 0.1]
    assert calculate_max_drawdown(returns) == pytest.approx(0.20)
    assert calculate_max_drawdown([0.1, 0.1]) == 0.0


def test_matrix_inversion_2x2() -> None:
    matrix = ((4.0, 7.0), (2.0, 6.0))
    # Det = 24 - 14 = 10. Inv = [[0.6, -0.7], [-0.2, 0.4]]
    inv = _invert_matrix(matrix)
    assert inv[0][0] == pytest.approx(0.6)
    assert inv[0][1] == pytest.approx(-0.7)
    assert inv[1][0] == pytest.approx(-0.2)
    assert inv[1][1] == pytest.approx(0.4)


def test_matrix_inversion_singular() -> None:
    matrix = ((1.0, 1.0), (1.0, 1.0))
    with pytest.raises(OptimizationError, match="Singular matrix"):
        _invert_matrix(matrix)


def test_matrix_vector_multiply() -> None:
    matrix = ((1.0, 2.0), (3.0, 4.0))
    vector = (5.0, 6.0)
    res = _matrix_vector_multiply(matrix, vector)
    assert res[0] == 17.0
    assert res[1] == 39.0


# --- OPTIMIZATION TESTS (20 Assertions) ---


def test_optimize_equal_weight() -> None:
    weights = optimize_equal_weight(("AAPL", "MSFT", "GOOG"))
    assert len(weights) == 3
    assert weights["AAPL"] == pytest.approx(0.3333333)


def test_optimize_inverse_volatility() -> None:
    vols = {"A": 0.10, "B": 0.20}  # A is half as volatile, should get 2/3 weight
    weights = optimize_inverse_volatility(("A", "B"), vols)
    assert weights["A"] == pytest.approx(0.6666666)
    assert weights["B"] == pytest.approx(0.3333333)


def test_optimize_minimum_variance() -> None:
    # Covariance Matrix: Var(A)=0.04, Var(B)=0.09, Cov=0
    cov = ((0.04, 0.0), (0.0, 0.09))
    weights = optimize_minimum_variance(("A", "B"), cov)
    # MinVar with 0 correlation weights inversely proportional to variance
    # A should get 9/13 (0.6923), B should get 4/13 (0.3076)
    assert weights["A"] == pytest.approx(0.6923076)
    assert weights["B"] == pytest.approx(0.3076923)


def test_optimize_maximum_sharpe() -> None:
    # Ret: A=0.1, B=0.2. Cov: Var(A)=0.04, Var(B)=0.09, Cov=0
    cov = ((0.04, 0.0), (0.0, 0.09))
    rets = (0.10, 0.20)
    weights = optimize_maximum_sharpe(("A", "B"), rets, cov)
    assert weights["A"] + weights["B"] == pytest.approx(1.0)
    assert sum(weights.values()) == pytest.approx(1.0)
    assert all(w >= 0.0 for w in weights.values())


def test_an_unconstrained_portfolio_holds_the_optimizers_own_weights(
    base_state: PortfolioEngineState, test_portfolio: Portfolio
) -> None:
    """Nothing configured, nothing applied (v3.13).

    Two highly correlated assets: the minimum-variance portfolio shorts the
    riskier one. Until v3.13 the manager clipped an unconfigured portfolio with
    the defaults of ``WeightConstraints()`` and recorded ``(1.0, 0.0)`` -- not
    the minimum-variance portfolio -- under the method's name.
    """

    covariance = ((0.04, 0.05), (0.05, 0.09))
    state = PortfolioEngine.create(base_state, test_portfolio, 1000.0)
    state = PortfolioEngine.optimize(
        state, "P-1", "MINIMUM_VARIANCE", ("A", "B"), {"covariance": covariance}, 1001.0
    )
    held = state.weights["P-1"].weights
    assert held == optimize_minimum_variance(("A", "B"), covariance)
    assert held["A"] == pytest.approx(4 / 3) and held["B"] == pytest.approx(-1 / 3)

    constrained = PortfolioEngine.apply_constraints(
        PortfolioEngine.create(base_state, test_portfolio, 1000.0),
        "P-1",
        WeightConstraints(long_only=True),
        1000.5,
    )
    constrained = PortfolioEngine.optimize(
        constrained, "P-1", "MINIMUM_VARIANCE", ("A", "B"), {"covariance": covariance}, 1001.0
    )
    assert constrained.weights["P-1"].weights == {"A": 1.0, "B": 0.0}


def test_risk_constraints_state_only_the_limits_that_are_checked() -> None:
    """Three limits, each required, each checked; the three nothing read are gone (v3.13)."""

    assert [field.name for field in dataclasses.fields(RiskConstraints)] == [
        "max_drawdown_limit",
        "max_volatility_limit",
        "max_turnover",
    ]
    assert "risk_limits" not in {field.name for field in dataclasses.fields(PortfolioEngineState)}
    with pytest.raises(TypeError):
        RiskConstraints()  # type: ignore[call-arg]
    for bad in (math.nan, math.inf, -0.1, True, "0.2"):
        with pytest.raises(PortfolioValidationError, match="finite number at least zero"):
            RiskConstraints(bad, 0.2, 0.5)  # type: ignore[arg-type]

    limits = RiskConstraints(max_drawdown_limit=0.2, max_volatility_limit=0.3, max_turnover=0.5)
    within = PortfolioMetrics(
        portfolio_id="P-1",
        timestamp=1000.0,
        total_return=0.1,
        annual_return=0.1,
        volatility=0.3,
        sharpe_ratio=0.33,
        sortino_ratio=0.4,
        calmar_ratio=0.5,
        max_drawdown=0.2,
        turnover=0.5,
        diversification_ratio=1.2,
    )
    validate_risk_constraints(within, limits)
    for breach in (
        dataclasses.replace(within, max_drawdown=0.21),
        dataclasses.replace(within, volatility=0.31),
        dataclasses.replace(within, turnover=0.51),
    ):
        with pytest.raises(ConstraintViolationError):
            validate_risk_constraints(breach, limits)


# --- CONSTRAINT TESTS (15 Assertions) ---


def test_apply_weight_constraints_long_only() -> None:
    raw = {"A": 1.5, "B": -0.5}
    con = WeightConstraints(long_only=True)
    final = apply_weight_constraints(raw, con)
    assert final["A"] == 1.0
    assert final["B"] == 0.0


def test_apply_weight_constraints_max_position() -> None:
    raw = {"A": 0.8, "B": 0.2}
    con = WeightConstraints(max_position_weight=0.5)
    final = apply_weight_constraints(raw, con)
    assert final["A"] == 0.5
    assert final["B"] == 0.5


def test_apply_weight_constraints_cash_reserve() -> None:
    raw = {"A": 0.5, "B": 0.5}
    con = WeightConstraints(cash_reserve_weight=0.2)
    final = apply_weight_constraints(raw, con)
    assert final["A"] == pytest.approx(0.4)
    assert final["B"] == pytest.approx(0.4)
    assert sum(final.values()) == pytest.approx(0.8)


# --- REBALANCE TRIGGERS TESTS (10 Assertions) ---


def test_rebalance_threshold() -> None:
    cw = {"A": 0.55, "B": 0.45}
    tw = {"A": 0.50, "B": 0.50}
    assert check_threshold_rebalance(cw, tw, threshold=0.04) is True
    assert check_threshold_rebalance(cw, tw, threshold=0.06) is False


def test_rebalance_schedule() -> None:
    assert check_schedule_rebalance(0.0, 86400.0, RebalanceTrigger.DAILY) is True
    assert check_schedule_rebalance(0.0, 80000.0, RebalanceTrigger.DAILY) is False
    assert check_schedule_rebalance(0.0, 86400.0 * 7, RebalanceTrigger.WEEKLY) is True


# --- COST ESTIMATOR TESTS (10 Assertions) ---


def test_estimate_costs(base_state: PortfolioEngineState, test_portfolio: Portfolio) -> None:
    s1 = PortfolioEngine.create(base_state, test_portfolio, 1000.0)

    # Mock Target weights internally inside the engine state.
    # ``weights`` is a PersistentMap as of v2.17 (ADR-0034), so a construction
    # site passes one; reading is unchanged, because it is still a Mapping.
    from dataclasses import replace

    from alphalab.common.persistent_map import PersistentMap
    from alphalab.portfolio_optimizer.weights import TargetWeights

    s1 = replace(
        s1,
        weights=PersistentMap({"P-1": TargetWeights("P-1", 1000.0, {"A": 0.6, "B": 0.4})}),
    )

    cw = {"A": 0.5, "B": 0.5}  # Drifted weights
    model = CostModel(0.001, 0.001, 0.0, 0.0, 1.0)  # Total 0.2% + $1 fee
    s2 = PortfolioEngine.estimate_costs(s1, "P-1", cw, model, 100_000.0, 1001.0)

    # Trade fraction = |0.6-0.5| + |0.4-0.5| = 0.2. Trade value = 20,000
    # Cost = 20,000 * 0.002 + 1 = 41.0
    est = expected_costs(s2, "P-1")
    assert est is not None
    assert est.total_trade_value == pytest.approx(20000.0)
    assert est.total_estimated_cost == pytest.approx(41.0)


# --- ENGINE FACADE & LIFECYCLE TESTS (30 Assertions) ---


def test_engine_initialization() -> None:
    state = PortfolioEngine.initialize("E1")
    assert state.engine_id == "E1"
    assert len(portfolio_summary(state)) == 0
    with pytest.raises(ValueError):
        PortfolioEngine.initialize("")


def test_create_portfolio(base_state: PortfolioEngineState, test_portfolio: Portfolio) -> None:
    s1 = PortfolioEngine.create(base_state, test_portfolio, 1000.0)
    assert len(portfolio_summary(s1)) == 1
    assert any(type(e).__name__ == "PortfolioCreated" for e in s1.events)


def test_create_duplicate(base_state: PortfolioEngineState, test_portfolio: Portfolio) -> None:
    s1 = PortfolioEngine.create(base_state, test_portfolio, 1000.0)
    with pytest.raises(PortfolioValidationError, match="already exists"):
        PortfolioEngine.create(s1, test_portfolio, 1001.0)


def test_allocate_capital(base_state: PortfolioEngineState, test_portfolio: Portfolio) -> None:
    s1 = PortfolioEngine.create(base_state, test_portfolio, 1000.0)
    alloc = CapitalAllocation("P-1", 1_000_000.0, 0.0, 1_000_000.0, 0.0, 1.0)
    s2 = PortfolioEngine.allocate(s1, alloc, 1001.0)

    rep = allocation_report(s2, "P-1")
    assert rep is not None
    assert rep.total_capital == 1_000_000.0
    assert any(type(e).__name__ == "AllocationChanged" for e in s2.events)


def test_optimize_and_constrain(
    base_state: PortfolioEngineState, test_portfolio: Portfolio
) -> None:
    s1 = PortfolioEngine.create(base_state, test_portfolio, 1000.0)

    # 1. Optimize
    s2 = PortfolioEngine.optimize(s1, "P-1", "EQUAL_WEIGHT", ("A", "B"), {}, 1001.0)
    w1 = weight_breakdown(s2, "P-1")
    assert w1 is not None
    assert w1.weights["A"] == 0.5
    assert any(type(e).__name__ == "WeightsCalculated" for e in s2.events)

    # 2. Constrain
    con = WeightConstraints(max_position_weight=0.4)
    s3 = PortfolioEngine.apply_constraints(s2, "P-1", con, 1002.0)
    w2 = weight_breakdown(s3, "P-1")
    assert w2 is not None
    assert w2.weights["A"] == 0.4
    assert w2.weights["B"] == 0.4  # Rest maps to cash implicitly due to constraint
    assert any(type(e).__name__ == "ConstraintViolated" for e in s3.events)


def test_engine_rebalance_trigger(
    base_state: PortfolioEngineState, test_portfolio: Portfolio
) -> None:
    s1 = PortfolioEngine.create(base_state, test_portfolio, 1000.0)
    s2 = PortfolioEngine.optimize(s1, "P-1", "EQUAL_WEIGHT", ("A", "B"), {}, 1001.0)

    cw = {"A": 0.6, "B": 0.4}  # 10% drift from 0.5 Target
    s3 = PortfolioEngine.rebalance(s2, "P-1", cw, RebalanceTrigger.THRESHOLD, 1000.0, 1002.0)
    assert any(type(e).__name__ == "Rebalanced" for e in s3.events)


def test_immutability(base_state: PortfolioEngineState, test_portfolio: Portfolio) -> None:
    s1 = PortfolioEngine.create(base_state, test_portfolio, 1000.0)
    assert s1 is not base_state
    assert len(base_state.portfolios) == 0
    assert len(s1.portfolios) == 1
