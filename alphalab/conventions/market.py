"""One instrument's market conventions, gathered so none of them can be assumed.

Every field here is a fact that changes what a number *means*, and every one of
them has been quietly defaulted somewhere in the history of this repository:
a currency to ``"USD"``, a multiplier to ``1``, an exercise style to American,
a funding interval to eight hours. Each default was right for one market and
wrong for the rest, and each produced a number rather than an error.

:class:`MarketConvention` is the declaration. Nothing in it is optional and
nothing has a default, so an instrument whose conventions nobody stated cannot
be constructed at all -- which is the only version of this that helps.

What it is not
--------------

* **Not an exchange database.** AlphaLab ships no venue's tick schedule, no
  holiday list and no lot table, for the reason ``alphalab.data.calendar`` gives
  at length: an exchange revises them, they differ between the cash and
  derivatives segments of one venue, and a table baked in here would be wrong
  within a year while looking authoritative. This is the contract a caller fills
  in.
* **Not an identity.** :class:`alphalab.instrument.record.InstrumentRecord` owns
  what an instrument *is*, and ``asset_id`` is derived from four fields that do
  not include any of these. A venue revising a lot size must not re-identify
  every position in it, so conventions are deliberately outside the key.
* **Not a calendar.** :attr:`MarketConvention.calendar_id` *names* the venue's
  calendar; :class:`alphalab.data.calendar.MarketCalendar` is the calendar. One
  authority, referenced rather than copied.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from alphalab.conventions.exceptions import ConventionInputError
from alphalab.conventions.lot import LotSpecification
from alphalab.conventions.settlement import SettlementRule
from alphalab.conventions.tick import TickSchedule, TickValue, tick_value

__all__ = ["ContractNotional", "MarketConvention", "contract_notional"]


@dataclass(frozen=True, slots=True)
class MarketConvention:
    """What one instrument's numbers mean on one venue.

    Attributes:
        venue: Where it trades. Conventionally a MIC (``"XNSE"``, ``"XCME"``)
            for a listed market and a venue name for a crypto exchange. This is
            the *listing* venue in the sense
            ``tests/regression/test_venue_concepts_stay_distinct.py`` pins, and
            is neither a market-data attribution nor an execution venue.
        calendar_id: The :class:`~alphalab.data.calendar.MarketCalendar` whose
            sessions this instrument trades in, by its ``calendar_id``. A name,
            not the calendar: the calendar carries holiday data an application
            supplies, and two instruments on one venue share it.
        quote_currency: What a *price* is denominated in. For an FX pair this is
            the quote side; for a commodity future it is the currency per unit.
        settlement_currency: What *cash* moves in. Usually the same as
            ``quote_currency`` and deliberately separate, because a
            quanto or a currency-settled contract is exactly the case where one
            field for both is wrong -- and converting between them is FX, which
            :mod:`alphalab.portfolio.fx` performs with a recorded rate.
        multiplier: Units of the underlying per contract. ``Decimal("1")`` for a
            cash equity, and stated rather than omitted so that
            :func:`contract_notional` never has to assume it.
        tick: The price grid.
        lot: The quantity grid.
        settlement: How a trade date becomes a settlement date.

    Raises:
        ConventionInputError: If any name is blank or the multiplier is not
            positive.
    """

    venue: str
    calendar_id: str
    quote_currency: str
    settlement_currency: str
    multiplier: Decimal
    tick: TickSchedule
    lot: LotSpecification
    settlement: SettlementRule

    def __post_init__(self) -> None:
        for name, value in (
            ("venue", self.venue),
            ("calendar_id", self.calendar_id),
            ("quote_currency", self.quote_currency),
            ("settlement_currency", self.settlement_currency),
        ):
            if not value.strip():
                raise ConventionInputError(
                    f"{name} is blank. A convention that does not say where it applies, or in "
                    "what, is not a convention."
                )
        if self.multiplier <= Decimal("0"):
            raise ConventionInputError(
                f"multiplier is {self.multiplier}; a contract controlling nothing has no "
                "notional and no tick value."
            )

    @property
    def settles_in_quote_currency(self) -> bool:
        """Whether cash moves in the currency prices are quoted in.

        ``False`` means a valuation of this instrument needs an FX rate to reach
        a settlement figure, and :mod:`alphalab.portfolio.fx` is where that rate
        is supplied and recorded.
        """

        return self.quote_currency == self.settlement_currency

    def tick_value_at(self, price: Decimal) -> TickValue:
        """What a one-tick move at ``price`` is worth on one contract.

        Denominated in :attr:`quote_currency`, because the tick is a price
        increment and a price is quoted in the quote currency. Reaching the
        settlement currency is a conversion, and this does not perform one.
        """

        return tick_value(
            tick_size=self.tick.tick_size_at(price),
            multiplier=self.multiplier,
            currency=self.quote_currency,
            price=price,
        )


@dataclass(frozen=True, slots=True)
class ContractNotional:
    """The money value of a contract position, and how it was reached.

    Attributes:
        amount: ``quantity * price * multiplier``, signed the way ``quantity``
            is. In :attr:`currency`.
        currency: The quote currency of the convention it came from.
        quantity: Contracts, signed. Not units of the underlying.
        price: Price per unit of the underlying, as quoted.
        multiplier: What was applied, recorded so a reader can verify it was
            applied once.
        underlying_units: ``quantity * multiplier``. How much of the underlying
            the position controls, which is a *quantity* and not money -- the
            two are separate fields because the whole point of this type is that
            they were being confused.
    """

    amount: Decimal
    currency: str
    quantity: Decimal
    price: Decimal
    multiplier: Decimal
    underlying_units: Decimal


def contract_notional(
    convention: MarketConvention, quantity: Decimal, price: Decimal
) -> ContractNotional:
    """The notional of ``quantity`` contracts marked at ``price``.

    This is the one place in AlphaLab that multiplies a contract count by a
    multiplier. :class:`alphalab.portfolio.position.Position` deliberately has
    no multiplier -- ``market_value`` is ``quantity * market_price`` and means
    *shares* -- so every futures, option and perpetual bridge in the repository
    tells its caller to apply the multiplier themselves. Doing it here, once,
    against a declared convention, is what stops it being done twice or not at
    all.

    Args:
        convention: The instrument's declared conventions.
        quantity: Number of contracts. Signed: negative is short, the same
            convention every ``Position`` in AlphaLab uses.
        price: Price per unit of the underlying.

    Raises:
        ConventionInputError: If ``price`` is negative. Zero is allowed -- an
            expiring worthless option really is worth nothing -- but a negative
            price against a positive multiplier silently flips the sign of an
            exposure, and a market that genuinely prints negative needs to say
            so rather than have it inferred here.
    """

    if price < Decimal("0"):
        raise ConventionInputError(
            f"price is {price}. A negative print is real in some markets and is not assumed "
            "here: it would flip the sign of an exposure with nothing recording that it had."
        )
    return ContractNotional(
        amount=quantity * price * convention.multiplier,
        currency=convention.quote_currency,
        quantity=quantity,
        price=price,
        multiplier=convention.multiplier,
        underlying_units=quantity * convention.multiplier,
    )
