"""Shared serialization helpers.

``dataclasses.asdict`` recurses into dataclasses, lists, tuples and dicts, and
falls back to ``copy.deepcopy`` for anything else. That fallback is wrong for
:class:`~alphalab.common.append_log.AppendOnlyLog`: a state's append-only history
would be deep-copied as an opaque object instead of being converted to a plain
sequence of dicts, and would then reach the JSON encoder as an unserializable
value. :func:`dataclass_to_dict` therefore does its own recursion, which is
``asdict``'s behaviour plus two rules:

* an ``AppendOnlyLog`` converts like the tuple it replaced, and
* a value that defines ``__serializable__`` converts as whatever that returns.

The second rule is how a type whose in-memory shape has no JSON form declares
one explicitly. :class:`~alphalab.oms.book.OrderBook` keys its orders by the
``OrderId`` dataclass, which JSON cannot use as an object key; rather than
weakening the typed identifier, or stringifying it at the boundary, the book
says what its serializable projection is (an ordered array of orders) and keeps
its typed keys in memory. Anything without such a projection still reaches the
encoder unchanged, and is still rejected there rather than stringified.
"""

from dataclasses import fields, is_dataclass
from typing import Any, Protocol, cast, runtime_checkable

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.exceptions import AlphaLabSerializationError


@runtime_checkable
class SupportsSerializable(Protocol):
    """A value that declares its own serializable projection."""

    def __serializable__(self) -> Any: ...


def _convert(value: Any) -> Any:
    """Recursively convert one value, mirroring ``dataclasses.asdict``."""

    if isinstance(value, SupportsSerializable) and not isinstance(value, type):
        # Checked before the dataclass branch: a dataclass may declare a
        # projection precisely because its field shape is not serializable.
        return _convert(value.__serializable__())
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _convert(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, AppendOnlyLog):
        # The sequence an AppendOnlyLog stands in for is a tuple, so it converts
        # exactly as the tuple field it replaced would have.
        return tuple(_convert(item) for item in value)
    if isinstance(value, list | tuple):
        # Preserve list/tuple identity (and namedtuple shape) as asdict does.
        if hasattr(value, "_fields"):
            return type(value)(*(_convert(item) for item in value))
        return type(value)(_convert(item) for item in value)
    if isinstance(value, dict):
        return type(value)((_convert(k), _convert(v)) for k, v in value.items())
    return value


def to_serializable(value: object) -> Any:
    """Convert any value to its serializable form, projections included."""

    return _convert(value)


def dataclass_to_dict(instance: object) -> dict[str, Any]:
    """Serialize a dataclass instance to a dictionary."""

    if isinstance(instance, type) or not is_dataclass(instance):
        raise AlphaLabSerializationError("instance must be a dataclass instance")
    converted = _convert(instance)
    if not isinstance(converted, dict):
        raise AlphaLabSerializationError(
            f"{type(instance).__name__} projects to "
            f"{type(converted).__name__}, not a mapping; use to_serializable()"
        )
    return cast(dict[str, Any], converted)
