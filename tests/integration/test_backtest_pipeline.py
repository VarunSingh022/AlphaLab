"""Integration tests for the unified backtest path.

Nothing here is stubbed. Every test drives a real dataset through
market -> strategy -> allocation -> risk -> OMS -> execution -> portfolio ->
analytics, and asserts on what the real engines produced.

Prices are deliberately not round -- 100.005, 120.007 -- and quantities are
fractional, because that is where the v2.1 monetary precision policy earns its
keep: a mid halfway between cents used to put the portfolio accounting identity
out by a cent per trade.
"""

from collections.abc import Mapping
from decimal import Decimal
from uuid import uuid4

from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    FillPolicy,
    LiquidityCappedFill,
    MarketDataset,
    StaticFill,
    equity_values,
    steps_with_fills,
)
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.fill import FillStatus
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.slippage import FixedSlippage
from alphalab.oms.status import OrderStatus
from alphalab.risk.limits import RiskLimits
from tests.integration.harness import (
    START_CASH,
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    permissive_risk_limits,
    running_strategy_state,
    sized_quote,
)

# Non-round prices: mids that fall between cents.
MIDS = [
    Decimal("100.005"),
    Decimal("120.007"),
    Decimal("119.003"),
    Decimal("121.009"),
    Decimal("118.001"),
]


def _run(
    plan: Mapping[float, Decimal],
    fill_policy: FillPolicy | None = None,
    simulator: ExecutionSimulator | None = None,
    risk_limits: RiskLimits | None = None,
) -> tuple[BacktestResult, str]:
    """Run one scripted scenario and hand back the result and its asset."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())
    result = BacktestEngine.run(
        backtest_config(
            strategy_id,
            fill_policy=fill_policy,
            simulator=simulator,
            risk_limits=risk_limits,
        ),
        dataset_of_quotes(asset_id, MIDS),
        running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, plan)),
        context_factory,
    )
    return result, asset_id


def _identity_holds(result: BacktestResult) -> bool:
    """equity == deposits + realized + unrealized - commissions."""

    valuation = result.valuation
    return valuation.equity == (
        START_CASH + valuation.realized_pnl + valuation.unrealized_pnl - valuation.commission_paid
    )


# ---------------------------------------------------------------------------
# The path runs end to end
# ---------------------------------------------------------------------------


def test_a_backtest_walks_the_whole_path() -> None:
    result, asset_id = _run({2.0: Decimal("10"), 4.0: Decimal("-4")})

    assert result.records_processed == 5
    assert len(result.orders) == 2
    assert len(result.fills) == 2
    assert len(result.trades) == 2
    assert result.state.portfolio.positions[asset_id].quantity == Decimal("6.000000")
    assert result.report is not None


def test_every_record_produces_one_equity_point() -> None:
    """Including the records that only marked the book, plus one at funding."""

    result, _ = _run({2.0: Decimal("10")})

    assert len(equity_values(result)) == 6
    assert len(steps_with_fills(result)) == 1


def test_orders_reach_a_terminal_state() -> None:
    result, _ = _run({2.0: Decimal("10"), 4.0: Decimal("-4")})

    assert all(order.status is OrderStatus.FILLED for order in result.orders)
    assert len(result.state.oms.active_orders) == 0
    assert len(result.state.oms.completed_orders) == 2


def test_no_allocation_capital_is_left_held_at_the_end() -> None:
    result, _ = _run({2.0: Decimal("10"), 4.0: Decimal("-4")})

    assert result.state.allocation.notional_allocated == Decimal("0.00")
    assert dict(result.state.allocation.reservations) == {}


# ---------------------------------------------------------------------------
# Portfolio accounting, at non-round prices
# ---------------------------------------------------------------------------


def test_the_accounting_identity_holds_at_non_round_prices() -> None:
    result, _ = _run({2.0: Decimal("10"), 4.0: Decimal("-4"), 6.0: Decimal("2")})

    assert _identity_holds(result)


def test_the_accounting_identity_holds_with_fractional_quantities() -> None:
    result, _ = _run({2.0: Decimal("10.5"), 4.0: Decimal("-4.25"), 6.0: Decimal("1.75")})

    assert _identity_holds(result)
    assert result.fills[0].quantity == Decimal("10.5")


def test_the_accounting_identity_holds_with_commission_and_slippage() -> None:
    simulator = ExecutionSimulator(
        commission_model=PerShareCommission(Decimal("0.0035")),
        slippage_model=FixedSlippage(Decimal("0.0100")),
    )
    result, _ = _run({2.0: Decimal("10.5"), 4.0: Decimal("-4.25")}, simulator=simulator)

    assert result.valuation.commission_paid > Decimal("0.00")
    assert _identity_holds(result)


def test_closing_a_position_keeps_its_realized_pnl() -> None:
    """The v2.1 fix: realized P&L survives the position leaving ``positions``."""

    result, asset_id = _run({2.0: Decimal("10"), 4.0: Decimal("-10")})

    assert asset_id not in result.state.portfolio.positions
    assert result.valuation.realized_pnl == Decimal("189.98")  # (119.003-100.005)*10
    assert result.valuation.unrealized_pnl == Decimal("0.00")
    assert _identity_holds(result)


def test_positions_are_marked_to_market_by_later_records() -> None:
    result, asset_id = _run({2.0: Decimal("10")})

    position = result.state.portfolio.positions[asset_id]

    assert position.market_price == MIDS[-1]
    assert result.valuation.unrealized_pnl == (MIDS[-1] - MIDS[0]) * Decimal("10")


def test_cash_moves_only_by_trade_value_and_commission() -> None:
    result, _ = _run({2.0: Decimal("10")})

    spent = START_CASH - result.valuation.cash

    assert spent == Decimal("10") * MIDS[0]
    assert result.valuation.commission_paid == Decimal("0.00")


# ---------------------------------------------------------------------------
# Execution semantics
# ---------------------------------------------------------------------------


def test_a_liquidity_capped_policy_partially_fills_an_oversized_order() -> None:
    strategy_id, asset_id = str(uuid4()), str(uuid4())
    dataset = MarketDataset.of(
        "THIN", [sized_quote(asset_id, 2.0 + i, mid, Decimal("3")) for i, mid in enumerate(MIDS)]
    )

    result = BacktestEngine.run(
        backtest_config(strategy_id, fill_policy=LiquidityCappedFill()),
        dataset,
        running_strategy_state(
            strategy_id, ScriptedStrategy(strategy_id, asset_id, {2.0: Decimal("10")})
        ),
        context_factory,
    )

    assert result.fills[0].quantity == Decimal("3")
    assert result.orders[0].status is OrderStatus.PARTIALLY_FILLED
    assert result.state.portfolio.positions[asset_id].quantity == Decimal("3.000000")


def test_a_partially_filled_order_keeps_its_residual_reserved() -> None:
    """It is still working, so the capital behind it is still committed."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())
    dataset = MarketDataset.of(
        "THIN", [sized_quote(asset_id, 2.0 + i, mid, Decimal("3")) for i, mid in enumerate(MIDS)]
    )

    result = BacktestEngine.run(
        backtest_config(strategy_id, fill_policy=LiquidityCappedFill()),
        dataset,
        running_strategy_state(
            strategy_id, ScriptedStrategy(strategy_id, asset_id, {2.0: Decimal("10")})
        ),
        context_factory,
    )

    assert result.state.allocation.notional_allocated == Decimal("7") * MIDS[0]


def test_participation_capping_spreads_a_large_order_across_records() -> None:
    strategy_id, asset_id = str(uuid4()), str(uuid4())
    dataset = MarketDataset.of(
        "THIN", [sized_quote(asset_id, 2.0 + i, mid, Decimal("8")) for i, mid in enumerate(MIDS)]
    )
    plan = {2.0: Decimal("10"), 3.0: Decimal("10"), 4.0: Decimal("10")}

    result = BacktestEngine.run(
        backtest_config(
            strategy_id, fill_policy=LiquidityCappedFill(participation_rate=Decimal("0.5"))
        ),
        dataset,
        running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, plan)),
        context_factory,
    )

    assert [fill.quantity for fill in result.fills] == [Decimal("4.0")] * 3
    assert result.state.portfolio.positions[asset_id].quantity == Decimal("12.000000")


def test_an_event_showing_no_liquidity_produces_no_fill() -> None:
    """The policy's own NO_FILL must be terminal for the order, like any other."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())
    dataset = MarketDataset.of(
        "DRY", [sized_quote(asset_id, 2.0 + i, mid, Decimal("0")) for i, mid in enumerate(MIDS)]
    )

    result = BacktestEngine.run(
        backtest_config(strategy_id, fill_policy=LiquidityCappedFill()),
        dataset,
        running_strategy_state(
            strategy_id, ScriptedStrategy(strategy_id, asset_id, {2.0: Decimal("10")})
        ),
        context_factory,
    )

    assert result.fills == ()
    assert all(order.status is OrderStatus.CANCELLED for order in result.orders)
    assert len(result.state.oms.active_orders) == 0
    assert result.state.allocation.notional_allocated == Decimal("0.00")
    assert result.valuation.equity == START_CASH


def test_a_rejecting_venue_leaves_no_position_and_no_held_capital() -> None:
    result, asset_id = _run({2.0: Decimal("10")}, fill_policy=StaticFill(FillStatus.REJECTED))

    assert result.fills == ()
    assert asset_id not in result.state.portfolio.positions
    assert all(order.status is OrderStatus.REJECTED for order in result.orders)
    assert result.state.allocation.notional_allocated == Decimal("0.00")
    assert result.valuation.cash == START_CASH


def test_an_unfilled_venue_cancels_rather_than_leaving_orders_open() -> None:
    result, _ = _run({2.0: Decimal("10")}, fill_policy=StaticFill(FillStatus.NO_FILL))

    assert all(order.status is OrderStatus.CANCELLED for order in result.orders)
    assert len(result.state.oms.active_orders) == 0


def test_a_request_risk_refuses_never_reaches_the_oms() -> None:
    result, _ = _run(
        {2.0: Decimal("10")},
        risk_limits=permissive_risk_limits(max_order_quantity=Decimal("1")),
    )

    assert result.orders == ()
    assert result.fills == ()
    assert result.state.allocation.notional_allocated == Decimal("0.00")
    assert result.valuation.equity == START_CASH


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def test_analytics_are_compiled_from_the_run_that_produced_them() -> None:
    result, _ = _run({2.0: Decimal("10"), 4.0: Decimal("-4")})
    report = result.report

    assert report is not None
    assert report.ending_capital == result.equity_curve[-1].total_equity
    assert len(report.returns.daily_returns) == len(result.equity_curve) - 1
    assert report.trades.turnover > 0.0
    assert len(result.state.trade_records) == 2


def test_drawdown_is_measured_over_the_run_equity_curve() -> None:
    """The dip at record 2 (119.003 after 120.007) must show up as drawdown."""

    result, _ = _run({2.0: Decimal("10")})
    report = result.report

    assert report is not None
    assert len(report.drawdowns.drawdowns) == len(result.equity_curve)
    assert report.drawdowns.max_drawdown > 0.0
    assert report.drawdowns.ulcer_index > 0.0


def test_exposure_reflects_the_position_the_run_ended_holding() -> None:
    result, asset_id = _run({2.0: Decimal("10")})
    report = result.report
    position_value = result.state.portfolio.positions[asset_id].market_value

    assert report is not None
    assert report.exposure.long == position_value
    assert report.exposure.short == Decimal("0.00")
    assert report.exposure.gross == position_value
    assert report.exposure.net == position_value
    assert report.exposure.leverage > 0.0


def test_turnover_reflects_the_notional_the_run_traded() -> None:
    quiet, _ = _run({2.0: Decimal("1")})
    busy, _ = _run({2.0: Decimal("10"), 4.0: Decimal("-4"), 6.0: Decimal("3")})

    assert quiet.report is not None
    assert busy.report is not None
    assert busy.report.trades.turnover > quiet.report.trades.turnover


def test_attribution_sees_every_trade_the_run_made() -> None:
    result, asset_id = _run({2.0: Decimal("10"), 4.0: Decimal("-10")})
    report = result.report

    assert report is not None
    assert report.attribution.pnl_by_asset[asset_id] == result.valuation.realized_pnl


def test_the_equity_curve_and_the_final_valuation_agree() -> None:
    result, _ = _run({2.0: Decimal("10"), 4.0: Decimal("-4")})

    assert result.equity_curve[-1].total_equity == result.valuation.equity
    assert result.equity_curve[-1].cash == result.valuation.cash
