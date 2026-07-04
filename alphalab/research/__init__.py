"""AlphaLab Research Engine."""

from alphalab.research.adapter import ResearchAdapter
from alphalab.research.bias import BiasReport, detect_bias
from alphalab.research.bootstrap import BootstrapReport, bootstrap_statistics
from alphalab.research.capacity import CapacityReport, estimate_capacity
from alphalab.research.cross_validation import WalkForwardReport, walk_forward_analysis
from alphalab.research.diagnostics import DiagnosticReport, generate_diagnostics
from alphalab.research.engine import ResearchEngine
from alphalab.research.events import (
    AnalysisCompleted,
    BiasDetected,
    DiagnosticsGenerated,
    ResearchCompleted,
    ResearchEvent,
    ResearchStarted,
)
from alphalab.research.exceptions import (
    InvalidResearchStateError,
    ResearchError,
    ResearchValidationError,
)
from alphalab.research.metrics import (
    calculate_cagr,
    calculate_max_drawdown,
    calculate_sharpe,
    calculate_volatility,
)
from alphalab.research.montecarlo import MonteCarloReport, monte_carlo_simulation
from alphalab.research.protocol import ResearchPayload, ResearchProtocol, TradePayload
from alphalab.research.regime import RegimeReport, analyze_regimes
from alphalab.research.research import ResearchScore, compute_overall_score
from alphalab.research.sensitivity import RobustnessReport, parameter_robustness
from alphalab.research.state import ResearchState
from alphalab.research.stress import StressReport, apply_stress_tests
from alphalab.research.validation import validate_payload
from alphalab.research.views import (
    bias_report,
    capacity_report,
    diagnostic_report,
    overall_score,
    stress_report,
    warnings,
)

__all__ = [
    "AnalysisCompleted",
    "BiasDetected",
    "BiasReport",
    "BootstrapReport",
    "CapacityReport",
    "DiagnosticReport",
    "DiagnosticsGenerated",
    "InvalidResearchStateError",
    "MonteCarloReport",
    "RegimeReport",
    "ResearchAdapter",
    "ResearchCompleted",
    "ResearchEngine",
    "ResearchError",
    "ResearchEvent",
    "ResearchPayload",
    "ResearchProtocol",
    "ResearchScore",
    "ResearchStarted",
    "ResearchState",
    "ResearchValidationError",
    "RobustnessReport",
    "StressReport",
    "TradePayload",
    "WalkForwardReport",
    "analyze_regimes",
    "apply_stress_tests",
    "bias_report",
    "bootstrap_statistics",
    "calculate_cagr",
    "calculate_max_drawdown",
    "calculate_sharpe",
    "calculate_volatility",
    "capacity_report",
    "compute_overall_score",
    "detect_bias",
    "diagnostic_report",
    "estimate_capacity",
    "generate_diagnostics",
    "monte_carlo_simulation",
    "overall_score",
    "parameter_robustness",
    "stress_report",
    "validate_payload",
    "walk_forward_analysis",
    "warnings",
]
