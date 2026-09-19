"""Raw prices, adjusted prices, and never being unable to tell which you have.

A price series that has been adjusted for splits and dividends is a *different
series* from the one the exchange printed, and the difference is not cosmetic:
Apple's close on 2014-06-06 was $645.57 raw and $92.22 split-adjusted. A
backtest that sizes positions from one and computes returns from the other is
wrong by 7x and produces no error.

So this module makes the distinction explicit and traceable. Every dataset
carries a :class:`PriceBasis` saying what its prices *are*, every adjustment
applied is returned as an :class:`AdjustmentRecord` naming the action, the
factor and the date, and :func:`apply_adjustments` is the only way to move
between bases.

The boundary, not a vendor integration
--------------------------------------

AlphaLab ships no corporate actions and no reference data to look them up with,
for the reason it ships no holidays and no FX rates: an action feed is
vendor-supplied, differs between vendors, and one invented here would look
authoritative while being incomplete. What is built is the boundary -- the
types an action is expressed in, the arithmetic that applies it, and the
provenance that records it. Supplying the actions is the application's job.

Not ``futures.roll.AdjustmentMethod``
-------------------------------------

:class:`alphalab.futures.roll.AdjustmentMethod` answers a different question:
how to splice *two contracts* into one continuous series across a roll, where
the gap is an artefact of switching instruments and nothing happened to the
company. :class:`PriceBasis` is about one instrument whose share count or cash
value genuinely changed. They are not two spellings of one idea, and
``tests/regression/test_shared_names_stay_distinct.py`` records why.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto

from alphalab.data.exceptions import DataValidationError
from alphalab.data.feed import Bar, Dividend, Split

__all__ = [
    "AdjustmentOutcome",
    "AdjustmentRecord",
    "Delisting",
    "InstrumentLifecycleEvent",
    "Merger",
    "PriceBasis",
    "SymbolChange",
    "apply_adjustments",
]


class PriceBasis(Enum):
    """What the prices in a series actually are."""

    #: Exactly what the venue printed. The only basis whose numbers can be
    #: compared against a broker statement or an exchange bulletin.
    RAW = auto()

    #: Adjusted so that a split does not appear as a price move. Returns are
    #: correct across splits; the levels are not what anyone traded at.
    SPLIT_ADJUSTED = auto()

    #: Adjusted for splits *and* cash dividends, so the series compounds like a
    #: total-return index. What a long-horizon equity study usually wants, and
    #: the basis furthest from what a trader would recognise.
    TOTAL_RETURN = auto()


@dataclass(frozen=True, slots=True)
class AdjustmentRecord:
    """One adjustment applied to a series, and what it was for.

    Attributes:
        action: ``"split"`` or ``"dividend"``.
        symbol: Instrument adjusted.
        effective_timestamp: Unix seconds of the ex-date. Bars strictly before
            this instant are adjusted; bars on or after it are not.
        factor: What prices before the ex-date were multiplied by.
        bars_affected: How many bars the factor was applied to.
        detail: The action in words, including the raw ratio or amount.
    """

    action: str
    symbol: str
    effective_timestamp: float
    factor: float
    bars_affected: int
    detail: str


@dataclass(frozen=True, slots=True)
class AdjustmentOutcome:
    """Adjusted bars, the basis they are now on, and how they got there."""

    bars: tuple[Bar, ...]
    basis: PriceBasis
    adjustments: tuple[AdjustmentRecord, ...]


@dataclass(frozen=True, slots=True)
class SymbolChange:
    """An instrument that started trading under a different name.

    Declared rather than applied. Rewriting history so that a series uses
    today's ticker throughout is a research decision with a look-ahead flavour
    -- it asserts knowledge of a rename that had not happened yet -- so
    AlphaLab records the event and leaves the choice to the caller.
    """

    old_symbol: str
    new_symbol: str
    effective_timestamp: float


@dataclass(frozen=True, slots=True)
class Delisting:
    """An instrument that stopped trading, and why."""

    symbol: str
    effective_timestamp: float
    reason: str


@dataclass(frozen=True, slots=True)
class Merger:
    """One instrument absorbed into another.

    ``share_ratio`` is how many acquirer shares each target share became; cash
    consideration is deliberately separate, because a cash-and-stock deal is
    not expressible as a single ratio and pretending otherwise loses the cash.
    """

    target_symbol: str
    acquirer_symbol: str
    share_ratio: float
    cash_per_share: float
    effective_timestamp: float


#: Events that change what an instrument *is*, as opposed to what it is worth.
type InstrumentLifecycleEvent = SymbolChange | Delisting | Merger


def apply_adjustments(
    bars: Sequence[Bar],
    splits: Sequence[Split],
    dividends: Sequence[Dividend],
    basis: PriceBasis,
) -> AdjustmentOutcome:
    """Move a raw series onto ``basis``, recording every factor applied.

    The input is always assumed to be :attr:`PriceBasis.RAW`, because adjusting
    an already-adjusted series applies the same factor twice and there is no way
    to detect that from the numbers. A caller holding adjusted bars should not
    be calling this.

    Splits are applied to prices *and* volumes -- a 2-for-1 halves the price and
    doubles the shares, and adjusting only the price silently breaks every
    turnover and notional calculation. Dividends are applied to prices only.

    Args:
        bars: Raw bars for one or more instruments, in any order.
        splits: Split actions to apply, for ``SPLIT_ADJUSTED`` and above.
        dividends: Cash dividends, applied only for ``TOTAL_RETURN``.
        basis: The basis to produce.

    Raises:
        DataValidationError: If a split ratio or dividend amount is not
            positive, or if a dividend cannot be applied because no bar
            precedes its ex-date.
    """

    if basis is PriceBasis.RAW:
        if splits or dividends:
            raise DataValidationError(
                "PriceBasis.RAW means the prices the venue printed, so there is nothing to "
                "apply. Passing actions alongside it asks for an adjustment and a claim that "
                "none was made."
            )
        return AdjustmentOutcome(bars=tuple(bars), basis=basis, adjustments=())

    working = list(bars)
    records: list[AdjustmentRecord] = []

    for split in sorted(splits, key=lambda item: item.timestamp):
        if split.ratio <= 0.0:
            raise DataValidationError(
                f"{split.symbol}: split ratio must be positive, got {split.ratio!r}."
            )
        factor = 1.0 / split.ratio
        affected = 0
        for index, bar in enumerate(working):
            if bar.symbol != split.symbol or bar.timestamp >= split.timestamp:
                continue
            working[index] = Bar(
                symbol=bar.symbol,
                timestamp=bar.timestamp,
                open=bar.open * factor,
                high=bar.high * factor,
                low=bar.low * factor,
                close=bar.close * factor,
                volume=bar.volume * split.ratio,
            )
            affected += 1
        records.append(
            AdjustmentRecord(
                action="split",
                symbol=split.symbol,
                effective_timestamp=split.timestamp,
                factor=factor,
                bars_affected=affected,
                detail=(
                    f"{split.ratio:g}-for-1 split: prices before the ex-date multiplied by "
                    f"{factor:.10g}, volumes by {split.ratio:g}"
                ),
            )
        )

    if basis is PriceBasis.TOTAL_RETURN:
        for dividend in sorted(dividends, key=lambda item: item.timestamp):
            if dividend.amount <= 0.0:
                raise DataValidationError(
                    f"{dividend.symbol}: dividend amount must be positive, got {dividend.amount!r}."
                )
            previous = _last_close_before(working, dividend.symbol, dividend.timestamp)
            if previous is None:
                raise DataValidationError(
                    f"{dividend.symbol}: no bar precedes the ex-date {dividend.timestamp!r}, "
                    "so the close the adjustment factor is computed from is unknown. Supply "
                    "the bar before the ex-date or drop the dividend."
                )
            if dividend.amount >= previous:
                raise DataValidationError(
                    f"{dividend.symbol}: dividend {dividend.amount!r} is at or above the "
                    f"prior close {previous!r}, which would adjust prices to zero or below."
                )
            factor = (previous - dividend.amount) / previous
            affected = 0
            for index, bar in enumerate(working):
                if bar.symbol != dividend.symbol or bar.timestamp >= dividend.timestamp:
                    continue
                working[index] = Bar(
                    symbol=bar.symbol,
                    timestamp=bar.timestamp,
                    open=bar.open * factor,
                    high=bar.high * factor,
                    low=bar.low * factor,
                    close=bar.close * factor,
                    volume=bar.volume,
                )
                affected += 1
            records.append(
                AdjustmentRecord(
                    action="dividend",
                    symbol=dividend.symbol,
                    effective_timestamp=dividend.timestamp,
                    factor=factor,
                    bars_affected=affected,
                    detail=(
                        f"cash dividend of {dividend.amount:g} {dividend.currency} against a "
                        f"prior close of {previous:g}: prices before the ex-date multiplied "
                        f"by {factor:.10g}"
                    ),
                )
            )

    return AdjustmentOutcome(bars=tuple(working), basis=basis, adjustments=tuple(records))


def _last_close_before(bars: Sequence[Bar], symbol: str, timestamp: float) -> float | None:
    """The close of the latest bar for ``symbol`` strictly before ``timestamp``."""

    candidates = [bar for bar in bars if bar.symbol == symbol and bar.timestamp < timestamp]
    if not candidates:
        return None
    return max(candidates, key=lambda bar: bar.timestamp).close
