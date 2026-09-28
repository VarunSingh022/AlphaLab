"""A mixed-currency book converts each currency's totals once (ledger PRF-001).

Until v3.10 a book holding positions in several currencies converted every
position separately on every valuation -- a conversion, and a rounding, per
position, and a cost that grew with the book. The book now keeps exact totals
per currency, so a valuation converts each currency's long value, short value
and unrealized P&L once: one rounding per figure, whatever the book holds.
"""

from decimal import Decimal

from alphalab.portfolio.account import Account
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.fx import FxRate, FxRates
from alphalab.portfolio.nav import NAVCalculator
from alphalab.portfolio.valuation import PortfolioValuation

RATES = FxRates.of([FxRate("EUR", "USD", Decimal("1.0833"), 0.0, "TEST")])


def _book() -> PortfolioState:
    state = PortfolioState(account=Account("ACC", "USD", "Mixed", 0.0))
    state = PortfolioEngine.apply_deposit(state, Decimal("100000"), "USD", 0.0)
    state = PortfolioEngine.apply_deposit(state, Decimal("50000"), "EUR", 0.0)
    fills = [
        ("E1", Decimal("3"), Decimal("10.01"), "EUR"),
        ("E2", Decimal("7"), Decimal("20.03"), "EUR"),
        ("E3", Decimal("-5"), Decimal("30.07"), "EUR"),
        ("U1", Decimal("4"), Decimal("50.00"), "USD"),
    ]
    for asset, quantity, price, currency in fills:
        state = PortfolioEngine.apply_fill(
            state, asset, quantity, price, Decimal("0"), 1.0, currency
        )
    prices = {
        "E1": Decimal("10.11"),
        "E2": Decimal("20.13"),
        "E3": Decimal("29.97"),
        "U1": Decimal("51"),
    }
    return PortfolioEngine.update_market_prices(state, prices, 2.0)


def test_each_currencys_totals_are_converted_once() -> None:
    state = _book()

    valuation = PortfolioValuation.snapshot(state, 2.0, "USD", RATES)

    # EUR longs: 3 x 10.11 + 7 x 20.13 = 30.33 + 140.91 = 171.24 EUR.
    # EUR shorts: -5 x 29.97 = -149.85 EUR. One conversion each, rounded once.
    eur_long = (Decimal("171.24") * Decimal("1.0833")).quantize(Decimal("0.01"))
    eur_short = (Decimal("-149.85") * Decimal("1.0833")).quantize(Decimal("0.01"))
    assert valuation.long_value == Decimal("204.00") + eur_long  # U1: 4 x 51
    assert valuation.short_value == eur_short
    position_conversions = [
        c for c in valuation.conversions if c.amount in {Decimal("171.24"), Decimal("-149.85")}
    ]
    assert len(position_conversions) == 2


def test_nav_is_the_valuations_equity() -> None:
    state = _book()

    valuation = PortfolioValuation.snapshot(state, 2.0, "USD", RATES)
    nav = NAVCalculator.calculate(state.cash, state.positions, "USD", RATES, 2.0)

    assert nav == valuation.equity


def test_adding_positions_in_a_currency_adds_no_conversions() -> None:
    state = _book()
    before = len(PortfolioValuation.snapshot(state, 2.0, "USD", RATES).conversions)
    for index in range(20):
        state = PortfolioEngine.apply_fill(
            state, f"X{index}", Decimal("1"), Decimal("10"), Decimal("0"), 3.0, "EUR"
        )

    after = len(PortfolioValuation.snapshot(state, 3.0, "USD", RATES).conversions)

    assert after == before
