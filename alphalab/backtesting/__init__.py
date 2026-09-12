"""AlphaLab Backtesting: one deterministic path from dataset to analytics.

This package does not add another engine. It composes the ones AlphaLab already
has -- market, strategy, allocation, risk, OMS, execution, portfolio, analytics
-- through :class:`~alphalab.runtime.execution_pipeline.ExecutionPipeline`, so a
backtest runs the production execution path rather than a parallel model of it.

Two drivers, one loop:

* :class:`~alphalab.backtesting.engine.BacktestEngine` walks a
  :class:`~alphalab.backtesting.dataset.MarketDataset` directly.
* :class:`~alphalab.backtesting.replay.ReplayBacktest` walks the same dataset
  through :mod:`alphalab.replay`'s cursor.

Both hand every record to :meth:`~alphalab.runtime.run.RunEngine.advance`, so
they share order, fill and accounting semantics by construction.

As of v2.14 neither owns run state. A run is a
:class:`~alphalab.runtime.run.RunState` configured by a
:class:`~alphalab.runtime.run.RunConfig`, and these are drivers over it --
``BacktestConfig``, ``BacktestState`` and ``BacktestStep`` are gone, and
``BacktestStep`` is :class:`~alphalab.runtime.run.RunStep`. See ADR-0030.
"""

from alphalab.backtesting.dataset import (
    MarketDataset,
    MarketInput,
    MarketRecord,
    validate_dataset,
)
from alphalab.backtesting.engine import (
    BacktestEngine,
    advance,
    finalize,
    id_scope,
    id_source,
    initialize,
    publish,
)
from alphalab.backtesting.exceptions import (
    BacktestError,
    DatasetValidationError,
    UnsupportedRecordError,
)
from alphalab.backtesting.replay import ReplayBacktest, session_for
from alphalab.backtesting.state import BacktestResult, ReplayResult
from alphalab.backtesting.views import (
    commission_paid,
    equity_values,
    executed_fills,
    final_cash,
    final_equity,
    performance_report,
    realized_pnl,
    steps_with_fills,
    submitted_orders,
    unrealized_pnl,
)
from alphalab.execution.policy import (
    FillDecision,
    FillPolicy,
    ImmediateFill,
    LiquidityCappedFill,
    LiquidityContext,
    StaticFill,
)
from alphalab.runtime.execution_pipeline import UnpricedAsset, UnpricedReason
from alphalab.runtime.run import ExecutionMode, RunConfig, RunState, RunStep

__all__ = [
    "BacktestEngine",
    "BacktestError",
    "BacktestResult",
    "DatasetValidationError",
    "ExecutionMode",
    "FillDecision",
    "FillPolicy",
    "ImmediateFill",
    "LiquidityCappedFill",
    "LiquidityContext",
    "MarketDataset",
    "MarketInput",
    "MarketRecord",
    "ReplayBacktest",
    "ReplayResult",
    "RunConfig",
    "RunState",
    "RunStep",
    "StaticFill",
    "UnpricedAsset",
    "UnpricedReason",
    "UnsupportedRecordError",
    "advance",
    "commission_paid",
    "equity_values",
    "executed_fills",
    "final_cash",
    "final_equity",
    "finalize",
    "id_scope",
    "id_source",
    "initialize",
    "performance_report",
    "publish",
    "realized_pnl",
    "session_for",
    "steps_with_fills",
    "submitted_orders",
    "unrealized_pnl",
    "validate_dataset",
]
