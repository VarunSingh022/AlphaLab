"""Shared metadata structures."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.types import MetadataMapping, MetadataValue


def copy_metadata(metadata: MetadataMapping) -> dict[str, MetadataValue]:
    """Return a validated shallow copy of metadata."""

    copied = dict(metadata)
    for key, value in copied.items():
        if not key:
            raise AlphaLabValidationError("metadata keys cannot be empty")
        if not isinstance(value, str | int | float | bool | None):
            raise AlphaLabValidationError("metadata values must be scalar")
    return copied


@dataclass(frozen=True, slots=True)
class Metadata:
    """Immutable metadata container."""

    values: Mapping[str, MetadataValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", copy_metadata(self.values))
