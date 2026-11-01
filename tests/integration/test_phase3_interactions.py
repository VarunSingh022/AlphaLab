"""The phase 3 capabilities against each other (FUT-022 to FUT-029, ACC-016, ACC-017).

Each test drives several of them through one run, on the canonical path:
a recall bought in through a halt that reopens by auction while the short
accrues its day's fee; an election's cash paid later while the balance accrues
interest day by day on the settled basis. Each is replayed and resumed from
every pause to one result, each scheduled item applied once.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Any

from alphalab.backtesting import (
    DailyAccrual,
    DailyBorrowFee,
    DailyInterest,
    ElectiveExchange,
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
    ScheduledBorrowFee,
    ScheduledInterest,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.conventions.daycount import DayCount
from alphalab.instrument.borrow import BorrowAvailability
from alphalab.instrument.controls import TradingControls, TradingHalt
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.corporate_actions import HoldingExchange
from alphalab.portfolio.financing import BalanceBasis, FinancingTier
from alphalab.portfolio.types import TransactionType
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration import test_borrow_recall as recall
from tests.integration import test_elective_exchanges as elective
from tests.integration import test_holding_exchanges as exchanges
from tests.integration import test_margin_and_liquidation as margin
from tests.integration.harness import context_factory, running_strategy_state

D = Decimal
FIRST_DAY = date(1970, 1, 1)


def resumed_everywhere(cfg: Any, dataset: Any, fresh: Any, objects: Any, schedule: Any) -> str:
    """The whole replay's result_id, asserting every pause resumes to it."""

    whole = ReplayBacktest.run(cfg, dataset, fresh(), context_factory, schedule=schedule)
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(dataset.records)):
        paused = ReplayBacktest.run(
            cfg, dataset, fresh(), context_factory, schedule=schedule,
            pause_when=margin._at(boundary),
        )  # fmt: skip
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        restored = restore_replay(replay_from_primitives(payload), objects(), dataset, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary
    return reference


def test_a_recall_bought_in_through_a_halt_reopening_by_auction_accrues_its_fee() -> None:
    record = recall.lent(
        BorrowAvailability(1.0, D("100"), recall.RATE),
        BorrowAvailability(3.0, D("0"), recall.RATE, buy_in_at=3.0),
    )
    halt = TradingControls(halts=(TradingHalt(3.5, 5.5, asset_id=record.asset_id, auction=True),))
    # Each day closes at four seconds past midnight UTC on 1 January 1970: the
    # cutoff falls at 4.0, while the short is still held, bought in later.
    accrual = DailyAccrual("UTC", time(0, 0, 4))
    fee = DailyBorrowFee(ScheduledBorrowFee(FIRST_DAY, date(1970, 1, 2), 7.0), accrual)
    schedule = RunSchedule.of(borrow_fees=[fee])
    mids = margin.series("100", "100", "104", "106", "108", "110")
    result, _ = recall.run(record, mids=mids, controls=halt, schedule=schedule)
    # The buy-in, placed at 3.0, rests through the halt (4.0 and 5.0) and
    # crosses at the reopening print, 6.0's 108.
    assert recall.fills(result) == [(2.0, D("-100")), (6.0, D("100"))]
    assert [b.timestamp for b in recall.buy_ins(result)] == [3.0]
    (charged,) = [
        t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.FEE
    ]
    # The day's fee on 100 short at the 4.0 mark of 104, 10% a year, ACT/360.
    assert charged.quantity == -(D("100") * D("104") * recall.RATE / 360).quantize(D("0.01"))

    cfg = recall.config(record, None, halt)
    dataset = recall.data(record, mids, None)
    resumed_everywhere(
        cfg,
        dataset,
        lambda: running_strategy_state("S", recall.Reader(recall.SHORT)),
        lambda: RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": recall.Reader(recall.SHORT)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        ),
        schedule,
    )


def test_an_elected_cash_payment_is_left_out_of_settled_interest_until_it_is_paid() -> None:
    cash_merger = HoldingExchange(exchanges.OLD.asset_id, cash_per_unit=D("120"))
    action = ElectiveExchange(
        (elective.STOCK, cash_merger), 0, 2.2, 2.8, 3.0, elective.REF, payment_at=3.05
    )
    # Cutoffs every day at 3.02 seconds past midnight: the day accrues on the
    # settled balance then, before the merger's cash is paid at 3.05.
    accrual = DailyAccrual("UTC", time(0, 0, 3, 20000))
    interest = DailyInterest(
        ScheduledInterest(
            "USD",
            FIRST_DAY,
            date(1970, 1, 2),
            3.1,
            DayCount.ACT_360,
            (FinancingTier(None, D("0.05")),),
            D("0.01"),
            BalanceBasis.SETTLED,
        ),
        accrual,
    )
    schedule = RunSchedule.of(exchanges=[action], interest=[interest])
    mids = {
        exchanges.OLD.asset_id: dict.fromkeys(elective.STEPS, "100"),
        exchanges.NEW.asset_id: {3.1: "200"},
    }
    result = exchanges.run(
        elective.STOCK,
        exchanges.OLD,
        exchanges.NEW,
        mids=mids,
        strategies=elective.strategies(("S", elective.Elector(exchanges.BUY, {2.5: 1}))),
        schedule=schedule,
    )
    assert elective.took_cash(result)
    (paid,) = [
        t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.INTEREST
    ]
    # Settled at the cutoff: the 1,000,000 less the 10,000 paid for OLDCO; the
    # 12,000 entitlement is not yet paid, and earns nothing.
    settled = exchanges.START - D("10000")
    assert paid.quantity == (settled * D("0.01") / 360).quantize(D("0.01"))

    cfg = exchanges.config(exchanges.OLD, exchanges.NEW)
    dataset = margin.prices(mids)
    reference = resumed_everywhere(
        cfg,
        dataset,
        lambda: elective.strategies(("S", elective.Elector(exchanges.BUY, {2.5: 1}))),
        lambda: RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": elective.Elector(exchanges.BUY, {2.5: 1})},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        ),
        schedule,
    )
    assert reference
