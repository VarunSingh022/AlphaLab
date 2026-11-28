"""Core domain enumerations.

:class:`OrderType` and :class:`TimeInForce` are defined in
:mod:`alphalab.common.order_terms` since v3.11 and re-exported here: a strategy
states an order's terms on its intent, and the strategy package imports nothing
but ``alphalab.common``. They are the same classes.
"""

from enum import StrEnum, unique

from alphalab.common.order_terms import OrderType, TimeInForce

__all__ = [
    "AssetType",
    "EventType",
    "OrderStatus",
    "OrderType",
    "Side",
    "TimeInForce",
]


@unique
class Side(StrEnum):
    """Order and execution direction."""

    BUY = "buy"
    SELL = "sell"


@unique
class OrderStatus(StrEnum):
    """Canonical lifecycle status values for orders."""

    NEW = "new"
    PENDING = "pending"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


@unique
class AssetType(StrEnum):
    """Supported financial instrument categories."""

    EQUITY = "equity"
    FUTURE = "future"
    OPTION = "option"
    FOREX = "forex"
    CRYPTO = "crypto"
    CASH = "cash"
    #: v4.1.1 (ledger FUT-010): a fixed-income instrument. Declares its
    #: economics -- a unit's multiplier on the quoted price -- and its maturity
    #: as its last trading instant; redeemed by a contract settlement.
    BOND = "bond"
    #: v4.1.1 (ledger FUT-013): a reference value -- a benchmark index level a
    #: cash-settled contract settles against. Valued one unit of price per
    #: point unless it declares otherwise, and never traded: a request in it is
    #: refused before an order exists.
    INDEX = "index"
    #: v4.3.0 (ADR-0076): an exchange-traded fund -- a share in every rule it
    #: trades by, classified apart.
    ETF = "etf"
    #: v4.3.0 (ADR-0076): an open-ended fund, dealt at its NAV by forward
    #: pricing: at the fund's first price after its dealing day's close.
    FUND = "fund"
    #: v4.3.0 (ADR-0065): a single-currency interest rate swap, booked by its
    #: declared :class:`~alphalab.rates.terms.SwapTerms`.
    SWAP = "swap"
    #: v4.3.0 (ADR-0065): a forward rate agreement, booked by its declared
    #: :class:`~alphalab.rates.terms.FraTerms`.
    FRA = "fra"


@unique
class EventType(StrEnum):
    """Core event categories emitted by the domain layer."""

    SIGNAL = "signal"
    ORDER = "order"
    FILL = "fill"
    TRADE = "trade"
    POSITION = "position"
    PORTFOLIO = "portfolio"
