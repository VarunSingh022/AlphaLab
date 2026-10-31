"""A queue model decides which resting limit orders reached the front (v4.1.1, ledger FUT-011).

Two halves. The model itself -- :class:`~alphalab.execution.queue.PriceTimeQueue`,
a pure function of a position and one event -- is held rule by rule. Then the
model on the historical drivers: what fills, when and how much, against the
same run without it; that the fills go through the canonical path; that a
replay equals the backtest and a replay paused at any boundary resumes to the
same queue and result; what is refused; and that a run declaring no model is
recorded exactly as it was.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    AlgorithmAssignment,
    BacktestEngine,
    ExecutionPlan,
    MarketDataset,
    PausedReplay,
    PriceTimeQueue,
    QueueBook,
    QueuedResult,
    QueueModelError,
    ReplayBacktest,
    ReplayResult,
    RunSchedule,
    ScheduledSplit,
)
from alphalab.backtesting.engine import initialize
from alphalab.backtesting.queueing import QUEUE_BOOK_SCHEMA, capture_queue, queue_from_primitives
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.common.ids import id_scope
from alphalab.common.order_terms import MARKET, OrderTerms, TimeInForce
from alphalab.core.enums import OrderStatus, Side
from alphalab.data.feed import TradeAggressor
from alphalab.execution.algorithms import TWAP, Urgency
from alphalab.execution.fill import FillStatus
from alphalab.execution.policy import FillTiming, LiquidityCappedFill, StaticFill
from alphalab.execution.queue import QueueObservation, QueuePosition
from alphalab.lifecycle import digest_run
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.quote import Quote
from alphalab.market.tick import Tick
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.portfolio.corporate_actions import Split
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import backtest_config, context_factory, equity, registry_of

ACME_RECORD = equity("ACME")
ACME = ACME_RECORD.asset_id
T0 = 1_700_000_000.0
LIMIT = Decimal("99.99")
QUEUE = PriceTimeQueue()
D = Decimal
HUNDRED = Decimal("100")


def quote(t: int, bid: str, bid_size: str, ask: str, ask_size: str) -> Quote:
    return Quote(ACME, T0 + t, D(bid), D(ask), D(bid_size), D(ask_size), "SIM", "USD")


def tick(t: int, price: str, quantity: str, side: TradeAggressor | None) -> Tick:
    return Tick(ACME, T0 + t, D(price), D(quantity), f"P{t}", "SIM", "USD", side)


def bar(t: int, open_: str, high: str, low: str, close: str) -> Bar:
    return Bar(
        ACME, T0 + t, D(open_), D(high), D(low), D(close), D("1000"), None, None, TimeFrame.M1
    )


SELLER = TradeAggressor.SELLER

#: A buy at 99.99 joins behind the 300 shown there; sellers then hit the bid --
#: 120, 200 (20 beyond the queue), 50 -- and the offer comes down to the limit.
DEPLETION = (
    quote(0, "99.99", "300", "100.01", "300"),
    tick(1, "99.99", "120", SELLER),
    tick(2, "99.99", "200", SELLER),
    tick(3, "99.99", "50", SELLER),
    quote(4, "99.98", "500", "99.99", "40"),
)


class Poster(BaseStrategy):
    """Places one order -- by default a buy of 100 at the limit -- at the first instant.

    Stateless, so a replay restored with a fresh instance continues as it was.
    """

    def __init__(self, terms: OrderTerms | None = None, quantity: Decimal = HUNDRED) -> None:
        self._terms = OrderTerms.limit(LIMIT) if terms is None else terms
        self._quantity = quantity

    def _place(self, timestamp: float) -> Iterable[Intent]:
        if timestamp != T0:
            return ()
        return (Intent("S", ACME, self._quantity, terms=self._terms, timestamp=timestamp),)

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self._place(event.timestamp)

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self._place(event.timestamp)

    def on_tick(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return self._place(event.timestamp)


def strategies(poster: Poster | None = None) -> StrategyRuntimeState:
    state = register_strategy(create_runtime(), "S", Poster() if poster is None else poster)
    running = {}
    for strategy_id, entry in state.strategies.items():
        configured, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
        initialized, _ = RuntimeSupervisor.initialize(configured, 1.1)
        subscribed, _ = RuntimeSupervisor.subscribe(initialized, frozenset({"*"}), 1.2)
        running[strategy_id], _ = RuntimeSupervisor.start(subscribed, 1.3)
    return replace(state, strategies=running)


def config(*, whole_units: bool = False, **settings: Any) -> RunConfig:
    base = backtest_config("S", **settings)
    constraints = replace(
        base.pipeline.allocation_constraints, enforce_integer_quantities=whole_units
    )
    pipeline = replace(
        base.pipeline,
        fill_timing=FillTiming.NEXT_EVENT,
        instruments=registry_of(ACME_RECORD),
        allocation_constraints=constraints,
    )
    return replace(base, pipeline=pipeline)


def dataset(records: Sequence[Quote | Tick | Bar], name: str = "QUEUE") -> MarketDataset:
    return MarketDataset.of(name, list(records))


def run(
    records: Sequence[Quote | Tick | Bar] = DEPLETION,
    model: PriceTimeQueue | None = QUEUE,
    poster: Poster | None = None,
    **settings: Any,
) -> Any:
    return BacktestEngine.run(
        config(**settings), dataset(records), strategies(poster), context_factory, queue=model
    )


def fills(result: Any) -> list[tuple[float, Decimal, Decimal, str]]:
    return [
        (report.timestamp - T0, report.fill_quantity, report.fill_price, report.liquidity_flag)
        for report in result.state.execution.history
    ]


def only_order(result: Any) -> Any:
    (order,) = result.state.oms.orders.orders()
    return order


def at(t: int, **shown: Any) -> QueueObservation:
    return QueueObservation(T0 + t, **shown)


# --------------------------------------------------------------------------- #
# The model, rule by rule
# --------------------------------------------------------------------------- #


def test_an_order_better_than_the_best_price_has_nothing_ahead() -> None:
    shown = at(0, bid=D("99.98"), bid_size=D("500"), ask=D("100.01"), ask_size=D("10"))
    assert QUEUE.join(Side.BUY, LIMIT, shown) == QueuePosition(LIMIT, D("0"))
    # A sell inside the spread, symmetrically.
    assert QUEUE.join(Side.SELL, D("100.00"), shown) == QueuePosition(D("100.00"), D("0"))


def test_an_order_at_the_best_price_queues_behind_everything_shown_there() -> None:
    shown = at(0, bid=LIMIT, bid_size=D("300"), ask=D("100.01"), ask_size=D("40"))
    assert QUEUE.join(Side.BUY, LIMIT, shown) == QueuePosition(LIMIT, D("300"))
    assert QUEUE.join(Side.SELL, D("100.01"), shown) == QueuePosition(D("100.01"), D("40"))


def test_behind_the_best_price_or_without_a_quote_the_queue_is_unknown() -> None:
    behind = at(0, bid=D("100.00"), bid_size=D("300"), ask=D("100.01"), ask_size=D("40"))
    assert QUEUE.join(Side.BUY, LIMIT, behind).ahead is None
    assert QUEUE.join(Side.BUY, LIMIT, at(0, low=D("99"), high=D("101"))).ahead is None
    assert (
        QUEUE.join(Side.BUY, LIMIT, at(0, trade_price=LIMIT, trade_quantity=D("5"))).ahead is None
    )


def test_a_print_at_the_limit_consumes_the_queue_before_the_order() -> None:
    position = QueuePosition(LIMIT, D("300"))
    hit = at(1, trade_price=LIMIT, trade_quantity=D("120"), aggressor=Side.SELL)
    short = QUEUE.advance(position, Side.BUY, hit)
    assert short.position.ahead == D("180") and short.eligible == D("0") and not short.may_fill
    beyond = QUEUE.advance(
        short.position,
        Side.BUY,
        at(2, trade_price=LIMIT, trade_quantity=D("200"), aggressor=Side.SELL),
    )
    assert beyond.position.ahead == D("0") and beyond.eligible == D("20")


def test_a_print_on_the_orders_own_side_or_unsigned_does_not_consume_it_by_default() -> None:
    position = QueuePosition(LIMIT, D("300"))
    lifted = at(1, trade_price=LIMIT, trade_quantity=D("500"), aggressor=Side.BUY)
    unsigned = at(1, trade_price=LIMIT, trade_quantity=D("500"))
    for shown in (lifted, unsigned):
        turn = QUEUE.advance(position, Side.BUY, shown)
        assert turn.position.ahead == D("300") and not turn.may_fill
    # Declared otherwise, an unsigned print counts -- the declaration is the model's.
    counting = PriceTimeQueue(count_unsigned_trades=True)
    assert counting.advance(position, Side.BUY, unsigned).eligible == D("200")
    assert counting.advance(position, Side.BUY, lifted).eligible == D("0")


@pytest.mark.parametrize(
    "shown",
    [
        at(1, bid=D("99.97"), bid_size=D("1"), ask=D("99.98"), ask_size=D("1")),
        at(1, trade_price=D("99.98"), trade_quantity=D("1")),
        at(1, low=D("99.98"), high=D("100.5")),
    ],
    ids=["quote", "print", "bar"],
)
def test_the_market_trading_through_the_limit_clears_the_queue(shown: QueueObservation) -> None:
    turn = QUEUE.advance(QueuePosition(LIMIT, None), Side.BUY, shown)
    assert turn.eligible is None and turn.position.ahead == D("0")


def test_a_sell_is_cleared_by_the_market_trading_above_it() -> None:
    limit = D("100.01")
    turn = QUEUE.advance(
        QueuePosition(limit, D("40")), Side.SELL, at(1, low=D("100"), high=D("100.02"))
    )
    assert turn.eligible is None
    touch = QUEUE.advance(QueuePosition(limit, D("40")), Side.SELL, at(1, low=D("99"), high=limit))
    assert not touch.may_fill


def test_touching_the_limit_fills_only_an_order_at_the_front() -> None:
    touch = at(1, low=LIMIT, high=D("100.5"))
    assert not QUEUE.advance(QueuePosition(LIMIT, D("1")), Side.BUY, touch).may_fill
    assert not QUEUE.advance(QueuePosition(LIMIT, None), Side.BUY, touch).may_fill
    assert QUEUE.advance(QueuePosition(LIMIT, D("0")), Side.BUY, touch).eligible is None


def test_the_displayed_level_bounds_the_queue_and_never_adds_to_it() -> None:
    position = QueuePosition(LIMIT, D("300"))

    def shown(bid: str, size: str) -> QueueObservation:
        return at(1, bid=D(bid), bid_size=D(size), ask=D("100.01"), ask_size=D("10"))

    # Fewer shown at the level: some of what was ahead has gone.
    assert QUEUE.advance(position, Side.BUY, shown("99.99", "120")).position.ahead == D("120")
    # More shown: arrivals behind the order; nothing ahead of it is added.
    assert QUEUE.advance(position, Side.BUY, shown("99.99", "900")).position.ahead == D("300")
    # The best bid below the level: nothing is displayed there, so nothing is ahead.
    assert QUEUE.advance(position, Side.BUY, shown("99.98", "900")).position.ahead == D("0")
    # The best bid above the level: the level's size is not shown, the bound stands.
    assert QUEUE.advance(position, Side.BUY, shown("100.00", "5")).position.ahead == D("300")
    # An unknown queue takes the size shown at its level as its bound.
    unknown = QueuePosition(LIMIT, None)
    assert QUEUE.advance(unknown, Side.BUY, shown("99.99", "70")).position.ahead == D("70")


def test_a_queue_book_round_trips_and_is_held_to_its_model() -> None:
    book = (
        QueueBook(QUEUE)
        .placed("a", QueuePosition(LIMIT, D("12.5")))
        .placed("b", QueuePosition(D("101"), None))
    )
    payload = json.loads(json.dumps(capture_queue(book)))
    assert payload["schema_version"] == QUEUE_BOOK_SCHEMA
    assert queue_from_primitives(payload, PriceTimeQueue()) == book
    with pytest.raises(QueueModelError, match="was supplied"):
        queue_from_primitives(payload, PriceTimeQueue(count_unsigned_trades=True))
    with pytest.raises(QueueModelError, match="schema"):
        queue_from_primitives({**payload, "schema_version": 99}, QUEUE)
    assert book.keeping(["b"]).positions == {"b": QueuePosition(D("101"), None)}


# --------------------------------------------------------------------------- #
# The model on the historical drivers
# --------------------------------------------------------------------------- #


def test_a_resting_buy_fills_only_what_trades_beyond_the_queue_ahead_of_it() -> None:
    plain = run(model=None)
    # Without a queue the first print at the limit fills the whole order.
    assert fills(plain) == [(1.0, D("100"), LIMIT, "MAKER")]

    queued = run()
    assert isinstance(queued, QueuedResult)
    assert fills(queued) == [
        (2.0, D("20"), LIMIT, "MAKER"),  # 200 printed, 180 of it the queue's
        (3.0, D("50"), LIMIT, "MAKER"),  # at the front: the whole print
        (4.0, D("30"), LIMIT, "MAKER"),  # the offer touches the limit
    ]
    # One OMS order, filled in three pieces through the canonical path.
    order = only_order(queued)
    assert order.status is OrderStatus.FILLED and order.filled_quantity == D("100")
    position = queued.state.portfolio.positions[ACME]
    assert position.quantity == D("100") and position.average_cost == LIMIT
    assert sum(f.quantity for f in queued.fills) == D("100")
    assert not queued.state.allocation.reservations
    assert not queued.queue.positions  # nothing left resting


#: 120 then 130 at the limit: 250 of the 300 ahead, so the order is not reached.
SHORT_OF_THE_FRONT = (*DEPLETION[:2], tick(2, "99.99", "130", SELLER))


def test_a_queue_not_yet_cleared_fills_nothing_and_the_order_rests() -> None:
    queued = run(SHORT_OF_THE_FRONT)
    assert fills(queued) == []
    order = only_order(queued)
    assert order.status is OrderStatus.ACCEPTED and order.is_open
    assert queued.queue.positions == {str(order.order_id.value): QueuePosition(LIMIT, D("50"))}
    assert fills(run(SHORT_OF_THE_FRONT, model=None))


def test_a_bar_touching_the_limit_does_not_fill_an_unknown_queue_and_one_through_it_does() -> None:
    bars = (
        bar(0, "100", "100.5", "99.995", "100.2"),  # placed here: the queue is unknown
        bar(60, "100.2", "100.4", "99.99", "100.1"),  # touches 99.99
        bar(120, "100.1", "100.3", "99.50", "99.8"),  # trades through it
    )
    assert fills(run(bars, model=None)) == [(60.0, D("100"), LIMIT, "MAKER")]
    assert fills(run(bars)) == [(120.0, D("100"), LIMIT, "MAKER")]
    # A bar opening beyond the limit fills at its better open, as without a queue.
    gapped = (*bars[:1], bar(60, "99.50", "99.7", "99.40", "99.6"))
    assert fills(run(gapped)) == [(60.0, D("100"), D("99.50"), "MAKER")]


def test_the_fill_policy_still_decides_within_what_the_queue_allows() -> None:
    capped = run(fill_policy=LiquidityCappedFill(D("0.5")))
    # Each print offers the policy half its size, held to the queue's release:
    # 100 printed -> 50 allowed, 20 released at t2; 25 of the 50 at t3; then
    # half the 40 shown on the offer at t4.
    assert [(t, q) for t, q, _, _ in fills(capped)] == [
        (2.0, D("20")),
        (3.0, D("25")),
        (4.0, D("20")),
    ]


def test_a_whole_unit_run_floors_what_the_queue_releases() -> None:
    records = (
        quote(0, "99.99", "300", "100.01", "300"),
        tick(1, "99.99", "320.5", SELLER),
        tick(2, "99.99", "0.7", SELLER),
    )
    queued = run(records, whole_units=True)
    assert fills(queued) == [(1.0, D("20"), LIMIT, "MAKER")]
    # 0.7 at the front floors to nothing: no fill, the order rests on.
    assert only_order(queued).remaining_quantity == D("80")


def test_an_expired_order_leaves_the_queue_book_and_never_fills() -> None:
    expiring = Poster(OrderTerms.limit(LIMIT, TimeInForce.GTD, expire_at=T0 + 2))
    queued = run(poster=expiring)
    order = only_order(queued)
    assert order.status is OrderStatus.EXPIRED and fills(queued) == []
    assert not queued.queue.positions


def test_the_fill_policy_is_not_asked_about_an_order_the_queue_has_not_reached() -> None:
    # A venue that expires whatever it is asked to fill: without a queue the
    # first print at the limit reaches the order and the venue expires it; with
    # one, the order is behind 300 and nothing is asked of the venue until a
    # print reaches it, at t2.
    expiring = StaticFill(FillStatus.EXPIRED)
    plain = only_order(run(model=None, fill_policy=expiring))
    queued = only_order(run(fill_policy=expiring))
    assert plain.status is OrderStatus.EXPIRED and queued.status is OrderStatus.EXPIRED
    assert plain.updated_at == T0 + 1 and queued.updated_at == T0 + 2


def test_a_paused_replay_holds_only_the_orders_still_resting() -> None:
    paused = ReplayBacktest.run(
        config(),
        dataset(DEPLETION),
        strategies(Poster(OrderTerms.limit(LIMIT, TimeInForce.GTD, expire_at=T0 + 2))),
        context_factory,
        queue=QUEUE,
        pause_when=lambda progress: progress.records_replayed == 4,
    )
    assert isinstance(paused, PausedReplay) and paused.queue is not None
    order = paused.run.pipeline.oms.orders.orders()[0]
    assert order.status is OrderStatus.EXPIRED
    assert not paused.queue.positions and not _payload(paused)["queue"]["positions"]


def test_each_step_leaves_only_working_orders_in_the_book() -> None:
    """The book a step hands back holds no order that ended at its turn there."""

    expiring = Poster(OrderTerms.limit(LIMIT, TimeInForce.GTD, expire_at=T0 + 2))
    book = QueueBook(QUEUE)
    held: list[int] = []
    cfg = config()
    with id_scope(cfg.seed):
        state = initialize(cfg, strategies(expiring))
        for record in dataset(DEPLETION).records:
            state, result = RunEngine.advance(state, record, context_factory, queue=book)
            assert result is not None and result.queue is not None
            book = result.queue
            held.append(len(book.positions))
    assert held == [1, 1, 0, 0, 0]  # joined at t0, expired at its turn at t2


def test_an_order_a_split_cancels_leaves_the_book_a_paused_replay_keeps() -> None:
    # A split between t1 and t2 cancels the resting order outside any turn of
    # its own; the book carried by a pause after it holds nothing.
    split = RunSchedule.of(splits=[ScheduledSplit(Split(ACME, D("2")), T0 + 1.5)])
    paused = ReplayBacktest.run(
        config(),
        dataset(DEPLETION),
        strategies(),
        context_factory,
        schedule=split,
        queue=QUEUE,
        pause_when=lambda progress: progress.records_replayed == 3,
    )
    assert isinstance(paused, PausedReplay) and paused.queue is not None
    assert paused.run.pipeline.oms.orders.orders()[0].status is OrderStatus.CANCELLED
    assert not paused.queue.positions


def test_a_stop_limit_joins_its_queue_when_it_is_triggered() -> None:
    records = (
        quote(0, "99.98", "300", "100.01", "300"),  # placed: untriggered, not queued
        quote(1, "99.99", "300", "100.03", "300"),  # triggered; rests at 99.99 behind 300
        tick(2, "99.99", "120", SELLER),  # 180 ahead
        quote(3, "99.99", "400", "100.03", "300"),  # more shown: arrivals behind it
        tick(4, "99.99", "200", SELLER),  # 20 beyond the queue
    )
    stop_limit = OrderTerms.stop_limit(D("100.02"), LIMIT)
    plain = run(records, model=None, poster=Poster(stop_limit))
    assert fills(plain)[0][0] == 2.0
    queued = run(records, poster=Poster(stop_limit))
    assert fills(queued) == [(4.0, D("20"), LIMIT, "MAKER")]


@pytest.mark.parametrize(
    "terms",
    [MARKET, OrderTerms.limit(D("100.01"), TimeInForce.IOC), OrderTerms.limit(D("100.10"))],
    ids=["market", "immediate-or-cancel", "marketable-limit"],
)
def test_an_order_that_takes_the_market_is_not_governed(terms: OrderTerms) -> None:
    poster = Poster(terms)
    plain = run(poster=poster, model=None)
    queued = run(poster=Poster(terms))
    assert fills(queued) == fills(plain) and fills(plain)


def test_an_order_placed_at_another_assets_event_starts_from_an_unknown_queue() -> None:
    other = equity("OTHER")
    records = (
        quote(-1, "99.99", "300", "100.01", "300"),
        Quote(other.asset_id, T0, D("10"), D("10.02"), D("5"), D("5"), "SIM", "USD"),
        tick(1, "99.99", "120", SELLER),  # unknown queue: a print at the limit is not counted
        quote(2, "99.99", "180", "100.01", "300"),  # the level now bounds it at 180
        tick(3, "99.99", "200", SELLER),
    )
    base = config()
    two = replace(
        base, pipeline=replace(base.pipeline, instruments=registry_of(ACME_RECORD, other))
    )
    queued = BacktestEngine.run(
        two, dataset(records), strategies(Poster()), context_factory, queue=QUEUE
    )
    assert fills(queued) == [(3.0, D("20"), LIMIT, "MAKER")]
    plain = BacktestEngine.run(two, dataset(records), strategies(Poster()), context_factory)
    assert fills(plain) == [(1.0, D("100"), LIMIT, "MAKER")]


def test_the_replay_equals_the_backtest_under_a_queue() -> None:
    backtest = run()
    replayed = ReplayBacktest.run(
        config(), dataset(DEPLETION), strategies(), context_factory, queue=QUEUE
    )
    assert isinstance(replayed, ReplayResult)
    assert replayed.queue == backtest.queue
    assert fills(replayed.backtest) == fills(backtest)
    assert replayed.backtest.valuation == backtest.valuation


def _replay(pause: int | None = None) -> ReplayResult | PausedReplay:
    return ReplayBacktest.run(
        config(),
        dataset(DEPLETION),
        strategies(),
        context_factory,
        queue=QUEUE,
        pause_when=lambda progress: pause is not None and progress.records_replayed == pause,
    )


def _objects(paused: PausedReplay) -> RunObjects:
    pipeline = paused.run.config.pipeline
    return RunObjects(
        RuntimeObjects(
            pipeline.sizing_model, pipeline.simulator, {"S": Poster()}, pipeline.instruments
        ),
        paused.run.config.fill_policy,
    )


def _payload(paused: PausedReplay) -> dict[str, Any]:
    return dict(deserialize(serialize(capture_replay(paused))))


def test_a_replay_paused_anywhere_resumes_to_the_same_queue_and_result() -> None:
    whole = _replay()
    assert isinstance(whole, ReplayResult)
    for pause in range(1, len(DEPLETION)):
        paused = _replay(pause)
        assert isinstance(paused, PausedReplay) and paused.queue is not None
        payload = _payload(paused)
        assert payload["queue"]["model"] == "PriceTimeQueue(count_unsigned_trades=False)"
        restored = restore_replay(
            replay_from_primitives(payload), _objects(paused), dataset(DEPLETION), queue=QUEUE
        )
        assert restored.queue == paused.queue
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert digest_run(resumed.backtest).result_id == digest_run(whole.backtest).result_id
        assert resumed.queue == whole.queue
        assert fills(resumed.backtest) == fills(whole.backtest)


def test_a_paused_replay_is_held_to_its_queue_model() -> None:
    paused = _replay(2)
    assert isinstance(paused, PausedReplay)
    snapshot = replay_from_primitives(_payload(paused))
    objects = _objects(paused)
    with pytest.raises(StateDecodeError, match="none was supplied"):
        restore_replay(snapshot, objects, dataset(DEPLETION))
    with pytest.raises(StateDecodeError, match="queue book cannot be continued"):
        restore_replay(
            snapshot, objects, dataset(DEPLETION), queue=PriceTimeQueue(count_unsigned_trades=True)
        )
    plain = ReplayBacktest.run(
        config(),
        dataset(DEPLETION),
        strategies(),
        context_factory,
        pause_when=lambda progress: progress.records_replayed == 2,
    )
    assert isinstance(plain, PausedReplay) and plain.queue is None
    with pytest.raises(StateDecodeError, match="no queue model"):
        restore_replay(
            replay_from_primitives(_payload(plain)), objects, dataset(DEPLETION), queue=QUEUE
        )


def test_a_queue_model_is_refused_where_nothing_is_simulated() -> None:
    plan = ExecutionPlan(AlgorithmAssignment(TWAP(2, Urgency.neutral()), 600.0, D("1")))
    with pytest.raises(QueueModelError, match="execution plan's children"):
        BacktestEngine.run(
            config(), dataset(DEPLETION), strategies(), context_factory, execution=plan, queue=QUEUE
        )
    # A paper run simulates its fills, so a queue may govern them; a live run's
    # orders are its venue's.
    live = initialize(replace(config(), mode=ExecutionMode.LIVE), strategies())
    with pytest.raises(RuntimeValidationError, match="routes EXTERNAL"):
        RunEngine.advance(
            live, dataset(DEPLETION).records[0], context_factory, queue=QueueBook(QUEUE)
        )


def test_a_run_is_recorded_as_it_was_and_its_result_names_the_model() -> None:
    market = Poster(MARKET)
    plain = run(poster=market, model=None)
    queued = run(poster=Poster(MARKET))
    # The queue book is driver state: the run's record and identities do not move.
    assert digest_run(queued) == digest_run(plain)
    assert type(plain) is not QueuedResult

    assumptions = queued.execution_assumptions
    assert assumptions.queue_model == "PriceTimeQueue(count_unsigned_trades=False)"
    assert "queue model" in assumptions.passive_fills and "front" in assumptions.passive_fills
    assert assumptions.settings()["execution.queue_model"] == assumptions.queue_model
    assert assumptions.optimistic == plain.execution_assumptions.optimistic

    without = plain.execution_assumptions
    assert without.queue_model is None and "execution.queue_model" not in without.settings()
    assert "optimistic for passive orders" in without.passive_fills
