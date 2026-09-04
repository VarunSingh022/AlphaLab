"""Shared serialization helpers.

``dataclasses.asdict`` recurses into dataclasses, lists, tuples and dicts, and
falls back to ``copy.deepcopy`` for anything else. That fallback is wrong for
:class:`~alphalab.common.append_log.AppendOnlyLog`: a state's append-only history
would be deep-copied as an opaque object instead of being converted to a plain
sequence of dicts, and would then reach the JSON encoder as an unserializable
value. :func:`dataclass_to_dict` therefore does its own recursion, which is
``asdict``'s behaviour plus one rule -- an ``AppendOnlyLog`` converts like the
tuple it replaced.
"""

from dataclasses import fields, is_dataclass
from typing import Any, cast

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.exceptions import AlphaLabSerializationError


def _convert(value: Any) -> Any:
    """Recursively convert one value, mirroring ``dataclasses.asdict``."""

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


def dataclass_to_dict(instance: object) -> dict[str, Any]:
    """Serialize a dataclass instance to a dictionary."""

    if isinstance(instance, type) or not is_dataclass(instance):
        raise AlphaLabSerializationError("instance must be a dataclass instance")
    return cast(dict[str, Any], _convert(instance))
