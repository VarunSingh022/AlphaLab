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
    """The finished run: its final state plus read-only views over it.

    Attributes:
        config: The configuration the run was driven with.
        state: Final execution-path state -- portfolio, orders, fills, analytics.
        steps: What each dataset record produced, in order.
        records_processed: How many records the run consumed.
        seed: The identifier seed, if the run was seeded.
        dataset_id: The dataset this run consumed, or ``None`` when the caller
            drove ``initialize``/``advance``/``finalize`` by hand and named no
            dataset. Until v2.7 the identity was discarded at this boundary:
            :meth:`~alphalab.backtesting.engine.BacktestEngine.run` had the
            dataset in scope and the result did not record it, which is why
            :class:`~alphalab.lifecycle.evidence.ValidationEvidence` has to take
            ``dataset_id`` from a caller who merely asserts it. ``None`` is an
            honest absence and not a default identity -- see ADR-0017.
    """

    config: BacktestConfig
    state: ExecutionPipelineState
    steps: tuple[BacktestStep, ...]
    records_processed: int
    seed: int | None
    dataset_id: str | None = None

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

    @property
    def dataset_id(self) -> str | None:
        """The dataset this replay consumed.

        Read from the run it wraps rather than stored again, so a replay cannot
        come to disagree with its own backtest about what it replayed.
        """

        return self.backtest.dataset_id
