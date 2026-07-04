"""Immutable registry for tracking accepted Datasets."""

from dataclasses import dataclass

from alphalab.data.metadata import DatasetMetadata
from alphalab.data.quality import QualityReport


@dataclass(frozen=True, slots=True)
class CatalogRecord:
    metadata: DatasetMetadata
    quality: QualityReport
    record_count: int