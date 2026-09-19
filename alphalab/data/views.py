"""Pure queries exposing transparent Universal Data State access."""

from collections.abc import Sequence

from alphalab.data.catalog import CatalogRecord
from alphalab.data.dataset import Dataset
from alphalab.data.metadata import DatasetMetadata
from alphalab.data.provenance import DatasetProvenance
from alphalab.data.quality import QualityReport
from alphalab.data.schema import DatasetSchema
from alphalab.data.state import UniversalDataState

__all__ = [
    "catalog_summary",
    "dataset_lineage",
    "dataset_provenance",
    "dataset_summary",
    "metadata_view",
    "quality_report",
    "schema_report",
]


def dataset_summary(state: UniversalDataState) -> Sequence[Dataset]:
    return tuple(state.datasets.values())


def quality_report(state: UniversalDataState, dataset_id: str) -> QualityReport | None:
    return state.quality_reports.get(dataset_id)


def schema_report(state: UniversalDataState, dataset_id: str) -> DatasetSchema | None:
    return state.schemas.get(dataset_id)


def catalog_summary(state: UniversalDataState) -> Sequence[CatalogRecord]:
    return tuple(state.catalog.values())


def metadata_view(state: UniversalDataState, dataset_id: str) -> DatasetMetadata | None:
    return state.metadata.get(dataset_id)


def dataset_provenance(state: UniversalDataState, dataset_id: str) -> DatasetProvenance | None:
    """How a held dataset came to exist, or ``None`` if it records none."""

    dataset = state.datasets.get(dataset_id)
    return None if dataset is None else dataset.provenance


def dataset_lineage(state: UniversalDataState, dataset_id: str) -> tuple[str, ...]:
    """The chain of versions a dataset was derived from, newest first.

    ``(dataset_id,)`` for a dataset read from a source: it is the root of its
    own lineage. ``(cleaned, raw)`` for a cleaned dataset, and so on back to
    whichever version was ingested.
    """

    chain = [dataset_id]
    seen = {dataset_id}
    cursor = state.lineage.get(dataset_id)
    while cursor is not None and cursor not in seen:
        chain.append(cursor)
        seen.add(cursor)
        cursor = state.lineage.get(cursor)
    return tuple(chain)
