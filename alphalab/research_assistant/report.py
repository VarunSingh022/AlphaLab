"""Deterministic research-report generation from ranked candidate evaluations.

This is a compact, domain-specific summary of a candidate search, not a
reimplementation of ``alphalab.reporting``. When a full sectioned report or a
CSV/JSON export is wanted, feed :func:`render_markdown`'s output or the
``AssistantReport`` fields into that package instead.
"""

from dataclasses import dataclass, field

from alphalab.research_assistant.evaluation import CandidateEvaluation, rank_evaluations
from alphalab.research_assistant.exceptions import ResearchAssistantInputError


@dataclass(frozen=True, slots=True)
class AssistantReport:
    """A structured summary of one research workflow.

    Attributes:
        title: Human-readable report title.
        template: The strategy template that was searched.
        objective: The metric name candidates were ranked on.
        higher_is_better: Whether a larger objective value ranked better.
        candidate_count: How many candidates were evaluated.
        best_candidate_id: The winning candidate's id.
        best_score: The winning candidate's objective score.
        ranked_rows: One ``(candidate_id, score)`` pair per evaluation, best
            first.
        generated_at: Unix timestamp the report was built.
    """

    title: str
    template: str
    objective: str
    higher_is_better: bool
    candidate_count: int
    best_candidate_id: str
    best_score: float
    ranked_rows: tuple[tuple[str, float], ...] = field(default_factory=tuple)
    generated_at: float = 0.0


def build_report(
    template: str,
    objective: str,
    evaluations: tuple[CandidateEvaluation, ...],
    timestamp: float,
    higher_is_better: bool = True,
    title: str | None = None,
) -> AssistantReport:
    """Builds an :class:`AssistantReport` from ranked (or unranked) evaluations.

    Raises:
        ResearchAssistantInputError: If ``evaluations`` is empty.
    """
    if not evaluations:
        raise ResearchAssistantInputError("evaluations cannot be empty.")

    ranked = rank_evaluations(evaluations, higher_is_better=higher_is_better)
    rows = tuple((e.candidate.candidate_id, e.score) for e in ranked)
    return AssistantReport(
        title=title or f"Research report: {template}",
        template=template,
        objective=objective,
        higher_is_better=higher_is_better,
        candidate_count=len(ranked),
        best_candidate_id=ranked[0].candidate.candidate_id,
        best_score=ranked[0].score,
        ranked_rows=rows,
        generated_at=timestamp,
    )


def render_markdown(report: AssistantReport) -> str:
    """Renders an :class:`AssistantReport` as deterministic Markdown text."""
    direction = "higher is better" if report.higher_is_better else "lower is better"
    lines = [
        f"# {report.title}",
        "",
        f"- Template: `{report.template}`",
        f"- Objective: `{report.objective}` ({direction})",
        f"- Candidates evaluated: {report.candidate_count}",
        f"- Best candidate: `{report.best_candidate_id}` (score {report.best_score:.6g})",
        "",
        "| Rank | Candidate | Score |",
        "| ---: | --- | ---: |",
    ]
    lines.extend(
        f"| {rank} | `{candidate_id}` | {score:.6g} |"
        for rank, (candidate_id, score) in enumerate(report.ranked_rows, start=1)
    )
    return "\n".join(lines)
