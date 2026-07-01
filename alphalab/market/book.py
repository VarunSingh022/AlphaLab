"""Pure functional utilities for order book calculations."""

from decimal import Decimal

from alphalab.market.snapshot import OrderBookSnapshot


def best_bid(snapshot: OrderBookSnapshot) -> Decimal:
    """Returns the highest bid price, or 0 if no bids exist."""
    return snapshot.bids[0].price if snapshot.bids else Decimal("0.00")


def best_ask(snapshot: OrderBookSnapshot) -> Decimal:
    """Returns the lowest ask price, or 0 if no asks exist."""
    return snapshot.asks[0].price if snapshot.asks else Decimal("0.00")


def spread(snapshot: OrderBookSnapshot) -> Decimal:
    """Calculates the absolute spread between best bid and best ask."""
    if not snapshot.bids or not snapshot.asks:
        return Decimal("0.00")
    return best_ask(snapshot) - best_bid(snapshot)


def mid_price(snapshot: OrderBookSnapshot) -> Decimal:
    """Calculates the standard mid price."""
    if not snapshot.bids or not snapshot.asks:
        return Decimal("0.00")
    return (best_bid(snapshot) + best_ask(snapshot)) / Decimal("2.0")


def weighted_mid(snapshot: OrderBookSnapshot) -> Decimal:
    """Calculates the volume-weighted mid price (Micro-price)."""
    if not snapshot.bids or not snapshot.asks:
        return Decimal("0.00")

    bid_p = snapshot.bids[0].price
    bid_s = snapshot.bids[0].size
    ask_p = snapshot.asks[0].price
    ask_s = snapshot.asks[0].size

    imbalance = bid_s + ask_s
    if imbalance == Decimal("0.00"):
        return mid_price(snapshot)

    # Standard weighted mid formula: (Bid * AskSize + Ask * BidSize) / (BidSize + AskSize)
    weighted = ((bid_p * ask_s) + (ask_p * bid_s)) / imbalance
    return weighted.quantize(Decimal("0.000001"))
