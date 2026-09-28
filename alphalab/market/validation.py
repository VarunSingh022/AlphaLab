"""Validation rules for market data integrity.

A price is data, and since v3.11 its sign is not this layer's question (ledger
ACC-007): a crude oil future printed below zero in April 2020, and power and
spread instruments routinely do, so a market engine that refused every
negative print could not carry them at all. Whether a price is one the
instrument can trade or be marked at is what the instrument's declared
economics say, and the execution pipeline -- which holds the registry that
declares them -- asks. Sizes, quantities, crossed quotes and inverted bars are
still refused here: none of them is a price.
"""

from decimal import Decimal

from alphalab.market.bar import Bar
from alphalab.market.exceptions import MarketValidationError
from alphalab.market.quote import Quote
from alphalab.market.snapshot import OrderBookSnapshot
from alphalab.market.tick import Tick
from alphalab.market.timestamp import is_valid_timestamp


def validate_tick(tick: Tick) -> None:
    if not tick.price.is_finite():
        raise MarketValidationError("Tick price must be a finite number.")
    if tick.quantity < Decimal("0"):
        raise MarketValidationError("Tick quantity cannot be negative.")
    if not is_valid_timestamp(tick.timestamp):
        raise MarketValidationError("Invalid timestamp for tick.")


def validate_quote(quote: Quote) -> None:
    if not quote.bid.is_finite() or not quote.ask.is_finite():
        raise MarketValidationError("Quote prices must be finite numbers.")
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
    if bar.trade_count is not None and bar.trade_count < 0:
        raise MarketValidationError("Bar trade count cannot be negative.")
    if bar.vwap is not None and not bar.low <= bar.vwap <= bar.high:
        raise MarketValidationError(
            f"Bar vwap {bar.vwap} lies outside its own range [{bar.low}, {bar.high}]; a "
            "volume-weighted average of prices traded in the bar cannot."
        )
    if not is_valid_timestamp(bar.timestamp):
        raise MarketValidationError("Invalid timestamp for bar.")


def validate_snapshot(snapshot: OrderBookSnapshot) -> None:
    if not is_valid_timestamp(snapshot.timestamp):
        raise MarketValidationError("Invalid timestamp for snapshot.")

    for level in snapshot.bids:
        if not level.price.is_finite() or level.size < Decimal("0"):
            raise MarketValidationError("Non-finite price or negative size in bid levels.")

    for level in snapshot.asks:
        if not level.price.is_finite() or level.size < Decimal("0"):
            raise MarketValidationError("Non-finite price or negative size in ask levels.")

    if snapshot.bids and snapshot.asks and snapshot.asks[0].price < snapshot.bids[0].price:
        raise MarketValidationError("Crossed book in snapshot: Ask < Bid.")
