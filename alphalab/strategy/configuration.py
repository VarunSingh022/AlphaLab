"""A strategy's configuration, declared by type and read back exactly (ledger FUT-002).

What a strategy is configured with is recorded on its run's snapshot as the
encoder writes it, and read back as JSON reads it (LIM-004): a ``Decimal`` as
its text, a tuple as a list, an enum by its value or name, a dataclass as a
mapping. That conversion is exact and stated
(``tests/regression/test_strategy_configuration_contract.py``), and it is also
lossy in type -- ``restore(capture(s)) == s`` does not hold for such a value.

A strategy that declares its configuration's type closes that, without moving
the payload. It sets ``configuration_type`` on its class::

    @dataclass(frozen=True)
    class Bands:
        window: int
        width: Decimal
        sides: tuple[Side, ...] = (Side.BUY, Side.SELL)


    class MeanReversion(BaseStrategy):
        configuration_type = Bands

Then :meth:`~alphalab.strategy.supervisor.RuntimeSupervisor.configure` refuses a
value that is not of that type -- naming the field and what was found -- and
:func:`~alphalab.runtime.snapshot.restore` reads the recorded value back *as*
that type: the ``Decimal`` from its text, the tuple, the enum member, the
dataclass rebuilt through its own constructor, so its defaults and its
``__post_init__`` constraints hold on the way back too. The payload is what the
encoder has always written, so no schema moves and no run's ``result_id`` or
``configuration_id`` moves; a strategy that declares nothing is read back as
before.

What may be declared
--------------------
``None``, ``bool``, ``int``, ``float`` (finite), ``str``, ``Decimal`` (finite),
``UUID``; an ``Enum`` subclass (a ``StrEnum`` or ``IntEnum`` written by value, a
plain ``Enum`` by ``Class.MEMBER``); ``Literal`` of those; ``list[T]``,
``tuple[T, ...]`` and ``tuple[A, B, ...]``; ``dict[K, V]`` and ``Mapping[K, V]``
with ``K`` one of ``str``, ``int``, ``bool``, ``float`` or a string or integer
enum -- what JSON can key by; ``T | None``; ``Annotated[T, ...]``; a dataclass
whose fields are declared with such types; and ``Any``, for a value that is
JSON-native already (``None``, ``bool``, ``int``, ``str``, a finite ``float``,
and lists and string-keyed mappings of them), which reads back exact without a
type. Anything else -- a ``set``, a ``datetime``, a union of two non-``None``
types (its JSON form would not say which), a class that is not a dataclass -- is
refused when the strategy is configured, never coerced.

Checks are strict: an ``int`` is not a ``float``, a ``bool`` not an ``int``, a
``str`` subclass not a ``str``, because each would read back as the other type.
"""

from __future__ import annotations

import math
import types
import typing
from collections.abc import Mapping
from dataclasses import MISSING, fields, is_dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Final, Literal, Union, get_args, get_origin
from uuid import UUID

from alphalab.strategy.exceptions import StrategyValidationError

__all__ = [
    "CONFIGURATION_ATTRIBUTE",
    "StrategyConfigurationError",
    "declared_configuration",
    "decode_configuration",
    "require_configuration",
]

#: The class attribute a strategy declares its configuration's type by.
CONFIGURATION_ATTRIBUTE: Final = "configuration_type"

_NONE_TYPE: Final = type(None)
_SCALARS: Final = (bool, int, float, str, Decimal, UUID)
_KEY_SCALARS: Final = (str, int, bool, float)


class StrategyConfigurationError(StrategyValidationError):
    """A configuration that is not of its declared type, or a type that cannot be declared."""


def declared_configuration(instance: object) -> Any | None:
    """The type ``instance``'s class declares its configuration as, or ``None``."""

    return getattr(type(instance), CONFIGURATION_ATTRIBUTE, None)


def require_configuration(value: Any, declared: Any, where: str = "config") -> None:
    """Refuse ``value`` unless it is of the ``declared`` type, exactly enough to read back as it.

    Raises:
        StrategyConfigurationError: Naming the first field that is not, or the
            first part of ``declared`` that cannot be declared.
    """

    _require_declarable(declared, where, set())
    _require(value, declared, where)


def decode_configuration(value: Any, declared: Any, where: str = "config") -> Any:
    """Read the encoder's form of a configuration back as its ``declared`` type.

    The inverse of what the encoder writes for every value
    :func:`require_configuration` admits: ``decode(json(encode(v))) == v``, and
    of the same types, for each.

    Raises:
        StrategyConfigurationError: If ``value`` is not the encoder's form of a
            value of that type, naming the field.
    """

    _require_declarable(declared, where, set())
    return _decode(value, declared, where)


# --------------------------------------------------------------------------- #
# The type, on its own
# --------------------------------------------------------------------------- #


def _unwrap(declared: Any) -> Any:
    """``declared`` without ``Annotated`` metadata."""

    while get_origin(declared) is typing.Annotated:
        declared = get_args(declared)[0]
    return declared


def _optional(declared: Any) -> Any | None:
    """``T`` for ``T | None`` (or ``Optional[T]``), else ``None``."""

    if get_origin(declared) in (Union, types.UnionType):
        members = [member for member in get_args(declared) if member is not _NONE_TYPE]
        if len(members) == 1 and len(get_args(declared)) == 2:
            return members[0]
    return None


def _refuse(where: str, message: str) -> StrategyConfigurationError:
    return StrategyConfigurationError(f"{where}: {message}")


def _enum_spelling(cls: type[Enum]) -> str:
    """How the encoder writes a member of ``cls``: ``value`` or ``name``."""

    if issubclass(cls, str | int):
        return "value"
    return "name"


def _require_declarable(declared: Any, where: str, seen: set[int]) -> None:
    declared = _unwrap(declared)
    if declared is Any or declared is None or declared is _NONE_TYPE:
        return
    inner = _optional(declared)
    if inner is not None:
        _require_declarable(inner, where, seen)
        return
    origin = get_origin(declared)
    if origin in (Union, types.UnionType):
        raise _refuse(
            where,
            f"{declared} is a union of two types besides None; its recorded form would not "
            "say which, so it cannot be read back. Declare one type.",
        )
    if origin is Literal:
        for member in get_args(declared):
            if type(member) not in (bool, int, str):
                raise _refuse(where, f"Literal member {member!r} is not a bool, int or str.")
        return
    if origin is list:
        (item,) = get_args(declared) or (Any,)
        _require_declarable(item, f"{where}[]", seen)
        return
    if origin is tuple:
        args = get_args(declared)
        if len(args) == 2 and args[1] is Ellipsis:
            _require_declarable(args[0], f"{where}[]", seen)
        else:
            for index, item in enumerate(args):
                _require_declarable(item, f"{where}[{index}]", seen)
        return
    if origin in (dict, Mapping):
        key, item = get_args(declared) or (str, Any)
        key = _unwrap(key)
        if not (
            key in _KEY_SCALARS
            or (isinstance(key, type) and issubclass(key, Enum) and issubclass(key, str | int))
        ):
            raise _refuse(
                where,
                f"a mapping keyed by {key} cannot be recorded: JSON keys a mapping by "
                "str, int, bool, float or a string or integer enum.",
            )
        _require_declarable(item, f"{where}[]", seen)
        return
    if isinstance(declared, type):
        if declared in _SCALARS:
            return
        if issubclass(declared, Enum):
            if _enum_spelling(declared) == "name":
                for member in declared:
                    if str(member) != f"{declared.__name__}.{member.name}":
                        raise _refuse(
                            where,
                            f"{declared.__name__} redefines how its members print, so the "
                            "name it is recorded by cannot be read back.",
                        )
            return
        if is_dataclass(declared):
            if id(declared) in seen:
                return
            seen.add(id(declared))
            hints = typing.get_type_hints(declared, include_extras=True)
            for item in fields(declared):
                if not item.init:
                    raise _refuse(
                        f"{where}.{item.name}",
                        f"{declared.__name__}.{item.name} is not an __init__ field, so the "
                        "dataclass cannot be rebuilt from its recorded fields.",
                    )
                _require_declarable(hints[item.name], f"{where}.{item.name}", seen)
            return
    raise _refuse(
        where,
        f"{declared!r} cannot be declared: the recorded form of its values cannot be read "
        "back as them. See alphalab.strategy.configuration for what can.",
    )


# --------------------------------------------------------------------------- #
# A value against its type
# --------------------------------------------------------------------------- #


def _found(value: Any) -> str:
    return f"{type(value).__name__} {value!r}"


def _require(value: Any, declared: Any, where: str) -> None:
    declared = _unwrap(declared)
    if declared is Any:
        _require_native(value, where)
        return
    if declared is None or declared is _NONE_TYPE:
        if value is not None:
            raise _refuse(where, f"expected None, found {_found(value)}")
        return
    inner = _optional(declared)
    if inner is not None:
        if value is not None:
            _require(value, inner, where)
        return
    origin = get_origin(declared)
    if origin is Literal:
        allowed = get_args(declared)
        if not any(type(value) is type(member) and value == member for member in allowed):
            raise _refuse(where, f"expected one of {allowed!r}, found {_found(value)}")
        return
    if origin is list:
        if type(value) is not list:
            raise _refuse(where, f"expected a list, found {_found(value)}")
        (item,) = get_args(declared) or (Any,)
        for index, entry in enumerate(value):
            _require(entry, item, f"{where}[{index}]")
        return
    if origin is tuple:
        if type(value) is not tuple:
            raise _refuse(where, f"expected a tuple, found {_found(value)}")
        args = get_args(declared)
        if len(args) == 2 and args[1] is Ellipsis:
            for index, entry in enumerate(value):
                _require(entry, args[0], f"{where}[{index}]")
            return
        if len(value) != len(args):
            raise _refuse(where, f"expected {len(args)} items, found {len(value)}")
        for index, (entry, item) in enumerate(zip(value, args, strict=True)):
            _require(entry, item, f"{where}[{index}]")
        return
    if origin in (dict, Mapping):
        if origin is dict and type(value) is not dict:
            raise _refuse(where, f"expected a dict, found {_found(value)}")
        if not isinstance(value, Mapping):
            raise _refuse(where, f"expected a mapping, found {_found(value)}")
        key, item = get_args(declared) or (str, Any)
        for entry_key, entry in value.items():
            _require(entry_key, key, f"{where} key")
            _require(entry, item, f"{where}[{entry_key!r}]")
        return
    if isinstance(declared, type) and is_dataclass(declared):
        if type(value) is not declared:
            raise _refuse(where, f"expected a {declared.__name__}, found {_found(value)}")
        hints = typing.get_type_hints(declared, include_extras=True)
        for item in fields(declared):
            _require(getattr(value, item.name), hints[item.name], f"{where}.{item.name}")
        return
    if isinstance(declared, type) and issubclass(declared, Enum):
        if type(value) is not declared:
            raise _refuse(where, f"expected a {declared.__name__} member, found {_found(value)}")
        return
    if type(value) is not declared:
        raise _refuse(where, f"expected {declared.__name__}, found {_found(value)}")
    if (declared is float and not math.isfinite(value)) or (
        declared is Decimal and not value.is_finite()
    ):
        raise _refuse(where, f"{value!r} is not finite, and has no recorded form")


def _require_native(value: Any, where: str) -> None:
    """A value declared ``Any`` must be one JSON reads back exactly."""

    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise _refuse(where, f"{value!r} is not finite, and has no recorded form")
        return
    if type(value) is list:
        for index, entry in enumerate(value):
            _require_native(entry, f"{where}[{index}]")
        return
    if type(value) is dict:
        for key, entry in value.items():
            if type(key) is not str:
                raise _refuse(where, f"a key {_found(key)} reads back as text; declare its type")
            _require_native(entry, f"{where}[{key!r}]")
        return
    raise _refuse(
        where,
        f"{_found(value)} is declared Any, and reads back as JSON reads it; declare its type",
    )


# --------------------------------------------------------------------------- #
# The recorded form back to the value
# --------------------------------------------------------------------------- #


def _decode(value: Any, declared: Any, where: str) -> Any:
    declared = _unwrap(declared)
    if declared is Any:
        return value
    if declared is None or declared is _NONE_TYPE:
        if value is not None:
            raise _refuse(where, f"expected null, found {_found(value)}")
        return None
    inner = _optional(declared)
    if inner is not None:
        return None if value is None else _decode(value, inner, where)
    origin = get_origin(declared)
    if origin is Literal:
        for member in get_args(declared):
            if type(value) is type(member) and value == member:
                return member
        raise _refuse(where, f"expected one of {get_args(declared)!r}, found {_found(value)}")
    if origin in (list, tuple):
        if type(value) is not list:
            raise _refuse(where, f"expected a list, found {_found(value)}")
        args = get_args(declared)
        if origin is list:
            (item,) = args or (Any,)
            return [_decode(entry, item, f"{where}[{index}]") for index, entry in enumerate(value)]
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(
                _decode(entry, args[0], f"{where}[{index}]") for index, entry in enumerate(value)
            )
        if len(value) != len(args):
            raise _refuse(where, f"expected {len(args)} items, found {len(value)}")
        return tuple(
            _decode(entry, item, f"{where}[{index}]")
            for index, (entry, item) in enumerate(zip(value, args, strict=True))
        )
    if origin in (dict, Mapping):
        if type(value) is not dict:
            raise _refuse(where, f"expected an object, found {_found(value)}")
        key, item = get_args(declared) or (str, Any)
        decoded = {
            _decode_key(entry_key, key, f"{where} key"): _decode(
                entry, item, f"{where}[{entry_key!r}]"
            )
            for entry_key, entry in value.items()
        }
        return decoded if origin is dict else types.MappingProxyType(decoded)
    if isinstance(declared, type) and is_dataclass(declared):
        return _decode_dataclass(value, declared, where)
    if isinstance(declared, type) and issubclass(declared, Enum):
        return _decode_enum(value, declared, where)
    return _decode_scalar(value, declared, where)


def _decode_dataclass(value: Any, declared: type, where: str) -> Any:
    if type(value) is not dict:
        raise _refuse(where, f"expected an object of {declared.__name__}, found {_found(value)}")
    hints = typing.get_type_hints(declared, include_extras=True)
    names = {item.name for item in fields(declared)}
    unknown = sorted(set(value) - names)
    if unknown:
        raise _refuse(where, f"{declared.__name__} has no field {unknown[0]!r}")
    arguments: dict[str, Any] = {}
    for item in fields(declared):
        if item.name in value:
            arguments[item.name] = _decode(
                value[item.name], hints[item.name], f"{where}.{item.name}"
            )
        elif item.default is MISSING and item.default_factory is MISSING:
            raise _refuse(
                f"{where}.{item.name}", f"missing, and {declared.__name__} has no default"
            )
        # Absent with a default: the dataclass supplies it -- a field added since
        # the payload was written reads back at its declared default.
    try:
        return declared(**arguments)
    except StrategyConfigurationError:
        raise
    except Exception as refused:  # the dataclass's own constraints refused it
        raise _refuse(
            where, f"{declared.__name__} refused the recorded value: {refused}"
        ) from refused


def _decode_enum(value: Any, declared: type[Enum], where: str) -> Enum:
    try:
        if _enum_spelling(declared) == "value":
            if isinstance(value, bool) or not isinstance(value, str | int):
                raise ValueError(value)
            return declared(value)
        prefix = f"{declared.__name__}."
        if not isinstance(value, str) or not value.startswith(prefix):
            raise ValueError(value)
        return declared[value.removeprefix(prefix)]
    except (ValueError, KeyError) as refused:
        raise _refuse(where, f"{_found(value)} is not a {declared.__name__} member") from refused


def _decode_scalar(value: Any, declared: type, where: str) -> Any:
    if declared is bool or declared is str:
        if type(value) is not declared:
            raise _refuse(where, f"expected {declared.__name__}, found {_found(value)}")
        return value
    if declared is int:
        if type(value) is not int:
            raise _refuse(where, f"expected int, found {_found(value)}")
        return value
    if declared is float:
        if type(value) is not float:
            raise _refuse(where, f"expected float, found {_found(value)}")
        return value
    if declared is Decimal:
        if type(value) is not str:
            raise _refuse(where, f"expected a Decimal's text, found {_found(value)}")
        try:
            decoded = Decimal(value)
        except InvalidOperation as refused:
            raise _refuse(where, f"{value!r} is not a Decimal") from refused
        if not decoded.is_finite() or str(decoded) != value:
            raise _refuse(where, f"{value!r} is not the text of a finite Decimal")
        return decoded
    if declared is UUID:
        if type(value) is not str:
            raise _refuse(where, f"expected a UUID's text, found {_found(value)}")
        try:
            decoded_id = UUID(value)
        except ValueError as refused:
            raise _refuse(where, f"{value!r} is not a UUID") from refused
        if str(decoded_id) != value:
            raise _refuse(where, f"{value!r} is not a UUID's canonical text")
        return decoded_id
    raise _refuse(where, f"{declared!r} cannot be declared")  # pragma: no cover - refused earlier


def _decode_key(value: Any, declared: Any, where: str) -> Any:
    """A mapping key from its JSON text: what ``json.dumps`` writes for each key type."""

    declared = _unwrap(declared)
    if type(value) is not str:
        raise _refuse(where, f"expected a key's text, found {_found(value)}")
    if declared is str:
        return value
    if declared is bool:
        if value not in ("true", "false"):
            raise _refuse(where, f"{value!r} is not a bool key")
        return value == "true"
    if isinstance(declared, type) and issubclass(declared, Enum):
        raw: Any = value
        if issubclass(declared, int):
            raw = _decode_key(value, int, where)
        return _decode_enum(raw, declared, where)
    try:
        decoded = int(value) if declared is int else float(value)
    except ValueError as refused:
        raise _refuse(where, f"{value!r} is not a {declared.__name__} key") from refused
    if (declared is int and str(decoded) != value) or (
        declared is float and repr(decoded) != value
    ):
        article = "an int" if declared is int else "a float"
        raise _refuse(where, f"{value!r} is not the text {article} key is written as")
    return decoded
