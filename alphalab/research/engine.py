"""Top-level Engine Facade orchestrating Institutional Research."""

from dataclasses import replace

from alphalab.common.ids import new_id
from alphalab.research.bias import detect_bias
from alphalab.research.bootstrap import bootstrap_statistics
from alphalab.research.capacity import estimate_capacity
from alphalab.research.cross_validation import walk_forward_analysis
from alphalab.research.diagnostics import generate_diagnostics
from alphalab.research.events import (
    AnalysisCompleted,
    BiasDetected,
    DiagnosticsGenerated,
    ResearchCompleted,
    ResearchStarted,
)
from alphalab.research.exceptions import InvalidResearchStateError
from alphalab.research.montecarlo import monte_carlo_simulation
from alphalab.research.protocol import ResearchPayload
from alphalab.research.regime import analyze_regimes
from alphalab.research.research import compute_overall_score
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
        state: ResearchState, payload: ResearchPayload, timestamp: float
    ) -> ResearchState:
        """Executes the entire research pipeline deterministically."""
        validate_payload(payload)
        if state.completed:
            raise InvalidResearchStateError("Research already completed.")

        # 1. Execute Analyses
        bias = detect_bias(payload)
        cv = walk_forward_analysis(payload)
        mc = monte_carlo_simulation(payload)
        boot = bootstrap_statistics(payload)
        robust = parameter_robustness(payload)
        regime = analyze_regimes(payload)
        cap = estimate_capacity(payload)
        stress = apply_stress_tests(payload)
        diag = generate_diagnostics(payload)

        # 2. Score
        score = compute_overall_score(bias, boot, robust, cap, cv, regime, stress)

        # 3. Events
        evts = [
            BiasDetected(
                ResearchEngine._create_id(),
                timestamp,
                state.research_id,
                "Look-Ahead",
                bias.look_ahead_risk,
            ),
            AnalysisCompleted(
                ResearchEngine._create_id(), timestamp, state.research_id, "Walk-Forward"
            ),
            AnalysisCompleted(
                ResearchEngine._create_id(), timestamp, state.research_id, "Monte-Carlo"
            ),
            DiagnosticsGenerated(
                ResearchEngine._create_id(), timestamp, state.research_id, len(diag.warnings)
            ),
            ResearchCompleted(
                ResearchEngine._create_id(), timestamp, state.research_id, score.overall_score
            ),
        ]

        # 4. Assemble Immutable State
        return replace(
            state,
            completed=True,
            bias_report=bias,
            walk_forward_report=cv,
            monte_carlo_report=mc,
            bootstrap_report=boot,
            robustness_report=robust,
            regime_report=regime,
            capacity_report=cap,
            stress_report=stress,
            diagnostic_report=diag,
            score=score,
            events=(*state.events, *evts),
        )
