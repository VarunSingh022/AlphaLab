"""What an executor costs over the trials it runs (v4.3, ADR-0072, plan WS-05 item 15).

The plan holds an executor's overhead to at most 5 percent of the summed trial
time. Measured on ``_experiment_workload``'s experiment -- 48 trials, each one
strategy over ten years of daily records, about 0.6 s of a strategy that does no
work of its own, so the executor's costs weigh on it as heavily as on any trial
-- as CPU time: the process's own and its workers', so a busy host's waiting is
never counted.

* **Sequential** is the reference: the trials' own time.
* **Spawned, one worker reused** (``trials_per_worker=None``) is what is judged
  against ``MAX_EXECUTOR_OVERHEAD``: the executor's own cost, one worker's start
  -- an interpreter, AlphaLab's imports and the experiment, about half a second
  -- and each trial's request and answer, a few milliseconds. On the reference
  machine (CPython 3.12, Apple arm64) it is 1.8 percent.
* **Spawned, one fresh interpreter per trial** (``trials_per_worker=1``) pays
  a start every trial. It is printed, not judged: the most isolated executor,
  chosen for that.
* **Spawned, four workers reused** is printed, not judged. Each worker adds a
  start, so with ``W`` workers the executor's cost is ``W`` starts and the
  exchanges: the starts fall under 5 percent once each worker runs about ten
  seconds of trials, and a trial of the plan's cost envelope (K.5) runs ten
  minutes. The figure also holds what no executor controls: the host's cores
  slow when all are busy. A control of the same trials in four plain processes,
  with no executor, cost 10.1 percent more CPU than the sequential run on the
  reference machine, which has four performance and four efficiency cores.

Every spawned experiment must equal the sequential one record for record, or
the benchmark fails before it judges anything. Run from the repository root::

    python benchmarks/benchmark_experiments.py
"""

from __future__ import annotations

import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _experiment_workload import REGISTRY, SPEC

from alphalab.lifecycle.experiments import (
    ExperimentRecord,
    SequentialExecutor,
    SpawnExecutor,
    TrialExecutor,
    TrialLedger,
    run_experiment,
)
from alphalab.model_registry.artifact_store import MemoryArtifactStore
from alphalab.model_registry.evidence import EvidenceStore, MemoryEvidenceIndex

#: The plan's budget for an executor's own cost (WS-05 item 15).
MAX_EXECUTOR_OVERHEAD = 0.05
WHERE = "_experiment_workload:DEFINITION"


def _children_cpu() -> float:
    used = resource.getrusage(resource.RUSAGE_CHILDREN)
    return used.ru_utime + used.ru_stime


def _timed(executor: TrialExecutor) -> tuple[float, float, ExperimentRecord]:
    """CPU seconds the experiment took under ``executor``, its workers' included, and its wall."""

    ledger = TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))
    own, children, wall = time.process_time(), _children_cpu(), time.perf_counter()
    record = run_experiment(SPEC, REGISTRY, "flip", ledger, executor)
    cpu = (time.process_time() - own) + (_children_cpu() - children)
    return cpu, time.perf_counter() - wall, record


def run_benchmark() -> int:
    trials = len(SPEC.trials())
    print(f"Starting Experiment Executor Benchmark ({trials} trials)...")
    sequential, wall, reference = _timed(SequentialExecutor())
    print(f"  sequential: {sequential:.2f} CPU s for the trials ({wall:.1f} s on the clock)")
    failures = 0
    for name, executor, judged in (
        ("spawned, one worker reused", SpawnExecutor(WHERE, 1, None), True),
        ("spawned, one fresh interpreter per trial", SpawnExecutor(WHERE, 1, 1), False),
        ("spawned, four workers reused", SpawnExecutor(WHERE, 4, None), False),
    ):
        seconds, wall, record = _timed(executor)
        if record != reference:
            raise SystemExit(f"The {name} experiment's record differs from the sequential one.")
        overhead = seconds / sequential - 1.0
        verdict = "printed, not judged"
        if judged:
            verdict = "ok" if overhead <= MAX_EXECUTOR_OVERHEAD else "OVER THE PLAN'S BUDGET"
            failures += verdict != "ok"
        print(
            f"  {name}: {seconds:.2f} CPU s, overhead {overhead:+.1%} of the trials' "
            f"(budget {MAX_EXECUTOR_OVERHEAD:.0%}), {(seconds - sequential) / trials * 1e3:.0f} "
            f"ms a trial ({wall:.1f} s on the clock) {verdict}"
        )
    print(f"  every record equal: {reference.counts.recorded} trials, {reference.counts}")
    print("  judged: CPU time of the process and of its workers")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run_benchmark())
