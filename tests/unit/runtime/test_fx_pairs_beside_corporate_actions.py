"""A corporate action beside a declared FX pair reads no contract terms into it (ADR-0067).

A split or a holding exchange checks every contract the book holds or works for
a deliverable the action changes. An FX deal's terms deliver no instrument --
the deal exchanges two currencies -- so an FX order working at the venue is
passed over. Until this was found, the check read an option's adjustments from
it and the action failed.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import time
from decimal import Decimal

from alphalab.core.enums import AssetType
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.terms import FxKind, FxTerms, FxValueDateRule
from alphalab.portfolio.corporate_actions import HoldingExchange, Split
from alphalab.runtime.calendars import VenueCalendars
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineState,
    ExecutionRouting,
)
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    registry_of,
    running_strategy_state,
    sized_quote,
)

STRATEGY_ID = str(uuid.uuid4())
_OPEN = dict.fromkeys(range(5), (SessionWindow(time(0, 0), time(23, 0)),))
CALENDARS = VenueCalendars(
    by_exchange={
        "XFXV": MarketCalendar("XFXV", "UTC", _OPEN),
        "TARGET": MarketCalendar("TARGET", "Europe/Berlin", _OPEN),
        "USNY": MarketCalendar("USNY", "America/New_York", _OPEN),
    }
)
PAIR = InstrumentRecord(
    "EURUSD",
    AssetType.FOREX,
    "XFXV",
    "USD",
    terms=FxTerms("EUR", "USD", FxKind.SPOT, FxValueDateRule(2, ("TARGET",), ("TARGET", "USNY"))),
)
SHARE = InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD")


def _working() -> ExecutionPipelineState:
    """An order in the pair, working at the venue."""

    base = pipeline_config(STRATEGY_ID)
    config = replace(
        base,
        budget=base.budget.in_currency("USD"),
        routing=ExecutionRouting.EXTERNAL,
        instruments=registry_of(PAIR, SHARE),
        calendars=CALENDARS,
        also_settles=frozenset({"EUR"}),
    )
    strategy = ScriptedStrategy(STRATEGY_ID, PAIR.asset_id, {2.0: Decimal("1000")})
    state = ExecutionPipeline.initialize(config, running_strategy_state(STRATEGY_ID, strategy), 1.0)
    quote = sized_quote(PAIR.asset_id, 2.0, Decimal("1.08"), Decimal("1.08"))
    state = ExecutionPipeline.process_quote(state, quote, context_factory).state
    assert state.oms.working_orders_for(PAIR.asset_id)
    return state


def test_a_split_passes_over_an_fx_order_working_at_the_venue() -> None:
    state = _working()
    after = ExecutionPipeline.apply_split(state, Split(SHARE.asset_id, Decimal("2")), 3.0)
    assert after.oms.working_orders_for(PAIR.asset_id) == state.oms.working_orders_for(
        PAIR.asset_id
    )


def test_a_holding_exchange_passes_over_it_too() -> None:
    state = _working()
    exchange = HoldingExchange(SHARE.asset_id, cash_per_unit=Decimal("10"))
    after = ExecutionPipeline.apply_exchange(state, exchange, 3.0)
    assert after.oms.working_orders_for(PAIR.asset_id) == state.oms.working_orders_for(
        PAIR.asset_id
    )
