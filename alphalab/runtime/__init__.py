"""AlphaLab Live Trading Runtime Layer.

:class:`~alphalab.runtime.execution_pipeline.ExecutionPipeline` is the
integrated execution path. :class:`~alphalab.runtime.session.TradingSession`
drives it from a market-data source, which is what makes backtest, replay,
paper and live one loop rather than four; and
:mod:`alphalab.runtime.broker_routing` is the boundary an order crosses to
reach a real venue and its fills cross to come back.
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
from alphalab.runtime.exceptions import (
    AlphaLabRuntimeError,
    InvalidRuntimeTransitionError,
    RuntimeValidationError,
)
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineConfig,
    ExecutionPipelineResult,
    ExecutionPipelineState,
    ExecutionRouting,
)
from alphalab.runtime.lifecycle import RuntimeStatus
from alphalab.runtime.metrics import RuntimeMetrics
from alphalab.runtime.runtime import create_runtime
from alphalab.runtime.session import (
    ExecutionMode,
    SessionConfig,
    SessionState,
    SkippedRecord,
    TradingSession,
)
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
    "SessionConfig",
    "SessionState",
    "SkippedRecord",
    "SupervisorState",
    "TradingSession",
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
