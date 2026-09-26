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

import gc
import math
import time
from collections.abc import Callable

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


def _fine_grained(clock: Callable[[], float]) -> bool:
    """Whether ``clock`` advances in steps fine enough to time a few milliseconds.

    The process CPU clock steps in nanoseconds on Linux and microseconds on
    macOS, but on Windows it advances with the scheduler tick -- about 15.6 ms,
    longer than the smallest sample below -- whatever ``time.get_clock_info``
    reports. So the step is observed rather than trusted, for at most half a
    second of wall time: a clock that does not move in that time is not one to
    time anything with.
    """

    steps: list[float] = []
    deadline = time.perf_counter() + 0.5
    last = clock()
    while len(steps) < 3 and time.perf_counter() < deadline:
        now = clock()
        if now != last:
            steps.append(now - last)
            last = now
    return len(steps) == 3 and max(steps) < 1e-4


#: What every timed region reads: the process's CPU time where the platform
#: keeps it finely, and the wall clock where it does not. CPU time leaves out
#: the moments the process spends descheduled -- a neighbour on a shared CI
#: runner, another process on this core -- which are not work the code under
#: test did, and which fall disproportionately on the longer sample.
_CLOCK: Callable[[], float] = (
    time.process_time if _fine_grained(time.process_time) else time.perf_counter
)


def _timings(
    measure: Callable[[int], float], small: int, large: int, rounds: int = 5
) -> tuple[float, float]:
    """The fastest of ``rounds`` samples at each size, the two sizes interleaved.

    Every timing assertion below compares a small run against a large one. Three
    things can make that comparison fail on a linear implementation, and each is
    removed here without touching the bound the assertions state:

    * **The collector's work is not the registry's.** CPython runs a full
      collection whenever the objects promoted since the last one exceed a
      quarter of the long-lived heap. In the full suite that heap holds about
      190,000 tracked objects when this file runs, so an 8,000-entry run crosses
      the line in *every* repeat while a 2,000-entry run mostly does not: each
      large sample paid for traversing the rest of the suite's heap, which is a
      cost of the test session, not of the code under test, and one that grows
      as the suite does. Taking the fastest sample cannot remove a cost present
      in every sample. Collection is disabled while timing, as ``timeit`` does.
    * **Time spent descheduled is not the registry's either** -- ``_CLOCK``.
    * **A slow phase is not a scaling.** Thermal throttling, a move to an
      efficiency core or a noisy neighbour can outlast one sample, so the sizes
      alternate and share whatever phases occur, and the fastest sample of each
      is compared: noise only ever makes a sample slower.

    Measured on an eight-core machine with a heap the size of the suite's, the
    previous method -- the best of three per size, sizes timed one after the
    other on the wall clock with the collector running -- read the linear
    registry at 5.1x on a quiet machine, failed 8 of 60 comparisons with every
    core busy (peaking at 8.9x, the failure a full-suite run met) and 31 of 60
    when oversubscribed. This method read 4.0x quiet and failed none of 640
    comparisons under the same loads, peaking at 6.1x. It weakens nothing: each
    test was run against the defect it guards -- a container copied, a log
    rebuilt, every name validated on every write -- and read 11x to 16x, and
    failed. The earlier best-of-three was itself added after
    ``_log_distinct_metrics`` failed intermittently on one collection; this
    removes the cause.
    """

    fastest_small = fastest_large = math.inf
    collecting = gc.isenabled()
    gc.disable()
    try:
        for _ in range(rounds):
            fastest_small = min(fastest_small, measure(small))
            fastest_large = min(fastest_large, measure(large))
    finally:
        if collecting:
            gc.enable()
    return fastest_small, fastest_large


def _log_metrics(count: int) -> float:
    tracker, run_id = start_run(ExperimentTracker(), "bench", {"lr": 0.1}, 0.0)
    start = _CLOCK()
    for i in range(count):
        tracker = log_metric(tracker, run_id, "loss", float(i))
    return _CLOCK() - start


def _start_runs(count: int) -> float:
    tracker = ExperimentTracker()
    start = _CLOCK()
    for i in range(count):
        tracker, _ = start_run(tracker, "bench", {"lr": 0.1}, float(i))
    return _CLOCK() - start


def _register_versions(count: int) -> float:
    registry = ModelRegistry()
    start = _CLOCK()
    for i in range(count):
        registry, _ = register_model(registry, "alpha", object(), float(i))
    return _CLOCK() - start


def _register_names(count: int) -> float:
    registry = ModelRegistry()
    start = _CLOCK()
    for i in range(count):
        registry, _ = register_model(registry, f"model-{i}", object(), float(i))
    return _CLOCK() - start


def _promote(count: int) -> float:
    registry = ModelRegistry()
    for i in range(count):
        registry, _ = register_model(registry, "alpha", object(), float(i))
    start = _CLOCK()
    for version in range(1, count + 1):
        registry = promote(registry, "alpha", version, ModelStage.PRODUCTION, float(version))
    return _CLOCK() - start


def _deploy(count: int) -> float:
    manager = DeploymentManager()
    for i in range(count):
        manager, _ = register_release(manager, "line", {"c": str(i)}, {}, float(i))
    start = _CLOCK()
    for version in range(1, count + 1):
        manager = deploy(manager, "line", version, "prod", float(version))
    return _CLOCK() - start


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
    small, large = _timings(_log_metrics, 2_000, 8_000)

    assert large < small * 8.0, (
        f"Logging 8,000 values took {large:.3f}s against {small:.3f}s for 2,000; "
        "the metric history is being rebuilt per logged value."
    )


def test_starting_runs_stays_linear_in_the_runs_already_started() -> None:
    small, large = _timings(_start_runs, 2_000, 8_000)

    assert large < small * 8.0, (
        f"Starting 8,000 runs took {large:.3f}s against {small:.3f}s for 2,000; "
        "the tracker is being copied per run."
    )


def test_registering_versions_stays_linear_in_the_versions_already_registered() -> None:
    small, large = _timings(_register_versions, 2_000, 8_000)

    assert large < small * 8.0, (
        f"Registering 8,000 versions took {large:.3f}s against {small:.3f}s for 2,000; "
        "the version list is being rebuilt per registration."
    )


def test_registering_names_stays_linear_in_the_names_already_registered() -> None:
    """The second axis: many models, one version each.

    This is the one an early v2.4 draft regressed, by validating every entry of
    the name mapping on every write.
    """
    small, large = _timings(_register_names, 2_000, 8_000)

    assert large < small * 8.0, (
        f"Registering 8,000 model names took {large:.3f}s against {small:.3f}s for "
        "2,000; the name mapping is being copied or rescanned per registration."
    )


def test_promotion_stays_linear_in_the_versions_and_transitions_before_it() -> None:
    small, large = _timings(_promote, 1_000, 4_000)

    assert large < small * 8.0, (
        f"Promoting 4,000 versions took {large:.3f}s against {small:.3f}s for 1,000; "
        "the promotion path is scanning the versions or rebuilding the audit log."
    )


def test_deployment_stays_linear_in_the_ledger_before_it() -> None:
    small, large = _timings(_deploy, 1_000, 4_000)

    assert large < small * 8.0, (
        f"Deploying 4,000 releases took {large:.3f}s against {small:.3f}s for 1,000; "
        "the deployment path is scanning the ledger."
    )


def _log_distinct_metrics(count: int) -> float:
    """The second growth axis of a run: distinct metric names, not values."""

    tracker, run_id = start_run(ExperimentTracker(), "bench", {"lr": 0.1}, 0.0)
    start = _CLOCK()
    for i in range(count):
        tracker = log_metric(tracker, run_id, f"metric-{i}", float(i))
    return _CLOCK() - start


def test_logging_distinct_metrics_stays_linear_in_the_names_already_logged() -> None:
    """A run that logs a value per feature, per asset or per layer has thousands
    of metric names, and rebuilding the name mapping per write is quadratic in
    exactly that. This is why ``metrics`` is a persistent map and not a dict --
    a draft that made it a dict, to buy back a constant factor on the read side,
    scaled at 3.8x per doubling here.
    """
    small, large = _timings(_log_distinct_metrics, 1_000, 4_000)

    assert large < small * 8.0, (
        f"Logging 4,000 distinct metric names took {large:.3f}s against {small:.3f}s "
        "for 1,000; the metric name mapping is being rebuilt per write."
    )
