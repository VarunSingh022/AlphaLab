"""The v4.1 release stress run: v4.0's scenarios, and the scale of a paused replay.

Kept for provenance and re-runnable, not run by the suite (it takes minutes)::

    PYTHONHASHSEED=0 python docs/audit/scripts/stress_v4_1.py [scenario ...]

The scenarios of ``stress_v4_0.py`` -- and through it v3.13's and v3.12's -- are
re-run as they were written. v4.1 adds one, asserting its outcome as well as
printing its cost; CPU seconds by ``time.process_time`` with the collector
running, as the earlier programs measured:

``replay``  Replay 2,000, 8,000 and 32,000 records, pause before the last,
            capture and serialize the paused replay, decode and restore it with
            the dataset and fresh objects, and resume it to the end (ledger
            FUT-001) -- once keeping every history, and once with a retention
            policy bounding every derived history, the cursor's lifecycle log
            included. Each resumed result must equal the uninterrupted replay's;
            the per-record costs must stay flat with every history kept, and the
            bounded payload must stop growing with the records.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
import time
from collections.abc import Callable
from decimal import Decimal
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


V40 = _load("stress_v4_0", Path(__file__).with_name("stress_v4_0.py"))

STRATEGY = "5c1e7a2b-3d4f-4a5b-8c6d-7e8f9a0b1c20"
ASSET = "6d2f8b3c-4e5a-4b6c-9d7e-8f9a0b1c2d30"


def _replay_once(records: int, bounded: bool) -> tuple[float, float]:
    """One replay of ``records``: paused before its last, checkpointed, restored, resumed.

    Returns the checkpoint's bytes and its capture's CPU microseconds, each a record.
    """

    from alphalab.backtesting.replay import PausedReplay, ReplayBacktest, ReplayProgress
    from alphalab.backtesting.replay_snapshot import capture, from_primitives, restore
    from alphalab.backtesting.state import ReplayResult
    from alphalab.lifecycle import digest_run
    from alphalab.persistence import deserialize, serialize
    from alphalab.runtime.retention import RetentionPolicy
    from alphalab.runtime.run_snapshot import RunObjects
    from alphalab.runtime.snapshot import RuntimeObjects
    from alphalab.strategy.state import RuntimeState
    from tests.integration.harness import (
        ScriptedStrategy,
        backtest_config,
        context_factory,
        dataset_of_quotes,
        running_strategy_state,
    )

    plan = {
        2.0 + index: Decimal("5") if index % 10 == 0 else Decimal("-5")
        for index in range(0, records, 5)
    }
    config = backtest_config(STRATEGY, seed=41)
    if bounded:
        policy = RetentionPolicy(market_history=500, steps=500, audit_events=500, results=500)
        config = dataclasses.replace(
            config, pipeline=dataclasses.replace(config.pipeline, retention=policy)
        )
    dataset = dataset_of_quotes(
        ASSET, [Decimal(100) + Decimal(index % 7) for index in range(records)]
    )

    def strategy_state() -> RuntimeState:
        return running_strategy_state(STRATEGY, ScriptedStrategy(STRATEGY, ASSET, plan))

    def before_the_last(progress: ReplayProgress) -> bool:
        return progress.records_replayed == records - 1

    def uninterrupted() -> ReplayResult:
        return ReplayBacktest.run(config, dataset, strategy_state(), context_factory)

    whole, replay_cpu = V40._timed(uninterrupted)
    paused = ReplayBacktest.run(
        config, dataset, strategy_state(), context_factory, pause_when=before_the_last
    )
    assert isinstance(paused, PausedReplay)

    def checkpoint() -> str:
        return serialize(capture(paused))

    payload, capture_cpu = V40._timed(checkpoint)
    objects = RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=config.pipeline.sizing_model,
            simulator=config.pipeline.simulator,
            strategies={STRATEGY: ScriptedStrategy(STRATEGY, ASSET, plan)},
            instruments=config.pipeline.instruments,
        ),
        fill_policy=config.fill_policy,
    )

    def restored_replay() -> PausedReplay:
        return restore(from_primitives(deserialize(payload)), objects, dataset)

    restored, restore_cpu = V40._timed(restored_replay)

    def resumed_replay() -> ReplayResult:
        return ReplayBacktest.resume(restored, context_factory)

    resumed, resume_cpu = V40._timed(resumed_replay)
    assert digest_run(resumed.backtest).result_id == digest_run(whole.backtest).result_id
    per_byte, per_capture = len(payload) / records, capture_cpu / records * 1e6
    print(
        f"  {records:>6} records: replay {replay_cpu / records * 1e6:6.1f} us a record; "
        f"checkpoint {len(payload) / 1e6:7.2f} MB ({per_byte:7.1f} B a record), "
        f"capture {per_capture:6.1f} us a record, restore "
        f"{restore_cpu / records * 1e6:6.1f} us a record, resume {resume_cpu:5.2f}s; "
        "resumed == uninterrupted"
    )
    return per_byte, per_capture


def scenario_replay() -> None:
    print("== replay: pause before the last record, capture, restore with fresh objects, resume")
    for bounded in (False, True):
        print(f"  -- {'every history bounded at 500' if bounded else 'every history kept'}")
        measured = [_replay_once(records, bounded) for records in (2_000, 8_000, 32_000)]
        payloads = [bytes_a_record for bytes_a_record, _ in measured]
        captures = [capture_a_record for _, capture_a_record in measured]
        if bounded:
            print(f"  bytes a record at 16x the run: {payloads[-1] / payloads[0]:.2f}x")
            assert payloads[-1] < payloads[0], "a bounded checkpoint stops growing with records"
        else:
            print(
                f"  at 16x the run: bytes a record {payloads[-1] / payloads[0]:.2f}x, "
                f"capture a record {captures[-1] / captures[0]:.2f}x"
            )
            assert payloads[-1] / payloads[0] < 1.1 and captures[-1] / captures[0] < 2.0


SCENARIOS: dict[str, Callable[[], None]] = {
    **V40.SCENARIOS,
    "replay": scenario_replay,
}

if __name__ == "__main__":
    chosen = sys.argv[1:] or list(SCENARIOS)
    for name in chosen:
        started = time.perf_counter()
        SCENARIOS[name]()
        print(f"  ({name}: {time.perf_counter() - started:.1f}s wall)")
