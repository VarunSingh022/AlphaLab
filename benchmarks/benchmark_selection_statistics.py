"""PBO by CSCV at the plan's size, and the bootstrap tests beside it (v4.3, plan WS-06 item 15).

The plan holds the probability of backtest overfitting with ``S = 16`` blocks,
over 100 trials of 2,520 periods -- ten years of daily returns -- to at most 60
seconds of pure Python. CSCV counts every one of the ``C(16, 8) = 12,870``
splits. Each split ranks the 100 trials in sample and out of sample, from
per-block sums that are formed once, so a split costs its trials' block
combinations, not their periods.

* **Judged:** PBO by each selection measure, against ``BUDGET_SECONDS``. The
  mean compares exactly rounded sums. The Sharpe ratio also needs each half's
  sum of squares, so it costs more. On the reference machine (CPython 3.12,
  Apple arm64) they take about 1.7 and 5.7 CPU seconds.
* **Printed, not judged:** White's Reality Check and Hansen's SPA over the same
  matrix, with 500 bootstrap samples each. Their cost is the samples times the
  matrix, ``O(B * N * T)``.

The 2,520 periods do not fill 16 equal blocks, so the oldest eight are dropped,
as PBO requires a caller to say (``Trim.OLDEST``). The returns are seeded
draws: the figures are timings, and the statistics' values say nothing.

The budget is judged by ``_stable_timing``'s method (ledger TST-011): CPU time,
the collector paused, the two measures interleaved, the fastest of three. Run
from the repository root::

    python benchmarks/benchmark_selection_statistics.py
"""

from __future__ import annotations

import functools
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _stable_timing import SAMPLES, fastest

from alphalab.research.data_snooping import reality_check, superior_predictive_ability
from alphalab.research.selection import (
    PBOResult,
    SelectionMeasure,
    TrialMatrix,
    Trim,
    probability_of_backtest_overfitting,
)

#: The plan's budget for PBO at this size, in CPU seconds (WS-06 item 15).
BUDGET_SECONDS = 60.0
TRIALS = 100
PERIODS = 2520
BLOCKS = 16
REPLICATIONS = 500


def _matrix() -> TrialMatrix:
    """100 trials of 2,520 seeded daily returns, their means a little apart."""

    draws = random.Random(20261116)
    return TrialMatrix.from_columns(
        {
            f"trial-{k:03d}": tuple(draws.gauss(0.0002 * (k % 7 - 3), 0.01) for _ in range(PERIODS))
            for k in range(TRIALS)
        },
        [float(t) for t in range(PERIODS)],
        "daily return",
    )


def run_benchmark() -> int:
    print(
        f"Starting Selection Statistics Benchmark ({TRIALS} trials x {PERIODS} periods, "
        f"S = {BLOCKS})..."
    )
    matrix = _matrix()
    results: dict[SelectionMeasure, PBOResult] = {}

    def pbo(measure: SelectionMeasure) -> None:
        results[measure] = probability_of_backtest_overfitting(
            matrix, blocks=BLOCKS, measure=measure, trim=Trim.OLDEST
        )

    measures = (SelectionMeasure.MEAN, SelectionMeasure.SHARPE_RATIO)
    judged = fastest([functools.partial(pbo, measure) for measure in measures])
    worst = max(judged)
    for measure, seconds in zip(measures, judged, strict=True):
        result = results[measure]
        print(
            f"  PBO by {measure.name}: {seconds:.2f} CPU s over {result.combinations} splits, "
            f"{result.trimmed} oldest periods trimmed (budget {BUDGET_SECONDS:.0f} s)"
        )
    for name, test in (("Reality Check", reality_check), ("SPA", superior_predictive_ability)):
        start = time.process_time()
        test(matrix, replications=REPLICATIONS, mean_block_length=10.0, seed=7)
        print(
            f"  {name}, B = {REPLICATIONS}: {time.process_time() - start:.2f} CPU s "
            "printed, not judged"
        )
    verdict = "ok" if worst <= BUDGET_SECONDS else "OVER THE PLAN'S BUDGET"
    print(f"  judged: CPU time, collector paused, fastest of {SAMPLES}: {verdict}")
    if worst > BUDGET_SECONDS:
        raise SystemExit(f"PBO took {worst:.2f} CPU s, over the plan's {BUDGET_SECONDS:.0f} s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_benchmark())
