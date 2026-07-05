"""Immutable domain events describing the Data Engine lifecycle."""

from dataclasses import dataclass

from alphalab.common.events import BaseEvent


@dataclass(frozen=True, slots=True)
class DataEvent(BaseEvent):
    pass


@dataclass(frozen=True, slots=True)
class DatasetIngested(DataEvent):
    dataset_id: str
    record_count: int


@dataclass(frozen=True, slots=True)
class DatasetCleaned(DataEvent):
    dataset_id: str
    records_removed: int


@dataclass(frozen=True, slots=True)
class QualityReportGenerated(DataEvent):
    dataset_id: str
    quality_score: float


@dataclass(frozen=True, slots=True)
class DatasetCataloged(DataEvent):
    dataset_id: str
    asset_class: str
