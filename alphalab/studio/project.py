"""Core structural definitions for isolated research environments."""

from dataclasses import dataclass, field

from alphalab.studio.backtest import BacktestConfiguration
from alphalab.studio.pipeline import PipelineDefinition
from alphalab.studio.strategy import StrategyDefinition


@dataclass(frozen=True, slots=True)
class Project:
    project_id: str
    name: str
    created_at: float
    strategies: tuple[StrategyDefinition, ...] = field(default_factory=tuple)
    pipelines: tuple[PipelineDefinition, ...] = field(default_factory=tuple)
    backtests: tuple[BacktestConfiguration, ...] = field(default_factory=tuple)