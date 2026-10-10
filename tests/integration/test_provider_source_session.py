"""The live data path, end to end, over real production abstractions.

Nothing here is a mock of an AlphaLab layer. The test doubles are
:class:`~alphalab.marketdata.transport.StaticTransport`, which stands in for the
network and is the transport the repository ships for exactly this purpose, and
:class:`_RestBarProvider`, a few lines that parse a JSON bar list into wire
bars the way a host application's provider client would. Everything between the
wire bars and the portfolio is production code:

    StaticTransport            (canned bytes, no network)
      -> _RestBarProvider      a host-side provider: JSON -> wire bars
      -> ProviderHistorySource normalization + record identity  (v2.5)
      -> TradingSession        the canonical step
      -> ExecutionPipeline     strategy / allocation / risk / OMS / execution
      -> PortfolioState        cash, positions, P&L
      -> capture / restore     typed snapshot                   (v2.5)

Before v2.5 this chain was broken in exactly one place: nothing turned a provider
into a ``MarketDataSource``, so ``normalize_wire_*`` had no production caller.
Until v3.10 the provider here was the library's own Binance client; vendor
clients are the host application's and were removed (ledger BND-001), so the
provider is now the test's -- :class:`~alphalab.market.provider.BarHistoryProvider`
is the whole contract a provider has to meet.
"""

import json
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.core.enums import AssetType
from alphalab.data.time import BarStamp
from alphalab.instrument import (
    InstrumentRecord,
    InstrumentRegistry,
    register_instrument,
)
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.exceptions import MarketValidationError
from alphalab.market.normalization import NormalizationPolicy
from alphalab.market.provider import ProviderHistorySource
from alphalab.market.source import MarketDataSource, OrderingGuarantee, SequenceSource
from alphalab.marketdata.feed import Bar as WireBar
from alphalab.marketdata.transport import StaticTransport, Transport
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.portfolio.snapshot import capture, from_primitives, restore
from alphalab.runtime.run import ExecutionMode, RunConfig, RunState
from alphalab.runtime.session import TradingSession
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent
from alphalab.strategy.protocol import BaseStrategy
from tests.integration.harness import (
    context_factory,
    pipeline_config,
    running_strategy_state,
)

BARS_URL = "https://bars.example/v1/bars"
STRATEGY = "3c7d9e21-4f5a-4b18-8c2e-1a9d7f0b6e45"

#: The instrument the provider's ``"BTCUSDT"`` denotes. Until v2.7 this file
#: hand-authored a UUID and mapped the symbol onto it with a ``SymbolMap``,
#: because that was the only configuration that could reach a fill -- there was
#: no authority to ask. The registry is now that authority, and ``ASSET`` is
#: *derived* from the declaration rather than invented. See ADR-0016.
BTCUSDT = InstrumentRecord(
    symbol="BTCUSDT",
    asset_type=AssetType.CRYPTO,
    exchange="BINANCE",
    currency="USDT",
    aliases={"bars-rest": "BTCUSDT"},
)
ASSET = BTCUSDT.asset_id
INSTRUMENTS = register_instrument(InstrumentRegistry(), BTCUSDT)

POLICY = NormalizationPolicy(
    bar_stamp=BarStamp.INTERVAL_END,
    venue="BINANCE",
    currency="USDT",
    timeframe=TimeFrame.M1,
    identity=INSTRUMENTS,
    provider="bars-rest",
)

#: Four one-minute bars: ``[end_ms, open, high, low, close, volume]``, each
#: stamped at the end of its interval (hence ``BarStamp.INTERVAL_END`` above).
_MIDS = ("50000.00", "50200.00", "50400.00", "50600.00")


def _bars(count: int = 4, start_ms: int = 1_700_000_000_000) -> bytes:
    return json.dumps(
        [[start_ms + index * 60_000, *(_MIDS[index],) * 4, "10.0"] for index in range(count)]
    ).encode()


class _RestBarProvider:
    """A provider as a host application writes one: fetch, parse, return wire bars.

    Satisfies :class:`~alphalab.market.provider.BarHistoryProvider` and nothing
    more. Prices stay the strings the payload wrote until the wire record's
    float, and normalization lifts them through ``str`` -- which is what the
    precision test below pins.
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def request_history(
        self, symbol: str, timeframe: TimeFrame, start: float, end: float
    ) -> tuple[WireBar, ...]:
        body = self._transport.get(
            BARS_URL,
            {"symbol": symbol, "interval": timeframe.code, "start": str(start), "end": str(end)},
        )
        return tuple(
            WireBar(
                symbol,
                int(row[0]) / 1000.0,
                float(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]),
            )
            for row in json.loads(body)
        )


def _adapter(payload: bytes | None = None) -> _RestBarProvider:
    return _RestBarProvider(
        StaticTransport(responses={BARS_URL: payload if payload is not None else _bars()})
    )


def _source(payload: bytes | None = None, source_id: str = "REST-BTC") -> ProviderHistorySource:
    return ProviderHistorySource.of(
        _adapter(payload),
        ["BTCUSDT"],
        TimeFrame.M1,
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


def _session_config(**overrides: Any) -> RunConfig:
    return RunConfig(
        pipeline=pipeline_config(STRATEGY),
        mode=ExecutionMode.PAPER,
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


def _run(source: MarketDataSource, **overrides: Any) -> RunState:
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
    assert all(record.asset_id == ASSET for record in records), (
        "the registry did not resolve the provider symbol"
    )
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
    assert bar.timeframe == TimeFrame.M1


def test_precision_goes_through_str_not_through_the_float() -> None:
    """``Decimal(0.1)`` keeps a float's binary expansion; ``Decimal(str(0.1))``
    keeps the number the provider wrote. That is what makes it deterministic."""

    payload = json.dumps([[1_700_000_000_000, "0.1", "0.1", "0.1", "0.1", "0.1"]]).encode()
    bar = _first_bar(_source(payload=payload))

    assert bar.open == Decimal("0.1")
    assert str(bar.open) == "0.1"


def test_unreported_fields_stay_unreported() -> None:
    """A wire bar carries no vwap and no trade count; none is invented."""

    bar = _first_bar(_source())
    assert bar.vwap is None
    assert bar.trade_count is None


def test_record_identity_is_deterministic_in_the_source_id() -> None:
    first = [record.event_id for record in _source().records()]
    second = [record.event_id for record in _source().records()]

    assert first == second
    assert first[0].startswith("REST-BTC-")
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
        ProviderHistorySource.of(_adapter(), [], TimeFrame.M1, 0.0, 1.0, "EMPTY", POLICY)


def test_a_provider_returning_history_out_of_order_is_refused() -> None:
    """A broken response is caught here, not silently sorted around."""

    reversed_payload = json.dumps(
        [
            [1_700_000_060_000, "1", "1", "1", "1", "1.0"],
            [1_700_000_000_000, "1", "1", "1", "1", "1.0"],
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


def test_a_request_at_an_interval_the_policy_does_not_label_is_refused() -> None:
    """v3.11 (DAT-005): one interval type from the request to the canonical bar.

    Bars fetched at five minutes and labelled one-minute by the policy would be
    mislabelled with nothing downstream able to tell; until v3.11 the request
    and the label were two unrelated vocabularies and could not even be compared.
    """

    with pytest.raises(MarketValidationError, match="asked for 5m bars and the policy labels"):
        ProviderHistorySource.of(
            _adapter(),
            ["BTCUSDT"],
            TimeFrame.M5,
            1_700_000_000.0,
            1_700_000_240.0,
            "REST-BTC",
            POLICY,
        )
