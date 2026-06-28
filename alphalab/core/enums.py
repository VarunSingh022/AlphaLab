"""Core domain enumerations."""

from enum import StrEnum, unique


@unique
class Side(StrEnum):
    """Order and execution direction."""

    BUY = "buy"
    SELL = "sell"


@unique
class OrderType(StrEnum):
    """Supported order execution instructions."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


@unique
class AssetType(StrEnum):
    """Supported financial instrument categories."""

    EQUITY = "equity"
    FUTURE = "future"
    OPTION = "option"
    FOREX = "forex"
    CRYPTO = "crypto"
    CASH = "cash"


@unique
class EventType(StrEnum):
    """Core event categories emitted by the domain layer."""

    SIGNAL = "signal"
    ORDER = "order"
    FILL = "fill"
    TRADE = "trade"
    POSITION = "position"
    PORTFOLIO = "portfolio"


@unique
class TimeInForce(StrEnum):
    """Order lifetime policies."""

    DAY = "day"
    GTC = "good_til_cancelled"
    IOC = "immediate_or_cancel"
    FOK = "fill_or_kill"
