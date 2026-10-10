"""Pure queries exposing transparent Research State access."""

from collections.abc import Mapping, Sequence

from alphalab.research.bias import BiasReport
from alphalab.research.capacity import CapacityReport
from alphalab.research.diagnostics import DiagnosticReport
from alphalab.research.state import ResearchState
from alphalab.research.stress import StressReport


def research_metrics_of(state: ResearchState) -> Mapping[str, float]:
    """Every measurement a completed evaluation made; empty before it completes.

    Replaced ``overall_score`` in v3.12 (ledger RES-001): there is no grade.
    """
    return state.metrics


def bias_report(state: ResearchState) -> BiasReport | None:
    return state.bias_report


def capacity_report(state: ResearchState) -> CapacityReport | None:
    return state.capacity_report


def stress_report(state: ResearchState) -> StressReport | None:
    return state.stress_report


def diagnostic_report(state: ResearchState) -> DiagnosticReport | None:
    return state.diagnostic_report


def warnings(state: ResearchState) -> Sequence[str]:
    """Returns every finding: each number that crossed a bound the policy stated."""
    if state.diagnostic_report:
        return state.diagnostic_report.warnings
    return ()
