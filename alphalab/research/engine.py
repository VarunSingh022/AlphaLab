"""Top-level Engine Facade orchestrating the evaluation of a finished run.

Restated over the v3.2 methodology in v3.12 (ledger RES-001): every report is a
measurement, every bound is the caller's :class:`ResearchPolicy`, every finding
names its number and its bound, and nothing grades the strategy. See
:mod:`alphalab.research.research`.
"""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.research.bias import detect_bias
from alphalab.research.bootstrap import bootstrap_statistics
from alphalab.research.capacity import estimate_capacity
from alphalab.research.cross_validation import walk_forward_analysis
from alphalab.research.diagnostics import generate_diagnostics
from alphalab.research.events import (
    AnalysisCompleted,
    DiagnosticsGenerated,
    ResearchCompleted,
    ResearchEvent,
    ResearchStarted,
)
from alphalab.research.exceptions import InvalidResearchStateError
from alphalab.research.montecarlo import monte_carlo_simulation
from alphalab.research.protocol import ResearchPayload
from alphalab.research.regime import analyze_regimes
from alphalab.research.research import ResearchPolicy, research_metrics
from alphalab.research.sensitivity import parameter_robustness
from alphalab.research.state import ResearchState
from alphalab.research.stress import apply_stress_tests
from alphalab.research.validation import validate_payload


class ResearchEngine:
    """Facade orchestrating pure functional research processes."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def initialize(research_id: str, strategy_id: str, timestamp: float) -> ResearchState:
        if not research_id.strip():
            raise ValueError("Research ID cannot be empty.")

        evt = ResearchStarted(ResearchEngine._create_id(), timestamp, research_id, strategy_id)
        return ResearchState(
            research_id=research_id, strategy_id=strategy_id, timestamp=timestamp, events=(evt,)
        )

    @staticmethod
    def run_full_research(
        state: ResearchState,
        payload: ResearchPayload,
        policy: ResearchPolicy,
        timestamp: float,
        seed: int,
    ) -> ResearchState:
        """Executes the entire evaluation deterministically.

        ``seed`` drives the Monte Carlo and bootstrap resampling. It is required:
        until v3.10 both defaulted to 42, so every study resampled the same way
        whether or not anyone had chosen to (ledger DET-004). ``policy`` is
        required for the same reason since v3.12: every bound and shock in it
        was a literal here before (ledger RES-001).
        """
        validate_payload(payload)
        if state.completed:
            raise InvalidResearchStateError("Research already completed.")

        bias = detect_bias(payload)
        walk_forward = walk_forward_analysis(payload, policy.walk_forward_windows)
        monte_carlo = monte_carlo_simulation(payload, seed, policy.ruin_drawdown)
        bootstrap = bootstrap_statistics(payload, seed)
        robustness = parameter_robustness(payload)
        regime = analyze_regimes(payload)
        capacity = estimate_capacity(payload)
        stress = apply_stress_tests(
            payload, policy.shock_return, policy.gain_multiplier, policy.loss_multiplier
        )
        diagnostics = generate_diagnostics(
            payload,
            policy.minimum_trades,
            policy.maximum_trade_share,
            policy.worst_period_return,
        )
        metrics = research_metrics(
            payload,
            bias,
            walk_forward,
            monte_carlo,
            bootstrap,
            robustness,
            regime,
            capacity,
            stress,
        )

        events: list[ResearchEvent] = [
            AnalysisCompleted(ResearchEngine._create_id(), timestamp, state.research_id, name)
            for name in ("Walk-Forward", "Monte-Carlo", "Bootstrap", "Stress")
        ]
        events.append(
            DiagnosticsGenerated(
                ResearchEngine._create_id(),
                timestamp,
                state.research_id,
                len(diagnostics.warnings),
            )
        )
        events.append(
            ResearchCompleted(
                ResearchEngine._create_id(),
                timestamp,
                state.research_id,
                len(metrics),
                len(diagnostics.warnings),
            )
        )

        return replace(
            state,
            completed=True,
            bias_report=bias,
            walk_forward_report=walk_forward,
            monte_carlo_report=monte_carlo,
            bootstrap_report=bootstrap,
            robustness_report=robustness,
            regime_report=regime,
            capacity_report=capacity,
            stress_report=stress,
            diagnostic_report=diagnostics,
            policy=policy,
            metrics=metrics,
            events=(*state.events, *events),
        )
