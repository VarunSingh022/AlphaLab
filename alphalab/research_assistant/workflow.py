"""End-to-end research automation: generate -> evaluate -> rank -> report.

This is the "workflow orchestration" capability -- a single deterministic call
chaining the assistant's own steps and, optionally, recording every candidate
into an ``alphalab.experiment_tracking`` tracker so the search leaves an audit
trail in the same store as hand-run experiments.
"""

from dataclasses import dataclass

from alphalab.experiment_tracking.tracker import (
    ExperimentTracker,
    complete_run,
    log_metrics,
    start_run,
)
from alphalab.research.overfitting import SweepResult, parameter_sweep
from alphalab.research_assistant.evaluation import (
    CandidateEvaluation,
    Evaluator,
    best_evaluation,
    evaluate_candidates,
)
from alphalab.research_assistant.generation import (
    ParameterAxes,
    ParameterSpace,
    StrategyCandidate,
    generate_candidates,
    parameter_space,
)
from alphalab.research_assistant.report import AssistantReport, build_report


@dataclass(frozen=True, slots=True)
class ResearchWorkflowResult:
    """Everything one :func:`run_research_workflow` call produced.

    Attributes:
        template: The searched strategy template.
        objective: The metric candidates were ranked on.
        candidates: Every generated candidate, in enumeration order.
        evaluations: Every evaluation, ranked best first.
        best: The winning evaluation.
        report: A built :class:`AssistantReport`.
        sweep: The search as the research authority counts it
            (:func:`~alphalab.research.overfitting.parameter_sweep`): every
            candidate's objective score keyed by its rendered parameters, the
            trial count every multiple-testing correction needs, and the
            surface's sensitivity. Since v3.12 (ledger SCF-003).
        tracker: The experiment tracker, populated with one completed run per
            candidate -- ``None`` when no tracker was passed in.
    """

    template: str
    objective: str
    candidates: tuple[StrategyCandidate, ...]
    evaluations: tuple[CandidateEvaluation, ...]
    best: CandidateEvaluation
    report: AssistantReport
    sweep: SweepResult
    tracker: ExperimentTracker | None = None


def run_research_workflow(
    template: str,
    space: ParameterSpace | ParameterAxes,
    evaluator: Evaluator,
    objective: str,
    timestamp: float,
    higher_is_better: bool = True,
    limit: int | None = None,
    tracker: ExperimentTracker | None = None,
) -> ResearchWorkflowResult:
    """Generates candidates, evaluates and ranks them, and builds a report.

    When ``tracker`` is given, each candidate is also recorded as a completed
    experiment run (parameters, all evaluator metrics) and the updated tracker
    is returned on the result.

    Raises:
        ResearchAssistantInputError: Propagated from generation or evaluation on
            invalid inputs.
    """
    searched = parameter_space(space)
    candidates = generate_candidates(template, searched, limit=limit)
    evaluations = evaluate_candidates(
        candidates, evaluator, objective, higher_is_better=higher_is_better
    )
    # Every candidate was evaluated exactly once above; the sweep reads those
    # scores, in the space's order, so its trial count is the search's.
    rendered = searched.rendered[: len(candidates)]
    position = {candidate.candidate_id: index for index, candidate in enumerate(candidates)}
    score_of = {
        rendered[position[evaluation.candidate.candidate_id]]: evaluation.score
        for evaluation in evaluations
    }
    sweep = parameter_sweep(
        objective, rendered, score_of.__getitem__, higher_is_better=higher_is_better
    )
    best = best_evaluation(evaluations, higher_is_better=higher_is_better)
    report = build_report(
        template, objective, evaluations, timestamp, higher_is_better=higher_is_better
    )

    updated_tracker = tracker
    if updated_tracker is not None:
        for evaluation in evaluations:
            updated_tracker, run_id = start_run(
                updated_tracker,
                template,
                dict(evaluation.candidate.parameters),
                timestamp,
                tags={"candidate_id": evaluation.candidate.candidate_id},
            )
            updated_tracker = log_metrics(updated_tracker, run_id, dict(evaluation.metrics))
            updated_tracker = complete_run(updated_tracker, run_id, timestamp)

    return ResearchWorkflowResult(
        template=template,
        objective=objective,
        candidates=candidates,
        evaluations=evaluations,
        best=best,
        report=report,
        sweep=sweep,
        tracker=updated_tracker,
    )
