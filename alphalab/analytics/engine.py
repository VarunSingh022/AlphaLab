"""Pure functional Analytics Engine generating historical research reports."""

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final

from alphalab.analytics.attribution import TradeRecord, calculate_attribution
from alphalab.analytics.drawdown import calculate_drawdowns
from alphalab.analytics.events import ReportGenerated
from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.analytics.exposure import calculate_exposure
from alphalab.analytics.metrics import (
    calmar_ratio,
    conditional_var,
    sharpe_ratio,
    sortino_ratio,
    value_at_risk,
)
from alphalab.analytics.report import PerformanceReport, Periodicity, ReturnSummary, RiskSummary
from alphalab.analytics.returns import (
    annualized_volatility,
    arithmetic_return,
    cagr,
    geometric_return,
    total_return,
)
from alphalab.analytics.state import AnalyticsState
from alphalab.analytics.summary import calculate_trade_metrics
from alphalab.analytics.validation import validate_capital, validate_returns
from alphalab.common.arithmetic import ACCOUNTING_CONTEXT, in_accounting_context
from alphalab.common.ids import new_id

#: Seconds in a year of 365.25 days: the span a curve's years are measured in.
SECONDS_PER_YEAR: Final = 365.25 * 86400.0


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    """Lightweight immutable extract representing portfolio state over time."""

    timestamp: float
    total_equity: Decimal
    cash: Decimal
    long_exposure: Decimal
    short_exposure: Decimal


class AnalyticsEngine:
    """Stateless functional engine orchestrating read-only metrics compilation."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def initialize() -> AnalyticsState:
        return AnalyticsState()

    @staticmethod
    @in_accounting_context
    def compile_report(
        state: AnalyticsState,
        snapshots: Sequence[PortfolioSnapshot],
        trades: Sequence[TradeRecord],
        timestamp: float,
        years_elapsed: float | None = None,
        risk_free_rate: float = 0.0,
        periods_per_year: float | None = None,
    ) -> AnalyticsState:
        """Compile one :class:`PerformanceReport` from an equity curve and its fills.

        The curve is first reduced to **one point per instant** -- the last
        snapshot at each timestamp, which is the book after everything that
        happened at that instant. Returns are taken between consecutive
        instants; annualization uses ``periods_per_year`` when declared, and
        otherwise the frequency the curve actually has (return periods divided
        by the years they span). CAGR compounds over ``years_elapsed`` when
        declared, and otherwise over the curve's own span. The report records
        which basis each came from.

        Args:
            years_elapsed: Declared span for CAGR, in years. ``None`` derives it
                from the curve's first and last instants (365.25-day years).
            risk_free_rate: Annual rate the Sharpe and Sortino ratios are in
                excess of; recorded on the report.
            periods_per_year: Declared annualization. ``None`` observes it.

        Raises:
            AnalyticsValidationError: If the snapshots go back in time, or a
                declared figure is not positive and finite.
        """
        if not snapshots:
            return state

        for label, declared in (
            ("years_elapsed", years_elapsed),
            ("periods_per_year", periods_per_year),
        ):
            if declared is not None and (not math.isfinite(declared) or declared <= 0.0):
                raise AnalyticsValidationError(
                    f"A declared {label} must be positive and finite, got {declared!r}."
                )

        # 1. One equity point per instant.
        points = _per_instant(snapshots)
        start_cap = points[0].total_equity
        end_cap = points[-1].total_equity
        validate_capital(start_cap)
        validate_capital(end_cap)

        # 2. Returns between consecutive instants.
        period_returns: list[float] = []
        equity_curve: list[float] = [float(points[0].total_equity)]
        for previous, current in itertools.pairwise(points):
            prev_eq = previous.total_equity
            curr_eq = current.total_equity
            ret = float((curr_eq - prev_eq) / prev_eq) if prev_eq > Decimal("0") else 0.0
            period_returns.append(ret)
            equity_curve.append(float(curr_eq))

        ret_tuple = tuple(period_returns)
        if ret_tuple:
            validate_returns(ret_tuple)

        # 3. The basis: how long, and how many periods make a year.
        span = (points[-1].timestamp - points[0].timestamp) / SECONDS_PER_YEAR
        observed_years = span if span > 0.0 else None
        years = years_elapsed if years_elapsed is not None else observed_years
        if periods_per_year is not None:
            periods: float | None = periods_per_year
            periodicity = Periodicity.DECLARED
        elif observed_years is not None and ret_tuple:
            periods = len(ret_tuple) / observed_years
            periodicity = Periodicity.OBSERVED
        else:
            periods = None
            periodicity = Periodicity.UNDEFINED

        # 4. Compute Modules
        tot_ret = total_return(start_cap, end_cap)
        cagr_val = cagr(start_cap, end_cap, years) if years is not None else None

        returns_summary = ReturnSummary(
            total_return=tot_ret,
            cagr=cagr_val,
            arithmetic_return=arithmetic_return(ret_tuple),
            geometric_return=geometric_return(ret_tuple),
            period_returns=ret_tuple,
            periods_per_year=periods,
            periodicity=periodicity,
            years_elapsed=years,
        )

        drawdown_metrics = calculate_drawdowns(tuple(equity_curve))

        risk_summary = RiskSummary(
            sharpe_ratio=(
                sharpe_ratio(ret_tuple, risk_free_rate, periods) if periods is not None else None
            ),
            sortino_ratio=(
                sortino_ratio(ret_tuple, risk_free_rate, periods) if periods is not None else None
            ),
            calmar_ratio=calmar_ratio(cagr_val, drawdown_metrics.max_drawdown),
            value_at_risk_95=value_at_risk(ret_tuple, 0.95),
            cvar_95=conditional_var(ret_tuple, 0.95),
            annualized_volatility=(
                annualized_volatility(ret_tuple, periods) if periods is not None else None
            ),
            risk_free_rate=risk_free_rate,
        )

        final_snap = points[-1]
        exposure_metrics = calculate_exposure(
            final_snap.long_exposure, final_snap.short_exposure, final_snap.cash
        )

        total_notional = sum((t.notional_value for t in trades), Decimal("0"))
        avg_equity = ACCOUNTING_CONTEXT.divide(
            sum((s.total_equity for s in points), Decimal("0")), Decimal(len(points))
        )

        trade_metrics = calculate_trade_metrics(
            profits=tuple(t.realized_pnl for t in trades),
            holding_periods=tuple(t.holding_period_seconds for t in trades),
            total_traded_notional=total_notional,
            average_equity=avg_equity,
        )

        attribution_metrics = calculate_attribution(trades)

        # 5. Assembly
        report_id = AnalyticsEngine._create_id()
        report = PerformanceReport(
            report_id=report_id,
            timestamp=timestamp,
            returns=returns_summary,
            risk=risk_summary,
            drawdowns=drawdown_metrics,
            exposure=exposure_metrics,
            trades=trade_metrics,
            attribution=attribution_metrics,
            ending_capital=end_cap,
        )

        event = ReportGenerated(
            event_id=AnalyticsEngine._create_id(),
            timestamp=timestamp,
            report_id=report_id,
            num_snapshots=len(points),
            num_trades=len(trades),
        )

        return replace(
            state,
            reports=(*state.reports, report),
            events=(*state.events, event),
        )


def _per_instant(snapshots: Sequence[PortfolioSnapshot]) -> tuple[PortfolioSnapshot, ...]:
    """The last snapshot at each instant, in time order.

    Raises:
        AnalyticsValidationError: If an instant precedes the one before it: a
            curve that goes back in time has no returns to take.
    """

    points: list[PortfolioSnapshot] = []
    for snapshot in snapshots:
        if points and snapshot.timestamp < points[-1].timestamp:
            raise AnalyticsValidationError(
                f"The equity curve goes back in time: {snapshot.timestamp} follows "
                f"{points[-1].timestamp}."
            )
        if points and snapshot.timestamp == points[-1].timestamp:
            points[-1] = snapshot
        else:
            points.append(snapshot)
    return tuple(points)
