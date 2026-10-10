"""The v4.0 release stress run: v3.13's scenarios, and the scale of what v4.0 touched.

Kept for provenance and re-runnable, not run by the suite (it takes minutes)::

    PYTHONHASHSEED=0 python3.12 docs/audit/scripts/stress_v4_0.py [scenario ...]

The scenarios of ``stress_v3_13.py`` -- and through it ``stress_v3_12.py`` -- are
re-run as they were written. v4.0 adds these, each asserting an outcome as well
as printing what it cost; CPU seconds by ``time.process_time`` with the collector
running, as the earlier programs measured:

``restore``     Capture, serialize, decode and restore a run of 2,000, 8,000 and
                32,000 records, every live object now described and compared
                (PER-008): the cost per record stays flat, and the restored run
                equals the captured one.
``ingestion``   Ingest 10,000, 40,000 and 160,000 rows with one in a hundred
                unreadable under a dropping policy, each drop recorded in the
                provenance (DAT-010), and the same rows refused under a refusing
                policy: linear in the rows, and the refusal is not slower than
                the ingestion it stops.
``startup``     Start 250, 1,000 and 4,000 strategies one at a time through
                ``start_strategy`` (DOC-009). Each call copies the runtime's
                strategy mapping, as ``register_strategy`` always has, so the
                total is quadratic in the count; the scenario measures it rather
                than assuming it, and states the envelope.
"""

from __future__ import annotations

import gc
import importlib.util
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V313 = _load("stress_v3_13", Path(__file__).with_name("stress_v3_13.py"))


def _timed(work: Callable[[], Any]) -> tuple[Any, float]:
    gc.collect()
    start = time.process_time()
    result = work()
    return result, time.process_time() - start


def scenario_restore() -> None:
    from alphalab.persistence import deserialize, serialize
    from alphalab.runtime.run_snapshot import RunObjects, capture, from_primitives, restore
    from alphalab.runtime.snapshot import RuntimeObjects

    print("== restore: capture, serialize, decode and restore, every live object described")
    per_record = []
    for records in (2_000, 8_000, 32_000):
        state, _ = V313._run(records, 10)
        strategies = {
            strategy_id: entry.instance
            for strategy_id, entry in state.pipeline.strategy.strategies.items()
        }
        objects = RunObjects(
            pipeline=RuntimeObjects(
                sizing_model=state.config.pipeline.sizing_model,
                simulator=state.config.pipeline.simulator,
                strategies=strategies,
                instruments=state.config.pipeline.instruments,
            ),
            fill_policy=state.config.fill_policy,
        )

        def round_trip(captured: Any = state, held: RunObjects = objects) -> Any:
            return restore(from_primitives(deserialize(serialize(capture(captured)))), held)

        restored, cpu = _timed(round_trip)
        assert restored == state, "the restored run is the captured run"
        per_record.append(cpu / records * 1e6)
        print(f"  {records:>6} records: {cpu:6.2f}s CPU, {per_record[-1]:.1f} us a record")
    growth = per_record[-1] / per_record[0]
    print(f"  per-record cost at 16x the run: {growth:.2f}x")
    assert growth < 2.0, "a round trip is linear in the run"


def _rows(count: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        close = "nan" if index % 100 == 37 else str(100 + index % 13)
        rows.append(
            {
                "timestamp": str(1_700_000_000 + index * 60),
                "symbol": "STRESS",
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": "1000",
            }
        )
    return rows


def scenario_ingestion() -> None:
    from alphalab.api import ingest_rows
    from alphalab.data import (
        REFUSE_EVERYTHING,
        CleaningPolicy,
        DataAssetClass,
        DataQualityError,
        DuplicatePolicy,
        IngestionRequest,
        InvalidRecordPolicy,
        MissingValuePolicy,
        OrderingPolicy,
        PriceBasis,
        SourceKind,
        TimeFrequency,
        raw_source_from_bytes,
    )
    from alphalab.data.time import BarStamp

    def request(policy: CleaningPolicy) -> IngestionRequest:
        return IngestionRequest(
            name="STRESS",
            source=raw_source_from_bytes(SourceKind.IN_MEMORY, "stress", b"", 1.0, "text/csv"),
            frequency=TimeFrequency.MINUTE,
            bar_stamp=BarStamp.INTERVAL_END,
            asset_class=DataAssetClass.EQUITY,
            cleaning_policy=policy,
            price_basis=PriceBasis.RAW,
        )

    dropping = CleaningPolicy(
        duplicates=DuplicatePolicy.REFUSE,
        ordering=OrderingPolicy.REFUSE,
        invalid_records=InvalidRecordPolicy.DROP,
        missing_values=MissingValuePolicy.DROP_ROW,
    )
    print("== ingestion: one row in a hundred unreadable, dropped and recorded; then refused")
    per_row = []
    for count in (10_000, 40_000, 160_000):
        rows = _rows(count)

        def ingest(rows: list[dict[str, object]] = rows) -> Any:
            return ingest_rows(rows, request(dropping))

        result, cpu = _timed(ingest)
        dropped = result.dataset.require_provenance().transformations[0]
        assert (dropped.operation, dropped.rows_affected) == ("drop_unreadable_row", count // 100)
        assert len(result.dataset.records) == count - count // 100

        def refuse(rows: list[dict[str, object]] = rows) -> str:
            try:
                ingest_rows(rows, request(REFUSE_EVERYTHING))
            except DataQualityError as error:
                return str(error)
            raise AssertionError("a refusing policy ingested an unreadable row")

        message, refused = _timed(refuse)
        assert "InvalidRecordPolicy.REFUSE" in message
        per_row.append(cpu / count * 1e6)
        print(
            f"  {count:>7} rows: ingested in {cpu:6.2f}s CPU ({per_row[-1]:.1f} us a row), "
            f"refused in {refused:6.2f}s"
        )
        assert refused <= 1.2 * cpu, "the refusal costs no more than the ingestion"
    growth = per_row[-1] / per_row[0]
    print(f"  per-row cost at 16x the rows: {growth:.2f}x")
    assert growth < 2.0, "ingestion is linear in the rows"


def scenario_startup() -> None:
    from alphalab.strategy import create_runtime, start_strategy
    from tests.integration.harness import ScriptedStrategy

    print("== startup: strategies started one at a time through start_strategy")
    timings = []
    for count in (250, 1_000, 4_000):

        def start_all(count: int = count) -> Any:
            state = create_runtime()
            for index in range(count):
                strategy_id = f"S-{index:05d}"
                state = start_strategy(
                    state,
                    strategy_id,
                    ScriptedStrategy(strategy_id, "00000000-0000-0000-0000-000000000001", {}),
                    config={},
                    subscriptions={"quotes"},
                    at=1.0,
                )
            return state

        state, cpu = _timed(start_all)
        assert len(state.strategies) == count
        timings.append(cpu)
        print(f"  {count:>5} strategies: {cpu:6.2f}s CPU")
    print(f"  at 16x the strategies: {timings[-1] / timings[0]:.1f}x the time")
    assert timings[-1] < 30.0, "4,000 strategies start well inside a run's setup budget"


SCENARIOS: dict[str, Callable[[], None]] = {
    **V313.SCENARIOS,
    "restore": scenario_restore,
    "ingestion": scenario_ingestion,
    "startup": scenario_startup,
}

if __name__ == "__main__":
    chosen = sys.argv[1:] or list(SCENARIOS)
    for name in chosen:
        started = time.perf_counter()
        SCENARIOS[name]()
        print(f"  ({name}: {time.perf_counter() - started:.1f}s wall)")
