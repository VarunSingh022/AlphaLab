"""Three things are called a venue, and they are not one thing.

The v2.8 archaeology proposed deriving a canonical record's ``venue`` from
``InstrumentRecord.exchange``, on the evidence that a registry holding
``SAP/XETR/EUR`` produced quotes stamped ``venue="XNAS"``. Reading the code
refuted it. There are three concepts, not two:

1. **Listing exchange** -- ``InstrumentRecord.exchange``. Identity-bearing: it is
   one of the four fields ``asset_id`` is derived from, so it cannot change
   without re-identifying the instrument.
2. **Market-data attribution** -- ``Quote.venue`` / ``Tick.venue``, supplied by
   ``NormalizationPolicy`` because no wire record carries it. ``Bar`` and
   ``OrderBookSnapshot`` have no such field at all -- and ``Bar`` is the only
   record type ``ProviderHistorySource`` produces, so the one production path
   from a provider to a fill carries no venue whatsoever.
3. **Execution venue** -- ``ExecutionPipelineConfig.venue`` (``"SIM"``) and
   ``RoutingConfig.venue`` (``"LIVE"``) reaching ``ExecutionReport.venue``. The
   only one of the three anything reads.

Merging them would make ``ExecutionReport.venue`` read ``"XNAS"`` for an order
that executed against the simulator, which is false. The repository's own tests
already use ``venue`` for a MIC (``"XNAS"``), for a trading venue
(``"BINANCE"``), and ``alphalab.feed.normalization`` sets it to the provider's
name -- which is why this file exists rather than a change.

ADR-0019 keeps them apart. These assertions are what a future "unification"
would have to break first.
"""

import inspect
from dataclasses import fields
from decimal import Decimal
from uuid import uuid4

from alphalab.core.enums import AssetType
from alphalab.data.feed import Quote as WireQuote
from alphalab.instrument import InstrumentRecord, InstrumentRegistry, register_instrument
from alphalab.market import bar as market_bar
from alphalab.market import normalization as market_normalization
from alphalab.market import quote as market_quote
from alphalab.market import snapshot as market_snapshot
from alphalab.market import tick as market_tick
from alphalab.market.normalization import NormalizationPolicy, normalize_wire_quote
from alphalab.runtime.execution_pipeline import ExecutionPipeline
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    running_strategy_state,
)


def _names(cls: type) -> set[str]:
    return {field.name for field in fields(cls)}


def _running(strategy_id: str, asset_id: str, plan) -> StrategyRuntimeState:  # type: ignore[no-untyped-def]
    return running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, plan))


# --------------------------------------------------------------------------- #
# Three fields, three owners
# --------------------------------------------------------------------------- #


def test_each_venue_concept_lives_in_its_own_layer() -> None:
    from alphalab.execution.fill import OrderInstruction
    from alphalab.execution.report import ExecutionReport
    from alphalab.instrument.record import InstrumentRecord as Record

    assert Record.__module__ == "alphalab.instrument.record"
    assert "exchange" in _names(Record)

    assert market_quote.Quote.__module__ == "alphalab.market.quote"
    assert market_tick.Tick.__module__ == "alphalab.market.tick"
    assert "venue" in _names(market_quote.Quote)
    assert "venue" in _names(market_tick.Tick)

    assert ExecutionReport.__module__ == "alphalab.execution.report"
    assert OrderInstruction.__module__ == "alphalab.execution.fill"
    assert "venue" in _names(ExecutionReport)
    assert "venue" in _names(OrderInstruction)


def test_a_canonical_record_never_carries_a_listing_exchange() -> None:
    """No record type has both, so neither can be quietly derived from the other."""

    for record in (market_quote.Quote, market_tick.Tick, market_bar.Bar):
        assert "exchange" not in _names(record)


def test_the_bar_and_the_book_carry_no_venue_at_all() -> None:
    """``Bar`` is the only record ``ProviderHistorySource`` produces.

    The one production path from a provider to a fill therefore attributes
    nothing. Adding a venue here is a decision, not a tidy-up.
    """

    assert "venue" not in _names(market_bar.Bar)
    assert "currency" not in _names(market_bar.Bar)
    assert "venue" not in _names(market_snapshot.OrderBookSnapshot)


# --------------------------------------------------------------------------- #
# The boundary does not consult the instrument
# --------------------------------------------------------------------------- #


def test_normalization_reads_the_policy_for_venue_and_never_the_instrument() -> None:
    source = inspect.getsource(market_normalization)

    assert "venue=policy.venue" in source
    assert ".exchange" not in source, "venue must not be derived from the listing exchange"


# --------------------------------------------------------------------------- #
# They diverge in one run, while identity does not
# --------------------------------------------------------------------------- #


def test_all_three_differ_in_one_run_while_the_identity_is_shared() -> None:
    """A registry, a feed and a simulator can each name a different venue.

    They are answering different questions, so their disagreement is not an
    inconsistency. The ``asset_id`` is shared, because that is the one thing
    they do agree about.
    """

    sap = InstrumentRecord("SAP", AssetType.EQUITY, "XETR", "EUR", aliases={"acme": "SAP"})
    registry = register_instrument(InstrumentRegistry(), sap)
    policy = NormalizationPolicy(
        venue="BINANCE", currency="EUR", identity=registry, provider="acme"
    )

    quote = normalize_wire_quote(WireQuote("SAP", 2.0, 10.0, 10.0, 1.0, 1.0), policy)

    strategy_id = str(uuid4())
    config = pipeline_config(strategy_id)
    state = ExecutionPipeline.initialize(
        config, _running(strategy_id, sap.asset_id, {2.0: Decimal("1")}), 1.0
    )
    result = ExecutionPipeline.process_quote(state, quote, context_factory)
    report = result.execution_reports[0]

    assert sap.exchange == "XETR", "listing exchange -- part of the identity"
    assert quote.venue == "BINANCE", "market-data attribution -- from the policy"
    assert report.venue == config.venue == "SIM", "execution venue -- where it traded"
    assert len({sap.exchange, quote.venue, report.venue}) == 3

    # One identity, shared by all three, and derived by the registry alone.
    assert quote.asset_id == sap.asset_id
    assert report.asset_id == sap.asset_id
    assert result.fills[0].asset_id == sap.asset_id


def test_the_listing_exchange_is_part_of_the_identity_and_the_venues_are_not() -> None:
    """Why they cannot be merged: one changes ``asset_id`` and two do not."""

    frankfurt = InstrumentRecord("SAP", AssetType.EQUITY, "XETR", "EUR", aliases={"acme": "SAP"})
    london = InstrumentRecord("SAP", AssetType.EQUITY, "XLON", "EUR")
    registry = register_instrument(InstrumentRegistry(), frankfurt)
    wire = WireQuote("SAP", 2.0, 10.0, 10.0, 1.0, 1.0)

    # The listing exchange is an identity input: change it, and it is a
    # different instrument.
    assert frankfurt.asset_id != london.asset_id

    # The attributed venue is not: two feeds naming the same instrument
    # differently still resolve to one identity.
    from_binance = normalize_wire_quote(
        wire,
        NormalizationPolicy(venue="BINANCE", currency="EUR", identity=registry, provider="acme"),
    )
    from_xetr = normalize_wire_quote(
        wire,
        NormalizationPolicy(venue="XETR", currency="EUR", identity=registry, provider="acme"),
    )

    assert from_binance.venue != from_xetr.venue
    assert from_binance.asset_id == from_xetr.asset_id, "attribution is not identity"
