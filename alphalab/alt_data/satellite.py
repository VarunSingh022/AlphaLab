"""Satellite imagery-derived metrics.

Two of the best-known real satellite alt-data categories: retail parking lot
traffic (a foot-traffic/revenue proxy, pioneered commercially by vendors like RS
Metrics and Orbital Insight) and storage tank fill levels (a crude oil/refined
product inventory proxy from measuring floating-roof tank shadows). Processing lag
between image capture and a delivered metric is typically real and non-trivial --
days to weeks, not the near-instant availability of a market data tick.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto

from alphalab.alt_data.exceptions import AltDataInputError
from alphalab.alt_data.provenance import DataProvenance


class SatelliteMetricType(Enum):
    """Categories of satellite-derived metrics this package models."""

    PARKING_LOT_TRAFFIC = auto()
    STORAGE_TANK_FILL_LEVEL = auto()
    CROP_HEALTH_INDEX = auto()
    CONSTRUCTION_ACTIVITY = auto()


@dataclass(frozen=True, slots=True)
class SatelliteObservation:
    """A single satellite-derived metric observation for one asset.

    Attributes:
        asset_id: Identifier of the asset this observation concerns.
        metric_type: Which kind of satellite-derived signal this is.
        capture_date: Unix timestamp the underlying image was captured.
        release_date: Unix timestamp the processed metric became available. Real
            satellite data typically has meaningful processing lag after capture.
        value: The metric value. Interpretation depends on metric_type -- e.g. a
            0-1 fill fraction for STORAGE_TANK_FILL_LEVEL, or an index value for
            PARKING_LOT_TRAFFIC.
        source: Vendor and reliability information.
    """

    asset_id: str
    metric_type: SatelliteMetricType
    capture_date: float
    release_date: float
    value: Decimal
    source: DataProvenance

    def __post_init__(self) -> None:
        if self.release_date < self.capture_date:
            raise AltDataInputError("release_date cannot be before capture_date.")

    @property
    def reference_period(self) -> float:
        """Alias for `capture_date`, satisfying PointInTimeRecord."""
        return self.capture_date
