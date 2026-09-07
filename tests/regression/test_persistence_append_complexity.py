"""Regression guard for the O(N^2) persistence append fixed in v2.9.

``MemoryStorage.append_event`` rebuilt three containers on every call: the
stored-event ``tuple``, the ``frozenset`` of event ids, and the system-event
``tuple`` on :class:`~alphalab.persistence.state.PersistenceState`.
``save_snapshot`` rebuilt the snapshot ``dict`` on every save. Appending N
events therefore copied O(N^2) entries -- measured on v2.8.0, 32,000 appends
took 14.0s and each doubling of the workload cost ~4.5x the time, so
``benchmarks/benchmark_persistence.py``'s 100k workload did not finish.

This is the same defect :class:`~alphalab.common.append_log.AppendOnlyLog`
removed from the engine histories in v2.1 and
:class:`~alphalab.common.persistent_map.PersistentMap` /
:class:`~alphalab.common.persistent_map.PersistentSet` removed from the OMS
order book in v2.2. The persistence package was never migrated, which did not
matter while nothing persisted through it and stops being optional now that
durable run state will.

The structural tests below are deterministic: they assert the store is not
copied on append, which is the property the fix rests on, *and* that it still
behaves as a value. The timing test is a coarse backstop -- it exists to catch a
return to quadratic behaviour, not to police constant factors, so its threshold
is wider than the release acceptance criterion. The measured result on the
machine this was developed on was 2.01x-2.03x per doubling through N=32,000,
against a release criterion of <= 2.2x.
"""

import gc
import time
from dataclasses import dataclass
from decimal import Decimal

import pytest

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap, PersistentSet
from alphalab.persistence import (
    MemoryStorage,
    PersistenceAdapter,
    PersistenceEngine,
    PersistenceState,
    StorageError,
    StoredEvent,
    deserialize,
    event_count,
    latest_snapshot,
    serialize,
    snapshot_count,
)

# Workload sizes for the doubling sweep. Linear growth predicts ~2x per
# doubling and quadratic ~4x; 3.0x sits between them, far enough from 2.0 to
# survive a loaded machine and far enough from 4.0 to fail a reintroduced
# quadratic.
SIZES = (2_000, 4_000, 8_000, 16_000)
MAX_GROWTH_PER_DOUBLING = 3.0
# 16,000 appends took ~0.12s after the fix and ~3.1s before, on the machine this
# was developed on. A 5s ceiling survives a slow CI runner while still failing
# the pre-fix implementation.
LARGEST_WORKLOAD_BUDGET_SECONDS = 5.0


@dataclass(frozen=True)
class DummyDomainEvent:
    trade_id: str
    price: Decimal


def _event(index: int) -> StoredEvent:
    return PersistenceAdapter.to_stored_event(
        f"E-{index}", float(index), DummyDomainEvent(f"T{index}", Decimal("1.50"))
    )


def _append(count: int) -> PersistenceState:
    """Append ``count`` events through the real storage path."""

    state = PersistenceEngine.initialize("MEM-COMPLEXITY")
    store = MemoryStorage()
    for index in range(count):
        state, _ = store.append_event(state, _event(index), float(index))
    return state


def _time_appends(count: int) -> float:
    """Time a run of appends with the cyclic collector paused."""

    events = [_event(index) for index in range(count)]
    state = PersistenceEngine.initialize("MEM-COMPLEXITY")
    store = MemoryStorage()
    gc.disable()
    try:
        start = time.perf_counter()
        for index, event in enumerate(events):
            state, _ = store.append_event(state, event, float(index))
        return time.perf_counter() - start
    finally:
        gc.enable()


# ---------------------------------------------------------------------------
# Structural guarantees (deterministic)
# ---------------------------------------------------------------------------


def test_every_persistence_container_is_persistent() -> None:
    state = PersistenceEngine.initialize("MEM-TYPES")

    assert isinstance(state.store.events, AppendOnlyLog)
    assert isinstance(state.store.event_ids, PersistentSet)
    assert isinstance(state.store.snapshots, PersistentMap)
    assert isinstance(state.events, AppendOnlyLog)


def test_appending_events_does_not_rebuild_the_event_log() -> None:
    state = PersistenceEngine.initialize("MEM-SHARE")
    store = MemoryStorage()
    buffers = set()
    for index in range(200):
        state, _ = store.append_event(state, _event(index), float(index))
        buffers.add(id(state.store.events._buffer))

    assert len(buffers) == 1
    assert len(state.store.events) == 200


def test_appending_events_does_not_rebuild_the_id_set() -> None:
    state = PersistenceEngine.initialize("MEM-SHARE-IDS")
    store = MemoryStorage()
    stores = set()
    for index in range(200):
        state, _ = store.append_event(state, _event(index), float(index))
        stores.add(id(state.store.event_ids._members._store))

    assert len(stores) == 1
    assert len(state.store.event_ids) == 200


def test_appending_events_does_not_rebuild_the_system_event_log() -> None:
    state = PersistenceEngine.initialize("MEM-SHARE-SYS")
    store = MemoryStorage()
    buffers = set()
    for index in range(200):
        state, _ = store.append_event(state, _event(index), float(index))
        buffers.add(id(state.events._buffer))

    assert len(buffers) == 1
    assert len(state.events) == 200


def test_saving_snapshots_does_not_rebuild_the_snapshot_index() -> None:
    state = PersistenceEngine.initialize("MEM-SHARE-SNAPS")
    store = MemoryStorage()
    stores = set()
    for index in range(200):
        snapshot = PersistenceAdapter.to_snapshot(
            f"S-{index}", "OMS", float(index), DummyDomainEvent("T", Decimal("1.00"))
        )
        state, _ = store.save_snapshot(state, snapshot, float(index))
        stores.add(id(state.store.snapshots._store))

    assert len(stores) == 1
    assert snapshot_count(state) == 200


# ---------------------------------------------------------------------------
# The containers still behave as values
# ---------------------------------------------------------------------------


def test_events_retain_insertion_order() -> None:
    state = _append(50)

    assert [event.event_id for event in state.store.events] == [f"E-{i}" for i in range(50)]
    assert list(state.store.event_ids) == [f"E-{i}" for i in range(50)]


def test_the_chronological_check_still_reads_the_last_event() -> None:
    state = _append(10)
    store = MemoryStorage()

    assert state.store.events[-1].event_id == "E-9"
    with pytest.raises(StorageError, match="Chronological ordering violation"):
        store.append_event(
            state,
            PersistenceAdapter.to_stored_event(
                "E-late", 1.0, DummyDomainEvent("T", Decimal("1.00"))
            ),
            100.0,
        )


def test_duplicate_detection_still_reads_the_id_set() -> None:
    state = _append(10)
    store = MemoryStorage()

    assert "E-3" in state.store.event_ids
    with pytest.raises(StorageError, match="Duplicate event ID"):
        store.append_event(state, _event(3), 100.0)


def test_an_earlier_state_does_not_see_later_events() -> None:
    first = _append(1)
    store = MemoryStorage()
    second, _ = store.append_event(first, _event(1), 1.0)

    assert event_count(first) == 1
    assert event_count(second) == 2
    assert "E-1" not in first.store.event_ids
    assert "E-1" in second.store.event_ids


def test_the_event_log_still_compares_equal_to_a_tuple() -> None:
    state = _append(3)

    assert state.store.events == tuple(state.store.events)
    assert state.events == tuple(state.events)


def test_views_and_snapshot_lookup_are_unchanged() -> None:
    state = PersistenceEngine.initialize("MEM-VIEWS")
    store = MemoryStorage()
    older = PersistenceAdapter.to_snapshot("S1", "OMS", 1000.0, DummyDomainEvent("T", Decimal("1")))
    newer = PersistenceAdapter.to_snapshot("S2", "OMS", 1005.0, DummyDomainEvent("T", Decimal("1")))
    state, _ = store.save_snapshot(state, older, 1000.0)
    state, _ = store.save_snapshot(state, newer, 1005.0)

    assert snapshot_count(state) == 2
    assert state.store.snapshots["S1"] == older
    assert latest_snapshot(state, "OMS") == newer
    assert latest_snapshot(state, "MISSING") is None


def test_the_state_now_serializes_deterministically() -> None:
    """``frozenset`` had no encoder branch, so this state could not be written down.

    ``PersistentSet`` declares its own projection and iterates in insertion
    order, so the whole state serializes -- and serializes identically twice.
    """

    state = _append(5)
    payload = serialize(state)

    assert payload == serialize(state)
    assert deserialize(payload)["store"]["event_ids"] == [f"E-{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# Timing backstop (coarse)
# ---------------------------------------------------------------------------


def test_append_cost_grows_linearly_with_the_workload() -> None:
    _time_appends(500)  # warm up

    timings = [min(_time_appends(size) for _ in range(2)) for size in SIZES]

    assert timings[-1] < LARGEST_WORKLOAD_BUDGET_SECONDS, (
        f"{SIZES[-1]} appends took {timings[-1]:.2f}s"
    )
    for size, previous, current in zip(SIZES[1:], timings[:-1], timings[1:], strict=True):
        growth = current / max(previous, 1e-6)
        assert growth < MAX_GROWTH_PER_DOUBLING, (
            f"doubling the workload to {size} cost {growth:.2f}x the time "
            f"({previous:.3f}s -> {current:.3f}s); persistence append looks quadratic again"
        )
