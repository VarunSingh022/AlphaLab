"""Orchestration of pure data transformation logic to state transitions."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.data.cleaning import remove_duplicates, remove_invalid_ohlc
from alphalab.data.conversion import resample_bars
from alphalab.data.dataset import Dataset
from alphalab.data.events import DatasetCleaned, DatasetIngested, QualityReportGenerated
from alphalab.data.exceptions import InvalidDataStateError
from alphalab.data.feed import Bar
from alphalab.data.quality import evaluate_bar_quality
from alphalab.data.state import UniversalDataState
from alphalab.data.validation import validate_dataset_ingestion


class DataManager:
    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def ingest(state: UniversalDataState, dataset: Dataset, ts: float) -> UniversalDataState:
        validate_dataset_ingestion(state, dataset)

        ds_id = dataset.metadata.dataset_id
        new_ds = dict(state.datasets)
        new_ds[ds_id] = dataset

        new_meta = dict(state.metadata)
        new_meta[ds_id] = dataset.metadata

        evt = DatasetIngested(DataManager._create_id(), ts, ds_id, len(dataset.records))

        return replace(state, datasets=new_ds, metadata=new_meta, events=(*state.events, evt))

    @staticmethod
    def clean(state: UniversalDataState, dataset_id: str, ts: float) -> UniversalDataState:
        if dataset_id not in state.datasets:
            raise InvalidDataStateError("Dataset not found.")

        dataset = state.datasets[dataset_id]

        # We only support Bar cleaning in this subset for standard evaluation
        if not all(isinstance(r, Bar) for r in dataset.records):
            return state

        bars = tuple(r for r in dataset.records if isinstance(r, Bar))
        start_count = len(bars)

        bars = remove_duplicates(bars)
        bars = remove_invalid_ohlc(bars)

        removed = start_count - len(bars)
        cleaned_ds = replace(dataset, records=bars)

        new_ds = dict(state.datasets)
        new_ds[dataset_id] = cleaned_ds

        evt = DatasetCleaned(DataManager._create_id(), ts, dataset_id, removed)
        return replace(state, datasets=new_ds, events=(*state.events, evt))

    @staticmethod
    def quality(state: UniversalDataState, dataset_id: str, ts: float) -> UniversalDataState:
        if dataset_id not in state.datasets:
            raise InvalidDataStateError("Dataset not found.")

        dataset = state.datasets[dataset_id]
        bars = tuple(r for r in dataset.records if isinstance(r, Bar))

        report = evaluate_bar_quality(dataset_id, bars)

        new_reports = dict(state.quality_reports)
        new_reports[dataset_id] = report

        updated_ds = replace(dataset, quality=report)
        new_ds = dict(state.datasets)
        new_ds[dataset_id] = updated_ds

        evt = QualityReportGenerated(DataManager._create_id(), ts, dataset_id, report.quality_score)

        return replace(
            state, quality_reports=new_reports, datasets=new_ds, events=(*state.events, evt)
        )

    @staticmethod
    def convert_timeframe(
        state: UniversalDataState, dataset_id: str, interval_sec: float, ts: float
    ) -> UniversalDataState:
        if dataset_id not in state.datasets:
            raise InvalidDataStateError("Dataset not found.")

        dataset = state.datasets[dataset_id]
        bars = tuple(r for r in dataset.records if isinstance(r, Bar))

        resampled = resample_bars(bars, interval_sec)

        updated_ds = replace(dataset, records=resampled)
        new_ds = dict(state.datasets)
        new_ds[dataset_id] = updated_ds

        return replace(state, datasets=new_ds)
