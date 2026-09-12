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
:class:`~alphalab.backtesting.dataset.MarketDataset`, and
:class:`~alphalab.backtesting.replay.ReplayBacktest` over
:mod:`alphalab.replay`'s cursor. That is what makes backtest, replay, paper and
live one loop rather than four; and :mod:`alphalab.runtime.broker_routing` is
the boundary an order crosses to reach a real venue and its fills cross to come
back.

Deprecated in v2.14, removed in v3.0
------------------------------------
Ten modules here -- ``engine``, ``dispatcher``, ``supervisor``, ``events``,
``validation``, ``state``, ``views``, ``metrics``, ``runtime`` and ``lifecycle``
-- implement a pure lifecycle state machine with heartbeats and dispatch
telemetry. Measured at v2.13 they had **zero** production importers: nothing
outside this package and its own unit test touched any of them, and nothing on
the execution path ever did.

They are deprecated for a second reason beyond being unused, and it is the
decisive one: their names collide with the canonical ones, and the canonical
ones lose. ``from alphalab.runtime import create_runtime`` returns the dead
function, while the one every harness calls is
:func:`alphalab.strategy.runtime.create_runtime`. ``RuntimeState`` here is a
heartbeat record, while the ``RuntimeState`` the pipeline actually threads is
:class:`alphalab.strategy.state.RuntimeState`. A release that settles runtime
ownership cannot ship :class:`~alphalab.runtime.run.RunEngine` beside a
``RuntimeEngine`` that means something else.

**Nothing is removed here, and nothing is aliased.** Those modules keep their
behaviour, their signatures and their tests for the whole of v2.x.

The warning fires on **use of a deprecated name through this package**, not on
importing the package, because this package is the execution path -- an
import-time warning would fire on every consumer of ``ExecutionPipeline`` to
deprecate names that path never uses, which is how people learn to filter
``DeprecationWarning``, and then the next real notice goes unread. That is the
PEP 562 mechanism :mod:`alphalab.persistence` already uses and for the same
reason; see ADR-0029 decision 6 and ADR-0030.
"""

from typing import TYPE_CHECKING

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
from alphalab.runtime.run import (
    ExecutionMode,
    RunConfig,
    RunEngine,
    RunState,
    RunStep,
    SkippedRecord,
)
from alphalab.runtime.session import TradingSession

if TYPE_CHECKING:
    # Deprecated, and served at runtime by ``__getattr__`` below so that touching
    # one of these names warns. Imported here for type checkers only: a caller
    # that still uses the orphan state machine keeps its exact types, and a
    # deprecation is not a reason to make working code un-checkable.
    from alphalab.runtime.dispatcher import EventDispatcher
    from alphalab.runtime.engine import RuntimeEngine
    from alphalab.runtime.events import (
        DispatchCompleted,
        DispatchFailed,
        Heartbeat,
        RuntimeEvent,
        RuntimeFailed,
        RuntimePaused,
        RuntimeResumed,
        RuntimeStarted,
        RuntimeStopped,
    )
    from alphalab.runtime.exceptions import InvalidRuntimeTransitionError
    from alphalab.runtime.lifecycle import RuntimeStatus
    from alphalab.runtime.metrics import RuntimeMetrics
    from alphalab.runtime.runtime import create_runtime
    from alphalab.runtime.state import RuntimeState, SupervisorState
    from alphalab.runtime.supervisor import RuntimeSupervisor
    from alphalab.runtime.validation import (
        validate_dispatch,
        validate_heartbeat_config,
        validate_transition,
    )
    from alphalab.runtime.views import (
        dispatcher_statistics,
        runtime_metrics,
        runtime_status,
        uptime,
    )

__all__ = [
    "AlphaLabRuntimeError",
    "DispatchCompleted",
    "DispatchFailed",
    "EventDispatcher",
    "ExecutionMode",
    "ExecutionPipeline",
    "ExecutionPipelineConfig",
    "ExecutionPipelineResult",
    "ExecutionPipelineState",
    "ExecutionRouting",
    "Heartbeat",
    "InvalidRuntimeTransitionError",
    "RoutingConfig",
    "RoutingDecision",
    "RoutingRefusal",
    "RoutingResult",
    "RunConfig",
    "RunEngine",
    "RunState",
    "RunStep",
    "RuntimeEngine",
    "RuntimeEvent",
    "RuntimeFailed",
    "RuntimeMetrics",
    "RuntimePaused",
    "RuntimeResumed",
    "RuntimeStarted",
    "RuntimeState",
    "RuntimeStatus",
    "RuntimeStopped",
    "RuntimeSupervisor",
    "RuntimeValidationError",
    "SettlementRefusal",
    "SkippedRecord",
    "SupervisorState",
    "TradingSession",
    "UnpricedAsset",
    "UnpricedReason",
    "apply_broker_execution",
    "create_runtime",
    "dispatcher_statistics",
    "execution_report_from_broker",
    "route_order",
    "runtime_metrics",
    "runtime_status",
    "uptime",
    "validate_dispatch",
    "validate_heartbeat_config",
    "validate_transition",
]

#: Deprecated name -> the module that still defines it, unchanged.
#:
#: The submodules themselves do not warn. A direct
#: ``from alphalab.runtime.engine import RuntimeEngine`` is silent, exactly as
#: ``from alphalab.persistence.storage import MemoryStorage`` is: the notice
#: guards the package surface, which is the one a reader is steered toward.
#:
#: ``AlphaLabRuntimeError`` and ``RuntimeValidationError`` are deliberately not
#: here. They live in the same ``exceptions`` module but are canonical --
#: ``execution_pipeline`` raises ``RuntimeValidationError`` -- and only
#: ``InvalidRuntimeTransitionError``, which nothing but the orphan state machine
#: raises, is deprecated from it.
_DEPRECATED_RUNTIME: dict[str, str] = {
    "DispatchCompleted": "events",
    "DispatchFailed": "events",
    "EventDispatcher": "dispatcher",
    "Heartbeat": "events",
    "InvalidRuntimeTransitionError": "exceptions",
    "RuntimeEngine": "engine",
    "RuntimeEvent": "events",
    "RuntimeFailed": "events",
    "RuntimeMetrics": "metrics",
    "RuntimePaused": "events",
    "RuntimeResumed": "events",
    "RuntimeStarted": "events",
    "RuntimeState": "state",
    "RuntimeStatus": "lifecycle",
    "RuntimeStopped": "events",
    "RuntimeSupervisor": "supervisor",
    "SupervisorState": "state",
    "create_runtime": "runtime",
    "dispatcher_statistics": "views",
    "runtime_metrics": "views",
    "runtime_status": "views",
    "uptime": "views",
    "validate_dispatch": "validation",
    "validate_heartbeat_config": "validation",
    "validate_transition": "validation",
}


def __getattr__(name: str) -> object:
    """Serve a deprecated runtime name with a notice, on use rather than on import.

    The orphan lifecycle state machine is deprecated in v2.14 and removed in
    v3.0. It never drove the execution path;
    :class:`~alphalab.runtime.run.RunEngine` owns a run and
    :class:`~alphalab.runtime.execution_pipeline.ExecutionPipeline` owns the
    step. The warning is deliberately *not* at module import: this package is
    the execution path, so an import-time warning would fire on every consumer
    of ``ExecutionPipeline`` to deprecate names that path never uses. See
    ADR-0030.

    The object served is the same object it always was. This is a notice, not an
    alias and not a shim.
    """

    module = _DEPRECATED_RUNTIME.get(name)
    if module is not None:
        import importlib
        import warnings

        warnings.warn(
            f"alphalab.runtime.{name} is deprecated and will be removed in v3.0. "
            "It belongs to a lifecycle state machine that never drove the execution "
            "path and whose names collide with the canonical ones; use "
            "alphalab.runtime.RunEngine for a run, alphalab.runtime.ExecutionPipeline "
            "for the execution step, and alphalab.strategy for strategy runtime state. "
            f"Importing it directly from alphalab.runtime.{module} does not warn.",
            DeprecationWarning,
            stacklevel=2,
        )
        return getattr(importlib.import_module(f"alphalab.runtime.{module}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
