"""Global immutable state container for the Universal Data Engine.

The indexes and the event log use the canonical containers from
:mod:`alphalab.common`, for the reason v2.1 and v2.2 introduced them. Until
v2.17 every mutator here rebuilt a whole ``dict`` and a whole ``tuple`` per
transition, so ``N`` transitions copied ``O(N^2)`` entries -- ADR-0032 category C
finding 1, closed by ADR-0034. Both containers are immutable, both define value
equality, and a ``PersistentMap`` iterates in first-insertion order of the keys
still present, which is what a ``dict`` does.
"""

from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.persistent_map import PersistentMap
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
    datasets: PersistentMap[str, Dataset] = field(default_factory=PersistentMap)
    catalog: PersistentMap[str, CatalogRecord] = field(default_factory=PersistentMap)
    quality_reports: PersistentMap[str, QualityReport] = field(default_factory=PersistentMap)
    schemas: PersistentMap[str, DatasetSchema] = field(default_factory=PersistentMap)
    metadata: PersistentMap[str, DatasetMetadata] = field(default_factory=PersistentMap)

    #: Derived version to the version it was derived from.
    #:
    #: Cleaning and resampling never overwrite a dataset, so both versions live
    #: in ``datasets`` and this is what says which came from which. A dataset
    #: read from a source has no entry, which is the honest statement that it
    #: is the root of its own lineage rather than a derivative of something.
    lineage: PersistentMap[str, str] = field(default_factory=PersistentMap)

    events: AppendOnlyLog[DataEvent] = field(default_factory=AppendOnlyLog)
