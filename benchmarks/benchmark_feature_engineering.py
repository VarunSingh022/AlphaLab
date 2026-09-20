"""Feature computation at four sizes, and the complexity each path claims.

Phase 14 of the v3.2 roadmap says to measure before optimizing, and to add
"complexity protection for intended linear / near-linear paths". This file does
the measuring; ``tests/regression/test_research_complexity.py`` does the
protecting, by asserting the growth ratio rather than a wall-clock time.

What is being measured, and what to expect
-------------------------------------------

* **Reading a field into an ObservationFrame** is linear in the number of
  records: one pass, one sort per symbol.
* **A fixed-window feature** is linear in observations and linear in the window
  -- ``O(n * w)`` -- because each observation re-reads its own trailing slice.
  That is a deliberate trade: an incremental formulation would be ``O(n)`` and
  would accumulate floating-point drift across a million updates, so the same
  window computed at the start and the end of a long series would not agree.
  ``EXPONENTIAL_MEAN`` is the one kind that *is* incremental, because an EWMA
  has no windowed form.
* **Ten features over one frame** cost one read of the records, not ten. That
  is the whole reason ``compute_feature`` takes a frame rather than a dataset,
  and the ratio printed below is what makes it visible.
* **Building a panel** is linear in the number of *values*, not quadratic in
  the number of instants -- ``FeaturePanel.of`` indexes in one pass instead of
  looking each cell up.
"""

import time
from collections.abc import Callable
from functools import partial

from alphalab.data.feed import Bar as WireBar
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeaturePanel,
    compute_feature,
    compute_features,
    observations_from_records,
)

DAY = 86400.0
START = 1_735_689_600.0
SIZES = (1_000, 10_000, 100_000, 1_000_000)


def _records(count: int, symbols: int = 1) -> list[WireBar]:
    """Deterministic, strictly positive bars. No PRNG: the shape is what matters."""

    per_symbol = count // symbols
    built = []
    for index in range(symbols):
        level = 100.0 + index
        for step in range(per_symbol):
            level *= 1.0 + 0.0004 * ((step % 7) - 3)
            built.append(
                WireBar(f"S{index}", START + step * DAY, level, level, level, level, 1000.0)
            )
    return built


def _timed[T](work: Callable[[], T]) -> tuple[float, T]:
    """Run ``work`` once and report how long it took.

    Callers bind their arguments with :func:`functools.partial` rather than
    closing over loop variables in a lambda -- a late-binding closure inside a
    benchmark loop is the classic way to time the wrong thing.
    """

    start = time.perf_counter()
    result = work()
    return time.perf_counter() - start, result


def run_benchmark() -> None:
    print("=" * 78)
    print("AlphaLab Feature Engineering Benchmark")
    print("=" * 78)

    # ----------------------------------------------------------------- #
    # 1. Reading a field: expected linear
    # ----------------------------------------------------------------- #

    print()
    print("1. Reading one field into an ObservationFrame (expected: linear)")
    print(f"   {'rows':>10} {'seconds':>10} {'rows/sec':>14} {'growth':>8}")
    previous = None
    for size in SIZES:
        records = _records(size)
        duration, _ = _timed(
            partial(observations_from_records, records, FeatureField.CLOSE, "UTC", "bench@v1")
        )
        growth = "-" if previous is None else f"{duration / previous:.2f}x"
        print(f"   {size:>10} {duration:>10.4f} {size / duration:>14,.0f} {growth:>8}")
        previous = duration
    print("   A tenfold increase in rows should cost roughly tenfold.")

    # ----------------------------------------------------------------- #
    # 2. One feature, fixed window: expected linear in observations
    # ----------------------------------------------------------------- #

    definition = FeatureDefinition(
        "sma_20", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=20
    )

    print()
    print("2. One 20-period rolling mean (expected: linear in observations)")
    print(f"   {'rows':>10} {'seconds':>10} {'values/sec':>14} {'growth':>8}")
    previous = None
    for size in SIZES:
        frame = observations_from_records(_records(size), FeatureField.CLOSE, "UTC", "bench@v1")
        duration, computed = _timed(partial(compute_feature, definition, frame))
        values = len(computed[0])
        growth = "-" if previous is None else f"{duration / previous:.2f}x"
        print(f"   {size:>10} {duration:>10.4f} {values / duration:>14,.0f} {growth:>8}")
        previous = duration

    # ----------------------------------------------------------------- #
    # 3. Window scaling: expected linear in the window
    # ----------------------------------------------------------------- #

    frame = observations_from_records(_records(100_000), FeatureField.CLOSE, "UTC", "bench@v1")

    print()
    print("3. Window scaling on 100,000 observations (expected: linear in window)")
    print(f"   {'window':>10} {'seconds':>10} {'growth':>8}")
    previous = None
    for window in (5, 20, 80, 320):
        sized = FeatureDefinition(
            f"sma_{window}", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=window
        )
        duration, _ = _timed(partial(compute_feature, sized, frame))
        growth = "-" if previous is None else f"{duration / previous:.2f}x"
        print(f"   {window:>10} {duration:>10.4f} {growth:>8}")
        previous = duration
    print("   O(n * w) is deliberate: an incremental rolling sum would be O(n)")
    print("   and would accumulate drift, so the same window computed early and")
    print("   late in a long series would not agree.")

    # ----------------------------------------------------------------- #
    # 4. Many features over one frame: the reason compute takes a frame
    # ----------------------------------------------------------------- #

    definitions = tuple(
        FeatureDefinition(
            f"sma_{window}", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=window
        )
        for window in range(10, 20)
    )

    records = _records(100_000)
    read_time, shared = _timed(
        partial(observations_from_records, records, FeatureField.CLOSE, "UTC", "bench@v1")
    )
    batch_time, _ = _timed(partial(compute_features, definitions, shared))

    naive_time = 0.0
    for one in definitions:
        per_read, per_frame = _timed(
            partial(observations_from_records, records, FeatureField.CLOSE, "UTC", "bench@v1")
        )
        per_compute, _ = _timed(partial(compute_feature, one, per_frame))
        naive_time += per_read + per_compute

    print()
    print("4. Ten features over 100,000 observations")
    print(f"   read once, compute ten : {read_time + batch_time:.4f}s")
    print(f"   re-read for each one   : {naive_time:.4f}s")
    print(
        f"   saved                  : {naive_time - (read_time + batch_time):.4f}s "
        f"({1 - (read_time + batch_time) / naive_time:.0%})"
    )
    print("   compute_feature takes a frame rather than a dataset for exactly")
    print("   this reason: the records are read once however many features")
    print("   are computed over them.")

    # ----------------------------------------------------------------- #
    # 5. Panel construction: linear in values, not quadratic in instants
    # ----------------------------------------------------------------- #

    print()
    print("5. Panel construction (expected: linear in values)")
    print(f"   {'symbols':>9} {'instants':>10} {'values':>10} {'seconds':>10} {'growth':>8}")
    previous = None
    for symbols, instants in ((10, 1_000), (10, 10_000), (100, 10_000), (100, 100_000)):
        panel_frame = observations_from_records(
            _records(symbols * instants, symbols), FeatureField.CLOSE, "UTC", "bench@v1"
        )
        series = compute_feature(definition, panel_frame)
        duration, _ = _timed(partial(FeaturePanel.of, series))
        values = sum(len(row) for row in series)
        growth = "-" if previous is None else f"{duration / previous:.2f}x"
        print(f"   {symbols:>9} {instants:>10} {values:>10} {duration:>10.4f} {growth:>8}")
        previous = duration
    print("   FeaturePanel.of indexes in one pass. Looking each cell up instead")
    print("   would be quadratic in the number of instants.")

    print()
    print("=" * 78)
    print("Measured, not asserted. The growth ratios are what")
    print("tests/regression/test_research_complexity.py holds to a bound.")
    print("=" * 78)


if __name__ == "__main__":
    run_benchmark()
