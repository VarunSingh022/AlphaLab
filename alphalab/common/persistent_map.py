"""Persistent map and set with O(1) amortized update and structural sharing.

AlphaLab's engines are pure: every state transition returns a new immutable
state. Keyed indexes were previously modelled as ``dict`` / ``frozenset`` fields
and grown with ``dict(old)`` / ``frozenset(set(old) | {x})``, which rebuilds the
whole container on every update. A run of ``N`` transitions therefore copies
``O(N^2)`` entries -- that is what made ``benchmarks/benchmark_oms.py`` unable to
finish its 100k-order workload, once :class:`~alphalab.common.append_log.AppendOnlyLog`
had removed the event-accumulation term in v2.1.

:class:`PersistentMap` keeps the value semantics those containers provided -- it
is an immutable ``Mapping`` and every mutation returns a new map -- while making
the common case (update the newest version of a map) O(1) amortized.

How it works
------------
This is the same idea as :class:`~alphalab.common.append_log.AppendOnlyLog`,
generalised from "append to a sequence" to "write to a key": shared append-only
storage plus a version number, and *copy on branch*.

A map is a *view* over a shared store: the triple ``(store, version, size)``.
The store keeps, per key, the append-only chain of ``(version, value)`` entries
written to that key, plus the order in which keys were first inserted. A view at
version ``v`` reads a key by finding the newest chain entry whose version is at
most ``v`` -- so a write made at version ``v + 1`` is invisible to it, and older
views keep observing exactly what they observed before. Writing to the newest
view (``version == store.live_version``) appends one chain entry and returns a
view one version newer; writing to an *older* view would need a version number
another lineage already used, so that case copies the view's contents into a
fresh store -- "copy on branch". Linear histories, which is what the engines
produce, never branch and never copy.

Because the store is only ever appended to, every existing view stays valid and
keeps reading its own version's contents. As with ``AppendOnlyLog``, this is not
safe under concurrent mutation of the same store from multiple threads;
AlphaLab's engines are single-threaded and deterministic by design.

Iteration is in first-insertion order of the keys still present, so a map --
and any state holding one -- iterates and serializes deterministically.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Hashable, Iterable, Iterator, Mapping
from collections.abc import Set as AbstractSet
from typing import Any, Final, TypeVar, cast

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")
T = TypeVar("T", bound=Hashable)


class _Missing:
    """Tombstone marking a key as absent from some version onwards."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<missing>"


_MISSING: Final = _Missing()


class _Store[K: Hashable, V]:
    """Append-only backing storage shared by every view of one lineage."""

    __slots__ = ("chains", "keys", "live_version")

    #: Per key, the ``(version, value)`` writes to it, in ascending version order.
    chains: dict[K, list[tuple[int, V | _Missing]]]
    #: Every key ever written, in first-insertion order. Never removed.
    keys: list[K]
    #: Version of the view that currently owns this store.
    live_version: int

    def __init__(
        self,
        chains: dict[K, list[tuple[int, V | _Missing]]],
        keys: list[K],
        live_version: int,
    ) -> None:
        self.chains = chains
        self.keys = keys
        self.live_version = live_version


class PersistentMap(Mapping[K, V]):
    """Immutable mapping with O(1) amortized set/delete and structural sharing."""

    __slots__ = ("_size", "_store", "_version")

    _store: _Store[K, V]
    _version: int
    _size: int

    def __init__(self, items: Mapping[K, V] | Iterable[tuple[K, V]] = ()) -> None:
        pairs = items.items() if isinstance(items, Mapping) else items
        chains: dict[K, list[tuple[int, V | _Missing]]] = {}
        keys: list[K] = []
        for key, value in pairs:
            if key not in chains:
                keys.append(key)
            chains[key] = [(0, value)]
        self._store = _Store(chains, keys, 0)
        self._version = 0
        self._size = len(keys)

    # -- construction -------------------------------------------------------

    @classmethod
    def _view(cls, store: _Store[K, V], version: int, size: int) -> PersistentMap[K, V]:
        """Build a map viewing ``store`` as of ``version``."""

        view: PersistentMap[K, V] = cls.__new__(cls)
        view._store = store
        view._version = version
        view._size = size
        return view

    def _branch(self) -> PersistentMap[K, V]:
        """Copy this view's contents into a store it owns outright."""

        return PersistentMap(self.items())

    # -- reads --------------------------------------------------------------

    def _lookup(self, key: K) -> V | _Missing:
        """Value of ``key`` at this view's version, or the tombstone."""

        chain = self._store.chains.get(key)
        if chain is None:
            return _MISSING
        version = self._version
        last_version, last_value = chain[-1]
        if last_version <= version:
            # Overwhelmingly the common case: this view is at or past the
            # newest write to the key, so no search is needed.
            return last_value
        # Newest entry at or before ``version``. Versions are unique per chain
        # and ascending, so the probe never compares values -- which therefore
        # need not be orderable.
        index = bisect_left(cast(list[Any], chain), (version + 1,))
        if index == 0:
            return _MISSING
        return chain[index - 1][1]

    def __getitem__(self, key: K) -> V:
        value = self._lookup(key)
        if isinstance(value, _Missing):
            raise KeyError(key)
        return value

    def __contains__(self, key: object) -> bool:
        try:
            return not isinstance(self._lookup(cast(K, key)), _Missing)
        except TypeError:  # unhashable key
            return False

    def __len__(self) -> int:
        return self._size

    def __iter__(self) -> Iterator[K]:
        for key in self._store.keys:
            if not isinstance(self._lookup(key), _Missing):
                yield key

    # -- writes -------------------------------------------------------------

    def set(self, key: K, value: V) -> PersistentMap[K, V]:
        """Return a new map with ``key`` bound to ``value``."""

        store = self._store
        if self._version != store.live_version:
            return self._branch().set(key, value)

        version = self._version + 1
        chain = store.chains.get(key)
        if chain is None:
            store.chains[key] = [(version, value)]
            store.keys.append(key)
            size = self._size + 1
        else:
            size = self._size + (1 if isinstance(chain[-1][1], _Missing) else 0)
            chain.append((version, value))
        store.live_version = version
        return PersistentMap._view(store, version, size)

    def delete(self, key: K) -> PersistentMap[K, V]:
        """Return a new map without ``key``. Raises ``KeyError`` if absent."""

        if isinstance(self._lookup(key), _Missing):
            raise KeyError(key)

        store = self._store
        if self._version != store.live_version:
            return self._branch().delete(key)

        version = self._version + 1
        store.chains[key].append((version, _MISSING))
        store.live_version = version
        return PersistentMap._view(store, version, self._size - 1)

    # -- conversion ---------------------------------------------------------

    def to_dict(self) -> dict[K, V]:
        """Return this map's contents as a plain ``dict``, in insertion order."""

        return dict(self.items())

    def __serializable__(self) -> dict[K, V]:
        """Serialize exactly as the ``dict`` field this map replaced.

        A map whose keys are not valid JSON object keys is still rejected by
        the encoder rather than stringified; a type with such keys declares its
        own projection instead (see :class:`~alphalab.oms.book.OrderBook`).
        """

        return self.to_dict()

    def __repr__(self) -> str:
        return f"PersistentMap({self.to_dict()!r})"


class PersistentSet(AbstractSet[T]):
    """Immutable set with O(1) amortized add/discard and structural sharing.

    The frozenset counterpart of :class:`PersistentMap`, and backed by one.
    Unlike ``frozenset`` it iterates in insertion order, so states holding one
    serialize deterministically.
    """

    __slots__ = ("_members",)

    _members: PersistentMap[T, None]

    def __init__(self, members: Iterable[T] = ()) -> None:
        self._members = PersistentMap((member, None) for member in members)

    @classmethod
    def _wrap(cls, members: PersistentMap[T, None]) -> PersistentSet[T]:
        wrapped: PersistentSet[T] = cls.__new__(cls)
        wrapped._members = members
        return wrapped

    def add(self, member: T) -> PersistentSet[T]:
        """Return a new set containing ``member``."""

        if member in self._members:
            return self
        return PersistentSet._wrap(self._members.set(member, None))

    def discard(self, member: T) -> PersistentSet[T]:
        """Return a new set without ``member``; a no-op if it is absent."""

        if member not in self._members:
            return self
        return PersistentSet._wrap(self._members.delete(member))

    def __contains__(self, member: object) -> bool:
        return member in self._members

    def __iter__(self) -> Iterator[T]:
        return iter(self._members)

    def __len__(self) -> int:
        return len(self._members)

    def __serializable__(self) -> tuple[T, ...]:
        """Serialize as the ordered sequence of members.

        A set has no JSON form of its own, and a mapping key is not always
        JSON-safe (``OrderId`` is a dataclass), so the members serialize as an
        array in insertion order -- deterministic, and reconstructible.
        """

        return tuple(self)

    def __repr__(self) -> str:
        return f"PersistentSet({list(self)!r})"
