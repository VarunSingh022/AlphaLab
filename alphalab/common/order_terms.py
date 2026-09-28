"""How an order asks to be executed: its type, its prices and how long it lives (ledger EXE-003).

Until v3.11 the canonical path could create only one kind of order. An
:class:`~alphalab.strategy.events.Intent` carried an ``execution_directive``
mapping that nothing read, :class:`~alphalab.core.order_request.OrderRequest`
had no field for an order type, and the pipeline built every OMS order as
``MARKET``, filled or withdrawn at the event it was placed on. A limit, a stop,
an order good until a date, an order for the opening auction: none could be
expressed, simulated or routed.

:class:`OrderTerms` is that missing value. It travels from the intent through
allocation, risk and the OMS to the simulator or the venue unchanged, and it is
checked once, when it is built: a limit order names its limit, a stop order its
stop, an order good until a date the date.

Why this lives in ``alphalab.common``
-------------------------------------

A strategy states its terms on its intent, and ``alphalab.strategy`` imports
nothing but ``alphalab.common`` (ADR-0016; pinned by the v3.7 invariants). The
two vocabularies the terms are written in -- :class:`OrderType` and
:class:`TimeInForce` -- therefore moved here from :mod:`alphalab.core.enums`,
which re-exports them: they are the same classes, so every existing import,
comparison and serialized value is unchanged.

Time in force
-------------

=========  ================================================================
``DAY``    Until the close of its session. A simulated resting order needs
           that close as ``expire_at`` -- the pipeline holds no calendar -- and
           :meth:`~alphalab.data.calendar.MarketCalendar.next_close` gives it.
``GTC``    Until filled or cancelled.
``GTD``    Until ``expire_at``.
``IOC``    Whatever fills at once; the rest is cancelled.
``FOK``    All at once, or nothing.
``OPG``    In the opening auction: at the open of the asset's next daily bar.
``CLS``    In the closing auction: at the close of the asset's next daily bar.
=========  ================================================================

The FIX ``TimeInForce`` values the same names carry (0, 1, 6, 3, 4, 2, 7).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique
from typing import Final

from alphalab.common.arithmetic import canonical_text
from alphalab.common.exceptions import AlphaLabValidationError

__all__ = [
    "MARKET",
    "OrderTerms",
    "OrderType",
    "TimeInForce",
]


@unique
class OrderType(StrEnum):
    """Supported order execution instructions."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


@unique
class TimeInForce(StrEnum):
    """Order lifetime policies. See the module docstring."""

    DAY = "day"
    GTC = "good_til_cancelled"
    IOC = "immediate_or_cancel"
    FOK = "fill_or_kill"
    GTD = "good_til_date"
    OPG = "at_the_opening"
    CLS = "at_the_close"


#: The lifetimes that end at the first attempt, whatever it achieved.
_IMMEDIATE: Final = frozenset({TimeInForce.IOC, TimeInForce.FOK})

#: The lifetimes that are an auction rather than a lifetime.
_AUCTION: Final = frozenset({TimeInForce.OPG, TimeInForce.CLS})


def _price(value: Decimal | None, name: str) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite():
        raise AlphaLabValidationError(f"An order's {name} must be a finite Decimal, got {value!r}.")


@dataclass(frozen=True, slots=True)
class OrderTerms:
    """How one order asks to be executed.

    Equal terms are the same terms whatever their spelling -- ``Decimal`` compares
    and hashes by value -- which is what lets allocation net the intents that
    share them into one order.

    Attributes:
        order_type: Market, limit, stop or stop-limit.
        limit_price: The worst price a limit or stop-limit order accepts.
        stop_price: The price a stop or stop-limit order is triggered at.
        time_in_force: How long it lives. See the module docstring.
        expire_at: When a ``GTD`` order -- or a ``DAY`` order whose session close
            the caller knows -- stops working, in Unix seconds.

    Raises:
        AlphaLabValidationError: If a price is missing for the type or given
            for a type that has none, a price is not finite, ``expire_at`` is
            missing for ``GTD`` or given for a lifetime that has none, or an
            auction order is a stop.
    """

    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    expire_at: float | None = None

    def __post_init__(self) -> None:
        _price(self.limit_price, "limit price")
        _price(self.stop_price, "stop price")
        needs_limit = self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT)
        needs_stop = self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT)
        if needs_limit != (self.limit_price is not None):
            raise AlphaLabValidationError(
                f"A {self.order_type.value} order "
                + ("names its limit price." if needs_limit else "has no limit price.")
            )
        if needs_stop != (self.stop_price is not None):
            raise AlphaLabValidationError(
                f"A {self.order_type.value} order "
                + ("names its stop price." if needs_stop else "has no stop price.")
            )
        if self.time_in_force is TimeInForce.GTD:
            if self.expire_at is None or not math.isfinite(self.expire_at):
                raise AlphaLabValidationError(
                    f"A good-til-date order names the instant it expires, got {self.expire_at!r}."
                )
        elif self.expire_at is not None:
            if self.time_in_force is not TimeInForce.DAY:
                raise AlphaLabValidationError(
                    f"A {self.time_in_force.value} order has no expiry instant; only "
                    "good-til-date and day orders do."
                )
            if not math.isfinite(self.expire_at):
                raise AlphaLabValidationError(
                    f"A day order's session close must be an instant, got {self.expire_at!r}."
                )
        if self.time_in_force in _AUCTION and needs_stop:
            raise AlphaLabValidationError(
                f"An auction order is a market or a limit order, not a {self.order_type.value}."
            )

    # --- the usual ones -------------------------------------------------------

    @classmethod
    def market(cls, time_in_force: TimeInForce = TimeInForce.DAY) -> OrderTerms:
        """A market order."""

        return cls(OrderType.MARKET, time_in_force=time_in_force)

    @classmethod
    def limit(
        cls,
        price: Decimal,
        time_in_force: TimeInForce = TimeInForce.GTC,
        expire_at: float | None = None,
    ) -> OrderTerms:
        """A limit order at ``price``, good until cancelled unless told otherwise."""

        return cls(OrderType.LIMIT, price, None, time_in_force, expire_at)

    @classmethod
    def stop(
        cls,
        price: Decimal,
        time_in_force: TimeInForce = TimeInForce.GTC,
        expire_at: float | None = None,
    ) -> OrderTerms:
        """A stop order: a market order once the market trades through ``price``."""

        return cls(OrderType.STOP, None, price, time_in_force, expire_at)

    @classmethod
    def stop_limit(
        cls,
        stop: Decimal,
        limit: Decimal,
        time_in_force: TimeInForce = TimeInForce.GTC,
        expire_at: float | None = None,
    ) -> OrderTerms:
        """A stop-limit order: a limit order at ``limit`` once triggered at ``stop``."""

        return cls(OrderType.STOP_LIMIT, limit, stop, time_in_force, expire_at)

    @classmethod
    def at_open(cls, limit: Decimal | None = None) -> OrderTerms:
        """An order for the opening auction; a limit-on-open when ``limit`` is given."""

        if limit is None:
            return cls(OrderType.MARKET, time_in_force=TimeInForce.OPG)
        return cls(OrderType.LIMIT, limit, time_in_force=TimeInForce.OPG)

    @classmethod
    def at_close(cls, limit: Decimal | None = None) -> OrderTerms:
        """An order for the closing auction; a limit-on-close when ``limit`` is given."""

        if limit is None:
            return cls(OrderType.MARKET, time_in_force=TimeInForce.CLS)
        return cls(OrderType.LIMIT, limit, time_in_force=TimeInForce.CLS)

    # --- what they mean -------------------------------------------------------

    @property
    def is_immediate(self) -> bool:
        """Whether the order ends at its first attempt: ``IOC`` or ``FOK``."""

        return self.time_in_force in _IMMEDIATE

    @property
    def is_auction(self) -> bool:
        """Whether the order is for an opening or closing auction."""

        return self.time_in_force in _AUCTION

    @property
    def rests(self) -> bool:
        """Whether the order can wait for a price: a limit, a stop, or an auction.

        A market order is worked once, at the event it reaches the market, and
        what it could not fill there is withdrawn -- the rule the pipeline has
        kept since v2.5 -- so its time in force never keeps it working.
        """

        if self.is_immediate:
            return False
        return self.is_auction or self.order_type is not OrderType.MARKET

    def rendering(self) -> str:
        """The terms as one line, prices by value."""

        parts = [self.order_type.value, self.time_in_force.value]
        if self.limit_price is not None:
            parts.append(f"limit={canonical_text(self.limit_price)}")
        if self.stop_price is not None:
            parts.append(f"stop={canonical_text(self.stop_price)}")
        if self.expire_at is not None:
            parts.append(f"expires={self.expire_at!r}")
        return " ".join(parts)


#: A market order good for the day: what every order was until v3.11.
MARKET: Final = OrderTerms()
