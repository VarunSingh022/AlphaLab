"""
AlphaLab Examples
=================

Example 40 : Multi-Asset Portfolio Research

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 31 (global market conventions)
✓ Example 33 (futures contracts and rolls)
✓ Example 37 (FX research)

Topics
------

• Five asset classes in one book, in four currencies, across four venues
• The multiplier applied exactly once -- and what it looks like when it is not
• Notional in the quote currency; settlement conversion with a recorded rate
• Sessions read in each venue's own local time
• The book's return split into what the assets did and what the currencies did

What this shows
---------------

Every part of this has its own example. This one is about the *seams* between
them, which is where the errors the v3.4 brief names actually live: a multiplier
applied twice, a notional attributed to the wrong currency, a session read in
the wrong timezone.

`Position` has no multiplier and never will -- `market_value` is
`quantity * market_price`, which is exactly right for a share and a thousand
times wrong for a contract on a thousand barrels. `ContractHolding` pairs a
position with the convention that says what its numbers mean, and every figure
goes through the one site that multiplies.

Run

    python examples/40_multi_asset_portfolio.py
"""

from datetime import UTC, date, datetime, time
from decimal import Decimal

from alphalab.conventions import (
    LotSpecification,
    MarketConvention,
    SettlementBasis,
    SettlementRule,
    TickSchedule,
    settlement_date,
)
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.portfolio.contracts import (
    ContractHolding,
    contract_exposures,
    settlement_exposures,
)
from alphalab.portfolio.exposure import ExposureEngine
from alphalab.portfolio.fx import FxRate, FxRates
from alphalab.portfolio.fx_research import currency_attribution
from alphalab.portfolio.position import Position

OPEN = datetime(2026, 3, 16, 12, 0, tzinfo=UTC).timestamp()
CLOSE = OPEN + 86400.0

CALENDARS = {
    "XNAS": MarketCalendar(
        calendar_id="XNAS",
        timezone_name="America/New_York",
        weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(9, 30), time(16, 0)),)),
    ),
    "XCME": MarketCalendar(
        calendar_id="XCME",
        timezone_name="America/Chicago",
        weekly_sessions=dict.fromkeys(range(5), (SessionWindow(time(17, 0), time(16, 0)),)),
    ),
    "XOSE": MarketCalendar(
        calendar_id="XOSE",
        timezone_name="Asia/Tokyo",
        weekly_sessions=dict.fromkeys(
            range(5),
            (SessionWindow(time(9, 0), time(11, 30)), SessionWindow(time(12, 30), time(15, 15))),
        ),
    ),
    "BINANCE": MarketCalendar.continuous("BINANCE", "UTC"),
}


def convention(
    venue: str,
    quote: str,
    settle: str,
    multiplier: str,
    tick: str,
    lot: LotSpecification,
    settlement: SettlementRule,
) -> MarketConvention:
    return MarketConvention(
        venue=venue,
        calendar_id=venue,
        quote_currency=quote,
        settlement_currency=settle,
        multiplier=Decimal(multiplier),
        tick=TickSchedule.flat(Decimal(tick)),
        lot=lot,
        settlement=settlement,
    )


SINGLE = LotSpecification.single_units()
T1 = SettlementRule(SettlementBasis.TRADING_DAYS, 1)
T0 = SettlementRule(SettlementBasis.TRADE_DATE, 0)

EQUITY = convention("XNAS", "USD", "USD", "1", "0.01", SINGLE, T1)
CRUDE = convention("XCME", "USD", "USD", "1000", "0.01", SINGLE, T0)
NIKKEI_OPTION = convention("XOSE", "JPY", "JPY", "1000", "1", SINGLE, T1)
NIKKEI_DOLLAR = convention("XCME", "JPY", "USD", "5", "5", SINGLE, T0)
PERPETUAL = convention(
    "BINANCE",
    "USDT",
    "USDT",
    "1",
    "0.1",
    LotSpecification(Decimal("0.001"), Decimal("0.001")),
    T0,
)


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def position(asset_id: str, quantity: str, price: str, currency: str, at: float) -> Position:
    return Position(
        asset_id=asset_id,
        quantity=Decimal(quantity),
        average_cost=Decimal(price),
        market_price=Decimal(price),
        realized_pnl=Decimal("0"),
        currency=currency,
        last_updated=at,
    )


def book(marks: dict[str, str], at: float) -> list[ContractHolding]:
    """The same five holdings, marked at whatever prices are supplied."""

    return [
        ContractHolding(position("AAPL", "400", marks["AAPL"], "USD", at), EQUITY),
        ContractHolding(position("CL_202606", "5", marks["CL_202606"], "USD", at), CRUDE),
        ContractHolding(
            position("NK_C_38000", "-4", marks["NK_C_38000"], "JPY", at), NIKKEI_OPTION
        ),
        ContractHolding(position("NKD_202606", "3", marks["NKD_202606"], "USD", at), NIKKEI_DOLLAR),
        ContractHolding(
            position("BTCUSDT_PERP", "1.5", marks["BTCUSDT_PERP"], "USDT", at), PERPETUAL
        ),
    ]


OPENING_MARKS = {
    "AAPL": "205.00",
    "CL_202606": "75.00",
    "NK_C_38000": "450",
    "NKD_202606": "38000",
    "BTCUSDT_PERP": "60000.0",
}
CLOSING_MARKS = {
    "AAPL": "208.50",
    "CL_202606": "76.20",
    "NK_C_38000": "415",
    "NKD_202606": "38400",
    "BTCUSDT_PERP": "61200.0",
}


def main() -> None:
    print("=" * 68)
    print("Example 40 : Multi-Asset Portfolio Research")
    print("=" * 68)

    holdings = book(OPENING_MARKS, OPEN)

    # ----------------------------------------------------------------- #
    rule("Five instruments, four venues, four currencies")

    print(
        f"  {'instrument':<14} {'venue':<8} {'qty':>7} {'price':>10} "
        f"{'mult':>6} {'quote':>6} {'settles':>8}"
    )
    print("  " + "-" * 66)
    for holding in holdings:
        print(
            f"  {holding.position.asset_id:<14} {holding.convention.venue:<8} "
            f"{holding.position.quantity:>7} {holding.position.market_price:>10} "
            f"{holding.convention.multiplier:>6} {holding.convention.quote_currency:>6} "
            f"{holding.convention.settlement_currency:>8}"
        )

    # ----------------------------------------------------------------- #
    rule("Sessions: one instant, four local answers")

    reading = datetime.fromtimestamp(OPEN, tz=UTC)
    print(f"  at {reading:%Y-%m-%d %H:%M} UTC")
    for venue, calendar in CALENDARS.items():
        local = calendar.local_datetime(OPEN)
        state = "OPEN" if calendar.is_open(OPEN) else "shut"
        print(f"    {venue:<9} {local:%Y-%m-%d %H:%M} local   {state}")

    print()
    trade_date = date(2026, 3, 13)
    print(f"  a trade struck {trade_date} settles:")
    for holding in holdings:
        convention_of = holding.convention
        calendar = CALENDARS[convention_of.calendar_id]
        needed = calendar if convention_of.settlement.needs_calendar else None
        settles = settlement_date(convention_of.settlement, trade_date, needed)
        print(
            f"    {holding.position.asset_id:<14} {convention_of.settlement.label:<22} -> {settles}"
        )

    # ----------------------------------------------------------------- #
    rule("The multiplier, applied once")

    exposure = contract_exposures(holdings)
    plain = {holding.position.asset_id: holding.position for holding in holdings}
    print(
        f"  {'instrument':<14} {'Position.market_value':>22} {'contract notional':>20} "
        f"{'units':>12}"
    )
    print("  " + "-" * 72)
    for holding in holdings:
        asset_id = holding.position.asset_id
        shares = holding.position.market_value
        notional = exposure.by_asset[asset_id]
        print(
            f"  {asset_id:<14} {shares:>22} {notional.amount:>20} {notional.underlying_units:>12}"
        )
    print()
    print(
        f"  ExposureEngine.gross_exposure (no multiplier) : {ExposureEngine.gross_exposure(plain)}"
    )
    print("  That figure mixes four currencies and ignores every multiplier. It")
    print("  is not wrong for shares -- it is the right answer to a different")
    print("  question, and `Position` carries no multiplier to tell them apart.")

    # ----------------------------------------------------------------- #
    rule("Exposure per currency: net offsets, gross does not")

    print(f"  {'currency':<10} {'net':>18} {'gross':>18}")
    print("  " + "-" * 48)
    for currency in exposure.gross.currencies:
        print(f"  {currency:<10} {exposure.net.of(currency):>18} {exposure.gross.of(currency):>18}")
    print()
    print("  Three *quote* currencies, three figures, never a total. The short")
    print("  Nikkei option keeps its direction: negative in net, positive in")
    print("  gross. A fourth currency -- the dollars the NKD future settles in --")
    print("  is not here at all, because a notional is denominated in the currency")
    print("  the price is quoted in, and reaching the settlement one is FX.")

    # ----------------------------------------------------------------- #
    rule("Settlement currency: one conversion, with the rate recorded")

    rates = FxRates.of(
        [
            FxRate("JPY", "USD", Decimal("0.006614"), OPEN, "declared-by-this-example"),
            FxRate("USDT", "USD", Decimal("0.9998"), OPEN, "declared-by-this-example"),
        ]
    )
    print(f"  {'instrument':<14} {'quoted':>18} {'ccy':>5} {'settled':>16} {'rate used':>26}")
    print("  " + "-" * 84)
    for row in settlement_exposures(holdings, rates, as_of=OPEN):
        if row.conversion is None:
            source = "(none needed)"
        else:
            source = f"{row.conversion.rate.rate} from {row.conversion.rate.source!r}"
        print(
            f"  {row.asset_id:<14} {row.quoted.amount:>18} {row.quoted.currency:>5} "
            f"{row.settled:>16} {source:>26}"
        )
    print()
    print("  Only the dollar-settled Nikkei future converts. The others are quoted")
    print("  and settled in one currency, and `conversion is None` says so --")
    print("  which is a different fact from converting at 1.0.")

    # ----------------------------------------------------------------- #
    rule("A day later: what the assets did, and what the currencies did")

    closing_rates = FxRates.of(
        [
            FxRate("JPY", "USD", Decimal("0.006480"), CLOSE, "declared-by-this-example"),
            FxRate("USDT", "USD", Decimal("1.0003"), CLOSE, "declared-by-this-example"),
        ]
    )
    opening_exposure = contract_exposures(book(OPENING_MARKS, OPEN))
    closing_exposure = contract_exposures(book(CLOSING_MARKS, CLOSE))

    opening = {c: opening_exposure.net.of(c) for c in opening_exposure.net.currencies}
    closing = {c: closing_exposure.net.of(c) for c in closing_exposure.net.currencies}

    report = currency_attribution(
        opening=opening,
        closing=closing,
        opening_rates=rates,
        closing_rates=closing_rates,
        reporting_currency="USD",
        opening_timestamp=OPEN,
        closing_timestamp=CLOSE,
    )

    print(
        f"  {'ccy':<6} {'open (local)':>16} {'close (local)':>16} "
        f"{'assets':>12} {'currency':>12} {'total':>12}"
    )
    print("  " + "-" * 80)
    for entry in report.by_currency:
        print(
            f"  {entry.currency:<6} {entry.opening_local:>16} {entry.closing_local:>16} "
            f"{entry.local_return:>12} {entry.currency_return:>12} {entry.total:>12}"
        )
    print("  " + "-" * 80)
    print(
        f"  {'':<6} {'':>16} {'':>16} {report.local_return:>12} "
        f"{report.currency_return:>12} {report.total:>12}"
    )
    print(f"\n  rounding carried: {report.rounding}")
    print(
        f"  components sum to the total: "
        f"{report.local_return + report.currency_return == report.total}"
    )

    # ----------------------------------------------------------------- #
    rule("The seams, checked")

    checks = (
        (
            "the multiplier is applied once",
            all(
                entry.amount == entry.quantity * entry.price * entry.multiplier
                for entry in exposure.by_asset.values()
            ),
        ),
        (
            "notional is in the quote currency",
            all(
                exposure.by_asset[holding.position.asset_id].currency
                == holding.convention.quote_currency
                for holding in holdings
            ),
        ),
        (
            "a short keeps its sign in net and not in gross",
            exposure.net.of("JPY") < Decimal("0") < exposure.gross.of("JPY"),
        ),
        (
            "every quote currency is reported apart",
            set(exposure.gross.currencies) == {"JPY", "USD", "USDT"},
        ),
        (
            "a quanto's notional is in its quote currency, not its settlement one",
            exposure.by_asset["NKD_202606"].currency == "JPY"
            and NIKKEI_DOLLAR.settlement_currency == "USD",
        ),
        (
            "the decomposition has no residual",
            report.local_return + report.currency_return == report.total,
        ),
        (
            "the reporting currency has no currency effect",
            next(e for e in report.by_currency if e.currency == "USD").currency_return
            == Decimal("0.00"),
        ),
        (
            "the result is deterministic",
            contract_exposures(book(OPENING_MARKS, OPEN)).net == exposure.net,
        ),
    )
    for label, held in checks:
        print(f"  [{'ok' if held else 'FAILED'}] {label}")

    assert all(held for _, held in checks)

    print("\n" + "=" * 68)
    print("Example 40 complete.")
    print("(Every price, rate, calendar and convention above is declared in this")
    print(" file. AlphaLab ships no market data, no holiday list and no FX rate.)")


if __name__ == "__main__":
    main()
