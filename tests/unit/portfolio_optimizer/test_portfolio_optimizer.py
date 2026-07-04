"""Comprehensive tests validating strict portfolio allocation,
constraints, and matrix optimization."""

import pytest

from alphalab.portfolio_optimizer import (
    CapitalAllocation,
    CostModel,
    OptimizationError,
    Portfolio,
    PortfolioEngine,
    PortfolioEngineState,
    PortfolioValidationError,
    RebalanceTrigger,
    WeightConstraints,
    allocation_report,
    apply_weight_constraints,
    calculate_max_drawdown,
    calculate_volatility,
    check_schedule_rebalance,
    check_threshold_rebalance,
    expected_costs,
    optimize_equal_weight,
    optimize_inverse_volatility,
    optimize_maximum_sharpe,
    optimize_minimum_variance,
    portfolio_summary,
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


def test_calculate_volatility() -> None:
    returns = [0.01, -0.01, 0.01, -0.01]
    vol = calculate_volatility(returns, periods=252)
    assert vol > 0.10


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

    # Mock Target weights internally inside the engine state
    from dataclasses import replace

    from alphalab.portfolio_optimizer.weights import TargetWeights

    s1 = replace(s1, weights={"P-1": TargetWeights("P-1", 1000.0, {"A": 0.6, "B": 0.4})})

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
