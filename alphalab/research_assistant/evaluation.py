"""Scoring and ranking strategy candidates through a caller-supplied evaluator.

The evaluator is any pure function from a candidate to a metric mapping -- a
backtest wrapper, a walk-forward harness, a cached lookup. The assistant does
not implement backtesting; it drives whatever evaluation the researcher provides
and ranks the results deterministically.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from alphalab.research_assistant.exceptions import ResearchAssistantInputError
from alphalab.research_assistant.generation import StrategyCandidate

Evaluator = Callable[[StrategyCandidate], Mapping[str, float]]
"""A pure function scoring one candidate into a mapping of metric name -> value."""


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    """A candidate together with the metrics it scored and its objective value.

    Attributes:
        candidate: The evaluated candidate.
        metrics: Every metric the evaluator returned.
        score: The value of the objective metric, copied out for ranking.
    """

    candidate: StrategyCandidate
    metrics: Mapping[str, float]
    score: float


def evaluate_candidates(
    candidates: tuple[StrategyCandidate, ...],
    evaluator: Evaluator,
    objective: str,
    higher_is_better: bool = True,
) -> tuple[CandidateEvaluation, ...]:
    """Runs ``evaluator`` over every candidate and returns evaluations, best first.

    Ties on the objective score are broken by ``candidate_id`` so the order is
    fully determined by the inputs.

    Raises:
        ResearchAssistantInputError: If ``candidates`` is empty, ``objective``
            is blank, or the evaluator's result for any candidate lacks the
            ``objective`` metric.
    """
    if not candidates:
        raise ResearchAssistantInputError("candidates cannot be empty.")
    if not objective.strip():
        raise ResearchAssistantInputError("objective cannot be empty.")

    evaluations = []
    for candidate in candidates:
        metrics = evaluator(candidate)
        if objective not in metrics:
            raise ResearchAssistantInputError(
                f"Evaluator result for '{candidate.candidate_id}' has no "
                f"'{objective}' metric; got {sorted(metrics)}."
            )
        evaluations.append(
            CandidateEvaluation(
                candidate=candidate, metrics=dict(metrics), score=metrics[objective]
            )
        )

    return rank_evaluations(tuple(evaluations), higher_is_better=higher_is_better)


def rank_evaluations(
    evaluations: tuple[CandidateEvaluation, ...], higher_is_better: bool = True
) -> tuple[CandidateEvaluation, ...]:
    """Returns ``evaluations`` sorted by score, best first, ties by candidate id."""
    return tuple(
        sorted(
            evaluations,
            key=lambda evaluation: (
                -evaluation.score if higher_is_better else evaluation.score,
                evaluation.candidate.candidate_id,
            ),
        )
    )


def best_evaluation(
    evaluations: tuple[CandidateEvaluation, ...], higher_is_better: bool = True
) -> CandidateEvaluation:
    """Returns the single best evaluation.

    Raises:
        ResearchAssistantInputError: If ``evaluations`` is empty.
    """
    if not evaluations:
        raise ResearchAssistantInputError("evaluations cannot be empty.")
    return rank_evaluations(evaluations, higher_is_better=higher_is_better)[0]


def top_k(
    evaluations: tuple[CandidateEvaluation, ...], k: int, higher_is_better: bool = True
) -> tuple[CandidateEvaluation, ...]:
    """Returns the best ``k`` evaluations.

    Raises:
        ResearchAssistantInputError: If ``k`` is not positive.
    """
    if k <= 0:
        raise ResearchAssistantInputError(f"k must be positive, got {k}.")
    return rank_evaluations(evaluations, higher_is_better=higher_is_better)[:k]
