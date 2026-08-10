"""Shipping and vessel-tracking-derived metrics.

Modeled on real AIS (Automatic Identification System) vessel-tracking alt-data:
port congestion indices, vessel counts, and cargo volumes, used as leading
indicators for trade flows, commodity supply, and shipping-exposed equities.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto

from alphalab.alt_data.exceptions import AltDataInputError
from alphalab.alt_data.provenance import DataProvenance


class ShippingMetricType(Enum):
    """Categories of shipping-derived metrics this package models."""

    PORT_CONGESTION_INDEX = auto()
    VESSEL_COUNT = auto()
    CARGO_VOLUME = auto()


@dataclass(frozen=True, slots=True)
class ShippingObservation:
    """A single shipping-derived metric observation.

    Attributes:
        identifier: What this observation concerns -- a company asset_id, a port
            code, or a route/commodity code (e.g. "PORT_LA", "BALTIC_DRY"),
            depending on metric_type and vendor.
        metric_type: Which kind of shipping-derived signal this is.
        reference_period: Unix timestamp of the period this observation describes.
        release_date: Unix timestamp this observation became available.
        value: The metric value.
        source: Vendor and reliability information.
    """

    identifier: str
    metric_type: ShippingMetricType
    reference_period: float
    release_date: float
    value: Decimal
    source: DataProvenance

    def __post_init__(self) -> None:
        if self.release_date < self.reference_period:
            raise AltDataInputError("release_date cannot be before reference_period.")
        if self.metric_type in (
            ShippingMetricType.VESSEL_COUNT,
            ShippingMetricType.CARGO_VOLUME,
        ) and self.value < Decimal("0"):
            raise AltDataInputError(
                f"{self.metric_type.name} cannot be negative, got {self.value}."
            )
