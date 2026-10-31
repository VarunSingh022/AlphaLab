"""A venue's price bands and trading halts, declared as dated inputs (ledger FUT-020).

Venues limit where and when an instrument trades: a daily price band -- a limit
up and a limit down, a collar around a reference price -- outside which no trade
is made, and a halt, during which nothing trades at all. Each is the venue's
published rule or its announcement, and each differs by venue and by instrument,
so nothing is assumed: a :class:`PriceBand` states, for one instrument and one
interval, the prices it trades between and what the venue does with an order
priced beyond them; a :class:`TradingHalt` states one interval in which one
instrument, or every instrument a venue lists, does not trade. A band rule
stated as a percentage of a reference price is turned into its dated bands by
whoever has the reference prices; the engine reads the bands.

What the execution path does with them: an order in a halted instrument is
accepted and rests -- a market order too -- and has its next turn at the first
event after the halt ends, at that event's price; an order priced beyond a band
is refused or has its limit capped to the band, as the band declares; and no
order fills at an event whose price is outside the band in force. These are
distinct from a missing quote, a closed session and an absent fill: each is
refused, rests or fails for its own reason. The intervals' ends are the
engine's to apply, never a strategy's to read: a strategy is told whether an
instrument is halted now and what band is in force now, nothing later.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from typing import Any

from alphalab.instrument.exceptions import InstrumentInputError

__all__ = [
    "BandAction",
    "PriceBand",
    "TradingControls",
    "TradingHalt",
    "controls_from_primitives",
    "controls_to_primitives",
]


class BandAction(Enum):
    """What a venue does with an order priced beyond its band."""

    #: The order is refused.
    REFUSE = auto()
    #: The order's limit is moved to the band's edge.
    CAP = auto()


def _instant(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise InstrumentInputError(f"{what} is an instant in Unix seconds, got {value!r}.")
    return float(value)


@dataclass(frozen=True, slots=True)
class PriceBand:
    """The prices one instrument trades between, from ``start`` until ``end``.

    Attributes:
        asset_id: The instrument.
        start: When the band takes effect.
        end: When it lapses, exclusive -- ``None`` for the rest of the run.
        lower: The limit down: no trade below it.
        upper: The limit up: no trade above it.
        beyond: What the venue does with an order priced beyond the band.
    """

    asset_id: str
    start: float
    end: float | None
    lower: Decimal
    upper: Decimal
    beyond: BandAction

    def __post_init__(self) -> None:
        start = _instant(self.start, "A band's start")
        end = None if self.end is None else _instant(self.end, "A band's end")
        if end is not None and end <= start:
            raise InstrumentInputError(f"A band ends after it starts, got {start} to {end}.")
        for value, what in ((self.lower, "lower"), (self.upper, "upper")):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise InstrumentInputError(f"A band's {what} limit is a finite Decimal.")
        if self.lower >= self.upper:
            raise InstrumentInputError(
                f"A band's limit down is below its limit up, got {self.lower} and {self.upper}."
            )
        if not isinstance(self.beyond, BandAction):
            raise InstrumentInputError(f"beyond is a BandAction, got {self.beyond!r}.")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def in_force(self, at: float) -> bool:
        """Whether the band applies at ``at``."""

        return self.start <= at and (self.end is None or at < self.end)

    def admits(self, price: Decimal) -> bool:
        """Whether ``price`` is within the band, its edges included."""

        return self.lower <= price <= self.upper


@dataclass(frozen=True, slots=True)
class TradingHalt:
    """An interval in which an instrument, or every instrument of a venue, does not trade.

    Attributes:
        start: When trading stops.
        end: When it resumes, exclusive -- ``None`` for the rest of the run.
        asset_id: The instrument halted, or ``None`` when the halt is a venue's.
        venue: The listing venue halted, every instrument it lists, or ``None``.
        reason: What the venue gave, for the record.
    """

    start: float
    end: float | None
    asset_id: str | None = None
    venue: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        start = _instant(self.start, "A halt's start")
        end = None if self.end is None else _instant(self.end, "A halt's end")
        if end is not None and end <= start:
            raise InstrumentInputError(f"A halt ends after it starts, got {start} to {end}.")
        if (self.asset_id is None) == (self.venue is None):
            raise InstrumentInputError(
                "A halt names one instrument or one venue: an instrument's halt, or a "
                "market-wide one across what a venue lists."
            )
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def in_force(self, at: float) -> bool:
        """Whether trading is halted at ``at``."""

        return self.start <= at and (self.end is None or at < self.end)


@dataclass(frozen=True, slots=True)
class TradingControls:
    """Every band and halt a run's venues declare.

    Bands of one instrument do not overlap -- two in force at once would be two
    answers to where it trades -- and are indexed by instrument, halts by
    instrument and by venue, so a lookup reads only what concerns the order.
    """

    bands: tuple[PriceBand, ...] = ()
    halts: tuple[TradingHalt, ...] = ()
    _bands_by_asset: Mapping[str, tuple[PriceBand, ...]] = field(
        init=False, repr=False, compare=False
    )
    _halts_by_asset: Mapping[str, tuple[TradingHalt, ...]] = field(
        init=False, repr=False, compare=False
    )
    _halts_by_venue: Mapping[str, tuple[TradingHalt, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        bands = tuple(sorted(self.bands, key=lambda b: (b.asset_id, b.start)))
        halts = tuple(sorted(self.halts, key=lambda h: (h.asset_id or "", h.venue or "", h.start)))
        if not all(isinstance(b, PriceBand) for b in bands):
            raise InstrumentInputError("Trading controls' bands are PriceBands.")
        if not all(isinstance(h, TradingHalt) for h in halts):
            raise InstrumentInputError("Trading controls' halts are TradingHalts.")
        by_asset: dict[str, list[PriceBand]] = {}
        for band in bands:
            earlier = by_asset.setdefault(band.asset_id, [])
            if earlier and (earlier[-1].end is None or earlier[-1].end > band.start):
                raise InstrumentInputError(
                    f"Two price bands of {band.asset_id} are in force at {band.start!r}."
                )
            earlier.append(band)
        halted_assets: dict[str, list[TradingHalt]] = {}
        halted_venues: dict[str, list[TradingHalt]] = {}
        for halt in halts:
            if halt.asset_id is not None:
                halted_assets.setdefault(halt.asset_id, []).append(halt)
            else:
                assert halt.venue is not None
                halted_venues.setdefault(halt.venue, []).append(halt)
        object.__setattr__(self, "bands", bands)
        object.__setattr__(self, "halts", halts)
        object.__setattr__(self, "_bands_by_asset", {a: tuple(b) for a, b in by_asset.items()})
        object.__setattr__(self, "_halts_by_asset", {a: tuple(h) for a, h in halted_assets.items()})
        object.__setattr__(self, "_halts_by_venue", {v: tuple(h) for v, h in halted_venues.items()})

    @property
    def assets(self) -> frozenset[str]:
        """Every instrument a band or an instrument halt names."""

        return frozenset((*self._bands_by_asset, *self._halts_by_asset))

    def band(self, asset_id: str, at: float) -> PriceBand | None:
        """The band in force for ``asset_id`` at ``at``, or ``None``."""

        for band in self._bands_by_asset.get(asset_id, ()):
            if band.in_force(at):
                return band
        return None

    def halt(self, asset_id: str, venue: str, at: float) -> TradingHalt | None:
        """The halt in force on ``asset_id`` or its listing ``venue`` at ``at``, or ``None``."""

        for halt in (*self._halts_by_asset.get(asset_id, ()), *self._halts_by_venue.get(venue, ())):
            if halt.in_force(at):
                return halt
        return None


def controls_to_primitives(controls: TradingControls | None) -> dict[str, Any] | None:
    """The controls as plain primitives: prices as text, the band action by name."""

    if controls is None:
        return None
    return {
        "bands": [
            {
                "asset_id": b.asset_id,
                "start": b.start,
                "end": b.end,
                "lower": str(b.lower),
                "upper": str(b.upper),
                "beyond": b.beyond.name,
            }
            for b in controls.bands
        ],
        "halts": [
            {
                "start": h.start,
                "end": h.end,
                "asset_id": h.asset_id,
                "venue": h.venue,
                "reason": h.reason,
            }
            for h in controls.halts
        ],
    }


def controls_from_primitives(value: Any, where: str = "controls") -> TradingControls | None:
    """Read controls :func:`controls_to_primitives` wrote.

    Raises:
        InstrumentInputError: If the value is not that shape or does not validate.
    """

    if value is None:
        return None
    try:
        return TradingControls(
            tuple(
                PriceBand(
                    b["asset_id"],
                    b["start"],
                    b["end"],
                    Decimal(b["lower"]),
                    Decimal(b["upper"]),
                    BandAction[b["beyond"]],
                )
                for b in value["bands"]
            ),
            tuple(
                TradingHalt(h["start"], h["end"], h["asset_id"], h["venue"], h["reason"])
                for h in value["halts"]
            ),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise InstrumentInputError(f"{where} is not trading controls: {exc}.") from exc
