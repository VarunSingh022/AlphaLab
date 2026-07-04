"""Immutable source metadata representations."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.data.symbols import DataAssetClass
from alphalab.data.time import TimeFrequency


@dataclass(frozen=True, slots=True)
class DatasetMetadata:
    dataset_id: str
    source_name: str
    asset_class: DataAssetClass
    frequency: TimeFrequency
    start_timestamp: float
    end_timestamp: float
    timezone: str = "UTC"
    metadata: Mapping[str, str] = field(default_factory=dict)