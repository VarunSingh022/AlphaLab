"""AlphaLab Immutable Kernel & State Engine package exports."""

from alphalab.kernel.diff import StateDiff
from alphalab.kernel.reducer import Reducer, ReducerRegistry
from alphalab.kernel.selectors import (
    get_cash,
    get_configuration_parameter,
    get_equity,
    get_market_price,
    get_portfolio_value,
    get_positions,
    get_realized_pnl,
    get_symbol_position,
    get_unrealized_pnl,
)
from alphalab.kernel.snapshot import Snapshot, SnapshotManager
from alphalab.kernel.state import (
    BaseState,
    MarketState,
    PortfolioState,
    PositionState,
    SystemState,
)
from alphalab.kernel.store import StateStore
from alphalab.kernel.version import Version, VersionManager

__all__ = [
    "BaseState",
    "MarketState",
    "PortfolioState",
    "PositionState",
    "Reducer",
    "ReducerRegistry",
    "Snapshot",
    "SnapshotManager",
    "StateDiff",
    "StateStore",
    "SystemState",
    "Version",
    "VersionManager",
    "get_cash",
    "get_configuration_parameter",
    "get_equity",
    "get_market_price",
    "get_portfolio_value",
    "get_positions",
    "get_realized_pnl",
    "get_symbol_position",
    "get_unrealized_pnl",
]


# Deprecated in v2.6, removed in v3.0.
#
# Nothing on the execution path imports this package: the subsystem engines own
# their own immutable state, and `alphalab.persistence` owns snapshots. The
# warning is at import because that reaches exactly the callers who import it.
# See ADR-0015 decision 9.
import warnings

warnings.warn(
    "alphalab.kernel is deprecated and will be removed in v3.0. "
    "Each subsystem owns its own immutable state; use alphalab.persistence "
    "for snapshots.",
    DeprecationWarning,
    stacklevel=2,
)
