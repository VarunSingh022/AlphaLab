"""Contract-bearing positions: the multiplier applied once, and the currency it lands in."""

from datetime import date, time
from decimal import Decimal

import pytest

from alphalab.conventions import (
    LotSpecification,
    MarketConvention,
    SettlementBasis,
    SettlementRule,
    TickSchedule,
)
from alphalab.portfolio.contracts import (
    ContractHolding,
    contract_exposures,
    holding_notional,
    settlement_exposures,
)
from alphalab.portfolio.exceptions import PortfolioError
from alphalab.portfolio.exposure import ExposureEngine
from alphalab.portfolio.fx import FxRate, FxRates, MissingRateError
from alphalab.portfolio.position import Position


def _convention(
    multiplier: str = "1000", quote: str = "USD", settle: str = "USD", venue: str = "XCME"
) -> MarketConvention:
    return MarketConvention(
        venue=venue,
        calendar_id=venue,
        quote_currency=quote,
        settlement_currency=settle,
        multiplier=Decimal(multiplier),
        tick=TickSchedule.flat(Decimal("0.01")),
        lot=LotSpecification.single_units(),
        settlement=SettlementRule(SettlementBasis.TRADE_DATE, 0),
    )


def _position(asset_id: str, quantity: str, price: str, currency: str = "USD") -> Position:
    return Position(
        asset_id=asset_id,
        quantity=Decimal(quantity),
        average_cost=Decimal(price),
        market_price=Decimal(price),
        realized_pnl=Decimal("0"),
        currency=currency,
        last_updated=0.0,
    )


def _holding(
    asset_id: str = "CL_202603", quantity: str = "10", price: str = "75.50", **kwargs: str
) -> ContractHolding:
    convention = _convention(**kwargs)
    return ContractHolding(
        _position(asset_id, quantity, price, convention.settlement_currency), convention
    )


# --------------------------------------------------------------------------- #
# The pairing
# --------------------------------------------------------------------------- #


def test_a_position_and_convention_disagreeing_on_currency_is_refused() -> None:
    with pytest.raises(PortfolioError, match="right about the amount and wrong about the currency"):
        ContractHolding(_position("X", "1", "10", "EUR"), _convention())


def test_the_multiplier_is_applied_exactly_once() -> None:
    notional = holding_notional(_holding())
    assert notional.amount == Decimal("755000.00")
    assert notional.amount == notional.quantity * notional.price * notional.multiplier


def test_a_position_without_a_multiplier_is_stated_as_one_and_is_visible() -> None:
    """A caller who already scaled into underlying units says so, truthfully."""

    scaled = _holding(quantity="10000", multiplier="1")
    assert holding_notional(scaled).amount == holding_notional(_holding()).amount
    assert holding_notional(scaled).multiplier == Decimal("1")


def test_the_repositorys_plain_exposure_is_the_unmultiplied_one() -> None:
    """The trap this module exists for: ``Position.market_value`` is shares."""

    positions = {"CL_202603": _position("CL_202603", "10", "75.50")}
    assert ExposureEngine.gross_exposure(positions) == Decimal("755.00")
    assert holding_notional(_holding()).amount == Decimal("755000.00")


# --------------------------------------------------------------------------- #
# Exposure across a book
# --------------------------------------------------------------------------- #


def test_notional_and_underlying_units_are_separate_dimensions() -> None:
    exposure = contract_exposures([_holding()])
    assert exposure.by_asset["CL_202603"].amount == Decimal("755000.00")
    assert exposure.underlying_units["CL_202603"] == Decimal("10000")


def test_net_offsets_and_gross_does_not() -> None:
    book = [
        _holding("CL_202603", "10", "75.50"),
        _holding("CL_202606", "-10", "75.50"),
    ]
    exposure = contract_exposures(book)
    assert exposure.net.of("USD") == Decimal("0")
    assert exposure.gross.of("USD") == Decimal("1510000.00")


def test_two_currencies_are_reported_apart_and_never_summed() -> None:
    book = [
        _holding("CL_202603", "10", "75.50"),
        _holding("NK_202603", "2", "38000", quote="JPY", settle="JPY", venue="XOSE"),
    ]
    exposure = contract_exposures(book)
    assert exposure.gross.currencies == ("JPY", "USD")
    assert exposure.gross.of("JPY") == Decimal("76000000")
    assert exposure.gross.of("USD") == Decimal("755000.00")


def test_a_duplicated_asset_id_is_refused_rather_than_doubled() -> None:
    with pytest.raises(PortfolioError, match="declared twice rather than held twice"):
        contract_exposures([_holding(), _holding()])


def test_an_empty_book_has_no_exposure_and_no_currencies() -> None:
    exposure = contract_exposures([])
    assert exposure.by_asset == {}
    assert exposure.gross.currencies == ()


# --------------------------------------------------------------------------- #
# Settlement currency
# --------------------------------------------------------------------------- #


def test_an_instrument_quoted_and_settled_alike_is_not_converted() -> None:
    """``conversion is None`` distinguishes 'never needed one' from 'converted at 1.0'."""

    result = settlement_exposures([_holding()], FxRates(), as_of=0.0)[0]
    assert result.conversion is None
    assert result.settled == result.quoted.amount


def test_a_quanto_is_converted_once_and_records_the_rate() -> None:
    quanto = ContractHolding(
        _position("Q", "10", "100", "EUR"), _convention(quote="USD", settle="EUR")
    )
    rates = FxRates.of([FxRate("USD", "EUR", Decimal("0.9"), 0.0, "ECB")])
    result = settlement_exposures([quanto], rates, as_of=5.0)[0]
    assert result.quoted.currency == "USD"
    assert result.quoted.amount == Decimal("1000000")  # 10 x 100 x 1000, once
    assert result.settled == Decimal("900000.00")
    assert result.conversion is not None
    assert result.conversion.rate.source == "ECB"


def test_a_quanto_without_a_rate_is_refused_rather_than_valued_at_one() -> None:
    quanto = ContractHolding(
        _position("Q", "10", "100", "EUR"), _convention(quote="USD", settle="EUR")
    )
    with pytest.raises(MissingRateError):
        settlement_exposures([quanto], FxRates(), as_of=5.0)


def test_settlement_exposures_preserve_the_order_they_were_given() -> None:
    book = [_holding("A", "1", "10"), _holding("B", "2", "20"), _holding("C", "3", "30")]
    assert [row.asset_id for row in settlement_exposures(book, FxRates(), 0.0)] == ["A", "B", "C"]


# --------------------------------------------------------------------------- #
# Conventions reach the settlement date through the calendar
# --------------------------------------------------------------------------- #


def test_a_conventions_settlement_rule_reaches_a_date_through_the_one_calendar() -> None:
    from alphalab.conventions import settlement_date
    from alphalab.data.calendar import MarketCalendar, SessionWindow

    calendar = MarketCalendar(
        calendar_id="XNSE",
        timezone_name="Asia/Kolkata",
        weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(9, 15), time(15, 30)),)),
    )
    convention = MarketConvention(
        venue="XNSE",
        calendar_id=calendar.calendar_id,
        quote_currency="INR",
        settlement_currency="INR",
        multiplier=Decimal("1"),
        tick=TickSchedule.flat(Decimal("0.05")),
        lot=LotSpecification.single_units(),
        settlement=SettlementRule(SettlementBasis.TRADING_DAYS, 1),
    )
    assert convention.calendar_id == calendar.calendar_id
    assert settlement_date(convention.settlement, date(2026, 3, 13), calendar) == date(2026, 3, 16)
