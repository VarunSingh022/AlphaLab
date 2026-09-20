"""What differs between one crypto venue and the next, declared rather than assumed.

The same pair on two exchanges is genuinely two instruments --
:class:`alphalab.data.assets.CryptoSpec` already says so, and requires a venue
for that reason. This module carries the rest of the difference: how often
funding is charged, what a trade costs, which price the venue marks against, and
how small an order it accepts.

None of it is derivable and none of it is universal:

* **Funding interval.** Eight hours is the most common and is a convention, not
  a rule. Some venues fund hourly, some every four hours, and one venue can use
  different intervals for different contracts.
* **Fees.** Maker and taker are different numbers, a maker fee is frequently
  *negative* (a rebate), and both move with a fee tier the account earns.
* **Price source.** A perpetual marks against an index, a basket or its own last
  trade depending on the venue, and a liquidation computed against the wrong one
  is computed against a price that never printed.
* **Minimum notional.** Below it the venue rejects, so a backtest that fills
  those orders has filled orders that could not exist.

So :class:`VenueSpecification` is a declaration with no defaults, and nothing
here reaches a venue: AlphaLab holds no exchange adapter, no API client and no
credential. This is metadata a caller supplies, in the same way holidays and FX
rates are.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto

from alphalab.crypto.exceptions import CryptoInputError

__all__ = [
    "BASIS_POINT",
    "FeeSchedule",
    "LiquidityRole",
    "PriceSource",
    "VenueDispersion",
    "VenueSpecification",
    "cross_venue_dispersion",
    "trading_fee",
]

#: One basis point as a fraction. Fees are quoted in basis points everywhere in
#: this market and a schedule stated in fractions invites a 100x error, so the
#: conversion happens once, here.
BASIS_POINT = Decimal("0.0001")


class LiquidityRole(Enum):
    """Whether an order added liquidity or removed it.

    Required at every fee computation. The two are different numbers on every
    venue and the maker side is often a rebate, so assuming one is not a small
    approximation -- it can be wrong in sign.
    """

    #: Rested on the book and was hit. Frequently rebated.
    MAKER = auto()

    #: Crossed the spread and removed liquidity.
    TAKER = auto()


class PriceSource(Enum):
    """Which price a venue marks positions against.

    A label, not a price. It exists so a research path can *state* which series
    it used and refuse to mix two, rather than silently reconciling an index
    mark against a last-trade mark.
    """

    #: A composite of several venues' prices. What most perpetual venues mark
    #: against, precisely so a single venue's print cannot trigger liquidations.
    INDEX = auto()

    #: The venue's own published mark, which is usually the index plus a
    #: funding-basis adjustment and is not the index itself.
    MARK = auto()

    #: The venue's last trade. The one a manipulated print moves.
    LAST_TRADE = auto()


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """What a venue charges, in basis points of notional.

    Attributes:
        maker_bps: Basis points charged when adding liquidity. May be negative,
            which is a rebate and is common.
        taker_bps: Basis points charged when removing liquidity.

    Raises:
        CryptoInputError: If the taker fee is negative. A venue paying to be
            hit on both sides of every trade is not a fee schedule, and
            accepting one would turn every backtest into a money printer.
    """

    maker_bps: Decimal
    taker_bps: Decimal

    def __post_init__(self) -> None:
        if self.taker_bps < Decimal("0"):
            raise CryptoInputError(
                f"taker_bps is {self.taker_bps}. A rebate for removing liquidity would pay a "
                "strategy for every trade it makes, which no venue does."
            )

    def rate_for(self, role: LiquidityRole) -> Decimal:
        """The fee for one role, as a fraction of notional."""

        bps = self.maker_bps if role is LiquidityRole.MAKER else self.taker_bps
        return bps * BASIS_POINT


@dataclass(frozen=True, slots=True)
class VenueSpecification:
    """One venue's declared trading conditions.

    Attributes:
        venue: The venue's name, matching
            :attr:`alphalab.crypto.instrument.CryptoInstrument.exchange`.
        funding_interval_hours: Hours between funding payments on this venue's
            perpetuals. Required, and the same figure
            :attr:`alphalab.crypto.funding.FundingRate.interval_hours` carries
            per observation -- this is the venue's declared schedule, that is
            what an observation reported, and a research path can compare them.
        fees: The fee schedule.
        price_source: Which price this venue marks against.
        minimum_notional: The smallest order the venue accepts, in the quote
            asset. Required, because a strategy whose orders fall below it does
            not trade at all and a backtest that fills them is not a backtest of
            anything.
        settlement_asset: What positions settle in -- ``"USDT"`` for a linear
            perpetual, the base asset for an inverse one. Required, because the
            two produce P&L in different currencies from the same price move.

    Raises:
        CryptoInputError: If any name is blank, the interval is not positive, or
            the minimum notional is negative.
    """

    venue: str
    funding_interval_hours: int
    fees: FeeSchedule
    price_source: PriceSource
    minimum_notional: Decimal
    settlement_asset: str

    def __post_init__(self) -> None:
        for name, value in (
            ("venue", self.venue),
            ("settlement_asset", self.settlement_asset),
        ):
            if not value.strip():
                raise CryptoInputError(f"{name} is blank; a venue specification names both.")
        if self.funding_interval_hours <= 0:
            raise CryptoInputError(
                f"{self.venue}: funding_interval_hours is {self.funding_interval_hours}; a "
                "non-positive interval would annualize to infinity."
            )
        if self.minimum_notional < Decimal("0"):
            raise CryptoInputError(f"{self.venue}: minimum_notional is {self.minimum_notional}.")

    @property
    def funding_intervals_per_year(self) -> Decimal:
        """How many funding payments a year this venue's schedule implies."""

        return Decimal(24 * 365) / Decimal(self.funding_interval_hours)


def trading_fee(
    specification: VenueSpecification, notional: Decimal, role: LiquidityRole
) -> Decimal:
    """The cash flow a trade's fee produces, signed.

    Negative is paid out, positive is received -- the same convention
    :func:`alphalab.crypto.funding.compute_funding_payment` uses, so the two can
    be added without either being negated first.

    ``notional`` is the absolute value of what traded, in the quote asset: a fee
    is charged on a sale exactly as on a purchase, so a signed notional would
    rebate one side.
    """

    if notional < Decimal("0"):
        raise CryptoInputError(
            f"notional is {notional}. A fee is charged on the size of a trade, not its "
            "direction; a signed notional would credit the sell side."
        )
    return -(notional * specification.fees.rate_for(role))


@dataclass(frozen=True, slots=True)
class VenueDispersion:
    """How far apart two or more venues priced the same thing at one instant.

    Attributes:
        prices: What each venue quoted, keyed by venue.
        lowest_venue: Which venue was cheapest.
        highest_venue: Which was dearest.
        lowest: The cheapest price.
        highest: The dearest price.
        spread: ``highest - lowest``, in the quote asset.
        relative_spread: ``spread / lowest``, a fraction. Separate from
            :attr:`spread` because one is a price and the other is
            dimensionless, and a field holding either would let them be
            compared.
    """

    prices: Mapping[str, Decimal]
    lowest_venue: str
    highest_venue: str
    lowest: Decimal
    highest: Decimal
    spread: Decimal
    relative_spread: Decimal


def cross_venue_dispersion(prices: Mapping[str, Decimal]) -> VenueDispersion:
    """How far apart venues priced one instrument at one instant.

    Every price must be for the same pair at the same instant and in the same
    quote asset; nothing here can check that, so it is the caller's assertion.
    What this does check is that there are at least two venues -- the dispersion
    of one venue is zero, which is a true statement about nothing and reads as
    if the venues agreed.

    Ties break on the venue name, so the result is deterministic when two
    venues quote identically.

    Raises:
        CryptoInputError: If fewer than two venues are supplied, or any price is
            not positive.
    """

    if len(prices) < 2:
        raise CryptoInputError(
            f"Dispersion across {len(prices)} venue(s) is not a measurement of disagreement. "
            "Supply at least two."
        )
    for venue, price in prices.items():
        if price <= Decimal("0"):
            raise CryptoInputError(f"{venue} quotes {price}, which is not a price.")

    ordered = sorted(prices.items(), key=lambda item: (item[1], item[0]))
    lowest_venue, lowest = ordered[0]
    highest_venue, highest = ordered[-1]
    return VenueDispersion(
        prices=dict(prices),
        lowest_venue=lowest_venue,
        highest_venue=highest_venue,
        lowest=lowest,
        highest=highest,
        spread=highest - lowest,
        relative_spread=(highest - lowest) / lowest,
    )
