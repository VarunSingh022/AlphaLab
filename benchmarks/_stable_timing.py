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
"""

from __future__ import annotations

import gc
import math
import time
from collections.abc import Callable, Sequence
from typing import Final

__all__ = ["SAMPLES", "fastest", "growth"]

#: Samples taken of each workload; the fastest is kept.
SAMPLES: Final = 3


def fastest(workloads: Sequence[Callable[[], object]], samples: int = SAMPLES) -> list[float]:
    """The fastest CPU time of each of ``workloads``, sampled interleaved.

    Returns one figure per workload, in the order given. The collector is
    paused for the whole measurement and restored afterwards, as it was.
    """

    best = [math.inf] * len(workloads)
    was_enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        for _ in range(samples):
            for index, workload in enumerate(workloads):
                start = time.process_time()
                workload()
                best[index] = min(best[index], time.process_time() - start)
    finally:
        if was_enabled:
            gc.enable()
    return best


def growth(work: Callable[[int], object], small: int, large: int) -> float:
    """How many times longer ``work(large)`` takes than ``work(small)``, judged as above."""

    small_time, large_time = fastest([lambda: work(small), lambda: work(large)])
    return large_time / max(small_time, 1e-9)
