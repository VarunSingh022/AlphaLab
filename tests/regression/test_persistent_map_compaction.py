"""PRF-002: a persistent map reclaims what it no longer shows, and reads like a dict.

Until v3.10 a :class:`~alphalab.common.persistent_map.PersistentMap` never
reclaimed anything. Its store kept every key ever written and every value ever
written to each key, so iterating an order index that had seen a million orders
come and go walked a million entries to list its three open ones, and memory
grew with the total number of writes rather than with the map.

The oracle here is a plain ``dict`` driven by the same writes. Every view --
including older views that later writes must not disturb, and branches written
from them -- must read exactly as its ``dict`` twin reads, **in the same order**.
Dict order is what makes rebasing unobservable: dropping dead entries never
reorders live ones.
"""

from __future__ import annotations

import random

import pytest

from alphalab.common.persistent_map import PersistentMap, PersistentSet

_KEYS = [f"k{index}" for index in range(12)]


def _step(
    rng: random.Random,
    versions: list[tuple[PersistentMap[str, int], dict[str, int]]],
) -> None:
    # Mostly extend the newest version, sometimes branch from an older one.
    index = len(versions) - 1 if rng.random() < 0.85 else rng.randrange(len(versions))
    current, model = versions[index]
    key = rng.choice(_KEYS)
    if key in model and rng.random() < 0.4:
        updated = current.delete(key)
        twin = dict(model)
        del twin[key]
    else:
        value = rng.randrange(1000)
        updated = current.set(key, value)
        twin = dict(model)
        twin[key] = value
    versions.append((updated, twin))


@pytest.mark.parametrize("seed", range(25))
def test_every_view_reads_exactly_as_the_dict_built_by_the_same_writes(seed: int) -> None:
    rng = random.Random(seed)
    versions: list[tuple[PersistentMap[str, int], dict[str, int]]] = [(PersistentMap(), {})]
    for _ in range(600):
        _step(rng, versions)

    for view, model in versions:
        assert list(view.items()) == list(model.items())
        assert list(view) == list(model)
        assert len(view) == len(model)
        for key in _KEYS:
            assert (key in view) == (key in model)
            assert view.get(key) == model.get(key)


def test_a_reinserted_key_moves_to_the_end_as_in_a_dict() -> None:
    built: PersistentMap[str, int] = PersistentMap({"a": 1, "b": 2, "c": 3})
    reinserted = built.delete("a").set("a", 9)

    assert list(reinserted) == ["b", "c", "a"]
    assert list(built) == ["a", "b", "c"]


def test_a_long_linear_history_iterates_its_live_keys_not_its_past() -> None:
    """The measured defect: iteration cost followed every key ever written."""

    index: PersistentMap[int, int] = PersistentMap()
    for order in range(20_000):
        index = index.set(order, order)
        if order >= 3:
            index = index.delete(order - 3)

    assert list(index) == [19_997, 19_998, 19_999]
    # The store behind the newest view holds a bounded multiple of what it shows.
    store = index._store
    assert store.entries <= 2 * len(index) + 64 + 2
    assert len(store.order) <= store.entries


def test_an_older_view_is_untouched_by_a_rebase_of_its_successor() -> None:
    first: PersistentMap[str, int] = PersistentMap({"a": 1})
    newest = first
    for value in range(500):
        newest = newest.set("b", value)

    assert dict(first) == {"a": 1}
    assert dict(newest) == {"a": 1, "b": 499}
    assert newest._store is not first._store


def test_a_persistent_set_rebases_too() -> None:
    members: PersistentSet[int] = PersistentSet()
    for member in range(5_000):
        members = members.add(member)
        if member >= 2:
            members = members.discard(member - 2)

    assert list(members) == [4_998, 4_999]
    assert members._members._store.entries <= 2 * len(members) + 64 + 2
