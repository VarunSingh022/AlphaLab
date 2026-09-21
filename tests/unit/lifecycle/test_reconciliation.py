"""AlphaLab's execution state against a broker's, one mismatch class at a time.

Every fixture here is built from the real types -- a real
``ExecutionPipelineState`` produced by ``ExecutionPipeline.initialize``, a real
``BrokerState``, a real ``ExternalOrderMap``. Nothing is a stand-in, because the
thing under test is whether two real states can be told apart correctly.
"""

import uuid
from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.broker.account import BrokerAccount
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.order import BrokerOrder, BrokerOrderStatus
from alphalab.broker.position import BrokerPosition
from alphalab.broker.reconciliation import ExternalOrderMap
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.common.persistent_map import PersistentMap
from alphalab.core.enums import OrderStatus, OrderType, Side
from alphalab.execution.fill import FillStatus
from alphalab.execution.report import ExecutionReport
from alphalab.execution.state import ExecutionState
from alphalab.lifecycle import (
    BROKER_STATUS_EQUIVALENTS,
    LifecycleInputError,
    MismatchCategory,
    ReconciliationTolerances,
    StateReconciliation,
    SymbolMapping,
    Tolerance,
    reconcile_execution_state,
)
from alphalab.oms.book import OrderBook
from alphalab.oms.ids import OrderId
from alphalab.oms.order import Order as OMSOrder
from alphalab.oms.state import OMSState
from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.position import Position
from alphalab.risk.limits import (
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineConfig,
    ExecutionPipelineState,
)
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState

HUGE = Decimal("100000000")
LIMITS = RiskLimits(
    order_size=OrderSizeLimit(HUGE, HUGE),
    position=PositionLimit(HUGE, HUGE),
    exposure=ExposureLimit(HUGE, HUGE),
    leverage=LeverageLimit(Decimal("1000")),
    margin=MarginLimit(Decimal("1.00")),
    daily_loss=DailyLossLimit(HUGE),
    drawdown=DrawdownLimit(Decimal("1.00")),
)

EXACT = Tolerance(absolute=Decimal("0"))
TOLERANCES = ReconciliationTolerances(
    order_quantity=EXACT,
    order_price=Tolerance(absolute=Decimal("0.01")),
    fill_quantity=EXACT,
    fill_price=Tolerance(absolute=Decimal("0.01")),
    commission=Tolerance(absolute=Decimal("0.01")),
    position_quantity=EXACT,
    cash=Tolerance(absolute=Decimal("0.01")),
)

OMS_ID = "11111111-1111-1111-1111-111111111111"
BROKER_ID = f"ALB-{OMS_ID}"
ASSET = "asset-a"


def base_pipeline(starting_cash: Decimal = Decimal("100000")) -> ExecutionPipelineState:
    config = ExecutionPipelineConfig(
        account=Account("acct-1", "USD", "Reconciliation Account", 1.0),
        starting_cash=starting_cash,
        budget=CapitalBudget(
            global_capital=starting_cash,
            maximum_exposure=starting_cash * Decimal("10"),
            cash_buffer=Decimal("0"),
            strategy_budgets={"s-1": starting_cash},
        ),
        allocation_constraints=AllocationConstraints(
            allow_shorting=True, enforce_integer_quantities=False
        ),
        risk_limits=LIMITS,
    )
    return ExecutionPipeline.initialize(config, StrategyRuntimeState(), 1.0)


def oms_order(
    quantity: str = "10",
    status: OrderStatus = OrderStatus.ACCEPTED,
    limit_price: str | None = "100.00",
    asset_id: str = ASSET,
    order_id: str = OMS_ID,
) -> OMSOrder:
    return OMSOrder(
        order_id=OrderId(uuid.UUID(order_id)),
        strategy_id="s-1",
        asset_id=asset_id,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        status=status,
        quantity=Decimal(quantity),
        filled_quantity=Decimal("0"),
        remaining_quantity=Decimal(quantity),
        limit_price=None if limit_price is None else Decimal(limit_price),
        stop_price=None,
        average_fill_price=Decimal("0"),
        created_at=1.0,
        updated_at=1.0,
    )


def broker_order(
    quantity: str = "10",
    price: str = "100.00",
    status: OrderStatus | BrokerOrderStatus = OrderStatus.ACCEPTED,
    symbol: str = ASSET,
    broker_order_id: str = BROKER_ID,
    oms_order_id: str = OMS_ID,
) -> BrokerOrder:
    return BrokerOrder(
        broker_order_id=broker_order_id,
        oms_order_id=oms_order_id,
        symbol=symbol,
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal(quantity),
        price=Decimal(price),
        filled_quantity=Decimal("0"),
        average_fill_price=Decimal("0"),
        status=status,
        created_at=1.0,
        updated_at=1.0,
    )


def pipeline_with(
    orders: tuple[OMSOrder, ...] = (),
    reports: tuple[ExecutionReport, ...] = (),
    positions: dict[str, Position] | None = None,
    cash: Decimal = Decimal("100000"),
) -> ExecutionPipelineState:
    state = base_pipeline(cash)
    book = OrderBook()
    for order in orders:
        book = book.add(order)
    portfolio: PortfolioState = state.portfolio
    if positions is not None:
        portfolio = replace(portfolio, positions=positions)
    return replace(
        state,
        oms=OMSState(orders=book),
        execution=ExecutionState(
            reports=PersistentMap({report.execution_id: report for report in reports})
        ),
        portfolio=portfolio,
    )


def broker_with(
    orders: tuple[BrokerOrder, ...] = (),
    executions: tuple[BrokerExecution, ...] = (),
    positions: tuple[BrokerPosition, ...] = (),
    cash: Decimal = Decimal("100000"),
    currency: str = "USD",
) -> BrokerState:
    return BrokerState(
        broker_name="fixture",
        connection_status=ConnectionStatus.CONNECTED,
        account=BrokerAccount(
            account_id="acct-1",
            cash=cash,
            equity=cash,
            buying_power=cash,
            margin=Decimal("0"),
            available_funds=cash,
            currency=currency,
        ),
        positions=PersistentMap({position.symbol: position for position in positions}),
        orders=PersistentMap({order.broker_order_id: order for order in orders}),
        executions=PersistentMap({execution.execution_id: execution for execution in executions}),
    )


def report(
    execution_id: str = "x-1",
    quantity: str = "10",
    price: str = "100.00",
    commission: str = "1.00",
    order_id: str = OMS_ID,
) -> ExecutionReport:
    return ExecutionReport(
        execution_id=execution_id,
        order_id=order_id,
        asset_id=ASSET,
        strategy_id="s-1",
        timestamp=2.0,
        fill_price=Decimal(price),
        fill_quantity=Decimal(quantity),
        commission=Decimal(commission),
        slippage=Decimal("0"),
        liquidity_flag="",
        venue="LIVE",
        currency="USD",
        status=FillStatus.FULL_FILL,
    )


def execution(
    execution_id: str = "x-1",
    quantity: str = "10",
    price: str = "100.00",
    commission: str = "1.00",
    broker_order_id: str = BROKER_ID,
) -> BrokerExecution:
    return BrokerExecution(
        execution_id=execution_id,
        broker_order_id=broker_order_id,
        symbol=ASSET,
        fill_quantity=Decimal(quantity),
        fill_price=Decimal(price),
        commission=Decimal(commission),
        timestamp=2.0,
    )


BOUND = ExternalOrderMap().bind(OMS_ID, BROKER_ID)
IDENTITY = SymbolMapping.identity()


def reconcile(
    pipeline: ExecutionPipelineState,
    broker: BrokerState,
    mapping: ExternalOrderMap = BOUND,
) -> StateReconciliation:
    return reconcile_execution_state(pipeline, broker, mapping, IDENTITY, TOLERANCES)


class TestAgreement:
    def test_two_states_that_agree_reconcile_cleanly(self) -> None:
        result = reconcile(
            pipeline_with(orders=(oms_order(),), reports=(report(),)),
            broker_with(orders=(broker_order(),), executions=(execution(),)),
        )
        assert result.mismatches == ()
        assert result.unreconciled == ()
        assert result.reconciled
        assert result.fully_reconciled
        assert result.compared_orders == 1
        assert result.compared_fills == 1

    def test_an_unbound_working_order_is_not_expected_at_the_broker(self) -> None:
        """It was never routed, so its absence there is not a break."""

        unbound = oms_order(order_id="22222222-2222-2222-2222-222222222222")
        result = reconcile(
            pipeline_with(orders=(oms_order(), unbound), reports=()),
            broker_with(orders=(broker_order(),)),
        )
        assert result.mismatches == ()
        assert result.compared_orders == 1


class TestOrderMismatches:
    def test_a_routed_order_the_broker_does_not_hold(self) -> None:
        result = reconcile(pipeline_with(orders=(oms_order(),)), broker_with())
        found = result.mismatches_in(MismatchCategory.MISSING_EXPECTED_ORDER)
        assert len(found) == 1
        assert found[0].key == OMS_ID
        assert found[0].observed is None

    def test_a_broker_order_no_binding_names(self) -> None:
        result = reconcile(
            pipeline_with(), broker_with(orders=(broker_order(),)), ExternalOrderMap()
        )
        found = result.mismatches_in(MismatchCategory.UNEXPECTED_OBSERVED_ORDER)
        assert len(found) == 1
        assert found[0].expected is None

    def test_an_ordered_quantity_that_differs(self) -> None:
        result = reconcile(
            pipeline_with(orders=(oms_order(quantity="10"),)),
            broker_with(orders=(broker_order(quantity="12"),)),
        )
        found = result.mismatches_in(MismatchCategory.ORDER_QUANTITY_MISMATCH)
        assert len(found) == 1
        assert (found[0].expected, found[0].observed) == ("10", "12")

    def test_a_price_that_differs(self) -> None:
        result = reconcile(
            pipeline_with(orders=(oms_order(limit_price="100.00"),)),
            broker_with(orders=(broker_order(price="101.00"),)),
        )
        found = result.mismatches_in(MismatchCategory.ORDER_PRICE_MISMATCH)
        assert len(found) == 1

    def test_a_price_inside_tolerance_is_not_a_mismatch(self) -> None:
        result = reconcile(
            pipeline_with(orders=(oms_order(limit_price="100.00"),)),
            broker_with(orders=(broker_order(price="100.01"),)),
        )
        assert result.mismatches_in(MismatchCategory.ORDER_PRICE_MISMATCH) == ()

    def test_an_order_with_no_price_at_all_is_reported_rather_than_compared_to_zero(
        self,
    ) -> None:
        result = reconcile(
            pipeline_with(orders=(oms_order(limit_price=None),)),
            broker_with(orders=(broker_order(price="100.00"),)),
        )
        found = result.mismatches_in(MismatchCategory.ORDER_PRICE_MISMATCH)
        assert len(found) == 1
        assert found[0].expected is None
        assert "cannot be checked against anything" in found[0].reason

    def test_a_reference_price_in_metadata_is_the_price_that_was_sent(self) -> None:
        order = replace(oms_order(limit_price=None), metadata={"reference_price": "100.00"})
        result = reconcile(
            pipeline_with(orders=(order,)), broker_with(orders=(broker_order(price="100.00"),))
        )
        assert result.mismatches_in(MismatchCategory.ORDER_PRICE_MISMATCH) == ()

    def test_a_status_that_cannot_be_true_on_both_sides(self) -> None:
        result = reconcile(
            pipeline_with(orders=(oms_order(status=OrderStatus.ACCEPTED),)),
            broker_with(orders=(broker_order(status=OrderStatus.CANCELLED),)),
        )
        found = result.mismatches_in(MismatchCategory.ORDER_STATUS_MISMATCH)
        assert len(found) == 1
        assert (found[0].expected, found[0].observed) == ("ACCEPTED", "CANCELLED")

    @pytest.mark.parametrize("broker_status", list(BrokerOrderStatus))
    def test_a_broker_local_status_is_judged_against_its_declared_equivalents(
        self, broker_status: BrokerOrderStatus
    ) -> None:
        for oms_status in BROKER_STATUS_EQUIVALENTS[broker_status]:
            result = reconcile(
                pipeline_with(orders=(oms_order(status=oms_status),)),
                broker_with(orders=(broker_order(status=broker_status),)),
            )
            assert result.mismatches_in(MismatchCategory.ORDER_STATUS_MISMATCH) == ()

    def test_a_broker_local_status_outside_its_equivalents_is_a_mismatch(self) -> None:
        result = reconcile(
            pipeline_with(orders=(oms_order(status=OrderStatus.FILLED),)),
            broker_with(orders=(broker_order(status=BrokerOrderStatus.SUBMITTED),)),
        )
        assert len(result.mismatches_in(MismatchCategory.ORDER_STATUS_MISMATCH)) == 1

    def test_terminal_on_one_side_only_is_a_lifecycle_mismatch(self) -> None:
        result = reconcile(
            pipeline_with(orders=(oms_order(status=OrderStatus.CANCELLED),)),
            broker_with(orders=(broker_order(status=OrderStatus.ACCEPTED),)),
        )
        found = result.mismatches_in(MismatchCategory.LIFECYCLE_STATE_MISMATCH)
        assert len(found) == 1
        assert (found[0].expected, found[0].observed) == ("closed", "working")

    def test_a_binding_naming_an_order_the_run_does_not_hold(self) -> None:
        result = reconcile(pipeline_with(), broker_with(orders=(broker_order(),)))
        found = result.mismatches_in(MismatchCategory.LIFECYCLE_STATE_MISMATCH)
        assert len(found) == 1
        assert "does not hold" in found[0].reason

    def test_a_broker_order_naming_a_different_oms_order(self) -> None:
        other = "33333333-3333-3333-3333-333333333333"
        result = reconcile(
            pipeline_with(orders=(oms_order(),)),
            broker_with(orders=(broker_order(oms_order_id=other),)),
        )
        found = result.mismatches_in(MismatchCategory.LIFECYCLE_STATE_MISMATCH)
        assert any("names a different OMS order" in entry.reason for entry in found)


class TestFillMismatches:
    def test_a_fill_alphalab_applied_and_the_broker_does_not_report(self) -> None:
        result = reconcile(
            pipeline_with(orders=(oms_order(),), reports=(report(),)),
            broker_with(orders=(broker_order(),)),
        )
        found = result.mismatches_in(MismatchCategory.MISSING_EXPECTED_FILL)
        assert len(found) == 1
        assert found[0].key == "x-1"

    def test_a_fill_the_broker_reports_and_alphalab_has_not_applied(self) -> None:
        result = reconcile(
            pipeline_with(orders=(oms_order(),)),
            broker_with(orders=(broker_order(),), executions=(execution(),)),
        )
        found = result.mismatches_in(MismatchCategory.UNEXPECTED_OBSERVED_FILL)
        assert len(found) == 1
        assert "the book is missing a position" in found[0].reason

    def test_a_fill_quantity_that_differs(self) -> None:
        result = reconcile(
            pipeline_with(orders=(oms_order(),), reports=(report(quantity="10"),)),
            broker_with(orders=(broker_order(),), executions=(execution(quantity="9"),)),
        )
        found = result.mismatches_in(MismatchCategory.FILL_QUANTITY_MISMATCH)
        assert len(found) == 1
        assert (found[0].expected, found[0].observed) == ("10", "9")

    def test_a_fill_price_or_commission_that_differs_is_an_execution_mismatch(self) -> None:
        result = reconcile(
            pipeline_with(
                orders=(oms_order(),), reports=(report(price="100.00", commission="1.00"),)
            ),
            broker_with(
                orders=(broker_order(),),
                executions=(execution(price="101.00", commission="3.00"),),
            ),
        )
        found = result.mismatches_in(MismatchCategory.EXECUTION_MISMATCH)
        assert len(found) == 2

    def test_a_fill_applied_to_the_wrong_order_is_an_execution_mismatch(self) -> None:
        other = "44444444-4444-4444-4444-444444444444"
        result = reconcile(
            pipeline_with(orders=(oms_order(),), reports=(report(order_id=other),)),
            broker_with(orders=(broker_order(),), executions=(execution(),)),
        )
        found = result.mismatches_in(MismatchCategory.EXECUTION_MISMATCH)
        assert any("reports it against another" in entry.reason for entry in found)


class TestPositionMismatches:
    def test_a_position_size_that_differs(self) -> None:
        result = reconcile(
            pipeline_with(
                positions={
                    ASSET: Position(
                        ASSET,
                        Decimal("10"),
                        Decimal("100"),
                        Decimal("100"),
                        Decimal("0"),
                        "USD",
                        1.0,
                    )
                }
            ),
            broker_with(
                positions=(
                    BrokerPosition(
                        ASSET,
                        Decimal("12"),
                        Decimal("100"),
                        Decimal("1200"),
                        Decimal("0"),
                        Decimal("0"),
                    ),
                )
            ),
        )
        found = result.mismatches_in(MismatchCategory.POSITION_QUANTITY_MISMATCH)
        assert len(found) == 1
        assert (found[0].expected, found[0].observed) == ("10", "12")

    def test_a_position_the_broker_holds_and_the_book_does_not(self) -> None:
        result = reconcile(
            pipeline_with(),
            broker_with(
                positions=(
                    BrokerPosition(
                        "ghost",
                        Decimal("5"),
                        Decimal("10"),
                        Decimal("50"),
                        Decimal("0"),
                        Decimal("0"),
                    ),
                )
            ),
        )
        found = result.mismatches_in(MismatchCategory.UNEXPECTED_POSITION)
        assert len(found) == 1
        assert found[0].expected is None

    def test_a_position_the_book_holds_and_the_broker_does_not(self) -> None:
        result = reconcile(
            pipeline_with(
                positions={
                    ASSET: Position(
                        ASSET,
                        Decimal("10"),
                        Decimal("100"),
                        Decimal("100"),
                        Decimal("0"),
                        "USD",
                        1.0,
                    )
                }
            ),
            broker_with(),
        )
        found = result.mismatches_in(MismatchCategory.POSITION_QUANTITY_MISMATCH)
        assert len(found) == 1
        assert "does not report" in found[0].reason

    def test_a_venue_symbol_that_resolves_to_nothing(self) -> None:
        result = reconcile_execution_state(
            pipeline_with(),
            broker_with(
                positions=(
                    BrokerPosition(
                        "AAPL",
                        Decimal("5"),
                        Decimal("10"),
                        Decimal("50"),
                        Decimal("0"),
                        Decimal("0"),
                    ),
                )
            ),
            ExternalOrderMap(),
            SymbolMapping(mapping={}),
            TOLERANCES,
        )
        found = result.mismatches_in(MismatchCategory.INSTRUMENT_MISMATCH)
        assert len(found) == 1
        assert "resolves to no AlphaLab instrument" in found[0].reason

    def test_a_supplied_mapping_joins_a_venue_symbol_to_an_asset(self) -> None:
        result = reconcile_execution_state(
            pipeline_with(
                positions={
                    ASSET: Position(
                        ASSET, Decimal("5"), Decimal("10"), Decimal("10"), Decimal("0"), "USD", 1.0
                    )
                }
            ),
            broker_with(
                positions=(
                    BrokerPosition(
                        "AAPL",
                        Decimal("5"),
                        Decimal("10"),
                        Decimal("50"),
                        Decimal("0"),
                        Decimal("0"),
                    ),
                )
            ),
            ExternalOrderMap(),
            SymbolMapping(mapping={"AAPL": ASSET}),
            TOLERANCES,
        )
        assert result.mismatches == ()

    def test_two_symbols_resolving_to_one_asset_are_refused_rather_than_summed(self) -> None:
        result = reconcile_execution_state(
            pipeline_with(),
            broker_with(
                positions=(
                    BrokerPosition(
                        "AAPL",
                        Decimal("5"),
                        Decimal("10"),
                        Decimal("50"),
                        Decimal("0"),
                        Decimal("0"),
                    ),
                    BrokerPosition(
                        "AAPL.O",
                        Decimal("5"),
                        Decimal("10"),
                        Decimal("50"),
                        Decimal("0"),
                        Decimal("0"),
                    ),
                )
            ),
            ExternalOrderMap(),
            SymbolMapping(mapping={"AAPL": ASSET, "AAPL.O": ASSET}),
            TOLERANCES,
        )
        found = result.mismatches_in(MismatchCategory.INSTRUMENT_MISMATCH)
        assert any("would double an exposure" in entry.reason for entry in found)

    def test_an_order_on_the_wrong_instrument_is_an_instrument_mismatch(self) -> None:
        result = reconcile(
            pipeline_with(orders=(oms_order(asset_id=ASSET),)),
            broker_with(orders=(broker_order(symbol="asset-z"),)),
        )
        found = result.mismatches_in(MismatchCategory.INSTRUMENT_MISMATCH)
        assert len(found) == 1
        assert found[0].expected == ASSET


class TestCash:
    def test_a_cash_balance_that_differs(self) -> None:
        result = reconcile(
            pipeline_with(cash=Decimal("100000")), broker_with(cash=Decimal("99000"))
        )
        found = result.mismatches_in(MismatchCategory.ACCOUNT_CASH_MISMATCH)
        assert len(found) == 1
        assert found[0].key == "USD"

    def test_a_currency_the_broker_account_cannot_speak_about_is_unreconciled(self) -> None:
        state = base_pipeline(Decimal("100000"))
        funded = PortfolioEngine.apply_deposit(
            state.portfolio,
            Decimal("50000"),
            "JPY",
            2.0,
        )
        result = reconcile(
            replace(state, portfolio=funded),
            broker_with(cash=Decimal("100000"), currency="USD"),
            ExternalOrderMap(),
        )
        assert result.reconciled
        assert not result.fully_reconciled
        assert len(result.unreconciled) == 1
        assert "JPY" in result.unreconciled[0].reason
        assert "not the same as agreeing" in result.unreconciled[0].reason

    def test_a_broker_account_with_no_currency_compares_nothing(self) -> None:
        result = reconcile(pipeline_with(), broker_with(currency=" "), ExternalOrderMap())
        assert result.mismatches_in(MismatchCategory.ACCOUNT_CASH_MISMATCH) == ()
        assert len(result.unreconciled) == 1
        assert "no currency" in result.unreconciled[0].reason


class TestDeterminismAndStability:
    def test_reconciling_twice_returns_an_equal_result(self) -> None:
        pipeline = pipeline_with(orders=(oms_order(),), reports=(report(),))
        broker = broker_with(orders=(broker_order(quantity="12"),))
        assert reconcile(pipeline, broker) == reconcile(pipeline, broker)

    def test_reconciling_changes_neither_side(self) -> None:
        pipeline = pipeline_with(orders=(oms_order(),), reports=(report(),))
        broker = broker_with(orders=(broker_order(quantity="12"),), executions=(execution(),))
        before_pipeline, before_broker = pipeline, broker
        reconcile(pipeline, broker)
        assert pipeline == before_pipeline
        assert broker == before_broker

    def test_mismatches_come_out_in_category_order(self) -> None:
        result = reconcile(
            pipeline_with(
                orders=(oms_order(quantity="10", status=OrderStatus.CANCELLED),),
                reports=(report(),),
                positions={
                    ASSET: Position(
                        ASSET,
                        Decimal("10"),
                        Decimal("100"),
                        Decimal("100"),
                        Decimal("0"),
                        "USD",
                        1.0,
                    )
                },
                cash=Decimal("100000"),
            ),
            broker_with(
                orders=(broker_order(quantity="12"),),
                executions=(execution(execution_id="x-2"),),
                cash=Decimal("50000"),
            ),
        )
        order = list(MismatchCategory)
        indexes = [order.index(entry.category) for entry in result.mismatches]
        assert indexes == sorted(indexes)
        assert len(set(indexes)) > 3

    def test_an_inconsistent_binding_is_refused_before_anything_is_compared(self) -> None:
        broken = ExternalOrderMap(to_broker={OMS_ID: BROKER_ID}, to_oms={})
        with pytest.raises(LifecycleInputError, match="disagrees with itself"):
            reconcile(pipeline_with(), broker_with(), broken)

    def test_reconciled_and_fully_reconciled_are_different_questions(self) -> None:
        result = reconcile(pipeline_with(), broker_with(currency=" "), ExternalOrderMap())
        assert result.reconciled
        assert not result.fully_reconciled
