"""AlphaLab Strategy Runtime Core."""

from alphalab.strategy.context import (
    ClockProtocol,
    HistoryAccessorProtocol,
    MarketViewProtocol,
    OrderFacadeProtocol,
    PortfolioSnapshotProtocol,
    RiskViewProtocol,
    ScopedLoggerProtocol,
    StrategyContext,
    UniverseProtocol,
)
from alphalab.strategy.dispatcher import Dispatcher
from alphalab.strategy.engine import StrategyEngine
from alphalab.strategy.events import (
    FillEvent,
    Intent,
    LifecycleTransitioned,
    OrderEvent,
    StrategyRuntimeEvent,
    TimerEvent,
)
from alphalab.strategy.exceptions import (
    HookExecutionError,
    InvalidIntentError,
    InvalidTransitionError,
    StrategyRuntimeError,
)
from alphalab.strategy.protocol import BaseStrategy, StrategyProtocol
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import LifecycleState, RuntimeState, StrategyState
from alphalab.strategy.supervisor import RuntimeSupervisor
from alphalab.strategy.validation import validate_intent
from alphalab.strategy.views import active_strategies, failed_strategies, get_strategy

__all__ = [
    "BaseStrategy",
    "ClockProtocol",
    "Dispatcher",
    "FillEvent",
    "HistoryAccessorProtocol",
    "HookExecutionError",
    "Intent",
    "InvalidIntentError",
    "InvalidTransitionError",
    "LifecycleState",
    "LifecycleTransitioned",
    "MarketViewProtocol",
    "OrderEvent",
    "OrderFacadeProtocol",
    "PortfolioSnapshotProtocol",
    "RiskViewProtocol",
    "RuntimeState",
    "RuntimeSupervisor",
    "ScopedLoggerProtocol",
    "StrategyContext",
    "StrategyEngine",
    "StrategyProtocol",
    "StrategyRuntimeError",
    "StrategyRuntimeEvent",
    "StrategyState",
    "TimerEvent",
    "UniverseProtocol",
    "active_strategies",
    "create_runtime",
    "failed_strategies",
    "get_strategy",
    "register_strategy",
    "validate_intent",
]
