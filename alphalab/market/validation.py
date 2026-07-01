"""Validation rules for market data integrity."""

from decimal import Decimal

from alphalab.market.bar import Bar
from alphalab.market.exceptions import MarketValidationError
from alphalab.market.quote import Quote
from alphalab.market.snapshot import OrderBookSnapshot
from alphalab.market.tick import Tick
from alphalab.market.timestamp import is_valid_timestamp


def validate_tick(tick: Tick) -> None:
    if tick.price < Decimal("0"):
        raise MarketValidationError("Tick price cannot be negative.")
    if tick.quantity < Decimal("0"):
        raise MarketValidationError("Tick quantity cannot be negative.")
    if not is_valid_timestamp(tick.timestamp):
        raise MarketValidationError("Invalid timestamp for tick.")


def validate_quote(quote: Quote) -> None:
    if quote.bid < Decimal("0") or quote.ask < Decimal("0"):
        raise MarketValidationError("Quote prices cannot be negative.")
    if quote.ask < quote.bid:
        raise MarketValidationError("Negative spread: Ask is less than Bid.")
    if quote.bid_size < Decimal("0") or quote.ask_size < Decimal("0"):
        raise MarketValidationError("Quote sizes cannot be negative.")
    if not is_valid_timestamp(quote.timestamp):
        raise MarketValidationError("Invalid timestamp for quote.")


def validate_bar(bar: Bar) -> None:
    if bar.high < bar.low:
        raise MarketValidationError("Bar high cannot be less than low.")
    if bar.volume < Decimal("0"):
        raise MarketValidationError("Bar volume cannot be negative.")
    if bar.trade_count < 0:
        raise MarketValidationError("Bar trade count cannot be negative.")
    if not is_valid_timestamp(bar.timestamp):
        raise MarketValidationError("Invalid timestamp for bar.")


def validate_snapshot(snapshot: OrderBookSnapshot) -> None:
    if not is_valid_timestamp(snapshot.timestamp):
        raise MarketValidationError("Invalid timestamp for snapshot.")

    for level in snapshot.bids:
        if level.price < Decimal("0") or level.size < Decimal("0"):
            raise MarketValidationError("Negative values in bid levels.")

    for level in snapshot.asks:
        if level.price < Decimal("0") or level.size < Decimal("0"):
            raise MarketValidationError("Negative values in ask levels.")

    if snapshot.bids and snapshot.asks and snapshot.asks[0].price < snapshot.bids[0].price:
        raise MarketValidationError("Crossed book in snapshot: Ask < Bid.")
