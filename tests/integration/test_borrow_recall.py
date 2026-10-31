"""A lender's recall, and the buy-in that follows its deadline (v4.2.0, ledger FUT-022).

A recall is an entry in an instrument's borrow terms: from its notice, less is
available, and from its ``buy_in_at`` deadline a short beyond what is available
is bought in. The buy-in is each short strategy's own target through
allocation, risk and the OMS -- a market order resting to the asset's next
event -- so it fills as the market allows: at the market's price, in part with
thin liquidity, not at all in a halt or with nothing shown. What it leaves is
bought in at the next step; a buy-in working is never placed twice.

ACME quotes 100 from 2.0, one quote a second; the strategy shorts 100 at 2.0.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
)
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.backtesting.schedule import ScheduledBorrowFee
from alphalab.common.order_terms import OrderTerms, TimeInForce
from alphalab.conventions.daycount import DayCount
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.conventions.lot import LotSpecification
from alphalab.execution.policy import FillPolicy, LiquidityCappedFill
from alphalab.instrument.borrow import (
    BorrowAvailability,
    BorrowTerms,
    borrow_from_primitives,
    borrow_to_primitives,
)
from alphalab.instrument.controls import TradingControls, TradingHalt
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.registry import declare_trading_controls
from alphalab.instrument.snapshot import capture as capture_registry
from alphalab.instrument.snapshot import from_primitives as registry_from_primitives
from alphalab.instrument.snapshot import restore as restore_registry
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.types import TransactionType
from alphalab.risk.events import BuyIn
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from tests.integration import test_margin_and_liquidation as margin
from tests.integration.harness import (
    context_factory,
    quote,
    registry_of,
    running_strategy_state,
    sized_quote,
)

D = Decimal
RATE = D("0.10")


def lent(*entries: BorrowAvailability) -> Any:
    return replace(margin.PLAIN, borrow=BorrowTerms(entries, DayCount.ACT_360))


#: 100 lent from 1.0; at 3.0 the lender recalls all of it, to be covered by 4.0.
RECALLED = lent(
    BorrowAvailability(1.0, D("100"), RATE),
    BorrowAvailability(3.0, D("0"), RATE, buy_in_at=4.0),
)
SHORT = {(2.0, margin.PLAIN.asset_id): D("-100")}


class Reader(margin.Trader):
    """Trades its plan; keeps what its universe says of the borrow at each quote."""

    def __init__(self, plan: dict[tuple[float, str], Any]) -> None:
        super().__init__({k: v for k, v in plan.items() if isinstance(v, Decimal)})
        self._orders = {k: v for k, v in plan.items() if isinstance(v, Intent)}
        self.seen: list[tuple[float, Any, Any]] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        at, asset = event.quote.timestamp, event.quote.asset_id
        record = context.universe.instrument(asset)
        entry = None if record is None or record.borrow is None else record.borrow.entries[-1]
        self.seen.append((at, context.universe.borrow(asset), entry))
        own = self._orders.get((at, asset))
        return (own,) if own is not None else super().on_quote(context, event)


def run(
    record: Any,
    plan: dict[tuple[float, str], Any] | None = None,
    *,
    mids: dict[float, str] | None = None,
    sizes: dict[float, str] | None = None,
    fill_policy: FillPolicy | None = None,
    controls: TradingControls | None = None,
    schedule: RunSchedule | None = None,
    strategy: Reader | None = None,
) -> tuple[BacktestResult, Reader]:
    trader = strategy if strategy is not None else Reader(SHORT if plan is None else plan)
    cfg = config(record, fill_policy, controls)
    return (
        BacktestEngine.run(
            cfg,
            data(record, mids, sizes),
            running_strategy_state("S", trader),
            context_factory,
            schedule=RunSchedule.of() if schedule is None else schedule,
        ),
        trader,
    )


def config(record: Any, fill_policy: FillPolicy | None, controls: TradingControls | None) -> Any:
    cfg = margin.config(record, fill_policy=fill_policy)
    if controls is None:
        return cfg
    registry = declare_trading_controls(cfg.pipeline.instruments, controls)
    return replace(cfg, pipeline=replace(cfg.pipeline, instruments=registry))


def data(
    record: Any, mids: dict[float, str] | None, sizes: dict[float, str] | None
) -> MarketDataset:
    series = mids or margin.series("100", "100", "100", "100", "100", "100")
    quotes = [
        quote(record.asset_id, at, D(mid))
        if sizes is None
        else sized_quote(record.asset_id, at, D(mid), D(sizes[at]))
        for at, mid in series.items()
    ]
    return MarketDataset.of("RECALL", quotes)


def buy_ins(result: BacktestResult) -> list[BuyIn]:
    return [e for e in result.state.risk.events if isinstance(e, BuyIn)]


def fills(result: BacktestResult) -> list[tuple[float, Decimal]]:
    return margin.fills(result, margin.PLAIN)


# --------------------------------------------------------------------------- #
# Recalled and bought in
# --------------------------------------------------------------------------- #


def test_a_full_recall_is_bought_in_after_its_deadline_at_the_markets_next_price() -> None:
    result, _ = run(RECALLED, mids=margin.series("100", "101", "102", "103", "104", "105"))
    # Shorted at 2.0; the recall at 3.0 is a notice; at 4.0 its deadline has
    # passed, so the buy-in is placed there and fills at the next quote, 5.0.
    assert fills(result) == [(2.0, D("-100")), (5.0, D("100"))]
    (bought,) = buy_ins(result)
    assert (bought.timestamp, bought.short, bought.available, bought.excess) == (
        4.0,
        D("100"),
        D("0"),
        D("100"),
    )
    assert len(bought.orders) == 1
    covered = [
        t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.BUY
    ]
    assert [t.price for t in covered] == [D("103.00")]
    assert margin.PLAIN.asset_id not in result.state.portfolio.positions


def test_a_partial_recall_buys_in_only_the_short_beyond_what_is_still_lent() -> None:
    record = lent(
        BorrowAvailability(1.0, D("100"), RATE),
        BorrowAvailability(3.0, D("40"), RATE, buy_in_at=3.0),
    )
    result, _ = run(record)
    assert fills(result) == [(2.0, D("-100")), (4.0, D("60"))]
    (bought,) = buy_ins(result)
    assert (bought.timestamp, bought.excess) == (3.0, D("60"))
    assert margin.held(result, margin.PLAIN) == D("-40")


def test_a_buy_in_covers_in_whole_lots_rounding_the_excess_up() -> None:
    lots = InstrumentEconomics(
        D("1"), SettlementModel.CASH_EQUITY, lot=LotSpecification(D("10"), D("10"))
    )
    record = replace(
        lent(
            BorrowAvailability(1.0, D("100"), RATE),
            BorrowAvailability(3.0, D("35"), RATE, buy_in_at=3.0),
        ),
        economics=lots,
    )
    result, _ = run(record)
    # 65 beyond what is lent, in lots of 10: 70 bought in, never less than due.
    (bought,) = buy_ins(result)
    assert bought.excess == D("70")
    assert margin.held(result, margin.PLAIN) == D("-30")


def test_a_recall_with_no_deadline_keeps_the_short_and_refuses_more() -> None:
    record = lent(BorrowAvailability(1.0, D("100"), RATE), BorrowAvailability(3.0, D("0"), RATE))
    plan = {**SHORT, (4.0, margin.PLAIN.asset_id): D("-10")}
    result, trader = run(record, plan)
    assert fills(result) == [(2.0, D("-100"))]
    assert buy_ins(result) == []
    assert any("available to borrow" in r for r in trader.rejections)


def test_a_deadline_between_events_buys_in_at_the_first_event_after_it() -> None:
    # The deadline falls overnight, after the 3.0 quote; the next quote is at
    # 10.0, where the buy-in is placed, and it fills at the one after.
    record = lent(
        BorrowAvailability(1.0, D("100"), RATE),
        BorrowAvailability(3.0, D("0"), RATE, buy_in_at=3.5),
    )
    mids = {2.0: "100", 3.0: "100", 10.0: "100", 11.0: "100"}
    result, _ = run(record, mids=mids)
    assert [b.timestamp for b in buy_ins(result)] == [10.0]
    assert fills(result) == [(2.0, D("-100")), (11.0, D("100"))]


def test_two_recalls_each_buy_in_what_their_own_entry_leaves_uncovered() -> None:
    record = lent(
        BorrowAvailability(1.0, D("100"), RATE),
        BorrowAvailability(3.0, D("60"), RATE, buy_in_at=3.0),
        BorrowAvailability(5.0, D("0"), RATE, buy_in_at=5.0),
    )
    result, _ = run(record)
    assert [(b.timestamp, b.excess) for b in buy_ins(result)] == [(3.0, D("40")), (5.0, D("60"))]
    assert fills(result) == [(2.0, D("-100")), (4.0, D("40")), (6.0, D("60"))]


# --------------------------------------------------------------------------- #
# The market decides the buy-in: liquidity, halts, nothing shown
# --------------------------------------------------------------------------- #


def test_thin_liquidity_buys_in_part_and_the_rest_at_the_next_step() -> None:
    # 100 shown each quote, a quarter of it ours: 25 a quote.
    sizes = {2.0: "1000", 3.0: "1000", 4.0: "100", 5.0: "100", 6.0: "100", 7.0: "100"}
    record = lent(
        BorrowAvailability(1.0, D("100"), RATE),
        BorrowAvailability(3.0, D("50"), RATE, buy_in_at=3.0),
    )
    result, _ = run(record, sizes=sizes, fill_policy=LiquidityCappedFill(D("0.25")))
    # 50 to cover: 25 at 4.0, the remainder cancelled as a market order's is,
    # then the 25 still beyond what is lent bought in at 5.0 -- never two at once.
    assert fills(result) == [(2.0, D("-100")), (4.0, D("25")), (5.0, D("25"))]
    assert [(b.timestamp, b.excess) for b in buy_ins(result)] == [(3.0, D("50")), (4.0, D("25"))]


def test_with_nothing_shown_the_buy_in_fills_nothing_and_is_placed_again() -> None:
    sizes = {2.0: "1000", 3.0: "0", 4.0: "0", 5.0: "0", 6.0: "1000", 7.0: "1000"}
    record = lent(
        BorrowAvailability(1.0, D("100"), RATE),
        BorrowAvailability(3.0, D("0"), RATE, buy_in_at=3.0),
    )
    result, _ = run(record, sizes=sizes, fill_policy=LiquidityCappedFill(D("1")))
    # Nothing is invented: each empty quote fills nothing, the order's
    # remainder is cancelled, and the buy-in is placed again until one fills.
    assert fills(result) == [(2.0, D("-100")), (6.0, D("100"))]
    assert all(b.excess == D("100") for b in buy_ins(result))
    assert [b.timestamp for b in buy_ins(result)] == [3.0, 4.0, 5.0]


def test_a_buy_in_rests_through_a_halt_and_fills_at_the_first_event_after_it() -> None:
    halt = TradingControls(halts=(TradingHalt(3.5, 5.5, asset_id=margin.PLAIN.asset_id),))
    record = lent(
        BorrowAvailability(1.0, D("100"), RATE),
        BorrowAvailability(3.0, D("0"), RATE, buy_in_at=3.0),
    )
    mids = margin.series("100", "100", "104", "106", "108", "110")
    result, _ = run(record, mids=mids, controls=halt)
    # Placed at 3.0; the 4.0 and 5.0 quotes are in the halt; it trades at 6.0.
    assert fills(result) == [(2.0, D("-100")), (6.0, D("100"))]
    assert [b.timestamp for b in buy_ins(result)] == [3.0]


def test_a_strategys_resting_limit_buy_does_not_defer_the_buy_in() -> None:
    far = Intent(
        "S",
        margin.PLAIN.asset_id,
        D("100"),
        timestamp=2.5,
        terms=OrderTerms.limit(D("1"), TimeInForce.GTC),
    )
    mids = {2.0: "100", 2.5: "100", 3.0: "100", 4.0: "100", 5.0: "100"}
    result, _ = run(RECALLED, {**SHORT, (2.5, margin.PLAIN.asset_id): far}, mids=mids)
    # The bid at 1 waits for a price; the lender's deadline does not.
    assert [b.timestamp for b in buy_ins(result)] == [4.0]
    assert fills(result)[:2] == [(2.0, D("-100")), (5.0, D("100"))]


def test_a_strategy_that_covers_before_the_deadline_is_never_bought_in() -> None:
    plan = {**SHORT, (3.0, margin.PLAIN.asset_id): D("100")}
    result, _ = run(RECALLED, plan)
    assert fills(result) == [(2.0, D("-100")), (3.0, D("100"))]
    assert buy_ins(result) == []


# --------------------------------------------------------------------------- #
# What it costs and what the strategy sees
# --------------------------------------------------------------------------- #


def test_the_fee_is_charged_on_the_short_still_held_after_a_partial_buy_in() -> None:
    record = lent(
        BorrowAvailability(1.0, D("100"), RATE),
        BorrowAvailability(3.0, D("40"), RATE, buy_in_at=3.0),
    )
    fee = ScheduledBorrowFee(date(2024, 1, 2), date(2024, 1, 3), 6.0)
    result, _ = run(record, schedule=RunSchedule.of(borrow_fees=[fee]))
    (charged,) = [
        t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.FEE
    ]
    # 40 still short at 100, 10% a year, one day of 360.
    assert charged.quantity == -(D("40") * D("100") * RATE / D("360")).quantize(D("0.01"))


def test_a_strategy_sees_the_recall_from_its_notice_and_never_before() -> None:
    reader = Reader(SHORT)
    run(RECALLED, strategy=reader)
    first, recalled = RECALLED.borrow.entries
    by_instant = {at: (offered, entry) for at, offered, entry in reader.seen}
    assert by_instant[2.0] == ((D("100"), RATE), first)
    assert by_instant[3.0] == ((D("0"), RATE), recalled)
    assert by_instant[3.0][1].buy_in_at == 4.0


# --------------------------------------------------------------------------- #
# Replay, persistence, declarations
# --------------------------------------------------------------------------- #


def test_a_buy_in_replays_and_resumes_from_every_pause_to_one_result() -> None:
    sizes = dict.fromkeys(margin.series(*(["100"] * 6)), "100")
    sizes[2.0] = sizes[3.0] = "1000"
    record = lent(
        BorrowAvailability(1.0, D("100"), RATE),
        BorrowAvailability(3.0, D("50"), RATE, buy_in_at=3.0),
    )
    policy = LiquidityCappedFill(D("0.25"))
    cfg, dataset = config(record, policy, None), data(record, None, sizes)
    whole = ReplayBacktest.run(
        cfg, dataset, running_strategy_state("S", Reader(SHORT)), context_factory
    )
    reference = digest_run(whole.backtest).result_id
    expected = buy_ins(whole.backtest)
    assert len(expected) == 2
    for boundary in range(1, len(dataset.records)):
        paused = ReplayBacktest.run(
            cfg,
            dataset,
            running_strategy_state("S", Reader(SHORT)),
            context_factory,
            pause_when=margin._at(boundary),
        )
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": Reader(SHORT)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, dataset)
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary
        assert [(b.timestamp, b.excess) for b in buy_ins(resumed)] == [
            (b.timestamp, b.excess) for b in expected
        ]


def test_a_recall_round_trips_and_terms_without_one_keep_their_form() -> None:
    assert borrow_from_primitives(borrow_to_primitives(RECALLED.borrow)) == RECALLED.borrow
    plain = BorrowTerms((BorrowAvailability(1.0, D("100"), RATE),), DayCount.ACT_360)
    written = borrow_to_primitives(plain)
    assert written is not None and "buy_in_at" not in written["entries"][0]
    registry = registry_of(RECALLED, margin.OTHER)
    restored = restore_registry(
        registry_from_primitives(deserialize(serialize(capture_registry(registry))))
    )
    assert restored.record_for(RECALLED.asset_id) == RECALLED
    assert restored.recallable is not None
    assert list(restored.recallable) == [RECALLED.asset_id]


def test_a_recall_that_never_binds_leaves_the_runs_identity_as_none_would() -> None:
    late = lent(
        BorrowAvailability(1.0, D("100"), RATE),
        BorrowAvailability(50.0, D("0"), RATE, buy_in_at=60.0),
    )
    plain = lent(BorrowAvailability(1.0, D("100"), RATE))
    recalled, _ = run(late)
    unrecalled, _ = run(plain)
    assert buy_ins(recalled) == []
    assert digest_run(recalled).result_id == digest_run(unrecalled).result_id


@pytest.mark.parametrize(
    "build",
    [
        lambda: BorrowAvailability(3.0, D("0"), RATE, buy_in_at=2.0),
        lambda: BorrowAvailability(3.0, None, RATE, buy_in_at=4.0),
        lambda: BorrowAvailability(3.0, D("0"), RATE, buy_in_at=True),
        lambda: BorrowAvailability(3.0, D("0"), RATE, buy_in_at=float("nan")),
    ],
)
def test_a_recall_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(InstrumentInputError):
        build()
