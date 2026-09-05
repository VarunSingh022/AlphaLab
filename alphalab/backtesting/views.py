"""Pure queries exposing transparent access to a finished run."""

from collections.abc import Sequence
from decimal import Decimal

from alphalab.analytics.report import PerformanceReport
from alphalab.backtesting.state import BacktestResult, BacktestStep
from alphalab.core.fill import Fill
from alphalab.oms.order import Order


def final_equity(result: BacktestResult) -> Decimal:
    """Total account equity at the end of the run."""
    return result.valuation.equity


def final_cash(result: BacktestResult) -> Decimal:
    """Cash balance at the end of the run."""
    return result.valuation.cash


def realized_pnl(result: BacktestResult) -> Decimal:
    """Cumulative P&L crystallised by reductions and closes."""
    return result.valuation.realized_pnl


def unrealized_pnl(result: BacktestResult) -> Decimal:
    """Open P&L across all positions at their final marks."""
    return result.valuation.unrealized_pnl


def commission_paid(result: BacktestResult) -> Decimal:
    """Cumulative commissions expensed over the run."""
    return result.valuation.commission_paid


def equity_values(result: BacktestResult) -> tuple[Decimal, ...]:
    """Equity at every recorded snapshot, in order.

    ``BacktestResult.equity_curve`` carries the whole snapshots; this is the
    equity series alone, for plotting or comparison.
    """
    return tuple(snapshot.total_equity for snapshot in result.equity_curve)


def submitted_orders(result: BacktestResult) -> Sequence[Order]:
    """Every order the run submitted, in submission order."""
    return result.orders


def executed_fills(result: BacktestResult) -> Sequence[Fill]:
    """Every fill the run produced, in execution order."""
    return result.fills


def steps_with_fills(result: BacktestResult) -> Sequence[BacktestStep]:
    """Only the records that actually traded."""
    return tuple(step for step in result.steps if step.fills)


def performance_report(result: BacktestResult) -> PerformanceReport | None:
    """The compiled performance report, if analytics ran."""
    return result.report
