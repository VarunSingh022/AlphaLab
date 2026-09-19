"""Top-level Engine Facade orchestrating Universal Data."""

from collections.abc import Mapping, Sequence
from typing import Any

from alphalab.data.cleaning import CleaningPolicy
from alphalab.data.dataset import Dataset
from alphalab.data.exceptions import InvalidDataStateError
from alphalab.data.ingestion import parse_and_load
from alphalab.data.manager import DataManager
from alphalab.data.metadata import DatasetMetadata
from alphalab.data.registry import DatasetRegistry
from alphalab.data.state import UniversalDataState

__all__ = ["UniversalDataEngine"]


class UniversalDataEngine:
    """Facade for managing deterministic canonical data structures.

    This is the *state* surface: it holds a catalogue of dataset versions and
    the event log of what was done to them. :mod:`alphalab.api` is the
    stateless surface an application calls to turn a file into a dataset in the
    first place, and the two meet at :meth:`ingest`.
    """

    @staticmethod
    def initialize(engine_id: str) -> UniversalDataState:
        if not engine_id.strip():
            raise ValueError("Engine ID cannot be empty.")
        return UniversalDataState(engine_id=engine_id)

    @staticmethod
    def load(metadata: DatasetMetadata, raw_rows: Sequence[Mapping[str, Any]]) -> Dataset:
        """Parse raw vendor arrays into an immutable dataset carrying no provenance.

        For a dataset whose lineage is recorded, use
        :func:`alphalab.api.ingest_csv` or
        :func:`alphalab.api.ingest_rows`.
        """

        return parse_and_load(metadata, raw_rows)

    @staticmethod
    def ingest(state: UniversalDataState, dataset: Dataset, ts: float) -> UniversalDataState:
        """Register a dataset version, refusing to overwrite one already held."""

        return DataManager.ingest(state, dataset, ts)

    @staticmethod
    def clean(
        state: UniversalDataState, dataset_id: str, policy: CleaningPolicy, ts: float
    ) -> UniversalDataState:
        """Derive a cleaned version under an explicit policy.

        ``policy`` is required. See :mod:`alphalab.data.manager` for why there
        is no default one, and :data:`~alphalab.data.cleaning.REFUSE_EVERYTHING`
        for the position that nothing may be altered.
        """

        return DataManager.clean(state, dataset_id, policy, ts)

    @staticmethod
    def quality(state: UniversalDataState, dataset_id: str, ts: float) -> UniversalDataState:
        return DataManager.quality(state, dataset_id, ts)

    @staticmethod
    def convert(
        state: UniversalDataState, dataset_id: str, interval_sec: float, ts: float
    ) -> UniversalDataState:
        """Derive a resampled version, leaving the original in place."""

        return DataManager.convert_timeframe(state, dataset_id, interval_sec, ts)

    @staticmethod
    def catalog(state: UniversalDataState, dataset_id: str, ts: float) -> UniversalDataState:
        if dataset_id not in state.datasets:
            raise InvalidDataStateError(f"Dataset {dataset_id!r} not found.")
        dataset = state.datasets[dataset_id]
        return DatasetRegistry.catalog(state, dataset, ts)
