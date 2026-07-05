"""Global immutable state container for the Universal Data Engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.data.catalog import CatalogRecord
from alphalab.data.dataset import Dataset
from alphalab.data.events import DataEvent
from alphalab.data.metadata import DatasetMetadata
from alphalab.data.quality import QualityReport
from alphalab.data.schema import DatasetSchema


@dataclass(frozen=True, slots=True)
class UniversalDataState:
    """Deterministic snapshot of the Universal Data Engine."""

    engine_id: str
    datasets: Mapping[str, Dataset] = field(default_factory=dict)
    catalog: Mapping[str, CatalogRecord] = field(default_factory=dict)
    quality_reports: Mapping[str, QualityReport] = field(default_factory=dict)
    schemas: Mapping[str, DatasetSchema] = field(default_factory=dict)
    metadata: Mapping[str, DatasetMetadata] = field(default_factory=dict)
    events: tuple[DataEvent, ...] = field(default_factory=tuple)
