"""Deterministic serialization utilities using strict JSON."""

import json
from dataclasses import is_dataclass
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.serialization import dataclass_to_dict
from alphalab.persistence.exceptions import SerializationError


class DeterministicEncoder(json.JSONEncoder):
    """Strict JSON encoder handling Decimals, dataclasses, logs and enums.

    Every supported type is handled by an explicit branch. Anything else raises
    :class:`SerializationError` rather than being coerced with ``str()``: a
    silent stringify turns an unserializable value into a plausible-looking
    payload that cannot be read back, which is exactly how append-only histories
    came to be persisted as ``"AppendOnlyLog([...])"``.
    """

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        if is_dataclass(obj) and not isinstance(obj, type):
            return dataclass_to_dict(obj)
        if isinstance(obj, AppendOnlyLog):
            # A log serializes as the sequence it stands in for. Reached only
            # when a bare log is serialized; inside a dataclass, the conversion
            # has already happened in dataclass_to_dict.
            return list(obj)
        if isinstance(obj, Enum):
            # StrEnum members never reach here (json encodes them natively);
            # plain Enum members serialize by name, as they always have.
            return str(obj)
        if isinstance(obj, UUID):
            # Typed identifiers (OrderId, FillId, ...) wrap a UUID.
            return str(obj)
        raise SerializationError(
            f"No deterministic JSON representation for {type(obj).__module__}."
            f"{type(obj).__name__}. Add an explicit branch to DeterministicEncoder "
            f"rather than relying on a string fallback."
        )


def serialize(obj: Any) -> str:
    """Serializes an object into a strict, deterministic, sorted JSON string."""
    try:
        return json.dumps(obj, cls=DeterministicEncoder, sort_keys=True, separators=(",", ":"))
    except Exception as e:
        raise SerializationError(f"Failed to serialize object: {e}") from e


def deserialize(payload: str) -> Any:
    """Deserializes a strict JSON string back into primitive Python structures."""
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        raise SerializationError(f"Failed to deserialize payload. Corrupt JSON: {e}") from e
