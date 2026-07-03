"""Comprehensive tests validating strict chronological storage, serialization, and immutability."""

from dataclasses import dataclass
from decimal import Decimal

import pytest

from alphalab.persistence import (
    MemoryStorage,
    PersistenceAdapter,
    PersistenceEngine,
    PersistenceState,
    PersistenceValidationError,
    SerializationError,
    StorageError,
    deserialize,
    event_count,
    latest_snapshot,
    serialize,
    snapshot_count,
    storage_statistics,
)


@dataclass(frozen=True)
class DummyDomainEvent:
    trade_id: str
    price: Decimal


@pytest.fixture
def default_state() -> PersistenceState:
    return PersistenceEngine.initialize("MEM-1")


def test_initialization(default_state: PersistenceState) -> None:
    assert event_count(default_state) == 0
    assert snapshot_count(default_state) == 0
    assert storage_statistics(default_state).bytes_stored == 0

    with pytest.raises(ValueError, match="empty"):
        PersistenceEngine.initialize("")


# --- SERIALIZATION TESTS ---


def test_serializer_primitive() -> None:
    payload = serialize({"key": "value", "list": [1, 2, 3]})
    assert payload == '{"key":"value","list":[1,2,3]}'
    obj = deserialize(payload)
    assert obj["key"] == "value"


def test_serializer_decimal() -> None:
    payload = serialize({"price": Decimal("150.50")})
    assert payload == '{"price":"150.50"}'
    obj = deserialize(payload)
    assert obj["price"] == "150.50"


def test_serializer_dataclass() -> None:
    event = DummyDomainEvent("T1", Decimal("100.00"))
    payload = serialize(event)
    assert payload == '{"price":"100.00","trade_id":"T1"}'


def test_serializer_corrupt_json() -> None:
    with pytest.raises(SerializationError, match="Corrupt JSON"):
        deserialize('{"broken":')


# --- ADAPTER TESTS ---


def test_adapter_to_event() -> None:
    domain = DummyDomainEvent("T1", Decimal("100.00"))
    stored = PersistenceAdapter.to_stored_event("E1", 1000.0, domain)

    assert stored.event_id == "E1"
    assert stored.event_type == "DummyDomainEvent"
    assert stored.timestamp == 1000.0
    assert "trade_id" in stored.payload


def test_adapter_to_snapshot() -> None:
    domain = DummyDomainEvent("T1", Decimal("100.00"))
    snap = PersistenceAdapter.to_snapshot("S1", "OMS", 1000.0, domain)

    assert snap.snapshot_id == "S1"
    assert snap.subsystem == "OMS"
    assert snap.timestamp == 1000.0
    assert "price" in snap.payload


# --- STORAGE & PROTOCOL TESTS ---


def test_save_snapshot(default_state: PersistenceState) -> None:
    store = MemoryStorage()
    domain = DummyDomainEvent("T1", Decimal("100.00"))
    snap = PersistenceAdapter.to_snapshot("S1", "OMS", 1000.0, domain)

    s1, evts = store.save_snapshot(default_state, snap, 1001.0)

    assert snapshot_count(s1) == 1
    assert storage_statistics(s1).total_snapshots_saved == 1
    assert len(evts) == 1
    assert type(evts[0]).__name__ == "SnapshotSaved"
    assert latest_snapshot(s1, "OMS") == snap


def test_save_duplicate_snapshot(default_state: PersistenceState) -> None:
    store = MemoryStorage()
    domain = DummyDomainEvent("T1", Decimal("100.00"))
    snap = PersistenceAdapter.to_snapshot("S1", "OMS", 1000.0, domain)

    s1, _ = store.save_snapshot(default_state, snap, 1001.0)

    with pytest.raises(StorageError, match="Duplicate snapshot ID"):
        store.save_snapshot(s1, snap, 1002.0)


def test_save_invalid_snapshot(default_state: PersistenceState) -> None:
    store = MemoryStorage()
    # Negative timestamp
    domain = DummyDomainEvent("T1", Decimal("100.00"))
    snap = PersistenceAdapter.to_snapshot("S1", "OMS", -10.0, domain)

    with pytest.raises(PersistenceValidationError, match="negative"):
        store.save_snapshot(default_state, snap, 1001.0)


def test_load_snapshot(default_state: PersistenceState) -> None:
    store = MemoryStorage()
    domain = DummyDomainEvent("T1", Decimal("100.00"))
    snap = PersistenceAdapter.to_snapshot("S1", "OMS", 1000.0, domain)

    s1, _ = store.save_snapshot(default_state, snap, 1001.0)
    _, loaded, evts = store.load_snapshot(s1, "S1", 1002.0)

    assert loaded == snap
    assert len(evts) == 1
    assert type(evts[0]).__name__ == "SnapshotLoaded"


def test_load_missing_snapshot(default_state: PersistenceState) -> None:
    store = MemoryStorage()
    with pytest.raises(StorageError, match="not found"):
        store.load_snapshot(default_state, "MISSING", 1000.0)


def test_append_event(default_state: PersistenceState) -> None:
    store = MemoryStorage()
    domain = DummyDomainEvent("T1", Decimal("100.00"))
    evt = PersistenceAdapter.to_stored_event("E1", 1000.0, domain)

    s1, sys_evts = store.append_event(default_state, evt, 1001.0)

    assert event_count(s1) == 1
    assert storage_statistics(s1).total_events_appended == 1
    assert len(sys_evts) == 1
    assert type(sys_evts[0]).__name__ == "EventAppended"


def test_append_duplicate_event(default_state: PersistenceState) -> None:
    store = MemoryStorage()
    domain = DummyDomainEvent("T1", Decimal("100.00"))
    evt = PersistenceAdapter.to_stored_event("E1", 1000.0, domain)

    s1, _ = store.append_event(default_state, evt, 1001.0)

    with pytest.raises(StorageError, match="Duplicate event ID"):
        store.append_event(s1, evt, 1002.0)


def test_append_chronological_violation(default_state: PersistenceState) -> None:
    store = MemoryStorage()
    domain = DummyDomainEvent("T1", Decimal("100.00"))

    evt1 = PersistenceAdapter.to_stored_event("E1", 1005.0, domain)
    evt2 = PersistenceAdapter.to_stored_event("E2", 1000.0, domain)  # Older

    s1, _ = store.append_event(default_state, evt1, 1006.0)

    with pytest.raises(StorageError, match="Chronological ordering violation"):
        store.append_event(s1, evt2, 1007.0)


def test_load_events(default_state: PersistenceState) -> None:
    store = MemoryStorage()
    domain = DummyDomainEvent("T1", Decimal("100.00"))

    evt1 = PersistenceAdapter.to_stored_event("E1", 1000.0, domain)
    evt2 = PersistenceAdapter.to_stored_event("E2", 1001.0, domain)

    s1, _ = store.append_event(default_state, evt1, 1000.0)
    s2, _ = store.append_event(s1, evt2, 1001.0)

    _, loaded, sys_evts = store.load_events(s2, 1002.0)

    assert len(loaded) == 2
    assert loaded[0] == evt1
    assert loaded[1] == evt2
    assert len(sys_evts) == 1
    assert type(sys_evts[0]).__name__ == "EventsLoaded"


def test_clear_storage(default_state: PersistenceState) -> None:
    store = MemoryStorage()
    domain = DummyDomainEvent("T1", Decimal("100.00"))
    evt = PersistenceAdapter.to_stored_event("E1", 1000.0, domain)
    s1, _ = store.append_event(default_state, evt, 1001.0)

    assert event_count(s1) == 1

    s2, evts = store.clear(s1, "Purge", 1002.0)

    assert event_count(s2) == 0
    assert snapshot_count(s2) == 0
    assert storage_statistics(s2).total_events_appended == 0
    assert len(evts) == 1
    assert type(evts[0]).__name__ == "StorageCleared"


def test_latest_snapshot_view(default_state: PersistenceState) -> None:
    store = MemoryStorage()
    domain = DummyDomainEvent("T1", Decimal("100.00"))

    snap1 = PersistenceAdapter.to_snapshot("S1", "OMS", 1000.0, domain)
    snap2 = PersistenceAdapter.to_snapshot("S2", "OMS", 1005.0, domain)
    snap3 = PersistenceAdapter.to_snapshot("S3", "RISK", 1010.0, domain)

    s1, _ = store.save_snapshot(default_state, snap1, 1000.0)
    s2, _ = store.save_snapshot(s1, snap2, 1005.0)
    s3, _ = store.save_snapshot(s2, snap3, 1010.0)

    latest_oms = latest_snapshot(s3, "OMS")
    latest_risk = latest_snapshot(s3, "RISK")
    latest_none = latest_snapshot(s3, "MISSING")

    assert latest_oms == snap2
    assert latest_risk == snap3
    assert latest_none is None


def test_immutability(default_state: PersistenceState) -> None:
    store = MemoryStorage()
    domain = DummyDomainEvent("T1", Decimal("100.00"))
    evt = PersistenceAdapter.to_stored_event("E1", 1000.0, domain)

    s1, _ = store.append_event(default_state, evt, 1001.0)

    assert default_state is not s1
    assert len(default_state.store.events) == 0
    assert len(s1.store.events) == 1
