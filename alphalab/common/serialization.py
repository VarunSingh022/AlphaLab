"""Shared serialization helpers."""

from dataclasses import asdict, is_dataclass
from typing import Any, cast

from alphalab.common.exceptions import AlphaLabSerializationError


def dataclass_to_dict(instance: object) -> dict[str, Any]:
    """Serialize a dataclass instance to a dictionary."""

    if isinstance(instance, type) or not is_dataclass(instance):
        raise AlphaLabSerializationError("instance must be a dataclass instance")
    data: dict[str, Any] = asdict(cast(Any, instance))
    return data
