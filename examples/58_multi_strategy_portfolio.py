"""
AlphaLab Examples
=================

Example 58 : A Multi-Strategy, Multi-Currency Portfolio

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 14 (money and currencies)
✓ Example 40 (a multi-asset book in four currencies)

Topics
------

• Three strategies, each kept by its own accounting state, trading in dollars,
  euros and yen
• One book of their sleeves, with an identity that follows its content
• Every instrument aggregated with its provenance: who holds how much, and
  where two strategies stand on opposite sides
• The book valued in dollars, per strategy, per instrument and per currency,
  every figure reconciling to the cent
• Every conversion recorded with its rate, source and time
• Reporting in euros: a yen holding needs a yen/euro rate, which is refused
  until the caller derives one through a named currency
• Valuations refused without rates, or with a rate dated after the valuation
• A strategy removed or replaced: a new book, the old one untouched

What this shows
---------------

Several strategies sharing one portfolio each keep their own books: their own
cash, positions, realized P&L and commissions, in whatever currencies they
traded. A multi-strategy book puts the sleeves side by side without merging
them, so every aggregate says which strategies it came from and every total
reconciles with the sleeves that make it up. Money stays in the currency it is
in until a valuation converts it, at a stated rate from a stated source, and a
figure that would need a rate nobody supplied is refused rather than invented.

Run

    python examples/58_multi_strategy_portfolio.py
"""

from decimal import Decimal

from _portfolio_world import (
    AS_OF,
    RATES,
    STRATEGIES,
    banner,
    book,
    quantity,
    refusal,
    section,
    strategy_state,
    utc,
)

from alphalab.portfolio import (
    NO_RATES,
    FutureDatedRateError,
    MissingRateError,
    MixedCurrencyValuationError,
    PortfolioEngine,
    StrategySleeve,
    value_book,
)
from alphalab.portfolio.amounts import CurrencyAmounts


def amounts(values: CurrencyAmounts) -> str:
    return ", ".join(f"{values.of(code):,} {code}" for code in values.currencies) or "none"


def main() -> None:
    banner(58, "A Multi-Strategy, Multi-Currency Portfolio")

    # ----------------------------------------------------------------- #
    # 1. Three strategies, three sets of books
    # ----------------------------------------------------------------- #

    section("1. Each strategy keeps its own books, in the currencies it traded")
    states = {strategy: strategy_state(strategy) for strategy in STRATEGIES}
    for strategy, state in states.items():
        held = ", ".join(
            f"{quantity(position.quantity, signed=True)} {asset}"
            for asset, position in sorted(state.positions.items())
        )
        print(f"  {strategy} ({state.account.base_currency} account)")
        cash = ", ".join(
            f"{amount:,} {code}" for code, amount in sorted(state.cash.balances.items())
        )
        print(f"    cash        : {cash}")
        print(f"    positions   : {held}")
        print(f"    realized    : {amounts(state.realized_pnl)}")
        print(f"    commissions : {amounts(state.commission_paid)}")

    # ----------------------------------------------------------------- #
    # 2. One book
    # ----------------------------------------------------------------- #

    section("2. One book of three sleeves")
    fund = book()
    print(f"  book        : {fund.portfolio_id}, {fund.book_id[:16]}")
    print(f"  strategies  : {', '.join(fund.strategies)}")
    print(f"  instruments : {', '.join(fund.instruments)}")
    print(f"  unassigned  : {amounts(fund.unassigned_cash)} (capital no strategy holds)")

    # ----------------------------------------------------------------- #
    # 3. Holdings, with provenance
    # ----------------------------------------------------------------- #

    section("3. Every instrument, aggregated, with who holds it")
    print(f"  {'':6}{'net':>9}{'long':>9}{'short':>9}{'crossed':>9}  held by")
    for holding in fund.holdings():
        owners = ", ".join(
            f"{part.strategy_id} {quantity(part.quantity, signed=True)}"
            for part in holding.contributions
        )
        flag = "  <- opposite sides" if holding.opposing else ""
        print(
            f"  {holding.asset_id:<6}{quantity(holding.net_quantity, signed=True):>9}"
            f"{quantity(holding.long_quantity):>9}{quantity(holding.short_quantity):>9}"
            f"{quantity(holding.crossed_quantity):>9}  {owners}{flag}"
        )
    print(
        "  'crossed' is quantity held long by one strategy and short by another: netting\n"
        "  it away would hide two positions that each carry their own risk and P&L."
    )

    # ----------------------------------------------------------------- #
    # 4. The book in dollars
    # ----------------------------------------------------------------- #

    section("4. The book valued in dollars")
    valuation = value_book(fund, reporting_currency="USD", rates=RATES, as_of=AS_OF)
    print(f"  {'':10}{'net':>14}{'gross':>14}{'cash':>14}{'NAV':>14}{'unrealized':>12}")
    for entry in valuation.strategies:
        print(
            f"  {entry.strategy_id:<10}{entry.net_exposure:>14,}{entry.gross_exposure:>14,}"
            f"{entry.cash:>14,}{entry.nav:>14,}{entry.unrealized_pnl:>12,}"
        )
    print(f"  {'unassigned':<10}{'':>14}{'':>14}{valuation.unassigned_cash:>14,}")
    print(
        f"  {'book':<10}{valuation.net_exposure:>14,}{valuation.gross_exposure:>14,}"
        f"{valuation.cash:>14,}{valuation.nav:>14,}"
    )
    strategies_nav = sum((entry.nav for entry in valuation.strategies), Decimal(0))
    lines_net = sum((line.reporting_value for line in valuation.lines), Decimal(0))
    print(
        f"  reconciles: strategies' NAV {strategies_nav:,} + unassigned "
        f"{valuation.unassigned_cash:,} = {strategies_nav + valuation.unassigned_cash:,}"
    )
    print(
        f"              the {len(valuation.lines)} lines net to {lines_net:,} = book net exposure"
    )
    for entry in valuation.strategies:
        print(
            f"  {entry.strategy_id:<10} realized {amounts(entry.realized_pnl)}"
            f" = {entry.reporting_realized_pnl:,} USD"
        )

    # ----------------------------------------------------------------- #
    # 5. Currencies, native and translated
    # ----------------------------------------------------------------- #

    section("5. Exposure per currency: settlement truth, then its translation")
    for code in valuation.exposure_by_currency.currencies:
        native = valuation.exposure_by_currency.of(code)
        translated = valuation.reporting_exposure_by_currency[code]
        print(f"  {code}: {native:>18,} {code}  -> {translated:>14,} USD")
    print(f"  {len(valuation.conversions)} conversions, each recorded; the first three:")
    for conversion in valuation.conversions[:3]:
        print(f"    {conversion.summary}")
        print(f"      at the rate of {utc(conversion.rate.as_of)}")

    # ----------------------------------------------------------------- #
    # 6. Reporting in euros needs a rate nobody quoted
    # ----------------------------------------------------------------- #

    section("6. Reporting in euros: a yen holding needs a yen/euro rate")
    try:
        value_book(fund, reporting_currency="EUR", rates=RATES, as_of=AS_OF)
    except MissingRateError as error:
        refusal("valued in EUR with dollar quotes only", error)
    cross = RATES.cross_rate("JPY", "EUR", via="USD")
    in_euros = value_book(fund, reporting_currency="EUR", rates=RATES.with_rate(cross), as_of=AS_OF)
    print(f"  derived deliberately: JPY/EUR {cross.rate:.10g} (derived={cross.derived})")
    print(f"    source: {cross.source}")
    print(f"  book NAV in euros   : {in_euros.nav:,} EUR  (in dollars: {valuation.nav:,} USD)")
    print(
        "  native exposures are identical in both reports: "
        f"{in_euros.exposure_by_currency == valuation.exposure_by_currency}"
    )

    # ----------------------------------------------------------------- #
    # 7. Valuations that are refused
    # ----------------------------------------------------------------- #

    section("7. No rates, or a rate from after the valuation, is refused")
    try:
        value_book(fund, reporting_currency="USD", rates=NO_RATES, as_of=AS_OF)
    except MixedCurrencyValuationError as error:
        refusal("a three-currency book with no rates", error)
    try:
        value_book(fund, reporting_currency="USD", rates=RATES, as_of=AS_OF - 7_200.0)
    except FutureDatedRateError as error:
        refusal("valued two hours before the fixing it would use", error)

    # ----------------------------------------------------------------- #
    # 8. Changing the book
    # ----------------------------------------------------------------- #

    section("8. A strategy removed or replaced is a new book")
    without_value = fund.without_strategy("VALUE")
    momentum = states["MOMENTUM"]
    trimmed = PortfolioEngine.apply_fill(
        momentum, "GINZ", Decimal("-30000"), Decimal("2840"), Decimal("150"), AS_OF, "JPY"
    )
    replaced = fund.with_updated_sleeve(StrategySleeve.from_portfolio_state("MOMENTUM", trimmed))
    print(f"  the book           : {fund.book_id[:16]}  {fund.strategies}")
    print(f"  without VALUE      : {without_value.book_id[:16]}  {without_value.strategies}")
    print(f"  MOMENTUM trims GINZ: {replaced.book_id[:16]}")
    before, after = fund.holding("GINZ"), replaced.holding("GINZ")
    print(
        f"  GINZ net           : {quantity(before.net_quantity, signed=True)} -> "
        f"{quantity(after.net_quantity, signed=True)}"
    )
    still = quantity(fund.holding("GINZ").net_quantity, signed=True)
    print(f"  the original book still holds {still} GINZ: values do not change under you")


if __name__ == "__main__":
    main()
