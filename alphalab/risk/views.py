"""Pure query functions exposing transparent Risk Engine access."""

from collections.abc import Sequence

from alphalab.risk.decision import RiskDecision
from alphalab.risk.exposure import ExposureStatus
from alphalab.risk.limits import RiskLimits
from alphalab.risk.margin import MarginStatus
from alphalab.risk.models import RiskViolation
from alphalab.risk.state import RiskState


def active_limits(state: RiskState) -> RiskLimits:
    return state.active_limits


def latest_decision(state: RiskState) -> RiskDecision | None:
    if not state.history:
        return None
    return state.history[-1]


def risk_history(state: RiskState) -> Sequence[RiskDecision]:
    return state.history


def margin_status(state: RiskState) -> MarginStatus:
    return state.margin


def current_exposure(state: RiskState) -> ExposureStatus:
    return state.exposure


def violations(state: RiskState) -> Sequence[RiskViolation]:
    """Extracts all violations generated in the history of the current state."""
    return tuple(v for decision in state.history for v in decision.violations)
