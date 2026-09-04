"""AlphaLab AI Research Assistant.

Strategy generation, research automation, report generation, and workflow
orchestration -- implemented deterministically. This repository has no LLM and
no network access, so "AI Research Assistant" here means a reproducible
grid-search research driver, not a learned generative model. Every step is a
pure function of its inputs:

- generation: enumerate a researcher-defined parameter grid into
  ``StrategyCandidate`` points (``generate_candidates``).
- evaluation: score every candidate through a caller-supplied evaluator (a
  backtest wrapper, a walk-forward harness, ...) and rank the results
  (``evaluate_candidates``, ``rank_evaluations``, ``top_k``).
- report: summarise a ranked search as an ``AssistantReport`` plus Markdown
  (``build_report``, ``render_markdown``).
- workflow: chain all of the above in one call, optionally recording each
  candidate into an ``alphalab.experiment_tracking`` tracker
  (``run_research_workflow``).
- studio_bridge: lift a chosen candidate into the canonical
  ``alphalab.studio`` ``StrategyDefinition`` (``to_strategy_definition``).

The assistant does not implement backtesting, optimisation, or model training
-- it orchestrates the engines that do.
"""

from alphalab.research_assistant.evaluation import (
    CandidateEvaluation,
    Evaluator,
    best_evaluation,
    evaluate_candidates,
    rank_evaluations,
    top_k,
)
from alphalab.research_assistant.exceptions import (
    ResearchAssistantError,
    ResearchAssistantInputError,
)
from alphalab.research_assistant.generation import (
    ParameterSpace,
    StrategyCandidate,
    candidate_count,
    generate_candidates,
)
from alphalab.research_assistant.report import AssistantReport, build_report, render_markdown
from alphalab.research_assistant.studio_bridge import to_strategy_definition
from alphalab.research_assistant.workflow import ResearchWorkflowResult, run_research_workflow

__all__ = [
    "AssistantReport",
    "CandidateEvaluation",
    "Evaluator",
    "ParameterSpace",
    "ResearchAssistantError",
    "ResearchAssistantInputError",
    "ResearchWorkflowResult",
    "StrategyCandidate",
    "best_evaluation",
    "build_report",
    "candidate_count",
    "evaluate_candidates",
    "generate_candidates",
    "rank_evaluations",
    "render_markdown",
    "run_research_workflow",
    "to_strategy_definition",
    "top_k",
]
