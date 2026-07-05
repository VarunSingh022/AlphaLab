"""Pure queries exposing transparent Universal Data State access."""

from collections.abc import Sequence

from alphalab.data.catalog import CatalogRecord
from alphalab.data.dataset import Dataset
from alphalab.data.metadata import DatasetMetadata
from alphalab.data.quality import QualityReport
from alphalab.data.schema import DatasetSchema
from alphalab.data.state import UniversalDataState


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
