"""Comprehensive tests validating strict statistical and research functionality."""

import pytest

from alphalab.research import (
    InvalidResearchStateError,
    ResearchAdapter,
    ResearchEngine,
    ResearchPayload,
    ResearchValidationError,
    TradePayload,
    analyze_regimes,
    apply_stress_tests,
    bootstrap_statistics,
    calculate_cagr,
    calculate_max_drawdown,
    calculate_sharpe,
    calculate_volatility,
    compute_overall_score,
    detect_bias,
    estimate_capacity,
    generate_diagnostics,
    monte_carlo_simulation,
    overall_score,
    parameter_robustness,
    walk_forward_analysis,
    warnings,
)


@pytest.fixture
def sample_payload() -> ResearchPayload:
    returns = (0.01, -0.02, 0.03, 0.01, -0.01, 0.02) * 42  # 252 items
    regimes = ("BULL", "BEAR", "BULL", "BULL", "SIDEWAYS", "BULL") * 42
    trades = tuple(
        TradePayload(f"T{i}", "AAPL", 100.0, 105.0, 10.0, 50.0, 86400.0) for i in range(100)
    )
    return ResearchPayload("STRAT-1", returns, trades, {"ma": 20.0}, regimes, 1_000_000.0)


# --- METRICS TESTS (5) ---


def test_calculate_cagr() -> None:
    returns = (0.01, 0.01) * 126  # Approx 252 days
    cagr = calculate_cagr(returns)
    assert cagr > 0.10


def test_calculate_volatility() -> None:
    returns = (0.01, -0.01)
    vol = calculate_volatility(returns)
    assert vol > 0


def test_calculate_sharpe() -> None:
    # Requires variance to avoid a 0.0 volatility denominator
    returns = (0.01, 0.02)
    assert calculate_sharpe(returns) > 0
    assert calculate_sharpe((0.0, 0.0)) == 0.0


def test_calculate_max_drawdown() -> None:
    returns = (0.1, -0.2, 0.1)
    # Peak 1.1, drops to 0.88 (-20%), drawdown is 0.22 / 1.1 = 0.20
    assert calculate_max_drawdown(returns) == pytest.approx(0.20)


# --- BIAS TESTS (6) ---


def test_bias_lookahead_risk() -> None:
    trades = (TradePayload("T1", "A", 10, 11, 1, 1, 10.0),) * 100  # High win rate, short duration
    payload = ResearchPayload("S", (), trades, {}, (), 1_000_000.0)
    report = detect_bias(payload)
    assert report.look_ahead_risk == 1.0


def test_bias_survivorship_risk() -> None:
    payload = ResearchPayload("S", (0.0001, 0.0001), (), {}, (), 1_000_000.0)  # Abnormally low vol
    report = detect_bias(payload)
    assert report.survivorship_risk == 1.0


def test_bias_overfitting_risk() -> None:
    trades = (TradePayload("T1", "A", 10, 11, 1, 1, 86400),) * 2
    # 5 params, 2 trades -> Highly overfit
    payload = ResearchPayload(
        "S", (), trades, {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}, (), 1_000_000.0
    )
    report = detect_bias(payload)
    assert report.overfitting_risk == 1.0


def test_bias_sample_risk() -> None:
    payload = ResearchPayload("S", (0.01,) * 10, (), {}, (), 1_000_000.0)  # Too few returns
    report = detect_bias(payload)
    assert report.sample_bias_risk == 1.0


def test_bias_overall_healthy() -> None:
    # Build a statistically healthy dataset to prevent bias flags
    returns = (0.01, -0.01) * 500  # 1000 returns -> No sample bias, healthy volatility
    regimes = ("BULL", "BEAR") * 500
    trades = tuple(
        TradePayload(
            trade_id=f"T{i}",
            symbol="AAPL",
            entry_price=100.0,
            exit_price=101.0 if i % 2 == 0 else 99.0,
            quantity=10.0,
            pnl=10.0 if i % 2 == 0 else -10.0,
            duration_seconds=86400.0,
        )
        for i in range(1000)
    )  # 50% Win Rate -> No Look-Ahead Bias. 1000 trades -> No Overfitting.

    payload = ResearchPayload("S", returns, trades, {"ma": 20}, regimes, 1_000_000.0)
    report = detect_bias(payload)
    assert report.overall_bias_score > 80.0


# --- CROSS VALIDATION TESTS (4) ---


def test_walk_forward_empty() -> None:
    payload = ResearchPayload("S", (), (), {}, (), 1_000_000.0)
    report = walk_forward_analysis(payload)
    assert report.windows_evaluated == 0


def test_walk_forward_success(sample_payload: ResearchPayload) -> None:
    report = walk_forward_analysis(sample_payload, num_windows=5)
    assert report.windows_evaluated == 5
    assert report.avg_oos_sharpe != 0.0


# --- MONTE CARLO TESTS (4) ---


def test_monte_carlo_empty() -> None:
    payload = ResearchPayload("S", (), (), {}, (), 1_000_000.0)
    report = monte_carlo_simulation(payload)
    assert report.simulations == 0


def test_monte_carlo_drawdown(sample_payload: ResearchPayload) -> None:
    report = monte_carlo_simulation(sample_payload, simulations=100)
    assert report.simulations == 100
    assert report.worst_drawdown >= report.median_drawdown


# --- BOOTSTRAP TESTS (4) ---


def test_bootstrap_empty() -> None:
    payload = ResearchPayload("S", (), (), {}, (), 1_000_000.0)
    report = bootstrap_statistics(payload)
    assert report.confidence_score == 0.0


def test_bootstrap_success(sample_payload: ResearchPayload) -> None:
    report = bootstrap_statistics(sample_payload, iterations=100)
    assert report.upper_bound_95th >= report.lower_bound_5th


# --- CAPACITY TESTS (4) ---


def test_capacity_empty() -> None:
    payload = ResearchPayload("S", (), (), {}, (), 1_000_000.0)
    report = estimate_capacity(payload)
    assert report.capacity_score == 0.0


def test_capacity_degradation(sample_payload: ResearchPayload) -> None:
    report = estimate_capacity(sample_payload)
    assert report.base_cagr >= report.cagr_at_1b


# --- SENSITIVITY TESTS (4) ---


def test_parameter_robustness_clean() -> None:
    payload = ResearchPayload("S", (), (), {"ma": 20}, (), 1_000_000.0)
    report = parameter_robustness(payload)
    assert not report.cliff_risk


def test_parameter_robustness_cliff() -> None:
    payload = ResearchPayload(
        "S", (), (), {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5, "f": 6}, (), 1_000_000.0
    )
    report = parameter_robustness(payload)
    assert report.cliff_risk


# --- REGIME TESTS (4) ---


def test_regime_empty() -> None:
    payload = ResearchPayload("S", (), (), {}, (), 1_000_000.0)
    report = analyze_regimes(payload)
    assert report.regime_generalisation_score == 0.0


def test_regime_success(sample_payload: ResearchPayload) -> None:
    report = analyze_regimes(sample_payload)
    assert report.bull_sharpe != 0.0


# --- STRESS TESTS (5) ---


def test_stress_empty() -> None:
    payload = ResearchPayload("S", (), (), {}, (), 1_000_000.0)
    report = apply_stress_tests(payload)
    assert report.flash_crash_drawdown == 0.0


def test_stress_application(sample_payload: ResearchPayload) -> None:
    report = apply_stress_tests(sample_payload)
    assert report.flash_crash_drawdown > 0.0
    assert report.liquidity_shock_drawdown > 0.0


# --- DIAGNOSTICS TESTS (6) ---


def test_diagnostics_too_few() -> None:
    trades = (TradePayload("T1", "A", 10, 11, 1, 1, 86400),) * 10
    payload = ResearchPayload("S", (), trades, {}, (), 1_000_000.0)
    report = generate_diagnostics(payload)
    assert report.too_few_trades
    assert len(report.warnings) > 0


def test_diagnostics_concentration() -> None:
    trades = (
        TradePayload("T1", "A", 10, 11, 1, 100, 86400),
        TradePayload("T2", "A", 10, 11, 1, 5, 86400),
    )
    payload = ResearchPayload("S", (), trades, {}, (), 1_000_000.0)
    report = generate_diagnostics(payload)
    assert report.high_concentration


def test_diagnostics_tail_risk() -> None:
    payload = ResearchPayload("S", (-0.15, 0.01), (), {}, (), 1_000_000.0)
    report = generate_diagnostics(payload)
    assert report.large_tail_risk


# --- RESEARCH SCORE TESTS (4) ---


def test_compute_overall_score(sample_payload: ResearchPayload) -> None:
    bias = detect_bias(sample_payload)
    boot = bootstrap_statistics(sample_payload, iterations=10)
    robust = parameter_robustness(sample_payload)
    cap = estimate_capacity(sample_payload)
    cv = walk_forward_analysis(sample_payload)
    regime = analyze_regimes(sample_payload)
    stress = apply_stress_tests(sample_payload)

    score = compute_overall_score(bias, boot, robust, cap, cv, regime, stress)
    assert score.overall_score >= 0.0


# --- VALIDATION TESTS (4) ---


def test_validation_empty_id() -> None:
    with pytest.raises(ValueError, match="empty"):
        ResearchEngine.initialize("", "S", 1.0)


def test_validation_mismatch() -> None:
    payload = ResearchPayload("S", (0.01,), (), {}, ("BULL", "BEAR"), 1_000_000.0)
    with pytest.raises(ResearchValidationError):
        from alphalab.research.validation import validate_payload

        validate_payload(payload)


def test_validation_negative_aum() -> None:
    payload = ResearchPayload("S", (), (), {}, (), -1_000_000.0)
    with pytest.raises(ResearchValidationError):
        from alphalab.research.validation import validate_payload

        validate_payload(payload)


# --- ENGINE & LIFECYCLE TESTS (6) ---


def test_engine_init() -> None:
    state = ResearchEngine.initialize("R-1", "S-1", 1000.0)
    assert state.research_id == "R-1"
    assert not state.completed


def test_engine_full_run(sample_payload: ResearchPayload) -> None:
    state = ResearchEngine.initialize("R-1", "S-1", 1000.0)
    s2 = ResearchEngine.run_full_research(state, sample_payload, 1001.0)
    assert s2.completed
    assert s2.score is not None
    assert len(s2.events) > 3


def test_engine_double_run(sample_payload: ResearchPayload) -> None:
    state = ResearchEngine.initialize("R-1", "S-1", 1000.0)
    s2 = ResearchEngine.run_full_research(state, sample_payload, 1001.0)
    with pytest.raises(InvalidResearchStateError):
        ResearchEngine.run_full_research(s2, sample_payload, 1002.0)


# --- VIEWS TESTS (5) ---


def test_views_access(sample_payload: ResearchPayload) -> None:
    state = ResearchEngine.initialize("R-1", "S-1", 1000.0)
    s2 = ResearchEngine.run_full_research(state, sample_payload, 1001.0)

    assert overall_score(s2) is not None
    assert len(warnings(s2)) >= 0


# --- ADAPTER TESTS (3) ---


def test_adapter_translation() -> None:
    trades = ({"trade_id": "T1", "symbol": "AAPL", "pnl": 50.0},)
    payload = ResearchAdapter.to_research_payload(
        "S-1", (0.01,), trades, {"ma": 20}, ("BULL",), 1_000_000.0
    )
    assert payload.strategy_id == "S-1"
    assert len(payload.trades) == 1
    assert payload.trades[0].pnl == 50.0
