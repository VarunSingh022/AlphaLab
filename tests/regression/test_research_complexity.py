"""The v3.2 research paths stay near-linear, measured on every run.

The benchmarks in ``benchmarks/`` print timings; nothing reads them, so a path
that quietly became quadratic would show up in a number nobody was looking at.
This file is the standing version, and it follows the shape
``test_data_ingestion_complexity.py`` established: measure the *growth ratio*
between two sizes rather than an absolute duration, so the assertion is about
the algorithm rather than about the machine it ran on.

Why the bounds are loose
------------------------

A tenfold increase in input on a linear path costs roughly tenfold; on a
quadratic one it costs a hundredfold. The bounds below sit far above the first
and far below the second -- typically 25x for a 10x increase -- because the
point is to catch a change in *complexity class*, not a 30% regression. A tight
bound on a timing measured on shared CI hardware is a flaky test, and a flaky
test is worse than no test.

Each bound records what it is protecting, so relaxing one is a decision rather
than a reflex.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from alphalab.data.feed import Bar as WireBar
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    FeaturePanel,
    compute_feature,
    compute_features,
    forward_returns,
    information_coefficient,
    observations_from_records,
)
from alphalab.research import (
    PurgePolicy,
    SplitInterval,
    WindowMode,
    apply_purge_and_embargo,
    label_ends_from_horizon,
    walk_forward_splits,
)

DAY = 86400.0
START = 1_735_689_600.0

#: A 10x input increase may cost at most this much more time.
#:
#: Linear is 10x and quadratic is 100x, so this sits between them with room for
#: the noise of a shared machine on either side.
LINEAR_BOUND = 25.0

SMA_20 = FeatureDefinition("sma_20", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=20)


def _records(instants: int, symbols: int = 1) -> list[WireBar]:
    built = []
    for index in range(symbols):
        level = 100.0 + index
        for step in range(instants):
            level *= 1.0 + 0.0004 * ((step * (index + 1)) % 11 - 5)
            built.append(
                WireBar(f"S{index}", START + step * DAY, level, level, level, level, 1000.0)
            )
    return built


def _elapsed(work: Callable[[], object]) -> float:
    """Best of three, so one scheduling hiccup does not fail the suite."""

    return min(_once(work) for _ in range(3))


def _once(work: Callable[[], object]) -> float:
    start = time.perf_counter()
    work()
    return time.perf_counter() - start


def _growth(small: Callable[[], object], large: Callable[[], object]) -> float:
    """How much more the 10x input costs, with a floor on the small measurement.

    The floor matters: a small case that runs in a microsecond divides into a
    huge ratio for reasons that have nothing to do with complexity.
    """

    return _elapsed(large) / max(_elapsed(small), 1e-4)


# --------------------------------------------------------------------------- #
# Reading records
# --------------------------------------------------------------------------- #


def test_reading_a_field_is_near_linear_in_records() -> None:
    """One pass and one sort per symbol. A scan per field would be quadratic."""

    small = _records(10_000)
    large = _records(100_000)

    growth = _growth(
        lambda: observations_from_records(small, FeatureField.CLOSE, "UTC", "bench@v1"),
        lambda: observations_from_records(large, FeatureField.CLOSE, "UTC", "bench@v1"),
    )

    assert growth < LINEAR_BOUND, f"reading records grew {growth:.1f}x for 10x the rows"


# --------------------------------------------------------------------------- #
# Computing features
# --------------------------------------------------------------------------- #


def test_a_fixed_window_feature_is_near_linear_in_observations() -> None:
    small = observations_from_records(_records(10_000), FeatureField.CLOSE, "UTC", "bench@v1")
    large = observations_from_records(_records(100_000), FeatureField.CLOSE, "UTC", "bench@v1")

    growth = _growth(lambda: compute_feature(SMA_20, small), lambda: compute_feature(SMA_20, large))

    assert growth < LINEAR_BOUND, f"a fixed window grew {growth:.1f}x for 10x the observations"


def test_a_feature_is_near_linear_in_its_window() -> None:
    """``O(n * w)`` is the intended cost; ``O(n * w^2)`` would not be."""

    frame = observations_from_records(_records(20_000), FeatureField.CLOSE, "UTC", "bench@v1")
    narrow = FeatureDefinition("sma_5", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=5)
    wide = FeatureDefinition("sma_50", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=50)

    growth = _growth(lambda: compute_feature(narrow, frame), lambda: compute_feature(wide, frame))

    assert growth < LINEAR_BOUND, f"a 10x window cost {growth:.1f}x"


def test_computing_ten_features_reads_the_records_once() -> None:
    """The property that makes ``compute_feature`` take a frame, not a dataset.

    Ten features over one frame must cost close to ten computations plus *one*
    read, not ten reads. Asserted as a comparison against the re-reading form,
    so it stays true whatever the absolute speeds are.
    """

    records = _records(50_000)
    definitions = tuple(
        FeatureDefinition(
            f"sma_{window}", FeatureKind.ROLLING_MEAN, FeatureField.CLOSE, window=window
        )
        for window in range(10, 20)
    )

    def shared() -> object:
        frame = observations_from_records(records, FeatureField.CLOSE, "UTC", "bench@v1")
        return compute_features(definitions, frame)

    def repeated() -> object:
        results = []
        for definition in definitions:
            frame = observations_from_records(records, FeatureField.CLOSE, "UTC", "bench@v1")
            results.append(compute_feature(definition, frame))
        return results

    assert _elapsed(shared) < _elapsed(repeated), (
        "reading the records once for ten features is not faster than re-reading them, "
        "which means the frame is not being reused"
    )


def test_building_a_panel_is_near_linear_in_values() -> None:
    """``FeaturePanel.of`` indexes in one pass.

    Looking each cell up with ``value_at`` instead would be quadratic in the
    number of instants, which is the mistake this guards.
    """

    def build(symbols: int, instants: int) -> Callable[[], object]:
        frame = observations_from_records(
            _records(instants, symbols), FeatureField.CLOSE, "UTC", "bench@v1"
        )
        series = compute_feature(SMA_20, frame)
        return lambda: FeaturePanel.of(series)

    growth = _growth(build(10, 2_000), build(10, 20_000))

    assert growth < LINEAR_BOUND, f"panel construction grew {growth:.1f}x for 10x the values"


# --------------------------------------------------------------------------- #
# Splits and purging
# --------------------------------------------------------------------------- #


def test_purging_is_near_linear_in_the_candidates_examined() -> None:
    """A label end is a dictionary lookup, never a scan of the other instants."""

    def build(size: int) -> Callable[[], object]:
        span = [START + index * DAY for index in range(size + 500)]
        policy = PurgePolicy(label_ends_from_horizon(span, 20))
        validation = SplitInterval(span[size], span[size + 499])
        candidates = tuple(span[:size])
        return lambda: apply_purge_and_embargo(candidates, validation, policy)

    growth = _growth(build(10_000), build(100_000))

    assert growth < LINEAR_BOUND, f"purging grew {growth:.1f}x for 10x the candidates"


def test_walk_forward_generation_is_near_linear_in_instants() -> None:
    def build(size: int) -> Callable[[], object]:
        instants = [START + index * DAY for index in range(size)]
        policy = PurgePolicy(label_ends_from_horizon(instants, 20))
        return lambda: walk_forward_splits(
            instants, size // 10, size // 20, size // 20, WindowMode.ROLLING, policy=policy
        )

    growth = _growth(build(5_000), build(50_000))

    assert growth < LINEAR_BOUND, f"split generation grew {growth:.1f}x for 10x the instants"


# --------------------------------------------------------------------------- #
# Diagnostics
# --------------------------------------------------------------------------- #


def test_the_information_coefficient_is_near_linear_in_instants() -> None:
    """Ranking each cross-section adds a log factor in *assets*, not in instants."""

    def build(instants: int) -> Callable[[], object]:
        frame = observations_from_records(
            _records(instants, 10), FeatureField.CLOSE, "UTC", "bench@v1"
        )
        panel = compute_feature(SMA_20, frame)
        realized = forward_returns(frame, 5)
        indexed = FeaturePanel.of(panel)
        return lambda: information_coefficient(indexed, realized, 5)

    growth = _growth(build(1_000), build(10_000))

    assert growth < LINEAR_BOUND, f"the IC grew {growth:.1f}x for 10x the instants"


def test_a_series_lookup_does_not_scan() -> None:
    """``FeatureSeries.value_at`` is a binary search.

    A linear scan would make any caller that looks up per instant quadratic,
    and the panel is not the only such caller.
    """

    def build(instants: int) -> Callable[[], object]:
        frame = observations_from_records(_records(instants), FeatureField.CLOSE, "UTC", "bench@v1")
        series = compute_feature(SMA_20, frame)[0]
        targets = series.timestamps[::7]
        return lambda: [series.value_at(stamp) for stamp in targets]

    # 10x the series length AND 10x the lookups: a scan would be 100x.
    growth = _growth(build(2_000), build(20_000))

    assert growth < LINEAR_BOUND, (
        f"value_at grew {growth:.1f}x for 10x the lookups over a 10x longer series, "
        "which is the signature of a linear scan"
    )
