"""A contract's lifecycle in markets whose conventions differ, none named in the engine (FUT-013).

Each profile here is **synthetic and declared**: a calendar with its time zone,
sessions and holidays, a currency, a multiplier, a deliverable quantity, an
exercise style and a settlement rule. The time zones are real ones --
``Asia/Kolkata`` keeps no daylight saving, ``America/New_York`` and
``Europe/London`` change on different dates -- because time-zone arithmetic is
what is under test; nothing here claims to be any exchange's actual rule book.
The last profile is a market AlphaLab has never heard of, trading Sunday to
Thursday with a lunch break, registered by declaring it -- and run through the
same code.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import date, time
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    BacktestEngine,
    BacktestResult,
    RunSchedule,
    ScheduledExercise,
    ScheduledExpiry,
)
from alphalab.backtesting.dataset import MarketDataset
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.conventions.lot import LotSpecification
from alphalab.conventions.settlement import SettlementBasis, SettlementRule
from alphalab.core.enums import AssetType
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.terms import AutomaticExercise, Deliverable, OptionTerms
from alphalab.market.quote import Quote
from alphalab.options.enums import ExerciseStyle, OptionType
from alphalab.options.expiration import SettlementStyle
from alphalab.portfolio.account import Account
from alphalab.portfolio.corporate_actions import Exercise, OptionExpiry
from alphalab.portfolio.fx import FxRate
from alphalab.runtime.calendars import session_close, settles_at
from alphalab.runtime.exceptions import RuntimeValidationError
from alphalab.runtime.run import RunConfig
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, IntentKind
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    backtest_config,
    context_factory,
    registry_of,
    running_strategy_state,
)

WEEKDAYS = (0, 1, 2, 3, 4)


def calendar(
    calendar_id: str,
    zone: str,
    windows: tuple[tuple[time, time], ...],
    days: Iterable[int] = WEEKDAYS,
    holidays: Iterable[date] = (),
) -> MarketCalendar:
    sessions = tuple(SessionWindow(opens, closes) for opens, closes in windows)
    return MarketCalendar(calendar_id, zone, dict.fromkeys(days, sessions), frozenset(holidays))


NO_DST = calendar("SYN-KOLKATA", "Asia/Kolkata", ((time(9, 15), time(15, 30)),))
NEW_YORK = calendar("SYN-NEWYORK", "America/New_York", ((time(9, 30), time(16, 0)),))
LONDON = calendar("SYN-LONDON", "Europe/London", ((time(8, 0), time(16, 30)),))
#: A market AlphaLab has never heard of: Sunday to Thursday, a lunch break, a
#: holiday on a Monday.
UNHEARD_OF = calendar(
    "SYN-XSYN",
    "Asia/Dubai",
    ((time(10, 0), time(12, 0)), (time(13, 0), time(15, 0))),
    days=(6, 0, 1, 2, 3),
    holidays=(date(2024, 3, 11),),
)
T_PLUS_ONE = SettlementRule(SettlementBasis.TRADING_DAYS, 1)


# --------------------------------------------------------------------------- #
# Instants from a market's own calendar
# --------------------------------------------------------------------------- #


def test_the_close_is_the_markets_own_across_daylight_saving() -> None:
    # New York moved its clocks on 10 March 2024; London on 31 March; Kolkata never.
    assert session_close(NEW_YORK, date(2024, 3, 8)) == 1709931600.0  # 21:00 UTC
    assert session_close(NEW_YORK, date(2024, 3, 11)) == 1710187200.0  # 20:00 UTC
    assert session_close(LONDON, date(2024, 3, 28)) == 1711643400.0  # 16:30 UTC
    assert session_close(LONDON, date(2024, 4, 2)) == 1712071800.0  # 15:30 UTC
    assert session_close(NO_DST, date(2024, 3, 8)) == 1709892000.0  # 10:00 UTC
    assert session_close(NO_DST, date(2024, 4, 2)) - session_close(NO_DST, date(2024, 3, 8)) == (
        25 * 86400
    )
    # After a lunch break the close is the afternoon's.
    assert session_close(UNHEARD_OF, date(2024, 3, 10)) == 1710068400.0  # 15:00 Dubai


def test_a_date_the_market_is_shut_is_not_moved_by_a_default() -> None:
    with pytest.raises(RuntimeValidationError, match="does not trade on 2024-03-09"):
        session_close(NEW_YORK, date(2024, 3, 9))
    with pytest.raises(RuntimeValidationError, match="does not trade on 2024-03-11"):
        session_close(UNHEARD_OF, date(2024, 3, 11))  # its holiday
    with pytest.raises(RuntimeValidationError, match="does not trade on 2024-03-15"):
        session_close(UNHEARD_OF, date(2024, 3, 15))  # a Friday


def test_a_settlement_rule_counts_the_markets_own_trading_days() -> None:
    # Thursday + 1 trading day: Friday in New York, Sunday in the unheard-of market,
    # whose Monday after is a holiday.
    assert settles_at(NEW_YORK, T_PLUS_ONE, date(2024, 3, 7)) == 1709908200.0  # Fri 14:30 UTC
    assert settles_at(UNHEARD_OF, T_PLUS_ONE, date(2024, 3, 7)) == 1710050400.0  # Sun 10:00 Dubai
    assert settles_at(UNHEARD_OF, T_PLUS_ONE, date(2024, 3, 10)) == 1710223200.0  # Tue: Mon shut
    calendar_days = SettlementRule(SettlementBasis.CALENDAR_DAYS, 2)
    with pytest.raises(RuntimeValidationError, match="does not trade on"):
        settles_at(NEW_YORK, calendar_days, date(2024, 3, 7))  # lands on a Saturday


# --------------------------------------------------------------------------- #
# One engine, several conventions
# --------------------------------------------------------------------------- #


class Buyer(BaseStrategy):
    def __init__(self, asset_id: str, at: float, quantity: Decimal) -> None:
        self._asset, self._at, self._quantity = asset_id, at, quantity

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if event.quote.asset_id != self._asset or event.quote.timestamp != self._at:
            return ()
        return (
            Intent(
                "S",
                self._asset,
                self._quantity,
                timestamp=self._at,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )


def _quote(asset: str, at: float, mid: str, currency: str) -> Quote:
    price = Decimal(mid)
    return Quote(asset, at, price, price, Decimal("1000"), Decimal("1000"), "SIM", currency)


def _config(
    base: str, records: Iterable[InstrumentRecord], also: frozenset[str] = frozenset()
) -> RunConfig:
    config = backtest_config("S")
    pipeline = replace(
        config.pipeline,
        account=Account(f"ACC-{base}", base, f"{base} book", 1.0),
        currency="",
        also_settles=also,
        instruments=registry_of(*records),
        # Two settlement currencies: the budget states which one it is in.
        budget=replace(config.pipeline.budget, currency=base if also else ""),
    )
    return replace(config, pipeline=pipeline)


def _profile(
    market: MarketCalendar,
    currency: str,
    style: ExerciseStyle,
    settlement: SettlementStyle,
    multiplier: str,
    units: str,
    expiry_day: date,
) -> tuple[InstrumentRecord, InstrumentRecord, float]:
    underlying = InstrumentRecord(
        f"U{currency}", AssetType.EQUITY, market.calendar_id[4:], currency
    )
    expires = session_close(market, expiry_day)
    contract = InstrumentRecord(
        f"O{currency}",
        AssetType.OPTION,
        market.calendar_id[4:],
        currency,
        economics=InstrumentEconomics(
            Decimal(multiplier),
            SettlementModel.OPTION_PREMIUM,
            lot=LotSpecification(Decimal("1"), Decimal("1")),
        ),
        last_trade_at=expires,
        terms=OptionTerms(
            OptionType.CALL,
            Decimal("100"),
            style,
            settlement,
            Deliverable(underlying.asset_id, Decimal(units)),
            expires_at=expires,
            automatic_exercise=AutomaticExercise(Decimal("0")),
        ),
    )
    return underlying, contract, expires


def _run(
    base: str,
    underlying: InstrumentRecord,
    contract: InstrumentRecord,
    start: float,
    schedule: RunSchedule,
    also: frozenset[str] = frozenset(),
) -> BacktestResult:
    # The data runs past every lifecycle instant: an input after the last record
    # is not applied, because the run could not have acted on it.
    after = max(entry.at for entry in schedule.entries) + 3600
    quotes = [
        _quote(contract.asset_id, start, "4", contract.currency),
        _quote(underlying.asset_id, start, "100", underlying.currency),
        _quote(underlying.asset_id, start + 3600, "103", underlying.currency),
        _quote(underlying.asset_id, after, "104", underlying.currency),
    ]
    return BacktestEngine.run(
        _config(base, (underlying, contract), also),
        MarketDataset.of("CONV", quotes),
        running_strategy_state("S", Buyer(contract.asset_id, start, Decimal("2"))),
        context_factory,
        schedule=schedule,
    )


@pytest.mark.parametrize(
    ("market", "currency", "multiplier", "units", "day"),
    [
        (NO_DST, "INR", "75", "75", date(2024, 3, 28)),
        (NEW_YORK, "USD", "100", "100", date(2024, 3, 15)),
        (LONDON, "GBP", "1000", "1000", date(2024, 4, 19)),
        (UNHEARD_OF, "AED", "10", "10", date(2024, 3, 14)),
    ],
    ids=["no-dst-inr", "dst-usd", "dst-gbp", "unheard-of-aed"],
)
def test_a_european_cash_option_settles_in_its_own_currency_and_units(
    market: MarketCalendar, currency: str, multiplier: str, units: str, day: date
) -> None:
    underlying, contract, expires = _profile(
        market, currency, ExerciseStyle.EUROPEAN, SettlementStyle.CASH, multiplier, units, day
    )
    start = expires - 3 * 86400
    result = _run(
        currency,
        underlying,
        contract,
        start,
        RunSchedule.of(
            expiries=[ScheduledExpiry(OptionExpiry(contract.asset_id, Decimal("107")), expires)]
        ),
    )
    paid = 2 * 7 * Decimal(multiplier)
    premium = 2 * 4 * Decimal(multiplier)
    assert result.state.portfolio.cash.balance(currency) == Decimal("1000000") - premium + paid
    assert contract.asset_id not in result.state.portfolio.positions


def test_an_american_physical_option_is_exercised_and_delivered_on_its_markets_settlement_day() -> (
    None
):
    underlying, contract, _ = _profile(
        UNHEARD_OF,
        "AED",
        ExerciseStyle.AMERICAN,
        SettlementStyle.PHYSICAL,
        "10",
        "10",
        date(2024, 3, 14),
    )
    # Instructed on Thursday the 7th; delivered T+1 by its calendar, the Sunday.
    delivers = settles_at(UNHEARD_OF, T_PLUS_ONE, date(2024, 3, 7))
    start = session_close(UNHEARD_OF, date(2024, 3, 7)) - 7200
    result = _run(
        "AED",
        underlying,
        contract,
        start,
        RunSchedule.of(
            exercises=[ScheduledExercise(Exercise(contract.asset_id, Decimal("2")), delivers)],
        ),
    )
    position = result.state.portfolio.positions[underlying.asset_id]
    assert position.quantity == Decimal("20") and position.average_cost == Decimal("100")
    assert result.state.portfolio.cash.balance("AED") == (
        Decimal("1000000") - Decimal("80") - Decimal("2000")
    )
    assert position.opened_at == delivers


def test_an_option_settling_in_another_currency_is_valued_at_the_rate_in_force() -> None:
    underlying, contract, expires = _profile(
        LONDON,
        "GBP",
        ExerciseStyle.EUROPEAN,
        SettlementStyle.CASH,
        "1000",
        "1000",
        date(2024, 4, 19),
    )
    start = expires - 3 * 86400
    rates = [
        FxRate("GBP", "USD", Decimal("1.25"), start - 60, "desk"),
        FxRate("GBP", "USD", Decimal("1.30"), expires - 60, "desk"),
    ]
    from alphalab.backtesting.schedule import Funding

    schedule = RunSchedule.of(
        rates=rates,
        fundings=[Funding(Decimal("50000"), "GBP", start - 30)],
        expiries=[ScheduledExpiry(OptionExpiry(contract.asset_id, Decimal("107")), expires)],
    )
    result = _run("USD", underlying, contract, start, schedule, also=frozenset({"GBP"}))
    gbp = result.state.portfolio.cash.balance("GBP")
    assert gbp == Decimal("50000") - Decimal("8000") + Decimal("14000")
    # The book is reported in dollars at the rate in force at the expiry.
    assert result.state.portfolio_snapshots[-1].total_equity == (
        Decimal("1000000") + gbp * Decimal("1.30")
    )
