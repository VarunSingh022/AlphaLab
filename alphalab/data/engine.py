"""Top-level Engine Facade orchestrating Universal Data."""

from collections.abc import Mapping, Sequence
from typing import Any

from alphalab.data.dataset import Dataset
from alphalab.data.ingestion import parse_and_load
from alphalab.data.manager import DataManager
from alphalab.data.metadata import DatasetMetadata
from alphalab.data.registry import DatasetRegistry
from alphalab.data.state import UniversalDataState


class UniversalDataEngine:
    """Facade for managing deterministic canonical data structures."""

    @staticmethod
    def initialize(engine_id: str) -> UniversalDataState:
        if not engine_id.strip():
            raise ValueError("Engine ID cannot be empty.")
        return UniversalDataState(engine_id=engine_id)

    @staticmethod
    def load(metadata: DatasetMetadata, raw_rows: Sequence[Mapping[str, Any]]) -> Dataset:
        """Parses raw vendor arrays into Immutable Datasets."""
        return parse_and_load(metadata, raw_rows)

    @staticmethod
    def ingest(state: UniversalDataState, dataset: Dataset, ts: float) -> UniversalDataState:
        """Registers the dataset into the active working state."""
        return DataManager.ingest(state, dataset, ts)

    @staticmethod
    def clean(state: UniversalDataState, dataset_id: str, ts: float) -> UniversalDataState:
        return DataManager.clean(state, dataset_id, ts)

    @staticmethod
    def quality(state: UniversalDataState, dataset_id: str, ts: float) -> UniversalDataState:
        return DataManager.quality(state, dataset_id, ts)

    @staticmethod
    def convert(
        state: UniversalDataState, dataset_id: str, interval_sec: float, ts: float
    ) -> UniversalDataState:
        return DataManager.convert_timeframe(state, dataset_id, interval_sec, ts)

    @staticmethod
    def catalog(state: UniversalDataState, dataset_id: str, ts: float) -> UniversalDataState:
        if dataset_id not in state.datasets:
            raise ValueError("Dataset not found.")
        dataset = state.datasets[dataset_id]
        return DatasetRegistry.catalog(state, dataset, ts)
