"""The v1 run evaluation, restated over the v3.2 methodology (ledger RES-001).

Every report is a measurement, every bound is the caller's ``ResearchPolicy``,
every finding names its number and its bound, and nothing grades the strategy.
The tests below check the measurements against figures computed here, the
bounds against the findings they produce, and that the periods a year holds and
the risk-free rate are the caller's -- never 252 and zero by assumption.
"""

import math
from dataclasses import replace

import pytest

from alphalab.common.statistics import sample_variance
from alphalab.research import (
    InvalidResearchStateError,
    ResearchAdapter,
    ResearchEngine,
    ResearchPayload,
    ResearchPolicy,
    ResearchValidationError,
    TradePayload,
    analyze_regimes,
    apply_stress_tests,
    bootstrap_statistics,
    calculate_cagr,
    calculate_max_drawdown,
    calculate_sharpe,
    calculate_volatility,
    detect_bias,
    estimate_capacity,
    generate_diagnostics,
    monte_carlo_simulation,
    parameter_robustness,
    parameter_sweep,
    research_metrics_of,
    walk_forward_analysis,
    warnings,
)
from alphalab.research.validation import validate_payload

DAILY = 252
POLICY = ResearchPolicy(
    walk_forward_windows=5,
    ruin_drawdown=0.20,
    minimum_trades=50,
    maximum_trade_share=0.30,
    worst_period_return=-0.10,
    shock_return=-0.10,
    gain_multiplier=0.5,
    loss_multiplier=2.0,
)


def _payload(
    returns: tuple[float, ...] = (),
    trades: tuple[TradePayload, ...] = (),
    parameters: dict[str, float] | None = None,
    regimes: tuple[str, ...] | None = None,
    periods_per_year: int = DAILY,
    risk_free_rate: float = 0.0,
) -> ResearchPayload:
    return ResearchPayload(
        "S",
        returns,
        trades,
        {} if parameters is None else parameters,
        ("BULL",) * len(returns) if regimes is None else regimes,
        1_000_000.0,
        periods_per_year,
        risk_free_rate,
    )


@pytest.fixture
def sample_payload() -> ResearchPayload:
    returns = (0.01, -0.02, 0.03, 0.01, -0.01, 0.02) * 42  # 252 items
    regimes = ("BULL", "BEAR", "BULL", "BULL", "SIDEWAYS", "BULL") * 42
    trades = tuple(
        TradePayload(f"T{i}", "AAPL", 100.0, 105.0, 10.0, 50.0, 86400.0) for i in range(100)
    )
    return ResearchPayload(
        "STRAT-1", returns, trades, {"ma": 20.0}, regimes, 1_000_000.0, DAILY, 0.0
    )


# --- METRICS: the periods and the rate are the caller's ---


def test_annualization_uses_the_periods_it_is_given() -> None:
    returns = (0.01, -0.005, 0.02, 0.0, -0.01, 0.015)
    sd = math.sqrt(sample_variance(returns))

    for periods in (12, 52, 252, 365):
        assert calculate_volatility(returns, periods) == pytest.approx(sd * math.sqrt(periods))
        expected = (sum(returns) / len(returns) * periods - 0.03) / (sd * math.sqrt(periods))
        assert calculate_sharpe(returns, periods, 0.03) == pytest.approx(expected)


def test_cagr_is_annualized_by_the_periods_given() -> None:
    returns = (0.01,) * 12
    assert calculate_cagr(returns, 12) == pytest.approx(1.01**12 - 1.0)
    assert calculate_cagr(returns, 252) == pytest.approx(1.01**252 - 1.0)


@pytest.mark.parametrize("periods", [0, -1, True, 1.5])
def test_a_year_of_no_periods_is_refused(periods: object) -> None:
    with pytest.raises(ResearchValidationError, match="periods"):
        calculate_volatility((0.01, 0.02), periods)  # type: ignore[arg-type]
    with pytest.raises(ResearchValidationError, match="periods"):
        _payload((0.01,), periods_per_year=periods)  # type: ignore[arg-type]


def test_a_non_finite_rate_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="risk_free_rate"):
        calculate_sharpe((0.01, 0.02), 252, math.nan)
    with pytest.raises(ResearchValidationError, match="risk_free_rate"):
        _payload((0.01,), risk_free_rate=math.inf)


def test_calculate_max_drawdown() -> None:
    returns = (0.1, -0.2, 0.1)
    # Peak 1.1, drops to 0.88 (-20%), drawdown is 0.22 / 1.1 = 0.20
    assert calculate_max_drawdown(returns) == pytest.approx(0.20)


# --- REPORTS: measurements, not scores ---


def test_the_bias_report_carries_what_the_run_shows() -> None:
    trades = (
        TradePayload("T1", "A", 10, 11, 1, 5.0, 60.0),
        TradePayload("T2", "A", 10, 9, 1, -5.0, 180.0),
        TradePayload("T3", "A", 10, 12, 1, 9.0, 120.0),
    )
    payload = _payload((0.01, -0.02, 0.03), trades, {"a": 1.0, "b": 2.0})

    report = detect_bias(payload)

    assert report.win_rate == pytest.approx(2 / 3)
    assert report.mean_trade_duration_seconds == pytest.approx(120.0)
    assert report.annualized_volatility == pytest.approx(
        calculate_volatility(payload.returns, DAILY)
    )
    assert (report.parameter_count, report.trade_count, report.observations) == (2, 3, 3)
    assert report.trades_per_parameter == pytest.approx(1.5)
    assert not hasattr(report, "look_ahead_risk")


def test_the_bias_report_says_not_measured_rather_than_zero() -> None:
    report = detect_bias(_payload((0.01,)))

    assert report.win_rate is None
    assert report.mean_trade_duration_seconds is None
    assert report.annualized_volatility is None
    assert report.trades_per_parameter is None


def test_walk_forward_reports_each_windows_sharpe(sample_payload: ResearchPayload) -> None:
    report = walk_forward_analysis(sample_payload, 4)
    chunk = len(sample_payload.returns) // 4
    expected = tuple(
        calculate_sharpe(sample_payload.returns[i * chunk : (i + 1) * chunk], DAILY, 0.0)
        for i in range(4)
    )

    assert report.windows_evaluated == 4
    assert report.window_sharpes == pytest.approx(expected)
    assert report.mean_sharpe == pytest.approx(sum(expected) / 4)
    assert report.sharpe_variance == pytest.approx(sample_variance(expected))
    assert report.first_to_last_change == pytest.approx(expected[-1] - expected[0])


def test_walk_forward_on_too_short_a_series_measures_nothing() -> None:
    report = walk_forward_analysis(_payload((0.01, 0.02)), 5)

    assert (report.windows_evaluated, report.window_sharpes, report.mean_sharpe) == (0, (), None)
    with pytest.raises(ResearchValidationError):
        walk_forward_analysis(_payload((0.01, 0.02)), 0)


def test_monte_carlo_counts_ruin_against_the_stated_bound(sample_payload: ResearchPayload) -> None:
    tight = monte_carlo_simulation(sample_payload, 7, ruin_drawdown=0.01)
    loose = monte_carlo_simulation(sample_payload, 7, ruin_drawdown=0.99)

    assert tight.median_drawdown == loose.median_drawdown  # same seed, same paths
    assert tight.ruin_probability == 1.0
    assert loose.ruin_probability == 0.0
    assert tight.median_drawdown is not None
    assert tight.worst_drawdown is not None and tight.percentile_95_drawdown is not None
    assert tight.median_drawdown <= tight.percentile_95_drawdown <= tight.worst_drawdown


def test_the_bootstrap_reports_percentiles_and_no_score(sample_payload: ResearchPayload) -> None:
    report = bootstrap_statistics(sample_payload, seed=42, iterations=200)

    assert report.iterations == 200
    assert report.lower_bound_5th is not None and report.upper_bound_95th is not None
    assert report.median_50th is not None
    assert report.lower_bound_5th <= report.median_50th <= report.upper_bound_95th
    assert not hasattr(report, "confidence_score")
    assert bootstrap_statistics(_payload((0.01,)), seed=1).median_50th is None


def test_robustness_is_the_parameter_searchs_or_not_measured() -> None:
    sweep = parameter_sweep(
        "sharpe", ["w=5", "w=10", "w=20"], {"w=5": 1.0, "w=10": 1.4, "w=20": 0.9}.__getitem__
    )
    searched = parameter_robustness(replace(_payload((0.01,), parameters={"w": 10.0}), sweep=sweep))
    unsearched = parameter_robustness(_payload((0.01,), parameters={"w": 10.0}))

    assert (searched.trials, searched.sensitivity, searched.neighbour_drop) == (
        3,
        sweep.sensitivity,
        sweep.neighbour_drop,
    )
    assert (unsearched.trials, unsearched.sensitivity, unsearched.neighbour_drop) == (
        None,
        None,
        None,
    )


def test_every_regime_label_is_reported(sample_payload: ResearchPayload) -> None:
    labelled = replace(
        sample_payload,
        market_regimes=tuple(
            "CRISIS" if index % 7 == 0 else label
            for index, label in enumerate(sample_payload.market_regimes)
        ),
    )
    report = analyze_regimes(labelled)

    assert set(report.observations_by_regime) == {"BEAR", "BULL", "CRISIS", "SIDEWAYS"}
    assert sum(report.observations_by_regime.values()) == len(labelled.returns)
    crisis = [
        r
        for r, label in zip(labelled.returns, labelled.market_regimes, strict=True)
        if label == "CRISIS"
    ]
    assert report.sharpe_by_regime["CRISIS"] == pytest.approx(calculate_sharpe(crisis, DAILY, 0.0))


def test_capacity_keeps_the_runs_scale_and_estimates_nothing(
    sample_payload: ResearchPayload,
) -> None:
    report = estimate_capacity(sample_payload)

    assert report.base_aum == 1_000_000.0
    assert report.base_cagr == pytest.approx(calculate_cagr(sample_payload.returns, DAILY))
    assert report.trade_count == 100
    assert report.trades_per_year == pytest.approx(100.0)
    assert not hasattr(report, "cagr_at_100m")


def test_stress_measures_the_declared_shocks(sample_payload: ResearchPayload) -> None:
    report = apply_stress_tests(sample_payload, -0.25, 0.5, 2.0)
    shocked = list(sample_payload.returns)
    shocked[len(shocked) // 2] += -0.25

    assert report.base_drawdown == pytest.approx(calculate_max_drawdown(sample_payload.returns))
    assert report.shock_drawdown == pytest.approx(calculate_max_drawdown(shocked))
    assert report.liquidity_drawdown == pytest.approx(
        calculate_max_drawdown([r * 0.5 if r > 0 else r * 2.0 for r in sample_payload.returns])
    )


def test_findings_name_the_number_and_the_stated_bound() -> None:
    trades = (
        TradePayload("T1", "A", 10, 11, 1, 90.0, 60.0),
        TradePayload("T2", "A", 10, 11, 1, 10.0, 60.0),
    )
    report = generate_diagnostics(_payload((-0.15, 0.01), trades), 50, 0.30, -0.10)

    assert (report.too_few_trades, report.high_concentration, report.large_tail_risk) == (
        True,
        True,
        True,
    )
    assert "2 trades, fewer than the stated minimum of 50" in report.warnings[0]
    assert "0.9000 of the gross profit, above the stated 0.3" in report.warnings[1]
    assert "-0.15, below the stated -0.1" in report.warnings[2]
    assert generate_diagnostics(_payload((-0.15, 0.01), trades), 1, 0.95, -0.5).warnings == ()


# --- POLICY ---


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"walk_forward_windows": 0}, "walk_forward_windows"),
        ({"minimum_trades": True}, "minimum_trades"),
        ({"ruin_drawdown": 0.0}, "ruin_drawdown"),
        ({"maximum_trade_share": 1.5}, "maximum_trade_share"),
        ({"shock_return": -1.0}, "shock_return"),
        ({"loss_multiplier": -1.0}, "multiplier"),
        ({"worst_period_return": math.nan}, "finite"),
    ],
)
def test_a_policy_that_states_an_impossible_bound_is_refused(
    changes: dict[str, object], match: str
) -> None:
    with pytest.raises(ResearchValidationError, match=match):
        replace(POLICY, **changes)  # type: ignore[arg-type]


# --- VALIDATION ---


def test_validation_empty_id() -> None:
    with pytest.raises(ResearchValidationError):
        validate_payload(replace(_payload(), strategy_id=" "))


def test_validation_mismatch() -> None:
    with pytest.raises(ResearchValidationError):
        validate_payload(_payload((0.01,), regimes=("BULL", "BEAR")))


def test_validation_negative_aum() -> None:
    with pytest.raises(ResearchValidationError):
        validate_payload(replace(_payload(), aum=-1_000_000.0))


# --- ENGINE ---


def test_engine_init() -> None:
    state = ResearchEngine.initialize("R-1", "S-1", 1000.0)
    assert state.research_id == "R-1"
    assert not state.completed


def test_engine_full_run_measures_and_grades_nothing(sample_payload: ResearchPayload) -> None:
    state = ResearchEngine.initialize("R-1", "S-1", 1000.0)
    done = ResearchEngine.run_full_research(state, sample_payload, POLICY, 1001.0, seed=42)

    assert done.completed
    assert done.policy == POLICY
    metrics = research_metrics_of(done)
    assert not any("score" in name for name in metrics)
    assert metrics["sharpe"] == pytest.approx(calculate_sharpe(sample_payload.returns, DAILY, 0.0))
    assert metrics["trade_count"] == 100.0
    assert metrics["monte_carlo_ruin_probability"] == done.monte_carlo_report.ruin_probability  # type: ignore[union-attr]
    assert {"sharpe_in_BULL", "sharpe_in_BEAR", "sharpe_in_SIDEWAYS"} <= set(metrics)
    # No parameter search was recorded, so robustness is absent, not zero.
    assert "parameter_sweep_sensitivity" not in metrics


def test_the_same_inputs_measure_the_same(sample_payload: ResearchPayload) -> None:
    def run() -> object:
        state = ResearchEngine.initialize("R-1", "S-1", 1000.0)
        return research_metrics_of(
            ResearchEngine.run_full_research(state, sample_payload, POLICY, 1001.0, seed=42)
        )

    assert run() == run()


def test_the_periods_change_every_annualized_measurement(sample_payload: ResearchPayload) -> None:
    def metrics(periods: int) -> dict[str, float]:
        state = ResearchEngine.initialize("R-1", "S-1", 1000.0)
        payload = replace(sample_payload, periods_per_year=periods)
        return dict(
            research_metrics_of(ResearchEngine.run_full_research(state, payload, POLICY, 1001.0, 1))
        )

    daily, weekly = metrics(252), metrics(52)
    assert daily["sharpe"] / weekly["sharpe"] == pytest.approx(math.sqrt(252 / 52))
    assert daily["max_drawdown"] == weekly["max_drawdown"]


def test_the_policy_reaches_every_report_it_bounds(sample_payload: ResearchPayload) -> None:
    """Each bound the policy states is the one its report used (mutations X39, X40)."""

    policy = replace(POLICY, walk_forward_windows=3, ruin_drawdown=0.35, shock_return=-0.25)
    state = ResearchEngine.initialize("R-1", "S-1", 1000.0)
    done = ResearchEngine.run_full_research(state, sample_payload, policy, 1001.0, seed=42)

    assert done.walk_forward_report is not None
    assert done.walk_forward_report.windows_evaluated == 3
    assert len(done.walk_forward_report.window_sharpes) == 3
    assert done.monte_carlo_report is not None
    assert done.monte_carlo_report.ruin_drawdown == 0.35
    assert done.stress_report is not None
    assert done.stress_report.shock_return == -0.25


def test_engine_double_run(sample_payload: ResearchPayload) -> None:
    state = ResearchEngine.initialize("R-1", "S-1", 1000.0)
    done = ResearchEngine.run_full_research(state, sample_payload, POLICY, 1001.0, seed=42)
    with pytest.raises(InvalidResearchStateError):
        ResearchEngine.run_full_research(done, sample_payload, POLICY, 1002.0, seed=42)


def test_views_access(sample_payload: ResearchPayload) -> None:
    state = ResearchEngine.initialize("R-1", "S-1", 1000.0)
    done = ResearchEngine.run_full_research(state, sample_payload, POLICY, 1001.0, seed=42)

    assert research_metrics_of(done)
    assert warnings(done) == done.diagnostic_report.warnings  # type: ignore[union-attr]


# --- ADAPTER ---


def test_adapter_translation() -> None:
    trades = ({"trade_id": "T1", "symbol": "AAPL", "pnl": 50.0},)
    payload = ResearchAdapter.to_research_payload(
        "S-1", (0.01,), trades, {"ma": 20}, ("BULL",), 1_000_000.0, 12, 0.02
    )
    assert payload.strategy_id == "S-1"
    assert len(payload.trades) == 1
    assert payload.trades[0].pnl == 50.0
    assert (payload.periods_per_year, payload.risk_free_rate) == (12, 0.02)
