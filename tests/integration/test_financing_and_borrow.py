"""Financing, margin purchases and short borrow (v4.2.0, ledger FUT-018).

A broker's terms, declared: an instrument may allow a purchase on margin, so its
buying power is its initial requirement and the cash it lacks is a financed
debit; a period's interest on a currency's balance is charged by declared tiers
and day count; a short is sold only against borrow a lender offers, dated on the
instrument, and pays the fee in effect for each period it is held. Nothing is
assumed: no rate, no availability, no lender.
"""

from __future__ import annotations

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
from alphalab.backtesting.schedule import (
    ScheduledBorrowFee,
    ScheduledInterest,
    ScheduleValidationError,
)
from alphalab.conventions.daycount import DayCount
from alphalab.instrument.borrow import (
    BorrowAvailability,
    BorrowTerms,
    borrow_from_primitives,
    borrow_to_primitives,
)
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.margin import MarginBasis, MarginTerms, margin_to_primitives
from alphalab.instrument.snapshot import capture as capture_registry
from alphalab.instrument.snapshot import from_primitives as registry_from_primitives
from alphalab.instrument.snapshot import restore as restore_registry
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.exceptions import InvalidTransactionError
from alphalab.portfolio.financing import (
    BalanceBasis,
    FinancingTier,
    accrued_interest,
    day_fraction,
)
from alphalab.portfolio.types import TransactionType
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from tests.integration import test_margin_and_liquidation as margin
from tests.integration.harness import context_factory, quote, registry_of, running_strategy_state

D = Decimal
ON_MARGIN = MarginTerms(MarginBasis.VALUE_RATE, D("0.5"), D("0.3"), purchase_on_margin=True)
BOUGHT = replace(margin.PLAIN, margin=ON_MARGIN)
TIERS = (FinancingTier(D("1000"), D("0.05")), FinancingTier(None, D("0.08")))
DAY = (date(2024, 1, 2), date(2024, 1, 3))


def interest(at: float, currency: str = "USD", **kw: Any) -> ScheduledInterest:
    return ScheduledInterest(currency, *DAY, at, DayCount.ACT_360, TIERS, **kw)


def usd(result: BacktestResult) -> Decimal:
    return result.state.portfolio.cash.balance("USD")


def run(plan: dict[tuple[float, str], Decimal], *records: Any, **kw: Any) -> BacktestResult:
    mids = kw.pop("mids", None) or {records[0].asset_id: margin.series("100", "100", "100")}
    return margin.run(mids, plan, *records, **kw)


# --------------------------------------------------------------------------- #
# Interest by declared tiers, on a margin purchase's debit
# --------------------------------------------------------------------------- #


def test_a_margin_purchase_spends_its_initial_requirement_and_is_financed() -> None:
    result = run({(2.0, BOUGHT.asset_id): D("150")}, BOUGHT)
    # 15,000 of shares against 10,000 of cash: 7,500 of initial margin is the
    # buying power spent, and the 5,000 the cash lacks is a debit.
    assert margin.fills(result, BOUGHT) == [(2.0, D("150"))]
    assert usd(result) == D("-5000.00")


def test_a_financed_purchase_whose_price_falls_is_called_and_sold_down() -> None:
    mids = {BOUGHT.asset_id: margin.series("100", "40", "40", "40")}
    result = run({(2.0, BOUGHT.asset_id): D("150")}, BOUGHT, mids=mids)
    # At 40 the 150 shares are worth 6,000 against the 5,000 debit: equity 1,000
    # under the 1,800 maintenance. Restoring the 3,000 initial requirement sells
    # 2,000 / 20 = 100 at the next quote, the proceeds paying down the debit.
    (call,) = margin.calls(result)
    assert (call.timestamp, call.equity, call.deficit) == (3.0, D("1000.00"), D("2000"))
    assert margin.fills(result, BOUGHT) == [(2.0, D("150")), (4.0, D("-100"))]
    assert usd(result) == D("-1000.00")


def test_a_purchase_beyond_the_initial_requirement_is_refused() -> None:
    plan = {(2.0, BOUGHT.asset_id): D("250")}
    trader = margin.Trader(plan)
    result = run(plan, BOUGHT, strategy=trader)
    # 25,000 needs 12,500 of initial margin; equity is 10,000.
    assert margin.fills(result, BOUGHT) == []
    assert any("below its initial margin" in r for r in trader.rejections)


def test_a_debit_is_charged_band_by_band_for_the_period_by_its_day_count() -> None:
    schedule = RunSchedule.of(interest=[interest(3.0)])
    result = run({(2.0, BOUGHT.asset_id): D("150")}, BOUGHT, schedule=schedule)
    # 1,000 at 5% and 4,000 at 8%, for one day of 360: 370 / 360 = 1.0277...
    (charged,) = [
        t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.INTEREST
    ]
    assert charged.timestamp == 3.0
    assert usd(result) == D("-5000.00") - D("1.03")


def test_a_credit_earns_only_a_declared_credit_rate() -> None:
    earning = RunSchedule.of(interest=[interest(3.0, credit_rate=D("0.02"))])
    plain = run({}, margin.PLAIN, schedule=RunSchedule.of(interest=[interest(3.0)]))
    earned = run({}, margin.PLAIN, schedule=earning)
    assert usd(plain) == margin.CASH  # no credit rate: nothing paid on 10,000
    # 10,000 at 2% for a day of 360: 0.5555... received.
    assert usd(earned) == margin.CASH + D("0.56")


def test_each_currency_is_charged_on_its_own_balance() -> None:
    schedule = RunSchedule.of(interest=[interest(3.0, "EUR")])
    result = run({(2.0, BOUGHT.asset_id): D("150")}, BOUGHT, schedule=schedule)
    # The debit is in USD; a EUR schedule finds no EUR balance and books nothing.
    assert usd(result) == D("-5000.00")
    assert not [
        t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.INTEREST
    ]


@pytest.mark.parametrize(
    ("balance", "credit", "expected"),
    [
        (D("-500"), None, D("-25")),  # within the first band
        (D("-1000"), None, D("-50")),  # exactly its ceiling
        (D("-3000"), None, D("-210")),  # 1,000 x 5% + 2,000 x 8%
        (D("0"), D("0.02"), D("0")),
        (D("2000"), D("0.02"), D("40")),
    ],
)
def test_the_tiers_charge_each_band_at_its_own_rate(
    balance: Decimal, credit: Decimal | None, expected: Decimal
) -> None:
    assert accrued_interest(balance, TIERS, credit, D("1")) == expected


def test_a_period_is_counted_by_its_day_count_and_runs_forward() -> None:
    from alphalab.common.arithmetic import ACCOUNTING_CONTEXT as ctx

    # 2024 is a leap year: 31 January to 1 March is 30 actual days; 30/360 US
    # counts 31 (the 31st becomes the 30th, so 2 x 30 - 29).
    assert day_fraction(date(2024, 1, 31), date(2024, 3, 1), DayCount.ACT_365_FIXED) == (
        ctx.divide(D(30), D(365))
    )
    assert day_fraction(date(2024, 1, 31), date(2024, 3, 1), DayCount.THIRTY_360_US) == (
        ctx.divide(D(31), D(360))
    )
    with pytest.raises(InvalidTransactionError):
        day_fraction(date(2024, 1, 3), date(2024, 1, 3), DayCount.ACT_360)


@pytest.mark.parametrize(
    "tiers",
    [
        (),
        (FinancingTier(D("1000"), D("0.05")),),  # the last band has a ceiling
        (
            FinancingTier(D("2000"), D("0.05")),
            FinancingTier(D("1000"), D("0.06")),
            FinancingTier(None, D("0.07")),
        ),
    ],
)
def test_tiers_that_cannot_charge_every_debit_are_refused(tiers: Any) -> None:
    with pytest.raises(InvalidTransactionError):
        ScheduledInterest("USD", *DAY, 3.0, DayCount.ACT_360, tiers)


def test_a_settled_basis_charges_nothing_on_a_purchase_not_yet_paid() -> None:
    from alphalab.runtime.calendars import VenueCalendars
    from tests.integration.test_settlement_date_accounting import NEW_YORK, T_PLUS_2, ny

    settled = replace(BOUGHT, settlement=T_PLUS_2)
    wed, thu = ny(27), ny(28)
    trade_date = ScheduledInterest("USD", *DAY, thu, DayCount.ACT_360, TIERS)
    on_settled = replace(trade_date, basis=BalanceBasis.SETTLED)
    results = []
    for entry in (trade_date, on_settled):
        cfg = margin.config(settled)
        cfg = replace(
            cfg, pipeline=replace(cfg.pipeline, calendars=VenueCalendars({"XNAS": NEW_YORK}))
        )
        results.append(
            BacktestEngine.run(
                cfg,
                MarketDataset.of("FIN", [quote(settled.asset_id, t, D("100")) for t in (wed, thu)]),
                running_strategy_state("S", margin.Trader({(wed, settled.asset_id): D("150")})),
                context_factory,
                schedule=RunSchedule.of(interest=[entry]),
            )
        )
    by_trade, by_settlement = results
    # Bought Wednesday, settling Friday: on Thursday the trade-date debit is
    # 5,000 and charged; the settled balance still holds the 10,000, and nothing is.
    assert usd(by_trade) == D("-5000.00") - D("1.03")
    assert usd(by_settlement) == D("-5000.00")


# --------------------------------------------------------------------------- #
# Borrow: availability and fees, dated on the instrument
# --------------------------------------------------------------------------- #


def borrowing(*entries: tuple[float, str | None, str]) -> Any:
    terms = BorrowTerms(
        tuple(
            BorrowAvailability(at, None if available is None else D(available), D(rate))
            for at, available, rate in entries
        ),
        DayCount.ACT_360,
    )
    return replace(margin.PLAIN, borrow=terms)


@pytest.mark.parametrize(
    ("available", "placed"),
    [("50", False), ("100", True), (None, True), ("0", False)],
)
def test_a_short_is_sold_only_against_borrow_available(available: str | None, placed: bool) -> None:
    record = borrowing((1.0, available, "0.10"))
    plan = {(2.0, record.asset_id): D("-100")}
    trader = margin.Trader(plan)
    result = run(plan, record, strategy=trader)
    assert (margin.fills(result, record) == [(2.0, D("-100"))]) is placed
    assert any("available to borrow" in r for r in trader.rejections) is not placed


def test_before_the_first_entry_nothing_can_be_borrowed() -> None:
    record = borrowing((5.0, None, "0.10"))
    plan = {(2.0, record.asset_id): D("-10")}
    trader = margin.Trader(plan)
    result = run(plan, record, strategy=trader)
    assert margin.fills(result, record) == []
    assert any("No borrow is in effect" in r for r in trader.rejections)


def test_a_change_in_availability_refuses_more_and_leaves_what_is_held() -> None:
    # 100 available at first, none from 3.0: the second short is refused, and the
    # first is neither bought in nor charged more than its fee.
    record = borrowing((1.0, "100", "0.10"), (3.0, "0", "0.10"))
    plan = {(2.0, record.asset_id): D("-60"), (4.0, record.asset_id): D("-20")}
    trader = margin.Trader(plan)
    result = run(
        plan, record, strategy=trader, mids={record.asset_id: margin.series("100", "100", "100")}
    )
    assert margin.fills(result, record) == [(2.0, D("-60"))]
    assert any("available to borrow" in r for r in trader.rejections)


def test_a_cover_is_never_refused_for_borrow() -> None:
    record = borrowing((1.0, "100", "0.10"), (3.0, "0", "0.10"))
    plan = {(2.0, record.asset_id): D("-60"), (4.0, record.asset_id): D("40")}
    result = run(plan, record, mids={record.asset_id: margin.series("100", "100", "100")})
    assert margin.fills(result, record) == [(2.0, D("-60")), (4.0, D("40"))]


def fee(at: float) -> ScheduledBorrowFee:
    return ScheduledBorrowFee(*DAY, at)


def test_a_short_pays_the_fee_in_effect_for_each_period_on_what_it_still_holds() -> None:
    # 10% a year to 3.5, 20% after; short 100 at 100, 40 covered at 3.0.
    record = borrowing((1.0, None, "0.10"), (3.5, None, "0.20"))
    plan = {(2.0, record.asset_id): D("-100"), (3.0, record.asset_id): D("40")}
    schedule = RunSchedule.of(borrow_fees=[fee(2.0), fee(3.0), fee(4.0)])
    result = run(plan, record, schedule=schedule)
    fees = [t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.FEE]
    # At 2.0: 10,000 x 10% / 360 = 2.78. At 3.0, after the cover: 6,000 x 10% / 360
    # = 1.67. At 4.0: 6,000 x 20% / 360 = 3.33.
    assert [(t.timestamp, t.asset_id) for t in fees] == [(2.0, record.asset_id)] * 1 + [
        (3.0, record.asset_id),
        (4.0, record.asset_id),
    ]
    proceeds = D("10000.00") - D("4000.00")
    assert usd(result) == margin.CASH + proceeds - D("2.78") - D("1.67") - D("3.33")


def test_a_short_in_an_instrument_declaring_no_borrow_pays_no_fee() -> None:
    plan = {(2.0, margin.PLAIN.asset_id): D("-100")}
    result = run(plan, margin.PLAIN, schedule=RunSchedule.of(borrow_fees=[fee(3.0)]))
    assert not [
        t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.FEE
    ]


def test_a_short_held_with_no_borrow_in_effect_is_refused_at_its_fee() -> None:
    from alphalab.backtesting.schedule import _borrow_fees

    # A short the book holds -- sold under terms then in effect -- read against a
    # registry whose terms start later, as a corrected declaration would: the fee
    # has nothing declared to charge, and is refused rather than taken as zero.
    record = borrowing((1.0, None, "0.10"))
    held = run({(2.0, record.asset_id): D("-100")}, record)
    assert margin.fills(held, record) == [(2.0, D("-100"))]

    def against(registry: Any, prices: Any = None) -> Any:
        pipeline = (
            held.run.pipeline
            if prices is None
            else replace(held.run.pipeline, market_prices=prices)
        )
        config = replace(
            held.run.config, pipeline=replace(held.run.config.pipeline, instruments=registry)
        )
        return replace(held.run, config=config, pipeline=pipeline)

    later = BorrowTerms((BorrowAvailability(9.0, None, D("0.10")),), DayCount.ACT_360)
    with pytest.raises(ScheduleValidationError, match="no entry in effect"):
        _borrow_fees(against(registry_of(replace(record, borrow=later))), fee(3.0))
    # And with no mark there is nothing to value the short at.
    with pytest.raises(ScheduleValidationError, match="needs its mark"):
        _borrow_fees(against(registry_of(record), prices={}), fee(3.0))


# --------------------------------------------------------------------------- #
# Validation, persistence and replay
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "build",
    [
        lambda: BorrowTerms((), DayCount.ACT_360),
        lambda: BorrowTerms(
            (BorrowAvailability(2.0, None, D("0.1")), BorrowAvailability(2.0, None, D("0.1"))),
            DayCount.ACT_360,
        ),
        lambda: BorrowAvailability(1.0, D("-1"), D("0.1")),
        lambda: BorrowAvailability(1.0, None, D("-0.1")),
    ],
)
def test_borrow_terms_that_cannot_hold_are_refused(build: Any) -> None:
    with pytest.raises(InstrumentInputError):
        build()


def test_borrow_and_margin_purchase_round_trip_and_write_only_what_is_declared() -> None:
    record = replace(borrowing((1.0, "100", "0.10"), (3.0, None, "0.20")), margin=ON_MARGIN)
    assert borrow_from_primitives(borrow_to_primitives(record.borrow)) == record.borrow
    assert "purchase_on_margin" in margin_to_primitives(ON_MARGIN)  # type: ignore[operator]
    assert "purchase_on_margin" not in margin_to_primitives(margin.SHORT_MARGIN)  # type: ignore[operator]
    payload = deserialize(serialize(capture_registry(registry_of(record))))
    restored = restore_registry(registry_from_primitives(payload)).record_for(record.asset_id)
    assert restored is not None and restored.borrow == record.borrow
    assert restored.margin == ON_MARGIN


def test_financing_and_borrow_replay_and_restore_from_every_pause() -> None:
    record = replace(borrowing((1.0, None, "0.10")), symbol="BRW")
    bought = BOUGHT
    # Short 50 for 5,000 and buy 160 on margin for 16,000: 1,000 of debit to finance.
    plan = {(2.0, record.asset_id): D("-50"), (2.0, bought.asset_id): D("160")}
    schedule = RunSchedule.of(interest=[interest(3.0)], borrow_fees=[fee(3.0), fee(4.0)])
    mids = {
        record.asset_id: margin.series("100", "101", "102"),
        bought.asset_id: margin.series("100", "99", "98"),
    }
    cfg = margin.config(record, bought)
    data = margin.prices(mids)
    whole = ReplayBacktest.run(
        cfg,
        data,
        running_strategy_state("S", margin.Trader(plan)),
        context_factory,
        schedule=schedule,
    )
    backtest = BacktestEngine.run(
        cfg,
        data,
        running_strategy_state("S", margin.Trader(plan)),
        context_factory,
        schedule=schedule,
    )
    assert whole.backtest.fills == backtest.fills
    assert usd(whole.backtest) == usd(backtest)
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(data.records)):
        paused = ReplayBacktest.run(
            cfg,
            data,
            running_strategy_state("S", margin.Trader(plan)),
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
                {"S": margin.Trader(plan)},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, data, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory)
        assert digest_run(resumed.backtest).result_id == reference
        # Each period charged once, whatever the pause.
        charged = [
            t
            for t in resumed.backtest.state.portfolio.ledger.transactions
            if t.type in (TransactionType.INTEREST, TransactionType.FEE)
        ]
        assert len(charged) == 3


def test_a_run_declaring_none_of_it_records_exactly_what_it_did() -> None:
    plain = run({(2.0, margin.PLAIN.asset_id): D("-50")}, margin.PLAIN)
    declared = run(
        {(2.0, margin.PLAIN.asset_id): D("-50")},
        replace(
            margin.PLAIN,
            borrow=BorrowTerms((BorrowAvailability(1.0, None, D("0.1")),), DayCount.ACT_360),
        ),
    )
    # Declared borrow that is available and never charged changes nothing recorded.
    assert digest_run(plain).result_id == digest_run(declared).result_id


# --------------------------------------------------------------------------- #
# Borrow at its edges: an entry's own instant, working orders, side and units
# --------------------------------------------------------------------------- #


def test_an_entry_is_in_effect_from_its_own_instant() -> None:
    record = borrowing((2.0, "100", "0.10"))
    result = run({(2.0, record.asset_id): D("-100")}, record)
    assert margin.fills(result, record) == [(2.0, D("-100"))]


class RestingSeller(margin.Trader):
    """Rests a limit sell at 2.0, then asks for a market order at 3.0."""

    def __init__(self, resting: Decimal, then: Decimal) -> None:
        super().__init__({})
        self._resting, self._then = resting, then

    def on_quote(self, context: Any, event: Any) -> Any:
        from alphalab.common.order_terms import OrderTerms
        from alphalab.strategy.events import Intent

        at, asset = event.quote.timestamp, event.quote.asset_id
        if at == 2.0:
            return (
                Intent("S", asset, self._resting, timestamp=at, terms=OrderTerms.limit(D("110"))),
            )
        if at == 3.0:
            return (Intent("S", asset, self._then, timestamp=at),)
        return ()


@pytest.mark.parametrize(("then", "placed"), [(D("-60"), False), (D("-40"), True)])
def test_a_working_short_sale_counts_against_what_is_available(then: Decimal, placed: bool) -> None:
    # 100 available; a resting offer for 60 is working, so 40 more may be sold.
    record = borrowing((1.0, "100", "0.10"))
    seller = RestingSeller(D("-60"), then)
    result = run({}, record, strategy=seller)
    assert ((3.0, then) in margin.fills(result, record)) is placed
    assert any("available to borrow" in r for r in seller.rejections) is not placed


def test_a_buy_beside_a_working_short_sale_is_never_refused_for_borrow() -> None:
    # The resting offer is the book's only short; availability then falls to
    # nothing, and a buy of 20 -- which shrinks the committed short -- trades.
    record = borrowing((1.0, "100", "0.10"), (2.5, "0", "0.10"))
    seller = RestingSeller(D("-60"), D("20"))
    result = run({}, record, strategy=seller)
    assert (3.0, D("20")) in margin.fills(result, record)
    assert not any("borrow" in r for r in seller.rejections)


def test_a_margin_purchasable_instrument_is_still_charged_its_notional_to_short() -> None:
    record = replace(
        BOUGHT, borrow=BorrowTerms((BorrowAvailability(1.0, None, D("0.1")),), DayCount.ACT_360)
    )
    plan = {(2.0, record.asset_id): D("-150")}
    result = run(plan, record)
    # 15,000 of short sale against 10,000 of buying power: only a purchase is
    # charged its initial requirement.
    assert margin.fills(result, record) == []
    rejected = [d for d in result.state.risk.history if not d.approved]
    assert any(v.rule == "BuyingPowerLimit" for d in rejected for v in d.violations)


def test_a_long_in_a_borrowable_name_pays_no_fee_and_a_multiplier_counts() -> None:
    from alphalab.conventions.economics import InstrumentEconomics, SettlementModel

    # Ten shares a unit: 5 short units are 50 shares, 5,000 at 100.
    units = replace(
        borrowing((1.0, None, "0.36")),
        symbol="ADRX",
        economics=InstrumentEconomics(D("10"), SettlementModel.CASH_EQUITY),
    )
    longs = replace(borrowing((1.0, None, "0.36")), symbol="LONGX")
    plan = {(2.0, units.asset_id): D("-5"), (2.0, longs.asset_id): D("20")}
    mids = {
        units.asset_id: margin.series("100", "100"),
        longs.asset_id: margin.series("100", "100"),
    }
    result = run(plan, units, longs, mids=mids, schedule=RunSchedule.of(borrow_fees=[fee(3.0)]))
    fees = [t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.FEE]
    # 5,000 x 36% / 360 = 5.00, on the short alone.
    assert [t.asset_id for t in fees] == [units.asset_id]
    expected = margin.CASH + D("5000") - D("2000") - D("5.00")
    assert result.state.portfolio.cash.balance("USD") == expected
