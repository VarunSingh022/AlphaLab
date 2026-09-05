"""Unit tests for the persistent map and set behind the OMS order book.

The structure's whole reason to exist is that it is cheap *and* still a value:
an older version must keep observing exactly what it observed before a newer
version wrote. These tests pin both halves -- the value semantics first,
because a fast structure that quietly mutates history is worse than a slow one.
"""

from collections.abc import Set as AbstractSet
from decimal import Decimal

import pytest

from alphalab.common.persistent_map import PersistentMap, PersistentSet

# ---------------------------------------------------------------------------
# Value semantics
# ---------------------------------------------------------------------------


def test_empty_map_is_empty() -> None:
    empty: PersistentMap[str, int] = PersistentMap()

    assert len(empty) == 0
    assert dict(empty) == {}
    assert "a" not in empty


def test_set_returns_a_new_map_and_leaves_the_old_one_alone() -> None:
    base: PersistentMap[str, int] = PersistentMap({"a": 1})
    updated = base.set("b", 2)

    assert dict(base) == {"a": 1}
    assert dict(updated) == {"a": 1, "b": 2}
    assert base is not updated


def test_overwriting_a_key_does_not_change_older_versions() -> None:
    """The property a shared backing store could most easily get wrong."""

    v1: PersistentMap[str, int] = PersistentMap({"a": 1})
    v2 = v1.set("a", 2)
    v3 = v2.set("a", 3)

    assert v1["a"] == 1
    assert v2["a"] == 2
    assert v3["a"] == 3


def test_deleting_a_key_does_not_change_older_versions() -> None:
    v1: PersistentMap[str, int] = PersistentMap({"a": 1, "b": 2})
    v2 = v1.delete("a")

    assert dict(v1) == {"a": 1, "b": 2}
    assert dict(v2) == {"b": 2}
    assert "a" in v1
    assert "a" not in v2
    assert len(v1) == 2
    assert len(v2) == 1


def test_a_key_can_be_deleted_and_reinserted() -> None:
    v1: PersistentMap[str, int] = PersistentMap({"a": 1})
    v2 = v1.delete("a")
    v3 = v2.set("a", 9)

    assert v1["a"] == 1
    assert "a" not in v2
    assert v3["a"] == 9
    assert len(v3) == 1


def test_deleting_an_absent_key_raises() -> None:
    empty: PersistentMap[str, int] = PersistentMap()

    with pytest.raises(KeyError):
        empty.delete("missing")


def test_missing_key_raises_on_lookup_but_not_on_get() -> None:
    book: PersistentMap[str, int] = PersistentMap({"a": 1})

    with pytest.raises(KeyError):
        _ = book["b"]
    assert book.get("b") is None
    assert book.get("b", 7) == 7


def test_branching_from_an_older_version_keeps_both_lineages_correct() -> None:
    """Writing to a superseded version must not disturb the newer one."""

    v1: PersistentMap[str, int] = PersistentMap({"a": 1})
    main = v1.set("b", 2).set("c", 3)
    branch = v1.set("b", 99)

    assert dict(v1) == {"a": 1}
    assert dict(main) == {"a": 1, "b": 2, "c": 3}
    assert dict(branch) == {"a": 1, "b": 99}

    # And both lineages keep growing independently afterwards.
    assert dict(main.set("d", 4)) == {"a": 1, "b": 2, "c": 3, "d": 4}
    assert dict(branch.set("d", 40)) == {"a": 1, "b": 99, "d": 40}


def test_iteration_is_in_first_insertion_order() -> None:
    """Deterministic order is what makes a state holding one serialize stably."""

    built: PersistentMap[str, int] = PersistentMap()
    for key in ("c", "a", "b"):
        built = built.set(key, 1)
    built = built.set("a", 2)  # overwrite does not move the key

    assert list(built) == ["c", "a", "b"]


def test_equality_matches_a_plain_mapping() -> None:
    assert PersistentMap({"a": 1}) == {"a": 1}
    assert PersistentMap({"a": 1}) == PersistentMap({"a": 1})
    assert PersistentMap({"a": 1}) != PersistentMap({"a": 2})


def test_map_serializes_as_the_dict_it_replaced() -> None:
    assert PersistentMap({"a": Decimal("1.00")}).__serializable__() == {"a": Decimal("1.00")}


def test_constructed_from_pairs_last_value_wins() -> None:
    built = PersistentMap([("a", 1), ("b", 2), ("a", 3)])

    assert dict(built) == {"a": 3, "b": 2}
    assert list(built) == ["a", "b"]


# ---------------------------------------------------------------------------
# Structural sharing
# ---------------------------------------------------------------------------


def test_a_linear_run_of_writes_shares_one_backing_store() -> None:
    """The property the O(1) amortized claim rests on: no copying per write."""

    built: PersistentMap[int, int] = PersistentMap()
    stores = set()
    for i in range(500):
        built = built.set(i, i)
        stores.add(id(built._store))

    assert len(stores) == 1
    assert len(built) == 500


def test_only_branching_copies() -> None:
    v1: PersistentMap[int, int] = PersistentMap({0: 0})
    main = v1.set(1, 1)
    branch = v1.set(1, 100)

    assert id(main._store) != id(branch._store)


# ---------------------------------------------------------------------------
# PersistentSet
# ---------------------------------------------------------------------------


def test_set_add_and_discard_are_value_operations() -> None:
    v1: PersistentSet[str] = PersistentSet(("a",))
    v2 = v1.add("b")
    v3 = v2.discard("a")

    assert set(v1) == {"a"}
    assert set(v2) == {"a", "b"}
    assert set(v3) == {"b"}


def test_set_add_of_an_existing_member_is_a_no_op() -> None:
    v1: PersistentSet[str] = PersistentSet(("a",))

    assert v1.add("a") is v1


def test_set_discard_of_an_absent_member_is_a_no_op() -> None:
    v1: PersistentSet[str] = PersistentSet(("a",))

    assert v1.discard("zzz") is v1


def test_set_compares_equal_to_a_frozenset_both_ways() -> None:
    members: AbstractSet[str] = PersistentSet(("a", "b"))
    same: AbstractSet[str] = frozenset({"a", "b"})
    smaller: AbstractSet[str] = frozenset({"a"})

    assert members == same
    assert same == members
    assert members != smaller


def test_set_iterates_in_insertion_order_and_serializes_as_an_array() -> None:
    members: PersistentSet[str] = PersistentSet()
    for name in ("c", "a", "b"):
        members = members.add(name)

    assert list(members) == ["c", "a", "b"]
    assert members.__serializable__() == ("c", "a", "b")
