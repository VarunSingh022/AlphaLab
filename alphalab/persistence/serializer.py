"""Deterministic serialization utilities using strict JSON."""

import json
from dataclasses import is_dataclass
from decimal import Decimal
from typing import Any

from alphalab.common.serialization import dataclass_to_dict
from alphalab.persistence.exceptions import SerializationError


class DeterministicEncoder(json.JSONEncoder):
    """Strict JSON encoder handling Decimals and dataclasses deterministically."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        if is_dataclass(obj) and not isinstance(obj, type):
            return dataclass_to_dict(obj)
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


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
