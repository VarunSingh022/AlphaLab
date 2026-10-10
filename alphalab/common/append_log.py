"""Append-only log with O(1) amortized append and structural sharing.

AlphaLab's engines are pure: every state transition returns a new immutable
state. Append-only histories were previously modelled as ``tuple`` fields and
grown with ``(*state.events, event)``, which rebuilds the whole tuple on every
append. A run of ``N`` transitions therefore copies ``O(N^2)`` elements, which
is what made ``benchmarks/benchmark_risk_engine.py`` unable to finish its 100k
evaluation workload.

:class:`AppendOnlyLog` keeps the value semantics those tuples provided -- it is
an immutable ``Sequence`` and every mutation returns a new log -- while making
the common case (append to the newest version of a log) O(1) amortized.

How it works
------------
A log is a *view* over a shared backing list: the pair ``(buffer, length)``
where the log's elements are ``buffer[:length]``. Appending to the newest view
(``length == len(buffer)``) pushes onto the shared buffer and returns a new view
one element longer; the older view still reports its own shorter ``length`` and
therefore never observes the new element. Appending to an *older* view would
conflict with entries already past its end, so that case copies its prefix into
a fresh buffer -- "copy on branch". Linear histories, which is what the engines
produce, never branch and never copy.

The buffer is only ever appended to, so every existing view stays valid. This
is not safe under concurrent mutation of the same buffer from multiple threads;
AlphaLab's engines are single-threaded and deterministic by design.

Keeping only the newest entries (v3.12)
---------------------------------------
:meth:`AppendOnlyLog.retain_last` is how a declared retention policy bounds a
history a long run would otherwise keep whole (ledger PRF-004,
:mod:`alphalab.runtime.retention`). A view also has a *start* in its buffer, so
dropping the oldest entries moves the start and costs O(1); the entries a view
no longer reaches are released by copying the live ones to a fresh buffer once
they outnumber them, which each dropped entry pays for once. A log counts what
it has dropped (:attr:`AppendOnlyLog.dropped`): an entry's place in the whole
history is ``dropped`` plus its index, and a reader can tell a short history from
a trimmed one. Two logs are equal when they hold the same entries; how many each
dropped before them is stated by :attr:`~AppendOnlyLog.dropped` and is not part
of the comparison.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from itertools import islice
from typing import Final, TypeVar, overload

T = TypeVar("T")

#: Dropped entries a buffer holds before they are released, at the least: a
#: copy is not worth making for fewer.
_RELEASE_AFTER: Final = 32


class AppendOnlyLog(Sequence[T]):
    """Immutable append-only sequence with O(1) amortized append."""

    __slots__ = ("_buffer", "_dropped", "_length", "_start")

    _buffer: list[T]
    #: Where this view starts in its buffer.
    _start: int
    _length: int
    #: How many entries this log's history dropped before its first.
    _dropped: int

    def __init__(self, items: Iterable[T] = ()) -> None:
        buffer = list(items)
        self._buffer = buffer
        self._start = 0
        self._length = len(buffer)
        self._dropped = 0

    @classmethod
    def _view(cls, buffer: list[T], start: int, length: int, dropped: int) -> AppendOnlyLog[T]:
        """Build a log viewing ``length`` entries of ``buffer`` from ``start``."""

        log: AppendOnlyLog[T] = cls.__new__(cls)
        log._buffer = buffer
        log._start = start
        log._length = length
        log._dropped = dropped
        return log

    @classmethod
    def restored(cls, items: Iterable[T], dropped: int) -> AppendOnlyLog[T]:
        """A log holding ``items``, after ``dropped`` earlier entries it no longer keeps.

        How a snapshot puts back a log a retention policy trimmed.

        Raises:
            ValueError: If ``dropped`` is negative.
        """

        if isinstance(dropped, bool) or not isinstance(dropped, int) or dropped < 0:
            raise ValueError(f"A log cannot have dropped {dropped!r} entries.")
        buffer = list(items)
        return cls._view(buffer, 0, len(buffer), dropped)

    @property
    def dropped(self) -> int:
        """How many entries this log's history held before its first and no longer keeps."""

        return self._dropped

    def append(self, item: T) -> AppendOnlyLog[T]:
        """Return a new log with ``item`` appended.

        O(1) amortized when appending to the newest version of this log,
        O(len(self)) when branching from an older version.
        """

        end = self._start + self._length
        if end == len(self._buffer):
            self._buffer.append(item)
            return AppendOnlyLog._view(self._buffer, self._start, self._length + 1, self._dropped)

        buffer = self._buffer[self._start : end]
        buffer.append(item)
        return AppendOnlyLog._view(buffer, 0, self._length + 1, self._dropped)

    def retain_last(self, count: int) -> AppendOnlyLog[T]:
        """This log with only its newest ``count`` entries; the rest are counted as dropped.

        O(1), amortized: the view's start moves, and the buffer's dropped
        entries are released by one copy of the live ones once they outnumber
        them (and number at least a few dozen). A log already that short is
        returned as it is.

        Raises:
            ValueError: If ``count`` is negative.
        """

        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"A log cannot keep {count!r} entries.")
        if self._length <= count:
            return self
        excess = self._length - count
        start = self._start + excess
        if start > count and start >= _RELEASE_AFTER:
            return AppendOnlyLog._view(
                self._buffer[start : start + count], 0, count, self._dropped + excess
            )
        return AppendOnlyLog._view(self._buffer, start, count, self._dropped + excess)

    def extend(self, items: Iterable[T]) -> AppendOnlyLog[T]:
        """Return a new log with every element of ``items`` appended in order."""

        log = self
        for item in items:
            log = log.append(item)
        return log

    def to_tuple(self) -> tuple[T, ...]:
        """Return the log's elements as a plain tuple."""

        return tuple(self._buffer[self._start : self._start + self._length])

    def __len__(self) -> int:
        return self._length

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[T, ...]: ...

    def __getitem__(self, index: int | slice) -> T | tuple[T, ...]:
        if isinstance(index, slice):
            # Copies the entries asked for and no others. Until v3.11 a slice
            # copied the whole view first, so the ``log[before:]`` the
            # execution pipeline takes of the execution history and the
            # portfolio's events on every fill cost the length of the run, and
            # a run was quadratic in its length (ledger PRF-007).
            # ``indices`` bounds both ends by the view's length, so the shared
            # buffer's newer entries are never read; a list slice reaches its
            # start directly, where ``islice`` would walk to it.
            start, stop, step = index.indices(self._length)
            offset = self._start
            if step == 1:
                return tuple(self._buffer[offset + start : offset + stop])
            return tuple(self._buffer[offset + position] for position in range(start, stop, step))
        if index < 0:
            index += self._length
        if index < 0 or index >= self._length:
            raise IndexError("AppendOnlyLog index out of range")
        return self._buffer[self._start + index]

    def __iter__(self) -> Iterator[T]:
        """Iterate this view's elements, in order.

        :func:`itertools.islice` rather than a generator over ``range``, and the
        choice is worth ~8x: iterating a 10,000-element log cost 0.068s as a
        Python-level generator and 0.008s through ``islice``, against 0.005s for
        the plain ``tuple`` this container replaced. Every engine in the
        repository iterates these logs, so that constant was being paid
        everywhere -- and v2.17 spread it across nine more packages, which is
        what made it worth measuring.

        **Bounded, and that is not incidental.** ``islice`` stops at
        ``_length`` by index, so an append that grows the shared buffer while an
        iterator is in flight is never yielded -- the view keeps reporting
        exactly the elements ``len()`` claims. ``iter(self._buffer)`` would be
        marginally faster and would break that.
        """

        return islice(self._buffer, self._start, self._start + self._length)

    def __reversed__(self) -> Iterator[T]:
        """Iterate this view's elements, newest first.

        Snapshots the prefix rather than islicing, because ``reversed`` needs a
        sequence with a length. The copy is the same O(n) the iteration is, and
        this path is rare -- nothing on the execution path reads a log backwards.
        """

        return reversed(self._buffer[self._start : self._start + self._length])

    def __eq__(self, other: object) -> bool:
        if isinstance(other, AppendOnlyLog):
            return self.to_tuple() == other.to_tuple()
        if isinstance(other, tuple | list):
            return self.to_tuple() == tuple(other)
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.to_tuple())

    def __repr__(self) -> str:
        return f"AppendOnlyLog({list(self)!r})"
