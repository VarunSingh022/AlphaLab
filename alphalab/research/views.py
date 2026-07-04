"""Pure queries exposing transparent Research State access."""

from collections.abc import Sequence

from alphalab.research.bias import BiasReport
from alphalab.research.capacity import CapacityReport
from alphalab.research.diagnostics import DiagnosticReport
from alphalab.research.research import ResearchScore
from alphalab.research.state import ResearchState
from alphalab.research.stress import StressReport


def overall_score(state: ResearchState) -> ResearchScore | None:
    """Returns the unified strategy grade."""
    return state.score


def bias_report(state: ResearchState) -> BiasReport | None:
    return state.bias_report


def capacity_report(state: ResearchState) -> CapacityReport | None:
    return state.capacity_report


def stress_report(state: ResearchState) -> StressReport | None:
    return state.stress_report


def diagnostic_report(state: ResearchState) -> DiagnosticReport | None:
    return state.diagnostic_report


def warnings(state: ResearchState) -> Sequence[str]:
    """Returns all critical structural warnings generated during research."""
    if state.diagnostic_report:
        return state.diagnostic_report.warnings
    return ()
