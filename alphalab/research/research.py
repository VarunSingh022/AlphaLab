"""Aggregate Scoring for the Research Engine."""

from dataclasses import dataclass

from alphalab.research.bias import BiasReport
from alphalab.research.bootstrap import BootstrapReport
from alphalab.research.capacity import CapacityReport
from alphalab.research.cross_validation import WalkForwardReport
from alphalab.research.regime import RegimeReport
from alphalab.research.sensitivity import RobustnessReport
from alphalab.research.stress import StressReport


@dataclass(frozen=True, slots=True)
class ResearchScore:
    bias_score: float
    confidence_score: float
    robustness_score: float
    capacity_score: float
    stability_score: float
    generalisation_score: float
    stress_score: float
    overall_score: float


def compute_overall_score(
    bias: BiasReport,
    bootstrap: BootstrapReport,
    robustness: RobustnessReport,
    capacity: CapacityReport,
    walk_forward: WalkForwardReport,
    regime: RegimeReport,
    stress: StressReport,
) -> ResearchScore:
    """Aggregates individual subsystem reports into a unified institutional grade."""

    stability = 100.0 - walk_forward.degradation_score

    overall = (
        (bias.overall_bias_score * 0.20)
        + (bootstrap.confidence_score * 0.15)
        + (robustness.robustness_score * 0.15)
        + (capacity.capacity_score * 0.10)
        + (stability * 0.15)
        + (regime.regime_generalisation_score * 0.15)
        + (stress.stress_survival_score * 0.10)
    )

    return ResearchScore(
        bias_score=bias.overall_bias_score,
        confidence_score=bootstrap.confidence_score,
        robustness_score=robustness.robustness_score,
        capacity_score=capacity.capacity_score,
        stability_score=round(stability, 2),
        generalisation_score=regime.regime_generalisation_score,
        stress_score=stress.stress_survival_score,
        overall_score=round(overall, 2),
    )
