"""Continuous futures series construction across contract rolls.

A continuous series stitches together non-overlapping bar segments from a sequence
of individual contract months into one price series, adjusting for the price gap at
each roll so a backtest sees a continuous line rather than an artificial jump purely
from switching contracts. Three standard methods are supported.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum, auto

from alphalab.futures.contract import FutureContract
from alphalab.futures.exceptions import FuturesInputError
from alphalab.market.bar import Bar


class AdjustmentMethod(Enum):
    """How historical segments are adjusted to splice continuously into the newest."""

    UNADJUSTED = auto()
    """Raw prices, concatenated as-is. Preserves true historical levels but contains
    an artificial jump at every roll date equal to the outgoing/incoming price gap."""

    BACK_ADJUSTED = auto()
    """Additive (Panama method): each historical segment is shifted by the
    cumulative sum of roll gaps between it and the current contract. Preserves
    absolute price differences and dollar P&L calculations; can produce zero or
    negative prices for deeply discounted historical segments over many rolls."""

    RATIO_ADJUSTED = auto()
    """Multiplicative: each historical segment is scaled by the cumulative product
    of roll ratios between it and the current contract. Preserves percentage returns
    and avoids negative prices, at the cost of distorting absolute dollar P&L in
    historical segments."""


@dataclass(frozen=True, slots=True)
class RollSegment:
    """One contract's contribution to a continuous series.

    Attributes:
        contract: The contract this segment's bars belong to.
        bars: This segment's bars, ascending, not overlapping with any other
            segment's bars.
        outgoing_roll_price: This contract's price at the moment of rolling out to
            the next segment's contract. Required for every segment except the last
            (current, still-active) segment.
        incoming_roll_price: The next segment's contract's price observed at that
            same moment. Required for every segment except the last segment.
    """

    contract: FutureContract
    bars: tuple[Bar, ...]
    outgoing_roll_price: Decimal | None = None
    incoming_roll_price: Decimal | None = None


def _validate_segments(segments: tuple[RollSegment, ...], method: AdjustmentMethod) -> None:
    if not segments:
        raise FuturesInputError("segments cannot be empty.")

    if method is AdjustmentMethod.UNADJUSTED:
        return

    for i, segment in enumerate(segments[:-1]):
        if segment.outgoing_roll_price is None or segment.incoming_roll_price is None:
            raise FuturesInputError(
                f"Segment {i} ('{segment.contract.underlying_asset_id}') is not the "
                "final segment and must provide both outgoing_roll_price and "
                "incoming_roll_price."
            )


def _shift_bar(bar: Bar, adjustment: Decimal) -> Bar:
    """Returns a new Bar with OHLC and vwap shifted additively. Volume is untouched."""
    return replace(
        bar,
        open=bar.open + adjustment,
        high=bar.high + adjustment,
        low=bar.low + adjustment,
        close=bar.close + adjustment,
        vwap=bar.vwap + adjustment,
    )


def _scale_bar(bar: Bar, ratio: Decimal) -> Bar:
    """Returns a new Bar with OHLC and vwap scaled multiplicatively. Volume is untouched."""
    return replace(
        bar,
        open=bar.open * ratio,
        high=bar.high * ratio,
        low=bar.low * ratio,
        close=bar.close * ratio,
        vwap=bar.vwap * ratio,
    )


def build_continuous_series(
    segments: tuple[RollSegment, ...], method: AdjustmentMethod
) -> tuple[Bar, ...]:
    """Builds one continuous bar series from ordered, non-overlapping contract segments.

    Args:
        segments: Contract segments in ascending chronological order (oldest first,
            current/active contract last).
        method: How to adjust historical segments relative to the current contract.

    Raises:
        FuturesInputError: If segments is empty, or (for BACK_ADJUSTED/RATIO_ADJUSTED)
            any non-final segment is missing roll prices.
    """
    _validate_segments(segments, method)

    if method is AdjustmentMethod.UNADJUSTED:
        return tuple(bar for segment in segments for bar in segment.bars)

    # First pass: walk newest -> oldest, computing each segment's cumulative
    # adjustment relative to the current (last) segment, which is always the anchor.
    segment_adjustment = [Decimal("0")] * len(segments)
    segment_ratio = [Decimal("1")] * len(segments)
    cumulative_add = Decimal("0")
    cumulative_ratio = Decimal("1")

    for i in range(len(segments) - 1, -1, -1):
        segment_adjustment[i] = cumulative_add
        segment_ratio[i] = cumulative_ratio

        if i > 0:
            prior = segments[i - 1]
            assert prior.outgoing_roll_price is not None
            assert prior.incoming_roll_price is not None
            cumulative_add += prior.incoming_roll_price - prior.outgoing_roll_price
            cumulative_ratio *= prior.incoming_roll_price / prior.outgoing_roll_price

    # Second pass: walk oldest -> newest, applying each segment's adjustment. Both
    # segment order and each segment's internal bar order are already correct here,
    # so no reversal is needed.
    result: list[Bar] = []
    for i, segment in enumerate(segments):
        for bar in segment.bars:
            if method is AdjustmentMethod.BACK_ADJUSTED:
                result.append(_shift_bar(bar, segment_adjustment[i]))
            else:
                result.append(_scale_bar(bar, segment_ratio[i]))

    return tuple(result)
