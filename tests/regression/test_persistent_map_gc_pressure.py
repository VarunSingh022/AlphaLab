"""Regression guard for PRF-008: what a persistent map leaves for the collector to walk.

Until v3.11 a map's chain held a ``(version, value)`` pair per write, so every
write left one more container behind, and a rebase copied each live key into a
new list *and* a new pair. A rebase of a large map promoted that copy into the
oldest generation at once, and the OMS benchmark's accept-and-fill stage spent
2.7 s of its 5.6 s in the cyclic collector, against v3.9's 1.1 s of 3.6 s. A
chain is now one flat list, ``[version, value, version, value, ...]``.

Deterministic: the tests count the objects the collector tracks, with it
paused, rather than timing anything.
"""

import gc
from collections.abc import Iterator
from contextlib import contextmanager

from alphalab.common.persistent_map import PersistentMap, PersistentSet


@contextmanager
def _collector_paused() -> Iterator[None]:
    gc.collect()
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


def _tracked() -> int:
    return len(gc.get_objects())


def test_rewriting_a_key_leaves_no_container_behind() -> None:
    values = [object() for _ in range(1_000)]  # allocated before counting
    table = PersistentMap[int, object]((key, 0) for key in range(10_000))
    with _collector_paused():
        before = _tracked()
        for value in values:
            table = table.set(7, value)
        grown = _tracked() - before
    # One live view; the chain grew in place. Until v3.11: one pair per write.
    assert grown < 50, grown
    assert table[7] is values[-1]


class _Value:
    """A value the collector tracks, as an order or a position is."""

    def __init__(self, label: int) -> None:
        self.label = label


def test_a_rebase_allocates_one_container_per_live_key() -> None:
    live = 10_000
    values = [_Value(key) for key in range(live)]
    table = PersistentMap((key, values[key]) for key in range(live))
    # A store rebases on the write after it holds more than 2 * live + 64
    # entries. It starts with ``live``; ``live + 65`` rewrites bring it to
    # 2 * live + 65, so the next write -- the one measured -- rebases.
    for step in range(live + 65):
        table = table.set(step % live, values[step % live])
    marker = _Value(-1)
    with _collector_paused():
        before = _tracked()
        rebased = table.set(0, marker)
        allocated = _tracked() - before
    assert rebased[0] is marker
    assert len(rebased) == live
    # One flat list per live key (and the store's own few). Until v3.11 a list
    # and a pair per key: 2.0 per key.
    assert 0.9 < allocated / live < 1.1, allocated / live
    # The rebase is unobservable: the older view still reads what it read.
    assert table[0] is values[0]


def test_older_versions_still_read_their_own_values() -> None:
    history = [PersistentMap[str, int]()]
    for step in range(200):
        current = history[-1].set(f"k{step % 7}", step)
        if step % 5 == 4:
            current = current.delete(f"k{step % 7}")
        history.append(current)
    # Rebuild every version independently with plain dicts and compare.
    expected: dict[str, int] = {}
    replayed = [dict(expected)]
    for step in range(200):
        expected[f"k{step % 7}"] = step
        if step % 5 == 4:
            del expected[f"k{step % 7}"]
        replayed.append(dict(expected))
    for version, reference in zip(history, replayed, strict=True):
        assert dict(version.items()) == reference
        for key in (f"k{index}" for index in range(8)):
            assert (key in version) == (key in reference)
            assert version.get(key) == reference.get(key)


def test_a_set_removed_member_by_member_keeps_its_contents() -> None:
    members = PersistentSet(range(5_000))
    for member in range(0, 5_000, 3):
        members = members.discard(member)
    assert len(members) == 5_000 - len(range(0, 5_000, 3))
    assert list(members) == [member for member in range(5_000) if member % 3]
