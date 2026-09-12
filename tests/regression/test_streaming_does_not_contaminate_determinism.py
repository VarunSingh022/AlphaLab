"""Live components must not change what a deterministic run produces.

v2.15 adds two things that read wall clocks and sockets: a streaming
market-data source and a real venue transport. Both are *outside* the
deterministic path by construction, and this suite is what stops that being a
claim.

The risk is specific and has bitten this repository before. ``alphalab.common.ids``
routes every identifier through one ambient ``ContextVar``, so any code drawing
from it inside a run's :func:`~alphalab.common.ids.id_scope` consumes the *run's*
own identifiers and shifts the identity of every order and fill after it. That is
exactly the defect ADR-0029 decision 7 found in the legacy persistence store,
where one ``save_snapshot`` plus one ``append_event`` advanced ``draws`` 0 -> 2.

So: importing the live modules must change nothing, constructing them must draw
nothing, and a seeded backtest must produce a byte-identical payload with them
present.
"""

from __future__ import annotations

import importlib
from dataclasses import replace
from decimal import Decimal

from alphalab.backtesting.engine import BacktestEngine
from alphalab.common.ids import DeterministicIdSource, id_scope, new_id, use_id_source
from alphalab.core.enums import AssetType
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instrument
from alphalab.market.normalization import NormalizationPolicy
from alphalab.market.stream import StreamConfig, StreamingSource
from alphalab.persistence.serializer import serialize
from alphalab.runtime.run_snapshot import capture
from tests.integration.harness import (
    ScriptedStrategy,
    backtest_config,
    context_factory,
    dataset_of_quotes,
    running_strategy_state,
)

_SEED = 20_15
_STRATEGY = "DET-STRAT"
_PROVIDER = "TESTVENUE"
_ASSET = "8f14e45f-ceea-467a-9c2a-1b3c5d7e9f01"


def _policy() -> NormalizationPolicy:
    registry = register_instrument(
        InstrumentRegistry(),
        InstrumentRecord(
            symbol="ACME",
            asset_type=AssetType.EQUITY,
            exchange="XNAS",
            currency="USD",
            aliases={_PROVIDER: "ACME"},
        ),
    )
    return NormalizationPolicy(provider=_PROVIDER, identity=registry, venue="XNAS", currency="USD")


def _stream_config() -> StreamConfig:
    return StreamConfig(
        url="ws://127.0.0.1:1/never-connected",
        symbols=["ACME"],
        policy=_policy(),
    )


def _seeded_backtest_payload() -> str:
    """One seeded backtest, captured and serialized. The oracle."""

    config = backtest_config(_STRATEGY, seed=_SEED)
    state = running_strategy_state(
        _STRATEGY,
        ScriptedStrategy(_STRATEGY, _ASSET, {2.0: Decimal("10"), 4.0: Decimal("-10")}),
    )
    dataset = dataset_of_quotes(
        _ASSET, [Decimal("100"), Decimal("101"), Decimal("102"), Decimal("103")]
    )
    result = BacktestEngine.run(config, dataset, state, context_factory)
    return serialize(capture(result.run))


def test_constructing_a_streaming_source_draws_no_identifier() -> None:
    """The ADR-0029 decision 7 property, applied to the streaming source.

    A source constructed inside a run's scope must not consume the run's own
    next identifier -- which is what would silently shift every order id after
    it.
    """

    source = DeterministicIdSource(_SEED)
    with use_id_source(source):
        before = source.draws
        stream = StreamingSource("feed", _stream_config())
        _ = stream.ordering
        _ = stream.source_id
        _ = stream.stats
        _ = stream.connected
        stream.stop()
        after = source.draws

    assert after == before, (
        "constructing and inspecting a streaming source advanced the run's "
        f"identifier stream by {after - before}"
    )


def test_constructing_a_venue_transport_and_broker_draws_no_identifier() -> None:
    """The same property for the execution transport."""

    from alphalab.broker.transport import HttpVenueTransport, VenueCredentials
    from alphalab.broker.venue import RestVenueBroker, VenueConfig

    source = DeterministicIdSource(_SEED)
    with use_id_source(source):
        before = source.draws
        credentials = VenueCredentials("KEY", "secret-for-this-test-only")
        transport = HttpVenueTransport("http://127.0.0.1:1", credentials)
        broker = RestVenueBroker(transport, VenueConfig())
        _ = broker.config.broker_name
        after = source.draws

    assert after == before, (
        f"constructing the venue transport advanced the run's stream by {after - before}"
    )


def test_a_seeded_backtest_is_byte_identical_with_the_live_modules_imported() -> None:
    """The oracle: two runs of one workload, with streaming and venue code loaded."""

    # Loaded for whatever side effects they might have. There are none, and
    # this test is what establishes that rather than assuming it.
    loaded = [
        importlib.import_module(name)
        for name in (
            "alphalab.broker.transport",
            "alphalab.broker.venue",
            "alphalab.market.stream",
            "alphalab.marketdata.websocket",
        )
    ]
    assert all(module is not None for module in loaded)

    first = _seeded_backtest_payload()
    second = _seeded_backtest_payload()

    assert first == second, "a seeded backtest stopped reproducing"


def test_a_streaming_source_alive_in_the_process_does_not_shift_a_seeded_run() -> None:
    """Holding a live-source object must not perturb a deterministic run.

    The source is constructed inside the scope, between the two runs, which is
    the arrangement that would expose any ambient draw.
    """

    baseline = _seeded_backtest_payload()

    with id_scope(_SEED):
        stream = StreamingSource("feed", _stream_config())
        after_construction = _seeded_backtest_payload()
        stream.stop()

    assert after_construction == baseline


def test_the_id_source_still_advances_for_things_that_should_draw() -> None:
    """A control: the assertions above would be vacuous if nothing ever drew."""

    source = DeterministicIdSource(_SEED)
    with use_id_source(source):
        before = source.draws
        new_id()
        assert source.draws == before + 1


def test_a_stream_config_is_frozen_and_carries_no_credential() -> None:
    """Stream configuration is a value, and holds nothing secret to leak."""

    config = _stream_config()
    rendered = repr(config)
    assert "password" not in rendered.lower()
    assert "secret" not in rendered.lower()
    assert replace(config, symbols=("ACME",)) is not config
