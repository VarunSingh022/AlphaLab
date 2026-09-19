"""Immutable domain events describing the Data Engine lifecycle."""

from dataclasses import dataclass

from alphalab.common.events import BaseEvent

__all__ = [
    "DataEvent",
    "DatasetCataloged",
    "DatasetCleaned",
    "DatasetIngested",
    "DatasetResampled",
    "QualityReportGenerated",
]


@dataclass(frozen=True, slots=True)
class DataEvent(BaseEvent):
    pass


@dataclass(frozen=True, slots=True)
class DatasetIngested(DataEvent):
    dataset_id: str
    record_count: int


@dataclass(frozen=True, slots=True)
class DatasetCleaned(DataEvent):
    """A cleaned version was derived from another.

    ``dataset_id`` is the version that was *read*; ``derived_dataset_id`` is
    the new version that was written. They are always different, because
    cleaning never overwrites -- that is what makes a version immutable, and
    what lets a study name the raw data and the cleaned data separately.
    """

    dataset_id: str
    records_removed: int
    derived_dataset_id: str = ""


@dataclass(frozen=True, slots=True)
class DatasetResampled(DataEvent):
    """A resampled version was derived from another."""

    dataset_id: str
    derived_dataset_id: str
    interval_seconds: float
    record_count: int


@dataclass(frozen=True, slots=True)
class QualityReportGenerated(DataEvent):
    dataset_id: str
    quality_score: float


@dataclass(frozen=True, slots=True)
class DatasetCataloged(DataEvent):
    dataset_id: str
    asset_class: str
