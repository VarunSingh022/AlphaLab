"""Parameter Robustness & Cliff Detection."""

from dataclasses import dataclass

from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class RobustnessReport:
    parameter_count: int
    instability_index: float
    cliff_risk: bool
    robustness_score: float


def parameter_robustness(payload: ResearchPayload) -> RobustnessReport:
    """Analyzes the parameter counts to identify overfitting and cliffs."""
    # In a full run, this ingests the Optimizer's nearby results.
    # Here we infer risk deterministically from the payload structure.
    param_count = len(payload.parameters)
    instability = min(1.0, param_count * 0.15)
    cliff_risk = param_count > 5
    score = 100.0 - (instability * 100.0)

    return RobustnessReport(param_count, round(instability, 4), cliff_risk, round(score, 2))
