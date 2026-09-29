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
The store keeps, per key, the append-only chain of ``version, value`` entries
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

Iteration is in insertion order, exactly as a ``dict`` iterates: rewriting a key
keeps its position, and a key that is deleted and later inserted again moves to
the end. A map -- and any state holding one -- therefore iterates and serializes
deterministically, and in the order a ``dict`` built by the same writes would.

Compaction, and what v3.10 changed
----------------------------------
Until v3.10 a store never reclaimed anything: every key ever written stayed in
its key list and every value ever written stayed in its chain. Iterating a map
cost ``O(keys ever written)`` rather than ``O(keys present)``, and memory grew
with the total number of writes -- an order index that had seen a million orders
come and go iterated a million entries to list its three open ones.

A store now counts its chain entries, and the newest view **rebases** a write
onto a fresh store holding only its live keys once the store holds more than
twice as many entries as the view has keys (plus a small constant). Older views
keep the store they were built on and observe nothing. Each rebase copies the
live keys once and follows at least as many writes as it copies, so writes stay
O(1) amortized and iteration becomes ``O(keys present)``. Because iteration
order is ``dict`` order, a rebase never changes it: dropping dead entries does
not reorder the live ones, which is what makes the rebase unobservable.

A chain is one flat list, ``[version, value, version, value, ...]``, rather
than a list of pairs (v3.11, ledger PRF-008): a write appends to a list that
already exists instead of allocating a pair, and a rebase allocates one list per
live key instead of a list and a pair. Every allocation that survives is one
more object the cyclic garbage collector walks for as long as the store lives,
and a rebase of a large map promotes its copy into the oldest generation at
once -- which is what had the OMS benchmark's accept-and-fill stage spend
nearly half its time in the collector.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Hashable, ItemsView, Iterable, Iterator, Mapping, ValuesView
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


#: A newest-version write rebases onto a fresh store once the store holds more
#: than ``_REBASE_FACTOR * size + _REBASE_SLACK`` chain entries.
_REBASE_FACTOR: Final = 2
_REBASE_SLACK: Final = 64


class _Store[K: Hashable, V]:
    """Append-only backing storage shared by every view of one lineage."""

    __slots__ = ("chains", "entries", "inserted_at", "keys", "live_version", "reinsertions")

    #: Per key, the writes to it in ascending version order, flat: the version of
    #: write ``i`` at index ``2 * i`` and its value at ``2 * i + 1``.
    chains: dict[K, list[Any]]
    #: Every insertion -- a first write, or a write after a deletion -- in
    #: ascending version order: the key here and, at the same index, the version
    #: in :attr:`inserted_at`. Two flat lists rather than a list of pairs, so an
    #: insertion allocates no container the cyclic garbage collector must then
    #: walk for as long as the store lives (ledger PRF-006). Never removed.
    keys: list[K]
    inserted_at: list[int]
    #: The versions a key was inserted at, ascending -- kept only for a key
    #: inserted more than once. A key inserted once was inserted at the version
    #: of its chain's first entry, which is the one record of it needed.
    reinsertions: dict[K, list[int]]
    #: Version of the view that currently owns this store.
    live_version: int
    #: Total chain entries, which is what a rebase is triggered by.
    entries: int

    def __init__(
        self,
        chains: dict[K, list[Any]],
        keys: list[K],
        inserted_at: list[int],
        live_version: int,
        entries: int,
    ) -> None:
        self.chains = chains
        self.keys = keys
        self.inserted_at = inserted_at
        self.reinsertions = {}
        self.live_version = live_version
        self.entries = entries


class PersistentMap(Mapping[K, V]):
    """Immutable mapping with O(1) amortized set/delete and structural sharing."""

    __slots__ = ("_size", "_store", "_version")

    _store: _Store[K, V]
    _version: int
    _size: int

    def __init__(self, items: Mapping[K, V] | Iterable[tuple[K, V]] = ()) -> None:
        pairs = items.items() if isinstance(items, Mapping) else items
        chains: dict[K, list[Any]] = {}
        keys: list[K] = []
        for key, value in pairs:
            if key not in chains:
                keys.append(key)
            chains[key] = [0, value]
        self._store = _Store(chains, keys, [0] * len(keys), 0, len(keys))
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
        """Copy this view's contents into a store it owns outright.

        For the newest view -- a rebase, which is the common case -- every
        key's current value is the last entry of its chain and its current
        insertion the last of its insertions, so the copy reads those directly
        rather than resolving each key by version (ledger PRF-006). An older
        view resolves as any read does.
        """

        store = self._store
        if self._version != store.live_version:
            return PersistentMap(self.items())
        chains = store.chains
        reinsertions = store.reinsertions
        fresh: dict[K, list[Any]] = {}
        keys: list[K] = []
        for key, inserted_at in zip(store.keys, store.inserted_at, strict=True):
            history = reinsertions.get(key)
            if history is not None and history[-1] != inserted_at:
                continue
            value = chains[key][-1]
            if isinstance(value, _Missing):
                continue
            fresh[key] = [0, value]
            keys.append(key)
        rebased: _Store[K, V] = _Store(fresh, keys, [0] * len(keys), 0, len(keys))
        return PersistentMap._view(rebased, 0, len(keys))

    # -- reads --------------------------------------------------------------

    def _lookup(self, key: K) -> V | _Missing:
        """Value of ``key`` at this view's version, or the tombstone."""

        chain = self._store.chains.get(key)
        if chain is None:
            return _MISSING
        version = self._version
        if chain[-2] <= version:
            # Overwhelmingly the common case: this view is at or past the
            # newest write to the key, so no search is needed. (An annotated
            # local, not ``cast``: ``cast(V | _Missing, ...)`` builds a union
            # at run time on every read.)
            newest: V | _Missing = chain[-1]
            return newest
        # The newest write at or before ``version``: a binary search over the
        # versions, at the even positions. Versions are unique per chain and
        # ascending, and only versions are compared -- values need not be
        # orderable. ``low`` ends as the number of writes at or before it.
        low, high = 0, len(chain) // 2
        while low < high:
            middle = (low + high) // 2
            if chain[2 * middle] <= version:
                low = middle + 1
            else:
                high = middle
        if low == 0:
            return _MISSING
        found: V | _Missing = chain[2 * low - 1]
        return found

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

    def _live(self) -> Iterator[tuple[K, V | _Missing]]:
        """Each insertion this view can see that is still its key's current one.

        Yields ``(key, value)`` with ``value`` possibly the tombstone; callers
        skip those. An insertion is current for this view when it is the key's
        newest insertion at or before the view's version -- an earlier one was
        superseded by a delete and a re-insert, which moved the key to the end.
        """

        store = self._store
        version = self._version
        reinsertions = store.reinsertions
        lookup = self._lookup
        for key, inserted_at in zip(store.keys, store.inserted_at, strict=False):
            if inserted_at > version:
                # Insertions are in ascending version order; nothing later is
                # visible to this view.
                return
            history = reinsertions.get(key)
            if history is not None:
                # Inserted more than once: only the insertion current at this
                # view's version places the key.
                if history[-1] <= version:
                    current = history[-1]
                else:
                    current = history[bisect_right(history, version) - 1]
                if inserted_at != current:
                    continue
            yield key, lookup(key)

    def __iter__(self) -> Iterator[K]:
        for key, value in self._live():
            if not isinstance(value, _Missing):
                yield key

    def _iter_items(self) -> Iterator[tuple[K, V]]:
        """Every live ``(key, value)`` pair, resolving each key once.

        ``Mapping``'s default views iterate the keys and then index the map,
        which resolves every key twice: once to decide it is present, once to
        read it. On a persistent map a resolution is a chain probe rather than a
        hash lookup, so paying for it twice is worth avoiding -- it made
        ``values()`` over a 500-entry map about 1.7x slower than it needed to
        be, which showed up as a regression in every reader that scans a whole
        map. :meth:`items` and :meth:`values` are built on this.
        """

        for key, value in self._live():
            if not isinstance(value, _Missing):
                yield key, value

    def items(self) -> ItemsView[K, V]:
        """A view of the map's items, iterating with one lookup per entry."""

        return _PersistentItemsView(self)

    def values(self) -> ValuesView[V]:
        """A view of the map's values, iterating with one lookup per entry."""

        return _PersistentValuesView(self)

    # -- writes -------------------------------------------------------------

    def set(self, key: K, value: V) -> PersistentMap[K, V]:
        """Return a new map with ``key`` bound to ``value``."""

        store = self._store
        if self._version != store.live_version or self._rebase_due():
            return self._branch().set(key, value)

        version = self._version + 1
        chain = store.chains.get(key)
        if chain is None:
            store.chains[key] = [version, value]
            store.keys.append(key)
            store.inserted_at.append(version)
            size = self._size + 1
        else:
            if isinstance(chain[-1], _Missing):
                # A re-insert: the key moves to the end, as it would in a dict.
                store.keys.append(key)
                store.inserted_at.append(version)
                history = store.reinsertions.get(key)
                if history is None:
                    # Its first insertion was its chain's first write.
                    store.reinsertions[key] = [chain[0], version]
                else:
                    history.append(version)
                size = self._size + 1
            else:
                size = self._size
            chain.append(version)
            chain.append(value)
        store.entries += 1
        store.live_version = version
        return PersistentMap._view(store, version, size)

    def delete(self, key: K) -> PersistentMap[K, V]:
        """Return a new map without ``key``. Raises ``KeyError`` if absent."""

        if isinstance(self._lookup(key), _Missing):
            raise KeyError(key)

        store = self._store
        if self._version != store.live_version or self._rebase_due():
            return self._branch().delete(key)

        version = self._version + 1
        chain = store.chains[key]
        chain.append(version)
        chain.append(_MISSING)
        store.entries += 1
        store.live_version = version
        return PersistentMap._view(store, version, self._size - 1)

    def _rebase_due(self) -> bool:
        """Whether the store has accumulated enough history to rebase this view."""

        return self._store.entries > _REBASE_FACTOR * self._size + _REBASE_SLACK

    # -- conversion ---------------------------------------------------------

    def to_dict(self) -> dict[K, V]:
        """Return this map's contents as a plain ``dict``, in insertion order."""

        return dict(self._iter_items())

    def __serializable__(self) -> dict[K, V]:
        """Serialize exactly as the ``dict`` field this map replaced.

        A map whose keys are not valid JSON object keys is still rejected by
        the encoder rather than stringified; a type with such keys declares its
        own projection instead (see :class:`~alphalab.oms.book.OrderBook`).
        """

        return self.to_dict()

    def __repr__(self) -> str:
        return f"PersistentMap({self.to_dict()!r})"


class _PersistentItemsView(ItemsView[K, V]):
    """``ItemsView`` that iterates a :class:`PersistentMap` in one pass."""

    __slots__ = ("_source",)

    def __init__(self, mapping: PersistentMap[K, V]) -> None:
        super().__init__(mapping)
        self._source = mapping

    def __iter__(self) -> Iterator[tuple[K, V]]:
        return self._source._iter_items()


class _PersistentValuesView(ValuesView[V]):
    """``ValuesView`` that iterates a :class:`PersistentMap` in one pass."""

    __slots__ = ("_source",)

    def __init__(self, mapping: PersistentMap[Any, V]) -> None:
        super().__init__(mapping)
        self._source = mapping

    def __iter__(self) -> Iterator[V]:
        for _, value in self._source._iter_items():
            yield value


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
