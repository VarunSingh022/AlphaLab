"""Immutable representation of a single computed feature value."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class FeatureValue:
    """A single computed value for a registered feature.

    Attributes:
        feature_id: Identifier of the feature this value belongs to.
        version: Definition version of the feature this value was computed against.
        asset_id: Asset the value applies to. Must be None for features registered
            with `asset_scoped=False` and non-None otherwise -- enforced by
            `feature_store.checks.check_asset_scope`.
        value: The computed value. Must match the feature's declared
            `FeatureValueType` -- enforced by `feature_store.checks.check_value_type`.
        timestamp: Unix timestamp the value was computed at.
    """

    feature_id: str
    version: int
    asset_id: str | None
    value: float | Decimal | int | bool | str
    timestamp: float


@dataclass(frozen=True, slots=True)
class KnownFeatureValue:
    """A written value and the instant the store knew it (v4.3, GAP-13).

    Attributes:
        value: The value written.
        known_at: The later of the instant it was computed at and the instant
            it was written: before either, no read could have returned it.
    """

    value: FeatureValue
    known_at: float
