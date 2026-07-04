"""Immutable definitions mapping structure expectations."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DatasetSchema:
    fields: tuple[str, ...]
    data_type: str  # e.g., 'BAR', 'TRADE'