"""Signal diagnostics, robustness perturbations and overfitting diagnostics.

Three rules run through all of it and each is asserted directly rather than
assumed:

* **An unmeasurable quantity is ``None``, never ``0.0``.** A zero information
  coefficient is a finding; an absent one is not a measurement.
* **A stochastic step is seeded, or refused.** The same seed reproduces the
  same numbers; no seed means no run.
* **Nothing produces a verdict.** The overfitting report states what was
  measured and which of the *caller's own* bounds it crossed.
"""

import pytest

from alphalab.data.feed import Bar as WireBar
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeaturePanel,
    ObservationFrame,
    compute_panel,
    forward_returns,
    observations_from_records,
)
from alphalab.research import (
    OverfittingPolicy,
    Perturbation,
    PerturbationKind,
    PerturbationRun,
    ResearchValidationError,
    block_bootstrap_indices,
    build_overfitting_report,
    conditional_diagnostics,
    delay_signal,
    drop_observations,
    monte_carlo_orders,
    parameter_sweep,
    period_stability,
    perturb_observations,
    perturb_signal,
    sample_by,
    sample_degradation,
    shift_parameter,
    signal_diagnostics,
    signal_horizons,
    symbol_stability,
)
from alphalab.research.perturbation import apply_cost

DAY = 86400.0
START = 1_735_689_600.0


def _frame(paths: dict[str, list[float]], version: str = "ds@v1") -> ObservationFrame:
    records = [
        WireBar(symbol, START + index * DAY, close, close, close, close, 1_000_000.0)
        for symbol, closes in paths.items()
        for index, close in enumerate(closes)
    ]
    return observations_from_records(records, FeatureField.CLOSE, "UTC", version)


def _graded(assets: int = 8, instants: int = 20) -> tuple[FeaturePanel, ObservationFrame]:
    """A universe whose ordering is fixed: asset k always grows fastest for k."""

    paths = {
        f"S{index}": [100.0 * (1.0 + 0.002 * (index + 1)) ** step for step in range(instants)]
        for index in range(assets)
    }
    frame = _frame(paths)
    panel = compute_panel(
        FeatureDefinition("m", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=3), frame
    )
    return panel, frame


# --------------------------------------------------------------------------- #
# Signal diagnostics
# --------------------------------------------------------------------------- #


def test_a_perfectly_graded_signal_reports_a_monotone_quantile_profile() -> None:
    panel, frame = _graded()

    measured = signal_diagnostics(panel, forward_returns(frame, 1), buckets=4, minimum_assets=4)

    assert measured.monotonicity == pytest.approx(1.0)
    assert measured.spread is not None and measured.spread > 0.0
    assert [bucket.bucket for bucket in measured.quantiles] == [0, 1, 2, 3]
    assert measured.rank_ic.mean_rank == pytest.approx(1.0)


def test_every_bucket_reports_the_sample_behind_it() -> None:
    panel, frame = _graded()

    measured = signal_diagnostics(panel, forward_returns(frame, 1), buckets=4, minimum_assets=4)

    assert sum(bucket.observations for bucket in measured.quantiles) == measured.observations
    assert all(bucket.observations > 0 and bucket.instants > 0 for bucket in measured.quantiles)


def test_a_sample_too_small_to_measure_reports_none_rather_than_zero() -> None:
    panel, frame = _graded(assets=3)

    measured = signal_diagnostics(panel, forward_returns(frame, 1), buckets=2, minimum_assets=10)

    assert measured.rank_ic.mean_rank is None
    assert measured.rank_ic.instants_skipped > 0


def test_diagnostics_across_two_datasets_are_refused() -> None:
    panel, _ = _graded()
    other = _frame({f"S{index}": [100.0, 101.0, 102.0, 103.0] for index in range(8)}, "ds@v2")

    with pytest.raises(ResearchValidationError, match="two different datasets"):
        signal_diagnostics(panel, forward_returns(other, 1))


def test_one_bucket_is_refused_because_its_spread_is_zero_by_construction() -> None:
    panel, frame = _graded()
    with pytest.raises(ResearchValidationError, match="One bucket is the universe"):
        signal_diagnostics(panel, forward_returns(frame, 1), buckets=1)


def test_a_horizon_study_measures_each_horizon_separately() -> None:
    panel, frame = _graded(instants=30)

    measured = signal_horizons(panel, frame, [1, 3, 5], buckets=4, minimum_assets=4)

    assert sorted(measured) == [1, 3, 5]
    assert all(result.horizon == horizon for horizon, result in measured.items())
    assert measured[1].instants > measured[5].instants


def test_conditional_diagnostics_slice_by_the_callers_own_labels() -> None:
    panel, frame = _graded(instants=24)
    instants = panel.timestamps
    regimes = {
        stamp: ("early" if index < len(instants) // 2 else "late")
        for index, stamp in enumerate(instants)
    }

    measured = conditional_diagnostics(
        panel, forward_returns(frame, 1), regimes, buckets=4, minimum_assets=4
    )

    assert sorted(measured) == ["early", "late"]
    assert measured["early"].label == "early"
    assert measured["early"].instants + measured["late"].instants <= len(instants)


def test_an_unlabelled_instant_is_excluded_rather_than_pooled() -> None:
    panel, frame = _graded(instants=24)
    instants = panel.timestamps
    regimes = dict.fromkeys(instants[:6], "known")

    measured = conditional_diagnostics(
        panel, forward_returns(frame, 1), regimes, buckets=4, minimum_assets=4
    )

    assert sorted(measured) == ["known"]
    assert measured["known"].instants <= 6


def test_a_regime_that_occurred_too_rarely_is_omitted() -> None:
    panel, frame = _graded(instants=24)
    instants = panel.timestamps
    regimes = {stamp: ("rare" if index == 0 else "common") for index, stamp in enumerate(instants)}

    measured = conditional_diagnostics(
        panel, forward_returns(frame, 1), regimes, buckets=4, minimum_assets=4, minimum_instants=5
    )

    assert sorted(measured) == ["common"]


def test_conditional_diagnostics_refuse_when_no_slice_is_large_enough() -> None:
    panel, frame = _graded()
    regimes = dict.fromkeys(panel.timestamps, "only")

    with pytest.raises(ResearchValidationError, match="No regime held at least"):
        conditional_diagnostics(
            panel, forward_returns(frame, 1), regimes, minimum_assets=4, minimum_instants=10_000
        )


# --------------------------------------------------------------------------- #
# Perturbations
# --------------------------------------------------------------------------- #


def test_a_stochastic_perturbation_without_a_seed_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="cannot be reproduced"):
        Perturbation(PerturbationKind.DATA, 0.01)


def test_a_deterministic_perturbation_with_a_seed_is_refused() -> None:
    """A seed on a run that drew nothing suggests a guarantee it cannot make."""

    with pytest.raises(ResearchValidationError, match="drew no random number"):
        Perturbation(PerturbationKind.EXECUTION_DELAY, 1.0, seed=7)


def test_the_same_seed_reproduces_the_same_perturbed_data() -> None:
    frame = _frame({"A": [100.0, 101.0, 102.0, 103.0]})

    first = perturb_observations(frame, 0.01, seed=42)
    second = perturb_observations(frame, 0.01, seed=42)
    different = perturb_observations(frame, 0.01, seed=43)

    assert first.series["A"].values == second.series["A"].values
    assert first.series["A"].values != different.series["A"].values


def test_perturbing_leaves_the_baseline_untouched() -> None:
    frame = _frame({"A": [100.0, 101.0, 102.0]})
    original = frame.series["A"].values

    perturb_observations(frame, 0.05, seed=1)

    assert frame.series["A"].values == original


def test_a_perturbation_that_changes_nothing_is_refused() -> None:
    frame = _frame({"A": [100.0, 101.0, 102.0]})
    with pytest.raises(ResearchValidationError, match="changes nothing"):
        perturb_observations(frame, 0.0, seed=1)


def test_dropping_observations_deletes_and_never_fills() -> None:
    frame = _frame({"A": [float(value) for value in range(100)]})

    dropped = drop_observations(frame, 0.3, seed=5)

    assert len(dropped.series["A"]) < len(frame.series["A"])
    assert set(dropped.series["A"].timestamps) <= set(frame.series["A"].timestamps)
    for stamp, value in zip(
        dropped.series["A"].timestamps, dropped.series["A"].values, strict=True
    ):
        index = frame.series["A"].timestamps.index(stamp)
        assert frame.series["A"].values[index] == value, "surviving values are unchanged"


def test_dropping_everything_from_a_symbol_is_refused() -> None:
    frame = _frame({"A": [1.0, 2.0]})
    with pytest.raises(ResearchValidationError, match="left none"):
        drop_observations(frame, 0.999999, seed=1)


def test_delaying_a_signal_moves_it_forward_by_exactly_the_periods_asked() -> None:
    panel, _ = _graded(instants=12)
    instants = panel.timestamps

    delayed = delay_signal(panel, 2)

    assert delayed.cross_section(instants[2]) == panel.cross_section(instants[0])
    assert delayed.cross_section(instants[0]) == {}
    assert "delay(periods=2)" in delayed.lineage


def test_a_zero_delay_is_refused_because_it_is_the_baseline() -> None:
    panel, _ = _graded()
    with pytest.raises(ResearchValidationError, match="delay of zero is the baseline"):
        delay_signal(panel, 0)


def test_a_cost_is_charged_against_every_return_and_may_not_be_negative() -> None:
    returns = {START: {"A": 0.01, "B": -0.02}}

    charged = apply_cost(returns, 0.001)

    assert charged[START]["A"] == pytest.approx(0.009)
    assert charged[START]["B"] == pytest.approx(-0.021)
    with pytest.raises(ResearchValidationError, match="must not be negative"):
        apply_cost(returns, -0.001)


def test_shifting_a_parameter_always_changes_it() -> None:
    """Rounding to no change would record a test the result never faced."""

    assert shift_parameter(20, 0.1) == 22
    assert shift_parameter(20, -0.1) == 18
    assert shift_parameter(3, 0.1) == 4, "a small window still moves"
    assert shift_parameter(3, -0.1) == 2


def test_shifting_a_window_below_one_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="not a window"):
        shift_parameter(2, -1.0)


def test_a_block_bootstrap_preserves_contiguous_runs() -> None:
    """IID resampling would destroy the dependence a time-series result rests on."""

    positions = block_bootstrap_indices(20, block_size=5, seed=3)

    assert len(positions) == 20
    for start in range(0, 20, 5):
        block = positions[start : start + 5]
        assert list(block) == list(range(block[0], block[0] + 5))


def test_a_block_bootstrap_is_reproducible_from_its_seed() -> None:
    assert block_bootstrap_indices(50, 10, seed=9) == block_bootstrap_indices(50, 10, seed=9)
    assert block_bootstrap_indices(50, 10, seed=9) != block_bootstrap_indices(50, 10, seed=10)


def test_a_block_longer_than_the_series_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="exceeds the series length"):
        block_bootstrap_indices(5, 10, seed=1)


def test_each_monte_carlo_path_is_independent_given_the_seed() -> None:
    """Shuffling in place would make path 500 depend on the 499 before it."""

    paths = monte_carlo_orders(10, 4, seed=11)

    assert len(paths) == 4
    assert all(sorted(path) == list(range(10)) for path in paths)
    assert monte_carlo_orders(10, 4, seed=11) == paths


def test_sample_by_reorders_and_refuses_an_out_of_range_position() -> None:
    assert sample_by([10.0, 20.0, 30.0], [2, 0, 0]) == (30.0, 10.0, 10.0)
    with pytest.raises(ResearchValidationError, match="outside a series"):
        sample_by([1.0], [5])


def test_a_perturbed_signal_stays_aligned_with_the_baseline() -> None:
    panel, _ = _graded()

    perturbed = perturb_signal(panel, 0.01, seed=4)

    assert perturbed.timestamps == panel.timestamps
    assert perturbed.symbols == panel.symbols
    assert perturbed.cross_section(panel.timestamps[0]) != panel.cross_section(panel.timestamps[0])


def test_a_run_and_its_baseline_must_report_the_same_quantities() -> None:
    perturbation = Perturbation(PerturbationKind.EXECUTION_DELAY, 1.0)

    run = PerturbationRun(perturbation, "baseline", "ds@v1", {"ic": 0.02}, {"ic": 0.05})

    assert run.change("ic") == pytest.approx(-0.03)
    assert run.relative_change("ic") == pytest.approx(-0.6)

    with pytest.raises(ResearchValidationError, match="compared side by side"):
        PerturbationRun(perturbation, "baseline", "ds@v1", {"ic": 0.02}, {"sharpe": 0.05})


def test_a_relative_change_against_a_zero_baseline_is_undefined() -> None:
    run = PerturbationRun(
        Perturbation(PerturbationKind.EXECUTION_DELAY, 1.0),
        "baseline",
        "ds@v1",
        {"ic": 0.02},
        {"ic": 0.0},
    )

    assert run.relative_change("ic") is None


def test_a_delayed_signal_loses_the_information_a_simultaneous_one_had() -> None:
    """The cheapest robustness test there is, exercised end to end."""

    panel, frame = _graded(instants=24)
    realized = forward_returns(frame, 1)

    baseline = signal_diagnostics(panel, realized, buckets=4, minimum_assets=4)
    delayed = signal_diagnostics(delay_signal(panel, 3), realized, buckets=4, minimum_assets=4)

    assert baseline.rank_ic.mean_rank is not None
    assert delayed.rank_ic.mean_rank is not None
    assert delayed.instants < baseline.instants


# --------------------------------------------------------------------------- #
# Overfitting diagnostics
# --------------------------------------------------------------------------- #


def test_a_sweep_counts_every_configuration_it_evaluated() -> None:
    surface = {"w=5": 0.10, "w=10": 0.30, "w=15": 0.12, "w=20": 0.11}

    result = parameter_sweep("ic", list(surface), surface.__getitem__)

    assert result.trials == 4
    assert result.best == "w=10"
    assert result.best_score == 0.30
    assert set(result.scores) == set(surface), "the full surface, not the survivors"


def test_a_cliff_is_visible_as_a_large_neighbour_drop() -> None:
    cliff = {"w=5": 0.10, "w=10": 0.90, "w=15": 0.11}
    plateau = {"w=5": 0.85, "w=10": 0.90, "w=15": 0.88}

    steep = parameter_sweep("ic", list(cliff), cliff.__getitem__).neighbour_drop
    gentle = parameter_sweep("ic", list(plateau), plateau.__getitem__).neighbour_drop

    assert steep is not None and gentle is not None
    assert steep > 0.8
    assert gentle < 0.05


def test_a_flat_surface_has_low_sensitivity_and_a_spiky_one_has_high() -> None:
    flat = {f"w={index}": 0.50 for index in range(5)}
    spiky = {"w=0": 0.01, "w=1": 0.99, "w=2": 0.02, "w=3": 0.98, "w=4": 0.03}

    even = parameter_sweep("ic", list(flat), flat.__getitem__).sensitivity
    jagged = parameter_sweep("ic", list(spiky), spiky.__getitem__).sensitivity

    assert even is not None and jagged is not None
    assert even == pytest.approx(0.0)
    assert jagged > 1.0


def test_a_repeated_configuration_is_refused() -> None:
    with pytest.raises(ResearchValidationError, match="understate the search"):
        parameter_sweep("ic", ["w=5", "w=5"], lambda _: 1.0)


def test_degradation_reads_zero_when_a_result_holds_and_one_when_it_vanishes() -> None:
    assert sample_degradation(0.5, 0.5) == pytest.approx(0.0)
    assert sample_degradation(0.5, 0.0) == pytest.approx(1.0)
    assert sample_degradation(0.5, -0.5) == pytest.approx(2.0)
    assert sample_degradation(0.0, 0.1) is None


def test_stability_reports_the_weakest_slice_and_the_positive_share() -> None:
    report = period_stability("ic", {"2021": 0.05, "2022": -0.01, "2023": 0.04, "2024": 0.06})

    assert report.weakest == "2022"
    assert report.strongest == "2024"
    assert report.positive_share == pytest.approx(0.75)
    assert report.dimension == "period"


def test_symbol_stability_catches_a_result_driven_by_one_name() -> None:
    concentrated = symbol_stability("ic", {"AAA": 0.40, "BBB": 0.00, "CCC": 0.01, "DDD": -0.01})
    spread = symbol_stability("ic", {"AAA": 0.05, "BBB": 0.04, "CCC": 0.05, "DDD": 0.06})

    assert concentrated.coefficient_of_variation is not None
    assert spread.coefficient_of_variation is not None
    assert concentrated.coefficient_of_variation > spread.coefficient_of_variation
    assert concentrated.positive_share == pytest.approx(0.5)


def test_stability_needs_at_least_two_slices() -> None:
    with pytest.raises(ResearchValidationError, match="at least 2 slices"):
        period_stability("ic", {"2021": 0.05})


def test_a_policy_that_bounds_nothing_is_refused() -> None:
    """It would make every report produced under it read as a pass."""

    with pytest.raises(ResearchValidationError, match="checks nothing"):
        OverfittingPolicy()


def test_a_report_names_the_bound_it_crossed_and_the_value_that_crossed_it() -> None:
    policy = OverfittingPolicy(maximum_degradation=0.5)

    report = build_overfitting_report("ic", 0.10, 0.01, policy, effective_parameters=2)

    assert report.degradation == pytest.approx(0.9)
    assert len(report.findings) == 1
    assert "0.5" in report.findings[0] and "degradation" in report.findings[0]


def test_a_report_that_crosses_nothing_carries_no_findings() -> None:
    policy = OverfittingPolicy(maximum_degradation=0.95)

    report = build_overfitting_report("ic", 0.10, 0.01, policy, effective_parameters=2)

    assert report.findings == ()


def test_a_bound_on_a_measurement_that_was_not_taken_is_a_finding() -> None:
    """An absent number is not a passing one -- ``evaluate_policy``'s rule."""

    policy = OverfittingPolicy(maximum_sensitivity=0.3)

    report = build_overfitting_report("ic", 0.1, 0.1, policy, effective_parameters=1)

    assert any("not measured" in finding for finding in report.findings)


def test_the_bonferroni_threshold_divides_by_the_trial_count() -> None:
    surface = {f"w={index}": float(index) for index in range(20)}
    sweep = parameter_sweep("ic", list(surface), surface.__getitem__)
    policy = OverfittingPolicy(maximum_degradation=1.0, alpha=0.05)

    report = build_overfitting_report("ic", 0.1, 0.05, policy, 3, sweep=sweep)

    assert report.trials == 20
    assert report.bonferroni_alpha == pytest.approx(0.05 / 20)


def test_a_report_with_no_sweep_counts_one_trial() -> None:
    policy = OverfittingPolicy(maximum_degradation=1.0)
    report = build_overfitting_report("ic", 0.1, 0.05, policy, 1)

    assert report.trials == 1
    assert report.bonferroni_alpha == pytest.approx(policy.alpha)


def test_a_sweep_measuring_a_different_metric_is_refused() -> None:
    surface = {"w=1": 1.0, "w=2": 2.0}
    sweep = parameter_sweep("sharpe", list(surface), surface.__getitem__)
    policy = OverfittingPolicy(maximum_degradation=1.0)

    with pytest.raises(ResearchValidationError, match="the sweep measured"):
        build_overfitting_report("ic", 0.1, 0.05, policy, 1, sweep=sweep)


def test_the_report_states_measurements_without_attaching_a_verdict() -> None:
    """There is no score, and ``describe`` must not invent one."""

    policy = OverfittingPolicy(maximum_degradation=0.5)
    report = build_overfitting_report("ic", 0.10, 0.01, policy, 2)
    described = report.describe()

    assert "in=" in described and "out=" in described and "findings=" in described
    for verdict in ("overfit", "score", "grade", "pass", "fail"):
        assert verdict not in described.lower()


def test_observations_per_parameter_is_none_for_a_rule_with_nothing_to_fit() -> None:
    policy = OverfittingPolicy(maximum_degradation=1.0)

    assert build_overfitting_report("ic", 0.1, 0.1, policy, 0).observations_per_parameter is None
    assert build_overfitting_report("ic", 0.1, 0.1, policy, 2).observations_per_parameter == 0.5
