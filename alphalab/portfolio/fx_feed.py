"""Where exchange rates come from, and what a feed is allowed to do with one.

:mod:`alphalab.portfolio.fx` answers *what a rate is* and *what a table of them
can convert*. It says nothing about how a table comes to hold what it holds, and
ADR-0033 recorded that gap in its own words: "**No rate feed.** AlphaLab ships no
FX data, exactly as it ships no classification data."

That sentence is still true and this module does not change it. **No rate here
is invented, derived, defaulted or fetched.** What was missing was not data --
it was the *boundary* a supplier crosses, and the rules that boundary enforces.
Without one, every caller wrote its own fold from quotes to an
:class:`~alphalab.portfolio.fx.FxRates`, and every one of them had to decide,
alone and usually implicitly, what to do about a rate that arrived out of order,
a rate redelivered after a reconnect, and two sources disagreeing about one pair
at one instant. Those three questions have one right answer each, and this is
where they are answered.

The shape is :class:`~alphalab.market.source.MarketDataSource`'s, deliberately
----------------------------------------------------------------------------

A :class:`FxRateSource` yields :class:`FxQuote` s and nothing else, exactly as a
``MarketDataSource`` yields canonical records and nothing else. No provider API
is modelled here -- no HTTP, no websocket, no vendor authentication, no
reconnect loop -- because inventing a vendor's API shape would be guessing at an
interface AlphaLab cannot test. A vendor adapter implements the protocol in its
own package and normalizes into :class:`~alphalab.portfolio.fx.FxRate` on the way
out. :class:`SequenceFxSource` is the deterministic source the tests and
examples use, and it is the analogue of
:class:`~alphalab.market.source.SequenceSource`.

The three rules, and why each is the only honest one
-----------------------------------------------------

:meth:`FxFeed.apply` returns a :class:`FxFeedDecision` saying which rule fired.
Nothing is silently dropped and nothing is silently preferred.

``APPLIED``
    The quote is newer than what the table holds for its pair, so it replaces
    it. This is the normal case.

``DUPLICATE``
    The same pair, the same ``as_of``, the same rate, the same source. A venue
    redelivers after a reconnect, and applying it twice would be harmless here
    but the fact that it *happened* is worth reporting -- it is the same
    distinction :class:`~alphalab.broker.state.BrokerState` draws for a
    redelivered execution.

``SUPERSEDED``
    The quote is **older** than what the table already holds for its pair, so it
    is not applied. A feed that accepted it would move the book's view of the
    market backwards because two packets arrived out of order, and a valuation
    taken between the two would be wrong in a way nothing could detect
    afterwards. ``OrderingGuarantee`` exists on ``MarketDataSource`` because a
    live venue can reorder; this rule is what makes reordering *safe* here
    rather than merely declared.

A fourth case is **refused rather than decided**: the same pair and the same
``as_of`` carrying a *different* rate or a *different* source. That is two
answers to one question, and :meth:`~alphalab.portfolio.fx.FxRates.of` already
refuses exactly this shape at construction -- "which one is right is not a
question this table will answer by picking". A feed that let the later packet win
would be picking, and would look authoritative while doing it.

Staleness stays where it was
-----------------------------

A feed does not decide whether a rate is too old to *use*; that is
:attr:`~alphalab.portfolio.fx.FxRates.max_age_seconds`, checked at conversion
against the instant the conversion is for, and unchanged. What a feed knows is
something different and also worth having: when it last heard anything at all.
:attr:`FxFeedState.last_quote_at` and :meth:`FxFeedState.silent_for` answer
*"has this feed gone quiet?"* -- a liveness question about the **connection**,
which a stale-rate refusal at conversion time cannot distinguish from a source
that is simply not quoting a pair.

Replay and durability
----------------------

:class:`FxFeedState` is a value like every other state here, and
:func:`capture` / :func:`restore` make it durable on the same terms as
:mod:`alphalab.broker.snapshot`: what is captured is *what AlphaLab believed*,
never a live connection. Applying the same quotes in the same order always
produces the same state, so a replayed run values its book with the rates the
original run used rather than with today's.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from typing import Any, Final, Protocol, runtime_checkable

from alphalab.common.append_log import AppendOnlyLog
from alphalab.persistence.decode import (
    as_bool,
    as_decimal,
    as_float,
    as_int,
    as_mapping,
    as_named_enum,
    as_sequence,
    as_str,
    require,
    require_schema_version,
)
from alphalab.persistence.exceptions import StateDecodeError
from alphalab.portfolio.exceptions import PortfolioError
from alphalab.portfolio.fx import FxRate, FxRates

__all__ = [
    "FX_FEED_SNAPSHOT_SCHEMA",
    "ConflictingQuoteError",
    "FxFeed",
    "FxFeedDecision",
    "FxFeedOutcome",
    "FxFeedSnapshot",
    "FxFeedState",
    "FxQuote",
    "FxRateSource",
    "SequenceFxSource",
    "capture",
    "from_primitives",
    "restore",
]

#: Schema version of an :class:`FxFeedState` payload.
#:
#: A module-local literal rather than ``DEFAULT_SCHEMA_VERSION``, for the reason
#: v2.6 gave for the portfolio and v2.8 for the lifecycle: that constant also
#: versions ``BaseEvent``, so bumping it would version every event in the system
#: as a side effect of one subsystem's change. New in v2.17, so it has nothing to
#: be compatible with: no legacy shape, no migration, and no "missing means 1".
FX_FEED_SNAPSHOT_SCHEMA: Final = 1

_SUBSYSTEM = "fx_feed"


class ConflictingQuoteError(PortfolioError):
    """Raised when two quotes for one pair claim one instant and disagree.

    Distinct from :class:`~alphalab.portfolio.fx.MissingRateError` (the table
    does not hold this pair) and :class:`~alphalab.portfolio.fx.StaleRateError`
    (the rate it holds is too old to use). This one says the feed was handed two
    answers to the same question, and picking between them is not something a
    feed will do quietly.
    """


@dataclass(frozen=True, slots=True)
class FxQuote:
    """One rate observation, and where it sat in the stream that produced it.

    Attributes:
        rate: The observed rate, carrying its own ``as_of`` and ``source``. The
            provenance is the rate's, not the quote's -- an aggregating feed
            carries rates from several sources and each says which.
        sequence: Position in the producing source's stream, counting from zero.
            **Ordering evidence, not identity**: what decides whether a quote
            supersedes another is the rate's ``as_of``, because two sources have
            two independent sequences. A sequence is what lets a caller detect a
            gap in one source's stream.
    """

    rate: FxRate
    sequence: int = 0

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise PortfolioError(f"FxQuote.sequence cannot be negative, got {self.sequence}.")

    @property
    def pair(self) -> tuple[str, str]:
        """The ``(base, quote)`` this observation is for."""

        return self.rate.pair


class FxFeedOutcome(Enum):
    """What :meth:`FxFeed.apply` did with a quote. See the module docstring."""

    #: Newer than what was held for this pair; the table now holds it.
    APPLIED = auto()

    #: Byte-identical to what is already held. The table is unchanged.
    DUPLICATE = auto()

    #: Older than what is already held. The table is unchanged, deliberately.
    SUPERSEDED = auto()


@dataclass(frozen=True, slots=True)
class FxFeedDecision:
    """What a feed did with one quote, and what it replaced.

    Attributes:
        quote: The quote that was offered.
        outcome: Which rule fired.
        previous: The rate this quote replaced, or ``None`` when the pair was
            not held before. Kept so a reconciliation can say what the table
            used to think rather than discovering that it changed.
    """

    quote: FxQuote
    outcome: FxFeedOutcome
    previous: FxRate | None = None

    @property
    def applied(self) -> bool:
        """Whether the table moved."""

        return self.outcome is FxFeedOutcome.APPLIED

    @property
    def summary(self) -> str:
        """Human-readable, for a log or a report."""

        base, quote = self.quote.pair
        return (
            f"{base}/{quote} {self.outcome.name.lower()}: {self.quote.rate.rate} "
            f"from {self.quote.rate.source!r} as of {self.quote.rate.as_of}"
        )


@dataclass(frozen=True, slots=True)
class FxFeedState:
    """What a feed has observed, and the table that observation produced.

    Attributes:
        feed_id: Identifier for this feed. Caller-supplied and opaque, exactly
            as ``RunStateRef.run_id`` is: AlphaLab mints no feed identity.
        rates: The current table. This is the value that goes straight into
            :meth:`~alphalab.portfolio.valuation.PortfolioValuation.snapshot`
            and into settlement -- a feed produces the canonical type rather
            than a parallel one.
        observed: How many quotes have been offered, including the ones no rule
            applied. A feed that is receiving duplicates is working; a feed
            receiving nothing is not, and the two look identical from the table
            alone.
        applied_count: How many of them moved the table.
        last_quote_at: The ``as_of`` of the newest quote **offered**, or ``None``
            before the first. Not the newest *applied*: a superseded quote is
            still evidence that the connection is alive.
        decisions: Every decision, in order.
    """

    feed_id: str
    rates: FxRates = field(default_factory=FxRates)
    observed: int = 0
    applied_count: int = 0
    last_quote_at: float | None = None
    decisions: AppendOnlyLog[FxFeedDecision] = field(default_factory=AppendOnlyLog)

    @property
    def pairs(self) -> tuple[tuple[str, str], ...]:
        """Every pair the table can convert, sorted."""

        return self.rates.pairs

    def silent_for(self, now: float) -> float | None:
        """How long since this feed last heard anything, or ``None`` if never.

        A **liveness** question about the connection, and deliberately not the
        same question as :attr:`~alphalab.portfolio.fx.FxRates.max_age_seconds`,
        which asks whether a particular rate is too old to use. A feed quoting
        EUR/USD every second while never quoting USD/JPY is alive and missing a
        pair; a feed that has gone silent is neither, and only this can tell
        them apart.
        """

        if self.last_quote_at is None:
            return None
        return now - self.last_quote_at


@runtime_checkable
class FxRateSource(Protocol):
    """Anything that can produce FX quotes in sequence.

    The whole contract, and deliberately small -- the boundary
    :class:`~alphalab.market.source.MarketDataSource` draws for market records,
    drawn again for rates. What a source must guarantee:

    * **Identity.** :attr:`source_id` names the stream, so a caller holding two
      feeds can say which is which.
    * **Provenance.** Every :class:`~alphalab.portfolio.fx.FxRate` it yields
      already carries a non-empty ``source`` and an ``as_of``; the rate's own
      ``__post_init__`` refuses otherwise, so a source cannot emit an
      unattributed rate even by accident.
    * **Nothing about order.** A source may yield quotes in any order.
      :meth:`FxFeed.apply` is what makes that safe, rather than a guarantee a
      live venue cannot keep.
    """

    @property
    def source_id(self) -> str:
        """Identifier for this stream."""
        ...

    def quotes(self) -> Iterator[FxQuote]:
        """Yield every quote this source has, in the order it has them."""
        ...


@dataclass(frozen=True, slots=True)
class SequenceFxSource:
    """A deterministic source over a fixed sequence of rates.

    The analogue of :class:`~alphalab.market.source.SequenceSource`, and what
    tests, examples and a replayed run use. Sequences are assigned from the
    position in the supplied tuple, so the same input always produces the same
    stream.

    This is **not invented market data**: the caller supplies every rate, each
    with the source and instant it was actually true. What this class adds is
    repeatability, which is the property a test needs and a vendor cannot give.
    """

    source_id: str
    rates: tuple[FxRate, ...] = ()

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise PortfolioError("FxRateSource.source_id cannot be empty.")

    @classmethod
    def of(cls, source_id: str, rates: Iterable[FxRate]) -> SequenceFxSource:
        """Build a source from any iterable of rates."""

        return cls(source_id, tuple(rates))

    def quotes(self) -> Iterator[FxQuote]:
        """Yield one :class:`FxQuote` per supplied rate, numbered in order."""

        for sequence, rate in enumerate(self.rates):
            yield FxQuote(rate=rate, sequence=sequence)


class FxFeed:
    """Stateless fold from quotes to an :class:`~alphalab.portfolio.fx.FxRates`."""

    @staticmethod
    def initialize(feed_id: str, max_age_seconds: float | None = None) -> FxFeedState:
        """An empty feed.

        ``max_age_seconds`` is carried onto the table this feed builds and is
        **not** a feed policy: it is the tolerance
        :meth:`~alphalab.portfolio.fx.FxRates.convert` checks at conversion
        time, and its default is ``None`` for the reason
        :mod:`alphalab.portfolio.fx` gives -- AlphaLab does not know what
        tolerance a desk runs to, and a default either way would be an invented
        policy presented as an architectural one.

        Raises:
            PortfolioError: If ``feed_id`` is blank.
        """

        if not feed_id.strip():
            raise PortfolioError("FxFeedState.feed_id cannot be empty.")
        return FxFeedState(feed_id=feed_id, rates=FxRates(max_age_seconds=max_age_seconds))

    @staticmethod
    def apply(state: FxFeedState, quote: FxQuote) -> tuple[FxFeedState, FxFeedDecision]:
        """Offer one quote to the feed, and say what happened to it.

        See the module docstring for the three rules and the one refusal.

        Raises:
            ConflictingQuoteError: If the table already holds a rate for this
                pair at this exact ``as_of`` that disagrees about the rate or
                the source. Nothing is applied and the state passed in is
                unchanged.
        """

        held = state.rates.rate_for(*quote.pair)
        incoming = quote.rate

        observed = replace(
            state,
            observed=state.observed + 1,
            last_quote_at=(
                incoming.as_of
                if state.last_quote_at is None
                else max(state.last_quote_at, incoming.as_of)
            ),
        )

        if held is not None and incoming.as_of == held.as_of:
            if incoming == held:
                decision = FxFeedDecision(quote, FxFeedOutcome.DUPLICATE, held)
                return replace(observed, decisions=observed.decisions.append(decision)), decision

            base, target = quote.pair
            raise ConflictingQuoteError(
                f"Two rates claim {base}/{target} as of {incoming.as_of}: "
                f"{held.rate} from {held.source!r} and {incoming.rate} from "
                f"{incoming.source!r}. Which one is right is not a question this feed "
                "will answer by picking -- the later packet winning would look "
                "authoritative while being arbitrary. Reconcile the sources, or run "
                "one feed per source and choose between the tables deliberately."
            )

        if held is not None and incoming.as_of < held.as_of:
            decision = FxFeedDecision(quote, FxFeedOutcome.SUPERSEDED, held)
            return replace(observed, decisions=observed.decisions.append(decision)), decision

        decision = FxFeedDecision(quote, FxFeedOutcome.APPLIED, held)
        applied = replace(
            observed,
            rates=state.rates.with_rate(incoming),
            applied_count=state.applied_count + 1,
            decisions=observed.decisions.append(decision),
        )
        return applied, decision

    @staticmethod
    def advance(
        state: FxFeedState, quotes: Iterable[FxQuote]
    ) -> tuple[FxFeedState, tuple[FxFeedDecision, ...]]:
        """Offer many quotes in order, and return every decision.

        A refusal stops the fold: the state returned by a raising call is the
        one passed in, so a caller that catches
        :class:`ConflictingQuoteError` still holds a table nothing ambiguous
        reached.
        """

        current = state
        decisions: list[FxFeedDecision] = []
        for quote in quotes:
            current, decision = FxFeed.apply(current, quote)
            decisions.append(decision)
        return current, tuple(decisions)

    @staticmethod
    def drain(
        state: FxFeedState, source: FxRateSource
    ) -> tuple[FxFeedState, tuple[FxFeedDecision, ...]]:
        """Offer everything a source currently has.

        Deliberately a *pull*: the feed asks the source for what it holds, the
        same way :class:`~alphalab.runtime.session.TradingSession` reads a
        ``MarketDataSource``. How a source comes to have quotes -- a poll, a
        socket, a file -- is its business and not modelled here.
        """

        return FxFeed.advance(state, source.quotes())


# --------------------------------------------------------------------------- #
# Durability
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FxFeedSnapshot:
    """A restorable projection of an :class:`FxFeedState`.

    What is captured is **what AlphaLab believed**, on the same terms as
    :mod:`alphalab.broker.snapshot`: never a connection, never a source. A
    restore does not reconnect and does not re-fetch; the caller supplies a
    freshly constructed :class:`FxRateSource` if it wants one.
    """

    feed_id: str
    max_age_seconds: float | None
    rates: tuple[FxRate, ...]
    observed: int
    applied_count: int
    last_quote_at: float | None
    decisions: tuple[FxFeedDecision, ...]
    schema_version: int = FX_FEED_SNAPSHOT_SCHEMA


def capture(state: FxFeedState) -> FxFeedSnapshot:
    """Project ``state`` into a restorable snapshot."""

    return FxFeedSnapshot(
        feed_id=state.feed_id,
        max_age_seconds=state.rates.max_age_seconds,
        rates=tuple(state.rates.rates[pair] for pair in state.rates.pairs),
        observed=state.observed,
        applied_count=state.applied_count,
        last_quote_at=state.last_quote_at,
        decisions=tuple(state.decisions),
    )


def restore(snapshot: FxFeedSnapshot) -> FxFeedState:
    """Rebuild the state a snapshot was taken from.

    ``restore(capture(state)) == state``, which is the contract ADR-0014 states
    and every snapshot module here holds.
    """

    return FxFeedState(
        feed_id=snapshot.feed_id,
        rates=FxRates(
            {rate.pair: rate for rate in snapshot.rates},
            max_age_seconds=snapshot.max_age_seconds,
        ),
        observed=snapshot.observed,
        applied_count=snapshot.applied_count,
        last_quote_at=snapshot.last_quote_at,
        decisions=AppendOnlyLog(snapshot.decisions),
    )


def _rate_from(payload: Mapping[str, Any], where: str) -> FxRate:
    return FxRate(
        base=as_str(require(payload, "base"), f"{where}.base"),
        quote=as_str(require(payload, "quote"), f"{where}.quote"),
        rate=as_decimal(require(payload, "rate"), f"{where}.rate"),
        as_of=as_float(require(payload, "as_of"), f"{where}.as_of"),
        source=as_str(require(payload, "source"), f"{where}.source"),
        derived=as_bool(require(payload, "derived"), f"{where}.derived"),
    )


def _decision_from(payload: Mapping[str, Any], where: str) -> FxFeedDecision:
    quote_payload = as_mapping(require(payload, "quote"), f"{where}.quote")
    previous = payload.get("previous")
    resolved = as_named_enum(FxFeedOutcome, require(payload, "outcome"), f"{where}.outcome")
    return FxFeedDecision(
        quote=FxQuote(
            rate=_rate_from(
                as_mapping(require(quote_payload, "rate"), f"{where}.quote.rate"),
                f"{where}.quote.rate",
            ),
            sequence=as_int(require(quote_payload, "sequence"), f"{where}.quote.sequence"),
        ),
        outcome=resolved,
        previous=(
            None
            if previous is None
            else _rate_from(as_mapping(previous, f"{where}.previous"), f"{where}.previous")
        ),
    )


def from_primitives(payload: Mapping[str, Any]) -> FxFeedSnapshot:
    """Decode a deserialized payload back into a typed snapshot.

    Raises:
        StateDecodeError: If the payload does not describe the state it claims
            to. The field that failed is named, because "a snapshot did not
            load" is not actionable.
    """

    if not isinstance(payload, Mapping):
        raise StateDecodeError("An fx_feed snapshot is not an object.")

    require_schema_version(payload, FX_FEED_SNAPSHOT_SCHEMA, _SUBSYSTEM)

    max_age = payload.get("max_age_seconds")
    last_quote = payload.get("last_quote_at")

    return FxFeedSnapshot(
        feed_id=as_str(require(payload, "feed_id"), "feed_id"),
        max_age_seconds=None if max_age is None else as_float(max_age, "max_age_seconds"),
        rates=tuple(
            _rate_from(as_mapping(entry, f"rates[{index}]"), f"rates[{index}]")
            for index, entry in enumerate(as_sequence(require(payload, "rates"), "rates"))
        ),
        observed=as_int(require(payload, "observed"), "observed"),
        applied_count=as_int(require(payload, "applied_count"), "applied_count"),
        last_quote_at=None if last_quote is None else as_float(last_quote, "last_quote_at"),
        decisions=tuple(
            _decision_from(as_mapping(entry, f"decisions[{index}]"), f"decisions[{index}]")
            for index, entry in enumerate(as_sequence(require(payload, "decisions"), "decisions"))
        ),
        schema_version=FX_FEED_SNAPSHOT_SCHEMA,
    )
