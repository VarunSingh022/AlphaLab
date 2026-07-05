"""Shared in-memory registry primitive."""

from collections.abc import Mapping


def with_mapping_item[KeyT, ValueT](
    mapping: Mapping[KeyT, ValueT], key: KeyT, value: ValueT
) -> dict[KeyT, ValueT]:
    """Return a mutable copy of a mapping with a key set."""

    updated = dict(mapping)
    updated[key] = value
    return updated


def without_mapping_key[KeyT, ValueT](
    mapping: Mapping[KeyT, ValueT], key: KeyT
) -> dict[KeyT, ValueT]:
    """Return a mutable copy of a mapping with a key removed."""

    updated = dict(mapping)
    del updated[key]
    return updated


