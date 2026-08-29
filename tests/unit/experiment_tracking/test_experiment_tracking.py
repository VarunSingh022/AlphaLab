"""Comprehensive tests for Experiment Tracking: the studio bridge, run lifecycle,
versioning/lineage, comparison, and a real deep_learning training integration."""

from dataclasses import FrozenInstanceError

import pytest

from alphalab.deep_learning import ActivationType, Sequential, create_dense_layer, train_network
from alphalab.experiment_tracking import (
    ExperimentTracker,
    ExperimentTrackingInputError,
    RunStatus,
    best_metric_value,
    best_run,
    compare_runs,
    complete_run,
    fail_run,
    latest_metric_value,
    lineage,
    log_metric,
    log_metrics,
    new_version,
    record_experiment,
    start_run,
    version_number,
)
from alphalab.studio import StrategyStudioEngine
from alphalab.studio.exceptions import StudioValidationError
from alphalab.studio.project import Project
from alphalab.studio.state import StrategyStudioState
from alphalab.studio.views import experiment_summary

# --------------------------------------------------------------------------- #
# Studio bridge: closing the confirmed gap
# --------------------------------------------------------------------------- #


def _studio_with_project() -> StrategyStudioState:
    state = StrategyStudioEngine.initialize("STUDIO-TEST")
    project = Project(project_id="PROJ-1", name="Test Project", created_at=1000.0)
    return StrategyStudioEngine.create_project(state, project, 1000.0)


def test_experiment_summary_is_empty_before_any_recording() -> None:
    """Confirms the gap this bridge closes: before record_experiment, the
    pre-existing experiment_summary() reader has nothing to show."""
    state = _studio_with_project()
    assert experiment_summary(state) == ()


def test_record_experiment_makes_experiment_summary_non_empty() -> None:
    state = _studio_with_project()
    new_state, experiment_id = record_experiment(
        state, "PROJ-1", parameters={"l2_penalty": 1.0}, target_metric=0.95, timestamp=1001.0
    )
    summary = experiment_summary(new_state)
    assert len(summary) == 1
    assert summary[0].experiment_id == experiment_id
    assert summary[0].target_metric == 0.95


def test_record_experiment_stores_in_state_experiments() -> None:
    state = _studio_with_project()
    new_state, experiment_id = record_experiment(
        state, "PROJ-1", parameters={"a": 1.0}, target_metric=0.5, timestamp=1001.0
    )
    assert experiment_id in new_state.experiments
    assert new_state.experiments[experiment_id].project_id == "PROJ-1"


def test_record_experiment_rejects_unknown_project() -> None:
    state = _studio_with_project()
    with pytest.raises(StudioValidationError):
        record_experiment(state, "NONEXISTENT", parameters={}, target_metric=0.0, timestamp=1001.0)


def test_record_experiment_emits_an_event() -> None:
    state = _studio_with_project()
    new_state, _ = record_experiment(
        state, "PROJ-1", parameters={"a": 1.0}, target_metric=0.5, timestamp=1001.0
    )
    assert len(new_state.events) == len(state.events) + 1


# --------------------------------------------------------------------------- #
# Run lifecycle
# --------------------------------------------------------------------------- #


def test_start_run_creates_running_run() -> None:
    tracker = ExperimentTracker()
    tracker, run_id = start_run(tracker, "exp1", parameters={"lr": 0.1}, timestamp=1000.0)
    run = tracker.runs[run_id]
    assert run.status is RunStatus.RUNNING
    assert run.name == "exp1"
    assert run.parameters == {"lr": 0.1}


def test_start_run_rejects_empty_name() -> None:
    tracker = ExperimentTracker()
    with pytest.raises(ExperimentTrackingInputError):
        start_run(tracker, "", parameters={}, timestamp=1000.0)


def test_start_run_supports_mixed_type_parameters() -> None:
    """The specific gap vs. studio.ExperimentResult, which is float-only."""
    tracker = ExperimentTracker()
    tracker, run_id = start_run(
        tracker,
        "exp1",
        parameters={"lr": 0.1, "optimizer": "adam", "use_dropout": True, "layers": 3},
        timestamp=1000.0,
    )
    params = tracker.runs[run_id].parameters
    assert params["optimizer"] == "adam"
    assert params["use_dropout"] is True
    assert params["layers"] == 3


def test_log_metric_appends_to_history_not_overwrites() -> None:
    tracker = ExperimentTracker()
    tracker, run_id = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    tracker = log_metric(tracker, run_id, "loss", 0.5)
    tracker = log_metric(tracker, run_id, "loss", 0.3)
    tracker = log_metric(tracker, run_id, "loss", 0.1)
    assert tracker.runs[run_id].metrics["loss"] == (0.5, 0.3, 0.1)


def test_log_metric_rejects_unknown_run() -> None:
    tracker = ExperimentTracker()
    with pytest.raises(ExperimentTrackingInputError):
        log_metric(tracker, "nonexistent", "loss", 0.5)


def test_log_metric_rejects_logging_to_completed_run() -> None:
    tracker = ExperimentTracker()
    tracker, run_id = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    tracker = complete_run(tracker, run_id, timestamp=1001.0)
    with pytest.raises(ExperimentTrackingInputError):
        log_metric(tracker, run_id, "loss", 0.5)


def test_log_metrics_logs_several_at_once() -> None:
    tracker = ExperimentTracker()
    tracker, run_id = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    tracker = log_metrics(tracker, run_id, {"loss": 0.5, "accuracy": 0.8})
    run = tracker.runs[run_id]
    assert run.metrics["loss"] == (0.5,)
    assert run.metrics["accuracy"] == (0.8,)


def test_complete_run_sets_status_and_completed_at() -> None:
    tracker = ExperimentTracker()
    tracker, run_id = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    tracker = complete_run(tracker, run_id, timestamp=1050.0)
    run = tracker.runs[run_id]
    assert run.status is RunStatus.COMPLETED
    assert run.completed_at == 1050.0


def test_fail_run_sets_failed_status() -> None:
    tracker = ExperimentTracker()
    tracker, run_id = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    tracker = fail_run(tracker, run_id, timestamp=1050.0)
    assert tracker.runs[run_id].status is RunStatus.FAILED


def test_cannot_complete_an_already_completed_run() -> None:
    tracker = ExperimentTracker()
    tracker, run_id = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    tracker = complete_run(tracker, run_id, timestamp=1050.0)
    with pytest.raises(ExperimentTrackingInputError):
        complete_run(tracker, run_id, timestamp=1060.0)


def test_experiment_run_is_immutable() -> None:
    tracker = ExperimentTracker()
    tracker, run_id = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    with pytest.raises(FrozenInstanceError):
        tracker.runs[run_id].status = RunStatus.COMPLETED  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Versioning / lineage
# --------------------------------------------------------------------------- #


def test_new_version_shares_parent_name() -> None:
    tracker = ExperimentTracker()
    tracker, v1 = start_run(tracker, "exp1", parameters={"lr": 0.1}, timestamp=1000.0)
    tracker, v2 = new_version(tracker, v1, parameters={"lr": 0.05}, timestamp=1001.0)
    assert tracker.runs[v2].name == tracker.runs[v1].name == "exp1"


def test_new_version_sets_parent_run_id() -> None:
    tracker = ExperimentTracker()
    tracker, v1 = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    tracker, v2 = new_version(tracker, v1, parameters={}, timestamp=1001.0)
    assert tracker.runs[v2].parent_run_id == v1


def test_new_version_rejects_unknown_parent() -> None:
    tracker = ExperimentTracker()
    with pytest.raises(ExperimentTrackingInputError):
        new_version(tracker, "nonexistent", parameters={}, timestamp=1000.0)


def test_lineage_returns_full_chain_oldest_first() -> None:
    tracker = ExperimentTracker()
    tracker, v1 = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    tracker, v2 = new_version(tracker, v1, parameters={}, timestamp=1001.0)
    tracker, v3 = new_version(tracker, v2, parameters={}, timestamp=1002.0)

    chain = lineage(tracker, v3)
    assert [r.run_id for r in chain] == [v1, v2, v3]


def test_lineage_of_a_run_with_no_parent_is_length_one() -> None:
    tracker = ExperimentTracker()
    tracker, v1 = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    assert lineage(tracker, v1) == (tracker.runs[v1],)


def test_lineage_rejects_unknown_run() -> None:
    tracker = ExperimentTracker()
    with pytest.raises(ExperimentTrackingInputError):
        lineage(tracker, "nonexistent")


def test_version_number_increments_along_chain() -> None:
    tracker = ExperimentTracker()
    tracker, v1 = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    tracker, v2 = new_version(tracker, v1, parameters={}, timestamp=1001.0)
    assert version_number(tracker, v1) == 1
    assert version_number(tracker, v2) == 2


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #


def test_latest_metric_value_returns_most_recent() -> None:
    tracker = ExperimentTracker()
    tracker, run_id = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    tracker = log_metrics(tracker, run_id, {"loss": 0.5})
    tracker = log_metric(tracker, run_id, "loss", 0.3)
    assert latest_metric_value(tracker.runs[run_id], "loss") == 0.3


def test_latest_metric_value_none_when_never_logged() -> None:
    tracker = ExperimentTracker()
    tracker, run_id = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    assert latest_metric_value(tracker.runs[run_id], "loss") is None


def test_best_metric_value_min_and_max() -> None:
    tracker = ExperimentTracker()
    tracker, run_id = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    for value in (0.5, 0.9, 0.2, 0.7):
        tracker = log_metric(tracker, run_id, "accuracy", value)
    run = tracker.runs[run_id]
    assert best_metric_value(run, "accuracy", higher_is_better=True) == 0.9
    assert best_metric_value(run, "accuracy", higher_is_better=False) == 0.2


def test_best_run_picks_lower_loss_when_higher_is_better_false() -> None:
    tracker = ExperimentTracker()
    tracker, r1 = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    tracker = log_metric(tracker, r1, "loss", 0.5)
    tracker, r2 = start_run(tracker, "exp2", parameters={}, timestamp=1001.0)
    tracker = log_metric(tracker, r2, "loss", 0.1)

    winner = best_run(tracker, "loss", higher_is_better=False)
    assert winner is not None
    assert winner.run_id == r2


def test_best_run_returns_none_when_metric_never_logged() -> None:
    tracker = ExperimentTracker()
    tracker, _ = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    assert best_run(tracker, "nonexistent_metric") is None


def test_best_run_use_best_value_ignores_final_dip() -> None:
    """A run whose metric peaked mid-run but ended lower should still be found
    as the peak-value winner when use_best_value=True."""
    tracker = ExperimentTracker()
    tracker, r1 = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    for value in (0.5, 0.95, 0.6):  # peaked at 0.95, ended at 0.6
        tracker = log_metric(tracker, r1, "accuracy", value)
    tracker, r2 = start_run(tracker, "exp2", parameters={}, timestamp=1001.0)
    tracker = log_metric(tracker, r2, "accuracy", 0.7)

    by_latest = best_run(tracker, "accuracy", higher_is_better=True, use_best_value=False)
    by_peak = best_run(tracker, "accuracy", higher_is_better=True, use_best_value=True)
    assert by_latest is not None and by_latest.run_id == r2  # 0.7 > 0.6
    assert by_peak is not None and by_peak.run_id == r1  # 0.95 > 0.7


def test_compare_runs_returns_latest_value_per_run() -> None:
    tracker = ExperimentTracker()
    tracker, r1 = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    tracker = log_metric(tracker, r1, "loss", 0.4)
    tracker, r2 = start_run(tracker, "exp2", parameters={}, timestamp=1001.0)
    tracker = log_metric(tracker, r2, "loss", 0.2)

    result = compare_runs(tracker, (r1, r2), "loss")
    assert result == {r1: 0.4, r2: 0.2}


def test_compare_runs_rejects_empty_run_ids() -> None:
    tracker = ExperimentTracker()
    with pytest.raises(ExperimentTrackingInputError):
        compare_runs(tracker, (), "loss")


def test_compare_runs_rejects_unknown_run_id() -> None:
    tracker = ExperimentTracker()
    tracker, r1 = start_run(tracker, "exp1", parameters={}, timestamp=1000.0)
    with pytest.raises(ExperimentTrackingInputError):
        compare_runs(tracker, (r1, "nonexistent"), "loss")


# --------------------------------------------------------------------------- #
# Real integration: a genuine deep_learning training run tracked end-to-end
# --------------------------------------------------------------------------- #


def test_tracks_a_real_deep_learning_training_run() -> None:
    """Not a synthetic example: this is the actual XOR network from the deep_learning
    test suite, trained for real, with its real per-epoch loss logged."""
    network = Sequential(
        layers=(
            create_dense_layer(2, 4, ActivationType.TANH, seed=1),
            create_dense_layer(4, 1, ActivationType.SIGMOID, seed=2),
        )
    )
    x = ((0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0))
    y = ((0.0,), (1.0,), (1.0,), (0.0,))

    tracker = ExperimentTracker()
    tracker, run_id = start_run(
        tracker, "xor_v1", parameters={"learning_rate": 0.5, "hidden_units": 4}, timestamp=1000.0
    )

    _, epoch_losses = train_network(network, x, y, learning_rate=0.5, epochs=100)
    for loss in epoch_losses:
        tracker = log_metric(tracker, run_id, "loss", loss)
    tracker = complete_run(tracker, run_id, timestamp=1001.0)

    run = tracker.runs[run_id]
    assert run.status is RunStatus.COMPLETED
    assert len(run.metrics["loss"]) == 100
    assert run.metrics["loss"][-1] < run.metrics["loss"][0]  # loss genuinely decreased


def test_versioned_training_runs_are_comparable() -> None:
    """A real two-version comparison: a smaller and a larger hidden layer trained
    on the same task, compared by final loss."""
    x = ((0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0))
    y = ((0.0,), (1.0,), (1.0,), (0.0,))

    tracker = ExperimentTracker()
    tracker, v1 = start_run(tracker, "xor_sweep", parameters={"hidden_units": 2}, timestamp=1000.0)
    small_network = Sequential(
        layers=(
            create_dense_layer(2, 2, ActivationType.TANH, seed=1),
            create_dense_layer(2, 1, ActivationType.SIGMOID, seed=2),
        )
    )
    _, small_losses = train_network(small_network, x, y, learning_rate=0.5, epochs=200)
    tracker = log_metrics(tracker, v1, {"final_loss": small_losses[-1]})
    tracker = complete_run(tracker, v1, timestamp=1001.0)

    tracker, v2 = new_version(tracker, v1, parameters={"hidden_units": 8}, timestamp=1002.0)
    big_network = Sequential(
        layers=(
            create_dense_layer(2, 8, ActivationType.TANH, seed=1),
            create_dense_layer(8, 1, ActivationType.SIGMOID, seed=2),
        )
    )
    _, big_losses = train_network(big_network, x, y, learning_rate=0.5, epochs=200)
    tracker = log_metrics(tracker, v2, {"final_loss": big_losses[-1]})
    tracker = complete_run(tracker, v2, timestamp=1003.0)

    assert version_number(tracker, v2) == 2
    comparison = compare_runs(tracker, (v1, v2), "final_loss")
    assert set(comparison.keys()) == {v1, v2}
