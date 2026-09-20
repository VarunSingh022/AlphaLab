"""Splits, diagnostics and studies at scale, and what each path costs.

The companion to ``benchmark_feature_engineering.py``, measuring the research
layer rather than the computation layer.

What is being measured
----------------------

* **Split generation** is linear in instants for the forward schemes and
  ``O(folds * instants)`` for the blocked ones, which build each fold's
  training set from everything outside its block.
* **Purging** is linear in the candidates examined: a dictionary lookup of the
  label end per instant, never a scan.
* **The information coefficient** is ``O(instants * assets * log assets)`` --
  the log comes from ranking each cross-section, which is what makes it
  Spearman's coefficient rather than an approximation.
* **A whole study** is dominated by the features it computes, which is why
  ``run_study`` reads each field exactly once however many features read it.

Carrying the instants costs memory, on purpose
-----------------------------------------------

A ``TimeSplit`` holds the timestamps of each of its parts rather than the
interval bounds they came from. Section 1 reports what that costs at each size.
It buys two things the roadmap makes release-blocking: every fold is
independently inspectable, and purging can be a set operation rather than
arithmetic on dates.
"""

import time
from collections.abc import Callable
from functools import partial

from alphalab.data.feed import Bar as WireBar
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    compute_panel,
    forward_returns,
    information_coefficient,
    observations_from_records,
)
from alphalab.research import (
    CVMethod,
    PurgePolicy,
    SplitInterval,
    WindowMode,
    apply_purge_and_embargo,
    cross_validation_splits,
    label_ends_from_horizon,
    signal_diagnostics,
    walk_forward_splits,
)

DAY = 86400.0
START = 1_735_689_600.0


def _timed[T](work: Callable[[], T]) -> tuple[float, T]:
    start = time.perf_counter()
    result = work()
    return time.perf_counter() - start, result


def _records(instants: int, symbols: int) -> list[WireBar]:
    built = []
    for index in range(symbols):
        level = 100.0 + index
        for step in range(instants):
            level *= 1.0 + 0.0004 * ((step * (index + 1)) % 11 - 5)
            built.append(
                WireBar(f"S{index}", START + step * DAY, level, level, level, level, 1000.0)
            )
    return built


def run_benchmark() -> None:
    print("=" * 78)
    print("AlphaLab Research Validation Benchmark")
    print("=" * 78)

    # ----------------------------------------------------------------- #
    # 1. Split generation
    # ----------------------------------------------------------------- #

    print()
    print("1. Walk-forward split generation (expected: linear in instants)")
    print(f"   {'instants':>10} {'folds':>7} {'seconds':>10} {'growth':>8} {'stored':>10}")
    previous = None
    for size in (1_000, 10_000, 100_000):
        instants = [START + index * DAY for index in range(size)]
        policy = PurgePolicy(label_ends_from_horizon(instants, 20))
        duration, report = _timed(
            partial(
                walk_forward_splits,
                instants,
                size // 10,
                size // 20,
                size // 20,
                WindowMode.ROLLING,
                policy=policy,
            )
        )
        stored = sum(
            len(fold.train) + len(fold.validation) + len(fold.test) for fold in report.folds
        )
        growth = "-" if previous is None else f"{duration / previous:.2f}x"
        print(f"   {size:>10} {len(report):>7} {duration:>10.4f} {growth:>8} {stored:>10}")
        previous = duration
    print("   'stored' is the instants a report holds across every fold. That")
    print("   is the memory cost of making each fold independently inspectable.")

    # ----------------------------------------------------------------- #
    # 2. Every scheme at one size
    # ----------------------------------------------------------------- #

    instants = [START + index * DAY for index in range(20_000)]
    purge = PurgePolicy(label_ends_from_horizon(instants, 20))
    embargo = PurgePolicy(label_ends_from_horizon(instants, 20), embargo_seconds=20 * DAY)

    print()
    print("2. Every scheme over 20,000 instants, 5 folds")
    print(f"   {'method':<12} {'seconds':>10} {'purged':>9} {'embargoed':>11}")
    for method, policy in (
        (CVMethod.ROLLING, purge),
        (CVMethod.EXPANDING, purge),
        (CVMethod.PURGED, purge),
        (CVMethod.EMBARGOED, embargo),
    ):
        duration, report = _timed(partial(cross_validation_splits, instants, 5, method, policy))
        print(
            f"   {method.name:<12} {duration:>10.4f} "
            f"{report.total_purged:>9} {report.total_embargoed:>11}"
        )
    print("   The blocked schemes build each fold's training set from everything")
    print("   outside its block, so they examine more candidates per fold.")

    # ----------------------------------------------------------------- #
    # 3. Purging in isolation
    # ----------------------------------------------------------------- #

    print()
    print("3. Purging in isolation (expected: linear in candidates)")
    print(f"   {'candidates':>12} {'seconds':>10} {'growth':>8} {'purged':>8}")
    previous = None
    for size in (10_000, 100_000, 1_000_000):
        span = [START + index * DAY for index in range(size + 1_000)]
        policy = PurgePolicy(label_ends_from_horizon(span, 20))
        validation = SplitInterval(span[size], span[size + 999])
        duration, removed = _timed(
            partial(apply_purge_and_embargo, tuple(span[:size]), validation, policy)
        )
        growth = "-" if previous is None else f"{duration / previous:.2f}x"
        print(f"   {size:>12} {duration:>10.4f} {growth:>8} {len(removed.purged):>8}")
        previous = duration
    print("   A label end is a dictionary lookup per candidate, never a scan.")

    # ----------------------------------------------------------------- #
    # 4. The information coefficient
    # ----------------------------------------------------------------- #

    definition = FeatureDefinition("mom_20", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=20)

    print()
    print("4. Information coefficient (expected: instants * assets * log assets)")
    print(f"   {'instants':>10} {'assets':>8} {'pairs':>10} {'seconds':>10} {'pairs/sec':>14}")
    for size, symbols in ((1_000, 10), (10_000, 10), (1_000, 100), (10_000, 100)):
        frame = observations_from_records(
            _records(size, symbols), FeatureField.CLOSE, "UTC", "bench@v1"
        )
        panel = compute_panel(definition, frame)
        realized = forward_returns(frame, 5)
        duration, coefficient = _timed(partial(information_coefficient, panel, realized, 5))
        pairs = coefficient.observations
        print(
            f"   {size:>10} {symbols:>8} {pairs:>10} {duration:>10.4f} "
            f"{pairs / duration if duration else 0:>14,.0f}"
        )

    # ----------------------------------------------------------------- #
    # 5. A full diagnostic
    # ----------------------------------------------------------------- #

    print()
    print("5. The full signal diagnostic, 10,000 instants x 100 assets")
    frame = observations_from_records(_records(10_000, 100), FeatureField.CLOSE, "UTC", "bench@v1")
    panel = compute_panel(definition, frame)
    realized = forward_returns(frame, 5)
    duration, measured = _timed(partial(signal_diagnostics, panel, realized, 5, 5))
    print(f"   seconds         : {duration:.4f}")
    print(f"   observations    : {measured.observations:,}")
    print(f"   instants        : {measured.instants:,}")
    print(f"   throughput      : {measured.observations / duration:,.0f} asset-instant pairs/sec")
    print("   The diagnostic ranks and buckets each cross-section twice -- once")
    print("   for the IC and once for the quantile profile -- which is where")
    print("   the log factor comes from.")

    print()
    print("=" * 78)
    print("Measured, not asserted. tests/regression/test_research_complexity.py")
    print("holds the growth ratios to a bound on every run.")
    print("=" * 78)


if __name__ == "__main__":
    run_benchmark()
