"""End-to-end mark-to-market and portfolio-accounting tests (v2.1).

Every test here drives the real ExecutionPipeline -- strategy, allocation, risk,
OMS, execution simulator and portfolio -- and asserts exact numbers, never just
"something happened". The recurring assertion is the portfolio accounting
identity:

    equity == starting_cash + realized_pnl + unrealized_pnl - commission_paid

which ties together the four quantities v2.1 keeps separate.
"""

from decimal import Decimal
from uuid import uuid4

from alphalab.execution.commission import FixedCommission, PerShareCommission
from alphalab.execution.fill import FillStatus
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.oms.status import OrderStatus
from alphalab.portfolio.nav import NAVCalculator
from alphalab.portfolio.valuation import PortfolioValuation
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineResult,
    ExecutionPipelineState,
)
from tests.integration.harness import (
    START_CASH,
    ScriptedStrategy,
    context_factory,
    permissive_risk_limits,
    pipeline_config,
    quote,
    running_strategy_state,
)


def _start(
    plan: dict[float, Decimal],
    *,
    asset_for: dict[float, str] | None = None,
    simulator: ExecutionSimulator | None = None,
    risk_limits: object | None = None,
) -> tuple[ExecutionPipelineState, str]:
    strategy_id = str(uuid4())
    asset_id = str(uuid4())
    strategy = ScriptedStrategy(strategy_id, asset_id, plan, asset_for)
    config = pipeline_config(
        strategy_id,
        simulator=simulator,
        risk_limits=risk_limits,  # type: ignore[arg-type]
    )
    state = ExecutionPipeline.initialize(config, running_strategy_state(strategy_id, strategy), 1.0)
    return state, asset_id


def _assert_accounting_identity(
    result: ExecutionPipelineResult, starting_cash: Decimal = START_CASH
) -> None:
    """equity == starting cash + realized + unrealized - commissions."""

    valuation = result.valuation
    assert valuation is not None
    expected = (
        starting_cash
        + valuation.realized_pnl
        + valuation.unrealized_pnl
        - valuation.commission_paid
    ).quantize(Decimal("0.01"))
    assert valuation.equity == expected
    # NAV and the analytics snapshot must agree with the same valuation.
    assert valuation.equity == NAVCalculator.calculate(
        result.state.portfolio.cash, result.state.portfolio.positions, "USD"
    )
    assert result.state.portfolio_snapshots[-1].total_equity == valuation.equity


# ---------------------------------------------------------------------------
# 1. open long -> mark-to-market -> close
# ---------------------------------------------------------------------------


def test_open_long_mark_to_market_then_close() -> None:
    state, asset = _start({2.0: Decimal("10"), 4.0: Decimal("-10")})

    entry = ExecutionPipeline.process_quote(
        state, quote(asset, 2.0, Decimal("100.00")), context_factory
    )
    position = entry.state.portfolio.positions[asset]
    assert position.quantity == Decimal("10.000000")
    assert position.average_cost == Decimal("100.0000")
    assert position.market_price == Decimal("100.0000")
    assert position.unrealized_pnl == Decimal("0.00")
    assert entry.state.portfolio.cash.balance("USD") == START_CASH - Decimal("1000.00")
    _assert_accounting_identity(entry)

    # Hold: no intent at 3.0, so the only thing that moves is the mark.
    hold = ExecutionPipeline.process_quote(
        entry.state, quote(asset, 3.0, Decimal("115.00")), context_factory
    )
    assert hold.fills == ()
    marked = hold.state.portfolio.positions[asset]
    assert marked.quantity == Decimal("10.000000")
    assert marked.average_cost == Decimal("100.0000")  # cost basis is not moved by a mark
    assert marked.market_price == Decimal("115.0000")
    assert marked.unrealized_pnl == Decimal("150.00")
    assert hold.state.portfolio.realized_pnl == Decimal("0.00")  # marking realizes nothing
    assert hold.state.portfolio.cash.balance("USD") == START_CASH - Decimal("1000.00")
    assert hold.valuation is not None
    assert hold.valuation.equity == START_CASH + Decimal("150.00")
    _assert_accounting_identity(hold)

    exit_result = ExecutionPipeline.process_quote(
        hold.state, quote(asset, 4.0, Decimal("120.00")), context_factory
    )
    assert asset not in exit_result.state.portfolio.positions
    assert exit_result.state.portfolio.realized_pnl == Decimal("200.00")
    assert exit_result.valuation is not None
    assert exit_result.valuation.unrealized_pnl == Decimal("0.00")
    assert exit_result.state.portfolio.cash.balance("USD") == START_CASH + Decimal("200.00")
    _assert_accounting_identity(exit_result)


# ---------------------------------------------------------------------------
# 2. open short -> mark-to-market -> close
# ---------------------------------------------------------------------------


def test_open_short_mark_to_market_then_close() -> None:
    state, asset = _start({2.0: Decimal("-10"), 4.0: Decimal("10")})

    entry = ExecutionPipeline.process_quote(
        state, quote(asset, 2.0, Decimal("100.00")), context_factory
    )
    position = entry.state.portfolio.positions[asset]
    assert position.quantity == Decimal("-10.000000")
    assert position.average_cost == Decimal("100.0000")
    assert position.market_value == Decimal("-1000.00")
    # A short sale credits cash; the liability shows up as negative market value.
    assert entry.state.portfolio.cash.balance("USD") == START_CASH + Decimal("1000.00")
    _assert_accounting_identity(entry)

    # Price falls: a short gains.
    hold = ExecutionPipeline.process_quote(
        entry.state, quote(asset, 3.0, Decimal("90.00")), context_factory
    )
    marked = hold.state.portfolio.positions[asset]
    assert marked.unrealized_pnl == Decimal("100.00")
    assert marked.market_value == Decimal("-900.00")
    assert hold.valuation is not None
    assert hold.valuation.short_value == Decimal("-900.00")
    assert hold.valuation.long_value == Decimal("0.00")
    assert hold.valuation.equity == START_CASH + Decimal("100.00")
    _assert_accounting_identity(hold)

    # Price rises above entry before the cover: the short is now losing.
    cover = ExecutionPipeline.process_quote(
        hold.state, quote(asset, 4.0, Decimal("105.00")), context_factory
    )
    assert asset not in cover.state.portfolio.positions
    assert cover.state.portfolio.realized_pnl == Decimal("-50.00")
    assert cover.state.portfolio.cash.balance("USD") == START_CASH - Decimal("50.00")
    _assert_accounting_identity(cover)


# ---------------------------------------------------------------------------
# 3. partial close
# ---------------------------------------------------------------------------


def test_partial_close_realizes_only_the_closed_portion() -> None:
    state, asset = _start({2.0: Decimal("10"), 3.0: Decimal("-4")})

    entry = ExecutionPipeline.process_quote(
        state, quote(asset, 2.0, Decimal("100.00")), context_factory
    )
    reduced = ExecutionPipeline.process_quote(
        entry.state, quote(asset, 3.0, Decimal("130.00")), context_factory
    )

    position = reduced.state.portfolio.positions[asset]
    assert position.quantity == Decimal("6.000000")
    assert position.average_cost == Decimal("100.0000")  # remaining lot keeps its basis
    assert position.market_price == Decimal("130.0000")

    # Realized on the 4 sold; unrealized still open on the 6 held.
    assert reduced.state.portfolio.realized_pnl == Decimal("120.00")
    assert position.unrealized_pnl == Decimal("180.00")
    assert reduced.state.portfolio.cash.balance("USD") == START_CASH - Decimal("1000.00") + Decimal(
        "520.00"
    )
    _assert_accounting_identity(reduced)


def test_partial_fill_then_completion_keeps_position_consistent_with_fills() -> None:
    """A partially filled order: position quantity tracks the fills, not the order."""

    state, asset = _start({2.0: Decimal("10")})

    partial = ExecutionPipeline.process_quote(
        state,
        quote(asset, 2.0, Decimal("100.00")),
        context_factory,
        FillStatus.PARTIAL_FILL,
        Decimal("4"),
    )

    order = partial.state.oms.orders.find(partial.oms_orders[0].order_id)
    # The remainder is withdrawn (ADR-0014); the fill it did produce is kept,
    # and the position below still tracks the fills rather than the order.
    assert order.status is OrderStatus.CANCELLED
    assert order.filled_quantity == Decimal("4")
    assert order.remaining_quantity == Decimal("6.000000")

    position = partial.state.portfolio.positions[asset]
    assert position.quantity == Decimal("4.000000")
    assert position.quantity == order.filled_quantity
    assert partial.state.portfolio.cash.balance("USD") == START_CASH - Decimal("400.00")
    _assert_accounting_identity(partial)


# ---------------------------------------------------------------------------
# 4. multiple fills
# ---------------------------------------------------------------------------


def test_multiple_fills_average_cost_and_single_application_per_fill() -> None:
    state, asset = _start({2.0: Decimal("10"), 3.0: Decimal("10"), 4.0: Decimal("-20")})

    first = ExecutionPipeline.process_quote(
        state, quote(asset, 2.0, Decimal("100.00")), context_factory
    )
    second = ExecutionPipeline.process_quote(
        first.state, quote(asset, 3.0, Decimal("120.00")), context_factory
    )

    position = second.state.portfolio.positions[asset]
    assert position.quantity == Decimal("20.000000")
    assert position.average_cost == Decimal("110.0000")  # (10*100 + 10*120) / 20
    assert position.unrealized_pnl == Decimal("200.00")

    # Two fills, two ledger trade transactions, two portfolio position events.
    assert len(second.state.fills) == 2
    assert len(second.state.trades) == 2
    assert len(second.state.portfolio.ledger.by_asset(asset)) == 2
    _assert_accounting_identity(second)

    closed = ExecutionPipeline.process_quote(
        second.state, quote(asset, 4.0, Decimal("130.00")), context_factory
    )
    # 20 bought at an average of 110, sold at 130.
    assert closed.state.portfolio.realized_pnl == Decimal("400.00")
    assert asset not in closed.state.portfolio.positions
    assert closed.state.portfolio.cash.balance("USD") == START_CASH + Decimal("400.00")
    _assert_accounting_identity(closed)


# ---------------------------------------------------------------------------
# 5. commission / cost handling
# ---------------------------------------------------------------------------


def test_commission_is_charged_once_per_fill_and_never_enters_cost_basis() -> None:
    simulator = ExecutionSimulator(commission_model=PerShareCommission(Decimal("0.10")))
    state, asset = _start({2.0: Decimal("10"), 3.0: Decimal("-10")}, simulator=simulator)

    entry = ExecutionPipeline.process_quote(
        state, quote(asset, 2.0, Decimal("100.00")), context_factory
    )
    assert entry.execution_reports[0].commission == Decimal("1.0000")
    assert entry.state.portfolio.commission_paid == Decimal("1.00")
    # Cost basis stays a clean price: the commission was expensed to cash.
    assert entry.state.portfolio.positions[asset].average_cost == Decimal("100.0000")
    assert entry.state.portfolio.cash.balance("USD") == START_CASH - Decimal("1001.00")
    _assert_accounting_identity(entry)

    exit_result = ExecutionPipeline.process_quote(
        entry.state, quote(asset, 3.0, Decimal("110.00")), context_factory
    )
    assert exit_result.state.portfolio.commission_paid == Decimal("2.00")
    assert exit_result.state.portfolio.realized_pnl == Decimal("100.00")
    assert exit_result.state.portfolio.cash.balance("USD") == START_CASH + Decimal("98.00")
    _assert_accounting_identity(exit_result)


def test_analytics_trade_records_attribute_realized_pnl_to_the_right_fill() -> None:
    """An opening fill realizes nothing, even right after a profitable close."""

    simulator = ExecutionSimulator(commission_model=FixedCommission(Decimal("0.00")))
    state, asset = _start(
        {2.0: Decimal("10"), 3.0: Decimal("-10"), 4.0: Decimal("10")}, simulator=simulator
    )

    r1 = ExecutionPipeline.process_quote(
        state, quote(asset, 2.0, Decimal("100.00")), context_factory
    )
    r2 = ExecutionPipeline.process_quote(
        r1.state, quote(asset, 3.0, Decimal("150.00")), context_factory
    )
    r3 = ExecutionPipeline.process_quote(
        r2.state, quote(asset, 4.0, Decimal("150.00")), context_factory
    )

    records = list(r3.state.trade_records)
    assert len(records) == 3
    assert records[0].realized_pnl == Decimal("0.00")  # open
    assert records[1].realized_pnl == Decimal("500.00")  # close
    assert records[2].realized_pnl == Decimal("0.00")  # re-open, realizes nothing
    assert r3.state.portfolio.realized_pnl == Decimal("500.00")


# ---------------------------------------------------------------------------
# 6. invalid / missing market price
# ---------------------------------------------------------------------------


def test_request_for_an_asset_with_no_market_price_never_reaches_the_oms() -> None:
    unpriced_asset = str(uuid4())
    state, asset = _start({2.0: Decimal("10")}, asset_for={2.0: unpriced_asset})

    result = ExecutionPipeline.process_quote(
        state, quote(asset, 2.0, Decimal("100.00")), context_factory
    )

    # Allocation still produced a request; the pipeline refuses to price it.
    assert len(result.order_requests) == 1
    assert result.order_requests[0].asset_id == unpriced_asset
    assert result.unpriced_requests == result.order_requests
    assert result.oms_orders == ()
    assert result.risk_decisions == ()
    assert result.execution_reports == ()
    assert result.fills == ()
    assert result.state.portfolio.positions == {}
    assert result.state.portfolio.cash.balance("USD") == START_CASH
    assert len(result.state.oms.events) == 0
    _assert_accounting_identity(result)


def test_a_later_quote_makes_the_previously_unpriced_asset_tradeable() -> None:
    """Missing price is a per-event condition, not a permanent rejection."""

    other = str(uuid4())
    state, asset = _start({2.0: Decimal("10"), 3.0: Decimal("10")}, asset_for={2.0: other})

    blocked = ExecutionPipeline.process_quote(
        state, quote(asset, 2.0, Decimal("100.00")), context_factory
    )
    assert blocked.unpriced_requests != ()

    traded = ExecutionPipeline.process_quote(
        blocked.state, quote(asset, 3.0, Decimal("100.00")), context_factory
    )
    assert traded.unpriced_requests == ()
    assert traded.state.portfolio.positions[asset].quantity == Decimal("10.000000")


def test_a_non_positive_mark_is_ignored_and_leaves_the_previous_mark_standing() -> None:
    state, asset = _start({2.0: Decimal("10")})

    entry = ExecutionPipeline.process_quote(
        state, quote(asset, 2.0, Decimal("100.00")), context_factory
    )
    zero_quote = ExecutionPipeline.process_quote(
        entry.state, quote(asset, 3.0, Decimal("0.00")), context_factory
    )

    position = zero_quote.state.portfolio.positions[asset]
    assert position.market_price == Decimal("100.0000")
    assert position.unrealized_pnl == Decimal("0.00")
    assert zero_quote.valuation is not None
    assert zero_quote.valuation.equity == START_CASH


# ---------------------------------------------------------------------------
# 7. risk -> OMS -> execution -> portfolio integration
# ---------------------------------------------------------------------------


def test_risk_rejection_stops_the_order_before_oms_execution_and_portfolio() -> None:
    state, asset = _start({2.0: Decimal("500")}, risk_limits=permissive_risk_limits(Decimal("10")))

    result = ExecutionPipeline.process_quote(
        state, quote(asset, 2.0, Decimal("100.00")), context_factory
    )

    assert result.risk_decisions[0].approved is False
    assert result.risk_decisions[0].violations[0].rule == "OrderSizeQuantity"
    assert result.oms_orders == ()
    assert result.execution_reports == ()
    assert result.state.portfolio.positions == {}
    assert result.state.portfolio.cash.balance("USD") == START_CASH
    _assert_accounting_identity(result)


def test_venue_rejection_closes_the_order_and_leaves_the_portfolio_untouched() -> None:
    state, asset = _start({2.0: Decimal("10")})

    result = ExecutionPipeline.process_quote(
        state, quote(asset, 2.0, Decimal("100.00")), context_factory, FillStatus.REJECTED
    )

    assert result.risk_decisions[0].approved is True
    order = result.state.oms.orders.find(result.oms_orders[0].order_id)
    assert order.status is OrderStatus.REJECTED
    assert order.order_id not in result.state.oms.active_orders
    assert result.fills == ()
    assert result.state.portfolio.positions == {}
    assert result.state.portfolio.cash.balance("USD") == START_CASH
    assert result.state.allocation.notional_allocated == Decimal("0.00")
    _assert_accounting_identity(result)


def test_approved_order_flows_through_every_stage_with_consistent_numbers() -> None:
    state, asset = _start({2.0: Decimal("25")})

    result = ExecutionPipeline.process_quote(
        state, quote(asset, 2.0, Decimal("40.00")), context_factory
    )

    request = result.order_requests[0]
    decision = result.risk_decisions[0]
    order = result.state.oms.orders.find(result.oms_orders[0].order_id)
    report = result.execution_reports[0]
    fill = result.fills[0]
    position = result.state.portfolio.positions[asset]

    # The same 25 shares at 40.00 all the way down the path.
    assert request.quantity == Decimal("25.000000")
    assert decision.order_id == request.order_id
    assert order.status is OrderStatus.FILLED
    assert order.filled_quantity == Decimal("25.000000")
    assert order.average_fill_price == Decimal("40.00")
    assert report.fill_quantity == Decimal("25.000000")
    assert fill.quantity == report.fill_quantity
    assert position.quantity == Decimal("25.000000")
    assert result.state.portfolio.cash.balance("USD") == START_CASH - Decimal("1000.00")

    # Risk is resynced from the marked portfolio after the fill.
    assert result.state.risk.current_nav == START_CASH
    assert result.state.risk.exposure.gross_exposure == Decimal("1000.00")
    assert result.state.risk.exposure.long_exposure == Decimal("1000.00")
    _assert_accounting_identity(result)


def test_every_market_event_records_exactly_one_portfolio_snapshot() -> None:
    state, asset = _start({2.0: Decimal("10")})

    assert len(state.portfolio_snapshots) == 1  # the initial funded snapshot

    result = state
    for i, mid in enumerate((Decimal("100.00"), Decimal("110.00"), Decimal("120.00"))):
        result = ExecutionPipeline.process_quote(
            result, quote(asset, 2.0 + i, mid), context_factory
        ).state

    assert len(result.portfolio_snapshots) == 4
    equities = [s.total_equity for s in result.portfolio_snapshots]
    assert equities == [
        START_CASH,
        START_CASH,
        START_CASH + Decimal("100.00"),
        START_CASH + Decimal("200.00"),
    ]

    compiled = ExecutionPipeline.compile_analytics(result, 6.0)
    assert compiled.analytics.reports[-1].ending_capital == START_CASH + Decimal("200.00")


def test_valuation_snapshot_matches_the_portfolio_state_it_was_taken_from() -> None:
    state, asset = _start({2.0: Decimal("10")})
    result = ExecutionPipeline.process_quote(
        state, quote(asset, 2.0, Decimal("100.00")), context_factory
    )

    recomputed = PortfolioValuation.snapshot(result.state.portfolio, 2.0, "USD")
    assert result.valuation == recomputed
