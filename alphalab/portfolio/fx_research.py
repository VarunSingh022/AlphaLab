"""FX as research: forwards, carry, exposure, hedging and currency attribution.

:mod:`alphalab.portfolio.fx` is the rate authority -- :class:`~alphalab.portfolio.fx.FxRate`,
:class:`~alphalab.portfolio.fx.FxRates`, and the rules about provenance,
staleness and direction that make a converted figure honest. This module builds
research on top of those types and **defines no second rate type**, the same way
:mod:`alphalab.futures` builds contracts on ``Position`` and ``Side`` without
redefining either.

Quotation direction is carried, never inferred
-----------------------------------------------

Every figure here that has a direction states it as a ``(base, quote)`` pair,
because there is no convention that settles it. EURUSD is quoted one way and
USDJPY the other, market usage flips for some crosses, and a forward point
computed against the wrong direction is not a small error -- it changes the sign
of a carry. So a forward is an :class:`~alphalab.portfolio.fx.FxRate` with the
same ``base``/``quote`` meaning as a spot rate: *one unit of base buys ``rate``
of quote*.

No interest rate is invented
-----------------------------

:func:`covered_forward_rate` computes a forward from spot and two deposit rates
by covered interest parity. Both rates are **required arguments**, and so is the
day-count basis they accrue under: a "3-month forward" computed from a rate
whose basis was assumed is wrong by the difference between ACT/360 and ACT/365,
which is 1.4% of the interest. AlphaLab holds no deposit curve and derives none.

If a venue quoted an outright forward, use it directly -- a computed parity
forward is arbitrage-free and is not the price anyone traded.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from alphalab.conventions.daycount import DayCount, year_fraction
from alphalab.portfolio.amounts import CurrencyAmounts
from alphalab.portfolio.exceptions import PortfolioError
from alphalab.portfolio.fx import FxRate, FxRates
from alphalab.portfolio.money import to_money
from alphalab.portfolio.position import Position

__all__ = [
    "CurrencyAttribution",
    "CurrencyAttributionReport",
    "ForwardTerms",
    "carry_rate",
    "covered_forward_rate",
    "currency_attribution",
    "currency_exposures",
    "forward_points",
    "hedge_notional",
]


@dataclass(frozen=True, slots=True)
class ForwardTerms:
    """The inputs a covered-parity forward needs, gathered so none is assumed.

    Attributes:
        value_date: The forward's settlement date.
        spot_date: The spot leg's settlement date, which is what the forward is
            measured *from*. Not the trade date: spot FX settles T+2 in most
            pairs, so a forward priced from the trade date is priced over two
            days too many. :func:`alphalab.conventions.settlement.settlement_date`
            derives it from a declared rule.
        base_rate: Annualized deposit rate in the **base** currency, as a
            decimal fraction. Required.
        quote_rate: Annualized deposit rate in the **quote** currency. Required.
        basis: The day-count basis both rates accrue under. Required. Two rates
            quoted on different bases must be restated onto one before they
            reach here; carrying a single basis is what makes the parity
            arithmetic dimensionally coherent.

    Raises:
        PortfolioError: If the value date is not after the spot date.
    """

    value_date: date
    spot_date: date
    base_rate: float
    quote_rate: float
    basis: DayCount

    def __post_init__(self) -> None:
        if self.value_date <= self.spot_date:
            raise PortfolioError(
                f"value_date {self.value_date} is not after spot_date {self.spot_date}. A "
                "forward settles later than spot; reversing them inverts the carry."
            )

    @property
    def years(self) -> float:
        """The accrual period, under the declared basis."""

        return year_fraction(self.spot_date, self.value_date, self.basis)


def covered_forward_rate(spot: FxRate, terms: ForwardTerms) -> FxRate:
    """The arbitrage-free forward implied by a spot rate and two deposit rates.

    ``forward = spot * exp((quote_rate - base_rate) * years)``, continuously
    compounded. The sign follows from the quotation: if the quote currency pays
    more interest than the base, one unit of base buys *more* quote forward than
    spot, so the forward is above spot -- the base currency trades at a discount.

    The result is an :class:`~alphalab.portfolio.fx.FxRate` marked
    ``derived=True`` with a source naming both inputs, exactly as
    :meth:`~alphalab.portfolio.fx.FxRates.with_inverses` and
    :meth:`~alphalab.portfolio.fx.FxRates.cross_rate` mark theirs. Its ``as_of``
    is the spot rate's: a forward computed from a spot known at a given instant
    is known at that instant and no later, which is what keeps it usable in a
    point-in-time study.

    A computed parity forward is **not a quote**. Nobody traded it, and it
    differs from a dealt forward by the spread and by whatever the deposit
    market actually charges.
    """

    years = terms.years
    factor = math.exp((terms.quote_rate - terms.base_rate) * years)
    return FxRate(
        base=spot.base,
        quote=spot.quote,
        rate=spot.rate * Decimal(str(factor)),
        as_of=spot.as_of,
        source=(
            f"covered parity from {spot.source!r}: {terms.base_rate:+.6g} base / "
            f"{terms.quote_rate:+.6g} quote over {years:.6g}y on {terms.basis.name}"
        ),
        derived=True,
    )


def forward_points(spot: FxRate, forward: FxRate) -> Decimal:
    """``forward.rate - spot.rate``, in units of the quote currency.

    Positive means the base currency is at a forward premium. Not a "pip" count:
    a pip is a venue's quoting increment and differs by pair, so scaling to one
    would need a convention this function was not given. The difference in quote
    units is unambiguous, and
    :meth:`alphalab.conventions.market.MarketConvention.tick_value_at` is where
    a quoting increment lives.

    Raises:
        PortfolioError: If the two rates quote different pairs. Subtracting
            EURUSD from EURGBP produces a number with no units at all.
    """

    if spot.pair != forward.pair:
        raise PortfolioError(
            f"The spot quotes {spot.base}/{spot.quote} and the forward quotes "
            f"{forward.base}/{forward.quote}. Their difference is not forward points; it is "
            "two different prices subtracted."
        )
    return forward.rate - spot.rate


def carry_rate(terms: ForwardTerms) -> float:
    """The annualized interest differential a long-base position earns.

    ``base_rate - quote_rate``. Positive means holding the base currency and
    funding in the quote currency earns the difference before any spot move --
    the carry trade's return, and the opposite sign to
    :func:`forward_points`, which is why both exist and why each says which it
    is.

    Annualized, so a differential over one month and one over six are
    comparable. Unhedged and before transaction costs: this is the interest
    leg alone, and covered interest parity says a *hedged* version of it is
    zero.
    """

    return terms.base_rate - terms.quote_rate


def currency_exposures(positions: Mapping[str, Position]) -> CurrencyAmounts:
    """Market value per currency, in each position's own currency.

    Deliberately not converted and deliberately not totalled: the result is one
    figure per currency, which is what "exposure to a currency" means, and
    summing across them would be the figure ADR-0020 removed.
    :meth:`~alphalab.portfolio.amounts.CurrencyAmounts.total_in` converts when a
    caller supplies rates and names a currency.

    Market value is ``quantity * market_price`` -- ``Position``'s own
    definition, in units of whatever the position is denominated in. A
    contract-bearing position needs its multiplier applied, which
    :func:`alphalab.conventions.market.contract_notional` does and this does
    not: applying it here would double it for a caller who had already applied
    it, and ``Position`` carries no multiplier to tell the two apart.
    """

    amounts = CurrencyAmounts()
    for position in positions.values():
        amounts = amounts.add(position.market_value, position.currency)
    return amounts


def hedge_notional(exposure: Decimal, hedge_ratio: Decimal) -> Decimal:
    """How much of an exposure to sell forward, at a stated ratio.

    ``exposure * hedge_ratio``, signed so that the result is the amount of the
    exposed currency to **sell** -- a positive exposure fully hedged returns a
    positive number meaning "sell this much".

    ``hedge_ratio`` is required and has no default. A 100% hedge, a 50% hedge
    and an unhedged book are three different strategies with three different
    return series, and which one a portfolio runs is a mandate decision rather
    than a calculation. Ratios outside ``[0, 1]`` are permitted -- an
    over-hedge is a real, deliberate position -- and a negative one is refused,
    because selling a negative amount of an exposure is a long position stated
    in a way that hides it.

    Raises:
        PortfolioError: If ``hedge_ratio`` is negative.
    """

    if hedge_ratio < Decimal("0"):
        raise PortfolioError(
            f"hedge_ratio is {hedge_ratio}. A negative ratio is a long position in the "
            "exposure expressed as a hedge of it, which hides what the book is doing."
        )
    return to_money(exposure * hedge_ratio)


@dataclass(frozen=True, slots=True)
class CurrencyAttribution:
    """One currency's contribution to a reporting-currency return.

    The decomposition is exact rather than approximate, and has no residual::

        (V1 - V0) * r0 + V1 * (r1 - r0) == V1 * r1 - V0 * r0

    where ``V`` is the value in the local currency and ``r`` converts local to
    reporting. The left term is what the assets did; the right is what the
    currency did.

    Attributes:
        currency: The local currency this describes.
        opening_local: Value at the start, in ``currency``.
        closing_local: Value at the end, in ``currency``.
        opening_reporting: ``opening_local`` converted at ``opening_rate``.
        closing_reporting: ``closing_local`` converted at ``closing_rate``.
        opening_rate: ``currency`` to the reporting currency, at the start.
        closing_rate: The same pair, at the end.
        local_return: The asset return, converted at the **opening** rate. In
            the reporting currency.
        currency_return: The revaluation of the closing local value. In the
            reporting currency.
        total: ``local_return + currency_return``, and equal to the change in
            reporting-currency value. In the reporting currency.
    """

    currency: str
    opening_local: Decimal
    closing_local: Decimal
    opening_reporting: Decimal
    closing_reporting: Decimal
    opening_rate: FxRate
    closing_rate: FxRate
    local_return: Decimal
    currency_return: Decimal
    total: Decimal

    @property
    def rounding(self) -> Decimal:
        """What rounding each component to the minor unit cost.

        ``total - (closing_reporting - opening_reporting)``. Bounded by one
        minor unit, and zero whenever the figures divide evenly.
        """

        return self.total - (self.closing_reporting - self.opening_reporting)


@dataclass(frozen=True, slots=True)
class CurrencyAttributionReport:
    """Every currency's contribution, and the reporting currency they are in.

    Unlike :class:`alphalab.analytics.attribution.AttributionReport`'s
    ``CURRENCY`` dimension -- whose buckets are denominated in *different*
    currencies and deliberately have no total -- every figure here is already in
    ``reporting_currency``, so they do sum. That is not a contradiction: this
    report was given the rates that one was not, and each conversion records
    the rate it used.

    The two are different measurements and neither recomputes the other. This
    one takes local values and rates; that one takes trades.

    Attributes:
        reporting_currency: What every figure is denominated in.
        opening_timestamp: The instant the opening rates are read at.
        closing_timestamp: The instant the closing rates are read at.
        by_currency: One entry per local currency, ordered by currency code.
        local_return: Sum of the local components.
        currency_return: Sum of the currency components.
        total: ``local_return + currency_return``.
        rounding: Sum of every entry's rounding, so the report reconciles
            against the converted endpoints to the minor unit.
    """

    reporting_currency: str
    opening_timestamp: float
    closing_timestamp: float
    by_currency: tuple[CurrencyAttribution, ...]
    local_return: Decimal
    currency_return: Decimal
    total: Decimal
    rounding: Decimal


def currency_attribution(
    opening: Mapping[str, Decimal],
    closing: Mapping[str, Decimal],
    opening_rates: FxRates,
    closing_rates: FxRates,
    reporting_currency: str,
    opening_timestamp: float,
    closing_timestamp: float,
) -> CurrencyAttributionReport:
    """Split a reporting-currency change into what the assets did and what the
    currencies did.

    Args:
        opening: Value per local currency at the start, each in its own
            currency.
        closing: The same at the end. Every currency in ``opening`` must appear,
            and a currency that arrived during the period may be added -- its
            opening value is then zero, which is true.
        opening_rates: Rates as they stood at ``opening_timestamp``.
        closing_rates: Rates as they stood at ``closing_timestamp``. A separate
            table, because using one table for both instants is the mistake this
            function exists to make visible: with a single table the currency
            component is identically zero and the report says the currency did
            nothing.
        reporting_currency: What the result is denominated in.
        opening_timestamp: Read against ``opening_rates``. A rate dated after it
            is refused by
            :meth:`~alphalab.portfolio.fx.FxRates.convert`.
        closing_timestamp: Read against ``closing_rates``.

    Raises:
        PortfolioError: If ``closing`` omits a currency ``opening`` holds, or if
            the closing instant precedes the opening one.
        MissingRateError: If either table lacks a pair it needs. No rate is
            invented and no direction is inverted; the refusal names the pair.
        FutureDatedRateError: If a rate post-dates the instant it is read at.
    """

    if closing_timestamp < opening_timestamp:
        raise PortfolioError(
            f"closing_timestamp {closing_timestamp} precedes opening_timestamp "
            f"{opening_timestamp}; the period runs forwards."
        )
    missing = sorted(set(opening) - set(closing))
    if missing:
        raise PortfolioError(
            f"{missing} appear at the opening and not at the closing. A currency that left "
            "the book closed at zero, which is a value the caller states -- omitting it "
            "would drop its whole local return from the report without a trace."
        )

    entries: list[CurrencyAttribution] = []
    for currency in sorted(set(opening) | set(closing)):
        opened = opening.get(currency, Decimal("0"))
        closed = closing[currency]
        open_conversion = opening_rates.convert(
            Decimal("1"), currency, reporting_currency, opening_timestamp
        )
        close_conversion = closing_rates.convert(
            Decimal("1"), currency, reporting_currency, closing_timestamp
        )
        open_rate, close_rate = open_conversion.rate, close_conversion.rate
        local = to_money((closed - opened) * open_rate.rate)
        currency_effect = to_money(closed * (close_rate.rate - open_rate.rate))
        entries.append(
            CurrencyAttribution(
                currency=currency,
                opening_local=opened,
                closing_local=closed,
                opening_reporting=to_money(opened * open_rate.rate),
                closing_reporting=to_money(closed * close_rate.rate),
                opening_rate=open_rate,
                closing_rate=close_rate,
                local_return=local,
                currency_return=currency_effect,
                total=local + currency_effect,
            )
        )

    local_total = sum((entry.local_return for entry in entries), Decimal("0"))
    currency_total = sum((entry.currency_return for entry in entries), Decimal("0"))
    return CurrencyAttributionReport(
        reporting_currency=reporting_currency,
        opening_timestamp=opening_timestamp,
        closing_timestamp=closing_timestamp,
        by_currency=tuple(entries),
        local_return=local_total,
        currency_return=currency_total,
        total=local_total + currency_total,
        rounding=sum((entry.rounding for entry in entries), Decimal("0")),
    )
