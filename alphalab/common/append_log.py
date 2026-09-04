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
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import TypeVar, overload

T = TypeVar("T")


class AppendOnlyLog(Sequence[T]):
    """Immutable append-only sequence with O(1) amortized append."""

    __slots__ = ("_buffer", "_length")

    _buffer: list[T]
    _length: int

    def __init__(self, items: Iterable[T] = ()) -> None:
        buffer = list(items)
        self._buffer = buffer
        self._length = len(buffer)

    @classmethod
    def _view(cls, buffer: list[T], length: int) -> AppendOnlyLog[T]:
        """Build a log viewing the first ``length`` entries of ``buffer``."""

        log: AppendOnlyLog[T] = cls.__new__(cls)
        log._buffer = buffer
        log._length = length
        return log

    def append(self, item: T) -> AppendOnlyLog[T]:
        """Return a new log with ``item`` appended.

        O(1) amortized when appending to the newest version of this log,
        O(len(self)) when branching from an older version.
        """

        if self._length == len(self._buffer):
            self._buffer.append(item)
            return AppendOnlyLog._view(self._buffer, self._length + 1)

        buffer = self._buffer[: self._length]
        buffer.append(item)
        return AppendOnlyLog._view(buffer, self._length + 1)

    def extend(self, items: Iterable[T]) -> AppendOnlyLog[T]:
        """Return a new log with every element of ``items`` appended in order."""

        log = self
        for item in items:
            log = log.append(item)
        return log

    def to_tuple(self) -> tuple[T, ...]:
        """Return the log's elements as a plain tuple."""

        return tuple(self._buffer[: self._length])

    def __len__(self) -> int:
        return self._length

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[T, ...]: ...

    def __getitem__(self, index: int | slice) -> T | tuple[T, ...]:
        if isinstance(index, slice):
            return tuple(self._buffer[: self._length][index])
        if index < 0:
            index += self._length
        if index < 0 or index >= self._length:
            raise IndexError("AppendOnlyLog index out of range")
        return self._buffer[index]

    def __iter__(self) -> Iterator[T]:
        buffer = self._buffer
        for i in range(self._length):
            yield buffer[i]

    def __reversed__(self) -> Iterator[T]:
        buffer = self._buffer
        for i in range(self._length - 1, -1, -1):
            yield buffer[i]

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
