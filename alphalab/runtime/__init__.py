"""AlphaLab Runtime: two tiers, one owner each.

:class:`~alphalab.runtime.execution_pipeline.ExecutionPipeline` owns the
**execution step** -- market, strategy, allocation, risk, OMS, execution,
portfolio, analytics -- and is the stable core ADR-0030 freezes.
:class:`~alphalab.runtime.run.RunEngine` owns the **run**: how far it has read,
what it declined to act on, what each record produced, and the scope a stopped
run continues in.

Around them sit drivers, which own the input and the clock and nothing else:
:class:`~alphalab.runtime.session.TradingSession` over a
:class:`~alphalab.market.source.MarketDataSource`,
:class:`~alphalab.backtesting.engine.BacktestEngine` over a
:class:`~alphalab.backtesting.dataset.MarketDataset`,
:class:`~alphalab.backtesting.replay.ReplayBacktest` over :mod:`alphalab.replay`'s
cursor, and :class:`~alphalab.runtime.live.LiveSession` over a venue. That is
what makes backtest, replay, paper and live one loop rather than four; and
:mod:`alphalab.runtime.broker_routing` is the boundary an order crosses to reach
a real venue and its fills cross to come back.

Removed in v2.17
----------------
Ten modules here -- ``engine``, ``dispatcher``, ``supervisor``, ``events``,
``validation``, ``state``, ``views``, ``metrics``, ``runtime`` and ``lifecycle``
-- implemented a pure lifecycle state machine with heartbeats and dispatch
telemetry. They were deprecated in v2.14 with a v3.0 removal date; ADR-0034
brings that removal forward, and they are **gone**.

They had zero production importers when they were deprecated and zero when they
were removed. The decisive reason was never that they were unused, though: their
names collided with the canonical ones, and the canonical ones lost.
``from alphalab.runtime import create_runtime`` returned the dead function while
the one every harness calls is :func:`alphalab.strategy.runtime.create_runtime`;
``RuntimeState`` here was a heartbeat record while the ``RuntimeState`` the
pipeline threads is :class:`alphalab.strategy.state.RuntimeState`. A release that
freezes runtime ownership cannot ship :class:`~alphalab.runtime.run.RunEngine`
beside a ``RuntimeEngine`` that means something else, and v2.17 is that release.

**Nothing is aliased.** There is no compatibility shim and no PEP 562 hook here
any more. ``EventDispatcher``, ``RuntimeEngine``, ``RuntimeSupervisor``,
``RuntimeStatus``, ``create_runtime`` and the rest are simply absent from this
package; the canonical names beside them are unchanged.
"""

from alphalab.runtime.broker_routing import (
    RoutingConfig,
    RoutingDecision,
    RoutingRefusal,
    RoutingResult,
    apply_broker_execution,
    execution_report_from_broker,
    route_order,
)
from alphalab.runtime.exceptions import AlphaLabRuntimeError, RuntimeValidationError
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineConfig,
    ExecutionPipelineResult,
    ExecutionPipelineState,
    ExecutionRouting,
    SettlementRefusal,
    UnpricedAsset,
    UnpricedReason,
)
from alphalab.runtime.live import (
    LiveRunState,
    LiveSession,
    LiveStep,
    RoutedOrder,
    SettledExecution,
    live_health,
)
from alphalab.runtime.run import (
    ExecutionMode,
    RunConfig,
    RunEngine,
    RunState,
    RunStep,
    SkippedRecord,
)
from alphalab.runtime.session import TradingSession

__all__ = [
    "AlphaLabRuntimeError",
    "ExecutionMode",
    "ExecutionPipeline",
    "ExecutionPipelineConfig",
    "ExecutionPipelineResult",
    "ExecutionPipelineState",
    "ExecutionRouting",
    "LiveRunState",
    "LiveSession",
    "LiveStep",
    "RoutedOrder",
    "RoutingConfig",
    "RoutingDecision",
    "RoutingRefusal",
    "RoutingResult",
    "RunConfig",
    "RunEngine",
    "RunState",
    "RunStep",
    "RuntimeValidationError",
    "SettledExecution",
    "SettlementRefusal",
    "SkippedRecord",
    "TradingSession",
    "UnpricedAsset",
    "UnpricedReason",
    "apply_broker_execution",
    "execution_report_from_broker",
    "live_health",
    "route_order",
]
