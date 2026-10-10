"""Parameter robustness, as the search that chose the parameters measured it.

Robustness is how the result moves when the parameters move, and only a search
over neighbouring configurations shows that. Until v3.12 this module inferred it
from the *number* of parameters -- an "instability" of 0.15 per parameter, a
"cliff" past five -- without looking at a single result (ledger RES-001). It now
reads the :class:`~alphalab.research.overfitting.SweepResult` the payload
carries, and reports robustness as not measured when there is none.
"""

from dataclasses import dataclass

from alphalab.research.protocol import ResearchPayload


@dataclass(frozen=True, slots=True)
class RobustnessReport:
    """What the parameter search showed; ``None`` throughout when no search was recorded.

    Attributes:
        parameter_count: How many parameters the configuration has.
        trials: How many configurations the search evaluated.
        sensitivity: The search surface's coefficient of variation.
        neighbour_drop: How far the best configuration's score falls to its
            best neighbour, as a fraction of the best.
    """

    parameter_count: int
    trials: int | None
    sensitivity: float | None
    neighbour_drop: float | None


def parameter_robustness(payload: ResearchPayload) -> RobustnessReport:
    """Read the payload's parameter search; see the module docstring."""

    sweep = payload.sweep
    return RobustnessReport(
        parameter_count=len(payload.parameters),
        trials=None if sweep is None else sweep.trials,
        sensitivity=None if sweep is None else sweep.sensitivity,
        neighbour_drop=None if sweep is None else sweep.neighbour_drop,
    )
