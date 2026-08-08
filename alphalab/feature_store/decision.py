"""Immutable decision model generated after a feature value write attempt."""

from dataclasses import dataclass, field

from alphalab.feature_store.violations import FeatureViolation


@dataclass(frozen=True, slots=True)
class FeatureWriteDecision:
    """Deterministic outcome of an attempted feature value write.

    Attributes:
        decision_id: Unique identifier for this decision.
        timestamp: Unix timestamp the decision was made at.
        feature_id: Identifier of the feature the write targeted.
        version: Definition version the write was evaluated against.
        asset_id: Asset the value applied to, if any.
        approved: True if the value was accepted and written to state.
        reason: Human-readable summary of the outcome.
        violations: Every check that failed, empty when approved.
    """

    decision_id: str
    timestamp: float
    feature_id: str
    version: int
    asset_id: str | None
    approved: bool
    reason: str
    violations: tuple[FeatureViolation, ...] = field(default_factory=tuple)
