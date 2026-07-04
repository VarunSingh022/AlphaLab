"""Immutable representations of engine outcomes tracked by the Studio."""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class BacktestResult:
    result_id: str
    backtest_id: str
    strategy_id: str
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    metadata: Mapping[str, str] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class ResearchResult:
    result_id: str
    strategy_id: str
    bias_score: float
    robustness_score: float
    capacity_score: float
    overall_score: float

@dataclass(frozen=True, slots=True)
class PipelineResult:
    result_id: str
    pipeline_id: str
    success: bool
    execution_time_seconds: float
    step_metrics: Mapping[str, float] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class ExperimentResult:
    experiment_id: str
    project_id: str
    parameters: Mapping[str, float]
    target_metric: float
    timestamp: float