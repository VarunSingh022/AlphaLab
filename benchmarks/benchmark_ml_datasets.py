"""What building a point-in-time training set costs at the plan's scale (v4.3, plan WS-08 item 15).

The plan budgets, in pure Python, building a dataset for 500 assets over 2,520
days -- ten years of daily data -- within 60 seconds. The dataset here is the
whole path a supervised study takes:

* two feature panels, five-day momentum and a ten-day z-score, computed over
  the closes;
* triple-barrier labels on a declared 20-return rolling volatility, ten
  sessions to the vertical barrier;
* the point-in-time panel joining them, event by event;
* the training set of every row known by the last day, weighed by its
  uniqueness among those rows.

The closes are integer walks to the cent, built once outside the timing. The
ceiling is judged through ``_stable_timing``: CPU time, collector paused,
fastest of several.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _stable_timing import SAMPLES, fastest

from alphalab.factor_library import FeatureDefinition, FeatureField, FeatureKind
from alphalab.factor_library.compute import compute_panel
from alphalab.factor_library.observations import ObservationFrame, ObservationSeries
from alphalab.ml import (
    RollingVolatility,
    SampleWeights,
    TrainingSet,
    TripleBarrier,
    VerticalOutcome,
    point_in_time_panel,
    triple_barrier_labels,
)

#: The plan's budget for the whole build, in CPU seconds.
BUDGET_SECONDS = 60.0
ASSETS = 500
DAYS = 2_520
DAY = 86_400.0
FIRST = 1_704_153_600.0

FEATURES = (
    FeatureDefinition("mom_5", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=5),
    FeatureDefinition("z_10", FeatureKind.ROLLING_ZSCORE, FeatureField.CLOSE, window=10),
)
BARRIER = TripleBarrier(
    horizon=10,
    upper=1.0,
    lower=1.0,
    volatility=RollingVolatility(window=20),
    at_vertical=VerticalOutcome.SIGN,
)


def _frame() -> ObservationFrame:
    """500 walks of 2,520 closes, each a step of a few cents from the last."""

    stamps = tuple(FIRST + day * DAY for day in range(DAYS))
    state = 2026
    series = {}
    for asset in range(ASSETS):
        cents, values = 10_000 + 37 * asset, []
        for _ in range(DAYS):
            state = (1103515245 * state + 12345) % 2**31
            cents = max(100, cents + (state >> 8) % 61 - 30)
            values.append(cents / 100)
        name = f"A{asset:03d}"
        series[name] = ObservationSeries(name, stamps, tuple(values))
    return ObservationFrame(FeatureField.CLOSE, series, "UTC", "benchmark@ml")


def _build(frame: ObservationFrame) -> TrainingSet:
    features = {definition.feature_id: compute_panel(definition, frame) for definition in FEATURES}
    panel = point_in_time_panel(features, triple_barrier_labels(frame, BARRIER))
    return panel.known_by(FIRST + (DAYS - 1) * DAY).training_set(weights=SampleWeights.UNIQUENESS)


def run_benchmark() -> int:
    print(f"Starting ML Datasets Benchmark ({ASSETS} assets over {DAYS} days)...")
    frame = _frame()
    training = _build(frame)
    (seconds,) = fastest([lambda: _build(frame)])
    verdict = "ok" if seconds <= BUDGET_SECONDS else "OVER THE PLAN'S BUDGET"
    print(
        f"  a point-in-time training set of {len(training)} rows from {ASSETS} x {DAYS}: "
        f"{seconds:.1f} CPU s (budget {BUDGET_SECONDS:.0f} s) {verdict}"
    )
    print(f"  judged: CPU time, collector paused, fastest of {SAMPLES}")
    return 0 if verdict == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(run_benchmark())
