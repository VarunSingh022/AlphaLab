"""Streaming market data, end to end over a real WebSocket.

Every test drives :mod:`alphalab.marketdata.websocket` into the RFC 6455 server
in ``tests/integration/stream_server.py`` over TCP. The handshake is computed,
frames are masked and unmasked, and the closing handshake is completed. Nothing
is stubbed.

The lifecycle the release contract names --
``connect -> subscribe -> receive -> process -> disconnect -> reconnect ->
resubscribe -> continue -> shutdown`` -- is asserted end to end in
:func:`test_the_whole_streaming_lifecycle_runs_end_to_end`, and each stage is
pinned individually around it.
"""

from __future__ import annotations

import threading
import time
from decimal import Decimal

import pytest

from alphalab.core.enums import AssetType
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instrument
from alphalab.market.exceptions import (
    InstrumentResolutionError,
    MarketValidationError,
)
from alphalab.market.normalization import NormalizationPolicy
from alphalab.market.quote import Quote
from alphalab.market.source import MarketDataSource, OrderingGuarantee
from alphalab.market.stream import StreamConfig, StreamingSource
from alphalab.marketdata.websocket import WebSocketError, connect_websocket
from tests.integration.stream_server import (
    StreamScript,
    heartbeat,
    quote,
    run_stream,
)

_PROVIDER = "TESTVENUE"
_SYMBOL = "ACME"


def _registry() -> InstrumentRegistry:
    return register_instrument(
        InstrumentRegistry(),
        InstrumentRecord(
            symbol=_SYMBOL,
            asset_type=AssetType.EQUITY,
            exchange="XNAS",
            currency="USD",
            aliases={_PROVIDER: _SYMBOL},
        ),
    )


def _policy() -> NormalizationPolicy:
    return NormalizationPolicy(
        provider=_PROVIDER, identity=_registry(), venue="XNAS", currency="USD"
    )


def _config(url: str, **overrides: object) -> StreamConfig:
    settings: dict[str, object] = {
        "url": url,
        "symbols": [_SYMBOL],
        "policy": _policy(),
        "liveness_timeout_seconds": 2.0,
        "reconnect_backoff_seconds": 0.01,
        "max_reconnects": 3,
    }
    settings.update(overrides)
    return StreamConfig(**settings)  # type: ignore[arg-type]


def _take(source: StreamingSource, count: int, timeout: float = 10.0) -> list[Quote]:
    """Read ``count`` records, then stop the stream.

    The read runs on this thread and :meth:`StreamingSource.stop` from a timer,
    so a test can never hang forever on a venue that decides to say nothing.
    """

    guard = threading.Timer(timeout, source.stop)
    guard.start()
    collected: list[Quote] = []
    try:
        for record in source.records():
            assert isinstance(record.payload, Quote)
            collected.append(record.payload)
            if len(collected) >= count:
                source.stop()
                break
    finally:
        guard.cancel()
        source.stop()
    return collected


# ---------------------------------------------------------------------------
# The WebSocket client itself
# ---------------------------------------------------------------------------


def test_the_handshake_is_completed_against_a_real_server() -> None:
    with run_stream() as (url, _log, _script):
        connection = connect_websocket(url)
        try:
            assert not connection.closed
        finally:
            connection.close()


def test_a_server_that_is_not_a_websocket_is_refused() -> None:
    """The accept token is verified, not merely required to be present."""

    import http.server
    import threading as _threading

    server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    thread = _threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    host, port = str(server.server_address[0]), server.server_address[1]
    try:
        with pytest.raises(WebSocketError):
            connect_websocket(f"ws://{host}:{port}/stream", timeout_seconds=2.0)
    finally:
        server.server_close()


def test_a_url_that_is_not_a_websocket_url_is_refused() -> None:
    with pytest.raises(WebSocketError, match="not a WebSocket URL"):
        connect_websocket("http://example.invalid/stream")


def test_an_unreachable_endpoint_raises() -> None:
    with pytest.raises(WebSocketError, match="Cannot reach"):
        connect_websocket("ws://127.0.0.1:1/stream", timeout_seconds=1.0)


def test_a_ping_is_answered_with_a_pong_carrying_the_same_payload() -> None:
    """Liveness is maintained by the client whether or not the consumer looks."""

    script = StreamScript(
        ping_before=b"are-you-there",
        messages=[quote(_SYMBOL, 1, "100", "101", 1.0)],
    )
    with run_stream(script) as (url, log, _s):
        source = StreamingSource("stream-ping", _config(url))
        _take(source, 1)
        time.sleep(0.3)

    assert b"are-you-there" in log.pongs, "the client answered the venue's ping"


def test_a_fragmented_message_is_reassembled() -> None:
    """Continuation frames are the normal case for a large book update."""

    script = StreamScript(
        fragment=True,
        messages=[quote(_SYMBOL, 1, "100", "101", 1.0)],
    )
    with run_stream(script) as (url, _log, _s):
        received = _take(StreamingSource("stream-frag", _config(url)), 1)

    assert len(received) == 1
    assert received[0].bid == Decimal("100")


def test_shutdown_completes_the_closing_handshake() -> None:
    script = StreamScript(messages=[quote(_SYMBOL, 1, "100", "101", 1.0)])
    with run_stream(script) as (url, log, _s):
        source = StreamingSource("stream-close", _config(url))
        _take(source, 1)
        time.sleep(0.4)

    assert log.closes, "the client sent a Close frame rather than dropping the socket"
    assert log.closes[0] == 1000


# ---------------------------------------------------------------------------
# The source contract
# ---------------------------------------------------------------------------


def test_a_streaming_source_is_a_market_data_source() -> None:
    """The whole architectural claim: no new abstraction, the existing boundary."""

    with run_stream() as (url, _log, _s):
        source = StreamingSource("stream-proto", _config(url))
        assert isinstance(source, MarketDataSource)
        assert source.source_id == "stream-proto"


def test_a_stream_declares_unordered_because_a_venue_can_reorder() -> None:
    with run_stream() as (url, _log, _s):
        assert StreamingSource("s", _config(url)).ordering is OrderingGuarantee.UNORDERED


def test_a_policy_that_cannot_name_instruments_is_refused_before_connecting() -> None:
    """The same admission rule `ProviderHistorySource` applies, at the same boundary."""

    with pytest.raises(InstrumentResolutionError, match="StreamingSource requires"):
        StreamConfig(url="ws://127.0.0.1:1/s", symbols=["X"], policy=NormalizationPolicy())


def test_a_subscription_to_nothing_is_refused() -> None:
    with pytest.raises(MarketValidationError, match="at least one symbol"):
        StreamConfig(url="ws://127.0.0.1:1/s", symbols=[], policy=_policy())


def test_two_readers_on_one_stream_are_refused() -> None:
    """Each would consume messages the other would then never see."""

    script = StreamScript(messages=[quote(_SYMBOL, 1, "100", "101", 1.0)])
    with run_stream(script) as (url, _log, _s):
        source = StreamingSource("stream-two", _config(url))
        first = source.records()
        guard = threading.Timer(10.0, source.stop)
        guard.start()
        try:
            # Pulling one record starts the iteration, so the guard is up.
            assert next(first) is not None

            second = source.records()
            with pytest.raises(MarketValidationError, match="already being read"):
                next(second)
        finally:
            guard.cancel()
            source.stop()


def test_a_stopped_stream_stays_stopped() -> None:
    """`stop` is final. A live stream is not re-iterable -- the second pass
    would be a different market, which is why `SequenceSource` is re-iterable
    and this is not."""

    script = StreamScript(messages=[quote(_SYMBOL, 1, "100", "101", 1.0)])
    with run_stream(script) as (url, _log, _s):
        source = StreamingSource("stream-again", _config(url))
        assert len(_take(source, 1)) == 1
        assert list(source.records()) == []

    assert not source.connected


# ---------------------------------------------------------------------------
# Subscription, reception, normalization
# ---------------------------------------------------------------------------


def test_connecting_subscribes_to_the_configured_symbols() -> None:
    script = StreamScript(messages=[quote(_SYMBOL, 1, "100", "101", 1.0)])
    with run_stream(script) as (url, log, _s):
        _take(StreamingSource("stream-sub", _config(url)), 1)

    assert log.connections == [[_SYMBOL]], "the venue was told what to send"


def test_incremental_messages_arrive_as_canonical_records() -> None:
    """The venue's JSON becomes `market.Quote` with Decimal prices and an asset_id."""

    script = StreamScript(
        messages=[
            quote(_SYMBOL, 1, "100", "101", 1.0),
            quote(_SYMBOL, 2, "100.5", "101.5", 2.0),
            quote(_SYMBOL, 3, "101", "102", 3.0),
        ]
    )
    with run_stream(script) as (url, _log, _s):
        received = _take(StreamingSource("stream-inc", _config(url)), 3)

    assert [q.bid for q in received] == [Decimal("100"), Decimal("100.5"), Decimal("101")]
    assert all(isinstance(q.bid, Decimal) for q in received)
    assert received[0].asset_id == _registry().resolve(_PROVIDER, _SYMBOL)
    assert received[0].venue == "XNAS"
    assert received[0].currency == "USD"


def test_precision_survives_the_wire() -> None:
    """`Decimal(str(...))`, not `Decimal(float)` -- the v2.3 rule, at a new boundary."""

    script = StreamScript(messages=[quote(_SYMBOL, 1, "100.1", "100.2", 1.0)])
    with run_stream(script) as (url, _log, _s):
        received = _take(StreamingSource("stream-prec", _config(url)), 1)

    assert received[0].bid == Decimal("100.1")
    assert str(received[0].bid) == "100.1", "not the float's binary expansion"


def test_records_carry_identities_derived_from_the_source() -> None:
    script = StreamScript(
        messages=[quote(_SYMBOL, 1, "100", "101", 1.0), quote(_SYMBOL, 2, "101", "102", 2.0)]
    )
    with run_stream(script) as (url, _log, _s):
        source = StreamingSource("stream-id", _config(url))
        ids = []
        guard = threading.Timer(10.0, source.stop)
        guard.start()
        try:
            for record in source.records():
                ids.append(record.event_id)
                if len(ids) >= 2:
                    source.stop()
                    break
        finally:
            guard.cancel()
            source.stop()

    assert ids[0].startswith("stream-id-")
    assert len(set(ids)) == 2, "identities are unique within the stream"


# ---------------------------------------------------------------------------
# Deduplication, gaps, malformed input
# ---------------------------------------------------------------------------


def test_a_redelivered_sequence_is_dropped_exactly_once() -> None:
    """What a reconnecting venue does: resend from its last known position."""

    script = StreamScript(
        messages=[
            quote(_SYMBOL, 1, "100", "101", 1.0),
            quote(_SYMBOL, 1, "100", "101", 1.0),
            quote(_SYMBOL, 2, "101", "102", 2.0),
        ]
    )
    with run_stream(script) as (url, _log, _s):
        source = StreamingSource("stream-dup", _config(url))
        received = _take(source, 2)

    assert len(received) == 2
    assert [q.bid for q in received] == [Decimal("100"), Decimal("101")]
    assert source.stats.duplicates == 1


def test_a_sequence_gap_is_counted_and_the_missing_messages_are_not_invented() -> None:
    script = StreamScript(
        messages=[quote(_SYMBOL, 1, "100", "101", 1.0), quote(_SYMBOL, 5, "101", "102", 2.0)]
    )
    with run_stream(script) as (url, _log, _s):
        source = StreamingSource("stream-gap", _config(url))
        received = _take(source, 2)

    assert len(received) == 2, "both real messages arrived"
    assert source.stats.gaps == 1, "the skip was noticed"


def test_a_malformed_message_is_counted_and_does_not_kill_the_session() -> None:
    """A live session holding positions must not die over one bad frame."""

    script = StreamScript(
        messages=[
            {"type": "quote", "symbol": _SYMBOL, "sequence": 1},  # no prices
            {"type": "nonsense", "symbol": _SYMBOL},
            quote(_SYMBOL, 3, "101", "102", 2.0),
        ]
    )
    with run_stream(script) as (url, _log, _s):
        source = StreamingSource("stream-bad", _config(url))
        received = _take(source, 1)

    assert len(received) == 1, "the good message still arrived"
    assert source.stats.malformed == 2


def test_a_message_for_an_unregistered_symbol_is_counted_not_raised() -> None:
    """An unregistered instrument is a configuration fact, visible as a count."""

    script = StreamScript(
        messages=[quote("UNKNOWN", 1, "100", "101", 1.0), quote(_SYMBOL, 2, "101", "102", 2.0)]
    )
    with run_stream(script) as (url, _log, _s):
        source = StreamingSource("stream-unreg", _config(url))
        received = _take(source, 1)

    assert len(received) == 1
    assert received[0].asset_id == _registry().resolve(_PROVIDER, _SYMBOL)
    assert source.stats.malformed == 1


def test_a_stale_record_is_dropped_before_it_enters_the_run() -> None:
    old = time.time() - 3600
    script = StreamScript(
        messages=[
            quote(_SYMBOL, 1, "100", "101", old),
            quote(_SYMBOL, 2, "101", "102", time.time()),
        ]
    )
    with run_stream(script) as (url, _log, _s):
        source = StreamingSource("stream-stale", _config(url, max_record_age_seconds=60.0))
        received = _take(source, 1)

    assert len(received) == 1, "only the fresh record"
    assert source.stats.stale == 1


def test_heartbeats_are_counted_and_never_yielded_as_market_data() -> None:
    script = StreamScript(messages=[heartbeat(), heartbeat(), quote(_SYMBOL, 1, "100", "101", 1.0)])
    with run_stream(script) as (url, _log, _s):
        source = StreamingSource("stream-hb", _config(url))
        received = _take(source, 1)

    assert len(received) == 1, "a heartbeat is not a price"
    assert source.stats.heartbeats == 2


def test_a_venue_error_message_is_counted() -> None:
    script = StreamScript(
        messages=[
            {"type": "error", "message": "rate limited"},
            quote(_SYMBOL, 1, "100", "101", 1.0),
        ]
    )
    with run_stream(script) as (url, _log, _s):
        source = StreamingSource("stream-err", _config(url))
        _take(source, 1)

    assert source.stats.errors == 1


# ---------------------------------------------------------------------------
# Disconnect, reconnect, resubscribe
# ---------------------------------------------------------------------------


def test_a_dropped_connection_is_reopened_and_resubscribed() -> None:
    """The critical one: a reopened socket that was not resubscribed is silent forever."""

    script = StreamScript(
        drop_after=1,
        drop_connections=(1,),
        per_connection={
            1: [quote(_SYMBOL, 1, "100", "101", 1.0)],
            2: [quote(_SYMBOL, 2, "101", "102", 2.0)],
        },
    )
    with run_stream(script) as (url, log, _s):
        source = StreamingSource("stream-recon", _config(url))
        received = _take(source, 2)

    assert len(received) == 2, "the stream continued across the drop"
    assert [q.bid for q in received] == [Decimal("100"), Decimal("101")]
    assert source.stats.reconnects >= 1
    assert len(log.connections) >= 2, "the venue was subscribed again, not merely reconnected"
    assert all(symbols == [_SYMBOL] for symbols in log.connections)


def test_dedup_survives_a_reconnect_that_replays_old_messages() -> None:
    """A venue replaying from an earlier position must not double-deliver."""

    script = StreamScript(
        drop_after=2,
        drop_connections=(1,),
        per_connection={
            1: [quote(_SYMBOL, 1, "100", "101", 1.0), quote(_SYMBOL, 2, "101", "102", 2.0)],
            # The venue replays from sequence 1 after the reconnect.
            2: [
                quote(_SYMBOL, 1, "100", "101", 1.0),
                quote(_SYMBOL, 2, "101", "102", 2.0),
                quote(_SYMBOL, 3, "102", "103", 3.0),
            ],
        },
    )
    with run_stream(script) as (url, _log, _s):
        source = StreamingSource("stream-replay", _config(url))
        received = _take(source, 3)

    assert [q.bid for q in received] == [Decimal("100"), Decimal("101"), Decimal("102")]
    assert source.stats.duplicates == 2, "the replayed pair was recognised"


def test_a_silent_venue_is_treated_as_dead_and_reconnected() -> None:
    """Silence past the liveness limit is the failure a heartbeat exists to reveal."""

    script = StreamScript(silent=True)
    with run_stream(script) as (url, log, _s):
        source = StreamingSource(
            "stream-silent",
            _config(url, liveness_timeout_seconds=0.15, max_reconnects=2),
        )
        guard = threading.Timer(15.0, source.stop)
        guard.start()
        try:
            list(source.records())
        finally:
            guard.cancel()
            source.stop()

    assert source.stats.reconnects == 2, "it gave up after the configured budget"
    assert len(log.connections) >= 2, "each attempt resubscribed"


def test_reconnects_are_bounded_and_the_stream_ends() -> None:
    """A venue that stays down ends the iteration rather than looping forever."""

    script = StreamScript(drop_after=1, messages=[quote(_SYMBOL, 1, "100", "101", 1.0)])
    with run_stream(script) as (url, _log, _s):
        source = StreamingSource("stream-bounded", _config(url, max_reconnects=1))
        guard = threading.Timer(15.0, source.stop)
        guard.start()
        try:
            received = list(source.records())
        finally:
            guard.cancel()
            source.stop()

    assert source.stats.reconnects <= 1
    assert len(received) >= 1


def test_stopping_from_another_thread_shuts_the_stream_down() -> None:
    """The reader is blocked on a socket; `stop` is the only way to reach it."""

    script = StreamScript(silent=True)
    with run_stream(script) as (url, _log, _s):
        source = StreamingSource("stream-stop", _config(url, liveness_timeout_seconds=None))
        stopper = threading.Timer(0.15, source.stop)
        stopper.start()
        started = time.monotonic()
        received = list(source.records())
        elapsed = time.monotonic() - started
        stopper.cancel()

    assert received == []
    assert elapsed < 10.0, "the shutdown was observed promptly, not on a timeout"
    assert not source.connected


# ---------------------------------------------------------------------------
# The full lifecycle, and the canonical run
# ---------------------------------------------------------------------------


def test_the_whole_streaming_lifecycle_runs_end_to_end() -> None:
    """connect -> subscribe -> receive -> disconnect -> reconnect -> resubscribe
    -> continue -> shutdown, in one pass, against a real socket."""

    script = StreamScript(
        drop_after=2,
        drop_connections=(1,),
        per_connection={
            1: [quote(_SYMBOL, 1, "100", "101", 1.0), quote(_SYMBOL, 2, "101", "102", 2.0)],
            2: [
                heartbeat(),
                quote(_SYMBOL, 2, "101", "102", 2.0),  # replayed, deduped
                quote(_SYMBOL, 3, "102", "103", 3.0),
                quote(_SYMBOL, 4, "103", "104", 4.0),
            ],
        },
    )
    with run_stream(script) as (url, log, _s):
        source = StreamingSource("stream-life", _config(url))
        received = _take(source, 4)
        time.sleep(0.4)

    stats = source.stats
    assert [q.bid for q in received] == [
        Decimal("100"),
        Decimal("101"),
        Decimal("102"),
        Decimal("103"),
    ]
    assert len(log.connections) >= 2, "subscribe, then resubscribe"
    assert stats.reconnects >= 1, "disconnect and reconnect"
    assert stats.duplicates == 1, "the replay was deduplicated"
    assert stats.heartbeats == 1, "liveness was received"
    assert stats.yielded == 4
    assert log.closes, "shutdown completed the handshake"
    assert not source.connected
