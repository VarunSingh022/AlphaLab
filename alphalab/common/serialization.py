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


def _declares_projection(value: Any) -> bool:
    """Whether ``value``'s **class** declares ``__serializable__``.

    A plain attribute lookup on the type, and deliberately not
    ``isinstance(value, SupportsSerializable)``. That reads identically and costs
    enormously more: a ``runtime_checkable`` ``Protocol`` check runs through
    :func:`inspect.getattr_static`, which rebuilds a shadowed-dict view of the
    class for every value it is asked about. Profiled on a 1,600-event pipeline
    snapshot, that one line was ~60% of ``serialize`` -- 1,827,034 calls into
    ``inspect._shadowed_dict`` and 557,008 protocol checks, against 0.19s of
    actual JSON encoding. Reading the attribute off the type instead produces a
    **byte-identical payload** and measured 665.5ms against 2,419.5ms, a 3.64x
    reduction, on every snapshot in the repository.

    **The one behavioural difference, stated rather than glossed.**
    ``getattr_static`` also finds a ``__serializable__`` set on an *instance*;
    this finds only one declared on a class. Dunder lookup conventionally goes
    through the type -- ``len(x)`` does not consult ``x.__dict__['__len__']`` --
    so this is the more conventional reading, and no instance-level assignment
    exists anywhere in this repository: all seven declarations are ``def`` at
    class scope. A type that wants a projection declares one, which is what
    :class:`SupportsSerializable` documents.
    """

    return not isinstance(value, type) and hasattr(type(value), "__serializable__")


def _convert(value: Any) -> Any:
    """Recursively convert one value, mirroring ``dataclasses.asdict``."""

    if _declares_projection(value):
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
