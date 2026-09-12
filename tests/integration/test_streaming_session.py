"""A live WebSocket stream driving the canonical execution path.

This is the test the streaming work exists for. Everything in
``test_streaming_market_data.py`` proves the stream produces canonical records;
this proves those records go through
:meth:`~alphalab.runtime.run.RunEngine.advance` -- the same function a backtest
and a replay take -- and come out as orders, fills and a marked portfolio.

If this passes, the stream is not "an unused provider wrapper": it is on the
execution path, and the path cannot tell it from a stored dataset.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.core.enums import AssetType
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instrument
from alphalab.market.exceptions import MarketValidationError
from alphalab.market.normalization import NormalizationPolicy
from alphalab.market.source import OrderingGuarantee
from alphalab.market.stream import StreamConfig, StreamingSource
from alphalab.runtime.run import ExecutionMode, RunConfig, RunEngine
from alphalab.runtime.session import TradingSession
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    running_strategy_state,
)
from tests.integration.stream_server import StreamScript, quote, run_stream

_PROVIDER = "TESTVENUE"
_SYMBOL = "ACME"
_STRATEGY = "STREAM-STRAT"


def _instrument() -> InstrumentRecord:
    return InstrumentRecord(
        symbol=_SYMBOL,
        asset_type=AssetType.EQUITY,
        exchange="XNAS",
        currency="USD",
        aliases={_PROVIDER: _SYMBOL},
    )


def _registry() -> InstrumentRegistry:
    return register_instrument(InstrumentRegistry(), _instrument())


def _asset_id() -> str:
    return _instrument().asset_id


def _source(
    url: str, source_id: str = "live-feed", max_reconnects: int | None = 2
) -> StreamingSource:
    return StreamingSource(
        source_id,
        StreamConfig(
            url=url,
            symbols=[_SYMBOL],
            policy=NormalizationPolicy(
                provider=_PROVIDER, identity=_registry(), venue="XNAS", currency="USD"
            ),
            liveness_timeout_seconds=3.0,
            reconnect_backoff_seconds=0.01,
            max_reconnects=max_reconnects,
        ),
    )


def _run_config(mode: ExecutionMode = ExecutionMode.PAPER) -> RunConfig:
    """A run over a streaming source.

    ``ordering`` is ``UNORDERED`` because the source declares it: a venue can
    reorder, so a run that demanded chronology would be refused before the first
    record. That refusal is asserted below rather than worked around.
    """

    return RunConfig(
        pipeline=replace(pipeline_config(_STRATEGY), instruments=_registry()),
        mode=mode,
        ordering=OrderingGuarantee.UNORDERED,
        compile_analytics=False,
    )


def _strategy_state(plan: dict[float, Decimal]):  # type: ignore[no-untyped-def]
    return running_strategy_state(_STRATEGY, ScriptedStrategy(_STRATEGY, _asset_id(), plan))


def test_a_stream_drives_the_canonical_run_step_and_produces_a_fill() -> None:
    """The whole point: socket -> record -> RunEngine.advance -> order -> fill."""

    script = StreamScript(
        messages=[
            quote(_SYMBOL, 1, "100", "100", 1.0),
            quote(_SYMBOL, 2, "101", "101", 2.0),
            quote(_SYMBOL, 3, "102", "102", 3.0),
        ]
    )
    with run_stream(script) as (url, _log, _s):
        source = _source(url)
        state = replace(RunEngine.initialize(_run_config(), _strategy_state({2.0: Decimal("10")})))
        state = replace(state, source_id=source.source_id)

        guard = threading.Timer(15.0, source.stop)
        guard.start()
        try:
            for processed, record in enumerate(source.records(), start=1):
                state, _ = RunEngine.advance(state, record, context_factory, record.timestamp)
                if processed >= 3:
                    source.stop()
                    break
        finally:
            guard.cancel()
            source.stop()

    assert state.processed == 3, "every streamed record went through the run step"
    assert state.pipeline.portfolio.positions[_asset_id()].quantity == Decimal("10")
    assert state.steps.to_tuple()[1].fills, "the streamed quote produced a real fill"
    assert state.pipeline.portfolio.cash.balances["USD"] < Decimal("1000000")


def test_a_trading_session_runs_a_stream_with_the_same_loop_it_runs_a_dataset_with() -> None:
    """`TradingSession.run` is unchanged and does not know what it is reading."""

    # The session reads to exhaustion, so the stream has to end by itself. The
    # venue closes after its last message and the source is allowed no
    # reconnects, which is a finite live feed -- and also the case where a
    # session must terminate rather than hang.
    script = StreamScript(
        drop_after=2,
        messages=[
            quote(_SYMBOL, 1, "100", "100", 1.0),
            quote(_SYMBOL, 2, "101", "101", 2.0),
        ],
    )
    with run_stream(script) as (url, _log, _s):
        source = _source(url, "session-feed", max_reconnects=0)
        guard = threading.Timer(15.0, source.stop)
        guard.start()
        try:
            state = TradingSession.run(
                _run_config(),
                source,
                _strategy_state({2.0: Decimal("5")}),
                context_factory,
            )
        finally:
            guard.cancel()
            source.stop()

    assert state.source_id == "session-feed", "the run records which stream it read"
    assert state.processed >= 2
    assert state.pipeline.portfolio.positions[_asset_id()].quantity == Decimal("5")


def test_a_run_that_demands_chronological_records_refuses_a_stream() -> None:
    """The existing ADR-0014 rule, applied to a live source. Not worked around."""

    with run_stream() as (url, _log, _s):
        source = _source(url)
        chronological = replace(_run_config(), ordering=OrderingGuarantee.CHRONOLOGICAL)
        with pytest.raises(MarketValidationError, match="declares UNORDERED"):
            TradingSession.run(chronological, source, _strategy_state({}), context_factory)

    assert not source.connected, "refused before the source was ever read"


def test_a_record_that_regresses_is_skipped_and_recorded_not_acted_on() -> None:
    """A venue reordering across a reconnect must not mark the portfolio backwards."""

    script = StreamScript(
        messages=[
            quote(_SYMBOL, 1, "100", "100", 10.0),
            quote(_SYMBOL, 2, "500", "500", 5.0),  # older than the one before it
            quote(_SYMBOL, 3, "101", "101", 11.0),
        ]
    )
    with run_stream(script) as (url, _log, _s):
        source = _source(url)
        state = RunEngine.initialize(_run_config(), _strategy_state({}))

        guard = threading.Timer(15.0, source.stop)
        guard.start()
        try:
            for seen, record in enumerate(source.records(), start=1):
                state, _ = RunEngine.advance(state, record, context_factory, record.timestamp)
                if seen >= 3:
                    source.stop()
                    break
        finally:
            guard.cancel()
            source.stop()

    skipped = state.skipped.to_tuple()
    assert len(skipped) == 1, "the regressing record was skipped, not applied"
    assert "before the last record processed" in skipped[0].reason
    assert state.processed == 2
    # The portfolio was never marked at the stale 500 price.
    assert state.pipeline.market_prices[_asset_id()] == Decimal("101")


def test_a_paper_stream_and_a_live_stream_differ_only_in_where_orders_execute() -> None:
    """Parity: the same records, the same path, one simulated fill and one working order."""

    messages = [
        quote(_SYMBOL, 1, "100", "100", 1.0),
        quote(_SYMBOL, 2, "101", "101", 2.0),
    ]
    results = {}
    for mode in (ExecutionMode.PAPER, ExecutionMode.LIVE):
        with run_stream(StreamScript(messages=list(messages))) as (url, _log, _s):
            source = _source(url, f"feed-{mode.name}")
            state = RunEngine.initialize(_run_config(mode), _strategy_state({2.0: Decimal("4")}))
            guard = threading.Timer(15.0, source.stop)
            guard.start()
            try:
                for seen, record in enumerate(source.records(), start=1):
                    state, _ = RunEngine.advance(state, record, context_factory, record.timestamp)
                    if seen >= 2:
                        source.stop()
                        break
            finally:
                guard.cancel()
                source.stop()
            results[mode] = state

    paper = results[ExecutionMode.PAPER]
    live = results[ExecutionMode.LIVE]

    assert paper.processed == live.processed == 2, "identical up to the venue"
    assert paper.pipeline.portfolio.positions[_asset_id()].quantity == Decimal("4")
    assert not paper.working_orders, "paper simulated the fill"
    assert live.working_orders, "live left the order for a venue to answer"
    assert _asset_id() not in live.pipeline.portfolio.positions, "no fill was invented"
