"""How a benchmark's pass/fail ceiling is judged (ledger TST-010, TST-011).

A benchmark *reports* one run of each workload with the cyclic collector on,
which is what a real run pays. A ceiling -- "the 4x workload may cost at most 6x
the time", "the run layer may cost at most 1.25x the bare step" -- is a claim
about the code, and judging it on that one run reads the machine instead: a full
collection that lands in one sample and not the other, or a slow phase of the
host, is reported as the algorithm's growth. ``benchmark_institutional``'s
scenario ceiling failed the v3.11 release gate exactly that way, at 6.05x on code
whose repeated runs read 3.0x to 5.2x.

So every ceiling is judged the way the test suite's complexity guards time code
(``tests/regression/_timing.py``, ledger TST-001):

* the **process CPU clock**, which does not count time the process was not run;
* the **collector paused** while a sample is taken -- a collection triggered by
  whatever heap the benchmark built before is a cost of the session, not of the
  code being measured;
* the **workloads interleaved**, so a slow phase of the machine falls on every
  workload rather than on one;
* the **fastest of several samples**, because noise only ever makes a sample
  slower.

A ceiling on a small cost relative to a workload -- the capture's over the run
it captures, PC-07 -- is judged instead by the **median of paired ratios**
(:func:`rounds`). Each round runs every workload back to back, so a slow phase
or a slower core that covers the round scales the numerator and the denominator
alike, and the median discards a round one of them fell into alone. Hosted
macOS runners read the fastest of nine samples as figures that cannot all be
true: a capture cheaper than no capture, a series alone dearer than series and
trace (v4.3 M6, TD-36).
"""

from __future__ import annotations

import gc
import math
import time
from collections.abc import Callable, Sequence
from typing import Final

__all__ = ["SAMPLES", "fastest", "growth", "rounds"]

#: Samples taken of each workload; the fastest is kept.
SAMPLES: Final = 3


def rounds(
    workloads: Sequence[Callable[[], object]],
    samples: int = SAMPLES,
    *,
    clock: Callable[[], float] = time.process_time,
    pause_collector: bool = True,
) -> list[list[float]]:
    """Every workload's CPU time in every round: ``rounds(...)[round][workload]``.

    The rounds run the workloads interleaved, in the order given, the collector
    paused for the whole measurement and restored afterwards, as it was.
    """

    table: list[list[float]] = []
    was_enabled = gc.isenabled()
    gc.collect()
    if pause_collector:
        gc.disable()
    try:
        for _ in range(samples):
            row = []
            for workload in workloads:
                start = clock()
                workload()
                row.append(clock() - start)
            table.append(row)
    finally:
        if was_enabled:
            gc.enable()
    return table


def fastest(
    workloads: Sequence[Callable[[], object]],
    samples: int = SAMPLES,
    *,
    clock: Callable[[], float] = time.process_time,
    pause_collector: bool = True,
) -> list[float]:
    """The fastest CPU time of each of ``workloads``, sampled interleaved.

    Returns one figure per workload, in the order given. The collector is
    paused for the whole measurement and restored afterwards, as it was.
    ``clock`` is the process's CPU clock. A workload whose work runs in child
    processes is judged on a clock that counts theirs too, and with the
    collector running: a child's collector cannot be paused, so pausing only
    this process's would favour the workload that runs here (v4.3,
    ``benchmark_experiments``).
    """

    table = rounds(workloads, samples, clock=clock, pause_collector=pause_collector)
    best = [math.inf] * len(workloads)
    for row in table:
        best = [min(low, value) for low, value in zip(best, row, strict=True)]
    return best


def growth(work: Callable[[int], object], small: int, large: int) -> float:
    """How many times longer ``work(large)`` takes than ``work(small)``, judged as above."""

    small_time, large_time = fastest([lambda: work(small), lambda: work(large)])
    return large_time / max(small_time, 1e-9)
