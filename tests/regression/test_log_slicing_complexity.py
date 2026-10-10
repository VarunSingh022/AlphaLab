"""Regression guard for PRF-007: a slice of an append-only log copies only itself.

Until v3.11 ``AppendOnlyLog.__getitem__`` answered a slice by copying the whole
view and slicing the copy. The execution pipeline takes ``history[before:]`` of
the execution history and ``events[before:]`` of the portfolio's events on
every fill, so each fill cost the length of the run so far and a run was
quadratic in its length: with the garbage collector paused, the pipeline
benchmark's cost per event rose from 754 to 1,136 microseconds between 2,000
and 16,000 events, and is flat (664, 678, 651) with the fix.

The size test is deterministic: ``tracemalloc`` counts the bytes a slice
allocates, which is the copy the fix removes. The semantic tests pin that a
slice still means what a tuple's slice means, including on an older view whose
buffer a newer log has grown past.
"""

import tracemalloc

import pytest

from alphalab.common.append_log import AppendOnlyLog

_BOUNDS = (None, *range(-9, 10))
_STEPS = (None, 1, 2, 3, -1, -2, -3)


def _every_slice() -> list[slice]:
    return [slice(start, stop, step) for start in _BOUNDS for stop in _BOUNDS for step in _STEPS]


@pytest.mark.parametrize("length", [0, 1, 2, 5, 8])
def test_a_slice_means_what_a_tuple_slice_means(length: int) -> None:
    items = tuple(range(length))
    log = AppendOnlyLog(items)
    for index in _every_slice():
        assert log[index] == items[index], index


def test_an_older_view_never_reads_the_entries_a_newer_log_appended() -> None:
    older = AppendOnlyLog(range(5))
    newer = older.append(5).append(6)  # grows the buffer the older view shares
    assert len(newer) == 7
    for index in _every_slice():
        assert older[index] == tuple(range(5))[index], index
        assert newer[index] == tuple(range(7))[index], index


def test_a_slice_returns_a_tuple() -> None:
    log = AppendOnlyLog(range(4))
    assert isinstance(log[1:], tuple)
    assert isinstance(log[::-1], tuple)
    assert log[4:] == ()
    assert log[10:20] == ()


def _bytes_allocated_by(action: object) -> int:
    assert callable(action)
    tracemalloc.start()
    try:
        before, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        action()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak - before


def test_a_tail_slice_does_not_copy_the_log() -> None:
    log = AppendOnlyLog(range(200_000))
    # The fixed slice allocates the three entries and a tuple; the old one
    # allocated a 200,000-slot list first (about 1.6 MB).
    assert _bytes_allocated_by(lambda: log[199_997:]) < 16_384


def test_a_tail_slice_of_an_older_view_does_not_copy_the_log() -> None:
    older = AppendOnlyLog(range(200_000))
    older.append(-1)
    assert _bytes_allocated_by(lambda: older[199_990:]) < 16_384
    assert older[199_998:] == (199_998, 199_999)
