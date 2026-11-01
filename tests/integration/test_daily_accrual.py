"""Interest and borrow fees accrued day by day, posted once a period (phase 3, ledger FUT-023).

A :class:`DailyInterest` accrues each calendar day of its period at a declared
cutoff -- an IANA zone and a time of day -- on the balance the day leaves, at
the rates in effect that day; a :class:`DailyBorrowFee` accrues each day's fee
on each short at the run's mark then. Each day is an exact ``ACCRUAL`` in the
ledger moving no cash; the period posts the sum once, rounded once.

ACME quotes at 10:00 UTC each January day of 2024; cutoffs are 17:00 UTC.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time
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
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.backtesting.schedule import (
    DailyAccrual,
    DailyBorrowFee,
    DailyInterest,
    Funding,
    RateChange,
    ScheduledBorrowFee,
    ScheduledInterest,
    ScheduleValidationError,
)
from alphalab.conventions.daycount import DayCount
from alphalab.instrument.borrow import BorrowAvailability, BorrowTerms
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.persistence.exceptions import SerializationError
from alphalab.portfolio.financing import FinancingTier
from alphalab.portfolio.fx import FxRate
from alphalab.portfolio.types import TransactionType
from alphalab.runtime.retention import RetentionPolicy
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration import test_financing_and_borrow as financing
from tests.integration import test_margin_and_liquidation as margin
from tests.integration.harness import context_factory, running_strategy_state

D = Decimal
UTC_CLOSE = DailyAccrual("UTC", time(17, 0))
TIERS = financing.TIERS  # 1,000 at 5%, the rest at 8%
BOUGHT = financing.BOUGHT  # bought on margin: the cash it lacks is a debit


def jan(day: int, hour: int = 10) -> float:
    return datetime(2024, 1, day, hour, tzinfo=UTC).timestamp()


def daily(days: range | None = None, mid: str = "100") -> dict[float, str]:
    return {jan(d): mid for d in (days or range(1, 10))}


def interest(
    start: int = 1, end: int = 8, *, credit: str | None = None, **kw: Any
) -> ScheduledInterest:
    return ScheduledInterest(
        "USD",
        date(2024, 1, start),
        date(2024, 1, end),
        jan(end, 0),
        DayCount.ACT_360,
        TIERS,
        None if credit is None else D(credit),
        **kw,
    )


def run(
    plan: dict[tuple[float, str], Decimal],
    schedule: RunSchedule,
    record: Any = BOUGHT,
    mids: dict[float, str] | None = None,
) -> BacktestResult:
    return margin.run({record.asset_id: mids or daily()}, plan, record, schedule=schedule)


def posted(result: BacktestResult, kind: TransactionType) -> list[Decimal]:
    return [t.quantity for t in result.state.portfolio.ledger.transactions if t.type is kind]


def accruals(result: BacktestResult) -> list[Any]:
    return [
        t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.ACCRUAL
    ]


#: 150 bought at 100 on the 2nd against 10,000 of cash -- a 5,000 debit -- and
#: 100 sold on the 5th, leaving a 5,000 credit.
TRADES = {(jan(2), BOUGHT.asset_id): D("150"), (jan(5), BOUGHT.asset_id): D("-100")}


def credit(amount: str) -> Decimal:
    return D(amount) * D("0.01") / D("360")


def per_day_debit(debit: Decimal) -> Decimal:
    return (D("1000") * D("0.05") + (debit - D("1000")) * D("0.08")) / D("360")


# --------------------------------------------------------------------------- #
# The balance each day leaves
# --------------------------------------------------------------------------- #


def test_each_day_accrues_on_its_own_balance_and_the_period_posts_once() -> None:
    period = DailyInterest(interest(credit="0.01"), UTC_CLOSE)
    result = run(TRADES, RunSchedule.of(interest=[period]))
    # The 1st: a 10,000 credit at 1%. The 2nd to the 4th: a 5,000 debit. The
    # 5th to the 7th: a 5,000 credit. Each day 1/360 of a year.
    exact = credit("10000") - 3 * per_day_debit(D("5000")) + 3 * credit("5000")
    assert posted(result, TransactionType.INTEREST) == [exact.quantize(D("0.01"))]
    days = [t for t in accruals(result) if not t.metadata.get("accrual_closed")]
    assert [round((t.timestamp - jan(1, 17)) / 86400) for t in days] == list(range(7))
    assert sum((t.quantity for t in days), D("0")) == exact


def test_a_whole_period_charge_on_the_posting_balance_would_differ() -> None:
    # The same period, charged on the balance at the posting -- a 5,000
    # credit -- earns where the book paid: what FUT-018's instant basis gives.
    instant = interest(credit="0.01")
    daily_result = run(TRADES, RunSchedule.of(interest=[DailyInterest(instant, UTC_CLOSE)]))
    instant_result = run(TRADES, RunSchedule.of(interest=[instant]))
    assert posted(daily_result, TransactionType.INTEREST)[0] < 0
    assert posted(instant_result, TransactionType.INTEREST)[0] > 0


def test_a_rate_change_applies_from_its_day() -> None:
    dearer = (FinancingTier(None, D("0.12")),)
    period = DailyInterest(interest(), UTC_CLOSE, (RateChange(date(2024, 1, 4), dearer),))
    plan = {(jan(2), BOUGHT.asset_id): D("150")}
    result = run(plan, RunSchedule.of(interest=[period]))
    # The 2nd and 3rd at the tiers, the 4th to the 7th at 12% on the 5,000.
    exact = 2 * per_day_debit(D("5000")) + 4 * D("5000") * D("0.12") / D("360")
    assert posted(result, TransactionType.INTEREST) == [-exact.quantize(D("0.01"))]


def test_a_leap_day_is_a_day_and_each_counts_by_its_day_count() -> None:
    feb = {datetime(2024, 2, d, 10, tzinfo=UTC).timestamp(): "100" for d in (27, 28, 29)}
    feb[datetime(2024, 3, 1, 10, tzinfo=UTC).timestamp()] = "100"
    plan = {(min(feb), BOUGHT.asset_id): D("150")}
    item = ScheduledInterest(
        "USD",
        date(2024, 2, 28),
        date(2024, 3, 1),
        datetime(2024, 3, 1, 0, tzinfo=UTC).timestamp(),
        DayCount.ACT_365_FIXED,
        TIERS,
    )
    result = run(plan, RunSchedule.of(interest=[DailyInterest(item, UTC_CLOSE)]), mids=feb)
    # The 28th and the 29th, each 1/365.
    per_day = D("1000") * D("0.05") + D("4000") * D("0.08")
    assert posted(result, TransactionType.INTEREST) == [-(2 * per_day / 365).quantize(D("0.01"))]


def test_a_thirty_360_day_counts_what_the_convention_counts_it_as() -> None:
    # 30/360 counts the 30th to the 31st of January as no day and the 31st to
    # the 1st of February as one: two calendar days, one day's interest.
    mids = {jan(29): "100", jan(30): "100", jan(31): "100"}
    mids[datetime(2024, 2, 1, 10, tzinfo=UTC).timestamp()] = "100"
    item = ScheduledInterest(
        "USD",
        date(2024, 1, 30),
        date(2024, 2, 1),
        datetime(2024, 2, 1, 0, tzinfo=UTC).timestamp(),
        DayCount.THIRTY_360_US,
        TIERS,
    )
    plan = {(jan(29), BOUGHT.asset_id): D("150")}
    result = run(plan, RunSchedule.of(interest=[DailyInterest(item, UTC_CLOSE)]), mids=mids)
    assert posted(result, TransactionType.INTEREST) == [
        -per_day_debit(D("5000")).quantize(D("0.01"))
    ]


def test_an_accrual_that_rounds_to_nothing_closes_and_posts_nothing() -> None:
    tiny = (FinancingTier(None, D("0.0000001")),)
    item = replace(interest(), debit_tiers=tiny)
    result = run(TRADES, RunSchedule.of(interest=[DailyInterest(item, UTC_CLOSE)]))
    assert posted(result, TransactionType.INTEREST) == []
    closing = [t for t in accruals(result) if t.metadata.get("accrual_closed")]
    assert len(closing) == 1
    assert sum((t.quantity for t in accruals(result)), D("0")) == 0


def test_each_currency_accrues_on_its_own_balance() -> None:
    eur = ScheduledInterest(
        "EUR", date(2024, 1, 1), date(2024, 1, 8), jan(8, 0), DayCount.ACT_360, TIERS, D("0.02")
    )
    schedule = RunSchedule.of(
        interest=[DailyInterest(interest(), UTC_CLOSE), DailyInterest(eur, UTC_CLOSE)],
        fundings=[Funding(D("3600"), "EUR", jan(3, 12))],
        rates=[
            FxRate("EUR", "USD", D("1.25"), jan(1, 0), "t"),
            FxRate("USD", "EUR", D("0.8"), jan(1, 0), "t"),
        ],
    )
    cfg = margin.config(BOUGHT)
    pipeline = replace(cfg.pipeline, also_settles=frozenset({"EUR"}))
    cfg = replace(cfg, pipeline=replace(pipeline, budget=pipeline.budget.in_currency("USD")))
    result = BacktestEngine.run(
        cfg,
        margin.prices({BOUGHT.asset_id: daily()}),
        running_strategy_state("S", margin.Trader({(jan(2), BOUGHT.asset_id): D("150")})),
        context_factory,
        schedule=schedule,
    )
    flows = {
        t.currency: t.quantity
        for t in result.state.portfolio.ledger.transactions
        if t.type is TransactionType.INTEREST
    }
    # EUR: 3,600 at 2% from the 3rd's cutoff -- five days, the 3rd to the 7th.
    assert flows["EUR"] == (D("3600") * D("0.02") * 5 / D("360")).quantize(D("0.01"))
    assert flows["USD"] == -(6 * per_day_debit(D("5000"))).quantize(D("0.01"))


# --------------------------------------------------------------------------- #
# Borrow fees, day by day
# --------------------------------------------------------------------------- #


LENT = replace(
    margin.PLAIN,
    borrow=BorrowTerms(
        (
            BorrowAvailability(jan(1, 0), None, D("0.10")),
            BorrowAvailability(jan(5, 0), None, D("0.20")),
        ),
        DayCount.ACT_360,
    ),
)


def test_a_short_pays_for_the_days_it_was_held_at_each_days_mark_and_rate() -> None:
    fee = ScheduledBorrowFee(date(2024, 1, 1), date(2024, 1, 8), jan(8, 0))
    mids = {**daily(), jan(4): "110", jan(6): "90"}
    # Shorted on the 3rd, covered on the 6th: held at the cutoffs of the 3rd,
    # 4th and 5th.
    plan = {(jan(3), LENT.asset_id): D("-100"), (jan(6), LENT.asset_id): D("100")}
    result = run(plan, RunSchedule.of(borrow_fees=[DailyBorrowFee(fee, UTC_CLOSE)]), LENT, mids)
    days = D("100") * D("0.10") + D("110") * D("0.10") + D("100") * D("0.20")
    exact = days * 100 / D("360")
    assert posted(result, TransactionType.FEE) == [-exact.quantize(D("0.01"))]
    # The instant basis would charge nothing: nothing is short at the posting.
    instant = run(plan, RunSchedule.of(borrow_fees=[fee]), LENT, mids)
    assert posted(instant, TransactionType.FEE) == []


# --------------------------------------------------------------------------- #
# Replay, retention, declarations
# --------------------------------------------------------------------------- #


def test_daily_accrual_replays_and_resumes_from_every_pause_once() -> None:
    period = DailyInterest(interest(credit="0.01"), UTC_CLOSE)
    schedule = RunSchedule.of(interest=[period])
    cfg = margin.config(BOUGHT)
    data = margin.prices({BOUGHT.asset_id: daily()})
    whole = ReplayBacktest.run(
        cfg,
        data,
        running_strategy_state("S", margin.Trader(TRADES)),
        context_factory,
        schedule=schedule,
    )
    reference = digest_run(whole.backtest).result_id
    expected = posted(whole.backtest, TransactionType.INTEREST)
    assert len(expected) == 1
    for boundary in range(1, len(data.records)):
        paused = ReplayBacktest.run(
            cfg,
            data,
            running_strategy_state("S", margin.Trader(TRADES)),
            context_factory,
            schedule=schedule,
            pause_when=margin._at(boundary),
        )
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": margin.Trader(TRADES)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, data, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary
        assert posted(resumed, TransactionType.INTEREST) == expected


def test_a_results_bound_keeps_every_open_accrual_until_its_period_posts() -> None:
    period = DailyInterest(interest(credit="0.01"), UTC_CLOSE)
    schedule = RunSchedule.of(interest=[period])
    unbounded = run(TRADES, schedule)
    cfg = margin.config(BOUGHT)
    cfg = replace(cfg, pipeline=replace(cfg.pipeline, retention=RetentionPolicy(results=1)))
    bounded = BacktestEngine.run(
        cfg,
        margin.prices({BOUGHT.asset_id: daily()}),
        running_strategy_state("S", margin.Trader(TRADES)),
        context_factory,
        schedule=schedule,
    )
    assert posted(bounded, TransactionType.INTEREST) == posted(unbounded, TransactionType.INTEREST)


def test_a_run_declaring_no_daily_accrual_records_no_accrual() -> None:
    result = run(TRADES, RunSchedule.of(interest=[interest(credit="0.01")]))
    assert accruals(result) == []


def test_cutoffs_follow_the_zones_clock_across_a_daylight_saving_change() -> None:
    new_york = DailyAccrual("America/New_York", time(17, 0))
    # 10 March 2024: New York's clocks went forward, so that day is 23 hours.
    assert new_york.instant(date(2024, 3, 10)) - new_york.instant(date(2024, 3, 9)) == 23 * 3600


@pytest.mark.parametrize(
    "build",
    [
        lambda: DailyAccrual("Not/AZone", time(17, 0)),
        lambda: DailyAccrual("UTC", time(17, 0, tzinfo=UTC)),
        lambda: DailyInterest(replace(interest(), at=jan(7, 12)), UTC_CLOSE),
        lambda: DailyInterest(
            interest(),
            UTC_CLOSE,
            (RateChange(date(2024, 1, 4), TIERS), RateChange(date(2024, 1, 4), TIERS)),
        ),
        lambda: RateChange(date(2024, 1, 4), TIERS, D("-0.01")),
        lambda: DailyBorrowFee(
            ScheduledBorrowFee(date(2024, 1, 1), date(2024, 1, 8), jan(7, 12)), UTC_CLOSE
        ),
    ],
)
def test_a_daily_accrual_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(ScheduleValidationError):
        build()


def test_a_cutoff_serializes_as_a_wall_clock_time_and_an_aware_one_is_refused() -> None:
    assert serialize(time(17, 0)) == '"17:00:00"'
    with pytest.raises(SerializationError):
        serialize(time(17, 0, tzinfo=UTC))
