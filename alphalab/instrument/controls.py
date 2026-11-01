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

from alphalab.conventions.tick import RoundingDirection, TickSchedule, round_to_tick
from alphalab.instrument.exceptions import InstrumentInputError

__all__ = [
    "AuctionIndication",
    "AuctionInterest",
    "AuctionPriority",
    "AuctionTieBreak",
    "AuctionUncross",
    "BandAction",
    "BandReference",
    "PriceBand",
    "PublishedAuction",
    "ReferenceBand",
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
class BandReference:
    """A venue's reference price for one instrument, from ``effective_at`` (FUT-028, phase 3).

    The price a reference-price band is computed around -- the previous
    session's official close, a static reference after an auction -- published
    by the venue and declared, dated, as it was published: a correction is a
    later entry, effective from when it was made.

    Attributes:
        asset_id: The instrument.
        effective_at: When it takes effect.
        price: The reference, in the instrument's currency; above zero.
        source: What published it, for the record.
    """

    asset_id: str
    effective_at: float
    price: Decimal
    source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "effective_at", _instant(self.effective_at, "A reference's instant")
        )
        if not isinstance(self.price, Decimal) or not self.price.is_finite() or self.price <= 0:
            raise InstrumentInputError(f"A reference price is above zero, got {self.price!r}.")


@dataclass(frozen=True, slots=True)
class ReferenceBand:
    """A venue's band rule around a declared reference price (FUT-028, phase 3).

    From ``start`` until ``end`` the instrument trades within the band the
    reference in effect gives: below and above it by a rate of it -- ten per
    cent either side -- or by an absolute amount. A reference older than
    ``stale_after`` seconds is not current, and with no current reference the
    venue's limits are unknown: every order rests, as in a halt, until one is.
    The band's edges are rounded inward to the instrument's tick grid when it
    declares one.

    Attributes:
        asset_id: The instrument.
        start: When the rule takes effect.
        end: When it lapses, exclusive -- ``None`` for the rest of the run.
        beyond: What the venue does with an order priced beyond the band.
        lower_rate, upper_rate: The band below and above the reference, as rates
            of it in ``(0, 1)`` -- declared together, or not at all.
        lower_offset, upper_offset: The band as absolute amounts instead, above
            zero -- declared together, when the rates are not.
        stale_after: How old a reference may be, in seconds, or ``None``: any
            reference in effect is current.
    """

    asset_id: str
    start: float
    end: float | None
    beyond: BandAction
    lower_rate: Decimal | None = None
    upper_rate: Decimal | None = None
    lower_offset: Decimal | None = None
    upper_offset: Decimal | None = None
    stale_after: float | None = None

    def __post_init__(self) -> None:
        start = _instant(self.start, "A band rule's start")
        end = None if self.end is None else _instant(self.end, "A band rule's end")
        if end is not None and end <= start:
            raise InstrumentInputError(f"A band rule ends after it starts, got {start} to {end}.")
        if not isinstance(self.beyond, BandAction):
            raise InstrumentInputError(f"beyond is a BandAction, got {self.beyond!r}.")
        rates = (self.lower_rate, self.upper_rate)
        offsets = (self.lower_offset, self.upper_offset)
        by_rate = all(r is not None for r in rates) and all(o is None for o in offsets)
        by_offset = all(o is not None for o in offsets) and all(r is None for r in rates)
        if not (by_rate or by_offset):
            raise InstrumentInputError(
                "A band rule declares both its rates, or both its absolute offsets -- one way."
            )
        for value in (*rates, *offsets):
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
                raise InstrumentInputError(
                    f"A band rule's width is a finite Decimal, got {value!r}."
                )
        if by_rate and not all(Decimal("0") < r < Decimal("1") for r in rates if r is not None):
            raise InstrumentInputError(f"A band rule's rates are in (0, 1), got {rates}.")
        if by_offset and not all(o > 0 for o in offsets if o is not None):
            raise InstrumentInputError(f"A band rule's offsets are above zero, got {offsets}.")
        if self.stale_after is not None and not (
            isinstance(self.stale_after, int | float)
            and not isinstance(self.stale_after, bool)
            and math.isfinite(self.stale_after)
            and self.stale_after > 0
        ):
            raise InstrumentInputError(
                f"stale_after is a positive number of seconds or None, got {self.stale_after!r}."
            )
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def in_force(self, at: float) -> bool:
        """Whether the rule applies at ``at``."""

        return self.start <= at and (self.end is None or at < self.end)

    def band(self, reference: BandReference, tick: TickSchedule | None) -> PriceBand:
        """The band ``reference`` gives, its edges rounded inward to ``tick`` when declared."""

        price = reference.price
        if self.lower_rate is not None and self.upper_rate is not None:
            lower = price * (Decimal("1") - self.lower_rate)
            upper = price * (Decimal("1") + self.upper_rate)
        else:
            assert self.lower_offset is not None and self.upper_offset is not None
            lower, upper = price - self.lower_offset, price + self.upper_offset
        if tick is not None and lower > 0:
            lower = round_to_tick(lower, tick.tick_size_at(lower), RoundingDirection.UP)
            upper = round_to_tick(upper, tick.tick_size_at(upper), RoundingDirection.DOWN)
        start = max(self.start, reference.effective_at)
        return PriceBand(self.asset_id, start, self.end, lower, upper, self.beyond)


class AuctionTieBreak(Enum):
    """How a venue chooses between prices that match the same volume with the same imbalance."""

    #: The highest of them.
    HIGHEST = auto()
    #: The lowest of them.
    LOWEST = auto()
    #: The one nearest the reference price in effect (FUT-028); the lower of two
    #: equally near. A halt declaring it needs a reference for its instrument.
    NEAREST_REFERENCE = auto()


class AuctionPriority(Enum):
    """Where the run's own orders stand among a venue's published interest at one price."""

    #: Behind it: what the venue published at a price fills before the run's own
    #: orders there, which fill among themselves by time. The run cannot know
    #: when the published interest arrived, so it assumes it arrived first.
    PUBLISHED_FIRST = auto()
    #: Alongside it: the volume left at a price is shared in proportion to
    #: quantity, the run's own orders and the published interest alike.
    PRO_RATA = auto()


@dataclass(frozen=True, slots=True)
class AuctionUncross:
    """How a venue computes its reopening auction from its book (FUT-033, phase 4).

    The price is the one, of the published and the run's own limit prices, that
    matches the most volume; then the smallest imbalance; then ``tie_break``.
    Only the run's own orders fill, at that price, as makers, rationed by price,
    then by ``priority`` among what stands at the marginal price.

    Attributes:
        tie_break: How a remaining tie between prices is broken.
        priority: Where the run's orders stand among published interest at one price.
        extension: Seconds the auction is extended by, once, when its price is
            outside the band in force: nothing trades in the instrument until
            then, and the auction is computed again at the first event after,
            from the book published by then. ``None`` declares no extension.
    """

    tie_break: AuctionTieBreak
    priority: AuctionPriority = AuctionPriority.PUBLISHED_FIRST
    extension: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tie_break, AuctionTieBreak):
            raise InstrumentInputError(
                f"An uncross declares its tie-break as an AuctionTieBreak, got {self.tie_break!r}."
            )
        if not isinstance(self.priority, AuctionPriority):
            raise InstrumentInputError(
                f"An uncross declares its priority as an AuctionPriority, got {self.priority!r}."
            )
        if self.extension is not None:
            extension = _instant(self.extension, "An auction's extension")
            if extension <= 0:
                raise InstrumentInputError(
                    f"An auction's extension is a positive number of seconds, got {extension}."
                )
            object.__setattr__(self, "extension", extension)


@dataclass(frozen=True, slots=True)
class AuctionInterest:
    """What a venue publishes of its auction book, other than the run's own orders (FUT-033).

    The interest at each limit price and at market, on each side, as published
    at ``published_at``: a venue's indicative book, or the caller's record of
    it. Nothing is inferred beyond it -- no depth the venue did not publish.

    Attributes:
        asset_id: The instrument.
        published_at: When the venue published it.
        bids: ``(limit, quantity)`` to buy, one per price.
        offers: ``(limit, quantity)`` to sell, one per price.
        market_buy: Quantity to buy at any price.
        market_sell: Quantity to sell at any price.
    """

    asset_id: str
    published_at: float
    bids: tuple[tuple[Decimal, Decimal], ...] = ()
    offers: tuple[tuple[Decimal, Decimal], ...] = ()
    market_buy: Decimal = Decimal("0")
    market_sell: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id:
            raise InstrumentInputError("Published auction interest names its instrument.")
        object.__setattr__(
            self, "published_at", _instant(self.published_at, "An auction book's publication")
        )
        for side, levels in (("bids", self.bids), ("offers", self.offers)):
            prices = [price for price, _ in levels]
            if len(set(prices)) != len(prices):
                raise InstrumentInputError(
                    f"An auction book's {side} state each price once, got {prices}."
                )
            for price, quantity in levels:
                if not price.is_finite() or not quantity.is_finite() or quantity <= 0:
                    raise InstrumentInputError(
                        f"An auction book's {side} are a finite price and a positive quantity, "
                        f"got {price} for {quantity}."
                    )
            object.__setattr__(self, side, tuple(sorted(levels)))
        for name in ("market_buy", "market_sell"):
            quantity = getattr(self, name)
            if not isinstance(quantity, Decimal) or not quantity.is_finite() or quantity < 0:
                raise InstrumentInputError(
                    f"An auction book's {name} is a quantity of zero or more, got {quantity!r}."
                )

    @property
    def prices(self) -> tuple[Decimal, ...]:
        """Every limit price the book states, on either side."""

        return tuple(sorted({price for price, _ in (*self.bids, *self.offers)}))


@dataclass(frozen=True, slots=True)
class AuctionIndication:
    """What a venue publishes of its auction's indicative uncross (FUT-040, phase 5).

    A venue's indicative price, the quantity it would match there and the
    imbalance left, as published at ``published_at`` -- each only where the
    venue provides it -- or the withdrawal of its indication. Information for
    strategies: the run's uncross is computed from the book (FUT-033) or read
    from the print, never from an indication.

    Attributes:
        asset_id: The instrument.
        published_at: When the venue published it.
        price: The indicative uncross price, or ``None`` if not published.
        matched: The quantity the venue would match at it, or ``None``.
        imbalance: What would be left unmatched, signed (buying positive), or
            ``None``.
        withdrawn: Whether the venue withdrew its indication: then it states
            nothing else.
    """

    asset_id: str
    published_at: float
    price: Decimal | None = None
    matched: Decimal | None = None
    imbalance: Decimal | None = None
    withdrawn: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.asset_id, str) or not self.asset_id:
            raise InstrumentInputError("An auction indication names its instrument.")
        object.__setattr__(
            self, "published_at", _instant(self.published_at, "An auction indication's publication")
        )
        stated = (self.price, self.matched, self.imbalance)
        if not isinstance(self.withdrawn, bool):
            raise InstrumentInputError(f"withdrawn is a bool, got {self.withdrawn!r}.")
        if self.withdrawn and any(value is not None for value in stated):
            raise InstrumentInputError(
                "A withdrawn indication states nothing else: a price or quantity beside it "
                "would be published and withdrawn at once."
            )
        if not self.withdrawn and all(value is None for value in stated):
            raise InstrumentInputError(
                "An auction indication publishes a price, a matched quantity or an imbalance, "
                "or withdraws one."
            )
        for name, value, least in (
            ("price", self.price, "positive"),
            ("matched", self.matched, "zero or more"),
            ("imbalance", self.imbalance, None),
        ):
            if value is None:
                continue
            if not isinstance(value, Decimal) or not value.is_finite():
                raise InstrumentInputError(f"An indication's {name} is a finite Decimal.")
            if (least == "positive" and value <= 0) or (least == "zero or more" and value < 0):
                raise InstrumentInputError(f"An indication's {name} is {least}, got {value}.")


@dataclass(frozen=True, slots=True)
class PublishedAuction:
    """What a strategy is shown of an auction yet to be held (FUT-040, phase 5).

    Only what the venue published, by the strategy's instant, for the auction
    pending: the book and the indication last published since the halt began
    (FUT-033's book is the one the uncross will read, as published by then),
    never a later one, never the uncross the run will compute, and never depth
    the venue did not publish.

    Attributes:
        asset_id: The instrument.
        as_of: The strategy's instant.
        since: When the halt the auction reopens began.
        book: The book last published since then, or ``None``.
        indication: The indication last published since then -- a withdrawal
            among them -- or ``None``.
        extended_to: When an auction already extended (FUT-033) is held again,
            or ``None``.
    """

    asset_id: str
    as_of: float
    since: float
    book: AuctionInterest | None = None
    indication: AuctionIndication | None = None
    extended_to: float | None = None

    @property
    def book_age(self) -> float | None:
        """Seconds since the book shown was published, or ``None``."""

        return None if self.book is None else self.as_of - self.book.published_at

    @property
    def indication_age(self) -> float | None:
        """Seconds since the indication shown was published, or ``None``."""

        return None if self.indication is None else self.as_of - self.indication.published_at

    @property
    def withdrawn(self) -> bool:
        """Whether the venue's last indication withdrew it."""

        return self.indication is not None and self.indication.withdrawn


@dataclass(frozen=True, slots=True)
class TradingHalt:
    """An interval in which an instrument, or every instrument of a venue, does not trade.

    Attributes:
        start: When trading stops.
        end: When it resumes, exclusive -- ``None`` for the rest of the run.
        asset_id: The instrument halted, or ``None`` when the halt is a venue's.
        venue: The listing venue halted, every instrument it lists, or ``None``.
        reason: What the venue gave, for the record.
        auction: Whether trading reopens by auction (FUT-027, phase 3): at the
            first event after ``end`` the orders that rested through the halt
            cross at one price, that event's opening print, rather than each at
            its own turn. A halt with no ``end`` never reopens.
        uncross: How the auction's price is computed from the venue's
            published book (FUT-033, phase 4), instead of read from the first
            event's print; ``None`` reads the print. Only with ``auction``.
    """

    start: float
    end: float | None
    asset_id: str | None = None
    venue: str | None = None
    reason: str = ""
    auction: bool = False
    uncross: AuctionUncross | None = None

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
        if not isinstance(self.auction, bool):
            raise InstrumentInputError(f"auction is a bool, got {self.auction!r}.")
        if self.uncross is not None:
            if not isinstance(self.uncross, AuctionUncross):
                raise InstrumentInputError(f"uncross is an AuctionUncross, got {self.uncross!r}.")
            if not self.auction:
                raise InstrumentInputError(
                    "A halt computes an auction's uncross only when it reopens by one "
                    "(auction=True)."
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
    #: The venues' reference prices and the band rules around them (FUT-028).
    references: tuple[BandReference, ...] = ()
    reference_bands: tuple[ReferenceBand, ...] = ()
    #: What the venues publish of their auction books (FUT-033, phase 4).
    auction_books: tuple[AuctionInterest, ...] = ()
    #: What the venues publish of their auctions' indicative uncross (FUT-040).
    indications: tuple[AuctionIndication, ...] = ()
    _bands_by_asset: Mapping[str, tuple[PriceBand, ...]] = field(
        init=False, repr=False, compare=False
    )
    _halts_by_asset: Mapping[str, tuple[TradingHalt, ...]] = field(
        init=False, repr=False, compare=False
    )
    _halts_by_venue: Mapping[str, tuple[TradingHalt, ...]] = field(
        init=False, repr=False, compare=False
    )
    _references_by_asset: Mapping[str, tuple[BandReference, ...]] = field(
        init=False, repr=False, compare=False
    )
    _rules_by_asset: Mapping[str, tuple[ReferenceBand, ...]] = field(
        init=False, repr=False, compare=False
    )
    _books_by_asset: Mapping[str, tuple[AuctionInterest, ...]] = field(
        init=False, repr=False, compare=False
    )
    _indications_by_asset: Mapping[str, tuple[AuctionIndication, ...]] = field(
        init=False, repr=False, compare=False
    )
    _auctions: bool = field(init=False, repr=False, compare=False)

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
        references = tuple(sorted(self.references, key=lambda r: (r.asset_id, r.effective_at)))
        rules = tuple(sorted(self.reference_bands, key=lambda r: (r.asset_id, r.start)))
        if not all(isinstance(r, BandReference) for r in references):
            raise InstrumentInputError("Trading controls' references are BandReferences.")
        if not all(isinstance(r, ReferenceBand) for r in rules):
            raise InstrumentInputError("Trading controls' reference bands are ReferenceBands.")
        by_reference: dict[str, list[BandReference]] = {}
        for reference in references:
            published = by_reference.setdefault(reference.asset_id, [])
            if published and published[-1].effective_at == reference.effective_at:
                raise InstrumentInputError(
                    f"Two references for {reference.asset_id} take effect at "
                    f"{reference.effective_at!r}: a correction takes effect when it is made."
                )
            published.append(reference)
        by_rule: dict[str, list[ReferenceBand]] = {}
        for rule in rules:
            earlier_rules = by_rule.setdefault(rule.asset_id, [])
            if earlier_rules and (
                earlier_rules[-1].end is None or earlier_rules[-1].end > rule.start
            ):
                raise InstrumentInputError(
                    f"Two band rules of {rule.asset_id} are in force at {rule.start!r}."
                )
            earlier_rules.append(rule)
            for band in by_asset.get(rule.asset_id, ()):
                if band.start < (rule.end if rule.end is not None else math.inf) and (
                    band.end is None or band.end > rule.start
                ):
                    raise InstrumentInputError(
                        f"A declared band and a band rule of {rule.asset_id} are in force "
                        "together: two answers to where it trades."
                    )
        books = tuple(sorted(self.auction_books, key=lambda b: (b.asset_id, b.published_at)))
        if not all(isinstance(b, AuctionInterest) for b in books):
            raise InstrumentInputError("Trading controls' auction books are AuctionInterests.")
        by_book: dict[str, list[AuctionInterest]] = {}
        for book in books:
            published_books = by_book.setdefault(book.asset_id, [])
            if published_books and published_books[-1].published_at == book.published_at:
                raise InstrumentInputError(
                    f"Two auction books for {book.asset_id} are published at "
                    f"{book.published_at!r}: a later one replaces it when it is published."
                )
            published_books.append(book)
        indications = tuple(sorted(self.indications, key=lambda i: (i.asset_id, i.published_at)))
        if not all(isinstance(i, AuctionIndication) for i in indications):
            raise InstrumentInputError("Trading controls' indications are AuctionIndications.")
        by_indication: dict[str, list[AuctionIndication]] = {}
        for indication in indications:
            indicated = by_indication.setdefault(indication.asset_id, [])
            if indicated and indicated[-1].published_at == indication.published_at:
                raise InstrumentInputError(
                    f"Two auction indications for {indication.asset_id} are published at "
                    f"{indication.published_at!r}: a later one replaces it when it is published."
                )
            indicated.append(indication)
        for halt in halts:
            if (
                halt.uncross is not None
                and halt.uncross.tie_break is AuctionTieBreak.NEAREST_REFERENCE
                and halt.asset_id is not None
                and halt.asset_id not in by_reference
            ):
                raise InstrumentInputError(
                    f"A halt of {halt.asset_id} breaks its auction's ties by the reference "
                    "price, and no reference is declared for it."
                )
        object.__setattr__(self, "bands", bands)
        object.__setattr__(self, "halts", halts)
        object.__setattr__(self, "auction_books", books)
        object.__setattr__(self, "_books_by_asset", {a: tuple(b) for a, b in by_book.items()})
        object.__setattr__(self, "indications", indications)
        object.__setattr__(
            self, "_indications_by_asset", {a: tuple(i) for a, i in by_indication.items()}
        )
        object.__setattr__(self, "_auctions", any(halt.auction for halt in halts))
        object.__setattr__(self, "references", references)
        object.__setattr__(self, "reference_bands", rules)
        object.__setattr__(
            self, "_references_by_asset", {a: tuple(r) for a, r in by_reference.items()}
        )
        object.__setattr__(self, "_rules_by_asset", {a: tuple(r) for a, r in by_rule.items()})
        object.__setattr__(self, "_bands_by_asset", {a: tuple(b) for a, b in by_asset.items()})
        object.__setattr__(self, "_halts_by_asset", {a: tuple(h) for a, h in halted_assets.items()})
        object.__setattr__(self, "_halts_by_venue", {v: tuple(h) for v, h in halted_venues.items()})

    @property
    def assets(self) -> frozenset[str]:
        """Every instrument a band or an instrument halt names."""

        return frozenset(
            (
                *self._bands_by_asset,
                *self._halts_by_asset,
                *self._references_by_asset,
                *self._rules_by_asset,
                *self._books_by_asset,
                *self._indications_by_asset,
            )
        )

    @property
    def auctions(self) -> bool:
        """Whether any halt reopens by auction: the one test a run without them pays."""

        return self._auctions

    def auction_book(self, asset_id: str, since: float, at: float) -> AuctionInterest | None:
        """The book for ``asset_id`` last published in ``[since, at]``, or ``None`` (FUT-033).

        ``since`` is the halt's start: a book published before the halt is not
        this auction's.
        """

        found = None
        for book in self._books_by_asset.get(asset_id, ()):
            if book.published_at > at:
                break
            if book.published_at >= since:
                found = book
        return found

    def indication(self, asset_id: str, since: float, at: float) -> AuctionIndication | None:
        """The indication for ``asset_id`` last published in ``[since, at]``, or ``None``.

        ``since`` is the halt's start, as for :meth:`auction_book` (FUT-040).
        """

        found = None
        for indication in self._indications_by_asset.get(asset_id, ()):
            if indication.published_at > at:
                break
            if indication.published_at >= since:
                found = indication
        return found

    def band(self, asset_id: str, at: float, tick: TickSchedule | None = None) -> PriceBand | None:
        """The band in force for ``asset_id`` at ``at``, or ``None``.

        A declared band, or the one a band rule gives around its current
        reference, rounded inward to ``tick`` (FUT-028); ``None`` too when a
        rule is in force with no current reference -- :meth:`unbanded` says so.
        """

        for band in self._bands_by_asset.get(asset_id, ()):
            if band.in_force(at):
                return band
        rule = self._rule(asset_id, at)
        if rule is None:
            return None
        reference = self.current_reference(asset_id, at, rule)
        return None if reference is None else rule.band(reference, tick)

    def unbanded(self, asset_id: str, at: float) -> str | None:
        """Why a band rule in force at ``at`` gives no band, or ``None``: no current reference."""

        rule = self._rule(asset_id, at)
        if rule is None or self.current_reference(asset_id, at, rule) is not None:
            return None
        latest = self.reference(asset_id, at)
        if latest is None:
            return f"No reference price is in effect for {asset_id} at {at!r}."
        return (
            f"The reference price for {asset_id} in effect at {at!r} took effect at "
            f"{latest.effective_at!r}, more than {rule.stale_after!r} seconds before."
        )

    def reference(self, asset_id: str, at: float) -> BandReference | None:
        """The latest reference for ``asset_id`` in effect at ``at``, or ``None``."""

        found = None
        for reference in self._references_by_asset.get(asset_id, ()):
            if reference.effective_at > at:
                break
            found = reference
        return found

    def current_reference(
        self, asset_id: str, at: float, rule: ReferenceBand
    ) -> BandReference | None:
        """The reference in effect at ``at`` if ``rule`` counts it current, or ``None``."""

        reference = self.reference(asset_id, at)
        if reference is None:
            return None
        if rule.stale_after is not None and at - reference.effective_at > rule.stale_after:
            return None
        return reference

    def _rule(self, asset_id: str, at: float) -> ReferenceBand | None:
        for rule in self._rules_by_asset.get(asset_id, ()):
            if rule.in_force(at):
                return rule
        return None

    def reopened_by_auction(self, asset_id: str, venue: str, at: float) -> TradingHalt | None:
        """The latest halt on ``asset_id`` or ``venue`` ended by ``at`` reopening by auction."""

        ended = [
            halt
            for halt in (
                *self._halts_by_asset.get(asset_id, ()),
                *self._halts_by_venue.get(venue, ()),
            )
            if halt.auction and halt.end is not None and halt.end <= at
        ]
        return max(ended, key=lambda halt: halt.end or 0.0) if ended else None

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
                # Written only when declared (FUT-027, FUT-033).
                **({"auction": True} if h.auction else {}),
                **({} if h.uncross is None else {"uncross": _uncross_primitives(h.uncross)}),
            }
            for h in controls.halts
        ],
        **(
            {}
            if not controls.auction_books
            else {
                "auction_books": [
                    {
                        "asset_id": b.asset_id,
                        "published_at": b.published_at,
                        "bids": [[str(p), str(q)] for p, q in b.bids],
                        "offers": [[str(p), str(q)] for p, q in b.offers],
                        "market_buy": str(b.market_buy),
                        "market_sell": str(b.market_sell),
                    }
                    for b in controls.auction_books
                ]
            }
        ),
        # Written only when declared (FUT-040), so controls without them keep
        # their form.
        **(
            {}
            if not controls.indications
            else {
                "indications": [
                    {
                        "asset_id": i.asset_id,
                        "published_at": i.published_at,
                        "price": _text(i.price),
                        "matched": _text(i.matched),
                        "imbalance": _text(i.imbalance),
                        **({"withdrawn": True} if i.withdrawn else {}),
                    }
                    for i in controls.indications
                ]
            }
        ),
        # Written only when declared (FUT-028), so controls without them keep
        # their form.
        **(
            {}
            if not controls.references
            else {
                "references": [
                    {
                        "asset_id": r.asset_id,
                        "effective_at": r.effective_at,
                        "price": str(r.price),
                        "source": r.source,
                    }
                    for r in controls.references
                ]
            }
        ),
        **(
            {}
            if not controls.reference_bands
            else {
                "reference_bands": [
                    {
                        "asset_id": r.asset_id,
                        "start": r.start,
                        "end": r.end,
                        "beyond": r.beyond.name,
                        "lower_rate": _text(r.lower_rate),
                        "upper_rate": _text(r.upper_rate),
                        "lower_offset": _text(r.lower_offset),
                        "upper_offset": _text(r.upper_offset),
                        "stale_after": r.stale_after,
                    }
                    for r in controls.reference_bands
                ]
            }
        ),
    }


def _uncross_primitives(uncross: AuctionUncross) -> dict[str, Any]:
    return {
        "tie_break": uncross.tie_break.name,
        "priority": uncross.priority.name,
        "extension": uncross.extension,
    }


def _uncross_from(value: Any) -> AuctionUncross | None:
    if value is None:
        return None
    return AuctionUncross(
        AuctionTieBreak[value["tie_break"]], AuctionPriority[value["priority"]], value["extension"]
    )


def _levels(value: Any) -> tuple[tuple[Decimal, Decimal], ...]:
    return tuple((Decimal(price), Decimal(quantity)) for price, quantity in value)


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(value)


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
                TradingHalt(
                    h["start"],
                    h["end"],
                    h["asset_id"],
                    h["venue"],
                    h["reason"],
                    bool(h.get("auction", False)),
                    _uncross_from(h.get("uncross")),
                )
                for h in value["halts"]
            ),
            tuple(
                BandReference(r["asset_id"], r["effective_at"], Decimal(r["price"]), r["source"])
                for r in value.get("references", ())
            ),
            tuple(
                ReferenceBand(
                    r["asset_id"],
                    r["start"],
                    r["end"],
                    BandAction[r["beyond"]],
                    _optional_decimal(r["lower_rate"]),
                    _optional_decimal(r["upper_rate"]),
                    _optional_decimal(r["lower_offset"]),
                    _optional_decimal(r["upper_offset"]),
                    r["stale_after"],
                )
                for r in value.get("reference_bands", ())
            ),
            tuple(
                AuctionInterest(
                    b["asset_id"],
                    b["published_at"],
                    _levels(b["bids"]),
                    _levels(b["offers"]),
                    Decimal(b["market_buy"]),
                    Decimal(b["market_sell"]),
                )
                for b in value.get("auction_books", ())
            ),
            tuple(
                AuctionIndication(
                    i["asset_id"],
                    i["published_at"],
                    _optional_decimal(i["price"]),
                    _optional_decimal(i["matched"]),
                    _optional_decimal(i["imbalance"]),
                    i.get("withdrawn", False),
                )
                for i in value.get("indications", ())
            ),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise InstrumentInputError(f"{where} is not trading controls: {exc}.") from exc
