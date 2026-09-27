"""A book of independent strategies: provenance kept, totals reconciled, currencies honest."""

from __future__ import annotations

from decimal import Decimal

import pytest

from alphalab.portfolio.account import Account
from alphalab.portfolio.amounts import CurrencyAmounts
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.exceptions import MixedCurrencyValuationError, PortfolioError
from alphalab.portfolio.fx import (
    NO_RATES,
    FutureDatedRateError,
    FxRate,
    FxRates,
    MissingRateError,
    StaleRateError,
)
from alphalab.portfolio.multi_strategy import MultiStrategyBook, StrategySleeve, value_book
from alphalab.portfolio.position import Position

AS_OF = 1_000.0
RATES = FxRates.of([FxRate("EUR", "USD", Decimal("1.10"), 900.0, "unit desk")])


def position(
    asset: str, quantity: str, price: str, currency: str = "USD", cost: str | None = None
) -> Position:
    return Position(
        asset,
        Decimal(quantity),
        Decimal(cost or price),
        Decimal(price),
        Decimal("0"),
        currency,
        AS_OF,
    )


def sleeve(
    strategy: str, *positions: Position, cash: str = "0", realized: str = "0"
) -> StrategySleeve:
    return StrategySleeve(
        strategy_id=strategy,
        positions={p.asset_id: p for p in positions},
        cash=CurrencyAmounts.single(Decimal(cash), "USD") if cash != "0" else CurrencyAmounts(),
        realized_pnl=CurrencyAmounts.single(Decimal(realized), "USD")
        if realized != "0"
        else CurrencyAmounts(),
        commission_paid=CurrencyAmounts(),
    )


MOM = sleeve(
    "MOM",
    position("AAPL", "100", "200.00", cost="180.00"),
    position("SAP", "50", "120.00", "EUR", cost="110.00"),
    cash="5000",
    realized="250",
)
MR = sleeve(
    "MR",
    position("AAPL", "-40", "200.00", cost="210.00"),
    position("MSFT", "30", "400.00"),
    cash="10000",
)
BOOK = MultiStrategyBook("P1", (MR, MOM), CurrencyAmounts.single(Decimal("1000"), "EUR"))


# --------------------------------------------------------------------------- #
# The book
# --------------------------------------------------------------------------- #


def test_sleeves_are_held_in_strategy_order_and_named_once() -> None:
    assert BOOK.strategies == ("MOM", "MR")
    assert BOOK.instruments == ("AAPL", "MSFT", "SAP")
    with pytest.raises(PortfolioError, match="two sleeves"):
        MultiStrategyBook("P1", (MOM, MOM), CurrencyAmounts())


def test_a_shared_instrument_keeps_every_holder_and_nets_exactly() -> None:
    holding = BOOK.holding("AAPL")

    assert [(c.strategy_id, c.quantity) for c in holding.contributions] == [
        ("MOM", Decimal("100")),
        ("MR", Decimal("-40")),
    ]
    assert holding.net_quantity == Decimal("60")
    assert holding.net_quantity == sum((c.quantity for c in holding.contributions), Decimal(0))
    assert (holding.long_quantity, holding.short_quantity, holding.crossed_quantity) == (
        Decimal("100"),
        Decimal("40"),
        Decimal("40"),
    )
    assert holding.opposing
    assert holding.strategies == ("MOM", "MR")


def test_an_unshared_instrument_has_one_holder_and_nothing_crossed() -> None:
    holding = BOOK.holding("MSFT")

    assert holding.strategies == ("MR",)
    assert not holding.opposing


def test_one_instrument_at_two_marks_is_refused_not_averaged() -> None:
    other = sleeve("OTHER", position("AAPL", "10", "201.00"))
    with pytest.raises(PortfolioError, match="Mark every sleeve at one price"):
        BOOK.with_sleeve(other).holdings()


def test_an_instrument_nobody_holds_is_refused() -> None:
    with pytest.raises(PortfolioError, match="No strategy"):
        BOOK.holding("TSLA")


def test_adding_updating_and_removing_a_strategy() -> None:
    carry = sleeve("CARRY", position("MSFT", "5", "400.00"))
    grown = BOOK.with_sleeve(carry)
    replaced = grown.with_updated_sleeve(sleeve("CARRY", position("MSFT", "7", "400.00")))

    assert grown.strategies == ("CARRY", "MOM", "MR")
    assert replaced.holding("MSFT").net_quantity == Decimal("37")
    assert replaced.without_strategy("CARRY") == BOOK
    with pytest.raises(PortfolioError, match="already in the book"):
        grown.with_sleeve(carry)
    with pytest.raises(PortfolioError, match="no sleeve"):
        BOOK.without_strategy("CARRY")


def test_removing_a_strategy_leaves_every_other_figure_unchanged() -> None:
    full = value_book(BOOK, reporting_currency="USD", rates=RATES, as_of=AS_OF)
    without = value_book(
        BOOK.without_strategy("MR"), reporting_currency="USD", rates=RATES, as_of=AS_OF
    )

    assert without.strategy("MOM") == full.strategy("MOM")
    assert without.net_exposure == full.net_exposure - full.strategy("MR").net_exposure
    assert without.nav == full.nav - full.strategy("MR").nav


def test_a_sleeve_refuses_a_position_filed_under_another_asset() -> None:
    with pytest.raises(PortfolioError, match="under its own id"):
        StrategySleeve(
            "S",
            {"AAPL": position("MSFT", "1", "1.00")},
            CurrencyAmounts(),
            CurrencyAmounts(),
            CurrencyAmounts(),
        )
    with pytest.raises(PortfolioError, match="non-blank"):
        StrategySleeve(" ", {}, CurrencyAmounts(), CurrencyAmounts(), CurrencyAmounts())


def test_a_sleeve_is_lifted_from_a_strategy_s_own_portfolio_state() -> None:
    state = PortfolioState(account=Account("A", "USD", "acct", 0.0))
    state = PortfolioEngine.apply_deposit(state, Decimal("10000"), "USD", 1.0)
    lifted = StrategySleeve.from_portfolio_state("MOM", state)

    assert lifted.cash.of("USD") == Decimal("10000.00")
    assert lifted.positions == {}
    assert lifted.realized_pnl == state.realized_pnl


def test_the_book_identity_follows_its_content() -> None:
    assert (
        BOOK.book_id
        == MultiStrategyBook(
            "P1", (MOM, MR), CurrencyAmounts.single(Decimal("1000"), "EUR")
        ).book_id
    )
    assert BOOK.book_id != BOOK.without_strategy("MR").book_id


# --------------------------------------------------------------------------- #
# Valuation
# --------------------------------------------------------------------------- #


def test_every_holding_is_valued_from_the_canonical_position_and_converted_once() -> None:
    valuation = value_book(BOOK, reporting_currency="USD", rates=RATES, as_of=AS_OF)
    sap = next(line for line in valuation.lines if line.asset_id == "SAP")

    assert sap.market_value == MOM.positions["SAP"].market_value == Decimal("6000.00")
    assert sap.reporting_value == Decimal("6600.00")
    assert sap.conversion is not None and sap.conversion.rate.source == "unit desk"
    assert sap.unrealized_pnl == Decimal("500.00")
    assert sap.reporting_unrealized_pnl == Decimal("550.00")
    assert all(line.conversion is None for line in valuation.lines if line.currency == "USD")


def test_every_breakdown_reconciles_to_the_cent() -> None:
    valuation = value_book(BOOK, reporting_currency="USD", rates=RATES, as_of=AS_OF)

    by_strategy = sum((entry.net_exposure for entry in valuation.strategies), Decimal(0))
    by_instrument = sum((entry.reporting_value for entry in valuation.instruments), Decimal(0))
    by_currency = sum(valuation.reporting_exposure_by_currency.values(), Decimal(0))
    by_line = sum((line.reporting_value for line in valuation.lines), Decimal(0))

    assert by_strategy == by_instrument == by_currency == by_line == valuation.net_exposure
    assert valuation.net_exposure == Decimal("30600.00")
    assert (
        valuation.nav
        == sum((entry.nav for entry in valuation.strategies), Decimal(0))
        + valuation.unassigned_cash
    )
    # Cash 5,000 + 10,000 + the unassigned 1,000 EUR at 1.10, plus 30,600 net.
    assert valuation.cash == Decimal("16100.00")
    assert valuation.nav == Decimal("46700.00")


def test_native_exposure_stays_per_currency_and_is_never_totalled() -> None:
    valuation = value_book(BOOK, reporting_currency="USD", rates=RATES, as_of=AS_OF)

    assert dict(valuation.exposure_by_currency) == {
        "EUR": Decimal("6000.00"),
        "USD": Decimal("24000.00"),
    }
    assert valuation.reporting_exposure_by_currency == {
        "EUR": Decimal("6600.00"),
        "USD": Decimal("24000.00"),
    }


def test_strategy_figures_keep_long_short_and_results_apart() -> None:
    valuation = value_book(BOOK, reporting_currency="USD", rates=RATES, as_of=AS_OF)
    mr = valuation.strategy("MR")
    mom = valuation.strategy("MOM")

    assert (mr.long_exposure, mr.short_exposure, mr.net_exposure, mr.gross_exposure) == (
        Decimal("12000.00"),
        Decimal("8000.00"),
        Decimal("4000.00"),
        Decimal("20000.00"),
    )
    assert mr.unrealized_pnl == Decimal("400.00")
    assert mom.realized_pnl.of("USD") == Decimal("250.00")
    assert mom.reporting_realized_pnl == Decimal("250.00")
    assert mom.unrealized_pnl == Decimal("2000.00") + Decimal("550.00")


def test_an_instrument_s_value_is_the_sum_of_its_holders_with_rounding_accounted() -> None:
    odd = MultiStrategyBook(
        "P",
        (
            sleeve("A", position("X", "0.333", "10.015")),
            sleeve("B", position("X", "0.333", "10.015")),
        ),
        CurrencyAmounts(),
    )
    instrument = value_book(odd, reporting_currency="USD", rates=NO_RATES, as_of=AS_OF).instrument(
        "X"
    )

    assert instrument.market_value == Decimal("6.66")
    assert abs(instrument.rounding) <= Decimal("0.01")


def test_a_book_in_the_reporting_currency_needs_no_rates() -> None:
    usd = MultiStrategyBook("P", (MR,), CurrencyAmounts())
    valuation = value_book(usd, reporting_currency="USD", rates=NO_RATES, as_of=AS_OF)

    assert valuation.conversions == ()
    assert valuation.nav == Decimal("14000.00")


def test_a_mixed_book_without_rates_is_refused() -> None:
    with pytest.raises(MixedCurrencyValuationError, match="no FX rates were supplied"):
        value_book(BOOK, reporting_currency="USD", rates=NO_RATES, as_of=AS_OF)


def test_missing_stale_and_future_dated_rates_are_refused_by_the_canonical_authority() -> None:
    with pytest.raises(MissingRateError):
        value_book(
            BOOK,
            reporting_currency="USD",
            rates=FxRates.of([FxRate("GBP", "USD", Decimal("1.3"), 900.0, "x")]),
            as_of=AS_OF,
        )
    with pytest.raises(StaleRateError):
        value_book(
            BOOK,
            reporting_currency="USD",
            rates=FxRates.of(RATES.rates.values(), max_age_seconds=10.0),
            as_of=AS_OF,
        )
    with pytest.raises(FutureDatedRateError):
        value_book(BOOK, reporting_currency="USD", rates=RATES, as_of=800.0)


def test_every_conversion_is_recorded_and_the_valuation_identity_names_the_rates() -> None:
    first = value_book(BOOK, reporting_currency="USD", rates=RATES, as_of=AS_OF)
    other_rate = value_book(
        BOOK,
        reporting_currency="USD",
        rates=FxRates.of([FxRate("EUR", "USD", Decimal("1.11"), 900.0, "unit desk")]),
        as_of=AS_OF,
    )

    assert {c.rate.base for c in first.conversions} == {"EUR"}
    # SAP's value, SAP's open P&L and the unassigned euros: every euro figure,
    # once each. Nothing in dollars converts.
    assert [(c.amount, c.rate.base) for c in first.conversions] == [
        (Decimal("6000.00"), "EUR"),
        (Decimal("500.00"), "EUR"),
        (Decimal("1000.00"), "EUR"),
    ]
    assert (
        first.valuation_id
        == value_book(BOOK, reporting_currency="USD", rates=RATES, as_of=AS_OF).valuation_id
    )
    assert first.valuation_id != other_rate.valuation_id


def test_a_valuation_can_be_reported_in_any_currency_it_has_rates_for() -> None:
    to_eur = FxRates.of([FxRate("USD", "EUR", Decimal("0.90"), 900.0, "unit desk")])
    valuation = value_book(BOOK, reporting_currency="EUR", rates=to_eur, as_of=AS_OF)

    assert valuation.reporting_currency == "EUR"
    assert valuation.reporting_exposure_by_currency["EUR"] == Decimal("6000.00")
    assert valuation.reporting_exposure_by_currency["USD"] == Decimal("21600.00")


def test_a_book_with_no_strategy_is_its_unassigned_cash() -> None:
    """An empty portfolio is a state, not an error: no holdings, no lines, its cash."""

    empty = MultiStrategyBook("P0", (), CurrencyAmounts())
    funded = MultiStrategyBook("P0", (), CurrencyAmounts.single(Decimal("250"), "USD"))

    assert empty.holdings() == () and empty.strategies == ()
    valuation = value_book(empty, reporting_currency="USD", rates=NO_RATES, as_of=AS_OF)
    assert (valuation.nav, valuation.lines, valuation.strategies) == (Decimal("0.00"), (), ())
    assert value_book(funded, reporting_currency="USD", rates=NO_RATES, as_of=AS_OF).nav == Decimal(
        "250.00"
    )
    assert empty.book_id != funded.book_id
