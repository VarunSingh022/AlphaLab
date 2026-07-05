"""Shared type aliases."""

from collections.abc import Mapping

type MetadataValue = str | int | float | bool | None
type MetadataMapping = Mapping[str, MetadataValue]
