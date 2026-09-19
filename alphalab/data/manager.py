"""State transitions over the canonical dataset store.

The rule v3.1 establishes here: **a dataset version is never overwritten.**
Cleaning and resampling do not edit a dataset; they *derive* a new one with its
own identity, and both versions stay in state. That is what lets a study say it
used the raw file and a second study say it used the cleaned one, and lets a
reviewer compare them.

Before v3.1 both operations replaced ``state.datasets[dataset_id]`` in place, so
the raw data ceased to exist the moment anything was done to it and any evidence
naming that id became unverifiable -- the id still resolved, to different
numbers.

Cleaning takes a policy, and has no default one
-----------------------------------------------

:meth:`DataManager.clean` requires a :class:`~alphalab.data.cleaning.CleaningPolicy`.
It previously took none and silently applied "drop duplicate timestamps, drop
impossible bars", which is a policy -- a reasonable one, chosen by whoever wrote
the function rather than by whoever owns the data. ADR-0033 decision 10's rule
applies: a default either way is an invented policy presented as an
architectural one. :data:`~alphalab.data.cleaning.REFUSE_EVERYTHING` is the
named starting point for callers who want nothing altered.
"""

from collections.abc import Sequence
from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.data.cleaning import CleaningPolicy, TransformationRecord, clean_records
from alphalab.data.conversion import resample_bars
from alphalab.data.dataset import Dataset
from alphalab.data.events import (
    DatasetCleaned,
    DatasetIngested,
    DatasetResampled,
    QualityReportGenerated,
)
from alphalab.data.exceptions import DataValidationError, InvalidDataStateError
from alphalab.data.feed import Bar, CanonicalRecord
from alphalab.data.provenance import derive_transformed_version
from alphalab.data.quality import evaluate_bar_quality
from alphalab.data.state import UniversalDataState
from alphalab.data.validation import validate_records

__all__ = ["DataManager", "validate_dataset_ingestion"]


def validate_dataset_ingestion(state: UniversalDataState, dataset: Dataset) -> None:
    """Refuse a dataset that cannot enter ``state``.

    Two rules, both about identity rather than content: a dataset must be
    named, and a name already present must not be overwritten. The second is
    what makes a version in the engine's state immutable -- see
    :mod:`alphalab.data.provenance` for how the name itself is derived.

    Lives here rather than in :mod:`alphalab.data.validation` because it
    validates a *state transition*, not a record. That module answers "is this
    row usable?"; this answers "may this dataset enter this catalogue?".
    """

    if not dataset.metadata.dataset_id.strip():
        raise DataValidationError("Dataset ID cannot be empty.")
    if dataset.metadata.dataset_id in state.datasets:
        raise DataValidationError(f"Dataset {dataset.metadata.dataset_id} already exists.")


class DataManager:
    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def ingest(state: UniversalDataState, dataset: Dataset, ts: float) -> UniversalDataState:
        validate_dataset_ingestion(state, dataset)

        ds_id = dataset.metadata.dataset_id
        evt = DatasetIngested(DataManager._create_id(), ts, ds_id, len(dataset.records))

        return replace(
            state,
            datasets=state.datasets.set(ds_id, dataset),
            metadata=state.metadata.set(ds_id, dataset.metadata),
            schemas=state.schemas.set(ds_id, dataset.schema),
            events=state.events.append(evt),
        )

    @staticmethod
    def clean(
        state: UniversalDataState, dataset_id: str, policy: CleaningPolicy, ts: float
    ) -> UniversalDataState:
        """Derive a cleaned version of a dataset under ``policy``.

        The original version is left exactly as it was. When the policy changes
        nothing -- because there was nothing to change -- no new version is
        derived and the state is returned unchanged, because a dataset nothing
        was done to is the dataset it came from.

        Raises:
            InvalidDataStateError: If ``dataset_id`` is not in state.
            DataQualityError: If ``policy`` refuses a defect that is present.
        """

        dataset = DataManager._require(state, dataset_id)

        findings = validate_records(dataset.records, dataset.metadata.frequency)
        outcome = clean_records(dataset.records, policy, findings)
        if not outcome.transformations:
            return state

        derived = DataManager._derive(dataset, outcome.records, outcome.transformations)
        removed = len(dataset.records) - len(outcome.records)
        evt = DatasetCleaned(
            DataManager._create_id(),
            ts,
            dataset_id,
            removed,
            derived.metadata.dataset_id,
        )
        return DataManager._store(state, dataset_id, derived, evt)

    @staticmethod
    def quality(state: UniversalDataState, dataset_id: str, ts: float) -> UniversalDataState:
        """Measure a dataset and record the report, changing the dataset itself not at all.

        Quality is a *measurement of* a version, not part of it. Writing the
        report back onto the dataset -- which this did before v3.1 -- replaced
        the stored object for an immutable version, so two measurements of one
        dataset produced two dataset objects under one id.
        """

        dataset = DataManager._require(state, dataset_id)
        bars = tuple(record for record in dataset.records if isinstance(record, Bar))
        report = evaluate_bar_quality(dataset_id, bars)

        evt = QualityReportGenerated(DataManager._create_id(), ts, dataset_id, report.quality_score)
        return replace(
            state,
            quality_reports=state.quality_reports.set(dataset_id, report),
            events=state.events.append(evt),
        )

    @staticmethod
    def convert_timeframe(
        state: UniversalDataState, dataset_id: str, interval_sec: float, ts: float
    ) -> UniversalDataState:
        """Derive a resampled version of a dataset, leaving the original in place."""

        dataset = DataManager._require(state, dataset_id)
        bars = tuple(record for record in dataset.records if isinstance(record, Bar))
        resampled = resample_bars(bars, interval_sec)

        transformation = TransformationRecord(
            operation="resample_bars",
            rows_affected=len(bars),
            reason=f"aggregated into {interval_sec:g}-second buckets",
        )
        derived = DataManager._derive(dataset, resampled, (transformation,))
        evt = DatasetResampled(
            DataManager._create_id(),
            ts,
            dataset_id,
            derived.metadata.dataset_id,
            interval_sec,
            len(resampled),
        )
        return DataManager._store(state, dataset_id, derived, evt)

    # -- helpers ---------------------------------------------------------- #

    @staticmethod
    def _require(state: UniversalDataState, dataset_id: str) -> Dataset:
        if dataset_id not in state.datasets:
            raise InvalidDataStateError(f"Dataset {dataset_id!r} not found.")
        return state.datasets[dataset_id]

    @staticmethod
    def _derive(
        dataset: Dataset,
        records: Sequence[CanonicalRecord],
        transformations: tuple[TransformationRecord, ...],
    ) -> Dataset:
        """Build the derived version, carrying provenance forward where there is any."""

        carried = tuple(records)
        parent_id = dataset.metadata.dataset_id
        derived_id = derive_transformed_version(parent_id, transformations)

        provenance = dataset.provenance
        if provenance is not None:
            provenance = replace(
                provenance,
                dataset_version=derived_id,
                transformations=provenance.transformations + transformations,
            )

        stamps = [record.timestamp for record in carried]
        metadata = replace(
            dataset.metadata,
            dataset_id=derived_id,
            start_timestamp=min(stamps) if stamps else dataset.metadata.start_timestamp,
            end_timestamp=max(stamps) if stamps else dataset.metadata.end_timestamp,
        )
        bars = tuple(record for record in carried if isinstance(record, Bar))
        return Dataset(
            metadata=metadata,
            schema=dataset.schema,
            quality=evaluate_bar_quality(derived_id, bars),
            records=carried,
            provenance=provenance,
            quality_detail=None,
        )

    @staticmethod
    def _store(
        state: UniversalDataState,
        parent_id: str,
        derived: Dataset,
        event: DatasetCleaned | DatasetResampled,
    ) -> UniversalDataState:
        """Add a derived version, refusing to overwrite one already present."""

        derived_id = derived.metadata.dataset_id
        if derived_id in state.datasets:
            return replace(state, events=state.events.append(event))

        return replace(
            state,
            datasets=state.datasets.set(derived_id, derived),
            metadata=state.metadata.set(derived_id, derived.metadata),
            schemas=state.schemas.set(derived_id, derived.schema),
            lineage=state.lineage.set(derived_id, parent_id),
            events=state.events.append(event),
        )
