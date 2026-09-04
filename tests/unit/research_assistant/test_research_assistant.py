"""Comprehensive tests for the Research Assistant: deterministic candidate
generation, evaluation/ranking, report generation, the studio bridge, and the
end-to-end workflow with experiment-tracking integration."""

from collections.abc import Mapping
from dataclasses import FrozenInstanceError

import pytest

from alphalab.experiment_tracking.tracker import ExperimentTracker, RunStatus
from alphalab.research_assistant import (
    AssistantReport,
    ResearchAssistantInputError,
    StrategyCandidate,
    best_evaluation,
    build_report,
    candidate_count,
    evaluate_candidates,
    generate_candidates,
    rank_evaluations,
    render_markdown,
    run_research_workflow,
    to_strategy_definition,
    top_k,
)

SPACE: Mapping[str, tuple[float, ...]] = {"fast": (3.0, 5.0, 7.0), "slow": (10.0, 20.0, 30.0)}


def _evaluator(candidate: StrategyCandidate) -> Mapping[str, float]:
    """Peaks (score 0.0) at fast=5, slow=20; strictly worse elsewhere."""
    fast = candidate.parameters["fast"]
    slow = candidate.parameters["slow"]
    return {"sharpe": -abs(fast - 5.0) - abs(slow - 20.0), "turnover": fast + slow}


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #


def test_generate_candidates_enumerates_full_product() -> None:
    candidates = generate_candidates("ma_crossover", SPACE)
    assert len(candidates) == 9
    assert candidate_count(SPACE) == 9


def test_generate_candidates_ids_are_sequential_and_prefixed() -> None:
    candidates = generate_candidates("ma_crossover", SPACE)
    assert candidates[0].candidate_id == "ma_crossover-000"
    assert candidates[-1].candidate_id == "ma_crossover-008"
    assert all(c.template == "ma_crossover" for c in candidates)


def test_generate_candidates_order_is_independent_of_mapping_insertion_order() -> None:
    reordered = {"slow": (10.0, 20.0, 30.0), "fast": (3.0, 5.0, 7.0)}
    assert generate_candidates("t", reordered) == generate_candidates("t", SPACE)


def test_generate_candidates_respects_limit() -> None:
    candidates = generate_candidates("t", SPACE, limit=4)
    assert len(candidates) == 4
    assert candidates[-1].candidate_id == "t-003"


def test_generate_candidates_rejects_blank_template() -> None:
    with pytest.raises(ResearchAssistantInputError):
        generate_candidates("  ", SPACE)


def test_generate_candidates_rejects_empty_space() -> None:
    with pytest.raises(ResearchAssistantInputError):
        generate_candidates("t", {})


def test_generate_candidates_rejects_empty_choices() -> None:
    with pytest.raises(ResearchAssistantInputError):
        generate_candidates("t", {"fast": ()})


def test_generate_candidates_rejects_non_positive_limit() -> None:
    with pytest.raises(ResearchAssistantInputError):
        generate_candidates("t", SPACE, limit=0)


def test_candidate_count_rejects_empty_space() -> None:
    with pytest.raises(ResearchAssistantInputError):
        candidate_count({})


# --------------------------------------------------------------------------- #
# Evaluation and ranking
# --------------------------------------------------------------------------- #


def test_evaluate_candidates_ranks_best_first() -> None:
    candidates = generate_candidates("t", SPACE)
    evaluations = evaluate_candidates(candidates, _evaluator, "sharpe")
    assert evaluations[0].score == 0.0
    assert evaluations[0].candidate.parameters == {"fast": 5.0, "slow": 20.0}
    scores = [e.score for e in evaluations]
    assert scores == sorted(scores, reverse=True)


def test_evaluate_candidates_carries_all_metrics() -> None:
    candidates = generate_candidates("t", SPACE, limit=1)
    evaluations = evaluate_candidates(candidates, _evaluator, "sharpe")
    assert set(evaluations[0].metrics) == {"sharpe", "turnover"}


def test_evaluate_candidates_lower_is_better() -> None:
    candidates = generate_candidates("t", SPACE)
    evaluations = evaluate_candidates(candidates, _evaluator, "turnover", higher_is_better=False)
    assert evaluations[0].metrics["turnover"] == 13.0  # fast=3 + slow=10


def test_ranking_breaks_ties_by_candidate_id() -> None:
    candidates = generate_candidates("t", SPACE)
    # Constant score -> order must fall back to candidate_id ascending.
    evaluations = evaluate_candidates(candidates, lambda _c: {"flat": 1.0}, "flat")
    ids = [e.candidate.candidate_id for e in evaluations]
    assert ids == sorted(ids)


def test_evaluate_candidates_rejects_missing_objective_metric() -> None:
    candidates = generate_candidates("t", SPACE, limit=1)
    with pytest.raises(ResearchAssistantInputError):
        evaluate_candidates(candidates, _evaluator, "nonexistent")


def test_evaluate_candidates_rejects_empty_candidates() -> None:
    with pytest.raises(ResearchAssistantInputError):
        evaluate_candidates((), _evaluator, "sharpe")


def test_evaluate_candidates_rejects_blank_objective() -> None:
    candidates = generate_candidates("t", SPACE, limit=1)
    with pytest.raises(ResearchAssistantInputError):
        evaluate_candidates(candidates, _evaluator, "   ")


def test_best_evaluation_and_top_k() -> None:
    candidates = generate_candidates("t", SPACE)
    evaluations = evaluate_candidates(candidates, _evaluator, "sharpe")
    assert best_evaluation(evaluations).score == 0.0
    best_three = top_k(evaluations, 3)
    assert len(best_three) == 3
    assert [e.score for e in best_three] == sorted((e.score for e in best_three), reverse=True)


def test_best_evaluation_rejects_empty() -> None:
    with pytest.raises(ResearchAssistantInputError):
        best_evaluation(())


def test_top_k_rejects_non_positive_k() -> None:
    candidates = generate_candidates("t", SPACE)
    evaluations = evaluate_candidates(candidates, _evaluator, "sharpe")
    with pytest.raises(ResearchAssistantInputError):
        top_k(evaluations, 0)


def test_rank_evaluations_is_stable_and_pure() -> None:
    candidates = generate_candidates("t", SPACE)
    evaluations = evaluate_candidates(candidates, _evaluator, "sharpe")
    assert rank_evaluations(evaluations) == evaluations


# --------------------------------------------------------------------------- #
# Report generation
# --------------------------------------------------------------------------- #


def test_build_report_summarises_the_search() -> None:
    candidates = generate_candidates("ma_crossover", SPACE)
    evaluations = evaluate_candidates(candidates, _evaluator, "sharpe")
    report = build_report("ma_crossover", "sharpe", evaluations, timestamp=100.0)
    assert isinstance(report, AssistantReport)
    assert report.candidate_count == 9
    assert report.best_candidate_id == evaluations[0].candidate.candidate_id
    assert report.best_score == 0.0
    assert report.generated_at == 100.0
    assert report.ranked_rows[0] == (evaluations[0].candidate.candidate_id, 0.0)


def test_build_report_rejects_empty_evaluations() -> None:
    with pytest.raises(ResearchAssistantInputError):
        build_report("t", "sharpe", (), timestamp=1.0)


def test_render_markdown_is_deterministic_and_complete() -> None:
    candidates = generate_candidates("t", SPACE)
    evaluations = evaluate_candidates(candidates, _evaluator, "sharpe")
    report = build_report("t", "sharpe", evaluations, timestamp=1.0)
    first = render_markdown(report)
    assert render_markdown(report) == first
    assert "# Research report: t" in first
    assert "| Rank | Candidate | Score |" in first
    # table header row + separator row + one row per candidate
    assert first.count("\n| ") == report.candidate_count + 2


# --------------------------------------------------------------------------- #
# Studio bridge
# --------------------------------------------------------------------------- #


def test_to_strategy_definition_maps_candidate_fields() -> None:
    candidate = generate_candidates("ma_crossover", SPACE)[4]
    definition = to_strategy_definition(
        candidate,
        name="MA Crossover",
        version="1.0.0",
        author="assistant",
        description="generated",
    )
    assert definition.strategy_id == candidate.candidate_id
    assert definition.parameters == candidate.parameters
    assert definition.metadata["template"] == "ma_crossover"


def test_to_strategy_definition_merges_extra_metadata() -> None:
    candidate = generate_candidates("t", SPACE)[0]
    definition = to_strategy_definition(
        candidate,
        name="n",
        version="v",
        author="a",
        description="d",
        metadata={"universe": "sp500"},
    )
    assert definition.metadata == {"template": "t", "universe": "sp500"}


# --------------------------------------------------------------------------- #
# Workflow orchestration
# --------------------------------------------------------------------------- #


def test_run_research_workflow_without_tracker() -> None:
    result = run_research_workflow("ma_crossover", SPACE, _evaluator, "sharpe", timestamp=10.0)
    assert result.best.score == 0.0
    assert result.report.best_candidate_id == result.best.candidate.candidate_id
    assert len(result.candidates) == 9
    assert result.tracker is None


def test_run_research_workflow_is_deterministic() -> None:
    first = run_research_workflow("t", SPACE, _evaluator, "sharpe", timestamp=10.0)
    second = run_research_workflow("t", SPACE, _evaluator, "sharpe", timestamp=10.0)
    assert first.best.candidate == second.best.candidate
    assert first.report == second.report


def test_run_research_workflow_records_into_tracker() -> None:
    result = run_research_workflow(
        "t", SPACE, _evaluator, "sharpe", timestamp=10.0, tracker=ExperimentTracker()
    )
    assert result.tracker is not None
    runs = list(result.tracker.runs.values())
    assert len(runs) == 9
    assert all(run.status is RunStatus.COMPLETED for run in runs)
    assert all(run.name == "t" for run in runs)
    # Every candidate's metrics were logged as single-point histories.
    for run in runs:
        assert set(run.metrics) == {"sharpe", "turnover"}
        assert run.tags["candidate_id"].startswith("t-")


def test_run_research_workflow_leaves_input_tracker_untouched() -> None:
    original = ExperimentTracker()
    run_research_workflow("t", SPACE, _evaluator, "sharpe", timestamp=1.0, tracker=original)
    assert original.runs == {}


def test_run_research_workflow_respects_limit() -> None:
    result = run_research_workflow("t", SPACE, _evaluator, "sharpe", timestamp=1.0, limit=3)
    assert len(result.candidates) == 3
    assert result.report.candidate_count == 3


# --------------------------------------------------------------------------- #
# Immutability
# --------------------------------------------------------------------------- #


def test_strategy_candidate_is_frozen() -> None:
    candidate = generate_candidates("t", SPACE)[0]
    with pytest.raises(FrozenInstanceError):
        candidate.template = "other"  # type: ignore[misc]
