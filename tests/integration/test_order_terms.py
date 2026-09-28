"""An order carries the terms it was asked with, and the simulator honours them (EXE-003).

Until v3.11 the canonical path could create one kind of order. ``Intent`` had an
``execution_directive`` nothing read, ``OrderRequest`` had no order type, and the
pipeline built every OMS order ``MARKET`` and filled or withdrew it at the event
it was placed on. :class:`~alphalab.common.order_terms.OrderTerms` now travels
from the intent to the OMS order, the simulator works limit, stop, stop-limit,
immediate-or-cancel, fill-or-kill, good-til-date, day and auction orders by
fixed rules, and a live session routes each as what it is.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.allocation import AllocationEngine
from alphalab.broker import BrokerEngine, PaperBroker
from alphalab.common.exceptions import AlphaLabValidationError
from alphalab.common.order_terms import MARKET, OrderTerms, OrderType, TimeInForce
from alphalab.core.enums import OrderStatus
from alphalab.execution.commission import FixedCommission
from alphalab.execution.costs import (
    FREE,
    ExecutionCostModel,
    FixedHalfSpread,
    NoFee,
    NoImpact,
    NoSlippage,
    NoTax,
)
from alphalab.execution.policy import FillTiming, LiquidityCappedFill
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.record import MarketRecord
from alphalab.persistence import deserialize, serialize
from alphalab.runtime.broker_routing import RoutingConfig, route_order, venue_order_type
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineResult,
    ExecutionPipelineState,
)
from alphalab.runtime.snapshot import RuntimeObjects, capture, from_primitives, restore
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import (
    context_factory,
    pipeline_config,
)

ASSET = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
ZERO = Decimal("0")

# --------------------------------------------------------------------------- #
# The terms themselves
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("build", "message"),
    [
        (lambda: OrderTerms(OrderType.LIMIT), "names its limit price"),
        (lambda: OrderTerms(OrderType.MARKET, Decimal("1")), "has no limit price"),
        (lambda: OrderTerms(OrderType.STOP), "names its stop price"),
        (lambda: OrderTerms(OrderType.LIMIT, Decimal("1"), Decimal("1")), "has no stop price"),
        (lambda: OrderTerms.limit(Decimal("NaN")), "finite Decimal"),
        (lambda: OrderTerms.limit(Decimal("1"), TimeInForce.GTD), "names the instant"),
        (lambda: OrderTerms.limit(Decimal("1"), TimeInForce.GTC, 5.0), "has no expiry instant"),
        (
            lambda: OrderTerms.market(TimeInForce.DAY).__class__(
                OrderType.MARKET, time_in_force=TimeInForce.DAY, expire_at=float("inf")
            ),
            "must be an instant",
        ),
        (
            lambda: OrderTerms(OrderType.STOP, None, Decimal("1"), TimeInForce.OPG),
            "auction order is a market or a limit",
        ),
    ],
)
def test_terms_that_do_not_hold_are_refused_on_construction(build: Any, message: str) -> None:
    with pytest.raises(AlphaLabValidationError, match=message):
        build()


def test_terms_are_equal_by_value_and_say_what_they_do() -> None:
    assert OrderTerms.limit(Decimal("100")) == OrderTerms.limit(Decimal("100.00"))
    assert hash(OrderTerms.limit(Decimal("1E+2"))) == hash(OrderTerms.limit(Decimal("100")))
    assert OrderTerms.limit(Decimal("100.50")).rendering() == "limit good_til_cancelled limit=100.5"
    assert OrderTerms.market() == MARKET and not MARKET.rests
    assert OrderTerms.limit(Decimal("1")).rests
    assert OrderTerms.stop(Decimal("1")).rests
    assert OrderTerms.at_open().is_auction and OrderTerms.at_open().rests
    assert not OrderTerms.limit(Decimal("1"), TimeInForce.IOC).rests
    assert OrderTerms.limit(Decimal("1"), TimeInForce.FOK).is_immediate


def test_the_vocabulary_moved_without_becoming_another_class() -> None:
    from alphalab.core import enums

    assert enums.OrderType is OrderType
    assert enums.TimeInForce is TimeInForce


# --------------------------------------------------------------------------- #
# A strategy that asks for terms
# --------------------------------------------------------------------------- #


class _Asker(BaseStrategy):
    def __init__(self, plan: Mapping[float, tuple[Decimal, OrderTerms]]) -> None:
        self.plan = dict(plan)

    def _at(self, timestamp: float) -> Iterable[Intent]:
        planned = self.plan.get(timestamp)
        if planned is None:
            return ()
        delta, terms = planned
        return (Intent("A", ASSET, delta, terms=terms, timestamp=timestamp),)

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self._at(event.quote.timestamp)

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self._at(event.bar.timestamp)


def _runtime(strategy: BaseStrategy) -> RuntimeState:
    runtime = register_strategy(create_runtime(), "A", strategy)
    entry = runtime.strategies["A"]
    entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"*"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    return replace(runtime, strategies={"A": entry})


def _pipeline(
    plan: Mapping[float, tuple[Decimal, OrderTerms]],
    simulator: ExecutionSimulator | None = None,
    timing: FillTiming = FillTiming.SAME_EVENT,
) -> ExecutionPipelineState:
    config = replace(pipeline_config("A", simulator=simulator), fill_timing=timing)
    return ExecutionPipeline.initialize(config, _runtime(_Asker(plan)), 1.0)


def _quote(
    state: ExecutionPipelineState,
    at: float,
    bid: str,
    ask: str,
    size: str = "1000",
    policy: Any = None,
) -> ExecutionPipelineResult:
    from alphalab.market.quote import Quote

    quote = Quote(ASSET, at, Decimal(bid), Decimal(ask), Decimal(size), Decimal(size), "X", "USD")
    return ExecutionPipeline.process_quote(state, quote, context_factory, fill_policy=policy)


def _bar(
    state: ExecutionPipelineState,
    at: float,
    ohlc: tuple[str, str, str, str],
    timeframe: TimeFrame = TimeFrame.D1,
) -> ExecutionPipelineResult:
    o, h, low, c = (Decimal(value) for value in ohlc)
    bar = Bar(ASSET, at, o, h, low, c, Decimal("100000"), None, None, timeframe)
    return ExecutionPipeline.process_record(
        state, MarketRecord("DS-BARS", at, bar), context_factory
    )


def _order(state: ExecutionPipelineState) -> Any:
    (order,) = state.oms.orders.orders()
    return order


def _position(state: ExecutionPipelineState) -> Decimal:
    position = state.portfolio.positions.get(ASSET)
    return ZERO if position is None else position.quantity


# --------------------------------------------------------------------------- #
# Netting
# --------------------------------------------------------------------------- #


def test_only_equal_terms_net() -> None:
    from alphalab.allocation.budget import CapitalBudget
    from alphalab.allocation.constraints import AllocationConstraints
    from alphalab.allocation.sizing import FixedQuantitySizing

    state = AllocationEngine.initialize(CapitalBudget(Decimal("1000000"), Decimal("1000000")))
    limit = OrderTerms.limit(Decimal("99"))
    intents = (
        Intent("A", ASSET, Decimal("10"), terms=limit, timestamp=1.0),
        Intent("B", ASSET, Decimal("5"), terms=OrderTerms.limit(Decimal("99.00")), timestamp=1.0),
        Intent("A", ASSET, Decimal("-4"), timestamp=1.0),
    )
    _, requests = AllocationEngine.allocate(
        state,
        intents,
        {ASSET: Decimal("100")},
        FixedQuantitySizing(),
        AllocationConstraints(allow_shorting=True),
        1.0,
    )

    assert [(request.terms, request.side.value, request.quantity) for request in requests] == [
        (limit, "buy", Decimal("15")),
        (MARKET, "sell", Decimal("4")),
    ]
    assert [c.strategy_id for c in requests[0].contributions] == ["A", "B"]


def test_long_only_judges_each_order_against_the_ones_before_it() -> None:
    from alphalab.allocation.budget import CapitalBudget
    from alphalab.allocation.constraints import AllocationConstraints
    from alphalab.allocation.sizing import FixedQuantitySizing

    state = AllocationEngine.initialize(CapitalBudget(Decimal("1000000"), Decimal("1000000")))
    intents = (
        Intent("A", ASSET, Decimal("-10"), terms=OrderTerms.limit(Decimal("101")), timestamp=1.0),
        Intent("A", ASSET, Decimal("-10"), timestamp=1.0),
    )
    _, requests = AllocationEngine.allocate(
        state,
        intents,
        {ASSET: Decimal("100")},
        FixedQuantitySizing(),
        AllocationConstraints(allow_shorting=False),
        1.0,
        positions={ASSET: Decimal("10")},
    )

    # Two sells of the ten held would leave a short: the second is refused.
    assert [request.terms for request in requests] == [OrderTerms.limit(Decimal("101"))]


# --------------------------------------------------------------------------- #
# Limit orders
# --------------------------------------------------------------------------- #


def test_a_limit_below_the_market_rests_and_fills_at_its_limit_when_crossed() -> None:
    spread = ExecutionSimulator(
        cost_model=ExecutionCostModel(
            spread_model=FixedHalfSpread(Decimal("0.50")),
            slippage_model=NoSlippage(),
            impact_model=NoImpact(),
            commission_model=FixedCommission(Decimal("1.00")),
            fee_model=NoFee(),
            tax_model=NoTax(),
        )
    )
    state = _pipeline({2.0: (Decimal("10"), OrderTerms.limit(Decimal("99")))}, spread)

    placed = _quote(state, 2.0, "99.9", "100.1")
    assert placed.execution_reports == ()
    assert _order(placed.state).status is OrderStatus.ACCEPTED
    assert _order(placed.state).order_type is OrderType.LIMIT

    untouched = _quote(placed.state, 3.0, "99.4", "99.6")
    assert untouched.execution_reports == ()

    crossed = _quote(untouched.state, 4.0, "98.7", "98.9")
    (report,) = crossed.execution_reports
    # At its own limit, as a maker: no half spread, the commission still charged.
    assert (report.fill_price, report.fill_quantity) == (Decimal("99"), Decimal("10"))
    assert (report.liquidity_flag, report.slippage, report.commission) == (
        "MAKER",
        ZERO,
        Decimal("1.00"),
    )
    assert _order(crossed.state).status is OrderStatus.FILLED
    assert _position(crossed.state) == Decimal("10")


def test_a_marketable_limit_takes_the_market_within_its_limit() -> None:
    state = _pipeline({2.0: (Decimal("10"), OrderTerms.limit(Decimal("101")))})
    result = _quote(state, 2.0, "99.9", "100.1")

    (report,) = result.execution_reports
    assert report.liquidity_flag == "TAKER"
    assert report.fill_price == Decimal("100.0")  # the market price, costs being free


def test_a_limit_the_cost_model_would_take_past_its_limit_rests() -> None:
    """The half spread makes the ask 100.05; a limit at 100.02 does not cross it."""

    wide = ExecutionSimulator(
        cost_model=ExecutionCostModel(
            spread_model=FixedHalfSpread(Decimal("0.05")),
            slippage_model=NoSlippage(),
            impact_model=NoImpact(),
            commission_model=FixedCommission(ZERO),
            fee_model=NoFee(),
            tax_model=NoTax(),
        )
    )
    state = _pipeline({2.0: (Decimal("10"), OrderTerms.limit(Decimal("100.02")))}, wide)
    result = _quote(state, 2.0, "99.95", "100.05")

    assert result.execution_reports == ()
    assert _order(result.state).status is OrderStatus.ACCEPTED


def test_a_resting_limit_filled_in_part_rests_on_and_fills_later() -> None:
    state = _pipeline({2.0: (Decimal("10"), OrderTerms.limit(Decimal("99")))})
    state = _quote(state, 2.0, "99.9", "100.1").state
    capped = LiquidityCappedFill()

    part = _quote(state, 3.0, "98.8", "98.9", size="4", policy=capped)
    assert [report.fill_quantity for report in part.execution_reports] == [Decimal("4")]
    assert _order(part.state).status is OrderStatus.PARTIALLY_FILLED

    rest = _quote(part.state, 4.0, "98.8", "98.9", size="100", policy=capped)
    assert [report.fill_quantity for report in rest.execution_reports] == [Decimal("6")]
    assert _order(rest.state).status is OrderStatus.FILLED
    assert _position(rest.state) == Decimal("10")


def test_immediate_or_cancel_and_fill_or_kill_end_at_their_first_turn() -> None:
    ioc = _pipeline({2.0: (Decimal("10"), OrderTerms.limit(Decimal("99"), TimeInForce.IOC))})
    missed = _quote(ioc, 2.0, "99.9", "100.1")
    assert _order(missed.state).status is OrderStatus.CANCELLED
    assert missed.state.allocation.reservations == {}

    fok = _pipeline({2.0: (Decimal("10"), OrderTerms.limit(Decimal("101"), TimeInForce.FOK))})
    short = _quote(fok, 2.0, "99.9", "100.1", size="6", policy=LiquidityCappedFill())
    assert short.execution_reports == ()
    assert _order(short.state).status is OrderStatus.CANCELLED

    whole = _quote(fok, 2.0, "99.9", "100.1", size="60", policy=LiquidityCappedFill())
    assert [report.fill_quantity for report in whole.execution_reports] == [Decimal("10")]


def test_a_good_til_date_order_expires_and_frees_its_capital() -> None:
    terms = OrderTerms.limit(Decimal("90"), TimeInForce.GTD, expire_at=4.0)
    state = _quote(_pipeline({2.0: (Decimal("10"), terms)}), 2.0, "99.9", "100.1").state
    assert state.allocation.reservations

    still = _quote(state, 3.0, "99.9", "100.1").state
    assert _order(still).status is OrderStatus.ACCEPTED
    expired = _quote(still, 4.0, "80", "81").state  # would have crossed; it is too late

    assert _order(expired).status is OrderStatus.EXPIRED
    assert expired.allocation.reservations == {}
    assert _position(expired) == ZERO


def test_a_simulated_day_order_needs_its_session_close() -> None:
    day = OrderTerms.limit(Decimal("99"), TimeInForce.DAY)
    refused = _quote(_pipeline({2.0: (Decimal("10"), day)}), 2.0, "99.9", "100.1")
    assert refused.oms_orders == ()
    assert refused.state.allocation.reservations == {}

    closing = OrderTerms.limit(Decimal("99"), TimeInForce.DAY, expire_at=5.0)
    state = _quote(_pipeline({2.0: (Decimal("10"), closing)}), 2.0, "99.9", "100.1").state
    assert _order(_quote(state, 5.0, "90", "91").state).status is OrderStatus.EXPIRED


def test_terms_already_expired_are_refused_before_the_order_exists() -> None:
    terms = OrderTerms.limit(Decimal("99"), TimeInForce.GTD, expire_at=2.0)
    result = _quote(_pipeline({2.0: (Decimal("10"), terms)}), 2.0, "99.9", "100.1")

    assert result.oms_orders == ()


# --------------------------------------------------------------------------- #
# Stops
# --------------------------------------------------------------------------- #


def test_a_stop_waits_for_its_price_then_takes_the_market() -> None:
    state = _pipeline({2.0: (Decimal("10"), OrderTerms.stop(Decimal("102")))})
    state = _quote(state, 2.0, "99.9", "100.1").state
    assert _order(state).status is OrderStatus.ACCEPTED

    below = _quote(state, 3.0, "101.8", "101.9")
    assert below.execution_reports == ()

    through = _quote(below.state, 4.0, "102.4", "102.6")
    (report,) = through.execution_reports
    assert (report.fill_price, report.liquidity_flag) == (Decimal("102.5"), "TAKER")
    assert _order(through.state).status is OrderStatus.FILLED


def test_a_bar_that_gaps_through_a_stop_fills_at_its_open() -> None:
    state = _pipeline({2.0: (Decimal("-10"), OrderTerms.stop(Decimal("95")))})
    state = _bar(state, 2.0, ("100", "101", "99", "100")).state

    gapped = _bar(state, 3.0, ("90", "92", "88", "91"))
    (report,) = gapped.execution_reports
    assert report.fill_price == Decimal("90")  # the open: where the market was


def test_a_stop_limit_triggers_rests_as_a_limit_and_remembers_it() -> None:
    terms = OrderTerms.stop_limit(Decimal("102"), Decimal("102.50"))
    state = _pipeline({2.0: (Decimal("10"), terms)})
    state = _quote(state, 2.0, "99.9", "100.1").state

    # Reached at 104: triggered, but the limit is below the market -- it rests.
    triggered = _quote(state, 3.0, "103.9", "104.1").state
    order = _order(triggered)
    assert order.triggered_at == 3.0 and order.status is OrderStatus.ACCEPTED

    # The fact survives a snapshot: a restored run works it as the limit it is.
    objects = RuntimeObjects(
        sizing_model=triggered.config.sizing_model,
        simulator=triggered.config.simulator,
        strategies={"A": triggered.strategy.strategies["A"].instance},
    )
    restored = restore(from_primitives(deserialize(serialize(capture(triggered)))), objects)
    assert _order(restored).triggered_at == 3.0

    filled = _quote(restored, 4.0, "102.2", "102.4")
    (report,) = filled.execution_reports
    assert (report.fill_price, report.liquidity_flag) == (Decimal("102.50"), "MAKER")


# --------------------------------------------------------------------------- #
# Auctions
# --------------------------------------------------------------------------- #


def test_opening_and_closing_auction_orders_fill_at_the_next_daily_bar() -> None:
    at_open = _pipeline({2.0: (Decimal("10"), OrderTerms.at_open())})
    state = _bar(at_open, 2.0, ("100", "101", "99", "100")).state
    assert _order(state).status is OrderStatus.ACCEPTED, "not at the bar it was placed on"
    (report,) = _bar(state, 3.0, ("103", "106", "102", "105")).execution_reports
    assert (report.fill_price, report.liquidity_flag) == (Decimal("103"), "MAKER")

    at_close = _pipeline({2.0: (Decimal("10"), OrderTerms.at_close())})
    state = _bar(at_close, 2.0, ("100", "101", "99", "100")).state
    (report,) = _bar(state, 3.0, ("103", "106", "102", "105")).execution_reports
    assert report.fill_price == Decimal("105")


def test_a_limit_on_open_outside_its_limit_expires_unfilled() -> None:
    state = _pipeline({2.0: (Decimal("10"), OrderTerms.at_open(Decimal("101")))})
    state = _bar(state, 2.0, ("100", "101", "99", "100")).state
    result = _bar(state, 3.0, ("103", "106", "102", "105"))

    assert result.execution_reports == ()
    assert _order(result.state).status is OrderStatus.EXPIRED


def test_an_auction_order_without_daily_bars_is_refused() -> None:
    quotes = _quote(_pipeline({2.0: (Decimal("10"), OrderTerms.at_close())}), 2.0, "99", "101")
    assert quotes.oms_orders == ()

    intraday = _pipeline({2.0: (Decimal("10"), OrderTerms.at_close())})
    minute = _bar(intraday, 2.0, ("100", "101", "99", "100"), TimeFrame.M1)
    assert minute.oms_orders == ()


# --------------------------------------------------------------------------- #
# Parity, and the venue
# --------------------------------------------------------------------------- #


def test_market_orders_are_worked_exactly_as_before_under_either_timing() -> None:
    for timing in (FillTiming.SAME_EVENT, FillTiming.NEXT_EVENT):
        state = _pipeline({2.0: (Decimal("10"), MARKET)}, timing=timing)
        first = _quote(state, 2.0, "99.9", "100.1")
        second = _quote(first.state, 3.0, "100.9", "101.1")
        (report,) = (*first.execution_reports, *second.execution_reports)
        expected = Decimal("100.0") if timing is FillTiming.SAME_EVENT else Decimal("101.0")
        assert (report.fill_price, report.liquidity_flag) == (expected, "TAKER")


def test_a_live_session_sends_a_limit_as_a_limit_and_a_market_order_as_configured() -> None:
    from alphalab.runtime.execution_pipeline import ExecutionRouting

    config = replace(pipeline_config("A"), routing=ExecutionRouting.EXTERNAL)
    state = ExecutionPipeline.initialize(
        config,
        _runtime(
            _Asker({2.0: (Decimal("10"), OrderTerms.stop_limit(Decimal("102"), Decimal("103")))})
        ),
        1.0,
    )
    order = _order(_quote(state, 2.0, "99.9", "100.1").state)
    routing = RoutingConfig(venue="V", currency="USD", order_type=OrderType.LIMIT)

    assert venue_order_type(order, routing) is OrderType.STOP_LIMIT
    venue = BrokerEngine.initialize("V", Decimal("1000000"), "USD")
    from alphalab.broker.state import ConnectionStatus

    venue = replace(venue, connection_status=ConnectionStatus.CONNECTED)
    routed = route_order(venue, PaperBroker(FREE), order, 2.0, config=routing)
    assert routed.order is not None
    assert routed.order.order_type is OrderType.STOP_LIMIT
    assert (routed.order.price, routed.order.stop_price) == (Decimal("103"), Decimal("102"))
    assert routed.order.tif is TimeInForce.GTC
