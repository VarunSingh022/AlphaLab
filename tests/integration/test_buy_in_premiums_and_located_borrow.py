"""A buy-in's premium, and borrow located per strategy (phase 4, ledger FUT-035).

A borrow entry may declare what its lender charges on a buy-in -- a premium on
the bought-in value, booked with each fill of the cover, and a fixed fee with
its first -- and may locate its availability per strategy: each strategy may
be short its own allocation, and beyond it draws on what the lender shares,
first come, a unit never claimed twice. A recall then buys each strategy in
for its own excess, not in proportion to every short.

ACME quotes 100 from 2.0, one quote a second (the helpers are
tests/integration/test_borrow_recall.py's).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import BacktestEngine, PausedReplay, ReplayBacktest, RunSchedule
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.execution.policy import LiquidityCappedFill
from alphalab.instrument.borrow import (
    BorrowAvailability,
    borrow_from_primitives,
    borrow_to_primitives,
)
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.events import CashFlowBooked
from alphalab.portfolio.types import TransactionType
from alphalab.runtime import borrow_claims
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from tests.integration import test_borrow_recall as recall
from tests.integration import test_elective_exchanges as elective
from tests.integration import test_margin_and_liquidation as margin
from tests.integration.harness import context_factory

D = Decimal
RATE = recall.RATE
ACME = margin.PLAIN.asset_id


def fees(result: Any) -> list[tuple[float, Decimal, str]]:
    return [
        (t.timestamp, t.quantity, t.metadata.get("reference", ""))
        for t in result.state.portfolio.ledger.transactions
        if t.type is TransactionType.FEE
    ]


# --------------------------------------------------------------------------- #
# The lender's charge on a buy-in
# --------------------------------------------------------------------------- #

CHARGED = recall.lent(
    BorrowAvailability(1.0, D("100"), RATE),
    BorrowAvailability(
        3.0, D("0"), RATE, buy_in_at=4.0, buy_in_premium=D("0.02"), buy_in_fee=D("50")
    ),
)
SQUEEZE = margin.series("100", "101", "102", "103", "104", "105")


def test_a_buy_in_premium_and_fee_are_booked_with_the_cover() -> None:
    result, _ = recall.run(CHARGED, mids=SQUEEZE)
    # Bought in at 5.0 at 103: 2% of 10,300 and the 50 fee, as one charge to S.
    assert recall.fills(result) == [(2.0, D("-100")), (5.0, D("100"))]
    ((at, amount, _),) = fees(result)
    assert (at, amount) == (5.0, D("-256.00"))
    # Attributed to the strategy bought in, on the cash flow's record.
    (booked,) = [e for e in result.state.portfolio.events if isinstance(e, CashFlowBooked)]
    assert booked.reference == "buy-in:S"


def test_each_round_of_a_thin_buy_in_pays_on_its_own_fill_and_its_own_fee() -> None:
    sizes = {**dict.fromkeys(SQUEEZE, "240"), 2.0: "1000"}
    result, _ = recall.run(
        CHARGED, mids=SQUEEZE, sizes=sizes, fill_policy=LiquidityCappedFill(D("0.25"))
    )
    # Short 100 at 2.0; 60 of the 240 shown at 5.0 cover part, at 103, and the
    # 40 left are bought in again and cover at 6.0, at 104: each round pays
    # 2% of what it filled and its own 50.
    assert recall.fills(result)[1:] == [(5.0, D("60")), (6.0, D("40"))]
    assert [(at, amount) for at, amount, _ in fees(result)] == [
        (5.0, -(D("60") * D("103") * D("0.02") + D("50"))),
        (6.0, -(D("40") * D("104") * D("0.02") + D("50"))),
    ]


def test_terms_declaring_no_charge_book_none() -> None:
    result, _ = recall.run(recall.RECALLED, mids=SQUEEZE)
    assert fees(result) == []


# --------------------------------------------------------------------------- #
# Borrow located per strategy
# --------------------------------------------------------------------------- #


class Short(margin.Trader):
    """The margin trader, for its own strategy id."""

    def __init__(self, sid: str, plan: dict[tuple[float, str], Decimal]) -> None:
        super().__init__(plan)
        self.sid = sid

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        wanted = self._plan.get((event.quote.timestamp, event.quote.asset_id))
        if wanted is None:
            return ()
        return (Intent(self.sid, event.quote.asset_id, wanted, timestamp=event.quote.timestamp),)


def two(record: Any, a: dict[float, str], b: dict[float, str], **kw: Any) -> tuple[Any, Any]:
    plans = {
        sid: {(at, ACME): D(q) for at, q in plan.items()} for sid, plan in (("A", a), ("B", b))
    }
    traders = {sid: Short(sid, plan) for sid, plan in plans.items()}
    runtime = elective.strategies(*traders.items())  # type: ignore[arg-type]
    cfg = recall.config(record, kw.pop("fill_policy", None), None)
    result = BacktestEngine.run(
        cfg,
        recall.data(record, kw.pop("mids", None), None),
        runtime,
        context_factory,
        schedule=RunSchedule.of(),
    )
    return result, traders


LOCATED = recall.lent(
    BorrowAvailability(1.0, D("120"), RATE, allocations=(("A", D("60")), ("B", D("20")))),
)


def test_a_strategy_draws_beyond_its_own_only_on_what_is_shared() -> None:
    # A is short 20 of its 60; B short 60: 20 its own, 40 of the 40 shared.
    # B's next 10 would draw 50 on the 40 shared: A's unused 40 is A's.
    result, traders = two(LOCATED, {2.0: "-20"}, {2.0: "-60", 3.0: "-10"})
    # The 20 and the 60 net into one order of 80 at 2.0; nothing more trades.
    assert recall.fills(result) == [(2.0, D("-80"))]
    (refusal,) = traders["B"].rejections
    assert "located" in refusal and "shares between strategies" in refusal


def test_the_shared_borrow_is_first_come() -> None:
    # A takes 30 of the shared 40 beyond its 60 at 2.0; B's 30 beyond its 20 at
    # 3.0 would draw 60 on 40, and is refused; A keeps what it drew.
    result, traders = two(LOCATED, {2.0: "-90"}, {3.0: "-50"})
    assert recall.fills(result) == [(2.0, D("-90"))]
    assert traders["B"].rejections and not traders["A"].rejections


RECALLED_EACH = recall.lent(
    BorrowAvailability(1.0, D("100"), RATE),
    BorrowAvailability(
        3.0, D("40"), RATE, buy_in_at=4.0, allocations=(("A", D("20")), ("B", D("20")))
    ),
)


def test_a_recall_buys_each_strategy_in_for_its_own_excess() -> None:
    result, _ = two(RECALLED_EACH, {2.0: "-60"}, {2.0: "-20"})
    # From 4.0 each has 20 located and none is shared: A, short 60, is 40 over;
    # B, short 20, is within its own. A alone is bought in, for its 40.
    bought = [
        t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.BUY
    ]
    assert [(t.timestamp, t.quantity) for t in bought] == [(5.0, D("40"))]
    held = {c.strategy_id: c.short for c in borrow_claims(result.state, ACME, 6.0)}
    assert held == {"A": D("20"), "B": D("20")}


def test_a_recall_of_the_books_borrow_buys_in_every_short_in_proportion() -> None:
    book = recall.lent(
        BorrowAvailability(1.0, D("100"), RATE),
        BorrowAvailability(3.0, D("40"), RATE, buy_in_at=4.0),
    )
    result, _ = two(book, {2.0: "-60"}, {2.0: "-20"})
    held = {c.strategy_id: c.short for c in borrow_claims(result.state, ACME, 6.0)}
    # 40 over the book's 40, in proportion to the shorts: 30 of A's, 10 of B's.
    assert held == {"A": D("30"), "B": D("10")}


def test_claims_attribute_the_borrow_and_its_fee_to_each_strategy() -> None:
    result, _ = two(LOCATED, {2.0: "-20"}, {2.0: "-60"})
    claims = borrow_claims(result.state, ACME, 3.0)
    assert [(c.strategy_id, c.short, c.located, c.drawn) for c in claims] == [
        ("A", D("20"), D("60"), D("0")),
        ("B", D("60"), D("20"), D("40")),
    ]
    assert sum(c.share for c in claims) == 1
    assert [c.share for c in claims] == [D("0.25"), D("0.75")]


# --------------------------------------------------------------------------- #
# Declarations, persistence and replay
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "build",
    [
        lambda: BorrowAvailability(1.0, D("10"), RATE, allocations=(("A", D("6")), ("B", D("6")))),
        lambda: BorrowAvailability(1.0, D("10"), RATE, allocations=(("A", D("1")), ("A", D("1")))),
        lambda: BorrowAvailability(1.0, None, RATE, allocations=(("A", D("1")),)),
        lambda: BorrowAvailability(1.0, D("10"), RATE, allocations=(("A", D("-1")),)),
        lambda: BorrowAvailability(1.0, D("10"), RATE, buy_in_premium=D("-0.01")),
        lambda: BorrowAvailability(1.0, D("10"), RATE, buy_in_fee=D("NaN")),
    ],
)
def test_a_borrow_declaration_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(InstrumentInputError):
        build()


def test_charges_and_locates_round_trip_and_plain_terms_keep_their_form() -> None:
    for terms in (CHARGED.borrow, LOCATED.borrow, RECALLED_EACH.borrow):
        assert borrow_from_primitives(borrow_to_primitives(terms)) == terms
    plain = borrow_to_primitives(recall.RECALLED.borrow)
    assert plain is not None
    assert not any(
        {"buy_in_premium", "buy_in_fee", "allocations"} & set(e) for e in plain["entries"]
    )


def test_a_charged_buy_in_resumes_to_one_result_from_every_pause() -> None:
    cfg = recall.config(CHARGED, None, None)
    dataset = recall.data(CHARGED, SQUEEZE, None)

    def fresh() -> Any:
        return elective.strategies(("S", recall.Reader(recall.SHORT)))  # type: ignore[arg-type]

    whole = ReplayBacktest.run(cfg, dataset, fresh(), context_factory, schedule=RunSchedule.of())
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(dataset.records)):
        paused = ReplayBacktest.run(
            cfg, dataset, fresh(), context_factory, schedule=RunSchedule.of(),
            pause_when=margin._at(boundary),
        )  # fmt: skip
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": recall.Reader(recall.SHORT)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(
            replay_from_primitives(payload), objects, dataset, RunSchedule.of()
        )
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary
    assert len(fees(whole.backtest)) == 1


def test_a_venue_filling_a_buy_in_in_parts_charges_its_fee_once() -> None:
    from alphalab.execution.fill import FillStatus
    from alphalab.execution.report import ExecutionReport
    from alphalab.market.quote import Quote
    from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionRouting
    from tests.integration.harness import running_strategy_state

    base = recall.config(CHARGED, None, None).pipeline
    state = ExecutionPipeline.initialize(
        replace(base, routing=ExecutionRouting.EXTERNAL),
        running_strategy_state("S", recall.Reader(recall.SHORT)),
        1.0,
    )

    def report(order: Any, quantity: str, at: float, status: FillStatus) -> ExecutionReport:
        return ExecutionReport(
            execution_id=f"V-{at}-{quantity}", order_id=str(order.order_id.value),
            asset_id=ACME, strategy_id="", timestamp=at, fill_price=D(SQUEEZE[at]),
            fill_quantity=D(quantity), commission=D("0"), slippage=D("0"),
            liquidity_flag="TAKER", venue="V", currency="USD", status=status,
        )  # fmt: skip

    for at in (2.0, 3.0, 4.0):
        quoted = Quote(ACME, at, D(SQUEEZE[at]), D(SQUEEZE[at]), D("1000"), D("1000"), "X", "USD")
        state = ExecutionPipeline.process_quote(state, quoted, context_factory).state
        if at == 2.0:
            (short,) = state.oms.orders.open_orders()
            state, _, _ = ExecutionPipeline.apply_execution_report(
                state, short, report(short, "100", 2.0, FillStatus.FULL_FILL)
            )
    # At 4.0 the deadline has passed: the cover is the venue's to work.
    (cover,) = state.oms.orders.open_orders()
    state, _, _ = ExecutionPipeline.apply_execution_report(
        state, cover, report(cover, "60", 4.0, FillStatus.PARTIAL_FILL)
    )
    cover = state.oms.orders.find(cover.order_id)
    state, _, _ = ExecutionPipeline.apply_execution_report(
        state, cover, report(cover, "40", 4.0, FillStatus.FULL_FILL)
    )
    charged = [
        t.quantity for t in state.portfolio.ledger.transactions if t.type is TransactionType.FEE
    ]
    # 2% of each part at 102, and the 50 with the first part alone.
    assert charged == [
        -(D("60") * D("102") * D("0.02") + D("50")),
        -(D("40") * D("102") * D("0.02")),
    ]


def test_a_strategys_resting_short_counts_against_what_it_draws() -> None:
    from alphalab.common.order_terms import OrderTerms, TimeInForce

    class Resting(Short):
        def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
            if event.quote.timestamp == 2.0:
                offer = OrderTerms.limit(D("110"), TimeInForce.GTC)
                return (Intent(self.sid, ACME, D("-60"), timestamp=2.0, terms=offer),)
            return super().on_quote(context, event)

    traders = {"A": Short("A", {}), "B": Resting("B", {(3.0, ACME): D("-10")})}
    result = BacktestEngine.run(
        recall.config(LOCATED, None, None),
        recall.data(LOCATED, None, None),
        elective.strategies(*traders.items()),  # type: ignore[arg-type]
        context_factory,
        schedule=RunSchedule.of(),
    )
    # B's resting offer of 60 is short 60 if it fills: 20 its own and 40 shared.
    # A market sale of 10 more would draw 50 on 40, though nothing is filled.
    assert recall.fills(result) == []
    assert traders["B"].rejections
