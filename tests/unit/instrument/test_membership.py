"""Dated membership: what held, what was known, and a history that could have happened (ADR-0059).

A :class:`~alphalab.instrument.membership.MembershipChange` is dated when it took
effect and when it was announced; a
:class:`~alphalab.instrument.membership.HistoricalUniverse` refuses a history
that could not have happened and answers membership as known at an instant; the
registry carries universes beside its instruments, snapshots and digests a
registry without one exactly as before, and restores one that has them.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from alphalab.instrument.exceptions import InstrumentInputError, InstrumentRegistrationError
from alphalab.instrument.membership import (
    HistoricalUniverse,
    MembershipChange,
    MembershipKind,
    universe_from_primitives,
    universe_to_primitives,
)
from alphalab.instrument.registry import register_universe
from alphalab.instrument.snapshot import capture, from_primitives, registry_digest, restore
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.context_views import UniverseView
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.strategy.context import NoUniverse
from tests.integration.harness import equity, registry_of

ADD, REMOVE, DELIST = MembershipKind.ADDITION, MembershipKind.REMOVAL, MembershipKind.DELISTING
A, B, C = (equity(symbol) for symbol in ("AAA", "BBB", "CCC"))


def _change(
    asset: Any, kind: MembershipKind, effective: float, announced: float
) -> MembershipChange:
    return MembershipChange("IDX", asset.asset_id, kind, effective, announced)


def _history() -> HistoricalUniverse:
    return HistoricalUniverse(
        "IDX",
        (
            _change(B, REMOVE, 30.0, 25.0),  # given out of order: kept in effective order
            _change(A, ADD, 0.0, 0.0),
            _change(B, ADD, 0.0, 0.0),
            _change(C, ADD, 20.0, 12.0),
            _change(C, DELIST, 40.0, 40.0),
        ),
    )


# --- a change ------------------------------------------------------------------------


def test_a_change_announced_after_it_took_effect_is_refused() -> None:
    with pytest.raises(InstrumentInputError, match="look-ahead"):
        _change(A, ADD, 10.0, 11.0)
    assert _change(A, ADD, 10.0, 10.0).announced_at == 10.0


@pytest.mark.parametrize(
    "changed",
    [
        {"universe": ""},
        {"asset_id": "  "},
        {"kind": "ADDITION"},
        {"effective_at": math.inf},
        {"announced_at": math.nan},
        {"effective_at": True},
    ],
)
def test_a_change_is_named_kinded_and_dated(changed: dict[str, Any]) -> None:
    fields: dict[str, Any] = {
        "universe": "IDX",
        "asset_id": A.asset_id,
        "kind": ADD,
        "effective_at": 1.0,
        "announced_at": 0.0,
    }
    fields.update(changed)
    with pytest.raises(InstrumentInputError, match="MembershipChange"):
        MembershipChange(**fields)


# --- a history -----------------------------------------------------------------------


def test_a_history_is_kept_in_the_order_it_took_effect() -> None:
    assert [change.effective_at for change in _history().changes] == [0.0, 0.0, 20.0, 30.0, 40.0]
    assert _history().assets == {A.asset_id, B.asset_id, C.asset_id}


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ([_change(A, ADD, 0.0, 0.0), _change(A, ADD, 5.0, 5.0)], "already a member"),
        ([_change(A, REMOVE, 5.0, 5.0)], "removed .* while not a member"),
        ([_change(A, DELIST, 5.0, 5.0)], "delisted .* while not a member"),
        ([_change(A, ADD, 5.0, 5.0), _change(A, REMOVE, 5.0, 5.0)], r"twice at 5\.0"),
        ([MembershipChange("OTHER", A.asset_id, ADD, 0.0, 0.0)], "a change to 'OTHER'"),
    ],
)
def test_a_history_that_could_not_have_happened_is_refused(
    changes: list[MembershipChange], match: str
) -> None:
    with pytest.raises(InstrumentInputError, match=match):
        HistoricalUniverse("IDX", tuple(changes))


def test_members_are_read_as_known() -> None:
    history = _history()

    assert history.members(10.0, as_of=10.0) == {A.asset_id, B.asset_id}
    # C's addition, effective at 20, is known from 12: before then, never.
    assert history.members(25.0, as_of=11.0) == {A.asset_id, B.asset_id}
    assert history.members(25.0, as_of=12.0) == {A.asset_id, B.asset_id, C.asset_id}
    assert history.members(19.0, as_of=19.0) == {A.asset_id, B.asset_id}
    # B's removal at 30 is known from 25; C's delisting at 40 is known at 40.
    assert history.members(35.0, as_of=24.0) == {A.asset_id, B.asset_id, C.asset_id}
    assert history.members(35.0, as_of=35.0) == {A.asset_id, C.asset_id}
    assert history.members(45.0, as_of=45.0) == {A.asset_id}
    # The past read with later knowledge is the past: a change takes effect no
    # earlier than it is announced.
    assert history.members(10.0, as_of=100.0) == history.members(10.0, as_of=10.0)


def test_a_bounded_history_refuses_a_question_outside_its_bounds() -> None:
    history = HistoricalUniverse("IDX", _history().changes, coverage=(0.0, 50.0), known_until=45.0)

    assert history.members(49.0, as_of=45.0) == {A.asset_id}
    with pytest.raises(InstrumentInputError, match="assumed, not read"):
        history.members(50.0, as_of=45.0)
    with pytest.raises(InstrumentInputError, match="nothing was announced since"):
        history.members(40.0, as_of=46.0)
    with pytest.raises(InstrumentInputError, match=r"announced at 40\.0"):
        HistoricalUniverse("IDX", _history().changes, known_until=39.0)
    with pytest.raises(InstrumentInputError, match=r"effective at 40\.0"):
        HistoricalUniverse("IDX", _history().changes, coverage=(0.0, 40.0))
    with pytest.raises(InstrumentInputError, match="empty"):
        HistoricalUniverse("IDX", (), coverage=(5.0, 5.0))


@pytest.mark.parametrize("bounds", [{}, {"coverage": (0.0, 50.0), "known_until": 45.0}])
def test_a_history_round_trips_through_its_record(bounds: dict[str, Any]) -> None:
    history = HistoricalUniverse("IDX", _history().changes, **bounds)
    record = deserialize(serialize(universe_to_primitives(history)))

    assert universe_from_primitives("IDX", record) == history
    assert ("coverage" in record) == bool(bounds)


# --- the registry ----------------------------------------------------------------------


def test_the_registry_carries_a_universe_whose_members_it_holds() -> None:
    registry = register_universe(registry_of(A, B, C), _history())

    assert registry.universes["IDX"] == _history()
    with pytest.raises(InstrumentRegistrationError, match="already carries"):
        register_universe(registry, _history())
    with pytest.raises(InstrumentRegistrationError, match="does not hold"):
        register_universe(registry_of(A, B), _history())


def test_a_registry_without_universes_is_stored_and_digested_as_before() -> None:
    plain = registry_of(A, B, C)
    payload = serialize(capture(plain))

    assert '"universes"' not in payload
    carrying = register_universe(plain, _history())
    assert '"universes"' in serialize(capture(carrying))
    assert registry_digest(carrying) != registry_digest(plain)
    assert registry_digest(registry_of(A, B, C)) == registry_digest(plain)


def test_a_registry_with_a_universe_round_trips() -> None:
    registry = register_universe(registry_of(A, B, C), _history())
    restored = restore(from_primitives(deserialize(serialize(capture(registry)))))

    assert restored.universes == registry.universes
    assert registry_digest(restored) == registry_digest(registry)


# --- what a strategy reads ----------------------------------------------------------------


def test_a_strategy_reads_membership_as_known_at_its_own_instant() -> None:
    registry = register_universe(registry_of(A, B, C), _history())

    assert UniverseView(registry, 11.0).members("IDX", 25.0) == {A.asset_id, B.asset_id}
    assert C.asset_id in UniverseView(registry, 12.0).members("IDX", 25.0)
    with pytest.raises(RuntimeValidationError, match="no historical universe 'OTHER'"):
        UniverseView(registry, 12.0).members("OTHER", 25.0)
    with pytest.raises(RuntimeValidationError, match="no instant"):
        UniverseView(registry).members("IDX", 25.0)
    assert UniverseView(None, 12.0).members("IDX", 25.0) == frozenset()
    assert NoUniverse().members("IDX", 25.0) == frozenset()
