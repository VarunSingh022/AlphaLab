"""Pure queries to expose transparent Execution Engine access."""

from collections.abc import Sequence

from alphalab.execution.report import ExecutionReport
from alphalab.execution.state import ExecutionState


def report(state: ExecutionState, execution_id: str) -> ExecutionReport | None:
    """Looks up a specific execution report by ID."""
    return state.reports.get(execution_id)


def all_reports(state: ExecutionState) -> Sequence[ExecutionReport]:
    """Returns all execution reports."""
    return tuple(state.reports.values())


def reports_for_order(state: ExecutionState, order_id: str) -> Sequence[ExecutionReport]:
    """Returns all execution reports tied to a specific order."""
    return tuple(r for r in state.history if r.order_id == order_id)


def reports_for_asset(state: ExecutionState, asset_id: str) -> Sequence[ExecutionReport]:
    """Returns all execution reports tied to a specific asset."""
    return tuple(r for r in state.history if r.asset_id == asset_id)
