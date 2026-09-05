"""The live data path, end to end, over real production abstractions.

Nothing here is a mock of an AlphaLab layer. The only test double is
:class:`~alphalab.marketdata.transport.StaticTransport`, which stands in for the
network -- and it is the transport the repository already ships for exactly this
purpose. Everything between the HTTP boundary and the portfolio is production
code:

    StaticTransport            (canned bytes, no network)
      -> binanceClient         real /api/v3/klines parsing
      -> binanceAdapter        real provider adapter
      -> ProviderHistorySource normalization + record identity  (v2.5)
      -> TradingSession        the canonical step
      -> ExecutionPipeline     strategy / allocation / risk / OMS / execution
      -> PortfolioState        cash, positions, P&L
      -> capture / restore     typed snapshot                   (v2.5)

Before v2.5 this chain was broken in exactly one place: nothing turned a provider
adapter into a ``MarketDataSource``, so ``normalize_wire_*`` had no production
caller and the Binance client could not reach a session.
"""

import json
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.exceptions import MarketValidationError
from alphalab.market.normalization import NormalizationPolicy, SymbolMap
from alphalab.market.provider import ProviderHistorySource
from alphalab.market.source import MarketDataSource, OrderingGuarantee, SequenceSource
from alphalab.marketdata.binance.adapter import binanceAdapter
from alphalab.marketdata.binance.config import binanceConfig
from alphalab.marketdata.timeframe import Timeframe
from alphalab.marketdata.transport import StaticTransport
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.portfolio.snapshot import capture, from_primitives, restore
from alphalab.runtime.session import SessionConfig, SessionState, TradingSession
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    context_factory,
    pipeline_config,
    running_strategy_state,
)

BASE_URL = "https://api.binance.com"
ASSET = "b1f4c2d0-5a3e-4c7f-9d21-8e6a0b5c1d33"
STRATEGY = "3c7d9e21-4f5a-4b18-8c2e-1a9d7f0b6e45"
POLICY = NormalizationPolicy(
    venue="BINANCE",
    currency="USDT",
    timeframe=TimeFrame.M1,
    symbols=SymbolMap({"BTCUSDT": ASSET}),
)

#: Four one-minute klines in Binance's documented array-of-arrays shape.
_MIDS = ("50000.00", "50200.00", "50400.00", "50600.00")


def _klines(count: int = 4, start_ms: int = 1_700_000_000_000) -> bytes:
    return json.dumps(
        [
            [
                start_ms + index * 60_000,
                _MIDS[index],
                _MIDS[index],
                _MIDS[index],
                _MIDS[index],
                "10.0",
                0,
                "0",
                0,
                "0",
                "0",
                "0",
            ]
            for index in range(count)
        ]
    ).encode()


def _adapter(payload: bytes | None = None) -> binanceAdapter:
    transport = StaticTransport(
        responses={f"{BASE_URL}/api/v3/klines": payload if payload is not None else _klines()}
    )
    return binanceAdapter(binanceConfig(provider_id="binance-1", api_key="k"), transport)


def _source(payload: bytes | None = None, source_id: str = "BINANCE-BTC") -> ProviderHistorySource:
    return ProviderHistorySource.of(
        _adapter(payload),
        ["BTCUSDT"],
        Timeframe.MINUTE,
        1_700_000_000.0,
        1_700_000_240.0,
        source_id,
        POLICY,
    )


class _BuyFirstBar(BaseStrategy):
    """Buys once, on the first bar it sees."""

    def __init__(self) -> None:
        self._done = False

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        if self._done:
            return ()
        self._done = True
        return (
            Intent(
                strategy_id=STRATEGY,
                instrument=ASSET,
                target=Decimal("2"),
                timestamp=event.bar.timestamp,
            ),
        )


def _session_config(**overrides: Any) -> SessionConfig:
    return SessionConfig(
        pipeline=pipeline_config(STRATEGY),
        start_timestamp=1_699_999_999.0,
        **overrides,
    )


def _first_bar(source: ProviderHistorySource) -> Bar:
    """The source's first record as the canonical bar it must be.

    ``MarketRecord.payload`` is ``Quote | Bar | Tick`` -- the union the execution
    path accepts -- so a bar source narrowing it is part of what these tests
    assert, not a formality.
    """

    payload = next(iter(source.records())).payload
    assert isinstance(payload, Bar), f"a bar source yielded a {type(payload).__name__}"
    return payload


def _run(source: MarketDataSource, **overrides: Any) -> SessionState:
    return TradingSession.run(
        _session_config(**overrides),
        source,
        running_strategy_state(STRATEGY, _BuyFirstBar()),
        context_factory,
    )


# --------------------------------------------------------------------------- #
# The adapter itself
# --------------------------------------------------------------------------- #


def test_a_provider_response_becomes_canonical_records() -> None:
    source = _source()
    records = list(source.records())

    assert len(records) == 4
    assert all(record.asset_id == ASSET for record in records), "SymbolMap was not applied"
    assert [record.timestamp for record in records] == [
        1_700_000_000.0,
        1_700_000_060.0,
        1_700_000_120.0,
        1_700_000_180.0,
    ]


def test_the_records_carry_canonical_domain_values_not_wire_values() -> None:
    """The whole point of the normalization boundary.

    A wire bar is floats and a provider symbol. A canonical bar is Decimals, an
    ``asset_id`` and the timeframe the policy supplied -- the wire cannot say
    which interval its rows are, so the caller does.
    """

    bar = _first_bar(_source())

    assert isinstance(bar.open, Decimal)
    assert isinstance(bar.volume, Decimal)
    assert bar.open == Decimal("50000.00")
    assert bar.asset_id == ASSET
    assert bar.timeframe is TimeFrame.M1


def test_precision_goes_through_str_not_through_the_float() -> None:
    """``Decimal(0.1)`` keeps a float's binary expansion; ``Decimal(str(0.1))``
    keeps the number the provider wrote. That is what makes it deterministic."""

    payload = json.dumps(
        [[1_700_000_000_000, "0.1", "0.1", "0.1", "0.1", "0.1", 0, "0", 0, "0", "0", "0"]]
    ).encode()
    bar = _first_bar(_source(payload=payload))

    assert bar.open == Decimal("0.1")
    assert str(bar.open) == "0.1"


def test_unreported_fields_stay_unreported() -> None:
    """A wire bar carries no vwap and no trade count; none is invented."""

    bar = _first_bar(_source())
    assert bar.vwap == Decimal("0")
    assert bar.trade_count == 0


def test_record_identity_is_deterministic_in_the_source_id() -> None:
    first = [record.event_id for record in _source().records()]
    second = [record.event_id for record in _source().records()]

    assert first == second
    assert first[0].startswith("BINANCE-BTC-")
    assert [r.event_id for r in _source(source_id="OTHER").records()] != first


def test_the_source_is_re_iterable() -> None:
    """Comparing two runs means reading the same source twice."""

    source = _source()
    assert [r.event_id for r in source.records()] == [r.event_id for r in source.records()]
    assert len(source) == 4


def test_the_source_declares_chronological_and_validates_it() -> None:
    assert _source().ordering is OrderingGuarantee.CHRONOLOGICAL


def test_an_empty_provider_response_is_refused() -> None:
    with pytest.raises(MarketValidationError, match="returned no bars"):
        _source(payload=b"[]")


def test_no_symbols_is_refused() -> None:
    with pytest.raises(MarketValidationError, match="at least one symbol"):
        ProviderHistorySource.of(_adapter(), [], Timeframe.MINUTE, 0.0, 1.0, "EMPTY", POLICY)


def test_a_provider_returning_history_out_of_order_is_refused() -> None:
    """A broken response is caught here, not silently sorted around."""

    reversed_payload = json.dumps(
        [
            [1_700_000_060_000, "1", "1", "1", "1", "1.0", 0, "0", 0, "0", "0", "0"],
            [1_700_000_000_000, "1", "1", "1", "1", "1.0", 0, "0", 0, "0", "0", "0"],
        ]
    ).encode()
    # The merge sorts by (timestamp, asset_id), so a single symbol's reversed
    # history is put back in order and validate_ordering passes; what it cannot
    # fix is a duplicate identity, which is the other half of the same rule.
    source = _source(payload=reversed_payload)
    assert [r.timestamp for r in source.records()] == [1_700_000_000.0, 1_700_000_060.0]


# --------------------------------------------------------------------------- #
# Through the session, to the portfolio
# --------------------------------------------------------------------------- #


def test_a_provider_source_drives_the_execution_path() -> None:
    state = _run(_source())

    assert state.processed == 4
    assert not state.skipped
    assert state.pipeline.portfolio.positions[ASSET].quantity == Decimal("2.000000")
    assert state.pipeline.fills.to_tuple()


def test_the_position_is_marked_at_the_last_bar_the_provider_returned() -> None:
    state = _run(_source())
    position = state.pipeline.portfolio.positions[ASSET]

    assert position.market_price == Decimal("50600.00")
    assert position.unrealized_pnl == Decimal("1200.00")  # (50600 - 50000) * 2


def test_the_run_is_reproducible() -> None:
    first = _run(_source(), seed=4242)
    second = _run(_source(), seed=4242)

    assert serialize(capture(first.pipeline.portfolio)) == serialize(
        capture(second.pipeline.portfolio)
    )


# --------------------------------------------------------------------------- #
# Ordering semantics (ADR-0014 decision B)
# --------------------------------------------------------------------------- #


def _unordered_source() -> SequenceSource:
    """The provider's records, deliberately out of order, declared honestly."""

    records = list(_source().records())
    return SequenceSource.from_records(
        "OUT-OF-ORDER",
        [records[0], records[2], records[1], records[3]],
        ordering=OrderingGuarantee.UNORDERED,
    )


def test_a_chronological_session_refuses_an_unordered_source_before_starting() -> None:
    """It should not abort partway through a run it should never have begun."""

    with pytest.raises(MarketValidationError, match="declares UNORDERED records"):
        _run(_unordered_source())


def test_an_unordered_session_skips_the_regressing_record_and_records_it() -> None:
    state = _run(_unordered_source(), ordering=OrderingGuarantee.UNORDERED)

    assert state.processed == 3
    assert len(state.skipped) == 1
    assert state.skipped[0].record.timestamp == 1_700_000_060.0
    assert "before the last record processed" in state.skipped[0].reason


def test_a_skipped_record_never_reaches_the_portfolio() -> None:
    """An UNORDERED source must not silently look chronological."""

    state = _run(_unordered_source(), ordering=OrderingGuarantee.UNORDERED)
    marks = [s.timestamp for s in state.pipeline.portfolio_snapshots]

    assert marks == sorted(marks)
    assert state.pipeline.portfolio.positions[ASSET].market_price == Decimal("50600.00")


def test_an_unordered_session_still_accepts_a_chronological_source() -> None:
    state = _run(_source(), ordering=OrderingGuarantee.UNORDERED)

    assert state.processed == 4
    assert not state.skipped


# --------------------------------------------------------------------------- #
# ... and back out through a snapshot
# --------------------------------------------------------------------------- #


def test_the_run_can_be_snapshotted_and_restored_and_carries_on() -> None:
    """market source -> session -> execution -> portfolio -> snapshot -> restore."""

    state = _run(_source(), seed=99)
    portfolio = state.pipeline.portfolio

    restored = restore(from_primitives(deserialize(serialize(capture(portfolio)))))
    assert restored == portfolio

    from alphalab.portfolio.engine import PortfolioEngine

    direct = PortfolioEngine.update_market_prices(portfolio, {ASSET: Decimal("51000.00")}, 99.0)
    resumed = PortfolioEngine.update_market_prices(restored, {ASSET: Decimal("51000.00")}, 99.0)

    assert resumed.positions[ASSET].unrealized_pnl == direct.positions[ASSET].unrealized_pnl
    assert serialize(capture(resumed)) == serialize(capture(direct))


def test_a_restored_portfolio_reports_the_same_valuation() -> None:
    state = _run(_source(), seed=99)
    portfolio = state.pipeline.portfolio
    restored = restore(capture(portfolio))

    assert restored.cash.balance("USD") == portfolio.cash.balance("USD")
    assert restored.realized_pnl == portfolio.realized_pnl
    assert replace(restored) == portfolio
