"""FX research: cross rates, forwards, carry, hedging and currency attribution."""

from datetime import date
from decimal import Decimal

import pytest

from alphalab.conventions import DayCount
from alphalab.portfolio.exceptions import PortfolioError
from alphalab.portfolio.fx import FutureDatedRateError, FxRate, FxRates, MissingRateError
from alphalab.portfolio.fx_research import (
    ForwardTerms,
    carry_rate,
    covered_forward_rate,
    currency_attribution,
    currency_exposures,
    forward_points,
    hedge_notional,
)
from alphalab.portfolio.position import Position


def _rate(base: str, quote: str, value: str, as_of: float = 0.0) -> FxRate:
    return FxRate(base, quote, Decimal(value), as_of, "ECB")


# --------------------------------------------------------------------------- #
# Cross rates
# --------------------------------------------------------------------------- #


def test_a_cross_is_derived_only_when_asked_and_says_what_it_went_through() -> None:
    table = FxRates.of([_rate("EUR", "USD", "1.10"), _rate("USD", "JPY", "150")])
    crossed = table.cross_rate("EUR", "JPY", via="USD")
    assert crossed.rate == Decimal("165.00")
    assert crossed.derived
    assert "via USD" in crossed.source


def test_convert_still_refuses_the_cross_it_was_not_given() -> None:
    """ADR-0020's refusal is unchanged: the table triangulates nothing."""

    table = FxRates.of([_rate("EUR", "USD", "1.10"), _rate("USD", "JPY", "150")])
    with pytest.raises(MissingRateError, match="does not triangulate"):
        table.convert(Decimal("100"), "EUR", "JPY")


def test_a_derived_cross_is_not_in_the_table_until_it_is_put_there() -> None:
    table = FxRates.of([_rate("EUR", "USD", "1.10"), _rate("USD", "JPY", "150")])
    crossed = table.cross_rate("EUR", "JPY", via="USD")
    assert table.rate_for("EUR", "JPY") is None
    assert table.with_rate(crossed).rate_for("EUR", "JPY") is crossed


def test_two_routes_give_two_answers_which_is_why_via_is_required() -> None:
    table = FxRates.of(
        [
            _rate("EUR", "USD", "1.10"),
            _rate("USD", "JPY", "150"),
            _rate("EUR", "GBP", "0.85"),
            _rate("GBP", "JPY", "196"),
        ]
    )
    assert (
        table.cross_rate("EUR", "JPY", via="USD").rate
        != table.cross_rate("EUR", "JPY", via="GBP").rate
    )


def test_a_cross_takes_the_older_of_its_two_legs() -> None:
    table = FxRates.of(
        [_rate("EUR", "USD", "1.10", as_of=10.0), _rate("USD", "JPY", "150", as_of=4.0)]
    )
    assert table.cross_rate("EUR", "JPY", via="USD").as_of == 4.0


def test_a_missing_leg_is_named() -> None:
    table = FxRates.of([_rate("EUR", "USD", "1.10")])
    with pytest.raises(MissingRateError, match="USD/JPY"):
        table.cross_rate("EUR", "JPY", via="USD")


def test_a_cross_through_one_of_its_own_ends_is_refused() -> None:
    table = FxRates.of([_rate("EUR", "USD", "1.10")])
    with pytest.raises(PortfolioError, match="its own ends"):
        table.cross_rate("EUR", "USD", via="USD")


# --------------------------------------------------------------------------- #
# The look-ahead guard
# --------------------------------------------------------------------------- #


def test_a_rate_dated_after_the_conversion_instant_is_refused() -> None:
    table = FxRates.of([_rate("EUR", "USD", "1.10", as_of=100.0)])
    with pytest.raises(FutureDatedRateError, match="look-ahead"):
        table.convert(Decimal("100"), "EUR", "USD", as_of=50.0)


def test_a_rate_at_exactly_the_conversion_instant_is_accepted() -> None:
    table = FxRates.of([_rate("EUR", "USD", "1.10", as_of=100.0)])
    assert table.convert(Decimal("100"), "EUR", "USD", as_of=100.0).converted == Decimal("110.00")


def test_a_caller_naming_no_instant_claims_none_and_is_not_guarded() -> None:
    table = FxRates.of([_rate("EUR", "USD", "1.10", as_of=100.0)])
    assert table.convert(Decimal("100"), "EUR", "USD").converted == Decimal("110.00")


# --------------------------------------------------------------------------- #
# Forwards and carry
# --------------------------------------------------------------------------- #


def _terms(base_rate: float = 0.05, quote_rate: float = 0.02) -> ForwardTerms:
    return ForwardTerms(
        value_date=date(2026, 7, 2),
        spot_date=date(2026, 1, 2),
        base_rate=base_rate,
        quote_rate=quote_rate,
        basis=DayCount.ACT_360,
    )


def test_a_forward_settling_before_spot_is_refused() -> None:
    with pytest.raises(PortfolioError, match="inverts the carry"):
        ForwardTerms(date(2026, 1, 2), date(2026, 7, 2), 0.05, 0.02, DayCount.ACT_360)


def test_the_higher_yielding_base_trades_at_a_forward_discount() -> None:
    spot = _rate("EUR", "USD", "1.10")
    forward = covered_forward_rate(spot, _terms(base_rate=0.05, quote_rate=0.02))
    assert forward.rate < spot.rate
    assert forward_points(spot, forward) < Decimal("0")


def test_the_lower_yielding_base_trades_at_a_forward_premium() -> None:
    spot = _rate("EUR", "USD", "1.10")
    forward = covered_forward_rate(spot, _terms(base_rate=0.01, quote_rate=0.04))
    assert forward.rate > spot.rate
    assert forward_points(spot, forward) > Decimal("0")


def test_a_computed_forward_is_marked_derived_and_names_both_rates() -> None:
    forward = covered_forward_rate(_rate("EUR", "USD", "1.10"), _terms())
    assert forward.derived
    assert "covered parity" in forward.source
    assert "ACT_360" in forward.source


def test_a_forward_is_known_no_later_than_the_spot_it_came_from() -> None:
    spot = _rate("EUR", "USD", "1.10", as_of=42.0)
    assert covered_forward_rate(spot, _terms()).as_of == 42.0


def test_the_day_count_basis_changes_the_forward() -> None:
    spot = _rate("EUR", "USD", "1.10")
    by_360 = covered_forward_rate(spot, _terms())
    terms = ForwardTerms(date(2026, 7, 2), date(2026, 1, 2), 0.05, 0.02, DayCount.ACT_365_FIXED)
    assert covered_forward_rate(spot, terms).rate != by_360.rate


def test_forward_points_refuse_two_different_pairs() -> None:
    with pytest.raises(PortfolioError, match="not forward points"):
        forward_points(_rate("EUR", "USD", "1.10"), _rate("EUR", "GBP", "0.85"))


def test_carry_is_the_interest_differential_and_the_opposite_sign_to_points() -> None:
    spot = _rate("EUR", "USD", "1.10")
    terms = _terms(base_rate=0.05, quote_rate=0.02)
    assert carry_rate(terms) == pytest.approx(0.03)
    assert forward_points(spot, covered_forward_rate(spot, terms)) < Decimal("0")


# --------------------------------------------------------------------------- #
# Exposure and hedging
# --------------------------------------------------------------------------- #


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


def test_exposures_are_reported_per_currency_and_never_summed() -> None:
    positions = {
        "A": _position("A", "100", "10", "USD"),
        "B": _position("B", "50", "20", "EUR"),
        "C": _position("C", "-25", "40", "EUR"),
    }
    exposures = currency_exposures(positions)
    assert exposures.of("USD") == Decimal("1000")
    assert exposures.of("EUR") == Decimal("0")  # 1000 long, 1000 short
    assert set(exposures.currencies) == {"USD", "EUR"}


def test_an_empty_book_has_no_currencies() -> None:
    assert currency_exposures({}).currencies == ()


def test_a_hedge_ratio_is_required_and_scales_the_notional() -> None:
    assert hedge_notional(Decimal("1000"), Decimal("1")) == Decimal("1000.00")
    assert hedge_notional(Decimal("1000"), Decimal("0.5")) == Decimal("500.00")
    # An over-hedge is a real, deliberate position.
    assert hedge_notional(Decimal("1000"), Decimal("1.2")) == Decimal("1200.00")


def test_a_negative_hedge_ratio_is_refused() -> None:
    with pytest.raises(PortfolioError, match="hides what the book is doing"):
        hedge_notional(Decimal("1000"), Decimal("-1"))


# --------------------------------------------------------------------------- #
# Currency attribution
# --------------------------------------------------------------------------- #


def _attribution(opening_rate: str = "1.10", closing_rate: str = "1.20") -> object:
    return currency_attribution(
        opening={"EUR": Decimal("1000"), "USD": Decimal("500")},
        closing={"EUR": Decimal("1100"), "USD": Decimal("500")},
        opening_rates=FxRates.of([_rate("EUR", "USD", opening_rate, as_of=0.0)]),
        closing_rates=FxRates.of([_rate("EUR", "USD", closing_rate, as_of=10.0)]),
        reporting_currency="USD",
        opening_timestamp=0.0,
        closing_timestamp=10.0,
    )


def test_the_two_components_sum_to_the_change_in_reporting_value() -> None:
    report = _attribution()
    euro = next(entry for entry in report.by_currency if entry.currency == "EUR")  # type: ignore[attr-defined]
    # Local: (1100 - 1000) * 1.10 = 110. Currency: 1100 * (1.20 - 1.10) = 110.
    assert euro.local_return == Decimal("110.00")
    assert euro.currency_return == Decimal("110.00")
    assert euro.total == Decimal("220.00")
    assert euro.closing_reporting - euro.opening_reporting == Decimal("220.00")
    assert euro.rounding == Decimal("0.00")


def test_the_reporting_currency_itself_needs_no_rate_and_moves_only_locally() -> None:
    report = _attribution()
    usd = next(entry for entry in report.by_currency if entry.currency == "USD")  # type: ignore[attr-defined]
    assert usd.currency_return == Decimal("0.00")
    assert usd.opening_rate.source == "identity"


def test_the_report_totals_are_the_sums_of_its_entries() -> None:
    report = _attribution()
    assert report.local_return == sum(  # type: ignore[attr-defined]
        entry.local_return
        for entry in report.by_currency  # type: ignore[attr-defined]
    )
    assert report.total == report.local_return + report.currency_return  # type: ignore[attr-defined]


def test_one_table_for_both_instants_reports_no_currency_effect() -> None:
    """Which is the mistake the two-table signature exists to make visible."""

    table = FxRates.of([_rate("EUR", "USD", "1.10", as_of=0.0)])
    report = currency_attribution(
        {"EUR": Decimal("1000")},
        {"EUR": Decimal("1100")},
        table,
        table,
        "USD",
        0.0,
        10.0,
    )
    assert report.currency_return == Decimal("0.00")


def test_a_currency_that_left_the_book_must_be_stated_as_zero() -> None:
    with pytest.raises(PortfolioError, match="without a trace"):
        currency_attribution(
            {"EUR": Decimal("1000")},
            {},
            FxRates.of([_rate("EUR", "USD", "1.10")]),
            FxRates.of([_rate("EUR", "USD", "1.20", as_of=10.0)]),
            "USD",
            0.0,
            10.0,
        )


def test_a_currency_that_arrived_opens_at_zero() -> None:
    report = currency_attribution(
        {},
        {"EUR": Decimal("1000")},
        FxRates.of([_rate("EUR", "USD", "1.10")]),
        FxRates.of([_rate("EUR", "USD", "1.20", as_of=10.0)]),
        "USD",
        0.0,
        10.0,
    )
    euro = report.by_currency[0]
    assert euro.opening_local == Decimal("0")
    assert euro.local_return == Decimal("1100.00")


def test_a_backwards_period_is_refused() -> None:
    with pytest.raises(PortfolioError, match="runs forwards"):
        currency_attribution({}, {}, FxRates(), FxRates(), "USD", 10.0, 0.0)


def test_a_missing_rate_is_refused_rather_than_invented() -> None:
    with pytest.raises(MissingRateError):
        currency_attribution(
            {"JPY": Decimal("1000")},
            {"JPY": Decimal("1000")},
            FxRates.of([_rate("EUR", "USD", "1.10")]),
            FxRates.of([_rate("EUR", "USD", "1.20", as_of=10.0)]),
            "USD",
            0.0,
            10.0,
        )


def test_a_closing_rate_dated_after_the_closing_instant_is_refused() -> None:
    with pytest.raises(FutureDatedRateError):
        currency_attribution(
            {"EUR": Decimal("1000")},
            {"EUR": Decimal("1000")},
            FxRates.of([_rate("EUR", "USD", "1.10", as_of=0.0)]),
            FxRates.of([_rate("EUR", "USD", "1.20", as_of=999.0)]),
            "USD",
            0.0,
            10.0,
        )


def test_the_attribution_is_deterministic_and_ordered_by_currency() -> None:
    first, second = _attribution(), _attribution()
    assert first == second
    assert [entry.currency for entry in first.by_currency] == ["EUR", "USD"]  # type: ignore[attr-defined]
