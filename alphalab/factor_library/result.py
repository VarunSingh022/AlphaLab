"""Immutable output of a single factor computation.

FactorResult's fields are deliberately named and typed to structurally satisfy
`alphalab.feature_store.protocol.FeatureValueProtocol` -- any FactorResult can be
passed directly to `alphalab.feature_store.adapter.FeatureValueAdapter.to_feature_value`
without Feature Store ever importing this package.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FactorResult:
    """A single computed factor value for one asset (or the whole market).

    Attributes:
        feature_id: Identifier matching a Feature Store registration, e.g.
            "momentum_20d". Factor Library does not register features itself --
            it only computes values against an identifier the caller supplies.
        version: Feature Store definition version this value was computed for.
        asset_id: Asset the value applies to, or None for market-wide factors.
        value: The computed factor score.
        timestamp: Unix timestamp the computation was performed at.
    """

    feature_id: str
    version: int
    asset_id: str | None
    value: float
    timestamp: float
