"""High-performance benchmark suite for the model and strategy lifecycle.

Three shapes of growth, each of which was quadratic before v2.4 and each of
which a real workload actually produces:

* many versions of one strategy line -- a line that is iterated on for a year;
* many strategy lines -- a book of strategies, each with one version;
* a long deployment ledger for one environment -- a year of daily releases,
  where "what is live?" and "what was live before?" are asked on every write.

The end-to-end sweep is the one that matters most: it is the whole lifecycle,
not one registry, and it is what a caller would actually drive.
"""

import time
from dataclasses import replace

from alphalab.experiment_tracking import complete_run, log_metrics, start_run
from alphalab.lifecycle import (
    LifecycleState,
    MetricThreshold,
    ValidationMethod,
    ValidationPolicy,
    build_evidence,
    deploy_strategy_version,
    promote_strategy_version,
    record_evidence,
    register_model_version,
    register_strategy,
    rollback_environment,
)
from alphalab.model_registry import ModelStage, promote
from alphalab.studio.strategy import StrategyDefinition

POLICY = ValidationPolicy("bench", (MetricThreshold("sharpe_ratio", minimum=0.0),))


def _definition(index: int) -> StrategyDefinition:
    return StrategyDefinition(
        f"cand-{index:05d}", "bench", str(index), "bench", "", {"fast": float(index)}
    )


def _staged_model(state: LifecycleState) -> LifecycleState:
    tracker, run_id = start_run(state.experiments, "bench", {"lr": 0.1}, 0.0)
    tracker = log_metrics(tracker, run_id, {"sharpe": 1.0})
    tracker = complete_run(tracker, run_id, 1.0)
    state = replace(state, experiments=tracker)
    state, model = register_model_version(state, "bench-model", object(), 2.0, run_id=run_id)
    return replace(
        state, models=promote(state.models, model.name, model.version, ModelStage.STAGING, 3.0)
    )


def _promoted(state: LifecycleState, line: str, index: int) -> LifecycleState:
    state, ref = register_strategy(state, line, _definition(index), float(index))
    evidence = build_evidence(
        ValidationMethod.EXTERNAL, str(ref), "ds", {"sharpe_ratio": 1.0}, float(index)
    )
    state = record_evidence(state, evidence)
    return promote_strategy_version(
        state, ref.name, ref.version, POLICY, evidence.evidence_id, float(index)
    )


def run_benchmark() -> None:
    N_VERSIONS = 20_000
    print(f"Starting Lifecycle Benchmark: {N_VERSIONS} versions of one strategy line...")
    state = LifecycleState()
    start = time.perf_counter()
    for index in range(N_VERSIONS):
        state, _ = register_strategy(state, "bench", _definition(index), float(index))
    duration = time.perf_counter() - start
    print(f"  register_strategy (one line):  {duration:.4f}s, {N_VERSIONS / duration:.2f} ops/sec")

    N_LINES = 20_000
    state = LifecycleState()
    start = time.perf_counter()
    for index in range(N_LINES):
        state, _ = register_strategy(state, f"line-{index}", _definition(index), float(index))
    duration = time.perf_counter() - start
    print(f"  register_strategy (one each):  {duration:.4f}s, {N_LINES / duration:.2f} ops/sec")

    N_PROMOTE = 5_000
    state = LifecycleState()
    start = time.perf_counter()
    for index in range(N_PROMOTE):
        state = _promoted(state, "bench", index)
    duration = time.perf_counter() - start
    print(f"  register + evidence + promote: {duration:.4f}s, {N_PROMOTE / duration:.2f} ops/sec")

    N_DEPLOY = 2_000
    start = time.perf_counter()
    for version in range(1, N_DEPLOY + 1):
        state, _ = deploy_strategy_version(state, "bench", version, "paper", float(version))
    duration = time.perf_counter() - start
    print(f"  deploy ({N_DEPLOY} releases):     {duration:.4f}s, {N_DEPLOY / duration:.2f} ops/sec")

    N_ROLLBACK = 1_000
    start = time.perf_counter()
    for index in range(N_ROLLBACK):
        state, _ = rollback_environment(state, "paper", float(index))
    duration = time.perf_counter() - start
    print(f"  rollback (deep ledger):        {duration:.4f}s, {N_ROLLBACK / duration:.2f} ops/sec")

    N_END_TO_END = 2_000
    print(f"  end to end: {N_END_TO_END} strategies through promote -> deploy -> rollback...")
    state = _staged_model(LifecycleState())
    start = time.perf_counter()
    for index in range(N_END_TO_END):
        state = _promoted(state, "e2e", index)
        state, _ = deploy_strategy_version(state, "e2e", index + 1, "paper", float(index))
        if index:
            # Half the iterations also roll back, which archives the version
            # just deployed and restores the one before it. Re-deploying that
            # archived version would be refused, so the next iteration ships a
            # new one -- which is what a rollback actually leads to.
            state, _ = rollback_environment(state, "paper", float(index))
    duration = time.perf_counter() - start
    print(
        f"  full lifecycle:                {duration:.4f}s, "
        f"{N_END_TO_END / duration:.2f} lifecycles/sec"
    )


if __name__ == "__main__":
    run_benchmark()
