"""The canonical path books each instrument by what it is (ACC-005, ACC-006, ACC-007).

Until v3.11 every fill was booked as a fully paid cash equity: a futures
contract on fifty units of an index was worth its quoted price and cost that
price in cash, an option's premium ignored its multiplier, nothing could be
priced at or below zero, a maker rebate could not be represented, and neither a
split nor a dividend could be booked. Each is pinned here through the execution
pipeline itself -- allocation, risk, the OMS, the simulator and the portfolio --
not through the portfolio alone.
"""

from __future__ import annotations

import decimal
import hashlib
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.allocation import AllocationEngine
from alphalab.common.ids import id_scope
from alphalab.common.order_terms import OrderTerms
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.conventions.lot import LotSpecification
from alphalab.core.enums import AssetType, OrderStatus
from alphalab.execution.commission import PercentageCommission
from alphalab.execution.costs import FREE
from alphalab.execution.policy import ImmediateFill
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.instrument.record import InstrumentRecord
from alphalab.market.record import MarketRecord
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.corporate_actions import CashFlow, CashFlowKind, Split
from alphalab.portfolio.events import CashFlowBooked, PositionSplit, VariationSettled
from alphalab.portfolio.nav import NAVCalculator
from alphalab.portfolio.types import TransactionType
from alphalab.portfolio.valuation import PortfolioValuation
from alphalab.runtime.assumptions import execution_assumptions
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineResult,
    ExecutionPipelineState,
    ExecutionRouting,
)
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine, RunState
from alphalab.runtime.run_snapshot import RunObjects, capture, from_primitives, restore
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.events import Intent, IntentKind
from tests.integration.harness import (
    START_CASH,
    ScriptedStrategy,
    context_factory,
    permissive_risk_limits,
    pipeline_config,
    quote,
    registry_of,
    running_strategy_state,
)

STRATEGY = "ECON"
ZERO = Decimal("0")


def _economics(
    multiplier: str,
    settlement: SettlementModel,
    *,
    lot: str | None = None,
    negative: bool = False,
) -> InstrumentEconomics:
    return InstrumentEconomics(
        Decimal(multiplier),
        settlement,
        lot=None if lot is None else LotSpecification(Decimal(lot), Decimal(lot)),
        allows_negative_prices=negative,
    )


ES = InstrumentRecord(
    "ESZ6",
    AssetType.FUTURE,
    "XCME",
    "USD",
    economics=_economics("50", SettlementModel.FUTURES_VARIATION, lot="1"),
)
SPX_CALL = InstrumentRecord(
    "SPXC5000",
    AssetType.OPTION,
    "XCBO",
    "USD",
    economics=_economics("100", SettlementModel.OPTION_PREMIUM),
)
BTC_PERP = InstrumentRecord(
    "BTCPERP", AssetType.CRYPTO, "XBIN", "USD", economics=_economics("1", SettlementModel.PERPETUAL)
)
WTI = InstrumentRecord(
    "CLK0",
    AssetType.FUTURE,
    "XNYM",
    "USD",
    economics=_economics("1000", SettlementModel.FUTURES_VARIATION, negative=True),
)
UNDECLARED = InstrumentRecord("NQZ6", AssetType.FUTURE, "XCME", "USD")
SHARE = InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD")


def _pipeline(
    record: InstrumentRecord,
    plan: dict[float, Decimal],
    *,
    simulator: ExecutionSimulator | None = None,
    routing: ExecutionRouting = ExecutionRouting.SIMULATED,
    risk_limits: Any = None,
    records: tuple[InstrumentRecord, ...] = (),
) -> ExecutionPipelineState:
    strategy = ScriptedStrategy(STRATEGY, record.asset_id, plan)
    config = replace(
        pipeline_config(STRATEGY, simulator=simulator, risk_limits=risk_limits),
        instruments=registry_of(record, *records),
        routing=routing,
    )
    return ExecutionPipeline.initialize(config, running_strategy_state(STRATEGY, strategy), 1.0)


def _step(
    state: ExecutionPipelineState, record: InstrumentRecord, at: float, mid: str
) -> ExecutionPipelineResult:
    return ExecutionPipeline.process_quote(
        state, quote(record.asset_id, at, Decimal(mid)), context_factory
    )


def _play(
    state: ExecutionPipelineState, record: InstrumentRecord, marks: dict[float, str]
) -> list[ExecutionPipelineResult]:
    results = []
    for at, mid in marks.items():
        result = _step(state, record, at, mid)
        results.append(result)
        state = result.state
    return results


def _identity(result: ExecutionPipelineResult) -> None:
    """equity == deposits + realized + unrealized - commission, and NAV agrees."""

    valuation = result.valuation
    assert valuation is not None
    assert valuation.equity == (
        START_CASH + valuation.realized_pnl + valuation.unrealized_pnl - valuation.commission_paid
    )
    portfolio = result.state.portfolio
    assert valuation.equity == NAVCalculator.calculate(portfolio.cash, portfolio.positions, "USD")


def _cash(state: ExecutionPipelineState) -> Decimal:
    return state.portfolio.cash.balance("USD")


# --------------------------------------------------------------------------- #
# ACC-005: a future moves no notional and settles its gains as cash
# --------------------------------------------------------------------------- #


def test_a_future_pays_no_notional_and_settles_every_mark_as_variation_margin() -> None:
    state = _pipeline(ES, {2.0: Decimal("2"), 4.0: Decimal("-2")})

    bought, marked, sold = _play(state, ES, {2.0: "5000", 3.0: "5010", 4.0: "5005"})

    # Two contracts bought: no cash moves for the notional.
    assert _cash(bought.state) == START_CASH
    position = bought.state.portfolio.positions[ES.asset_id]
    assert position.quantity == Decimal("2")
    # Its value is its notional -- 2 x 5000 x 50 -- which risk reads ...
    assert position.market_value == Decimal("500000.00")
    assert bought.state.risk.exposure.gross_exposure == Decimal("500000.00")
    # ... and it adds nothing to equity beyond the cash its gains have paid.
    assert position.carrying_value == ZERO
    _identity(bought)

    # Marked 10 points higher: 2 x 10 x 50 = 1,000 arrives as cash, realized.
    assert _cash(marked.state) == START_CASH + Decimal("1000")
    assert marked.state.portfolio.realized_pnl.of("USD") == Decimal("1000.00")
    settled = [e for e in marked.state.portfolio.events if isinstance(e, VariationSettled)]
    assert [(e.price, e.amount) for e in settled] == [(Decimal("5010"), Decimal("1000.00"))]
    assert marked.valuation is not None and marked.valuation.equity == START_CASH + 1000
    _identity(marked)

    # Marked 5 lower, then closed there: 500 paid back, nothing more at the fill.
    assert _cash(sold.state) == START_CASH + Decimal("500")
    assert ES.asset_id not in sold.state.portfolio.positions
    assert sold.state.portfolio.realized_pnl.of("USD") == Decimal("500.00")
    _identity(sold)


def test_an_option_pays_its_premium_times_its_multiplier() -> None:
    state = _pipeline(SPX_CALL, {2.0: Decimal("3"), 4.0: Decimal("-3")})

    bought, marked, sold = _play(state, SPX_CALL, {2.0: "2.50", 3.0: "3.00", 4.0: "3.10"})

    assert _cash(bought.state) == START_CASH - Decimal("750.00")  # 3 x 2.50 x 100
    assert bought.state.portfolio.positions[SPX_CALL.asset_id].market_value == Decimal("750.00")
    _identity(bought)
    assert marked.valuation is not None
    assert marked.valuation.unrealized_pnl == Decimal("150.00")
    _identity(marked)
    assert _cash(sold.state) == START_CASH + Decimal("180.00")
    assert sold.state.portfolio.realized_pnl.of("USD") == Decimal("180.00")
    _identity(sold)


def test_a_perpetual_settles_like_a_future_and_pays_funding_as_a_cash_flow() -> None:
    run = _run(BTC_PERP, {2.0: Decimal("2")}, {2.0: "60000", 3.0: "60500"})
    run = RunEngine.apply_cash_flow(
        run,
        CashFlow(CashFlowKind.FUNDING, Decimal("-12.345"), "USD", BTC_PERP.asset_id, "8h"),
        3.5,
    )

    portfolio = run.pipeline.portfolio
    # +1,000 of variation, then 12.34 of funding paid (rounded half-even).
    assert portfolio.cash.balance("USD") == START_CASH + Decimal("1000") - Decimal("12.34")
    assert portfolio.realized_pnl.of("USD") == Decimal("987.66")
    (booked,) = [e for e in portfolio.events if isinstance(e, CashFlowBooked)]
    assert (booked.kind, booked.amount, booked.reference) == ("funding", Decimal("-12.34"), "8h")
    assert portfolio.ledger.transactions[-1].type is TransactionType.FUNDING
    # The equity curve shows the payment at the instant it was made.
    assert run.pipeline.portfolio_snapshots[-1].timestamp == 3.5


def test_a_future_that_declares_no_economics_is_refused_before_an_order_exists() -> None:
    state = _pipeline(UNDECLARED, {2.0: Decimal("1")})

    result = _step(state, UNDECLARED, 2.0, "18000")

    assert result.oms_orders == ()
    (refusal,) = result.settlement_refusals
    assert "declares no economics" in refusal.detail
    assert UNDECLARED.asset_id not in result.state.portfolio.positions
    assert result.state.allocation.reservations == {}


def test_a_share_undeclared_is_a_fully_paid_unit_of_one_as_before() -> None:
    state = _pipeline(SHARE, {2.0: Decimal("10")})

    result = _step(state, SHARE, 2.0, "100")

    assert _cash(result.state) == START_CASH - Decimal("1000.00")
    assert result.state.portfolio.positions[SHARE.asset_id].economics is None


# --------------------------------------------------------------------------- #
# Allocation and risk value a contract by what one is worth
# --------------------------------------------------------------------------- #


def test_a_weight_target_is_sized_in_contracts_by_their_value() -> None:
    class _Weight(ScriptedStrategy):
        def on_quote(self, context: Any, event: Any) -> Any:
            return (Intent(STRATEGY, ES.asset_id, Decimal("0.5"), kind=IntentKind.TARGET_WEIGHT),)

    config = replace(pipeline_config(STRATEGY), instruments=registry_of(ES))
    state = ExecutionPipeline.initialize(
        config, running_strategy_state(STRATEGY, _Weight(STRATEGY, ES.asset_id, {})), 1.0
    )

    result = _step(state, ES, 2.0, "5000")

    # Half of 1,000,000 is 500,000; one contract is worth 5000 x 50 = 250,000.
    ((request_quantity, reserved),) = [
        (request.quantity, request.price) for request in result.order_requests
    ]
    assert (request_quantity, reserved) == (Decimal("2"), Decimal("5000"))
    assert result.state.portfolio.positions[ES.asset_id].quantity == Decimal("2")


def test_a_notional_limit_reads_a_contract_as_its_multiplied_value() -> None:
    limits = replace(
        permissive_risk_limits(),
        order_size=replace(permissive_risk_limits().order_size, max_notional=Decimal("200000")),
    )
    state = _pipeline(ES, {2.0: Decimal("1")}, risk_limits=limits)

    result = _step(state, ES, 2.0, "5000")

    # One contract is 250,000 of notional, over the 200,000 limit -- though its
    # quoted price, 5,000, is far under it.
    (decision,) = result.risk_decisions
    assert decision.approved is False
    assert result.oms_orders == ()


# --------------------------------------------------------------------------- #
# ACC-007: negative prices, where declared, and rebates
# --------------------------------------------------------------------------- #


def test_a_contract_declared_to_go_negative_is_marked_and_traded_below_zero() -> None:
    state = _pipeline(WTI, {2.0: Decimal("1"), 4.0: Decimal("-1")})

    _bought, below, closed = _play(state, WTI, {2.0: "10.00", 3.0: "-37.63", 4.0: "-5.00"})

    # 1 x (-37.63 - 10) x 1000: paid, and owed whether or not cash covers it.
    assert _cash(below.state) == START_CASH - Decimal("47630.00")
    _identity(below)
    # Closed at -5.00: 32,630 back.
    assert _cash(closed.state) == START_CASH - Decimal("15000.00")
    assert closed.state.portfolio.realized_pnl.of("USD") == Decimal("-15000.00")
    assert WTI.asset_id not in closed.state.portfolio.positions
    (fill,) = closed.fills
    assert fill.price == Decimal("-5.00")
    _identity(closed)


def test_a_non_positive_price_for_anything_not_declared_so_is_skipped_and_recorded() -> None:
    run = _run(ES, {}, {2.0: "5000"})

    run, result = RunEngine.advance(
        run, MarketRecord("DS", 3.0, quote(ES.asset_id, 3.0, Decimal("-1"))), context_factory
    )

    assert result is None
    (skipped,) = run.skipped
    assert "must be positive" in skipped.reason


def test_a_maker_rebate_is_cash_in() -> None:
    simulator = ExecutionSimulator(
        cost_model=FREE, maker_commission_model=PercentageCommission(Decimal("-0.0002"))
    )

    class _Limit(ScriptedStrategy):
        def on_quote(self, context: Any, event: Any) -> Any:
            if event.quote.timestamp != 2.0:
                return ()
            return (
                Intent(
                    STRATEGY, SHARE.asset_id, Decimal("100"), terms=OrderTerms.limit(Decimal("99"))
                ),
            )

    config = replace(pipeline_config(STRATEGY, simulator=simulator), instruments=registry_of(SHARE))
    state = ExecutionPipeline.initialize(
        config, running_strategy_state(STRATEGY, _Limit(STRATEGY, SHARE.asset_id, {})), 1.0
    )

    rested = _step(state, SHARE, 2.0, "100").state
    filled = _step(rested, SHARE, 3.0, "98.5")

    (report,) = filled.execution_reports
    # 100 x 99 x 0.0002 = 1.98, paid to the maker.
    assert (report.liquidity_flag, report.commission) == ("MAKER", Decimal("-1.98"))
    assert _cash(filled.state) == START_CASH - Decimal("9900.00") + Decimal("1.98")
    assert filled.state.portfolio.commission_paid.of("USD") == Decimal("-1.98")
    _identity(filled)
    # The rebate is part of the model the run's evidence is measured under.
    assumptions = execution_assumptions(config, ImmediateFill())
    assert "maker_commission=PercentageCommission" in assumptions.costs


# --------------------------------------------------------------------------- #
# ACC-006: splits and cash flows
# --------------------------------------------------------------------------- #


def _start_run(
    record: InstrumentRecord,
    plan: dict[float, Decimal],
    *,
    routing: ExecutionRouting = ExecutionRouting.SIMULATED,
) -> RunState:
    pipeline = _pipeline(record, plan, routing=routing)
    return RunEngine.initialize(
        RunConfig(
            pipeline=pipeline.config, mode=ExecutionMode.BACKTEST, start_timestamp=1.0, seed=7
        ),
        pipeline.strategy,
    )


def _records(record: InstrumentRecord, marks: dict[float, str]) -> list[MarketRecord]:
    """The market data, built once: the harness's quote arithmetic is the test's, not the run's."""

    return [
        MarketRecord("DS", at, quote(record.asset_id, at, Decimal(mid)))
        for at, mid in marks.items()
    ]


def _advance(run: RunState, record: InstrumentRecord, marks: dict[float, str]) -> RunState:
    return _play_records(run, _records(record, marks))


def _play_records(run: RunState, records: list[MarketRecord]) -> RunState:
    for market_record in records:
        run, _ = RunEngine.advance(run, market_record, context_factory)
    return run


def _run(
    record: InstrumentRecord,
    plan: dict[float, Decimal],
    marks: dict[float, str],
    *,
    routing: ExecutionRouting = ExecutionRouting.SIMULATED,
) -> RunState:
    return _advance(_start_run(record, plan, routing=routing), record, marks)


def test_a_split_changes_the_count_and_not_the_value() -> None:
    run = _run(SHARE, {2.0: Decimal("100")}, {2.0: "50"})
    before = PortfolioValuation.snapshot(run.pipeline.portfolio, 2.0)

    run = RunEngine.apply_split(run, Split(SHARE.asset_id, Decimal("2")), 2.5)

    position = run.pipeline.portfolio.positions[SHARE.asset_id]
    assert (position.quantity, position.average_cost, position.market_price) == (
        Decimal("200"),
        Decimal("25"),
        Decimal("25"),
    )
    assert position.basis == Decimal("5000.00")
    after = PortfolioValuation.snapshot(run.pipeline.portfolio, 2.5)
    assert (after.equity, after.unrealized_pnl) == (before.equity, before.unrealized_pnl)
    assert run.pipeline.market_prices[SHARE.asset_id] == Decimal("25")
    (event,) = [e for e in run.pipeline.portfolio.events if isinstance(e, PositionSplit)]
    assert (event.ratio, event.quantity) == (Decimal("2"), Decimal("200"))
    # Each strategy's own position is restated too, so a target still holds.
    assert AllocationEngine.strategy_position(
        run.pipeline.allocation, STRATEGY, SHARE.asset_id
    ) == Decimal("200")


def test_a_split_cancels_orders_working_in_the_asset_and_frees_what_they_held() -> None:
    class _Resting(ScriptedStrategy):
        def on_quote(self, context: Any, event: Any) -> Any:
            if event.quote.timestamp != 2.0:
                return ()
            return (
                Intent(
                    STRATEGY, SHARE.asset_id, Decimal("10"), terms=OrderTerms.limit(Decimal("40"))
                ),
            )

    config = replace(pipeline_config(STRATEGY), instruments=registry_of(SHARE))
    state = ExecutionPipeline.initialize(
        config, running_strategy_state(STRATEGY, _Resting(STRATEGY, SHARE.asset_id, {})), 1.0
    )
    rested = _step(state, SHARE, 2.0, "50").state
    assert rested.allocation.reservations

    split = ExecutionPipeline.apply_split(rested, Split(SHARE.asset_id, Decimal("2")), 2.5)

    (order,) = split.oms.orders.orders()
    assert order.status is OrderStatus.CANCELLED
    assert split.allocation.reservations == {}
    assert split.allocation.notional_allocated == ZERO


def test_a_split_is_refused_while_the_venue_holds_orders_in_the_asset() -> None:
    state = _pipeline(SHARE, {2.0: Decimal("10")}, routing=ExecutionRouting.EXTERNAL)
    working = _step(state, SHARE, 2.0, "50").state
    assert working.oms.working_orders_for(SHARE.asset_id)

    with pytest.raises(RuntimeValidationError, match="venue decides"):
        ExecutionPipeline.apply_split(working, Split(SHARE.asset_id, Decimal("2")), 2.5)


def test_dividends_and_fees_are_cash_and_realized_and_keep_the_identity() -> None:
    run = _run(SHARE, {2.0: Decimal("100")}, {2.0: "50"})
    run = RunEngine.apply_cash_flow(
        run, CashFlow(CashFlowKind.DIVIDEND, Decimal("42.00"), "USD", SHARE.asset_id, "Q3"), 2.5
    )
    run = RunEngine.apply_cash_flow(run, CashFlow(CashFlowKind.FEE, Decimal("-3.50"), "USD"), 2.6)

    portfolio = run.pipeline.portfolio
    assert portfolio.cash.balance("USD") == START_CASH - Decimal("5000.00") + Decimal("38.50")
    assert portfolio.realized_pnl.of("USD") == Decimal("38.50")
    assert [tx.type for tx in portfolio.ledger.transactions][-2:] == [
        TransactionType.DIVIDEND,
        TransactionType.FEE,
    ]
    valuation = PortfolioValuation.snapshot(portfolio, 2.6)
    assert valuation.equity == START_CASH + valuation.realized_pnl + valuation.unrealized_pnl


def test_a_cash_flow_is_refused_in_the_past_or_in_a_currency_not_settled() -> None:
    run = _run(SHARE, {}, {2.0: "50"})

    with pytest.raises(RuntimeValidationError, match="before the last event"):
        RunEngine.apply_cash_flow(run, CashFlow(CashFlowKind.INTEREST, Decimal("1"), "USD"), 1.5)
    with pytest.raises(RuntimeValidationError, match="settles in"):
        RunEngine.apply_cash_flow(run, CashFlow(CashFlowKind.INTEREST, Decimal("1"), "JPY"), 2.5)


# --------------------------------------------------------------------------- #
# Persistence and determinism
# --------------------------------------------------------------------------- #


def _objects(run: RunState) -> RunObjects:
    return RunObjects(
        pipeline=RuntimeObjects(
            sizing_model=run.pipeline.config.sizing_model,
            simulator=run.pipeline.config.simulator,
            strategies={STRATEGY: run.pipeline.strategy.strategies[STRATEGY].instance},
            instruments=run.pipeline.config.instruments,
        ),
        fill_policy=ImmediateFill(),
    )


def test_a_book_of_futures_splits_and_cash_flows_survives_a_restart_and_goes_on() -> None:
    with id_scope(7):
        run = _run(ES, {2.0: Decimal("2")}, {2.0: "5000", 3.0: "5010"})
        run = RunEngine.apply_cash_flow(run, CashFlow(CashFlowKind.FEE, Decimal("-1"), "USD"), 3.5)

    restored = restore(from_primitives(deserialize(serialize(capture(run)))), _objects(run))

    assert restored == run
    assert restored.pipeline.portfolio.positions[ES.asset_id].economics == ES.economics
    (record,) = _records(ES, {4.0: "5020"})
    with RunEngine.resume(restored):
        continued, _ = RunEngine.advance(restored, record, context_factory)
    with RunEngine.resume(run):
        uninterrupted, _ = RunEngine.advance(run, record, context_factory)
    assert serialize(capture(continued)) == serialize(capture(uninterrupted))


def _digest(run: RunState) -> str:
    return hashlib.sha256(serialize(capture(run)).encode()).hexdigest()


def test_the_book_does_not_depend_on_the_callers_decimal_context() -> None:
    """Settlements, splits and cash flows are the run's arithmetic, pinned like the rest.

    The inputs -- the configuration, whose budget the harness computes, and the
    quotes, whose sides it computes -- are built in the default context: they
    are the test's arithmetic, not the run's.
    """

    first = _records(WTI, {2.0: "10.00", 3.0: "-37.63", 4.0: "12.3456789"})
    later = _records(WTI, {5.0: "13.987654321"})
    flow = CashFlow(CashFlowKind.FUNDING, Decimal("-1234.56789"), "USD", WTI.asset_id)
    split = Split(WTI.asset_id, Decimal("3"))

    def play(start: RunState) -> str:
        run = _play_records(start, first)
        run = RunEngine.apply_cash_flow(run, flow, 4.5)
        run = RunEngine.apply_split(run, split, 4.6)
        return _digest(_play_records(run, later))

    with id_scope(11):
        reference = play(_start_run(WTI, {2.0: Decimal("3")}))
    with id_scope(11):
        start = _start_run(WTI, {2.0: Decimal("3")})
        with decimal.localcontext() as hostile:
            hostile.prec = 4
            hostile.rounding = decimal.ROUND_UP
            assert play(start) == reference
