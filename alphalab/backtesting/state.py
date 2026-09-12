"""The results of a finished run, as read-only projections over its state.

Neither type here holds state of its own. A run's state is
:class:`~alphalab.runtime.run.RunState`, owned by
:class:`~alphalab.runtime.run.RunEngine`; these are what the backtest and replay
drivers report once that state is finished. Storing a second copy of a fact the
run already carries is how a result comes to disagree with the run it describes
-- which is exactly what happened to ``dataset_id`` before v2.14, and why every
attribute below reads through.
"""

from __future__ import annotations

from dataclasses import dataclass

from alphalab.analytics.engine import PortfolioSnapshot
from alphalab.analytics.report import PerformanceReport
from alphalab.core.fill import Fill as CoreFill
from alphalab.core.trade import Trade as CoreTrade
from alphalab.market.record import MarketRecord
from alphalab.oms.order import Order as OMSOrder
from alphalab.portfolio.valuation import PortfolioValuation, PortfolioValuationSnapshot
from alphalab.runtime.execution_pipeline import ExecutionPipelineState, UnpricedAsset
from alphalab.runtime.run import RunConfig, RunState, RunStep

__all__ = ["BacktestResult", "ReplayResult"]


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """The finished run: its final state plus read-only views over it.

    Attributes:
        run: The finished :class:`~alphalab.runtime.run.RunState`. Everything
            else on this class reads through it.
    """

    run: RunState

    @property
    def config(self) -> RunConfig:
        """The configuration the run was driven with."""

        return self.run.config

    @property
    def state(self) -> ExecutionPipelineState:
        """Final execution-path state -- portfolio, orders, fills, analytics."""

        return self.run.pipeline

    @property
    def steps(self) -> tuple[RunStep, ...]:
        """What each record produced, in order."""

        return self.run.steps.to_tuple()

    @property
    def records_processed(self) -> int:
        """How many records the run consumed."""

        return self.run.processed

    @property
    def seed(self) -> int | None:
        """The identifier seed, if the run was seeded."""

        return self.run.config.seed

    @property
    def dataset_id(self) -> str | None:
        """The dataset this run consumed, or ``None`` when it named none.

        Read from :attr:`~alphalab.runtime.run.RunState.source_id`, which the
        driver set when the run started and which the run snapshot carries. Until
        v2.14 this was an argument to ``finalize`` that no snapshot recorded, so a
        run that stopped and continued in another process arrived here with
        whatever the second caller supplied -- usually ``None`` -- and
        :func:`~alphalab.lifecycle.evidence.derive_evidence` then refused it.
        ``None`` is an honest absence and not a default identity; see ADR-0017
        and ADR-0030.
        """

        return self.run.source_id

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

    @property
    def unpriced_assets(self) -> tuple[UnpricedAsset, ...]:
        """Assets this run declined to trade for want of a price, in first-drop order.

        Read from the run rather than stored again, so a result cannot come to
        disagree with the run it describes. Empty for a run that priced
        everything its strategies named.
        """

        return self.run.unpriced_assets


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

    @property
    def unpriced_assets(self) -> tuple[UnpricedAsset, ...]:
        """What the replayed run declined to trade, read from the run it wraps."""

        return self.backtest.unpriced_assets
