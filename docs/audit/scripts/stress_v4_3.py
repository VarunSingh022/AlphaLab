"""The v4.3 programme's stress run: v4.1's scenarios, and one per milestone that needs one.

Kept for provenance and re-runnable, not run by the suite (it takes minutes)::

    PYTHONHASHSEED=0 python docs/audit/scripts/stress_v4_3.py [scenario ...]

The scenarios of ``stress_v4_1.py`` -- and through it v4.0's, v3.13's and
v3.12's -- are re-run as they were written (plan WS-21). Each v4.3 scenario
asserts its outcome as well as printing its cost, in CPU seconds by
``time.process_time`` with the collector running, as the earlier programs
measured. Later milestones add theirs: capture overhead (M3), experiments (M4),
per-venue execution (M8).

``registries`` (M0, DET-007 and D-54-7)
            Start 10,000 and then 100,000 experiment runs in one tracker inside
            a seeded scope. Every run id must be distinct, the scope's stream
            must not move -- a run's id is derived from its declaration and its
            place, never drawn -- and the cost of a start must stay flat as the
            tracker grows.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V41 = _load("stress_v4_1", Path(__file__).with_name("stress_v4_1.py"))


def _starts(runs: int) -> tuple[float, int, bool]:
    """``runs`` starts in one tracker inside a seeded scope.

    Returns CPU microseconds a start, how many distinct ids were made, and
    whether the scope's stream stayed where it was.
    """

    from alphalab.common.ids import current_id_position, id_scope
    from alphalab.experiment_tracking import ExperimentTracker, start_run

    tracker = ExperimentTracker()
    ids: set[str] = set()
    with id_scope(43):
        before = current_id_position()
        started = time.process_time()
        for index in range(runs):
            tracker, run_id = start_run(
                tracker, "sweep", {"lookback": index % 50, "threshold": 0.5}, float(index)
            )
            ids.add(run_id)
        elapsed = time.process_time() - started
        unmoved = current_id_position() == before
    return elapsed / runs * 1e6, len(ids), unmoved


def scenario_registries() -> None:
    print("== registries: experiment runs started inside a seeded scope (DET-007, D-54-7)")
    measured = {runs: _starts(runs) for runs in (10_000, 100_000)}
    for runs, (micros, distinct, unmoved) in measured.items():
        print(f"  {runs:>7,} starts: {micros:.1f} us a start, {distinct:,} distinct ids, "
              f"scope {'unmoved' if unmoved else 'MOVED'}")  # fmt: skip
        assert distinct == runs, "every run id is distinct"
        assert unmoved, "starting a run draws nothing from the scope around it"
    ratio = measured[100_000][0] / measured[10_000][0]
    print(f"  a start at 10x the tracker: {ratio:.2f}x")
    assert ratio < 2.0, "a start's cost stays flat as the tracker grows"


SCENARIOS: dict[str, Callable[[], None]] = {
    **V41.SCENARIOS,
    "registries": scenario_registries,
}

if __name__ == "__main__":
    chosen = sys.argv[1:] or list(SCENARIOS)
    for name in chosen:
        started = time.perf_counter()
        SCENARIOS[name]()
        print(f"  ({name}: {time.perf_counter() - started:.1f}s wall)")
