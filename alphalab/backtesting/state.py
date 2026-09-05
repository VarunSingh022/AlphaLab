"""Immutable state and results of a backtest run."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.analytics.engine import PortfolioSnapshot
from alphalab.analytics.report import PerformanceReport
from alphalab.backtesting.config import BacktestConfig
from alphalab.backtesting.dataset import MarketRecord
from alphalab.common.append_log import AppendOnlyLog
from alphalab.core.fill import Fill as CoreFill
from alphalab.core.trade import Trade as CoreTrade
from alphalab.execution.report import ExecutionReport
from alphalab.oms.order import Order as OMSOrder
from alphalab.portfolio.valuation import PortfolioValuation, PortfolioValuationSnapshot
from alphalab.runtime.execution_pipeline import ExecutionPipelineState


@dataclass(frozen=True, slots=True)
class BacktestStep:
    """What one dataset record produced when it went through the path."""

    index: int
    event_id: str
    timestamp: float
    orders: tuple[OMSOrder, ...]
    reports: tuple[ExecutionReport, ...]
    fills: tuple[CoreFill, ...]
    equity: Decimal


@dataclass(frozen=True, slots=True)
class BacktestState:
    """Immutable snapshot of a run in progress.

    The run owns no accounting of its own: everything about the portfolio, the
    orders and the fills lives on ``pipeline``, the same
    :class:`~alphalab.runtime.execution_pipeline.ExecutionPipelineState` the
    live path threads. What this state adds is only the run's own bookkeeping --
    where it is in the dataset, and what each record produced.
    """

    config: BacktestConfig
    pipeline: ExecutionPipelineState
    processed: int = 0
    current_timestamp: float = 0.0
    steps: AppendOnlyLog[BacktestStep] = field(default_factory=AppendOnlyLog)


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """The finished run: its final state plus read-only views over it."""

    config: BacktestConfig
    state: ExecutionPipelineState
    steps: tuple[BacktestStep, ...]
    records_processed: int
    seed: int | None

    @property
    def orders(self) -> tuple[OMSOrder, ...]:
        """Every order the run submitted, in submission order."""

        return tuple(self.state.oms.orders.orders())

    @property
    def fills(self) -> tuple[CoreFill, ...]:
        """Every fill the run produced, in execution order."""

        return self.state.fills.to_tuple()

    @property
    def trades(self) -> tuple[CoreTrade, ...]:
        """Every trade the run produced, in execution order."""

        return self.state.trades.to_tuple()

    @property
    def equity_curve(self) -> tuple[PortfolioSnapshot, ...]:
        """One portfolio snapshot per processed record, plus one at funding."""

        return self.state.portfolio_snapshots.to_tuple()

    @property
    def valuation(self) -> PortfolioValuationSnapshot:
        """Final mark-to-market valuation of the portfolio."""

        return PortfolioValuation.snapshot(
            self.state.portfolio,
            self.state.portfolio_snapshots[-1].timestamp,
            self.config.pipeline.currency,
        )

    @property
    def report(self) -> PerformanceReport | None:
        """The compiled performance report, if analytics ran."""

        reports = self.state.analytics.reports
        return reports[-1] if reports else None


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """A replay run: the backtest result plus the replay cursor it was driven by."""

    backtest: BacktestResult
    replay_status: str
    records_replayed: int
    last_record: MarketRecord | None
