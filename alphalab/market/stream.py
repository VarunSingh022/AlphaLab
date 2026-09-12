"""A continuously streaming market-data source, on the canonical boundary.

:class:`~alphalab.market.provider.ProviderHistorySource` put a provider on the
execution path and said exactly what it was not: "a *history* source ... It does
not poll, subscribe, reconnect or stream". This is the other one. It connects,
subscribes, and yields records as the venue sends them, for as long as the
caller reads.

It is a :class:`~alphalab.market.source.MarketDataSource` and **nothing more
than one**, which is the entire architectural point. ``records()`` already
returns an *iterator*; a generator pulling a live socket satisfies that contract
with no new abstraction, so
:meth:`~alphalab.runtime.session.TradingSession.run` drives a venue feed with
the same loop it drives a stored dataset with, and the execution path cannot
tell which it has. No second market-data lifecycle is introduced, and
:mod:`alphalab.live` and :mod:`alphalab.feed` -- which are standalone, off-path
engine libraries -- are not built on.

The pipeline
------------
::

    websocket frame     marketdata.websocket.WebSocketConnection
      -> JSON message   this module's `_parse`
      -> wire record    data.feed.Quote / Trade / Bar   (float, provider symbol)
      -> normalization  market.normalization            (Decimal, asset_id)
      -> MarketRecord   market.record
      -> StreamingSource                                (a MarketDataSource)
      -> RunEngine.advance                              the canonical run step

Every stage but the first two already existed and was already tested. That is
deliberate: a streaming source that normalized differently from a historical one
would make live and backtest results incomparable for a reason that has nothing
to do with the market.

Ordering, and the honest declaration
------------------------------------
This source declares :attr:`~alphalab.market.source.OrderingGuarantee.UNORDERED`
and cannot declare anything else. A venue can reorder, and a socket that
reconnects mid-second will deliver a record older than one already seen.
AlphaLab does not reorder market data -- ADR-0014 -- so a run over this source
must set ``RunConfig.ordering`` to ``UNORDERED``, under which a regressing
record is skipped and recorded on ``RunState.skipped`` rather than marking the
portfolio backwards. A run that demands ``CHRONOLOGICAL`` is refused before the
first record, which is the existing rule and is left exactly as it was.

**Sequence gaps are a different question from timestamp order**, and both are
answered. The venue stamps each message with a per-symbol sequence; this source
drops any message whose sequence it has already seen (:attr:`StreamStats.duplicates`)
and counts any that skips ahead (:attr:`StreamStats.gaps`) without inventing the
missing ones. Deduplication is what makes a reconnect safe, because a venue
replaying from its last known position will resend what was already delivered.

Backpressure
------------
There is no buffer to bound. The source is a *generator*: it reads one message
when the consumer asks for one, so a slow strategy applies backpressure through
TCP's own receive window rather than through a queue in this process that could
grow without limit. The only buffering here is the frame reassembly inside
:class:`~alphalab.marketdata.websocket.WebSocketConnection`, which is bounded by
a maximum frame size.

Determinism is not claimed, and that is the point
-------------------------------------------------
A live stream is not reproducible: it depends on what a venue sent and when.
This source therefore touches nothing a deterministic run depends on. It mints
record ids from its own ``source_id`` and position, exactly as
:meth:`~alphalab.market.source.SequenceSource.of` does, and **draws no
identifier from the ambient id source** -- so a stream feeding a seeded run
cannot shift the identity of a single order or fill. Backtest and replay are
byte-identical with this module present, which
``tests/regression/test_streaming_does_not_contaminate_determinism.py`` pins.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final

from alphalab.data.feed import Bar as WireBar
from alphalab.data.feed import Quote as WireQuote
from alphalab.data.feed import Trade as WireTrade
from alphalab.instrument.registry import InstrumentRegistry
from alphalab.market.exceptions import (
    InstrumentResolutionError,
    MarketValidationError,
)
from alphalab.market.normalization import (
    NormalizationPolicy,
    is_stale,
    normalize_wire_bar,
    normalize_wire_quote,
    normalize_wire_trade,
)
from alphalab.market.record import MarketInput, MarketRecord
from alphalab.market.source import OrderingGuarantee
from alphalab.marketdata.websocket import (
    WebSocketConnection,
    WebSocketError,
    WebSocketTransport,
)

__all__ = [
    "StreamConfig",
    "StreamStats",
    "StreamingSource",
]

#: Width the record counter is zero-padded to in an ``event_id``. A stream is
#: unbounded, so this is a formatting floor rather than a ceiling: a longer
#: counter simply renders longer, and ids stay unique either way.
_COUNTER_WIDTH: Final = 12

#: Message kinds this source understands. Anything else is counted as malformed
#: rather than guessed at.
_QUOTE: Final = "quote"
_TRADE: Final = "trade"
_BAR: Final = "bar"
_HEARTBEAT: Final = "heartbeat"
_SUBSCRIBED: Final = "subscribed"
_ERROR: Final = "error"


@dataclass(frozen=True, slots=True)
class StreamConfig:
    """How a stream connects, what it asks for, and when it gives up.

    Attributes:
        url: The ``ws://`` or ``wss://`` endpoint.
        symbols: Provider symbols to subscribe to. Must be non-empty: a
            subscription to nothing is a connection that will never yield.
        policy: How a wire record becomes canonical. Must resolve identity
            through an
            :class:`~alphalab.instrument.registry.InstrumentRegistry`, for the
            reason :meth:`~alphalab.market.provider.ProviderHistorySource.of`
            gives -- the unresolved mode yields provider symbols, which
            ``core.Fill`` refuses, so a run would trade nothing and say nothing
            about why.
        liveness_timeout_seconds: How long the venue may be silent before the
            connection is treated as dead and reopened. A venue sends
            heartbeats precisely so that silence is distinguishable from
            calm, and a feed that has quietly died looks exactly like a market
            that has stopped moving. ``None`` disables the check.
        max_reconnects: How many times the source reopens a dropped connection
            before giving up and raising. ``None`` reconnects indefinitely,
            which is what a long-running session wants.
        reconnect_backoff_seconds: How long to wait before reopening. Applied
            between attempts so that a venue refusing connections is not
            hammered.
        max_record_age_seconds: Oldest record this source will yield, measured
            against the wall clock. ``None`` disables the gate. Distinct from
            ``RunConfig.max_market_data_age_seconds``, which is the *run's*
            gate: this one stops a stale record entering the run at all, and
            that one stops the run acting on one. Either alone is sufficient;
            both are cheap.
    """

    url: str
    symbols: Sequence[str]
    policy: NormalizationPolicy
    liveness_timeout_seconds: float | None = 30.0
    max_reconnects: int | None = None
    reconnect_backoff_seconds: float = 0.5
    max_record_age_seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.symbols:
            raise MarketValidationError(
                "A streaming source needs at least one symbol. Subscribing to nothing "
                "opens a connection that can never yield a record."
            )
        if not isinstance(self.policy.identity, InstrumentRegistry):
            raise InstrumentResolutionError(
                "StreamingSource requires InstrumentRegistry-backed identity "
                "resolution. This policy uses UnresolvedIdentity, which passes "
                "provider symbols through unchanged and cannot produce an asset_id "
                "that core.Fill / core.Trade will accept. Register the instruments "
                "and supply an InstrumentRegistry."
            )
        object.__setattr__(self, "symbols", tuple(self.symbols))


@dataclass(frozen=True, slots=True)
class StreamStats:
    """What the stream has seen. Observability, and nothing reads it to decide.

    Counted rather than logged per occurrence, for the reason
    :class:`~alphalab.runtime.execution_pipeline.UnpricedAsset` is aggregated: a
    feed sending malformed messages sends them continuously, and the five
    hundredth says nothing the first did not.

    Attributes:
        received: Messages read off the socket, of every kind.
        yielded: Records handed to the consumer.
        duplicates: Messages dropped because their sequence was already seen.
            A reconnect makes these expected, not exceptional.
        gaps: Times a sequence skipped ahead of what was expected. The missing
            messages are **not** invented; this counts that they were missed.
        malformed: Messages that could not be read as a market record.
        stale: Records dropped for being older than the configured limit.
        heartbeats: Liveness messages received.
        errors: Error messages the venue sent.
        reconnects: Times the connection was reopened.
    """

    received: int = 0
    yielded: int = 0
    duplicates: int = 0
    gaps: int = 0
    malformed: int = 0
    stale: int = 0
    heartbeats: int = 0
    errors: int = 0
    reconnects: int = 0


class StreamingSource:
    """A :class:`~alphalab.market.source.MarketDataSource` over a live connection.

    Mutable, unlike every other source in AlphaLab, and necessarily so: it owns
    a socket, a subscription and a reconnect cursor. What it *yields* is
    immutable canonical records, which is where the immutability contract
    actually lives.

    Not re-iterable. :class:`~alphalab.market.source.SequenceSource` is, and
    documents why -- comparing two runs means replaying the same source twice --
    but a live stream cannot be: the second iteration would be a different
    market. Calling :meth:`records` twice concurrently is refused rather than
    silently interleaved.
    """

    __slots__ = (
        "_config",
        "_connection",
        "_counter",
        "_reading",
        "_sequences",
        "_source_id",
        "_stats",
        "_stopped",
        "_transport",
    )

    def __init__(
        self,
        source_id: str,
        config: StreamConfig,
        transport: WebSocketTransport | None = None,
    ) -> None:
        if not source_id.strip():
            raise MarketValidationError("StreamingSource.source_id cannot be empty.")

        self._source_id = source_id
        self._config = config
        self._transport = transport if transport is not None else WebSocketTransport()
        self._connection: WebSocketConnection | None = None
        self._counter = 0
        self._sequences: dict[str, int] = {}
        self._stats = StreamStats()
        self._stopped = False
        self._reading = False

    @property
    def source_id(self) -> str:
        """Identifier for this stream, used to derive record identities."""

        return self._source_id

    @property
    def ordering(self) -> OrderingGuarantee:
        """Always ``UNORDERED``. See this module's docstring.

        A venue cannot promise what a file can, and declaring otherwise would
        have :meth:`~alphalab.runtime.run.RunEngine.advance` raise on the first
        reordered record instead of skipping it.
        """

        return OrderingGuarantee.UNORDERED

    @property
    def stats(self) -> StreamStats:
        """What this stream has seen so far."""

        return self._stats

    @property
    def connected(self) -> bool:
        """Whether a connection is currently open."""

        return self._connection is not None and not self._connection.closed

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Ask the stream to shut down gracefully.

        Safe from another thread than the one iterating :meth:`records` -- which
        is the only useful case, since that thread is blocked on a socket. The
        reader notices within one poll interval, completes the WebSocket closing
        handshake, and the generator returns. It does not raise: a requested
        shutdown is not a failure.
        """

        self._stopped = True
        connection = self._connection
        if connection is not None:
            connection.close()

    def _connect(self) -> WebSocketConnection:
        """Open the connection and subscribe. The unit a reconnect repeats.

        Subscription is part of connecting, deliberately: a reopened socket that
        was not resubscribed is a connection that will sit silent forever, which
        is indistinguishable from a quiet market until someone notices no orders
        have been placed all day.
        """

        connection = self._transport.connect(self._config.url)
        connection.send(
            json.dumps(
                {"action": "subscribe", "symbols": list(self._config.symbols)},
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        self._connection = connection
        return connection

    def _reconnect(self) -> WebSocketConnection | None:
        """Reopen after a drop, or give up.

        Returns ``None`` when the attempt budget is spent, which ends the
        iteration. Raising here instead would make an expected end-of-stream
        indistinguishable from a defect.
        """

        limit = self._config.max_reconnects
        if limit is not None and self._stats.reconnects >= limit:
            return None

        self._stats = replace(self._stats, reconnects=self._stats.reconnects + 1)
        if self._config.reconnect_backoff_seconds > 0:
            time.sleep(self._config.reconnect_backoff_seconds)
        if self._stopped:
            return None

        try:
            return self._connect()
        except WebSocketError:
            # A venue that refuses the reopen is still down. The next iteration
            # tries again if the budget allows, and stops if it does not.
            self._connection = None
            return None

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def records(self) -> Iterator[MarketRecord]:
        """Yield canonical records as the venue sends them, until stopped.

        The full lifecycle, in one loop: connect, subscribe, read, deduplicate,
        normalize, yield; on a drop, reconnect and resubscribe and continue; on
        :meth:`stop`, complete the closing handshake and return.

        Raises:
            MarketValidationError: If a second iteration is started while one is
                already running. Two readers on one socket would each see half
                the messages, and neither would know it.
            WebSocketError: If the first connection cannot be opened. A stream
                that never started is a configuration error and is surfaced;
                one that started and then dropped is a reconnect.
        """

        if self._reading:
            raise MarketValidationError(
                f"Stream {self._source_id!r} is already being read. A live socket has "
                "one reader: a second would consume messages the first would then "
                "never see."
            )

        self._reading = True
        try:
            yield from self._read()
        finally:
            self._reading = False
            connection = self._connection
            if connection is not None:
                connection.close()
            self._connection = None

    def _read(self) -> Iterator[MarketRecord]:
        """The loop itself. Separated so `records` owns only the reader guard."""

        if self._stopped:
            # `stop` is final. Opening a socket here would connect, subscribe,
            # and immediately close it -- a visible connection at the venue for
            # a stream nobody is reading.
            return

        connection = self._connect()
        deadline = self._deadline()

        while not self._stopped:
            try:
                message = connection.poll()
            except WebSocketError:
                # The connection failed. Nothing partial is yielded from it.
                message = None
                connection.close()

            if connection.closed and not self._stopped:
                reopened = self._reconnect()
                if reopened is None:
                    return
                connection = reopened
                deadline = self._deadline()
                continue

            if message is None:
                if deadline is not None and time.monotonic() > deadline:
                    # Silence past the liveness limit. The venue has not said it
                    # is gone -- that is exactly the failure mode a heartbeat
                    # exists to reveal, and it is treated as a drop.
                    connection.close()
                continue

            deadline = self._deadline()
            self._stats = replace(self._stats, received=self._stats.received + 1)
            record = self._record_from(message)
            if record is not None:
                self._stats = replace(self._stats, yielded=self._stats.yielded + 1)
                yield record

        connection.close()

    def _deadline(self) -> float | None:
        """When the venue must have said something by, or ``None`` if never."""

        timeout = self._config.liveness_timeout_seconds
        return None if timeout is None else time.monotonic() + timeout

    # ------------------------------------------------------------------
    # Message translation
    # ------------------------------------------------------------------

    def _record_from(self, message: str) -> MarketRecord | None:
        """One venue message as a canonical record, or ``None`` if it is not one.

        Every rejection here is counted and none of them raises. A live feed
        that sends one malformed message must not take down a session holding
        positions; the counter is what makes the problem visible.
        """

        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            self._stats = replace(self._stats, malformed=self._stats.malformed + 1)
            return None

        if not isinstance(payload, Mapping):
            self._stats = replace(self._stats, malformed=self._stats.malformed + 1)
            return None

        kind = payload.get("type")
        if kind == _HEARTBEAT:
            self._stats = replace(self._stats, heartbeats=self._stats.heartbeats + 1)
            return None
        if kind == _SUBSCRIBED:
            return None
        if kind == _ERROR:
            self._stats = replace(self._stats, errors=self._stats.errors + 1)
            return None
        if kind not in {_QUOTE, _TRADE, _BAR}:
            self._stats = replace(self._stats, malformed=self._stats.malformed + 1)
            return None

        symbol = payload.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            self._stats = replace(self._stats, malformed=self._stats.malformed + 1)
            return None

        if not self._sequence_admits(symbol, payload):
            return None

        try:
            canonical = self._normalize(str(kind), symbol, payload)
        except (
            MarketValidationError,
            InstrumentResolutionError,
            KeyError,
            TypeError,
            ValueError,
        ):
            # An unregistered symbol, an invalid price, a field the venue simply
            # omitted (`KeyError`). All of them are this one message being
            # unusable, not the stream being broken -- and an unregistered
            # symbol in particular is a configuration fact that should be
            # visible as a count rather than as a killed session holding
            # positions.
            self._stats = replace(self._stats, malformed=self._stats.malformed + 1)
            return None

        if self._config.max_record_age_seconds is not None and is_stale(
            canonical.timestamp, time.time(), self._config.max_record_age_seconds
        ):
            self._stats = replace(self._stats, stale=self._stats.stale + 1)
            return None

        self._counter += 1
        return MarketRecord(
            f"{self._source_id}-{self._counter:0{_COUNTER_WIDTH}d}",
            canonical.timestamp,
            canonical,
        )

    def _sequence_admits(self, symbol: str, payload: Mapping[str, Any]) -> bool:
        """Whether this message is new, by the venue's per-symbol sequence.

        A message at or below the last sequence seen for its symbol is a
        redelivery -- which a reconnecting venue produces as a matter of course
        -- and is dropped. One that skips ahead is admitted and the skip is
        counted: the missing messages are gone, and fabricating them would be
        worse than recording that they are missing.

        A message carrying no sequence is admitted without deduplication. Not
        every venue sequences every channel, and refusing such a message would
        reject a whole feed over an optional field.
        """

        raw = payload.get("sequence")
        if raw is None:
            return True
        if not isinstance(raw, int) or isinstance(raw, bool):
            self._stats = replace(self._stats, malformed=self._stats.malformed + 1)
            return False

        last = self._sequences.get(symbol)
        if last is not None:
            if raw <= last:
                self._stats = replace(self._stats, duplicates=self._stats.duplicates + 1)
                return False
            if raw > last + 1:
                self._stats = replace(self._stats, gaps=self._stats.gaps + 1)

        self._sequences[symbol] = raw
        return True

    def _normalize(self, kind: str, symbol: str, payload: Mapping[str, Any]) -> MarketInput:
        """Lift one venue message into a canonical market input.

        Goes through :mod:`alphalab.market.normalization`'s existing
        ``normalize_wire_*``, so a streamed record and a historical one for the
        same instrument are produced by the same code: identity resolution,
        ``Decimal(str(...))`` precision, venue/currency/timeframe from the
        policy, and the unreported fields left at zero and documented as such.
        """

        timestamp = float(payload["timestamp"])
        if kind == _QUOTE:
            return normalize_wire_quote(
                WireQuote(
                    symbol=symbol,
                    timestamp=timestamp,
                    bid=float(payload["bid"]),
                    ask=float(payload["ask"]),
                    bid_size=float(payload.get("bid_size", 0.0)),
                    ask_size=float(payload.get("ask_size", 0.0)),
                ),
                self._config.policy,
            )
        if kind == _TRADE:
            return normalize_wire_trade(
                WireTrade(
                    symbol=symbol,
                    timestamp=timestamp,
                    price=float(payload["price"]),
                    size=float(payload["size"]),
                ),
                self._config.policy,
            )
        return normalize_wire_bar(
            WireBar(
                symbol=symbol,
                timestamp=timestamp,
                open=float(payload["open"]),
                high=float(payload["high"]),
                low=float(payload["low"]),
                close=float(payload["close"]),
                volume=float(payload.get("volume", 0.0)),
            ),
            self._config.policy,
        )
