"""Core structural definitions for isolated research environments.

The three collections are :class:`~alphalab.common.append_log.AppendOnlyLog` s
rather than tuples. A project accumulates every strategy registered, pipeline
defined and backtest run against it, and growing a tuple with
``(*proj.backtests, config)`` copied the whole history on each one -- the
quadratic term v2.1 removed from the execution path and v2.16 removes here. The
value semantics are identical: the log is an immutable ``Sequence``.
"""

from dataclasses import dataclass, field

from alphalab.common.append_log import AppendOnlyLog
from alphalab.studio.backtest import BacktestConfiguration
from alphalab.studio.pipeline import PipelineDefinition
from alphalab.studio.strategy import StrategyDefinition


@dataclass(frozen=True, slots=True)
class Project:
    project_id: str
    name: str
    created_at: float
    strategies: AppendOnlyLog[StrategyDefinition] = field(default_factory=AppendOnlyLog)
    pipelines: AppendOnlyLog[PipelineDefinition] = field(default_factory=AppendOnlyLog)
    backtests: AppendOnlyLog[BacktestConfiguration] = field(default_factory=AppendOnlyLog)
