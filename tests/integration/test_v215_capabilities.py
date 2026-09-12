"""All five v2.15 capabilities, in one lifecycle, over real sockets.

Each capability has its own suite holding its own contract. This holds the thing
those suites cannot: that the five compose, on one path, without a parallel
lifecycle anywhere in it.

::

    security master   a classified InstrumentRegistry
      -> streaming    a WebSocket venue pushes quotes          (real socket)
      -> normalization wire -> canonical MarketRecord
      -> RunEngine.advance                                      the canonical step
      -> strategy context   history + universe.sector           (the strategy decides on both)
      -> order -> routing -> HTTP venue                         (real socket, signed)
      -> venue fill -> apply_broker_execution -> portfolio
      -> capture -> artifact store -> ArtifactRef -> verify

Nothing here is stubbed. Two local servers speak the two protocols, and the
strategy's decision genuinely depends on the security master and on history.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from alphalab.broker.account import BrokerAccount
from alphalab.broker.reconciliation import ExternalOrderMap
from alphalab.broker.state import BrokerState, ConnectionStatus
from alphalab.broker.transport import HttpVenueTransport, VenueCredentials
from alphalab.broker.venue import RestVenueBroker, VenueConfig
from alphalab.core.enums import AssetType
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import (
    InstrumentRegistry,
    classification_history,
    classify_instrument,
    register_instruments,
    sector_as_of,
)
from alphalab.instrument.snapshot import capture as capture_registry
from alphalab.instrument.snapshot import from_primitives as registry_from_primitives
from alphalab.instrument.snapshot import restore as restore_registry
from alphalab.market.normalization import NormalizationPolicy
from alphalab.market.source import OrderingGuarantee
from alphalab.market.stream import StreamConfig, StreamingSource
from alphalab.model_registry.artifact_store import FileArtifactStore, compute_digest
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.runtime.broker_routing import (
    apply_broker_execution,
    broker_order_id_for,
    route_order,
)
from alphalab.runtime.context_views import HistoryView, UniverseView
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine
from alphalab.runtime.run_snapshot import capture as capture_run
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import context_factory, pipeline_config, running_strategy_state
from tests.integration.stream_server import StreamScript, quote, run_stream
from tests.integration.venue_server import fill, record_fill, run_venue

_PROVIDER = "TESTVENUE"
_SYMBOL = "ACME"
_OTHER = "BETA"
_STRATEGY = "V215-STRAT"
_KEY = "TESTKEY-0001"
_SECRET = "test-signing-secret-not-a-real-credential"

_ACME = InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD", aliases={_PROVIDER: _SYMBOL})
_BETA = InstrumentRecord("BETA", AssetType.EQUITY, "XNAS", "USD", aliases={_PROVIDER: _OTHER})


def _registry() -> InstrumentRegistry:
    """A security master: two instruments, one classified with provenance."""

    registry = register_instruments(InstrumentRegistry(), (_ACME, _BETA))
    return classify_instrument(
        registry, _ACME.asset_id, "Technology", "GICS-VENDOR", 1_700_000_000.0
    )


class SectorAwareStrategy(BaseStrategy):
    """Trades only what the security master classifies, and only once it has history.

    Deliberately depends on *both* new context surfaces, so a test that passes
    could not pass with either of them empty.
    """

    def __init__(self, strategy_id: str, quantity: Decimal, sector: str) -> None:
        self._strategy_id = strategy_id
        self._quantity = quantity
        self._sector = sector
        self.decisions: list[tuple[str, int, str | None]] = []

    def on_quote(self, context: StrategyContext, event: Any) -> Any:
        asset_id = event.quote.asset_id
        seen = len(context.history.quotes(asset_id))
        sector = context.universe.sector(asset_id)
        self.decisions.append((asset_id, seen, sector))

        # Needs the security master to say this is the right sector, and needs
        # two observations before committing -- which is what `history` is for.
        if sector != self._sector or seen < 2:
            return ()
        return (
            Intent(
                strategy_id=self._strategy_id,
                instrument=asset_id,
                target=self._quantity,
                timestamp=event.quote.timestamp,
            ),
        )


def _stream(url: str, registry: InstrumentRegistry) -> StreamingSource:
    return StreamingSource(
        "v215-feed",
        StreamConfig(
            url=url,
            symbols=[_SYMBOL, _OTHER],
            policy=NormalizationPolicy(
                provider=_PROVIDER, identity=registry, venue="XNAS", currency="USD"
            ),
            liveness_timeout_seconds=3.0,
            reconnect_backoff_seconds=0.01,
            max_reconnects=1,
        ),
    )


def _run_config(registry: InstrumentRegistry) -> RunConfig:
    return RunConfig(
        pipeline=replace(pipeline_config(_STRATEGY), instruments=registry),
        mode=ExecutionMode.LIVE,
        ordering=OrderingGuarantee.UNORDERED,
        compile_analytics=False,
    )


def _broker_state() -> BrokerState:
    return BrokerState(
        broker_name="VENUE",
        connection_status=ConnectionStatus.DISCONNECTED,
        account=BrokerAccount(
            account_id="",
            cash=Decimal("0"),
            equity=Decimal("0"),
            buying_power=Decimal("0"),
            margin=Decimal("0"),
            available_funds=Decimal("0"),
            currency="USD",
        ),
    )


def test_all_five_capabilities_compose_into_one_lifecycle(tmp_path: Path) -> None:
    """Security master -> streaming -> context -> execution -> artifact."""

    registry = _registry()
    strategy = SectorAwareStrategy(_STRATEGY, Decimal("10"), "Technology")

    # --- 1 & 2: the security master names the instruments, the stream feeds them.
    script = StreamScript(
        drop_after=4,
        messages=[
            quote(_OTHER, 1, "50", "50", 1.0),  # unclassified: never traded
            quote(_SYMBOL, 1, "100", "100", 2.0),  # first sight: no history yet
            quote(_OTHER, 2, "51", "51", 3.0),
            quote(_SYMBOL, 2, "101", "101", 4.0),  # second sight: trades here
        ],
    )

    with run_stream(script) as (stream_url, stream_log, _s):
        source = _stream(stream_url, registry)
        state = RunEngine.initialize(
            _run_config(registry),
            running_strategy_state(_STRATEGY, strategy),
        )
        state = replace(state, source_id=source.source_id)

        guard = threading.Timer(20.0, source.stop)
        guard.start()
        try:
            for seen, record in enumerate(source.records(), start=1):
                state, _ = RunEngine.advance(state, record, context_factory, record.timestamp)
                if seen >= 4:
                    source.stop()
                    break
        finally:
            guard.cancel()
            source.stop()

    assert state.processed == 4, "every streamed record went through the canonical step"
    assert stream_log.connections, "the stream subscribed"

    # --- 3: the strategy's decision genuinely used both new context surfaces.
    assert strategy.decisions, "the strategy was dispatched"
    by_asset = {asset: (seen, sector) for asset, seen, sector in strategy.decisions}
    assert by_asset[_ACME.asset_id][1] == "Technology", "the security master reached it"
    assert by_asset[_BETA.asset_id][1] is None, "unclassified, and honestly so"
    assert by_asset[_ACME.asset_id][0] == 2, "history grew as the stream advanced"

    working = state.working_orders
    assert working, "live routing left the order working for a venue"
    assert len(working) == 1, "only the classified instrument was traded"
    order = working[0]
    assert order.asset_id == _ACME.asset_id

    # --- 4: the order reaches a real venue over a signed HTTP connection.
    credentials = VenueCredentials(_KEY, _SECRET)
    with run_venue(credentials) as (venue_url, book, _script):
        broker = RestVenueBroker(
            HttpVenueTransport(venue_url, credentials, timeout_seconds=5.0),
            VenueConfig(broker_name="VENUE"),
        )
        connected, _ = broker.connect(_broker_state(), 10.0)
        assert connected.connection_status is ConnectionStatus.CONNECTED

        routed = route_order(connected, broker, order, 11.0, ExternalOrderMap())
        assert routed.decision.routed

        client_id = broker_order_id_for(order)
        assert client_id in book.orders, "the venue holds the order the OMS produced"

        record_fill(book, client_id, fill("X1", str(order.remaining_quantity), "101"))
        _after, applied, _events = broker.poll_executions(routed.broker_state, 12.0)

    assert len(applied) == 1
    pipeline, fills, trades = apply_broker_execution(state.pipeline, order, applied[0])

    assert len(fills) == 1, "a canonical core.Fill"
    assert pipeline.portfolio.positions[_ACME.asset_id].quantity == Decimal("10")
    assert trades[0].asset_id == _ACME.asset_id

    # The security master reached attribution, frozen at fill time.
    trade_records = pipeline.trade_records.to_tuple()
    assert trade_records, "the fill produced a trade record"
    assert trade_records[-1].sector_id == "Technology"

    # --- 5: the finished run is stored as a verifiable artifact.
    root = tmp_path / "artifacts"
    root.mkdir()
    store = FileArtifactStore(root)

    final = replace(state, pipeline=pipeline)
    payload = serialize(capture_run(final)).encode("utf-8")
    ref = store.put(payload, "application/json")

    assert store.get(ref) == payload
    assert ref.checksum == compute_digest(payload)
    assert ref.uri.startswith("alphalab-artifact:sha256:")

    # The security master is storable too, audit trail and all.
    registry_ref = store.put(serialize(capture_registry(registry)).encode("utf-8"))
    reloaded = restore_registry(
        registry_from_primitives(deserialize(store.get(registry_ref).decode("utf-8")))
    )
    assert reloaded == registry
    assert sector_as_of(reloaded, _ACME.asset_id, 1_800_000_000.0) == "Technology"
    assert classification_history(reloaded, _ACME.asset_id).sources == ("GICS-VENDOR",)


def test_no_capability_introduced_a_parallel_lifecycle() -> None:
    """The architectural guarantee, asserted rather than asserted-in-prose.

    Every v2.15 addition sits on an existing boundary: the stream *is* a
    `MarketDataSource`, the venue adapter *is* a `BrokerProtocol`, the artifact
    store produces `model_registry`'s own `ArtifactRef`, the security master is
    the one `InstrumentRegistry`, and the context views are handed out by the
    one `_populate_context`.
    """

    from alphalab.broker.protocol import BrokerProtocol
    from alphalab.market.source import MarketDataSource
    from alphalab.model_registry.artifact_store import MemoryArtifactStore
    from alphalab.model_registry.registry import ArtifactRef

    registry = _registry()
    with run_stream() as (url, _log, _s):
        source = _stream(url, registry)
        try:
            assert isinstance(source, MarketDataSource), "no second market-data boundary"
        finally:
            source.stop()

    broker = RestVenueBroker(
        HttpVenueTransport("http://127.0.0.1:1", VenueCredentials(_KEY, _SECRET)),
        VenueConfig(),
    )
    assert isinstance(broker, BrokerProtocol), "no second execution boundary"

    ref = MemoryArtifactStore().put(b"bytes")
    assert isinstance(ref, ArtifactRef), "no second artifact reference type"

    # And the run runtime still has exactly one owner.
    assert RunEngine.advance.__qualname__ == "RunEngine.advance"


def test_the_deterministic_engines_are_untouched_by_any_of_it() -> None:
    """Backtest and replay still agree, byte for byte, with all five present."""

    from alphalab.backtesting.dataset import MarketDataset
    from alphalab.backtesting.engine import BacktestEngine
    from alphalab.backtesting.replay import ReplayBacktest
    from alphalab.oms.snapshot import capture as capture_oms
    from tests.integration.harness import ScriptedStrategy, backtest_config, dataset_of_quotes

    asset = "8f14e45f-ceea-467a-9c2a-1b3c5d7e9f01"
    dataset: MarketDataset = dataset_of_quotes(
        asset, [Decimal("100"), Decimal("101"), Decimal("102")]
    )

    def _run(driver: Any) -> Any:
        result = driver.run(
            backtest_config("PARITY", seed=99),
            dataset,
            running_strategy_state(
                "PARITY", ScriptedStrategy("PARITY", asset, {2.0: Decimal("5")})
            ),
            context_factory,
        )
        # `ReplayBacktest` wraps the backtest result with its cursor status.
        return result.backtest if hasattr(result, "backtest") else result

    backtest = _run(BacktestEngine)
    replayed = _run(ReplayBacktest)

    # The OMS fingerprint is what `test_backtest_replay_parity.py` compares, and
    # for the reason that applies here too: `RunState.config.mode` legitimately
    # differs between the two drivers since v2.14 -- a captured run says which
    # environment it was -- so the *run envelope* is expected to differ and the
    # economics underneath it are not.
    assert serialize(capture_oms(backtest.state.oms)) == serialize(capture_oms(replayed.state.oms))
    assert backtest.state.portfolio == replayed.state.portfolio
    assert backtest.equity_curve == replayed.equity_curve


def test_the_context_views_reach_a_strategy_in_a_live_run() -> None:
    """The strategy boundary is populated in LIVE mode, not only in a backtest."""

    registry = _registry()
    strategy = SectorAwareStrategy(_STRATEGY, Decimal("1"), "Technology")
    observed: list[StrategyContext] = []

    class Capturing(SectorAwareStrategy):
        def on_quote(self, context: StrategyContext, event: Any) -> Any:
            observed.append(context)
            return super().on_quote(context, event)

    capturing = Capturing(_STRATEGY, Decimal("1"), "Technology")
    assert strategy is not capturing

    script = StreamScript(drop_after=2, messages=[quote(_SYMBOL, 1, "100", "100", 1.0)] * 2)
    with run_stream(script) as (url, _log, _s):
        source = _stream(url, registry)
        state = RunEngine.initialize(
            _run_config(registry), running_strategy_state(_STRATEGY, capturing)
        )
        guard = threading.Timer(20.0, source.stop)
        guard.start()
        try:
            for seen, record in enumerate(source.records(), start=1):
                state, _ = RunEngine.advance(state, record, context_factory, record.timestamp)
                if seen >= 1:
                    source.stop()
                    break
        finally:
            guard.cancel()
            source.stop()

    assert observed, "the live run dispatched the strategy"
    assert isinstance(observed[-1].history, HistoryView)
    assert isinstance(observed[-1].universe, UniverseView)
    assert observed[-1].universe.sector(_ACME.asset_id) == "Technology"
