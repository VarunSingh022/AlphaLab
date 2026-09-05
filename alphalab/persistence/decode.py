"""Typed decoding primitives for reading a snapshot back into domain values.

``serialize`` has always been strict: it writes deterministic JSON and raises on
anything it has no explicit branch for. ``deserialize`` was not its inverse --
it returns ``Any``, which is to say plain dicts, lists, strings and floats. Every
state in AlphaLab could be written down; only :mod:`alphalab.oms.snapshot` could
be read back.

This module is the other half. It is deliberately *not* a generic object mapper:
there is no reflection over dataclass fields and no "decode whatever this looks
like". A domain package states, field by field, what it expects, and each helper
here raises :class:`~alphalab.persistence.exceptions.StateDecodeError` naming the
field when the payload does not provide it. A wrong type, a missing key and a
malformed number are three different messages, and none of them is a default
value.

That distinction matters more than the convenience a reflective decoder would
buy. The failure this repository has already had once -- v2.1's append-only logs
persisted as the string ``"AppendOnlyLog([...])"`` -- came from a layer that
accepted anything rather than refusing what it did not understand.

Enum encoding
-------------
The encoder writes a ``StrEnum`` natively as its value, and every other ``Enum``
through ``str()``, which yields ``"ClassName.MEMBER"``. The two are decoded by
:func:`as_value_enum` and :func:`as_named_enum` respectively, and each accepts
only the form its encoder actually produces. Neither guesses.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from alphalab.persistence.exceptions import StateDecodeError

__all__ = [
    "as_bool",
    "as_decimal",
    "as_decimal_mapping",
    "as_float",
    "as_int",
    "as_mapping",
    "as_named_enum",
    "as_optional_decimal",
    "as_optional_str",
    "as_sequence",
    "as_str",
    "as_str_mapping",
    "as_value_enum",
    "require",
    "require_schema_version",
]


def require(payload: Mapping[str, Any], key: str) -> Any:
    """Return ``payload[key]``, or raise naming the field that is absent.

    A missing field is never filled in with a default: a snapshot that does not
    say what a value was cannot be read back into a state that claims one.
    """

    if key not in payload:
        raise StateDecodeError(f"Snapshot payload is missing {key!r}")
    return payload[key]


def require_schema_version(payload: Mapping[str, Any], supported: int, subsystem: str) -> int:
    """Check a snapshot's declared schema version against the one we can read.

    v2.5 supports exactly one version per subsystem, and there is nothing to
    migrate from. The field exists so that the first schema change is a decision
    someone makes rather than a payload that is silently misread.
    """

    version = require(payload, "schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise StateDecodeError(
            f"{subsystem} snapshot schema_version is not an integer: {version!r}"
        )
    if version != supported:
        raise StateDecodeError(
            f"{subsystem} snapshot declares schema version {version}, and this build "
            f"reads version {supported}. There is no migration path; read it with the "
            "build that wrote it."
        )
    return version


def as_mapping(value: Any, field: str) -> Mapping[str, Any]:
    """Require a JSON object."""

    if not isinstance(value, Mapping):
        raise StateDecodeError(f"{field} is not an object: {value!r}")
    return value


def as_sequence(value: Any, field: str) -> Sequence[Any]:
    """Require a JSON array. A string is a sequence in Python and is refused."""

    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise StateDecodeError(f"{field} is not an array: {value!r}")
    return value


def as_decimal(value: Any, field: str) -> Decimal:
    """Decode a monetary or quantity value.

    ``Decimal(str(value))`` is the same conversion
    :mod:`alphalab.market.normalization` uses, for the same reason: the encoder
    writes a ``Decimal`` as its exact string, and going back through ``str``
    returns the number that was written rather than a float's binary expansion.
    """

    if isinstance(value, bool) or value is None:
        raise StateDecodeError(f"{field} is not a decimal: {value!r}")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise StateDecodeError(f"{field} is not a decimal: {value!r}") from exc


def as_optional_decimal(value: Any, field: str) -> Decimal | None:
    """Decode a decimal that the domain allows to be absent."""

    return None if value is None else as_decimal(value, field)


def as_float(value: Any, field: str) -> float:
    """Decode a timestamp or ratio."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StateDecodeError(f"{field} is not a number: {value!r}")
    return float(value)


def as_int(value: Any, field: str) -> int:
    """Decode a version number or count."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise StateDecodeError(f"{field} is not an integer: {value!r}")
    return int(value)


def as_bool(value: Any, field: str) -> bool:
    """Decode a flag. ``0`` and ``1`` are integers here, not booleans."""

    if not isinstance(value, bool):
        raise StateDecodeError(f"{field} is not a boolean: {value!r}")
    return value


def as_str(value: Any, field: str) -> str:
    """Decode a string. A number is not coerced into one."""

    if not isinstance(value, str):
        raise StateDecodeError(f"{field} is not a string: {value!r}")
    return value


def as_optional_str(value: Any, field: str) -> str | None:
    """Decode a string the domain allows to be absent."""

    return None if value is None else as_str(value, field)


def as_str_mapping(value: Any, field: str) -> dict[str, str]:
    """Decode a mapping whose keys and values are both strings."""

    payload = as_mapping(value, field)
    return {
        as_str(key, f"{field} key"): as_str(item, f"{field}[{key}]")
        for key, item in payload.items()
    }


def as_decimal_mapping(value: Any, field: str) -> dict[str, Decimal]:
    """Decode a mapping of string keys to decimal values."""

    payload = as_mapping(value, field)
    return {
        as_str(key, f"{field} key"): as_decimal(item, f"{field}[{key}]")
        for key, item in payload.items()
    }


def as_value_enum[EnumT: Enum](enum_cls: type[EnumT], value: Any, field: str) -> EnumT:
    """Decode a ``StrEnum`` member, which the encoder writes as its value."""

    try:
        return enum_cls(value)
    except ValueError as exc:
        members = ", ".join(str(member.value) for member in enum_cls)
        raise StateDecodeError(
            f"{field} is not a {enum_cls.__name__}: {value!r}; expected one of {members}"
        ) from exc


def as_named_enum[EnumT: Enum](enum_cls: type[EnumT], value: Any, field: str) -> EnumT:
    """Decode a plain ``Enum`` member, which the encoder writes as ``"Cls.NAME"``.

    Only that form is accepted. A bare member name would also be unambiguous, but
    accepting it would mean this decoder reads payloads the encoder never writes,
    which is how a format quietly acquires two dialects.
    """

    text = as_str(value, field)
    prefix = f"{enum_cls.__name__}."
    if not text.startswith(prefix):
        raise StateDecodeError(
            f"{field} is not a {enum_cls.__name__}: {value!r}; expected {prefix!r} followed "
            "by a member name"
        )
    name = text[len(prefix) :]
    member = enum_cls.__members__.get(name)
    if member is None:
        members = ", ".join(enum_cls.__members__)
        raise StateDecodeError(
            f"{field} names no {enum_cls.__name__} member: {name!r}; expected one of {members}"
        )
    return member
