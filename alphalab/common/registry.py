"""Shared in-memory registry primitive."""

from collections.abc import Iterator
from typing import TypeVar

from alphalab.common.exceptions import AlphaLabRegistryError

KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")


class Registry[KeyT, ValueT]:
    """Small deterministic registry for keyed values."""

    def __init__(self) -> None:
        """Initialize an empty registry."""

        self._items: dict[KeyT, ValueT] = {}

    def register(self, key: KeyT, value: ValueT) -> None:
        """Register a value under a key."""

        if key in self._items:
            raise AlphaLabRegistryError(f"key already registered: {key!r}")
        self._items[key] = value

    def unregister(self, key: KeyT) -> None:
        """Remove a registered key."""

        if key not in self._items:
            raise AlphaLabRegistryError(f"key is not registered: {key!r}")
        del self._items[key]

    def get(self, key: KeyT) -> ValueT:
        """Return a registered value."""

        try:
            return self._items[key]
        except KeyError as exc:
            raise AlphaLabRegistryError(f"key is not registered: {key!r}") from exc

    def contains(self, key: KeyT) -> bool:
        """Return whether a key is registered."""

        return key in self._items

    def items(self) -> tuple[tuple[KeyT, ValueT], ...]:
        """Return registered items in insertion order."""

        return tuple(self._items.items())

    def __iter__(self) -> Iterator[KeyT]:
        """Iterate registered keys in insertion order."""

        return iter(self._items)

    def __len__(self) -> int:
        """Return the number of registered items."""

        return len(self._items)
