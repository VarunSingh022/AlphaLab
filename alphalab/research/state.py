"""Global immutable state container for the Research Engine."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from alphalab.research.bias import BiasReport
from alphalab.research.bootstrap import BootstrapReport
from alphalab.research.capacity import CapacityReport
from alphalab.research.cross_validation import WalkForwardReport
from alphalab.research.diagnostics import DiagnosticReport
from alphalab.research.events import ResearchEvent
from alphalab.research.montecarlo import MonteCarloReport
from alphalab.research.regime import RegimeReport
from alphalab.research.research import ResearchScore
from alphalab.research.sensitivity import RobustnessReport
from alphalab.research.stress import StressReport


@dataclass(frozen=True, slots=True)
class ResearchState:
    """Deterministic snapshot of an active research evaluation."""

    research_id: str
    strategy_id: str
    timestamp: float
    completed: bool = False
    bias_report: BiasReport | None = None
    walk_forward_report: WalkForwardReport | None = None
    monte_carlo_report: MonteCarloReport | None = None
    bootstrap_report: BootstrapReport | None = None
    robustness_report: RobustnessReport | None = None
    regime_report: RegimeReport | None = None
    capacity_report: CapacityReport | None = None
    stress_report: StressReport | None = None
    diagnostic_report: DiagnosticReport | None = None
    score: ResearchScore | None = None
    events: tuple[ResearchEvent, ...] = field(default_factory=tuple)
    metadata: Mapping[str, str] = field(default_factory=dict)
