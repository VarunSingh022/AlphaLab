"""Immutable violation record produced by feature validation checks."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FeatureViolation:
    """Deterministic description of a single failed feature validation check.

    Attributes:
        rule: Short machine-readable name of the check that failed.
        description: Human-readable explanation of the failure.
        severity: One of "LOW", "MEDIUM", "HIGH".
        feature_id: Identifier of the feature the violation applies to.
        current_value: String representation of the value that failed the check.
        allowed_value: String representation of what was expected instead.
    """

    rule: str
    description: str
    severity: str
    feature_id: str
    current_value: str
    allowed_value: str
