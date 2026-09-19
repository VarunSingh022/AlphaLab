"""Immutable source metadata representations."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.data.symbols import DataAssetClass
from alphalab.data.time import TimeFrequency

__all__ = ["DatasetMetadata"]


@dataclass(frozen=True, slots=True)
class DatasetMetadata:
    """What a dataset is, independently of what is in it.

    ``timezone`` carries a ``"UTC"`` default for the v1 construction path, which
    predates v3.1 and has no zone to be told. On the ingestion path it is always
    set explicitly from the zone the timestamps were resolved in, and it always
    equals :attr:`~alphalab.data.provenance.DatasetProvenance.timezone_name` --
    the pipeline is the only writer of both, and
    ``tests/regression/test_dataset_provenance_and_immutability.py`` pins the
    agreement so the two can never come to disagree about one dataset.
    """

    dataset_id: str
    source_name: str
    asset_class: DataAssetClass
    frequency: TimeFrequency
    start_timestamp: float
    end_timestamp: float
    timezone: str = "UTC"
    metadata: Mapping[str, str] = field(default_factory=dict)
