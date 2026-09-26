"""AlphaLab Research.

Two layers, and they consume different things.

* **Run evaluation** (v2) -- ``ResearchEngine`` and the reports it compiles,
  which read a *completed run's* returns, trades and parameters through
  ``ResearchPayload`` and score them. Nothing there knows about a dataset.
* **Study methodology** (v3.2) -- features, forward returns, signal
  diagnostics, walk-forward and cross-validation splits with purging and
  embargo, robustness perturbations and overfitting diagnostics. These run
  *before* there is a return series to score, from a canonical ``Dataset``
  through ``alphalab.factor_library``.

* **Point-in-time research** (v3.7) -- event studies anchored where
  information could first be traded (``event_study``), and regime detection
  from declared rules with a reconstructable state (``classify_regimes``),
  whose labels condition the v3.2 diagnostics directly. They read
  ``alphalab.alt_data`` for events and ``alphalab.factor_library`` for prices
  and features, and never ``alphalab.data``.

The first two meet at ``alphalab.lifecycle.evidence``, where either can be
recorded as evidence, and nowhere else. ``analyze_regimes`` and
``classify_regimes`` are the pair most easily confused after the walk-forwards:
the first scores a finished run's returns by labels somebody supplied, and
detects nothing; the second detects the labels. ``walk_forward_analysis`` and
``walk_forward_splits`` are the pair most easily confused: the first reads a
finished return series and reports its out-of-sample consistency, the second
partitions a time index into folds before anything has been run.
``tests/regression/test_shared_names_stay_distinct.py`` records why they stay
apart.
"""

from alphalab.research.adapter import ResearchAdapter
from alphalab.research.bias import BiasReport, detect_bias
from alphalab.research.bootstrap import BootstrapReport, bootstrap_statistics
from alphalab.research.capacity import CapacityReport, estimate_capacity
from alphalab.research.cross_validation import WalkForwardReport, walk_forward_analysis
from alphalab.research.diagnostics import DiagnosticReport, generate_diagnostics
from alphalab.research.engine import ResearchEngine
from alphalab.research.event_study import (
    EVENT_STUDY_SCHEME,
    AbnormalReturnModel,
    EventOutcome,
    EventStudyDefinition,
    EventStudyResult,
    EventWindow,
    ExcludedEvent,
    canonical_event_study_key,
    event_study,
    event_study_metrics,
)
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
from alphalab.research.overfitting import (
    OverfittingPolicy,
    OverfittingReport,
    StabilityReport,
    SweepResult,
    build_overfitting_report,
    parameter_sweep,
    period_stability,
    sample_degradation,
    symbol_stability,
)
from alphalab.research.perturbation import (
    Perturbation,
    PerturbationKind,
    PerturbationRun,
    apply_cost,
    block_bootstrap_indices,
    delay_signal,
    drop_observations,
    monte_carlo_orders,
    perturb_observations,
    perturb_signal,
    sample_by,
    shift_parameter,
)
from alphalab.research.protocol import ResearchPayload, ResearchProtocol, TradePayload
from alphalab.research.purging import (
    PurgePolicy,
    PurgeResult,
    apply_purge_and_embargo,
    label_ends_from_horizon,
)
from alphalab.research.regime import RegimeReport, analyze_regimes
from alphalab.research.regimes import (
    REGIME_DEFINITION_SCHEME,
    REGIME_SERIES_SCHEME,
    CompositeRule,
    RegimeCell,
    RegimeDefinition,
    RegimeProfile,
    RegimeRule,
    RegimeSeries,
    RegimeState,
    RegimeStatistics,
    RegimeTransition,
    ThresholdRule,
    TrailingQuantileRule,
    TransitionFrequency,
    canonical_regime_key,
    classify_regimes,
    initial_regime_state,
    regime_profile,
    regime_series_from_features,
)
from alphalab.research.research import ResearchScore, compute_overall_score
from alphalab.research.sensitivity import RobustnessReport, parameter_robustness
from alphalab.research.signals import (
    QuantileBucket,
    SignalDiagnostics,
    conditional_diagnostics,
    signal_diagnostics,
    signal_horizons,
)
from alphalab.research.splits import (
    SplitInterval,
    SplitReport,
    TimeSplit,
    require_chronological,
)
from alphalab.research.state import ResearchState
from alphalab.research.stress import StressReport, apply_stress_tests
from alphalab.research.study import (
    RESULT_KEY_SCHEME,
    STUDY_KEY_SCHEME,
    ResearchStudy,
    StudyResult,
    build_result,
    canonical_result_key,
    canonical_study_key,
    derive_result_id,
    derive_study_id,
)
from alphalab.research.time_series_cv import CVMethod, cross_validation_splits
from alphalab.research.validation import validate_payload
from alphalab.research.views import (
    bias_report,
    capacity_report,
    diagnostic_report,
    overall_score,
    stress_report,
    warnings,
)
from alphalab.research.walk_forward import WindowMode, walk_forward_splits

__all__ = [
    "EVENT_STUDY_SCHEME",
    "REGIME_DEFINITION_SCHEME",
    "REGIME_SERIES_SCHEME",
    "RESULT_KEY_SCHEME",
    "STUDY_KEY_SCHEME",
    "AbnormalReturnModel",
    "AnalysisCompleted",
    "BiasDetected",
    "BiasReport",
    "BootstrapReport",
    "CVMethod",
    "CapacityReport",
    "CompositeRule",
    "DiagnosticReport",
    "DiagnosticsGenerated",
    "EventOutcome",
    "EventStudyDefinition",
    "EventStudyResult",
    "EventWindow",
    "ExcludedEvent",
    "InvalidResearchStateError",
    "MonteCarloReport",
    "OverfittingPolicy",
    "OverfittingReport",
    "Perturbation",
    "PerturbationKind",
    "PerturbationRun",
    "PurgePolicy",
    "PurgeResult",
    "QuantileBucket",
    "RegimeCell",
    "RegimeDefinition",
    "RegimeProfile",
    "RegimeReport",
    "RegimeRule",
    "RegimeSeries",
    "RegimeState",
    "RegimeStatistics",
    "RegimeTransition",
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
    "ResearchStudy",
    "ResearchValidationError",
    "RobustnessReport",
    "SignalDiagnostics",
    "SplitInterval",
    "SplitReport",
    "StabilityReport",
    "StressReport",
    "StudyResult",
    "SweepResult",
    "ThresholdRule",
    "TimeSplit",
    "TradePayload",
    "TrailingQuantileRule",
    "TransitionFrequency",
    "WalkForwardReport",
    "WindowMode",
    "analyze_regimes",
    "apply_cost",
    "apply_purge_and_embargo",
    "apply_stress_tests",
    "bias_report",
    "block_bootstrap_indices",
    "bootstrap_statistics",
    "build_overfitting_report",
    "build_result",
    "calculate_cagr",
    "calculate_max_drawdown",
    "calculate_sharpe",
    "calculate_volatility",
    "canonical_event_study_key",
    "canonical_regime_key",
    "canonical_result_key",
    "canonical_study_key",
    "capacity_report",
    "classify_regimes",
    "compute_overall_score",
    "conditional_diagnostics",
    "cross_validation_splits",
    "delay_signal",
    "derive_result_id",
    "derive_study_id",
    "detect_bias",
    "diagnostic_report",
    "drop_observations",
    "estimate_capacity",
    "event_study",
    "event_study_metrics",
    "generate_diagnostics",
    "initial_regime_state",
    "label_ends_from_horizon",
    "monte_carlo_orders",
    "monte_carlo_simulation",
    "overall_score",
    "parameter_robustness",
    "parameter_sweep",
    "period_stability",
    "perturb_observations",
    "perturb_signal",
    "regime_profile",
    "regime_series_from_features",
    "require_chronological",
    "sample_by",
    "sample_degradation",
    "shift_parameter",
    "signal_diagnostics",
    "signal_horizons",
    "stress_report",
    "symbol_stability",
    "validate_payload",
    "walk_forward_analysis",
    "walk_forward_splits",
    "warnings",
]
