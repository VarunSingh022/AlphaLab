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
  machine (CPython 3.12, Apple arm64) it is 2.1 percent, fastest of three (1.8
  on single runs).
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

The judged pair is timed by ``_stable_timing``'s method (ledger TST-011): CPU
time, the two interleaved, the fastest of three -- on a clock that counts the
workers' CPU too, and with the collector running, because a worker's cannot be
paused, and pausing only the parent's would favour the sequential run. Every
spawned experiment must equal the sequential one record for record, or the
benchmark fails. Run from the repository root::

    python benchmarks/benchmark_experiments.py
"""

from __future__ import annotations

import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _experiment_workload import REGISTRY, SPEC
from _stable_timing import SAMPLES, fastest

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


def _cpu() -> float:
    """The CPU time of this process and of every child it has waited for."""

    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + children.ru_utime + children.ru_stime


def _run(executor: TrialExecutor) -> ExperimentRecord:
    ledger = TrialLedger(EvidenceStore(MemoryArtifactStore(), MemoryEvidenceIndex()))
    return run_experiment(SPEC, REGISTRY, "flip", ledger, executor)


def _once(executor: TrialExecutor) -> tuple[float, float, ExperimentRecord]:
    """One run's CPU seconds, its workers' included, its seconds on the clock, and its record."""

    cpu, wall = _cpu(), time.perf_counter()
    record = _run(executor)
    return _cpu() - cpu, time.perf_counter() - wall, record


def run_benchmark() -> int:
    trials = len(SPEC.trials())
    print(f"Starting Experiment Executor Benchmark ({trials} trials)...")
    records: dict[str, ExperimentRecord] = {}

    def judged(name: str, executor: TrialExecutor) -> None:
        records[name] = _run(executor)

    sequential, reused = fastest(
        [
            lambda: judged("sequential", SequentialExecutor()),
            lambda: judged("reused", SpawnExecutor(WHERE, 1, None)),
        ],
        clock=_cpu,
        pause_collector=False,
    )
    reference = records["sequential"]
    if records["reused"] != reference:
        raise SystemExit("The spawned experiment's record differs from the sequential one.")
    overhead = reused / sequential - 1.0
    verdict = "ok" if overhead <= MAX_EXECUTOR_OVERHEAD else "OVER THE PLAN'S BUDGET"
    print(f"  sequential: {sequential:.2f} CPU s for the trials")
    print(
        f"  spawned, one worker reused: {reused:.2f} CPU s, overhead {overhead:+.1%} of the "
        f"trials' (budget {MAX_EXECUTOR_OVERHEAD:.0%}), "
        f"{(reused - sequential) / trials * 1e3:.0f} ms a trial {verdict}"
    )
    for name, executor in (
        ("spawned, one fresh interpreter per trial", SpawnExecutor(WHERE, 1, 1)),
        ("spawned, four workers reused", SpawnExecutor(WHERE, 4, None)),
    ):
        seconds, wall, record = _once(executor)
        if record != reference:
            raise SystemExit(f"The {name} experiment's record differs from the sequential one.")
        print(
            f"  {name}: {seconds:.2f} CPU s, overhead {seconds / sequential - 1.0:+.1%} of the "
            f"trials', {(seconds - sequential) / trials * 1e3:.0f} ms a trial ({wall:.1f} s on "
            "the clock) printed, not judged"
        )
    print(f"  every record equal: {reference.counts.recorded} trials, {reference.counts}")
    print(
        "  judged: CPU time of the process and its workers, collector running, "
        f"fastest of {SAMPLES}"
    )
    return 0 if verdict == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(run_benchmark())
