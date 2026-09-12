"""Persisting inside a run's identifier scope consumes the run's own identifiers.

This is a **characterization** of v2.12 behaviour, written before the store that
changes it. It asserts what the code does today, not what it should do, so that
the defect is measured rather than remembered -- the order v2.9 followed when it
characterized the resume defect before fixing it.

Why it happens
--------------
``MemoryStorage`` emits one system event per operation, and stamps it with
``MemoryStorage._create_id()``, which is :func:`~alphalab.common.ids.new_id`.
``new_id`` reads the ambient ``_ID_SOURCE`` contextvar. Inside
:func:`~alphalab.common.ids.id_scope` that source is the *run's*
:class:`~alphalab.common.ids.DeterministicIdSource`, so the storage layer draws
from the same stream the orders, fills, trades and transactions draw from.
Nothing in either package knows about the other; the contextvar is the coupling.

What was measured
-----------------
Inside ``id_scope``, one ``save_snapshot`` plus one ``append_event`` advances
``current_id_position().draws`` **0 -> 2**. Every operation that emits a system
event costs one draw::

    save_snapshot   +1        load_events   +1
    append_event    +1        clear         +1
    load_snapshot   +1        statistics     0

``load_snapshot`` is on that list, which is the sharper half of the problem: a
durable store built on this protocol could not *read a run back* without moving
its stream.

The consumption is not bookkeeping. The identifiers the storage layer takes are
the run's own next identifiers: a run seeded at ``SEED`` that mints one id,
persists, then mints two more ends up holding its 1st, 4th and 5th, while its
2nd and 3rd are stamped on a ``SnapshotSaved`` and an ``EventAppended``.
``test_the_consumed_identifiers_are_the_runs_own_next_two`` pins exactly that.

What ADR-0029 requires instead
------------------------------
A complete ``put`` / ``get`` cycle through the v2.13 ``RunStateStore``, executed
inside ``id_scope``, must advance ``draws`` by **exactly zero** (decision 7). The
store mints nothing: ``run_id`` is supplied by the caller, ``sequence`` is an
integer, and there is no system-event log to stamp. That invariant is what makes
a mid-run checkpoint safe, and this file is the evidence it was needed.

This file is **not** inverted by that store landing, and deliberately so: it
pins what the legacy ``MemoryStorage`` still does, which is why ADR-0029 replaces
it rather than repairing it. The inversion lives beside the new boundary, in
``tests/regression/test_run_state_store.py``, where the same cycle over
``FileRunStateStore`` and ``MemoryRunStateStore`` is asserted to draw zero.
Both remain true until v3.0 removes the legacy store.
"""

from decimal import Decimal
from typing import Any

import pytest

from alphalab.common.ids import current_id_position, id_scope, new_id

# Imported from the modules that define them rather than through
# ``alphalab.persistence``. Both paths reach the same objects; the package
# surface is deprecated as of v2.13 (ADR-0029 decision 6) and warns, and a
# characterization of the legacy behaviour should read that behaviour without
# also exercising the notice about it.
from alphalab.persistence.adapter import PersistenceAdapter
from alphalab.persistence.engine import PersistenceEngine
from alphalab.persistence.snapshot import Snapshot, StoredEvent
from alphalab.persistence.state import PersistenceState
from alphalab.persistence.storage import MemoryStorage

SEED = 20260912

#: The one save plus one append the archaeology measured, and its result.
MEASURED_DRAWS = 2


def _state() -> PersistenceState:
    return PersistenceEngine.initialize("CHARACTERIZATION")


def _snapshot(name: str = "S1", timestamp: float = 1.0) -> Snapshot:
    """A snapshot record. Its id is the caller's, so building one draws nothing."""

    return PersistenceAdapter.to_snapshot(name, "pipeline", timestamp, {"cash": Decimal("100")})


def _event(name: str = "E1", timestamp: float = 1.0) -> StoredEvent:
    """A stored event. Its id is the caller's, so building one draws nothing."""

    return PersistenceAdapter.to_stored_event(name, timestamp, {"quantity": Decimal("1")})


def _draws_for(operation: Any) -> int:
    """Identifiers ``operation`` draws from a run's stream, with a store primed.

    The store is primed with one snapshot and one event *inside* the same scope,
    so that ``load_snapshot`` and ``load_events`` have something to read and
    every operation is measured under identical conditions.
    """

    store = MemoryStorage()
    with id_scope(SEED):
        state = store.save_snapshot(_state(), _snapshot("S0", 1.0), 1.0)[0]
        state = store.append_event(state, _event("E0", 1.0), 1.0)[0]
        before = current_id_position().draws
        operation(store, state)
        return current_id_position().draws - before


# ---------------------------------------------------------------------------
# The measurement ADR-0029 is written against
# ---------------------------------------------------------------------------


def test_one_save_and_one_append_advance_the_run_stream_from_zero_to_two() -> None:
    """The headline measurement: 0 -> 2, with nothing else running."""

    store = MemoryStorage()
    with id_scope(SEED):
        state = _state()
        assert current_id_position().draws == 0, "a fresh scope has drawn nothing"

        state, _ = store.save_snapshot(state, _snapshot(), 1.0)
        assert current_id_position().draws == 1, "save_snapshot drew one"

        state, _ = store.append_event(state, _event(), 1.0)
        assert current_id_position().draws == MEASURED_DRAWS, "append_event drew one more"


def test_the_stream_the_storage_layer_draws_from_is_the_runs_own() -> None:
    """The seed on the position is the run's, not a stream of the store's."""

    store = MemoryStorage()
    with id_scope(SEED):
        store.save_snapshot(_state(), _snapshot(), 1.0)
        position = current_id_position()

    assert position.seed == SEED
    assert position.draws == 1


@pytest.mark.parametrize(
    ("name", "operation", "expected"),
    [
        ("save_snapshot", lambda s, st: s.save_snapshot(st, _snapshot("S1", 2.0), 2.0), 1),
        ("load_snapshot", lambda s, st: s.load_snapshot(st, "S0", 2.0), 1),
        ("append_event", lambda s, st: s.append_event(st, _event("E1", 2.0), 2.0), 1),
        ("load_events", lambda s, st: s.load_events(st, 2.0), 1),
        ("clear", lambda s, st: s.clear(st, "characterization", 2.0), 1),
        ("statistics", lambda s, st: s.statistics(st), 0),
    ],
)
def test_every_operation_that_emits_a_system_event_costs_one_draw(
    name: str, operation: Any, expected: int
) -> None:
    """Including ``load_snapshot``: reading a state back would move the stream too."""

    assert _draws_for(operation) == expected, name


# ---------------------------------------------------------------------------
# Why the count matters: they are the run's identifiers, not spare ones
# ---------------------------------------------------------------------------


def test_the_consumed_identifiers_are_the_runs_own_next_two() -> None:
    """The storage layer does not draw beside the run. It draws from in front of it."""

    with id_scope(SEED):
        control = [str(new_id()) for _ in range(5)]

    store = MemoryStorage()
    with id_scope(SEED):
        first = str(new_id())
        state, saved = store.save_snapshot(_state(), _snapshot(), 1.0)
        _, appended = store.append_event(state, _event(), 1.0)
        consumed = [saved[0].event_id, appended[0].event_id]
        after = [str(new_id()) for _ in range(2)]

    assert first == control[0], "the run's first identifier is unaffected"
    assert consumed == control[1:3], "the storage events carry the run's 2nd and 3rd identifiers"
    assert after == control[3:5], "the run resumes two positions further along"


def test_a_run_that_persists_mints_different_identifiers_from_one_that_does_not() -> None:
    """The observable consequence, stated without reference to any counter."""

    def stream(persist: bool) -> list[str]:
        store = MemoryStorage()
        with id_scope(SEED):
            if persist:
                state, _ = store.save_snapshot(_state(), _snapshot(), 1.0)
                store.append_event(state, _event(), 1.0)
            return [str(new_id()) for _ in range(3)]

    assert stream(persist=True) != stream(persist=False)


# ---------------------------------------------------------------------------
# Where the draw comes from, and where it does not
# ---------------------------------------------------------------------------


def test_building_the_records_draws_nothing() -> None:
    """``PersistenceAdapter`` takes both identifiers, so it is not the source."""

    with id_scope(SEED):
        _snapshot()
        _event()

        assert current_id_position().draws == 0


def test_outside_a_scope_there_is_no_cursor_to_disturb() -> None:
    """An unseeded run has no position, so the draws are invisible and harmless.

    They are still ``uuid4`` values that cannot collide, which is why this is a
    defect about *seeded* runs -- the ones whose identifiers are supposed to
    reproduce.
    """

    store = MemoryStorage()
    store.save_snapshot(_state(), _snapshot(), 1.0)
    position = current_id_position()

    assert position.seed is None
    assert position.draws == 0
