"""Positions that are contracts, not shares -- and the multiplier applied once.

:class:`~alphalab.portfolio.position.Position` has no multiplier, deliberately
and since v1. ``market_value`` is ``quantity * market_price``, which is exactly
right for a share and is a thousand times wrong for a contract on a thousand
barrels. Every contract bridge in the repository says so in its own docstring
and tells the caller to apply the multiplier themselves:

    "economic exposure is ``quantity * price * contract.multiplier``, computed
    by the caller, not stored here -- Position itself has no concept of a
    multiplier, consistent with it never being modified for options."

That instruction is correct and it has one failure mode, which is the whole
reason this module exists: **the caller can apply it twice, or not at all, and
nothing notices.** A doubled multiplier produces a number that looks like an
exposure. ``alphalab.data.assets`` opens by naming this as the way "a backtest
silently computes P&L that is 1,000x wrong".

So a :class:`ContractHolding` pairs a position with the convention that says
what its numbers mean, and every figure here goes through
:func:`alphalab.conventions.market.contract_notional`, which is the single site
in AlphaLab where a contract count is multiplied by a multiplier. A caller who
has already scaled their quantity into underlying units states a multiplier of
one, which is true and is visible.

Not a second valuation authority
---------------------------------

:class:`~alphalab.portfolio.valuation.PortfolioValuation` remains the authority
on what a **book** is worth: cash plus positions, in one named currency,
refusing a mixed book it has no rates for. Nothing here re-derives equity, NAV
or P&L, and nothing here reads a cash ledger.

This module answers a different question -- *how much of the underlying does
this book control, and in what* -- which ``PortfolioValuation`` cannot answer at
all, because ``Position`` does not carry the multiplier it would need.
``tests/regression/test_shared_names_stay_distinct.py`` records the split.

Quote currency and settlement currency
---------------------------------------

A notional is denominated in the **quote** currency, because a price is. That is
frequently not the currency the contract settles in, and
:func:`settlement_exposures` converts between them with a recorded rate through
:class:`~alphalab.portfolio.fx.FxRates` -- never by assuming they are the same.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from alphalab.conventions.market import ContractNotional, MarketConvention, contract_notional
from alphalab.portfolio.amounts import CurrencyAmounts
from alphalab.portfolio.exceptions import PortfolioError
from alphalab.portfolio.fx import FxConversion, FxRates
from alphalab.portfolio.position import Position

__all__ = [
    "ContractExposure",
    "ContractHolding",
    "SettlementExposure",
    "contract_exposures",
    "holding_notional",
    "settlement_exposures",
]


@dataclass(frozen=True, slots=True)
class ContractHolding:
    """A position, and the convention that says what its numbers mean.

    Attributes:
        position: The holding. Its ``quantity`` is a **contract count**, not a
            count of the underlying, and its ``currency`` is what the position
            settles in.
        convention: The instrument's declared conventions.

    Raises:
        PortfolioError: If the position's currency is not the convention's
            settlement currency. The two describe the same fact and a
            disagreement means one of them is about a different instrument --
            which, unchecked, produces a correctly-computed exposure attributed
            to the wrong currency.
    """

    position: Position
    convention: MarketConvention

    def __post_init__(self) -> None:
        if self.position.currency != self.convention.settlement_currency:
            raise PortfolioError(
                f"{self.position.asset_id} settles in {self.position.currency} and its "
                f"convention on {self.convention.venue} settles in "
                f"{self.convention.settlement_currency}. One of the two describes a "
                "different instrument, and an exposure computed from both would be right "
                "about the amount and wrong about the currency."
            )


@dataclass(frozen=True, slots=True)
class ContractExposure:
    """What a book of contracts controls, per quote currency.

    Attributes:
        by_asset: Each holding's notional, keyed by ``asset_id``, in that
            instrument's quote currency.
        net: Signed notional per quote currency -- longs and shorts offsetting.
        gross: Absolute notional per quote currency. Never offsetting, which is
            what makes it the measure a margin or a limit reads.
        underlying_units: Signed units of the underlying controlled, keyed by
            ``asset_id``. A **quantity**, not money, and in its own field for
            that reason: 10 contracts on 1,000 barrels is 10,000 barrels and
            also some number of dollars, and the two are not interchangeable.
    """

    by_asset: Mapping[str, ContractNotional]
    net: CurrencyAmounts
    gross: CurrencyAmounts
    underlying_units: Mapping[str, Decimal]


def holding_notional(holding: ContractHolding) -> ContractNotional:
    """One holding's notional, at its position's current mark.

    The mark is :attr:`Position.market_price`, which is whatever the book was
    last marked at -- this does not fetch, interpolate or stale-check a price.
    Whether that mark is current is the caller's concern and
    :attr:`Position.last_updated` is what records it.
    """

    return contract_notional(
        holding.convention, holding.position.quantity, holding.position.market_price
    )


def contract_exposures(holdings: Sequence[ContractHolding]) -> ContractExposure:
    """Notional and underlying units across a book of contracts.

    Net and gross are reported **per quote currency** and never summed across
    currencies: adding a yen notional to a dollar one is the figure ADR-0020
    removed, and :meth:`~alphalab.portfolio.amounts.CurrencyAmounts.total_in`
    is what converts when a caller supplies rates and names a currency.

    Raises:
        PortfolioError: If two holdings name the same ``asset_id``. Which
            position is the book's is not a question this resolves by picking,
            and summing them would double an exposure that was declared twice by
            mistake.
    """

    by_asset: dict[str, ContractNotional] = {}
    units: dict[str, Decimal] = {}
    net = CurrencyAmounts()
    gross = CurrencyAmounts()
    for holding in holdings:
        asset_id = holding.position.asset_id
        if asset_id in by_asset:
            raise PortfolioError(
                f"{asset_id} appears in two holdings. A book holds one position per "
                "instrument, and summing two would double an exposure that was declared "
                "twice rather than held twice."
            )
        notional = holding_notional(holding)
        by_asset[asset_id] = notional
        units[asset_id] = notional.underlying_units
        net = net.add(notional.amount, notional.currency)
        gross = gross.add(abs(notional.amount), notional.currency)
    return ContractExposure(by_asset=by_asset, net=net, gross=gross, underlying_units=units)


@dataclass(frozen=True, slots=True)
class SettlementExposure:
    """One holding's notional restated in the currency it settles in.

    Attributes:
        asset_id: The instrument.
        quoted: The notional as quoted, in the quote currency.
        settled: The same amount in the settlement currency.
        conversion: The conversion performed, or ``None`` when the two
            currencies are the same and none was needed. ``None`` rather than an
            identity conversion, so that a report can tell a converted figure
            from one that never needed converting.
    """

    asset_id: str
    quoted: ContractNotional
    settled: Decimal
    conversion: FxConversion | None


def settlement_exposures(
    holdings: Sequence[ContractHolding], rates: FxRates, as_of: float
) -> tuple[SettlementExposure, ...]:
    """Restate each holding's notional in the currency it settles in.

    For the common case -- an instrument quoted and settled in one currency --
    nothing is converted and :attr:`SettlementExposure.conversion` is ``None``.
    For a quanto or a currency-settled contract, the conversion is performed
    once and the rate is recorded on the result.

    Args:
        holdings: The book.
        rates: Supplied rates. No rate is invented, no direction is inverted and
            nothing is triangulated: a missing pair raises
            :class:`~alphalab.portfolio.fx.MissingRateError` naming it.
        as_of: The instant the conversion is for. A rate dated after it raises
            :class:`~alphalab.portfolio.fx.FutureDatedRateError`, and one older
            than the table's tolerance raises
            :class:`~alphalab.portfolio.fx.StaleRateError`.

    Returns:
        One entry per holding, in the order given.
    """

    results: list[SettlementExposure] = []
    for holding in holdings:
        notional = holding_notional(holding)
        settlement_currency = holding.convention.settlement_currency
        if holding.convention.settles_in_quote_currency:
            results.append(
                SettlementExposure(
                    asset_id=holding.position.asset_id,
                    quoted=notional,
                    settled=notional.amount,
                    conversion=None,
                )
            )
            continue
        conversion = rates.convert(notional.amount, notional.currency, settlement_currency, as_of)
        results.append(
            SettlementExposure(
                asset_id=holding.position.asset_id,
                quoted=notional,
                settled=conversion.converted,
                conversion=conversion,
            )
        )
    return tuple(results)
