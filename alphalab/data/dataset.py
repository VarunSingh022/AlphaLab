"""Immutable canonical storage units mapping arrays of models."""

from dataclasses import dataclass, field

from alphalab.data.feed import CanonicalRecord
from alphalab.data.metadata import DatasetMetadata
from alphalab.data.quality import QualityReport
from alphalab.data.schema import DatasetSchema


@dataclass(frozen=True, slots=True)
class Dataset:
    metadata: DatasetMetadata
    schema: DatasetSchema
    quality: QualityReport
    records: tuple[CanonicalRecord, ...] = field(default_factory=tuple)
