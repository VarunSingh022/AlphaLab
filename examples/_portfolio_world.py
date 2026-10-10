"""Shared setup for the v3.8 portfolio and risk examples (56-60).

Every example reads the same small world, written out below:

* **eight instruments on three markets** -- four in New York in US dollars, two
  in Frankfurt in euros, two in Tokyo in yen -- with a sector and a country
  each. The sectors are registered in an instrument registry from a named
  source; the countries come from a second named file, because AlphaLab holds
  no country anywhere.
* **sixty daily returns per instrument, measured in US dollars**, built from a
  market factor, a sector factor, a region factor and each name's own noise.
  The noise comes from the Park-Miller recurrence written out in
  :func:`_uniforms` -- exact integer arithmetic and one division per value --
  so every return, and every figure an example derives from them, is the same
  on every machine.
* **FX rates** from a named desk, quoted into dollars, with the inverse
  directions derived and marked as derived.
* **three strategies' books**, each kept by its own accounting state through
  the canonical engine -- deposits, fills in three currencies, a partial close
  that realizes P&L, and a mark to market.

Examples 56 and 58-60 identify an instrument by its ticker, which keeps their
tables legible; an asset identifier is any stable string. Example 57 reads
sectors from the instrument registry, which keys by the canonical ``asset_id``,
and says where it re-keys.

Nothing here fetches anything, names a vendor or reads a clock.
"""

from __future__ import annotations

import textwrap
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from alphalab.analytics import Classification, CovarianceMatrix, FactorLoadings
from alphalab.core.enums import AssetType
from alphalab.data.feed import Bar
from alphalab.factor_library import (
    FeatureDefinition,
    FeatureField,
    FeatureKind,
    compute_panel,
    loadings_from_panels,
    neutralize_mean,
    observations_from_records,
)
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import (
    InstrumentRegistry,
    classify_instruments,
    register_instruments,
)
from alphalab.portfolio.account import Account
from alphalab.portfolio.amounts import CurrencyAmounts
from alphalab.portfolio.engine import PortfolioEngine, PortfolioState
from alphalab.portfolio.fx import FxRate, FxRates
from alphalab.portfolio.multi_strategy import MultiStrategyBook, StrategySleeve, value_book

#: Ticker -> (venue, currency, market, sector, country).
LISTINGS: Mapping[str, tuple[str, str, str, str, str]] = {
    "ATLS": ("XNYS", "USD", "US_EQUITIES", "Technology", "US"),
    "BRCK": ("XNYS", "USD", "US_EQUITIES", "Industrials", "US"),
    "CEDR": ("XNAS", "USD", "US_EQUITIES", "Technology", "US"),
    "DUNE": ("XNYS", "USD", "US_EQUITIES", "Energy", "US"),
    "ELBE": ("XETR", "EUR", "EU_EQUITIES", "Industrials", "DE"),
    "FALK": ("XETR", "EUR", "EU_EQUITIES", "Technology", "DE"),
    "GINZ": ("XTKS", "JPY", "JP_EQUITIES", "Technology", "JP"),
    "HAKO": ("XTKS", "JPY", "JP_EQUITIES", "Industrials", "JP"),
}
SYMBOLS = tuple(LISTINGS)
STRATEGIES = ("CARRY", "MOMENTUM", "VALUE")

#: The valuation instant: 2024-05-31 20:00 UTC, the New York close.
AS_OF = 1_717_185_600.0
DAY = 86_400.0
DAYS = 60
FIRST_DAY = AS_OF - (DAYS - 1) * DAY

#: Where each figure came from, in the words an audit would read.
RETURNS_SOURCE = "examples/_portfolio_world.py: 60 daily closes, returns measured in USD"
SECTOR_SOURCE = "example sector file, 2024-05"
COUNTRY_SOURCE = "example country-of-listing file, 2024-05"
FX_SOURCE = "example FX desk, 15:00 New York fixing"

# --------------------------------------------------------------------------- #
# Returns, from a factor structure and a written-out recurrence
# --------------------------------------------------------------------------- #

_MODULUS = 2_147_483_647


def _uniforms(seed: int, count: int) -> list[float]:
    """``count`` values in ``(-1, 1)`` from ``x <- 48271 x mod (2^31 - 1)``.

    Park and Miller's recurrence, spelled out so each value is exact integer
    arithmetic followed by one correctly rounded division: the same on every
    machine, which a library generator is not promised to be.
    """

    values = []
    state = seed
    for _ in range(count):
        state = 48_271 * state % _MODULUS
        values.append(2.0 * state / _MODULUS - 1.0)
    return values


#: Ticker -> (daily drift, market beta, own noise scale). The drift is the
#: example's choice, and it is what makes one name trend and another not.
_PROFILE = {
    "ATLS": (0.0011, 1.25, 0.012),
    "BRCK": (0.0004, 0.95, 0.008),
    "CEDR": (0.0008, 1.10, 0.010),
    "DUNE": (-0.0003, 0.80, 0.014),
    "ELBE": (0.0003, 0.90, 0.009),
    "FALK": (0.0006, 1.05, 0.011),
    "GINZ": (0.0007, 1.15, 0.013),
    "HAKO": (-0.0001, 0.85, 0.009),
}


def _returns() -> dict[str, tuple[float, ...]]:
    market = [0.009 * value for value in _uniforms(1, DAYS)]
    sector = {
        name: [0.004 * value for value in _uniforms(seed, DAYS)]
        for seed, name in enumerate(("Energy", "Industrials", "Technology"), start=2)
    }
    region = {
        name: [0.003 * value for value in _uniforms(seed, DAYS)]
        for seed, name in enumerate(("DE", "JP", "US"), start=5)
    }
    returns = {}
    for index, symbol in enumerate(SYMBOLS):
        drift, beta, scale = _PROFILE[symbol]
        _, _, _, sector_name, country = LISTINGS[symbol]
        noise = _uniforms(11 + index, DAYS)
        returns[symbol] = tuple(
            drift
            + beta * market[day]
            + sector[sector_name][day]
            + region[country][day]
            + scale * noise[day]
            for day in range(DAYS)
        )
    return returns


#: Ticker -> sixty daily returns, measured in US dollars.
RETURNS: Mapping[str, tuple[float, ...]] = _returns()


def covariance(symbols: Sequence[str] = SYMBOLS) -> CovarianceMatrix:
    """The sample covariance of the named instruments' USD returns, per day."""

    return CovarianceMatrix.sample(
        {symbol: RETURNS[symbol] for symbol in symbols},
        currency="USD",
        period="1D",
        source=RETURNS_SOURCE,
    )


def closes(symbol: str) -> list[float]:
    """The closes the returns compound to, from 100.0 the session before the first."""

    level = 100.0
    path = []
    for value in RETURNS[symbol]:
        level *= 1.0 + value
        path.append(level)
    return path


def factor_loadings() -> FactorLoadings:
    """Momentum and volatility loadings on the last day, from the factor library's panels.

    Twenty-day momentum and twenty-day realized volatility, each demeaned
    across the eight names so a loading reads as "more than the average
    name". Every step is in the lineage the loadings carry.
    """

    bars = [
        Bar(symbol, FIRST_DAY + day * DAY, close, close, close, close, 1_000_000.0)
        for symbol in SYMBOLS
        for day, close in enumerate(closes(symbol))
    ]
    frame = observations_from_records(bars, FeatureField.CLOSE, "UTC", "example-closes@v1")
    momentum = compute_panel(
        FeatureDefinition("momentum_20d", FeatureKind.MOMENTUM, FeatureField.CLOSE, window=20),
        frame,
    )
    volatility = compute_panel(
        FeatureDefinition(
            "volatility_20d",
            FeatureKind.REALIZED_VOLATILITY,
            FeatureField.CLOSE,
            window=20,
            parameters={"periods_per_year": 252.0},
        ),
        frame,
    )
    return loadings_from_panels(
        {
            "momentum": neutralize_mean(momentum).panel,
            "volatility": neutralize_mean(volatility).panel,
        },
        instant=FIRST_DAY + (DAYS - 1) * DAY,
        source="factor library: 20-day momentum and realized volatility, demeaned",
        asset_ids=None,
    )


# --------------------------------------------------------------------------- #
# Classifications, and the registry the sectors are kept in
# --------------------------------------------------------------------------- #

RECORDS = {
    symbol: InstrumentRecord(symbol, AssetType.EQUITY, venue, currency)
    for symbol, (venue, currency, _, _, _) in LISTINGS.items()
}
ASSET_ID = {symbol: record.asset_id for symbol, record in RECORDS.items()}
SYMBOL_OF = {asset_id: symbol for symbol, asset_id in ASSET_ID.items()}

#: The instrument registry an operator maintains, with each sector classified
#: from a named source -- what :func:`alphalab.api.sector_classification` reads.
REGISTRY: InstrumentRegistry = classify_instruments(
    register_instruments(InstrumentRegistry(), RECORDS.values()),
    {ASSET_ID[symbol]: listing[3] for symbol, listing in LISTINGS.items()},
    source=SECTOR_SOURCE,
)


def sectors() -> Classification:
    return Classification(
        "sector", {symbol: listing[3] for symbol, listing in LISTINGS.items()}, SECTOR_SOURCE, None
    )


def countries() -> Classification:
    return Classification(
        "country",
        {symbol: listing[4] for symbol, listing in LISTINGS.items()},
        COUNTRY_SOURCE,
        None,
    )


# --------------------------------------------------------------------------- #
# FX, and three strategies' books
# --------------------------------------------------------------------------- #

#: Quoted into dollars at the fixing an hour before the close; the opposite
#: directions are derived by inversion and recorded as derived.
RATES = FxRates.of(
    [
        FxRate("EUR", "USD", Decimal("1.0850"), AS_OF - 3_600.0, FX_SOURCE),
        FxRate("JPY", "USD", Decimal("0.006700"), AS_OF - 3_600.0, FX_SOURCE),
    ]
).with_inverses()

#: Each instrument's close on the valuation day, in its own currency.
MARKS = {
    "ATLS": Decimal("186.40"),
    "BRCK": Decimal("63.15"),
    "CEDR": Decimal("311.20"),
    "DUNE": Decimal("46.75"),
    "ELBE": Decimal("92.30"),
    "FALK": Decimal("143.60"),
    "GINZ": Decimal("2840"),
    "HAKO": Decimal("1215"),
}

#: Each strategy's account currency, its deposits, and its fills in order:
#: (ticker, signed quantity, price in the instrument's currency).
_BOOKS: Mapping[str, tuple[str, tuple[tuple[str, str], ...], tuple[tuple[str, str, str], ...]]] = {
    "MOMENTUM": (
        "USD",
        (("1000000", "USD"), ("200000000", "JPY")),
        (
            ("ATLS", "1500", "175.10"),
            ("CEDR", "800", "298.40"),
            ("GINZ", "60000", "2790"),
            ("DUNE", "-4000", "50.20"),
            ("ATLS", "-500", "184.00"),
        ),
    ),
    "VALUE": (
        "USD",
        (("800000", "USD"), ("300000", "EUR")),
        (
            ("BRCK", "3000", "61.90"),
            ("DUNE", "2500", "47.10"),
            ("ELBE", "1800", "88.40"),
            ("CEDR", "-400", "305.00"),
            ("CEDR", "100", "300.10"),
        ),
    ),
    "CARRY": (
        "EUR",
        (("600000", "EUR"), ("100000000", "JPY")),
        (
            ("FALK", "1200", "139.80"),
            ("ELBE", "1000", "90.10"),
            ("HAKO", "30000", "1190"),
            ("GINZ", "-20000", "2860"),
        ),
    ),
}


def strategy_state(strategy_id: str) -> PortfolioState:
    """One strategy's own accounting state, built by the canonical engine."""

    base, deposits, fills = _BOOKS[strategy_id]
    state = PortfolioState(account=Account(f"{strategy_id}-ACCT", base, strategy_id, FIRST_DAY))
    for amount, currency in deposits:
        state = PortfolioEngine.apply_deposit(state, Decimal(amount), currency, FIRST_DAY)
    for step, (symbol, quantity, price) in enumerate(fills, start=1):
        currency = LISTINGS[symbol][1]
        state = PortfolioEngine.apply_fill(
            state,
            symbol,
            Decimal(quantity),
            Decimal(price),
            Decimal("1.00") if currency != "JPY" else Decimal("150"),
            FIRST_DAY + step * DAY,
            currency,
        )
    return PortfolioEngine.update_market_prices(state, MARKS, AS_OF)


def book() -> MultiStrategyBook:
    """The three strategies as one book, with capital no strategy holds."""

    return MultiStrategyBook(
        "EXAMPLE-FUND",
        tuple(
            StrategySleeve.from_portfolio_state(strategy, strategy_state(strategy))
            for strategy in STRATEGIES
        ),
        CurrencyAmounts.single(Decimal("250000"), "USD"),
    )


#: What :func:`strategy_returns` measures, for any figure derived from it.
STRATEGY_RETURNS_SOURCE = (
    "each strategy's book at the close of 2024-05-31, held fixed over 60 days of USD returns"
)


def strategy_returns() -> dict[str, tuple[float, ...]]:
    """What each strategy's book today would have returned each day, in dollars.

    Each line's weight is its dollar value over its strategy's NAV, held fixed
    over the sixty days -- the assumption portfolio volatility makes. These are
    the series for comparing and weighting strategies by risk; they are not the
    strategies' realized track records, which a backtest of each would give.
    """

    valuation = value_book(book(), reporting_currency="USD", rates=RATES, as_of=AS_OF)
    series = {}
    for entry in valuation.strategies:
        weights = {
            line.asset_id: float(line.reporting_value / entry.nav)
            for line in valuation.lines
            if line.strategy_id == entry.strategy_id
        }
        series[entry.strategy_id] = tuple(
            sum(weight * RETURNS[asset][day] for asset, weight in sorted(weights.items()))
            for day in range(DAYS)
        )
    return series


# --------------------------------------------------------------------------- #
# Printing
# --------------------------------------------------------------------------- #


def banner(number: int, title: str) -> None:
    print("=" * 72)
    print(f"AlphaLab Example {number} : {title}")
    print("=" * 72)


def section(title: str) -> None:
    print()
    print(f"-- {title} " + "-" * max(0, 66 - len(title)))


def header(label: str = "", names: Sequence[str] = SYMBOLS) -> str:
    """A table header: a label column, then one column per name."""

    return f"  {label:<20}" + "".join(f"{name:>7}" for name in names)


def row(
    label: str,
    values: Mapping[str, float],
    names: Sequence[str] = SYMBOLS,
    scale: float = 100.0,
    digits: int = 1,
) -> str:
    """One table row, each value times ``scale`` -- percentages by default.

    A value that rounds to zero prints as ``0.0``: a weight of ``-1e-17`` is a
    zero the solver reached within its tolerance, not a short.
    """

    return f"  {label:<20}" + "".join(
        f"{round(scale * values[name], digits) + 0.0:7.{digits}f}" for name in names
    )


def quantity(value: Decimal, signed: bool = False) -> str:
    """A quantity with its trailing zeros dropped: ``1,000`` rather than ``1,000.000000``."""

    whole = value == value.to_integral_value()
    if signed:
        return f"{value:+,.0f}" if whole else f"{value.normalize():+,}"
    return f"{value:,.0f}" if whole else f"{value.normalize():,}"


def utc(instant: float) -> str:
    """A Unix instant as a UTC wall-clock reading, for printing."""

    return datetime.fromtimestamp(instant, UTC).strftime("%Y-%m-%d %H:%M UTC")


def refusal(what: str, error: Exception) -> None:
    """Print a refusal: what was asked, the exception, and its message, wrapped."""

    print(f"  {what} -> {type(error).__name__}:")
    print(textwrap.fill(str(error), width=76, initial_indent="    ", subsequent_indent="    "))


def wrapped(label: str, items: Sequence[str], width: int = 76) -> str:
    """``label: a, b, c`` wrapped under the label, for lists of constraint names."""

    lines = [f"  {label}: "]
    for index, item in enumerate(items):
        text = item + ("," if index < len(items) - 1 else "")
        if len(lines[-1]) + len(text) + 1 > width:
            lines.append(" " * (len(label) + 4))
        lines[-1] += ("" if lines[-1].endswith(": ") or lines[-1].isspace() else " ") + text
    return "\n".join(lines) if items else f"  {label}: none"
