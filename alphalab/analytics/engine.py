"""Pure functional Analytics Engine generating historical research reports."""

import uuid
from dataclasses import dataclass, replace
from decimal import Decimal

from alphalab.analytics.attribution import TradeRecord, calculate_attribution
from alphalab.analytics.drawdown import calculate_drawdowns
from alphalab.analytics.events import ReportGenerated
from alphalab.analytics.exposure import calculate_exposure
from alphalab.analytics.metrics import (
    calmar_ratio,
    conditional_var,
    sharpe_ratio,
    sortino_ratio,
    value_at_risk,
)
from alphalab.analytics.report import PerformanceReport, ReturnSummary, RiskSummary
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
        return str(uuid.uuid4())

    @staticmethod
    def initialize() -> AnalyticsState:
        return AnalyticsState()

    @staticmethod
    def compile_report(
        state: AnalyticsState,
        snapshots: tuple[PortfolioSnapshot, ...],
        trades: tuple[TradeRecord, ...],
        timestamp: float,
        years_elapsed: float = 1.0,
        risk_free_rate: float = 0.0,
    ) -> AnalyticsState:
        """
        Consumes immutable sequences of historical portfolio states and trade records
        to generate a strictly deterministic, pure output PerformanceReport.
        """
        if not snapshots:
            return state

        # 1. Base Validation
        start_cap = snapshots[0].total_equity
        end_cap = snapshots[-1].total_equity
        validate_capital(start_cap)
        validate_capital(end_cap)

        # 2. Extract Returns
        period_returns: list[float] = []
        equity_curve: list[float] = [float(snapshots[0].total_equity)]

        for i in range(1, len(snapshots)):
            prev_eq = snapshots[i - 1].total_equity
            curr_eq = snapshots[i].total_equity
            ret = float((curr_eq - prev_eq) / prev_eq) if prev_eq > Decimal("0") else 0.0
            period_returns.append(ret)
            equity_curve.append(float(curr_eq))

        ret_tuple = tuple(period_returns)
        if ret_tuple:
            validate_returns(ret_tuple)

        # 3. Compute Modules
        tot_ret = total_return(start_cap, end_cap)
        cagr_val = cagr(start_cap, end_cap, max(years_elapsed, 0.01))

        returns_summary = ReturnSummary(
            total_return=tot_ret,
            cagr=cagr_val,
            arithmetic_return=arithmetic_return(ret_tuple),
            geometric_return=geometric_return(ret_tuple),
            daily_returns=ret_tuple,
        )

        drawdown_metrics = calculate_drawdowns(tuple(equity_curve))

        risk_summary = RiskSummary(
            sharpe_ratio=sharpe_ratio(ret_tuple, risk_free_rate),
            sortino_ratio=sortino_ratio(ret_tuple, risk_free_rate),
            calmar_ratio=calmar_ratio(cagr_val, drawdown_metrics.max_drawdown),
            value_at_risk_95=value_at_risk(ret_tuple, 0.95),
            cvar_95=conditional_var(ret_tuple, 0.95),
            annualized_volatility=annualized_volatility(ret_tuple),
        )

        final_snap = snapshots[-1]
        exposure_metrics = calculate_exposure(
            final_snap.long_exposure, final_snap.short_exposure, final_snap.cash
        )

        total_notional = sum((t.notional_value for t in trades), Decimal("0"))
        avg_equity = sum((s.total_equity for s in snapshots), Decimal("0")) / len(snapshots)

        trade_metrics = calculate_trade_metrics(
            profits=tuple(t.realized_pnl for t in trades),
            holding_periods=tuple(t.holding_period_seconds for t in trades),
            total_traded_notional=total_notional,
            average_equity=avg_equity,
        )

        attribution_metrics = calculate_attribution(trades)

        # 4. Assembly
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
            num_snapshots=len(snapshots),
            num_trades=len(trades),
        )

        return replace(
            state,
            reports=(*state.reports, report),
            events=(*state.events, event),
        )
