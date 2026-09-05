"""Paper and live sessions over the real execution path.

Nothing here stubs a pipeline stage. Every test runs the actual
market -> strategy -> allocation -> risk -> OMS -> execution -> portfolio path;
what varies is only where records come from and where an accepted order goes.
"""

from decimal import Decimal

import pytest

from alphalab.backtesting.config import BacktestConfig
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.engine import BacktestEngine
from alphalab.broker import BrokerEngine, PaperBroker
from alphalab.broker.execution import BrokerExecution
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.core.enums import OrderStatus
from alphalab.market.source import SequenceSource
from alphalab.runtime.broker_routing import (
    RoutingConfig,
    RoutingRefusal,
    apply_broker_execution,
    broker_order_id_for,
    execution_report_from_broker,
    route_order,
)
from alphalab.runtime.execution_pipeline import ExecutionRouting
from alphalab.runtime.session import (
    ExecutionMode,
    SessionConfig,
    SessionState,
    TradingSession,
)
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    running_strategy_state,
)

_STRATEGY = "PAPER-STRAT"

#: Asset ids on the execution path are UUIDs -- `core.Fill` validates them --
#: so this is a fixed one rather than a fresh `uuid4()`, which keeps a seeded
#: run reproducible across test sessions.
_ASSET = "8f14e45f-ceea-467a-9c2a-1b3c5d7e9f01"
_MIDS = [Decimal("100"), Decimal("101"), Decimal("102")]
_PLAN = {2.0: Decimal("10"), 4.0: Decimal("-10")}


def _dataset() -> MarketDataset:
    return dataset_of_quotes(_ASSET, _MIDS)


def _strategy_state() -> StrategyRuntimeState:
    return running_strategy_state(_STRATEGY, ScriptedStrategy(_STRATEGY, _ASSET, _PLAN))


def _session_config(
    mode: ExecutionMode, max_market_data_age_seconds: float | None = None
) -> SessionConfig:
    backtest: BacktestConfig = backtest_config(_STRATEGY)
    return SessionConfig(
        pipeline=backtest.pipeline,
        mode=mode,
        fill_policy=backtest.fill_policy,
        seed=backtest.seed,
        start_timestamp=backtest.start_timestamp,
        max_market_data_age_seconds=max_market_data_age_seconds,
    )


# --- paper execution ---------------------------------------------------------


def test_a_paper_session_trades_and_accounts_end_to_end() -> None:
    """Paper is a complete run, not a scaffold: orders, fills, cash, positions."""
    dataset = _dataset()
    state = TradingSession.run(
        _session_config(ExecutionMode.PAPER),
        SequenceSource.from_records("PAPER", dataset.records),
        _strategy_state(),
        context_factory,
    )

    assert state.processed == 3
    assert len(state.pipeline.fills) == 2
    assert state.pipeline.portfolio.cash.balance("USD") != Decimal("1000000")
    # Bought 10 at 100 and sold 10 at 102: flat, +20 realised.
    assert _ASSET not in state.pipeline.portfolio.positions


def test_paper_and_backtest_over_the_same_records_agree_completely() -> None:
    """The parity claim, checked rather than asserted in prose."""
    dataset = _dataset()

    backtest = BacktestEngine.run(
        backtest_config(_STRATEGY), dataset, _strategy_state(), context_factory
    )
    paper = TradingSession.run(
        _session_config(ExecutionMode.PAPER),
        SequenceSource.from_records("DS", dataset.records),
        _strategy_state(),
        context_factory,
    )

    assert paper.processed == backtest.records_processed
    assert paper.pipeline.fills.to_tuple() == backtest.fills
    assert paper.pipeline.trades.to_tuple() == backtest.trades
    assert tuple(paper.pipeline.oms.orders.orders()) == backtest.orders
    assert paper.pipeline.portfolio.cash.balance("USD") == backtest.state.portfolio.cash.balance(
        "USD"
    )


def test_paper_uses_the_canonical_order_and_fill_types() -> None:
    from alphalab.core.fill import Fill
    from alphalab.oms.order import Order

    state = TradingSession.run(
        _session_config(ExecutionMode.PAPER),
        SequenceSource.from_records("P", _dataset().records),
        _strategy_state(),
        context_factory,
    )

    assert all(type(f) is Fill for f in state.pipeline.fills)
    assert all(type(o) is Order for o in state.pipeline.oms.orders.orders())


# --- stale market data -------------------------------------------------------


def test_a_stale_record_is_skipped_and_recorded_not_acted_on() -> None:
    """Acting on a stale quote is how a dead feed becomes real orders."""
    records = _dataset().records
    config = _session_config(ExecutionMode.PAPER, 1.0)

    state = TradingSession.run(
        config,
        SequenceSource.from_records("P", records),
        _strategy_state(),
        context_factory,
        clock=[r.timestamp + 10.0 for r in records],
    )

    assert state.processed == 0
    assert len(state.skipped) == len(records)
    assert "older than" in state.skipped[0].reason
    assert not state.pipeline.fills


def test_a_fresh_record_passes_the_same_gate() -> None:
    records = _dataset().records
    state = TradingSession.run(
        _session_config(ExecutionMode.PAPER, 1.0),
        SequenceSource.from_records("P", records),
        _strategy_state(),
        context_factory,
        clock=[r.timestamp + 0.5 for r in records],
    )

    assert state.processed == len(records)
    assert len(state.skipped) == 0


def test_a_historical_run_has_no_staleness_gate() -> None:
    """Every historical record is old; none of them is stale."""
    state = TradingSession.run(
        _session_config(ExecutionMode.BACKTEST),
        SequenceSource.from_records("P", _dataset().records),
        _strategy_state(),
        context_factory,
    )
    assert len(state.skipped) == 0


# --- the live boundary -------------------------------------------------------


def test_live_mode_leaves_orders_working_instead_of_inventing_fills() -> None:
    """A live venue has not answered yet, so nothing may be assumed about it."""
    state = TradingSession.run(
        _session_config(ExecutionMode.LIVE),
        SequenceSource.from_records("L", _dataset().records),
        _strategy_state(),
        context_factory,
    )

    assert state.processed == 3
    assert not state.pipeline.fills
    assert len(state.working_orders) == 2
    assert all(o.status is OrderStatus.ACCEPTED for o in state.working_orders)


def test_a_live_order_keeps_its_allocation_reservation() -> None:
    """The order is working, so the capital behind it is still committed."""
    state = TradingSession.run(
        _session_config(ExecutionMode.LIVE),
        SequenceSource.from_records("L", _dataset().records),
        _strategy_state(),
        context_factory,
    )
    assert len(state.pipeline.allocation.reservations) == len(state.working_orders)


def test_the_mode_decides_routing_even_if_the_config_disagrees() -> None:
    config = _session_config(ExecutionMode.LIVE)
    assert config.pipeline.routing is ExecutionRouting.EXTERNAL
    assert _session_config(ExecutionMode.PAPER).pipeline.routing is ExecutionRouting.SIMULATED


# --- routing an order to a venue ---------------------------------------------


def _live_session_with_a_working_order() -> SessionState:
    return TradingSession.run(
        _session_config(ExecutionMode.LIVE),
        SequenceSource.from_records("L", _dataset().records),
        _strategy_state(),
        context_factory,
    )


def _connected_broker() -> tuple[BrokerState, PaperBroker]:
    broker = PaperBroker()
    state = BrokerEngine.initialize("VENUE", Decimal("1000000.00"), "USD")
    state, _ = broker.connect(state, 1000.0)
    return state, broker


def test_routing_sends_the_order_and_binds_both_identities() -> None:
    session = _live_session_with_a_working_order()
    order = session.working_orders[0]
    broker_state, broker = _connected_broker()

    result = route_order(broker_state, broker, order, 1000.0)

    assert result.decision.routed
    assert result.order is not None
    assert result.order.broker_order_id == broker_order_id_for(order)
    assert result.order.oms_order_id == str(order.order_id.value)
    assert result.mapping.oms_id_for(result.order.broker_order_id) == str(order.order_id.value)


def test_routing_the_same_order_twice_never_creates_a_second_venue_order() -> None:
    """Idempotency: a retry after a lost response must not duplicate the order."""
    session = _live_session_with_a_working_order()
    order = session.working_orders[0]
    broker_state, broker = _connected_broker()

    first = route_order(broker_state, broker, order, 1000.0)
    second = route_order(first.broker_state, broker, order, 1001.0, first.mapping)

    assert not second.decision.routed
    assert second.decision.refusal is RoutingRefusal.DUPLICATE_SUBMISSION
    assert second.broker_state is first.broker_state
    assert len(first.broker_state.orders) == 1


def test_an_order_is_never_sent_on_a_connection_that_cannot_trade() -> None:
    session = _live_session_with_a_working_order()
    order = session.working_orders[0]
    broker = PaperBroker()
    disconnected = BrokerEngine.initialize("VENUE", Decimal("1000000.00"), "USD")

    result = route_order(disconnected, broker, order, 1000.0)

    assert not result.decision.routed
    assert result.decision.refusal is RoutingRefusal.DISCONNECTED
    assert result.broker_state is disconnected
    assert not result.broker_state.orders


@pytest.mark.parametrize(
    "status",
    [ConnectionStatus.RECONNECTING, ConnectionStatus.FAILED, ConnectionStatus.CONNECTING],
)
def test_only_a_connected_venue_accepts_orders(status: ConnectionStatus) -> None:
    from dataclasses import replace

    session = _live_session_with_a_working_order()
    order = session.working_orders[0]
    broker_state, broker = _connected_broker()
    degraded = replace(broker_state, connection_status=status)

    assert not route_order(degraded, broker, order, 1000.0).decision.routed


def test_a_refused_routing_can_be_retried_once_connected() -> None:
    session = _live_session_with_a_working_order()
    order = session.working_orders[0]
    broker = PaperBroker()
    state = BrokerEngine.initialize("VENUE", Decimal("1000000.00"), "USD")

    refused = route_order(state, broker, order, 1000.0)
    connected, _ = broker.connect(refused.broker_state, 1001.0)
    accepted = route_order(connected, broker, order, 1002.0, refused.mapping)

    assert accepted.decision.routed


def test_the_client_order_id_is_derived_not_minted() -> None:
    """Determinism here is what makes a retry address the same venue order."""
    session = _live_session_with_a_working_order()
    order = session.working_orders[0]
    assert broker_order_id_for(order) == broker_order_id_for(order)
    assert str(order.order_id.value) in broker_order_id_for(order)


# --- a venue fill coming back ------------------------------------------------


def _venue_fill(order_id: str, quantity: str, price: str, timestamp: float) -> BrokerExecution:
    return BrokerExecution(
        execution_id=f"EX-{order_id}",
        broker_order_id=order_id,
        symbol=_ASSET,
        fill_quantity=Decimal(quantity),
        fill_price=Decimal(price),
        commission=Decimal("1.00"),
        timestamp=timestamp,
        external_id=f"VENUE-{order_id}",
    )


def test_a_venue_fill_reaches_the_portfolio_through_the_canonical_path() -> None:
    """The whole point: broker fill -> Fill -> portfolio, with no live-only code."""
    session = _live_session_with_a_working_order()
    order = session.working_orders[0]
    cash_before = session.pipeline.portfolio.cash.balance("USD")

    fill = _venue_fill(broker_order_id_for(order), str(order.quantity), "100", 3.0)
    pipeline, fills, trades = apply_broker_execution(
        session.pipeline,
        order,
        fill,
        RoutingConfig(venue="VENUE", currency="USD"),
    )

    assert len(fills) == 1 and len(trades) == 1
    assert fills[0].quantity == order.quantity
    assert fills[0].price == Decimal("100")
    assert fills[0].commission == Decimal("1.00")

    # The OMS order advanced, the position opened, and cash moved -- all through
    # the same engines a simulated fill uses.
    assert pipeline.oms.orders.find(order.order_id).status is OrderStatus.FILLED
    assert pipeline.portfolio.positions[_ASSET].quantity == order.quantity
    assert pipeline.portfolio.cash.balance("USD") < cash_before


def test_a_partial_venue_fill_leaves_the_order_working() -> None:
    session = _live_session_with_a_working_order()
    order = session.working_orders[0]
    half = order.quantity / Decimal("2")

    pipeline, fills, _ = apply_broker_execution(
        session.pipeline,
        order,
        _venue_fill(broker_order_id_for(order), str(half), "100", 3.0),
    )

    assert fills[0].quantity == half
    working = pipeline.oms.orders.find(order.order_id)
    assert working.status is OrderStatus.PARTIALLY_FILLED
    assert working.remaining_quantity == half


def test_the_report_status_comes_from_the_oms_order_not_the_venue() -> None:
    from alphalab.execution.fill import FillStatus

    session = _live_session_with_a_working_order()
    order = session.working_orders[0]

    completing = execution_report_from_broker(
        _venue_fill("B-1", str(order.quantity), "100", 3.0), order
    )
    partial = execution_report_from_broker(
        _venue_fill("B-1", str(order.quantity / Decimal("2")), "100", 3.0), order
    )

    assert completing.status is FillStatus.FULL_FILL
    assert partial.status is FillStatus.PARTIAL_FILL


def test_a_venue_fill_records_no_slippage_it_did_not_measure() -> None:
    session = _live_session_with_a_working_order()
    order = session.working_orders[0]
    report = execution_report_from_broker(_venue_fill("B-1", "1", "100", 3.0), order)

    assert report.slippage == Decimal("0")
    assert report.liquidity_flag == ""


def test_a_venue_fill_consumes_the_allocation_reservation() -> None:
    session = _live_session_with_a_working_order()
    order = session.working_orders[0]
    order_id = str(order.order_id.value)
    assert order_id in session.pipeline.allocation.reservations

    pipeline, _, _ = apply_broker_execution(
        session.pipeline,
        order,
        _venue_fill(broker_order_id_for(order), str(order.quantity), "100", 3.0),
    )

    assert order_id not in pipeline.allocation.reservations


def test_routing_then_filling_walks_the_whole_live_round_trip() -> None:
    """Order out, fill back, portfolio updated -- every step through real code."""
    session = _live_session_with_a_working_order()
    order = session.working_orders[0]
    broker_state, broker = _connected_broker()

    routed = route_order(broker_state, broker, order, 1000.0)
    assert routed.order is not None

    # PaperBroker fills market orders on submission, so the venue already holds
    # the execution this round trip brings back.
    venue_execution = next(iter(routed.broker_state.executions.values()))
    pipeline, fills, _ = apply_broker_execution(session.pipeline, order, venue_execution)

    assert len(fills) == 1
    assert pipeline.oms.orders.find(order.order_id).is_closed
    assert pipeline.portfolio.positions[_ASSET].quantity == order.quantity
