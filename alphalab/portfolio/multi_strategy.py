"""Several independent strategies in one portfolio, each still itself.

A portfolio that runs a momentum strategy and a mean-reversion strategy side by
side holds, at the portfolio level, one position per instrument. Two questions
then need answers the portfolio-level position cannot give: *whose* is it, and
what did each strategy earn and risk? This module keeps the answer to the first
through every step that produces the second.

The representation
------------------

============================ =================================================================
portfolio identity           :attr:`MultiStrategyBook.portfolio_id`
strategy identity            :attr:`StrategySleeve.strategy_id` -- the
                             ``StrategyDefinition.strategy_id`` a registry resolves
instrument identity          the canonical ``asset_id`` each :class:`Position` carries
position                     each sleeve holds canonical
                             :class:`~alphalab.portfolio.position.Position` values, one per
                             instrument, exactly as a strategy's own run produced them
capital                      each sleeve's cash per currency; its net asset value is
                             :attr:`StrategyValuation.nav`
risk contribution            the lines of a :class:`BookValuation` are what
                             :func:`alphalab.analytics.risk_budget.evaluate_risk_budget`
                             and :mod:`alphalab.analytics.cross_strategy` read
============================ =================================================================

A **sleeve** is one strategy's holdings. Strategies need no change to coexist:
a sleeve is built from whatever state the strategy's own run produced
(:meth:`StrategySleeve.from_portfolio_state`), so two strategies researched and
run independently are combined rather than rewritten.

Two strategies holding the same instrument stay two holdings. The portfolio
level is an **aggregation** -- :meth:`MultiStrategyBook.holding` -- whose net
quantity carries a :class:`~alphalab.core.contribution.StrategyContribution`
per strategy, the canonical record ADR-0015 introduced for exactly this: who
contributed what to one portfolio-level quantity, signed. Nothing merges them.
Opposing holdings net and are reported as crossed, never cancelled.

Not a second portfolio model
----------------------------

:class:`~alphalab.portfolio.engine.PortfolioState` remains the one book of
record, and ``test_shared_names_stay_distinct.py`` keeps it so. A multi-strategy
book holds canonical positions and never applies a fill, moves cash or keeps a
ledger; it is a grouping of positions that already exist, and every figure it
reports is read from :class:`Position` itself -- ``market_value`` for exposure,
``unrealized_pnl`` for open P&L -- so there is no third exposure site
(ROADMAP, *No exposure computed by the comparison layer*). Per-strategy
sub-ledgers inside the accounting engine remain optional future evolution.

Share semantics: a :class:`Position` carries no multiplier (ADR-0039), so a
contract belongs in a sleeve with its quantity already in underlying units --
the rule :class:`~alphalab.portfolio.contracts.ContractHolding` states for a
caller who has scaled.

Money in more than one currency
-------------------------------

Every native figure stays in its own currency: a sleeve's cash, realized P&L and
commissions are :class:`~alphalab.portfolio.amounts.CurrencyAmounts`, and each
holding's market value is in its position's currency. :func:`value_book`
expresses the book in one **reporting currency** through the canonical
:meth:`FxRates.convert <alphalab.portfolio.fx.FxRates.convert>` -- one conversion
per native figure, rounded to money once, at a stated instant -- so a missing,
stale or future-dated rate refuses exactly as it does everywhere else, and every
conversion performed is kept on the valuation. Every total is a sum of those
converted figures, which is what makes every breakdown reconcile to the cent:
by strategy, by instrument and by currency, each partitions the same lines.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from alphalab.common.arithmetic import canonical_text
from alphalab.core.contribution import StrategyContribution
from alphalab.portfolio.amounts import CurrencyAmounts
from alphalab.portfolio.engine import PortfolioState
from alphalab.portfolio.exceptions import MixedCurrencyValuationError, PortfolioError
from alphalab.portfolio.fx import FxConversion, FxRates
from alphalab.portfolio.money import ZERO_MONEY
from alphalab.portfolio.position import Position

__all__ = [
    "BOOK_VALUATION_SCHEME",
    "MULTI_STRATEGY_BOOK_SCHEME",
    "BookValuation",
    "HoldingValuation",
    "InstrumentHolding",
    "InstrumentValuation",
    "MultiStrategyBook",
    "StrategySleeve",
    "StrategyValuation",
    "value_book",
]

MULTI_STRATEGY_BOOK_SCHEME: Final = "alphalab.multi_strategy_book.v2"
BOOK_VALUATION_SCHEME: Final = "alphalab.book_valuation.v2"


def _identifier(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PortfolioError(f"{what} must be a non-blank, unpadded string, got {value!r}.")
    if not value.isprintable():
        raise PortfolioError(f"{what} {value!r} contains a control character.")
    return value


def _digest(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# Amounts enter an identity by value (canonical_text), not by representation:
# since v3.10 nothing rounds an amount a second time, so ``250000`` and
# ``250000.00`` both reach a book, and two books that compare equal must not
# have two identities. Hence the v2 schemes.


def _render_amounts(amounts: CurrencyAmounts) -> str:
    return ",".join(
        f"{currency}={canonical_text(amounts.of(currency))}" for currency in amounts.currencies
    )


def _render_optional(value: Decimal | None) -> str:
    return "None" if value is None else canonical_text(value)


def _render_position(position: Position) -> str:
    return (
        f"{position.asset_id!r}|{canonical_text(position.quantity)}|"
        f"{canonical_text(position.average_cost)}|{canonical_text(position.market_price)}|"
        f"{canonical_text(position.realized_pnl)}|{position.currency!r}|"
        f"{position.last_updated!r}|{_render_optional(position.cost_basis)}|"
        f"{position.opened_at!r}"
    )


@dataclass(frozen=True, slots=True)
class StrategySleeve:
    """One strategy's holdings, capital and accumulated results.

    Attributes:
        strategy_id: The strategy. Non-blank: an unattributed holding is not a
            sleeve.
        positions: ``asset_id`` -> the canonical :class:`Position`, keyed by the
            position's own ``asset_id`` and held sorted.
        cash: The sleeve's cash per currency.
        realized_pnl: Cumulative realized P&L per settlement currency,
            including positions since closed.
        commission_paid: Cumulative commission per settlement currency.

    Raises:
        PortfolioError: On a blank strategy id, a position keyed by another
            asset's id, or a position with no currency.
    """

    strategy_id: str
    positions: Mapping[str, Position]
    cash: CurrencyAmounts
    realized_pnl: CurrencyAmounts
    commission_paid: CurrencyAmounts

    def __post_init__(self) -> None:
        _identifier(self.strategy_id, "StrategySleeve.strategy_id")
        ordered: dict[str, Position] = {}
        for asset_id, position in sorted(self.positions.items()):
            if not isinstance(position, Position):
                raise PortfolioError(f"{asset_id!r} is not a Position.")
            if position.asset_id != asset_id:
                raise PortfolioError(
                    f"Strategy {self.strategy_id!r} keys the position in {position.asset_id!r} "
                    f"under {asset_id!r}; a sleeve holds one position per instrument, under its "
                    "own id."
                )
            if not position.currency.strip():
                raise PortfolioError(f"The position in {asset_id!r} names no currency.")
            ordered[asset_id] = position
        object.__setattr__(self, "positions", MappingProxyType(ordered))

    @classmethod
    def from_portfolio_state(cls, strategy_id: str, state: PortfolioState) -> StrategySleeve:
        """The sleeve of a strategy whose own run produced ``state``.

        Positions, cash balances, realized P&L and commissions are read as the
        accounting engine recorded them. Reserved cash is still cash the sleeve
        holds, so balances are read whole; a currency whose balance is zero is
        left out, as :meth:`CurrencyAmounts.of` reads it as zero anyway.
        """

        cash = CurrencyAmounts()
        for currency, amount in sorted(state.cash.balances.items()):
            if amount != ZERO_MONEY:
                cash = cash.add(amount, currency)
        return cls(
            strategy_id=strategy_id,
            positions=dict(state.positions),
            cash=cash,
            realized_pnl=state.realized_pnl,
            commission_paid=state.commission_paid,
        )

    def rendering(self) -> list[str]:
        return [
            f"sleeve={self.strategy_id!r}",
            f"cash={_render_amounts(self.cash)}",
            f"realized={_render_amounts(self.realized_pnl)}",
            f"commission={_render_amounts(self.commission_paid)}",
            *(f"position={_render_position(position)}" for position in self.positions.values()),
        ]


@dataclass(frozen=True, slots=True)
class InstrumentHolding:
    """One instrument across every strategy that holds it -- aggregated, not merged.

    Attributes:
        asset_id: The instrument.
        currency: What it is denominated in.
        market_price: The one mark every holder carries it at.
        net_quantity: The sum of every holder's signed quantity -- exactly the
            sum of :attr:`contributions`.
        contributions: One per holding strategy, signed, ordered by strategy id.
            A strategy whose position is flat is listed with zero.
        long_quantity: Sum of the positive quantities.
        short_quantity: Sum of the negative quantities, as a magnitude.
        crossed_quantity: ``min(long, short)``: how much the strategies hold
            against each other and the portfolio does not hold at all.
    """

    asset_id: str
    currency: str
    market_price: Decimal
    net_quantity: Decimal
    contributions: tuple[StrategyContribution, ...]
    long_quantity: Decimal
    short_quantity: Decimal
    crossed_quantity: Decimal

    @property
    def strategies(self) -> tuple[str, ...]:
        """The strategies that hold it, sorted."""

        return tuple(contribution.strategy_id for contribution in self.contributions)

    @property
    def opposing(self) -> bool:
        """Whether one strategy is long what another is short."""

        return self.crossed_quantity > 0


@dataclass(frozen=True, slots=True)
class MultiStrategyBook:
    """A portfolio of independent strategy sleeves.

    Attributes:
        portfolio_id: The portfolio.
        sleeves: One per strategy, held sorted by strategy id.
        unassigned_cash: Capital the portfolio holds and no strategy does, per
            currency.

    Raises:
        PortfolioError: On a blank portfolio id or two sleeves for one strategy.
    """

    portfolio_id: str
    sleeves: tuple[StrategySleeve, ...]
    unassigned_cash: CurrencyAmounts

    def __post_init__(self) -> None:
        _identifier(self.portfolio_id, "MultiStrategyBook.portfolio_id")
        ordered = tuple(sorted(self.sleeves, key=lambda sleeve: sleeve.strategy_id))
        names = [sleeve.strategy_id for sleeve in ordered]
        if len(set(names)) != len(names):
            repeated = sorted({name for name in names if names.count(name) > 1})
            raise PortfolioError(
                f"The book has two sleeves for {repeated}. A strategy is one sleeve; two would "
                "make every per-strategy figure depend on which one was read."
            )
        object.__setattr__(self, "sleeves", ordered)

    @property
    def book_id(self) -> str:
        """The derived identity of this book's content."""

        return _digest(
            [
                MULTI_STRATEGY_BOOK_SCHEME,
                f"portfolio={self.portfolio_id!r}",
                f"unassigned={_render_amounts(self.unassigned_cash)}",
                *(line for sleeve in self.sleeves for line in sleeve.rendering()),
            ]
        )

    @property
    def strategies(self) -> tuple[str, ...]:
        """Every strategy in the book, sorted."""

        return tuple(sleeve.strategy_id for sleeve in self.sleeves)

    @property
    def instruments(self) -> tuple[str, ...]:
        """Every instrument any sleeve holds, sorted."""

        return tuple(sorted({asset for sleeve in self.sleeves for asset in sleeve.positions}))

    def sleeve(self, strategy_id: str) -> StrategySleeve:
        """One strategy's sleeve.

        Raises:
            PortfolioError: If the book has no such strategy.
        """

        for sleeve in self.sleeves:
            if sleeve.strategy_id == strategy_id:
                return sleeve
        raise PortfolioError(
            f"The book has no sleeve for {strategy_id!r}; it holds {list(self.strategies)}."
        )

    def with_sleeve(self, sleeve: StrategySleeve) -> MultiStrategyBook:
        """This book with a strategy added.

        Raises:
            PortfolioError: If the strategy is already in the book -- replacing
                it is :meth:`with_updated_sleeve`, a different act.
        """

        if sleeve.strategy_id in self.strategies:
            raise PortfolioError(
                f"{sleeve.strategy_id!r} is already in the book. Replacing a strategy's holdings "
                "is with_updated_sleeve()."
            )
        return MultiStrategyBook(self.portfolio_id, (*self.sleeves, sleeve), self.unassigned_cash)

    def with_updated_sleeve(self, sleeve: StrategySleeve) -> MultiStrategyBook:
        """This book with one existing strategy's sleeve replaced."""

        self.sleeve(sleeve.strategy_id)
        return MultiStrategyBook(
            self.portfolio_id,
            tuple(
                sleeve if existing.strategy_id == sleeve.strategy_id else existing
                for existing in self.sleeves
            ),
            self.unassigned_cash,
        )

    def without_strategy(self, strategy_id: str) -> MultiStrategyBook:
        """This book with a strategy removed, its sleeve's capital leaving with it."""

        self.sleeve(strategy_id)
        return MultiStrategyBook(
            self.portfolio_id,
            tuple(sleeve for sleeve in self.sleeves if sleeve.strategy_id != strategy_id),
            self.unassigned_cash,
        )

    def holding(self, asset_id: str) -> InstrumentHolding:
        """One instrument aggregated across every strategy holding it.

        Raises:
            PortfolioError: If no strategy holds it, or two hold it at different
                marks or in different currencies -- one instrument in one book
                has one price, and aggregating two would net quantities valued
                differently.
        """

        holders = [
            (sleeve.strategy_id, sleeve.positions[asset_id])
            for sleeve in self.sleeves
            if asset_id in sleeve.positions
        ]
        if not holders:
            raise PortfolioError(f"No strategy in the book holds {asset_id!r}.")
        return _aggregate(asset_id, holders)

    def holdings(self) -> tuple[InstrumentHolding, ...]:
        """Every instrument, aggregated, sorted by asset id. One pass over the sleeves."""

        holders: dict[str, list[tuple[str, Position]]] = {}
        for sleeve in self.sleeves:
            for asset_id, position in sleeve.positions.items():
                holders.setdefault(asset_id, []).append((sleeve.strategy_id, position))
        return tuple(_aggregate(asset_id, holders[asset_id]) for asset_id in sorted(holders))


def _aggregate(asset_id: str, holders: list[tuple[str, Position]]) -> InstrumentHolding:
    """Aggregate one instrument's holders, which arrive in strategy order."""

    marks = sorted({position.market_price for _, position in holders})
    currencies = sorted({position.currency for _, position in holders})
    if len(marks) > 1 or len(currencies) > 1:
        raise PortfolioError(
            f"{asset_id!r} is held at marks {marks} in {currencies} by different strategies. "
            "Mark every sleeve at one price, in one currency, before aggregating them."
        )
    contributions = tuple(
        StrategyContribution(strategy_id, position.quantity) for strategy_id, position in holders
    )
    long_quantity = sum(
        (position.quantity for _, position in holders if position.quantity > 0), Decimal(0)
    )
    short_quantity = sum(
        (-position.quantity for _, position in holders if position.quantity < 0), Decimal(0)
    )
    return InstrumentHolding(
        asset_id=asset_id,
        currency=currencies[0],
        market_price=marks[0],
        net_quantity=long_quantity - short_quantity,
        contributions=contributions,
        long_quantity=long_quantity,
        short_quantity=short_quantity,
        crossed_quantity=min(long_quantity, short_quantity),
    )


# --------------------------------------------------------------------------- #
# Valuation in a reporting currency
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class HoldingValuation:
    """One strategy's holding of one instrument, valued natively and in the reporting currency.

    This is the exposure line :mod:`alphalab.analytics.risk_budget` and
    :mod:`alphalab.analytics.cross_strategy` read.

    Attributes:
        strategy_id: Who holds it.
        asset_id: What is held.
        quantity: Signed quantity.
        market_price: The mark.
        currency: What the holding is denominated in.
        market_value: :attr:`Position.market_value`, in :attr:`currency`.
        reporting_value: The same, converted once to the reporting currency.
        conversion: The conversion performed, or ``None`` when none was needed
            (already in the reporting currency, or zero).
        unrealized_pnl: :attr:`Position.unrealized_pnl`, in :attr:`currency`.
        reporting_unrealized_pnl: The same, converted.
    """

    strategy_id: str
    asset_id: str
    quantity: Decimal
    market_price: Decimal
    currency: str
    market_value: Decimal
    reporting_value: Decimal
    conversion: FxConversion | None
    unrealized_pnl: Decimal
    reporting_unrealized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class StrategyValuation:
    """One strategy's exposure, capital and results, in the reporting currency.

    Attributes:
        strategy_id: The strategy.
        positions: How many instruments it holds.
        long_exposure: Sum of its positive reporting values.
        short_exposure: Sum of its negative reporting values, as a magnitude.
        net_exposure: ``long - short``.
        gross_exposure: ``long + short``.
        cash: Its cash, converted.
        nav: ``cash + net_exposure``: the capital the strategy stands on now.
        unrealized_pnl: Open P&L, converted.
        realized_pnl: Realized P&L per settlement currency -- settlement truth,
            never converted in place.
        reporting_realized_pnl: The same, translated at the valuation instant.
        commission_paid: Per settlement currency.
        reporting_commission_paid: Translated at the valuation instant.
    """

    strategy_id: str
    positions: int
    long_exposure: Decimal
    short_exposure: Decimal
    net_exposure: Decimal
    gross_exposure: Decimal
    cash: Decimal
    nav: Decimal
    unrealized_pnl: Decimal
    realized_pnl: CurrencyAmounts
    reporting_realized_pnl: Decimal
    commission_paid: CurrencyAmounts
    reporting_commission_paid: Decimal


@dataclass(frozen=True, slots=True)
class InstrumentValuation:
    """One instrument across strategies, valued, with each strategy's share kept.

    Attributes:
        holding: The aggregated quantities and contributions.
        market_value: The sum of every holder's native market value. Summed,
            not recomputed from the net quantity, so it reconciles exactly with
            the lines; it can differ from ``to_money(net * price)`` by at most
            half a cent per holder, and :attr:`rounding` says by how much.
        reporting_value: The sum of every holder's reporting value.
        gross_reporting_value: The sum of their magnitudes.
    """

    holding: InstrumentHolding
    market_value: Decimal
    reporting_value: Decimal
    gross_reporting_value: Decimal

    @property
    def rounding(self) -> Decimal:
        """``market_value - net_quantity * market_price``: per-holder rounding, accounted for."""

        return self.market_value - self.holding.net_quantity * self.holding.market_price


@dataclass(frozen=True, slots=True)
class BookValuation:
    """A multi-strategy book, valued in one reporting currency at one instant.

    Attributes:
        portfolio_id: The portfolio.
        book_id: The content that was valued.
        reporting_currency: What every reporting figure is in.
        as_of: The instant every rate was read at.
        lines: Every holding, sorted by strategy then instrument.
        strategies: Every strategy, sorted.
        instruments: Every instrument, sorted.
        exposure_by_currency: Net market value per denomination currency, in
            that currency. No total: that would need a rate, and the reporting
            total is :attr:`net_exposure`.
        reporting_exposure_by_currency: The same, each translated.
        long_exposure: Sum of positive line values.
        short_exposure: Sum of negative line values, as a magnitude.
        net_exposure: ``long - short``.
        gross_exposure: ``long + short``.
        cash: All sleeves' cash plus the unassigned cash, converted.
        unassigned_cash: The unassigned cash alone, converted.
        nav: ``cash + net_exposure``.
        conversions: Every conversion performed, in the order performed.
    """

    portfolio_id: str
    book_id: str
    reporting_currency: str
    as_of: float
    lines: tuple[HoldingValuation, ...]
    strategies: tuple[StrategyValuation, ...]
    instruments: tuple[InstrumentValuation, ...]
    exposure_by_currency: CurrencyAmounts
    reporting_exposure_by_currency: Mapping[str, Decimal]
    long_exposure: Decimal
    short_exposure: Decimal
    net_exposure: Decimal
    gross_exposure: Decimal
    cash: Decimal
    unassigned_cash: Decimal
    nav: Decimal
    conversions: tuple[FxConversion, ...]

    @property
    def valuation_id(self) -> str:
        """The derived identity of this valuation, rates included."""

        return _digest(
            [
                BOOK_VALUATION_SCHEME,
                f"book={self.book_id}",
                f"currency={self.reporting_currency!r}",
                f"as_of={self.as_of!r}",
                *(
                    f"conversion={canonical_text(conversion.amount)}|{conversion.rate.base}|"
                    f"{conversion.rate.quote}|{canonical_text(conversion.rate.rate)}|"
                    f"{conversion.rate.as_of!r}|{conversion.rate.source!r}|"
                    f"{conversion.rate.derived}|{canonical_text(conversion.converted)}"
                    for conversion in self.conversions
                ),
                f"nav={canonical_text(self.nav)}",
            ]
        )

    def strategy(self, strategy_id: str) -> StrategyValuation:
        """One strategy's valuation."""

        for entry in self.strategies:
            if entry.strategy_id == strategy_id:
                return entry
        raise PortfolioError(f"The valuation has no strategy {strategy_id!r}.")

    def instrument(self, asset_id: str) -> InstrumentValuation:
        """One instrument's valuation."""

        for entry in self.instruments:
            if entry.holding.asset_id == asset_id:
                return entry
        raise PortfolioError(f"The valuation has no instrument {asset_id!r}.")


def value_book(
    book: MultiStrategyBook,
    *,
    reporting_currency: str,
    rates: FxRates,
    as_of: float,
) -> BookValuation:
    """Express a book in one reporting currency, keeping every strategy's figures apart.

    Every native figure is converted by
    :meth:`FxRates.convert <alphalab.portfolio.fx.FxRates.convert>` at ``as_of``,
    once, and never when it is already in the reporting currency or zero. Every
    total is a sum of converted figures, so the per-strategy, per-instrument and
    per-currency breakdowns each reconcile with the totals to the cent.

    Args:
        book: The book.
        reporting_currency: The currency to report in.
        rates: The rates to convert with. ``NO_RATES`` is the honest table for
            a book entirely in the reporting currency; any other figure then
            refuses.
        as_of: The instant the conversion is for. A rate dated after it, or
            older than the table tolerates, is refused.

    Raises:
        PortfolioError: If an instrument is held at two marks or currencies.
        MixedCurrencyValuationError, MissingRateError, StaleRateError,
        FutureDatedRateError: From the rate table, exactly as
            :meth:`CurrencyAmounts.total_in` raises them.
    """

    _identifier(reporting_currency, "reporting_currency")
    performed: list[FxConversion] = []

    def convert(amount: Decimal, currency: str) -> tuple[Decimal, FxConversion | None]:
        if currency == reporting_currency or amount == ZERO_MONEY:
            return amount, None
        if not rates:
            raise MixedCurrencyValuationError(
                f"The book holds {amount} {currency} and is reported in {reporting_currency!r}, "
                "and no FX rates were supplied. No honest single figure exists without one; "
                "supply an FxRates table covering the book's currencies."
            )
        conversion = rates.convert(amount, currency, reporting_currency, as_of)
        performed.append(conversion)
        return conversion.converted, conversion

    def translate(amounts: CurrencyAmounts) -> Decimal:
        total = ZERO_MONEY
        for currency in amounts.currencies:
            value, _ = convert(amounts.of(currency), currency)
            total += value
        return total

    holdings = book.holdings()  # refuses an instrument held at two marks before anything converts
    lines: list[HoldingValuation] = []
    strategies: list[StrategyValuation] = []
    for sleeve in book.sleeves:
        sleeve_lines: list[HoldingValuation] = []
        for position in sleeve.positions.values():
            value, conversion = convert(position.market_value, position.currency)
            unrealized, _ = convert(position.unrealized_pnl, position.currency)
            sleeve_lines.append(
                HoldingValuation(
                    strategy_id=sleeve.strategy_id,
                    asset_id=position.asset_id,
                    quantity=position.quantity,
                    market_price=position.market_price,
                    currency=position.currency,
                    market_value=position.market_value,
                    reporting_value=value,
                    conversion=conversion,
                    unrealized_pnl=position.unrealized_pnl,
                    reporting_unrealized_pnl=unrealized,
                )
            )
        lines.extend(sleeve_lines)
        long_exposure = sum(
            (line.reporting_value for line in sleeve_lines if line.reporting_value > 0), ZERO_MONEY
        )
        short_exposure = sum(
            (-line.reporting_value for line in sleeve_lines if line.reporting_value < 0),
            ZERO_MONEY,
        )
        cash = translate(sleeve.cash)
        strategies.append(
            StrategyValuation(
                strategy_id=sleeve.strategy_id,
                positions=len(sleeve_lines),
                long_exposure=long_exposure,
                short_exposure=short_exposure,
                net_exposure=long_exposure - short_exposure,
                gross_exposure=long_exposure + short_exposure,
                cash=cash,
                nav=cash + long_exposure - short_exposure,
                unrealized_pnl=sum(
                    (line.reporting_unrealized_pnl for line in sleeve_lines), ZERO_MONEY
                ),
                realized_pnl=sleeve.realized_pnl,
                reporting_realized_pnl=translate(sleeve.realized_pnl),
                commission_paid=sleeve.commission_paid,
                reporting_commission_paid=translate(sleeve.commission_paid),
            )
        )

    by_asset: dict[str, list[HoldingValuation]] = {}
    for line in lines:
        by_asset.setdefault(line.asset_id, []).append(line)
    instruments = []
    for holding in holdings:
        own = by_asset[holding.asset_id]
        instruments.append(
            InstrumentValuation(
                holding=holding,
                market_value=sum((line.market_value for line in own), ZERO_MONEY),
                reporting_value=sum((line.reporting_value for line in own), ZERO_MONEY),
                gross_reporting_value=sum((abs(line.reporting_value) for line in own), ZERO_MONEY),
            )
        )

    exposure_by_currency = CurrencyAmounts()
    reporting_by_currency: dict[str, Decimal] = {}
    for line in lines:
        exposure_by_currency = exposure_by_currency.add(line.market_value, line.currency)
        reporting_by_currency[line.currency] = (
            reporting_by_currency.get(line.currency, ZERO_MONEY) + line.reporting_value
        )
    unassigned = translate(book.unassigned_cash)
    long_total = sum((entry.long_exposure for entry in strategies), ZERO_MONEY)
    short_total = sum((entry.short_exposure for entry in strategies), ZERO_MONEY)
    cash_total = sum((entry.cash for entry in strategies), ZERO_MONEY) + unassigned
    return BookValuation(
        portfolio_id=book.portfolio_id,
        book_id=book.book_id,
        reporting_currency=reporting_currency,
        as_of=as_of,
        lines=tuple(lines),
        strategies=tuple(strategies),
        instruments=tuple(instruments),
        exposure_by_currency=exposure_by_currency,
        reporting_exposure_by_currency=MappingProxyType(
            dict(sorted(reporting_by_currency.items()))
        ),
        long_exposure=long_total,
        short_exposure=short_total,
        net_exposure=long_total - short_total,
        gross_exposure=long_total + short_total,
        cash=cash_total,
        unassigned_cash=unassigned,
        nav=cash_total + long_total - short_total,
        conversions=tuple(performed),
    )
