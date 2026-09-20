"""A realistic multi-asset research path, end to end, with the units checked at every step.

Five asset classes in one book -- a US equity, a crude future, an index option, a
yen spot exposure and a perpetual -- valued in one reporting currency across two
instants. The point is not that each part works in isolation; the unit tests do
that. It is that the seams between them hold: a contract multiplier applied once,
a notional in the quote currency converted to the settlement one at a recorded
rate, sessions read in each venue's own time, and a currency return separated
from what the assets did.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from alphalab.conventions import (
    Compounding,
    DayCount,
    LotSpecification,
    MarketConvention,
    SettlementBasis,
    SettlementRule,
    TickSchedule,
    settlement_date,
)
from alphalab.crypto import (
    CryptoInstrument,
    FeeSchedule,
    FundingRate,
    FundingRateHistory,
    InstrumentType,
    LiquidityRole,
    PriceSource,
    VenueSpecification,
    accrued_funding,
    crypto_symbol,
    funding_instants,
    trading_fee,
)
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.futures import (
    AdjustmentMethod,
    ContractChain,
    FutureContract,
    RollPolicy,
    RollTrigger,
    build_continuous_series,
    continuous_segments,
    contract_tick_value,
    roll_schedule,
)
from alphalab.macro import Bond, clean_price, modified_duration
from alphalab.market.bar import Bar, TimeFrame
from alphalab.options import (
    ExerciseStyle,
    ExpirationPolicy,
    OptionContract,
    OptionType,
    SettlementStyle,
    black_scholes_price,
    implied_volatility,
    occ_symbol,
    resolve_expiration,
)
from alphalab.portfolio.contracts import ContractHolding, contract_exposures, settlement_exposures
from alphalab.portfolio.fx import FxRate, FxRates
from alphalab.portfolio.fx_research import currency_attribution
from alphalab.portfolio.position import Position

DAY = 86400.0
YEAR = 365.25 * DAY

# --------------------------------------------------------------------------- #
# The venues. Three timezones, three session shapes, three currencies.
# --------------------------------------------------------------------------- #

_XNAS = MarketCalendar(
    calendar_id="XNAS",
    timezone_name="America/New_York",
    weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(9, 30), time(16, 0)),)),
)
_XOSE = MarketCalendar(
    calendar_id="XOSE",
    timezone_name="Asia/Tokyo",
    weekly_sessions=dict.fromkeys(
        range(5),
        (SessionWindow(time(9, 0), time(11, 30)), SessionWindow(time(12, 30), time(15, 15))),
    ),
)
_XCME = MarketCalendar(
    calendar_id="XCME",
    timezone_name="America/Chicago",
    weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(17, 0), time(16, 0)),)),
)
_BINANCE = MarketCalendar.continuous("BINANCE", "UTC")

_EQUITY = MarketConvention(
    venue="XNAS",
    calendar_id="XNAS",
    quote_currency="USD",
    settlement_currency="USD",
    multiplier=Decimal("1"),
    tick=TickSchedule.flat(Decimal("0.01")),
    lot=LotSpecification.single_units(),
    settlement=SettlementRule(SettlementBasis.TRADING_DAYS, 1),
)
_CRUDE = MarketConvention(
    venue="XCME",
    calendar_id="XCME",
    quote_currency="USD",
    settlement_currency="USD",
    multiplier=Decimal("1000"),
    tick=TickSchedule.flat(Decimal("0.01")),
    lot=LotSpecification.single_units(),
    settlement=SettlementRule(SettlementBasis.TRADE_DATE, 0),
)
_NIKKEI_OPTION = MarketConvention(
    venue="XOSE",
    calendar_id="XOSE",
    quote_currency="JPY",
    settlement_currency="JPY",
    multiplier=Decimal("1000"),
    tick=TickSchedule.flat(Decimal("1")),
    lot=LotSpecification.single_units(),
    settlement=SettlementRule(SettlementBasis.TRADING_DAYS, 1),
)
_PERPETUAL = MarketConvention(
    venue="BINANCE",
    calendar_id="BINANCE",
    quote_currency="USDT",
    settlement_currency="USDT",
    multiplier=Decimal("1"),
    tick=TickSchedule.flat(Decimal("0.1")),
    lot=LotSpecification(Decimal("0.001"), Decimal("0.001")),
    settlement=SettlementRule(SettlementBasis.TRADE_DATE, 0),
)


def _position(asset_id: str, quantity: str, price: str, currency: str) -> Position:
    return Position(
        asset_id=asset_id,
        quantity=Decimal(quantity),
        average_cost=Decimal(price),
        market_price=Decimal(price),
        realized_pnl=Decimal("0"),
        currency=currency,
        last_updated=0.0,
    )


def _book() -> list[ContractHolding]:
    return [
        ContractHolding(_position("AAPL", "100", "200.00", "USD"), _EQUITY),
        ContractHolding(_position("CL_202606", "5", "75.00", "USD"), _CRUDE),
        ContractHolding(_position("NK_C_38000", "-2", "450", "JPY"), _NIKKEI_OPTION),
        ContractHolding(_position("BTCUSDT_PERP", "1.5", "60000.0", "USDT"), _PERPETUAL),
    ]


# --------------------------------------------------------------------------- #
# Sessions across three timezones
# --------------------------------------------------------------------------- #


def test_one_instant_is_read_in_each_venues_own_local_day() -> None:
    instant = datetime(2026, 3, 16, 21, 30, tzinfo=UTC).timestamp()
    assert _XNAS.local_datetime(instant).date() == date(2026, 3, 16)
    assert _XCME.local_datetime(instant).date() == date(2026, 3, 16)
    assert _XOSE.local_datetime(instant).date() == date(2026, 3, 17)
    assert _BINANCE.local_datetime(instant).date() == date(2026, 3, 16)


def test_the_four_venues_disagree_about_whether_they_were_open() -> None:
    """One instant, four answers -- which is the whole reason a venue's calendar
    is its own object rather than a global one."""

    instant = datetime(2026, 3, 16, 21, 30, tzinfo=UTC).timestamp()
    assert not _XNAS.is_open(instant)  # 17:30 New York, after the 16:00 close
    assert not _XCME.is_open(instant)  # 16:30 Chicago, in the daily maintenance break
    assert not _XOSE.is_open(instant)  # 06:30 Tokyo, before the open
    assert _BINANCE.is_open(instant)  # a venue that never closes

    # An hour later Chicago has reopened into the next overnight session and
    # nothing else has moved.
    reopened = instant + 3600
    assert _XCME.is_open(reopened)
    assert _XCME.trading_day_of(reopened) == date(2026, 3, 16)
    assert not _XNAS.is_open(reopened)
    assert not _XOSE.is_open(reopened)


def test_each_instruments_settlement_date_follows_its_own_venues_calendar() -> None:
    friday = date(2026, 3, 13)
    calendars = {"XNAS": _XNAS, "XOSE": _XOSE, "XCME": _XCME, "BINANCE": _BINANCE}
    for convention in (_EQUITY, _CRUDE, _NIKKEI_OPTION, _PERPETUAL):
        calendar = calendars[convention.calendar_id]
        needed = calendar if convention.settlement.needs_calendar else None
        settled = settlement_date(convention.settlement, friday, needed)
        assert settled >= friday

    # And the same rule on two calendars gives two dates.
    weekday_settled = settlement_date(_EQUITY.settlement, friday, _XNAS)
    continuous_settled = settlement_date(_NIKKEI_OPTION.settlement, friday, _BINANCE)
    assert weekday_settled == date(2026, 3, 16)
    assert continuous_settled == date(2026, 3, 14)


# --------------------------------------------------------------------------- #
# Exposure: the multiplier applied once, per currency
# --------------------------------------------------------------------------- #


def test_the_book_reports_three_currencies_and_never_sums_them() -> None:
    exposure = contract_exposures(_book())
    assert exposure.gross.currencies == ("JPY", "USD", "USDT")
    assert exposure.gross.of("USD") == Decimal("20000.00") + Decimal("375000.00")
    assert exposure.gross.of("JPY") == Decimal("900000")
    assert exposure.gross.of("USDT") == Decimal("90000.00")


def test_each_multiplier_is_applied_once_and_the_equity_is_not_scaled() -> None:
    exposure = contract_exposures(_book())
    assert exposure.by_asset["AAPL"].amount == Decimal("20000.00")
    assert exposure.by_asset["AAPL"].underlying_units == Decimal("100")
    assert exposure.by_asset["CL_202606"].amount == Decimal("375000.00")
    assert exposure.by_asset["CL_202606"].underlying_units == Decimal("5000")


def test_a_short_option_leg_keeps_its_direction_through_the_book() -> None:
    exposure = contract_exposures(_book())
    assert exposure.net.of("JPY") == Decimal("-900000")
    assert exposure.gross.of("JPY") == Decimal("900000")
    assert exposure.underlying_units["NK_C_38000"] == Decimal("-2000")


def test_every_position_settles_in_its_quote_currency_here_so_nothing_converts() -> None:
    for row in settlement_exposures(_book(), FxRates(), as_of=0.0):
        assert row.conversion is None
        assert row.settled == row.quoted.amount


def test_a_yen_settled_contract_quoted_in_dollars_converts_once_with_a_recorded_rate() -> None:
    quanto = ContractHolding(
        _position("NKD", "1", "38000", "USD"),
        MarketConvention(
            venue="XCME",
            calendar_id="XCME",
            quote_currency="JPY",
            settlement_currency="USD",
            multiplier=Decimal("5"),
            tick=TickSchedule.flat(Decimal("5")),
            lot=LotSpecification.single_units(),
            settlement=SettlementRule(SettlementBasis.TRADE_DATE, 0),
        ),
    )
    rates = FxRates.of([FxRate("JPY", "USD", Decimal("0.0067"), 0.0, "quote")])
    row = settlement_exposures([quanto], rates, as_of=10.0)[0]
    assert row.quoted.currency == "JPY"
    assert row.quoted.amount == Decimal("190000")  # 1 x 38000 x 5, once
    assert row.settled == Decimal("1273.00")
    assert row.conversion is not None
    assert row.conversion.rate.source == "quote"


# --------------------------------------------------------------------------- #
# Futures: chain, roll, continuous series, tick value
# --------------------------------------------------------------------------- #


def _chain() -> ContractChain:
    def contract(month: int) -> FutureContract:
        stamp = datetime(2026, month, 20, tzinfo=UTC).timestamp()
        return FutureContract("CL", stamp, stamp, 1000, Decimal("0.01"), "USD")

    return ContractChain("CL", (contract(3), contract(6), contract(9)))


def test_a_continuous_series_survives_the_whole_path_and_keeps_its_units() -> None:
    chain = _chain()
    observations = {
        symbol: tuple(
            Bar(
                asset_id=symbol,
                timestamp=index * DAY,
                open=Decimal(price),
                high=Decimal(price),
                low=Decimal(price),
                close=Decimal(price),
                volume=Decimal(volume),
                vwap=Decimal(price),
                trade_count=1,
                timeframe=TimeFrame.D1,
            )
            for index, (price, volume) in enumerate(series)
        )
        for symbol, series in {
            "CL_202603": [("70.00", "100" if i < 3 else "1") for i in range(9)],
            "CL_202606": [
                ("72.00", "50" if i < 3 else ("200" if i < 6 else "1")) for i in range(9)
            ],
            "CL_202609": [("74.00", "10" if i < 6 else "300") for i in range(9)],
        }.items()
    }
    rolls = roll_schedule(
        chain, RollPolicy(RollTrigger.VOLUME_CROSSOVER), observations=observations
    )
    segments = continuous_segments(chain, rolls, observations)
    series = build_continuous_series(segments, AdjustmentMethod.BACK_ADJUSTED)

    assert [event.timestamp for event in rolls] == [3 * DAY, 6 * DAY]
    assert len(series) == 9
    # The anchor is the current contract: the newest segment is untouched.
    assert series[-1].close == Decimal("74.00")
    # And a tick on this chain is worth the same money at every month.
    assert {contract_tick_value(c).amount for c in chain.contracts} == {Decimal("10.00")}


def test_the_multiplier_reaches_the_p_and_l_exactly_once() -> None:
    """Five contracts, a one-dollar move, a 1,000 multiplier: 5,000 dollars."""

    opened = ContractHolding(_position("CL_202606", "5", "75.00", "USD"), _CRUDE)
    marked = ContractHolding(_position("CL_202606", "5", "76.00", "USD"), _CRUDE)
    before = contract_exposures([opened]).by_asset["CL_202606"].amount
    after = contract_exposures([marked]).by_asset["CL_202606"].amount
    assert after - before == Decimal("5000.00")

    # And in ticks: a hundred ticks of 10.00 each, on five contracts.
    tick = contract_tick_value(FutureContract("CL", 0.0, DAY, 1000, Decimal("0.01"), "USD"))
    assert tick.amount * 100 * 5 == Decimal("5000.00")


# --------------------------------------------------------------------------- #
# Options: a chain quoted in yen, inverted and expired
# --------------------------------------------------------------------------- #


def _nikkei_call() -> OptionContract:
    return OptionContract(
        underlying_asset_id="NK225",
        strike=Decimal("38000"),
        expiry=YEAR,
        option_type=OptionType.CALL,
        style=ExerciseStyle.EUROPEAN,
        multiplier=1000,
    )


def test_an_option_priced_and_inverted_round_trips_on_a_non_us_multiplier() -> None:
    contract = _nikkei_call()
    price = black_scholes_price(contract, Decimal("38000"), 0.22, 0.01, 0.0)
    recovered = implied_volatility(contract, price, Decimal("38000"), 0.01, 0.0)
    assert recovered.value == pytest.approx(0.22, abs=1e-4)
    assert not recovered.assumptions.prices_early_exercise


def test_a_short_index_option_is_assigned_in_cash_and_the_sign_survives() -> None:
    policy = ExpirationPolicy(SettlementStyle.CASH, exercise_in_the_money=True)
    result = resolve_expiration(_nikkei_call(), Decimal("-2"), Decimal("38500"), policy)
    assert result.contract_symbol == occ_symbol(_nikkei_call())
    # 500 points of intrinsic, 1,000 multiplier, two contracts, short: paid out.
    assert result.cash_flow == Decimal("-1000000")
    assert result.underlying_units == Decimal("0")


def test_a_cash_settled_expiry_leaves_no_underlying_to_value() -> None:
    policy = ExpirationPolicy(SettlementStyle.CASH, exercise_in_the_money=True)
    physical = ExpirationPolicy(SettlementStyle.PHYSICAL, exercise_in_the_money=True)
    cash = resolve_expiration(_nikkei_call(), Decimal("1"), Decimal("38500"), policy)
    delivered = resolve_expiration(_nikkei_call(), Decimal("1"), Decimal("38500"), physical)
    assert cash.underlying_units == Decimal("0")
    assert delivered.underlying_units == Decimal("1000")
    assert cash.cash_flow != delivered.cash_flow


# --------------------------------------------------------------------------- #
# Crypto: funding and fees on a 24/7 venue
# --------------------------------------------------------------------------- #

_VENUE = VenueSpecification(
    venue="binance",
    funding_interval_hours=8,
    fees=FeeSchedule(maker_bps=Decimal("-1"), taker_bps=Decimal("5")),
    price_source=PriceSource.INDEX,
    minimum_notional=Decimal("5"),
    settlement_asset="USDT",
)


def test_funding_accrues_against_the_venues_own_schedule() -> None:
    instrument = CryptoInstrument(
        base_asset="BTC",
        quote_asset="USDT",
        instrument_type=InstrumentType.PERPETUAL,
        exchange="binance",
        contract_size=Decimal("1"),
    )
    symbol = crypto_symbol(instrument)
    instants = funding_instants(0.0, DAY, _VENUE.funding_interval_hours, anchor=0.0)
    assert len(instants) == 3

    history = FundingRateHistory(
        instrument_symbol=symbol,
        rates=tuple(
            FundingRate(symbol, Decimal("0.0001"), instant, _VENUE.funding_interval_hours)
            for instant in instants
        ),
    )
    marks = dict.fromkeys(instants, Decimal("60000"))
    summary = accrued_funding(history, Decimal("1.5"), marks, instrument.contract_size)
    assert summary.intervals == 3
    assert summary.total == Decimal("-27.0")  # a long pays when funding is positive


def test_a_fee_and_a_funding_payment_are_the_same_sign_convention() -> None:
    """Both are cash flows to the holder, so they add without either being negated."""

    fee = trading_fee(_VENUE, Decimal("90000"), LiquidityRole.TAKER)
    assert fee == Decimal("-45.00")
    assert fee < Decimal("0")


def test_a_venue_with_a_different_interval_annualizes_differently() -> None:
    hourly = VenueSpecification("other", 1, _VENUE.fees, PriceSource.MARK, Decimal("1"), "USDC")
    assert hourly.funding_intervals_per_year == _VENUE.funding_intervals_per_year * 8


# --------------------------------------------------------------------------- #
# Fixed income beside the rest
# --------------------------------------------------------------------------- #


def test_a_bond_sits_in_the_same_book_with_its_own_units() -> None:
    bond = Bond(
        face=Decimal("100"),
        coupon_rate=0.04,
        frequency=Compounding.SEMI_ANNUAL,
        issue_date=date(2024, 1, 15),
        maturity=date(2034, 1, 15),
        day_count=DayCount.ACT_365_FIXED,
        currency="USD",
    )
    settlement = date(2026, 1, 15)
    price = clean_price(bond, 0.045, settlement)
    duration = modified_duration(bond, 0.045, settlement)
    assert price < Decimal("100")  # yielding above its coupon
    assert 0 < duration < 8  # years, and shorter than the eight to maturity
    assert bond.currency == _EQUITY.settlement_currency


# --------------------------------------------------------------------------- #
# Currency attribution over the whole book
# --------------------------------------------------------------------------- #


def test_the_books_return_splits_into_what_the_assets_did_and_what_the_yen_did() -> None:
    opening_rates = FxRates.of(
        [
            FxRate("JPY", "USD", Decimal("0.0067"), 0.0, "open"),
            FxRate("USDT", "USD", Decimal("1.0"), 0.0, "open"),
        ]
    )
    closing_rates = FxRates.of(
        [
            FxRate("JPY", "USD", Decimal("0.0070"), DAY, "close"),
            FxRate("USDT", "USD", Decimal("1.0"), DAY, "close"),
        ]
    )
    report = currency_attribution(
        opening={
            "USD": Decimal("395000"),
            "JPY": Decimal("-900000"),
            "USDT": Decimal("90000"),
        },
        closing={
            "USD": Decimal("400000"),
            "JPY": Decimal("-880000"),
            "USDT": Decimal("91000"),
        },
        opening_rates=opening_rates,
        closing_rates=closing_rates,
        reporting_currency="USD",
        opening_timestamp=0.0,
        closing_timestamp=DAY,
    )

    assert report.reporting_currency == "USD"
    assert [entry.currency for entry in report.by_currency] == ["JPY", "USD", "USDT"]

    yen = report.by_currency[0]
    # The short yen leg shrank (a gain) while the yen strengthened (a loss on a short).
    assert yen.local_return > Decimal("0")
    assert yen.currency_return < Decimal("0")

    dollars = report.by_currency[1]
    assert dollars.currency_return == Decimal("0.00")

    assert report.total == report.local_return + report.currency_return
    assert abs(report.rounding) <= Decimal("0.03")  # at most a minor unit per currency


def test_the_whole_path_is_deterministic_across_repeated_runs() -> None:
    first = contract_exposures(_book())
    second = contract_exposures(_book())
    assert first.net == second.net
    assert first.gross == second.gross
    assert first.underlying_units == second.underlying_units
