"""Regression guard for the quadratic patterns v2.4 removed.

The lifecycle packages had the same defect v2.1, v2.2 and v2.3 removed from the
risk engine, the OMS, the market layer and the broker layer: an immutable
collection rebuilt from scratch on every write.

* ``ExperimentTracker.runs`` was copied with ``dict(old)`` per write and each
  metric history was rebuilt with ``(*old, value)`` per logged value, so a
  training run of N epochs cost O(N^2). Logging 8000 values took 13x as long as
  logging 2000.
* ``ModelRegistry.versions`` copied the name mapping and rebuilt the version
  tuple per registration, and ``promotions`` rebuilt the audit log per
  transition. ``production_version`` -- called from inside ``promote`` -- then
  scanned every version of the model, so a promote cost O(versions).
* ``DeploymentManager.releases`` and ``deployments`` had the same shape, and
  ``active_release`` scanned the entire ledger backwards from inside ``deploy``.

There are two independent axes here and both must stay linear: the number of
versions of *one* name, and the number of *names*. An early v2.4 draft fixed
the first and made the second dramatically worse, by inspecting every entry of
the container inside ``__post_init__`` -- which runs on every ``replace``, and
so on every write. Both axes are covered below.

The structural assertions are deterministic and are the real guard: they check
that each state holds a persistent container, which is the property the fix
rests on. The timing assertions are coarse backstops with wide tolerances --
there to catch a return to quadratic scaling, not to police constant factors.
"""

import time

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
from alphalab.deployment_manager import (
    DeploymentManager,
    deploy,
    register_release,
)
from alphalab.experiment_tracking import ExperimentTracker, log_metric, start_run
from alphalab.model_registry import (
    ModelRegistry,
    ModelStage,
    promote,
    register_model,
)


def _log_metrics(count: int) -> float:
    tracker, run_id = start_run(ExperimentTracker(), "bench", {"lr": 0.1}, 0.0)
    start = time.perf_counter()
    for i in range(count):
        tracker = log_metric(tracker, run_id, "loss", float(i))
    return time.perf_counter() - start


def _start_runs(count: int) -> float:
    tracker = ExperimentTracker()
    start = time.perf_counter()
    for i in range(count):
        tracker, _ = start_run(tracker, "bench", {"lr": 0.1}, float(i))
    return time.perf_counter() - start


def _register_versions(count: int) -> float:
    registry = ModelRegistry()
    start = time.perf_counter()
    for i in range(count):
        registry, _ = register_model(registry, "alpha", object(), float(i))
    return time.perf_counter() - start


def _register_names(count: int) -> float:
    registry = ModelRegistry()
    start = time.perf_counter()
    for i in range(count):
        registry, _ = register_model(registry, f"model-{i}", object(), float(i))
    return time.perf_counter() - start


def _promote(count: int) -> float:
    registry = ModelRegistry()
    for i in range(count):
        registry, _ = register_model(registry, "alpha", object(), float(i))
    start = time.perf_counter()
    for version in range(1, count + 1):
        registry = promote(registry, "alpha", version, ModelStage.PRODUCTION, float(version))
    return time.perf_counter() - start


def _deploy(count: int) -> float:
    manager = DeploymentManager()
    for i in range(count):
        manager, _ = register_release(manager, "line", {"c": str(i)}, {}, float(i))
    start = time.perf_counter()
    for version in range(1, count + 1):
        manager = deploy(manager, "line", version, "prod", float(version))
    return time.perf_counter() - start


# --- structural: the property the fix rests on -------------------------------


def test_experiment_tracker_runs_and_histories_are_persistent() -> None:
    tracker, run_id = start_run(ExperimentTracker(), "e", {"lr": 0.1}, 0.0)
    tracker = log_metric(tracker, run_id, "loss", 1.0)

    assert isinstance(tracker.runs, PersistentMap)
    assert isinstance(tracker.runs[run_id].metrics, PersistentMap)
    assert isinstance(tracker.runs[run_id].metrics["loss"], AppendOnlyLog)


def test_model_registry_versions_promotions_and_indexes_are_persistent() -> None:
    registry, _ = register_model(ModelRegistry(), "alpha", object(), 1.0)
    registry = promote(registry, "alpha", 1, ModelStage.PRODUCTION, 2.0)

    assert isinstance(registry.versions, PersistentMap)
    assert isinstance(registry.versions["alpha"], PersistentMap)
    assert isinstance(registry.promotions, AppendOnlyLog)
    assert isinstance(registry.production, PersistentMap)
    assert isinstance(registry.production_line, PersistentMap)
    assert isinstance(registry.production_line["alpha"], AppendOnlyLog)


def test_deployment_manager_releases_ledger_and_index_are_persistent() -> None:
    manager, _ = register_release(DeploymentManager(), "line", {"c": "1"}, {}, 1.0)
    manager = deploy(manager, "line", 1, "prod", 2.0)

    assert isinstance(manager.releases, PersistentMap)
    assert isinstance(manager.releases["line"], AppendOnlyLog)
    assert isinstance(manager.deployments, AppendOnlyLog)
    assert isinstance(manager.environments, PersistentMap)
    assert isinstance(manager.environments["prod"], AppendOnlyLog)


def test_a_write_is_invisible_to_the_value_before_it() -> None:
    """Sharing structure must not leak a later write into an earlier value."""
    first, run_id = start_run(ExperimentTracker(), "e", {}, 0.0)
    second, _ = start_run(first, "e", {}, 1.0)
    assert len(first.runs) == 1
    assert len(second.runs) == 2

    one, _ = register_model(ModelRegistry(), "alpha", object(), 1.0)
    two, _ = register_model(one, "alpha", object(), 2.0)
    assert len(one.versions["alpha"]) == 1
    assert len(two.versions["alpha"]) == 2

    logged = log_metric(first, run_id, "loss", 1.0)
    assert "loss" not in first.runs[run_id].metrics
    assert len(logged.runs[run_id].metrics["loss"]) == 1


# --- timing backstops --------------------------------------------------------
#
# Quadratic over a 4x workload is ~16x; linear is ~4x. The 8x bound catches the
# former without policing constant factors.


def test_logging_metrics_stays_linear_in_the_history_already_logged() -> None:
    small = _log_metrics(2_000)
    large = _log_metrics(8_000)

    assert large < small * 8.0, (
        f"Logging 8,000 values took {large:.3f}s against {small:.3f}s for 2,000; "
        "the metric history is being rebuilt per logged value."
    )


def test_starting_runs_stays_linear_in_the_runs_already_started() -> None:
    small = _start_runs(2_000)
    large = _start_runs(8_000)

    assert large < small * 8.0, (
        f"Starting 8,000 runs took {large:.3f}s against {small:.3f}s for 2,000; "
        "the tracker is being copied per run."
    )


def test_registering_versions_stays_linear_in_the_versions_already_registered() -> None:
    small = _register_versions(2_000)
    large = _register_versions(8_000)

    assert large < small * 8.0, (
        f"Registering 8,000 versions took {large:.3f}s against {small:.3f}s for 2,000; "
        "the version list is being rebuilt per registration."
    )


def test_registering_names_stays_linear_in_the_names_already_registered() -> None:
    """The second axis: many models, one version each.

    This is the one an early v2.4 draft regressed, by validating every entry of
    the name mapping on every write.
    """
    small = _register_names(2_000)
    large = _register_names(8_000)

    assert large < small * 8.0, (
        f"Registering 8,000 model names took {large:.3f}s against {small:.3f}s for "
        "2,000; the name mapping is being copied or rescanned per registration."
    )


def test_promotion_stays_linear_in_the_versions_and_transitions_before_it() -> None:
    small = _promote(1_000)
    large = _promote(4_000)

    assert large < small * 8.0, (
        f"Promoting 4,000 versions took {large:.3f}s against {small:.3f}s for 1,000; "
        "the promotion path is scanning the versions or rebuilding the audit log."
    )


def test_deployment_stays_linear_in_the_ledger_before_it() -> None:
    small = _deploy(1_000)
    large = _deploy(4_000)

    assert large < small * 8.0, (
        f"Deploying 4,000 releases took {large:.3f}s against {small:.3f}s for 1,000; "
        "the deployment path is scanning the ledger."
    )
