"""The one way AlphaLab's complexity guards time code (TST-001).

Every guard asserts a **growth ratio** between two input sizes -- a claim about
the algorithm, not the machine -- and every one reads it the same way:

* **the process CPU clock**, where the platform keeps it finely (time spent
  descheduled is not the code's), else the monotonic wall clock;
* **the collector disabled** while timing -- a full collection triggered by the
  rest of the suite's heap is a cost of the test session, not of the code;
* **the two sizes interleaved**, so a slow phase of the machine is shared by
  both rather than read as scaling;
* **the fastest of several samples** at each size, because noise only ever
  makes a sample slower.

Until v3.10 seven guards timed the wall clock with the collector running, one
size after the other, and failed under load: the replay cursor read 12x for a 4x
input beside a concurrent type check, and a defect-injection run failed the
scheduler's doubling sweep on a timing blip (ledger TST-001). The method here
was introduced by ``test_lifecycle_registry_complexity.py`` in v3.8, where its
derivation and measurements are recorded; it lives here so there is one method
to maintain.
"""

from __future__ import annotations

import gc
import math
import time
from collections.abc import Callable

__all__ = ["CLOCK", "compare", "growth", "timings"]


def _fine_grained(clock: Callable[[], float]) -> bool:
    """Whether ``clock`` advances in steps fine enough to time a few milliseconds.

    The process CPU clock steps in nanoseconds on Linux and microseconds on
    macOS, but on Windows it advances with the scheduler tick -- about 15.6 ms,
    longer than the smallest sample a guard takes -- whatever
    ``time.get_clock_info`` reports. So the step is observed rather than
    trusted, for at most half a second of wall time.
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


#: The clock every guard reads.
CLOCK: Callable[[], float] = (
    time.process_time if _fine_grained(time.process_time) else time.perf_counter
)


def timings(
    measure: Callable[[int], float], small: int, large: int, rounds: int = 5
) -> tuple[float, float]:
    """The fastest of ``rounds`` samples at each size, the sizes interleaved.

    ``measure(size)`` does the work for ``size`` and returns the seconds it took
    on :data:`CLOCK`.
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


def compare(
    first: Callable[[], object], second: Callable[[], object], rounds: int = 5
) -> tuple[float, float]:
    """The fastest of ``rounds`` samples of two pieces of work, read as :func:`timings` reads."""

    def once(work: Callable[[], object]) -> float:
        start = CLOCK()
        work()
        return CLOCK() - start

    works = (first, second)
    return timings(lambda index: once(works[index]), 0, 1, rounds)


def growth(
    small: Callable[[], object],
    large: Callable[[], object],
    rounds: int = 5,
    floor: float = 1e-4,
) -> float:
    """How many times longer ``large`` takes than ``small``, read as :func:`timings` reads.

    ``floor`` bounds the small measurement from below: a small case that runs in
    a microsecond divides into a huge ratio for reasons that have nothing to do
    with complexity.
    """

    fast_small, fast_large = compare(small, large, rounds)
    return fast_large / max(fast_small, floor)
