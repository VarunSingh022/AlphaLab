"""A replay pauses between two records and resumes to the uninterrupted result (ledger FUT-001).

Until v4.1 a replay could restart deterministically but not stop and continue:
its cursor and the cursor's own identifier stream had no snapshot (ADR-0029,
ADR-0030), and an interrupted replay began again from its dataset. These tests
hold the v4.1 contract:

* **Equality.** A replay paused at any boundary -- before the first record,
  inside an instant, before the last -- captured, serialized, restored with
  freshly built objects and resumed produces exactly what the uninterrupted
  replay produces: every order, fill, trade, step, position, cash balance, P&L
  figure, strategy lifecycle event and identity (``result_id`` is a digest of
  the whole recorded run). Nothing is processed twice and nothing is skipped;
  the workload (``tests/integration/replay_workload.py``) is built so that
  either would move an order.
* **Durability.** A fresh interpreter resumes what another one paused, and a
  process that dies mid-run resumes from its last stored checkpoint.
* **Refusal.** A payload that is corrupt, of another schema, internally
  inconsistent, or offered with another dataset or other live objects is
  refused by name -- never continued from.
"""

from __future__ import annotations

import dataclasses
import json
import os
import random
import subprocess
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from alphalab import api
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.exceptions import DatasetValidationError
from alphalab.backtesting.replay import (
    REPLAY_CURSOR_SEED_OFFSET,
    PausedReplay,
    ReplayBacktest,
    ReplayProgress,
)
from alphalab.backtesting.replay_snapshot import (
    REPLAY_SNAPSHOT_SCHEMA,
    ReplaySnapshot,
    capture,
    dataset_digest,
    from_primitives,
    restore,
)
from alphalab.backtesting.state import ReplayResult
from alphalab.common.ids import IdStreamPosition, current_id_position, id_scope, new_id
from alphalab.execution.policy import LiquidityCappedFill
from alphalab.market.quote import Quote
from alphalab.persistence import FileRunStateStore, deserialize, serialize
from alphalab.persistence.exceptions import SerializationError, StateDecodeError, StorageError
from alphalab.replay.engine import ReplayEngine
from alphalab.replay.events import ReplayAdvanced, ReplayPaused, ReplayResumed, ReplayStarted
from alphalab.replay.exceptions import InvalidReplayStateError, ReplayValidationError
from alphalab.replay.state import ReplayState, ReplayStatus
from alphalab.runtime.retention import RetentionPolicy
from alphalab.runtime.run import ExecutionMode
from alphalab.runtime.run_snapshot import RUN_SNAPSHOT_SCHEMA
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.runtime.snapshot import StrategyStateRecord
from tests.integration import replay_workload as workload
from tests.integration.harness import context_factory, sized_quote

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "snapshots" / "v4.1.0" / "replay_paused.json"

#: The uninterrupted replay every continuation is held to, computed once.
REFERENCE = workload.outcome(workload.uninterrupted())


def _paused(*boundaries: int, seed: int | None = workload.SEED) -> PausedReplay:
    result = workload.run(workload.pause_at(*boundaries), seed=seed)
    assert isinstance(result, PausedReplay), "the replay finished instead of pausing"
    return result


def _payload(paused: PausedReplay) -> dict[str, Any]:
    decoded = deserialize(serialize(capture(paused)))
    assert isinstance(decoded, dict)
    return decoded


def _restore_payload(payload: dict[str, Any], dataset: MarketDataset | None = None) -> PausedReplay:
    return restore(
        from_primitives(payload),
        workload.objects(),
        workload.dataset() if dataset is None else dataset,
    )


def _finish(paused: PausedReplay) -> ReplayResult:
    result = ReplayBacktest.resume(paused, context_factory)
    assert isinstance(result, ReplayResult)
    return result


# --------------------------------------------------------------------------- #
# A. Equality: every boundary, every route
# --------------------------------------------------------------------------- #


def test_the_workload_exercises_what_a_boundary_could_break() -> None:
    """A guard on the guard: the workload trades, partially fills, slices and remembers."""

    result = workload.uninterrupted()
    run = result.backtest
    assert result.records_replayed == workload.TOTAL == 24
    filled: dict[str, Decimal] = {}
    for fill in run.fills:
        filled[str(fill.order_id)] = filled.get(str(fill.order_id), Decimal(0)) + fill.quantity
    assert len(run.fills) >= 10
    assert any(
        filled.get(str(order.order_id.value), Decimal(0)) < order.quantity for order in run.orders
    ), "LiquidityCappedFill leaves orders partly filled across boundaries"
    state = capture_run(run.run).pipeline.strategy[0].state
    assert isinstance(state, StrategyStateRecord)
    assert len(state.payload["slices"]) == workload.INSTANTS
    assert state.payload["fills"] == len(run.fills)


@pytest.mark.parametrize("boundary", range(workload.TOTAL + 1))
def test_every_boundary_resumes_to_the_uninterrupted_replay(boundary: int) -> None:
    """Captured, serialized, restored with fresh objects in this process, and resumed.

    Odd boundaries fall between the two records of one instant, so the
    resumed replay must close that instant's slice once, after its second
    record. The last boundary (``TOTAL``) is after the last record: there is
    nothing left to pause before, and the replay finishes instead.
    """

    result = workload.run(workload.pause_at(boundary))
    if boundary == workload.TOTAL:
        assert isinstance(result, ReplayResult), "nothing remains to pause before"
    else:
        assert isinstance(result, PausedReplay)
        assert result.records_replayed == boundary
        result = _finish(workload.round_trip(result))
    assert workload.outcome(result) == REFERENCE


def test_a_paused_replay_resumes_in_memory_without_a_round_trip() -> None:
    assert workload.outcome(_finish(_paused(7))) == REFERENCE


def test_pausing_before_the_first_record() -> None:
    paused = _paused(0)

    assert paused.records_replayed == 0 and paused.run.processed == 0
    assert paused.run.last_record_timestamp is None
    assert [type(event) for event in paused.cursor.system_events] == [ReplayStarted, ReplayPaused]
    assert workload.outcome(_finish(workload.round_trip(paused))) == REFERENCE


def test_pausing_before_the_last_record() -> None:
    paused = _paused(workload.TOTAL - 1)

    assert paused.records_replayed == workload.TOTAL - 1
    assert workload.outcome(_finish(workload.round_trip(paused))) == REFERENCE


def test_a_replay_of_one_record() -> None:
    dataset = MarketDataset.of(
        "ONE", [sized_quote(workload.FIRST, 10.0, Decimal("100"), Decimal("3"))]
    )

    def replay(pause_when: Callable[[ReplayProgress], bool] | None) -> ReplayResult | PausedReplay:
        config = workload.config()
        if pause_when is None:
            return ReplayBacktest.run(config, dataset, workload.strategy_state(), context_factory)
        return ReplayBacktest.run(
            config, dataset, workload.strategy_state(), context_factory, pause_when=pause_when
        )

    whole = replay(None)
    assert isinstance(whole, ReplayResult) and whole.records_replayed == 1
    expected = workload.outcome(whole)

    paused = replay(lambda progress: True)
    assert isinstance(paused, PausedReplay) and paused.records_replayed == 0
    restored = restore(from_primitives(_payload(paused)), workload.objects(), dataset)
    assert workload.outcome(_finish(restored)) == expected
    # After its one record there is no boundary to pause at.
    after = replay(lambda progress: progress.records_replayed == 1)
    assert isinstance(after, ReplayResult) and workload.outcome(after) == expected


def test_an_empty_dataset_is_refused_before_a_replay_exists() -> None:
    with pytest.raises(DatasetValidationError, match="Empty datasets"):
        MarketDataset(workload.DATASET_ID, ())
    with pytest.raises(ReplayValidationError, match="Empty datasets"):
        ReplayEngine.initialize(ReplayBacktest.initialize(workload.dataset()).session, (), 0.0)


def test_pause_when_is_asked_at_every_boundary_before_a_record_and_never_after_the_last() -> None:
    asked: list[ReplayProgress] = []

    def never(progress: ReplayProgress) -> bool:
        asked.append(progress)
        return False

    result = workload.run(never)
    records = workload.dataset().records

    assert isinstance(result, ReplayResult)
    assert [progress.records_replayed for progress in asked] == list(range(workload.TOTAL))
    assert [progress.next_timestamp for progress in asked] == [r.timestamp for r in records]
    assert {progress.records_total for progress in asked} == {workload.TOTAL}
    assert min(progress.records_remaining for progress in asked) == 1
    assert workload.outcome(result) == REFERENCE


def test_a_resume_moves_before_it_is_asked_again() -> None:
    """The boundary a replay resumes from is not asked about: it is already paused there."""

    paused = _paused(5)
    asked: list[int] = []

    def always(progress: ReplayProgress) -> bool:
        asked.append(progress.records_replayed)
        return True

    again = ReplayBacktest.resume(paused, context_factory, pause_when=always)

    assert isinstance(again, PausedReplay)
    assert asked == [6] and again.records_replayed == 6


def test_pausing_at_every_boundary_in_one_chain() -> None:
    """The most a replay can pause: once per record, through a payload each time."""

    state: ReplayResult | PausedReplay = workload.run(lambda progress: True)
    pauses = 0
    while isinstance(state, PausedReplay):
        assert state.records_replayed == pauses
        pauses += 1
        state = ReplayBacktest.resume(
            workload.round_trip(state), context_factory, pause_when=lambda progress: True
        )

    assert pauses == workload.TOTAL
    assert workload.outcome(state) == REFERENCE


@pytest.mark.parametrize("schedule", range(24))
def test_any_pause_schedule_resumes_to_the_uninterrupted_replay(schedule: int) -> None:
    """A generated schedule: which boundaries pause, and whether each goes through a payload.

    The schedules come from a seeded generator, so each case is the same on
    every run; across them every boundary is paused at, alone and in company.
    """

    generator = random.Random(9_001 + schedule)
    boundaries = sorted(generator.sample(range(workload.TOTAL), generator.randint(1, 6)))
    state: ReplayResult | PausedReplay = workload.run(workload.pause_at(*boundaries))
    seen = []
    while isinstance(state, PausedReplay):
        seen.append(state.records_replayed)
        if generator.random() < 0.7:
            state = workload.round_trip(state)
        state = ReplayBacktest.resume(
            state, context_factory, pause_when=workload.pause_at(*boundaries)
        )

    assert seen == boundaries
    assert workload.outcome(state) == REFERENCE


def test_an_unseeded_replay_resumes_to_the_same_figures() -> None:
    """Without a seed every identifier is a ``uuid4``; every quantity still agrees."""

    def figures(result: ReplayResult) -> str:
        run = result.backtest
        return serialize(
            {
                "fills": [(f.quantity, f.price, f.commission, f.filled_at) for f in run.fills],
                "cash": run.valuation.cash,
                "equity": run.valuation.equity,
                "realized": run.valuation.realized_pnl,
                "strategy": capture_run(run.run).pipeline.strategy[0].state,
                "steps": [(s.index, s.event_id, s.equity) for s in run.steps],
            }
        )

    paused = _paused(9, seed=None)
    assert paused.cursor_position == IdStreamPosition()
    resumed = _finish(workload.round_trip(paused))

    assert figures(resumed) == figures(workload.uninterrupted(seed=None))


def test_pause_when_draws_from_neither_stream() -> None:
    """The predicate is caller code; an identifier it mints must not move the run's."""

    def minting(progress: ReplayProgress) -> bool:
        new_id()
        new_id()
        return progress.records_replayed == 11

    paused = workload.run(minting)
    assert isinstance(paused, PausedReplay)
    resumed = ReplayBacktest.resume(
        workload.round_trip(paused), context_factory, pause_when=minting
    )

    assert isinstance(resumed, ReplayResult)
    assert workload.outcome(resumed) == REFERENCE


def test_an_exception_in_pause_when_propagates_and_nothing_pauses() -> None:
    def broken(progress: ReplayProgress) -> bool:
        if progress.records_replayed == 3:
            raise RuntimeError("the host's flag could not be read")
        return False

    with pytest.raises(RuntimeError, match="flag could not be read"):
        workload.run(broken)


def test_the_cursor_log_records_each_pause_and_nothing_else() -> None:
    first = _paused(4)
    second = ReplayBacktest.resume(
        workload.round_trip(first), context_factory, pause_when=workload.pause_at(9)
    )
    assert isinstance(second, PausedReplay)
    records = workload.dataset().records

    kinds = [type(event) for event in second.cursor.system_events]
    assert kinds == [
        ReplayStarted,
        *[ReplayAdvanced] * 4,
        ReplayPaused,
        ReplayResumed,
        *[ReplayAdvanced] * 5,
        ReplayPaused,
    ]
    advanced = [e for e in second.cursor.system_events if isinstance(e, ReplayAdvanced)]
    assert [event.new_timestamp for event in advanced] == [r.timestamp for r in records[:9]]
    ids = [event.event_id for event in second.cursor.system_events]
    assert len(set(ids)) == len(ids), "a resumed cursor mints no identifier twice"
    assert second.cursor_position == IdStreamPosition(
        workload.SEED + REPLAY_CURSOR_SEED_OFFSET, len(ids)
    )


def test_no_identifier_is_minted_twice_across_a_chain() -> None:
    state: ReplayResult | PausedReplay = workload.run(workload.pause_at(3, 8, 15))
    while isinstance(state, PausedReplay):
        state = ReplayBacktest.resume(
            workload.round_trip(state), context_factory, pause_when=workload.pause_at(8, 15)
        )
    run = state.backtest
    minted = [str(order.order_id.value) for order in run.orders] + [
        str(f.fill_id) for f in run.fills
    ]

    assert len(set(minted)) == len(minted)


def test_capture_is_pure_and_repeatable() -> None:
    paused = _paused(10)

    with id_scope(5):
        before = current_id_position()
        first, second = capture(paused), capture(paused)
        assert current_id_position() == before, "capture draws no identifier"
    assert first == second and serialize(first) == serialize(second)
    # v4.1.1 moved it to 2: the schedule's digest (ledger FUT-003).
    assert first.schema_version == REPLAY_SNAPSHOT_SCHEMA == 2


def test_restoring_one_payload_twice_gives_two_equal_continuations() -> None:
    payload = _payload(_paused(13))

    first = _finish(_restore_payload(payload))
    second = _finish(_restore_payload(payload))

    assert workload.outcome(first) == workload.outcome(second) == REFERENCE


def test_skipped_records_and_trimmed_histories_count_toward_the_position() -> None:
    """A record the run declines is still a record the cursor took (ledger ACC-007, PRF-004)."""

    inputs = []
    for index in range(10):
        price = Decimal("0") if index == 4 else Decimal(100 + index % 3)
        inputs.append(sized_quote(workload.FIRST, 10.0 + index, price, Decimal("3")))
    dataset = MarketDataset.of("SKIPPED", inputs)
    base = workload.config()
    config = dataclasses.replace(
        base,
        pipeline=dataclasses.replace(base.pipeline, retention=RetentionPolicy(steps=3)),
    )

    def replay(boundary: int | None) -> ReplayResult | PausedReplay:
        if boundary is None:
            return ReplayBacktest.run(config, dataset, workload.strategy_state(), context_factory)
        return ReplayBacktest.run(
            config,
            dataset,
            workload.strategy_state(),
            context_factory,
            pause_when=workload.pause_at(boundary),
        )

    whole = replay(None)
    assert isinstance(whole, ReplayResult)
    assert len(whole.backtest.run.skipped) == 1 and whole.backtest.run.steps.dropped > 0
    for boundary in (3, 5, 8):
        paused = replay(boundary)
        assert isinstance(paused, PausedReplay)
        restored = restore(from_primitives(_payload(paused)), workload.objects(config), dataset)
        assert workload.outcome(_finish(restored)) == workload.outcome(whole)


def test_a_declared_retention_bounds_the_cursor_log_and_resumes_exactly() -> None:
    """The cursor's lifecycle log is an audit trail, and ``audit_events`` bounds it (v4.1).

    Until v4.1 a replay kept one ``ReplayAdvanced`` per record whatever the run
    declared -- about 175 bytes a record in every checkpoint, and the one
    history of a long replay no retention reached.
    """

    base = workload.config()
    config = dataclasses.replace(
        base, pipeline=dataclasses.replace(base.pipeline, retention=RetentionPolicy(audit_events=4))
    )

    def replay(boundary: int | None) -> ReplayResult | PausedReplay:
        if boundary is None:
            return ReplayBacktest.run(
                config, workload.dataset(), workload.strategy_state(), context_factory
            )
        return ReplayBacktest.run(
            config,
            workload.dataset(),
            workload.strategy_state(),
            context_factory,
            pause_when=workload.pause_at(boundary),
        )

    whole = replay(None)
    assert isinstance(whole, ReplayResult)
    for boundary in (3, 12, 23):
        paused = replay(boundary)
        assert isinstance(paused, PausedReplay)
        log = paused.cursor.system_events
        assert len(log) <= 4 + 1 + 1, "the bound, its slack, and the pause"
        assert log.dropped == boundary + 2 - len(log), "every event is kept or counted"
        snapshot = capture(paused)
        assert snapshot.events_dropped == log.dropped
        restored = restore(
            from_primitives(_payload(paused)), workload.objects(config), workload.dataset()
        )
        assert restored.cursor.system_events.dropped == log.dropped
        assert workload.outcome(_finish(restored)) == workload.outcome(whole)


@pytest.mark.parametrize(("dropped", "message"), [(-1, "dropped -1 events"), (5, "and 5 dropped")])
def test_a_dropped_count_that_does_not_fit_the_stream_is_refused(
    dropped: int, message: str
) -> None:
    payload = _payload(_paused(9))
    payload["events_dropped"] = dropped

    with pytest.raises(StateDecodeError, match=message):
        _restore_payload(payload)


def test_the_host_api_pauses_and_resumes() -> None:
    """``alphalab.api.replay`` takes ``pause_when``; ``resume_replay`` continues."""

    assert "resume_replay" in api.__all__
    paused = ReplayBacktest.run(
        workload.config(),
        workload.dataset(),
        workload.strategy_state(),
        context_factory,
        pause_when=workload.pause_at(6),
    )
    assert isinstance(paused, PausedReplay)
    resumed = api.resume_replay(workload.round_trip(paused), context_factory)
    assert workload.outcome(resumed) == REFERENCE


# --------------------------------------------------------------------------- #
# B. Durability: another process, an abrupt end, a failed write
# --------------------------------------------------------------------------- #


def _worker(*arguments: str, hash_seed: str) -> subprocess.CompletedProcess[str]:
    """Run the workload's worker in a fresh interpreter, importing this checkout."""

    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = hash_seed
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(ROOT), environment.get("PYTHONPATH")])
    )
    return subprocess.run(
        [sys.executable, "-W", "error", "-m", "tests.integration.replay_workload", *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


def test_a_fresh_process_resumes_what_another_paused(tmp_path: Path) -> None:
    store, resumed, whole = tmp_path / "store", tmp_path / "resumed", tmp_path / "whole"

    paused = _worker("pause", "11", str(store), hash_seed="1")
    assert paused.returncode == 0, paused.stderr
    continued = _worker("resume", str(store), str(resumed), hash_seed="2")
    assert continued.returncode == 0, continued.stderr
    uninterrupted = _worker("uninterrupted", str(whole), hash_seed="3")
    assert uninterrupted.returncode == 0, uninterrupted.stderr

    assert resumed.read_text(encoding="utf-8") == whole.read_text(encoding="utf-8") == REFERENCE


def test_a_process_that_dies_mid_replay_resumes_from_its_last_checkpoint(tmp_path: Path) -> None:
    store, resumed = tmp_path / "store", tmp_path / "resumed"

    crashed = _worker("checkpoint-and-crash", "5", "15", str(store), hash_seed="4")
    assert crashed.returncode == 86, (crashed.returncode, crashed.stderr)
    held = FileRunStateStore(store)
    latest = held.latest(workload.RUN_ID)
    assert latest is not None and latest.sequence == 3
    assert _restore_payload(deserialize(held.get(latest))).records_replayed == 15

    continued = _worker("resume", str(store), str(resumed), hash_seed="5")
    assert continued.returncode == 0, continued.stderr
    assert resumed.read_text(encoding="utf-8") == REFERENCE


def test_a_failed_checkpoint_write_leaves_the_previous_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FileRunStateStore(tmp_path)
    first = _paused(6)
    store.put(workload.RUN_ID, 1, serialize(capture(first)))
    second = ReplayBacktest.resume(first, context_factory, pause_when=workload.pause_at(12))
    assert isinstance(second, PausedReplay)

    def failing_replace(source: object, destination: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(StorageError, match="disk full"):
        store.put(workload.RUN_ID, 2, serialize(capture(second)))
    monkeypatch.undo()

    leftovers = [path.name for path in tmp_path.rglob("*") if path.suffix == ".tmp"]
    assert leftovers == [], "a failed write leaves no partial file"
    latest = store.latest(workload.RUN_ID)
    assert latest is not None and latest.sequence == 1
    restored = _restore_payload(deserialize(store.get(latest)))
    assert restored.records_replayed == 6
    assert workload.outcome(_finish(restored)) == REFERENCE


def test_an_unserializable_strategy_state_fails_capture_and_stores_nothing(
    tmp_path: Path,
) -> None:
    class Unwritable(workload.RememberingStrategy):
        def capture_state(self) -> dict[str, Any]:
            return {"handle": object()}

    instance = Unwritable()
    result = ReplayBacktest.run(
        workload.config(),
        workload.dataset(),
        workload.strategy_state(instance),
        context_factory,
        pause_when=workload.pause_at(4),
    )
    assert isinstance(result, PausedReplay)
    store = FileRunStateStore(tmp_path)

    with pytest.raises(SerializationError, match="No deterministic JSON representation"):
        store.put(workload.RUN_ID, 1, serialize(capture(result)))
    assert store.latest(workload.RUN_ID) is None


@pytest.mark.parametrize("damage", ["truncate", "flip"])
def test_a_damaged_checkpoint_is_refused_by_the_store(tmp_path: Path, damage: str) -> None:
    store = FileRunStateStore(tmp_path)
    ref = store.put(workload.RUN_ID, 1, serialize(capture(_paused(8))))
    (path,) = list(tmp_path.rglob("*.runstate"))
    contents = path.read_text(encoding="utf-8")
    if damage == "truncate":
        path.write_text(contents[: len(contents) // 2], encoding="utf-8")
    else:
        middle = len(contents) // 2
        flipped = "1" if contents[middle] != "1" else "2"
        path.write_text(contents[:middle] + flipped + contents[middle + 1 :], encoding="utf-8")

    with pytest.raises(StorageError, match="truncated or altered"):
        store.get(ref)


def test_a_truncated_payload_is_refused_as_json() -> None:
    text = serialize(capture(_paused(8)))

    with pytest.raises(SerializationError, match="Corrupt JSON"):
        deserialize(text[: len(text) - 40])


# --------------------------------------------------------------------------- #
# C. Refusals: the payload, the dataset, the live objects
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("version", "message"),
    [
        (None, "missing 'schema_version'"),
        (0, "declares schema version 0"),
        (3, "declares schema version 3"),
        ("1", "not an integer"),
        (True, "not an integer"),
    ],
)
def test_a_payload_of_another_schema_is_refused(version: object, message: str) -> None:
    payload = _payload(_paused(2))
    if version is None:
        del payload["schema_version"]
    else:
        payload["schema_version"] = version

    with pytest.raises(StateDecodeError, match=message):
        from_primitives(payload)


def test_a_payload_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(StateDecodeError, match="replay snapshot"):
        from_primitives([])  # type: ignore[arg-type]


def test_a_nested_run_of_an_unknown_schema_is_refused_by_the_run_decoder() -> None:
    payload = _payload(_paused(2))
    payload["run"]["schema_version"] = RUN_SNAPSHOT_SCHEMA + 1

    with pytest.raises(StateDecodeError, match="run snapshot declares schema version"):
        from_primitives(payload)


@pytest.mark.parametrize("field", ["status", "events", "cursor_position", "dataset_digest", "run"])
def test_a_missing_field_is_named(field: str) -> None:
    payload = _payload(_paused(2))
    del payload[field]

    with pytest.raises(StateDecodeError, match=field):
        from_primitives(payload)


def _tampered(edit: Callable[[dict[str, Any]], None], boundary: int = 9) -> dict[str, Any]:
    payload = _payload(_paused(boundary))
    edit(payload)
    return payload


def _events_without(kind: str) -> Callable[[dict[str, Any]], None]:
    """Drop the last ``kind`` event and the draw that minted it: a log consistent with its
    stream, and wrong about the replay."""

    def edit(payload: dict[str, Any]) -> None:
        events = payload["events"]
        index = max(i for i, entry in enumerate(events) if entry["event_type"] == kind)
        del events[index]
        payload["cursor_position"]["draws"] -= 1

    return edit


def _set(path: str, value: object) -> Callable[[dict[str, Any]], None]:
    def edit(payload: dict[str, Any]) -> None:
        *parents, last = path.split(".")
        target: Any = payload
        for key in parents:
            target = target[int(key)] if key.isdigit() else target[key]
        target[int(last) if last.isdigit() else last] = value

    return edit


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (_set("status", "ReplayStatus.RUNNING"), "is RUNNING, not PAUSED"),
        (_set("status", "ReplayStatus.COMPLETED"), "is COMPLETED, not PAUSED"),
        (_set("status", "ReplayStatus.WAITING"), "status"),
        (_set("current_index", 8), "taken 8 records and its run processed or skipped 9"),
        (_set("current_index", 10), "taken 10 records and its run processed or skipped 9"),
        (_set("run.processed", 10), "processed or skipped 10"),
        (_set("current_index", -1), "taken -1 records"),
        (_set("last_event_id", "FUT001-REPLAY-00000007"), "paused after 'FUT001-REPLAY-00000007'"),
        (_set("next_event_id", "FUT001-REPLAY-00000011"), "'FUT001-REPLAY-00000011' next"),
        (_set("cursor_position.seed", workload.SEED), "seeded"),
        (_set("cursor_position.draws", 3), "drawn 3 identifiers"),
        (_set("events.3.event_type", "ReplayResumed"), "missing .session_id."),
        (_events_without("ReplayPaused"), "not ReplayPaused"),
        (_events_without("ReplayAdvanced"), "advanced 8 times"),
        (_events_without("ReplayStarted"), "does not begin with ReplayStarted"),
        (_set("events.0.event_type", "ReplayCompleted"), "cannot hold"),
        (_set("events.0.event.total_events", 23), "started on 23 records"),
        (_set("current_timestamp", 99.0), "clock reads 99.0"),
        (_set("session.session_id", "OTHER"), "its run read 'FUT001-REPLAY'"),
        (_set("run.source_id", "OTHER"), "its run read 'OTHER'"),
        (_set("run.mode", "ExecutionMode.BACKTEST"), "BACKTEST run, not a REPLAY run"),
        (_set("records_total", 25), "started on 24 records"),
        (_set("dataset_digest", "0" * 64), "are not the ones it read"),
        (_set("session.end_time", 30.0), "spanned 10.0 to 30.0"),
    ],
)
def test_an_inconsistent_payload_is_refused_by_name(
    edit: Callable[[dict[str, Any]], None], message: str
) -> None:
    payload = _tampered(edit)

    with pytest.raises(StateDecodeError, match=message):
        _restore_payload(payload)


def _dataset_with(records: list[Any], dataset_id: str = workload.DATASET_ID) -> MarketDataset:
    return MarketDataset(dataset_id, tuple(records))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("another id", "dataset 'OTHER' was supplied"),
        ("a record missing before the boundary", "held 24 records when it paused and holds 23"),
        ("a record missing after the boundary", "held 24 records when it paused and holds 23"),
        ("a record added", "held 24 records when it paused and holds 25"),
        ("a price changed", "are not the ones it read"),
        ("one instant's records swapped at the boundary", "resuming would duplicate or skip"),
        ("one instant's records swapped elsewhere", "are not the ones it read"),
    ],
)
def test_another_dataset_is_refused(change: str, message: str) -> None:
    payload = _payload(_paused(9))
    records = list(workload.dataset().records)
    if change == "another id":
        dataset = workload.dataset(dataset_id="OTHER")
    elif change == "a record missing before the boundary":
        dataset = _dataset_with(records[:3] + records[4:])
    elif change == "a record missing after the boundary":
        dataset = _dataset_with(records[:15] + records[16:])
    elif change == "a record added":
        extra = sized_quote(workload.FIRST, 30.0, Decimal("100"), Decimal("3"))
        dataset = _dataset_with(
            [
                *records,
                dataclasses.replace(records[-1], event_id="EXTRA", timestamp=30.0, payload=extra),
            ]
        )
    elif change == "a price changed":
        quote = records[17].payload
        assert isinstance(quote, Quote)
        changed = dataclasses.replace(quote, bid=Decimal("999"), ask=Decimal("999"))
        records[17] = dataclasses.replace(records[17], payload=changed)
        dataset = _dataset_with(records)
    elif change == "one instant's records swapped at the boundary":
        records[8], records[9] = records[9], records[8]
        dataset = _dataset_with(records)
    else:
        records[20], records[21] = records[21], records[20]
        dataset = _dataset_with(records)

    with pytest.raises(StateDecodeError, match=message):
        _restore_payload(payload, dataset)


def test_a_duplicated_record_cannot_even_form_a_dataset() -> None:
    records = list(workload.dataset().records)

    with pytest.raises(DatasetValidationError, match="Duplicate record id"):
        _dataset_with([*records[:5], records[4], *records[5:]])


def test_another_commission_is_refused() -> None:
    """The run decoder's PER-008 check reaches a replay: a simulator configured otherwise."""

    payload = _payload(_paused(9))
    other = workload.config(commission=Decimal("5"))

    with pytest.raises(StateDecodeError, match="configured"):
        restore(from_primitives(payload), workload.objects(other), workload.dataset())


def test_another_fill_policy_is_refused() -> None:
    payload = _payload(_paused(9))
    other = workload.config(fill_policy=LiquidityCappedFill(participation_rate=Decimal("0.9")))

    with pytest.raises(StateDecodeError, match="fill policy"):
        restore(from_primitives(payload), workload.objects(other), workload.dataset())


def test_a_failed_restore_changes_nothing_and_can_be_retried() -> None:
    payload = _payload(_paused(9))
    snapshot = from_primitives(payload)
    before = serialize(snapshot)

    with pytest.raises(StateDecodeError):
        restore(
            snapshot, workload.objects(workload.config(commission=Decimal("5"))), workload.dataset()
        )
    with pytest.raises(StateDecodeError):
        restore(snapshot, workload.objects(), workload.dataset(dataset_id="OTHER"))

    assert serialize(snapshot) == before
    restored = restore(snapshot, workload.objects(), workload.dataset())
    assert workload.outcome(_finish(restored)) == REFERENCE


def _with(paused: PausedReplay, **changes: Any) -> None:
    PausedReplay(
        run=changes.get("run", paused.run),
        cursor=changes.get("cursor", paused.cursor),
        cursor_position=changes.get("cursor_position", paused.cursor_position),
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("running", "PAUSED, not RUNNING"),
        ("exhausted", "has a next record"),
        ("backtest", "REPLAY run, not BACKTEST"),
        ("another source", "one dataset's"),
        ("behind", "omit 1"),
    ],
)
def test_a_paused_replay_that_is_not_one_cannot_be_built(change: str, message: str) -> None:
    paused = _paused(9)
    cursor: ReplayState = paused.cursor
    if change == "running":
        changes: dict[str, Any] = {
            "cursor": dataclasses.replace(cursor, status=ReplayStatus.RUNNING)
        }
    elif change == "exhausted":
        changes = {"cursor": dataclasses.replace(cursor, current_index=len(cursor.events))}
    elif change == "backtest":
        run_config = dataclasses.replace(paused.run.config, mode=ExecutionMode.BACKTEST)
        changes = {"run": dataclasses.replace(paused.run, config=run_config)}
    elif change == "another source":
        changes = {"run": dataclasses.replace(paused.run, source_id="OTHER")}
    else:
        changes = {"cursor": dataclasses.replace(cursor, current_index=10)}

    with pytest.raises(InvalidReplayStateError, match=message):
        _with(paused, **changes)


# --------------------------------------------------------------------------- #
# D. The format: nested, frozen, covering every field
# --------------------------------------------------------------------------- #


def test_the_run_is_nested_exactly_as_the_run_snapshot_writes_it() -> None:
    paused = _paused(9)

    assert serialize(_payload(paused)["run"]) == serialize(capture_run(paused.run))
    assert _payload(paused)["run"]["schema_version"] == RUN_SNAPSHOT_SCHEMA == 5


def test_a_nested_older_run_payload_is_upgraded_by_the_run_history() -> None:
    """The nesting keeps the run's migrations: a run payload v3.13 wrote reads as version 5."""

    older = json.loads(
        (ROOT / "tests" / "fixtures" / "snapshots" / "v3.13.0" / "run_backtest.json").read_text(
            encoding="utf-8"
        )
    )
    assert older["schema_version"] == 4
    payload = _payload(_paused(9))
    payload["run"] = older

    snapshot = from_primitives(payload)

    assert snapshot.run.schema_version == RUN_SNAPSHOT_SCHEMA
    assert snapshot.run.fill_policy_description is None, "v3.13 did not record it (PER-008)"
    with pytest.raises(StateDecodeError, match="BACKTEST run, not a REPLAY run"):
        restore(snapshot, workload.objects(), workload.dataset())


def test_the_frozen_v4_1_0_payload_reads_and_resumes() -> None:
    """The first replay payload any release wrote, frozen so every later build reads it.

    Written by ``docs/audit/scripts/generate_v4_1_0_snapshot_fixtures.py`` from
    this workload, paused after eleven records. A later release that moves the
    replay schema reads it through ``REPLAY_SCHEMA_HISTORY``.
    """

    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1

    restored = _restore_payload(payload)

    assert restored.records_replayed == 11
    assert serialize(capture(restored)) == serialize(from_primitives(payload))
    assert workload.outcome(_finish(restored)) == REFERENCE


def test_the_frozen_payload_is_what_this_build_writes() -> None:
    """Byte for byte, but for the one step schema 2 declares (v4.1.1, ledgers FUT-003,
    FUT-009 and FUT-011): the version, the digest of a schedule, the algorithm book of
    an execution plan and the queue book of a queue model, none of which a v4.1.0
    replay could have had."""

    frozen = json.loads(FIXTURE.read_text(encoding="utf-8"))
    upgraded = {
        **frozen,
        "schema_version": 2,
        "schedule_digest": None,
        "algorithms": None,
        "queue": None,
    }
    assert upgraded == _payload(_paused(11))


def test_the_snapshot_covers_every_cursor_field() -> None:
    """Silent state loss, guarded as ``test_snapshot_field_coverage.py`` guards the rest."""

    carried = {field.name for field in dataclasses.fields(ReplaySnapshot)} | {
        "events",  # the dataset's records: supplied back, held to records_total and the digest
        "system_events",  # -> events, tagged
    }
    assert {field.name for field in dataclasses.fields(ReplayState)} - carried == set()
    assert {field.name for field in dataclasses.fields(PausedReplay)} == {
        "run",
        "cursor",
        "cursor_position",
        # v4.1.1: carried as its digest (schema 2), the schedule itself supplied
        # back on restore and held to it, as the dataset is (ledger FUT-003;
        # test_heterogeneous_inputs_on_every_driver.py
        # ::test_a_payload_resumed_with_another_schedule_or_none_is_refused).
        "schedule",
        # v4.1.1: carried whole (schema 2), the plan supplied back and held to it
        # (ledger FUT-009; test_algorithms_on_the_historical_path.py
        # ::test_a_replay_paused_with_children_working_resumes_from_its_payload).
        "algorithms",
        # v4.1.1: carried whole (schema 2), the queue model supplied back and held
        # to it (ledger FUT-011; test_queue_model.py
        # ::test_a_replay_paused_anywhere_resumes_to_the_same_queue_and_result).
        "queue",
    }


def test_the_dataset_digest_reads_every_record_in_order() -> None:
    dataset = workload.dataset()
    records = list(dataset.records)
    records[0], records[1] = records[1], records[0]

    assert dataset_digest(dataset) == dataset_digest(workload.dataset())
    assert dataset_digest(dataset) != dataset_digest(_dataset_with(records))
    assert len(dataset_digest(dataset)) == 64
