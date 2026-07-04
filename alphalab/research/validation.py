"""Strict validation rules for research payloads."""

from alphalab.research.exceptions import ResearchValidationError
from alphalab.research.protocol import ResearchPayload


def validate_payload(payload: ResearchPayload) -> None:
    """Ensures a research payload is structurally valid for scientific analysis."""
    if not payload.strategy_id.strip():
        raise ResearchValidationError("Strategy ID cannot be empty.")
    if payload.aum <= 0:
        raise ResearchValidationError("AUM must be strictly positive.")
    if len(payload.returns) != len(payload.market_regimes):
        raise ResearchValidationError("Returns and Market Regimes arrays must be equal length.")
