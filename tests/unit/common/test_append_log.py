"""Unit tests for the AppendOnlyLog primitive."""

import pytest

from alphalab.common.append_log import AppendOnlyLog


def test_empty_log_behaves_like_an_empty_sequence() -> None:
    log: AppendOnlyLog[int] = AppendOnlyLog()

    assert len(log) == 0
    assert list(log) == []
    assert not log
    assert log == ()
    with pytest.raises(IndexError):
        log[0]


def test_append_returns_a_new_log_and_leaves_the_original_alone() -> None:
    first: AppendOnlyLog[str] = AppendOnlyLog(("a",))
    second = first.append("b")

    assert first is not second
    assert list(first) == ["a"]
    assert list(second) == ["a", "b"]


def test_branching_from_an_older_version_keeps_both_versions_correct() -> None:
    """Two logs grown from the same ancestor must not see each other's entries."""

    base = AppendOnlyLog((1, 2, 3))
    left = base.append(4).append(5)
    right = base.append(40).append(50)

    assert list(base) == [1, 2, 3]
    assert list(left) == [1, 2, 3, 4, 5]
    assert list(right) == [1, 2, 3, 40, 50]


def test_a_stale_reference_never_observes_later_appends() -> None:
    base = AppendOnlyLog((1,))
    grown = base.append(2).append(3)

    assert len(base) == 1
    assert base[-1] == 1
    assert len(grown) == 3


def test_appending_to_the_newest_version_shares_the_backing_buffer() -> None:
    """The O(1)-append guarantee: a linear chain never copies its history."""

    log: AppendOnlyLog[int] = AppendOnlyLog()
    buffers = []
    for i in range(100):
        log = log.append(i)
        buffers.append(id(log._buffer))

    assert len(set(buffers)) == 1
    assert list(log) == list(range(100))


def test_branching_copies_only_the_branch_point_prefix() -> None:
    base = AppendOnlyLog(range(10))
    branch_a = base.append(99)
    branch_b = base.append(100)

    assert id(branch_a._buffer) != id(branch_b._buffer)
    # Growing the branch after the copy is linear again.
    grown = branch_b.append(101)
    assert id(grown._buffer) == id(branch_b._buffer)


def test_extend_appends_every_item_in_order() -> None:
    log = AppendOnlyLog(("a",)).extend(("b", "c"))

    assert list(log) == ["a", "b", "c"]
    assert AppendOnlyLog(("a",)).extend(()) == ("a",)


def test_indexing_slicing_and_reversal() -> None:
    log = AppendOnlyLog(range(5))

    assert log[0] == 0
    assert log[-1] == 4
    assert log[1:3] == (1, 2)
    assert log[2:] == (2, 3, 4)
    assert list(reversed(log)) == [4, 3, 2, 1, 0]
    assert 3 in log
    with pytest.raises(IndexError):
        log[5]


def test_slicing_a_stale_view_is_bounded_by_that_view() -> None:
    base = AppendOnlyLog(range(3))
    base.append(99)  # grows the shared buffer past `base`

    assert base[:] == (0, 1, 2)
    assert base[1:] == (1, 2)


def test_equality_and_hashing_use_the_visible_elements() -> None:
    log = AppendOnlyLog((1, 2))

    assert log == AppendOnlyLog((1, 2))
    assert log == (1, 2)
    assert log == [1, 2]
    assert log != (1, 2, 3)
    assert log != "12"
    assert hash(log) == hash((1, 2))


def test_to_tuple_and_repr() -> None:
    log = AppendOnlyLog((1, 2))

    assert log.to_tuple() == (1, 2)
    assert isinstance(log.to_tuple(), tuple)
    assert repr(log) == "AppendOnlyLog([1, 2])"
