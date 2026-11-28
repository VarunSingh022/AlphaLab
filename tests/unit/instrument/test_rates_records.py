"""A swap's and an FRA's record: their declared terms, and nothing assumed (v4.3, ADR-0065 s. 4).

* **A rates type books by its terms.** A ``SWAP`` record without
  :class:`~alphalab.rates.terms.SwapTerms`, or an ``FRA`` without
  :class:`~alphalab.rates.terms.FraTerms`, is refused, and so are terms on
  the other type, in another currency, with no economics, with economics a
  swap's value cannot settle by, or refusing the negative price a swap is
  worth whenever rates move against its holder.
* **Terms are outside the identity key**: the same swap with another fixed
  rate keeps its ``asset_id``.
* **The registry persists them**, and reads them back equal.
* **A corporate action passes over a swap.** A split and a holding exchange
  check every contract the book works for a deliverable they change; a swap
  delivers none, and is passed over.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import date, time
from decimal import Decimal

import pytest

from alphalab.conventions import BusinessDayConvention, DayCount, Tenor
from alphalab.conventions.economics import InstrumentEconomics, SettlementModel
from alphalab.core.enums import AssetType
from alphalab.data.calendar import MarketCalendar, SessionWindow
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import InstrumentRegistry, register_instruments
from alphalab.instrument.snapshot import capture, from_primitives, restore
from alphalab.instrument.terms import terms_from_primitives, terms_to_primitives
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.portfolio.corporate_actions import HoldingExchange, Split
from alphalab.rates import FixedLeg, FloatingLeg, FraTerms, LegSchedule, SwapTerms
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

D = Decimal
LEG = LegSchedule(Tenor.parse("1Y"), BusinessDayConvention.MODIFIED_FOLLOWING, ("USNY",), False)
QUARTERLY = LegSchedule(
    Tenor.parse("3M"), BusinessDayConvention.MODIFIED_FOLLOWING, ("USNY",), False
)


def swap_terms(rate: str = "0.0418") -> SwapTerms:
    return SwapTerms(
        "USD",
        date(2025, 1, 17),
        Tenor.parse("5Y"),
        FixedLeg(D("1"), D(rate), DayCount.THIRTY_360_US, LEG),
        FloatingLeg(D("1"), "USD-SOFR", D("0"), DayCount.ACT_360, QUARTERLY, 2, "TEST"),
    )


FRA = FraTerms(
    "USD",
    D("1"),
    D("0.04"),
    date(2025, 7, 17),
    date(2025, 10, 17),
    DayCount.ACT_360,
    "USD-SOFR",
    2,
    ("USNY",),
    "TEST",
)
CLEARED = InstrumentEconomics(
    D("1"), SettlementModel.FUTURES_VARIATION, allows_negative_prices=True
)


def swap(
    terms: SwapTerms | None = None, economics: InstrumentEconomics | None = CLEARED
) -> InstrumentRecord:
    return InstrumentRecord(
        "USD-5Y-SWAP",
        AssetType.SWAP,
        "XOTC",
        "USD",
        economics=economics,
        terms=terms or swap_terms(),
    )


def test_a_rates_record_declares_its_terms_and_economics() -> None:
    assert swap().terms == swap_terms()
    paid = InstrumentEconomics(D("1"), SettlementModel.CASH_EQUITY, allows_negative_prices=True)
    assert swap(economics=paid).economics == paid
    fra = InstrumentRecord("USD-3X6", AssetType.FRA, "XOTC", "USD", economics=CLEARED, terms=FRA)
    assert fra.terms == FRA
    refusals: list[tuple[Callable[[], object], str]] = [
        (
            lambda: InstrumentRecord("S", AssetType.SWAP, "XOTC", "USD", economics=CLEARED),
            "declares none",
        ),
        (
            lambda: InstrumentRecord(
                "F", AssetType.FRA, "XOTC", "USD", economics=CLEARED, terms=swap_terms()
            ),
            "a swap's terms",
        ),
        (
            lambda: InstrumentRecord(
                "S", AssetType.SWAP, "XOTC", "EUR", economics=CLEARED, terms=swap_terms()
            ),
            "terms are in USD",
        ),
        (lambda: swap(economics=None), "no economics"),
        (
            lambda: swap(
                economics=InstrumentEconomics(
                    D("1"), SettlementModel.OPTION_PREMIUM, allows_negative_prices=True
                )
            ),
            "option_premium",
        ),
        (
            lambda: swap(economics=InstrumentEconomics(D("1"), SettlementModel.FUTURES_VARIATION)),
            "allows_negative_prices",
        ),
    ]
    for build, message in refusals:
        with pytest.raises(InstrumentInputError, match=message):
            build()


def test_terms_are_outside_the_identity_key() -> None:
    assert swap(swap_terms("0.05")).asset_id == swap().asset_id


def test_the_registry_persists_rates_terms() -> None:
    fra = InstrumentRecord("USD-3X6", AssetType.FRA, "XOTC", "USD", economics=CLEARED, terms=FRA)
    registry = register_instruments(InstrumentRegistry(), (swap(), fra))
    read = restore(from_primitives(deserialize(serialize(capture(registry)))))
    assert read == registry
    assert read.record_for(swap().asset_id) == swap()
    assert read.record_for(fra.asset_id) == fra
    assert terms_from_primitives(terms_to_primitives(FRA)) == FRA
    broken = terms_to_primitives(swap_terms())
    assert broken is not None
    broken["fixed"]["schedule"]["convention"] = "NEAREST"
    with pytest.raises(InstrumentInputError, match="not a swap's or an FRA's terms"):
        terms_from_primitives(broken)


STRATEGY_ID = str(uuid.uuid4())
_OPEN = dict.fromkeys(range(5), (SessionWindow(time(0, 0), time(23, 0)),))
SHARE = InstrumentRecord("ACME", AssetType.EQUITY, "XNAS", "USD")


def _working() -> ExecutionPipelineState:
    """An order in the swap, working at the venue."""

    record = swap()
    base = pipeline_config(STRATEGY_ID)
    config = replace(
        base,
        routing=ExecutionRouting.EXTERNAL,
        instruments=registry_of(record, SHARE),
        calendars=VenueCalendars(by_exchange={"XOTC": MarketCalendar("XOTC", "UTC", _OPEN)}),
    )
    strategy = ScriptedStrategy(STRATEGY_ID, record.asset_id, {2.0: D("50000")})
    state = ExecutionPipeline.initialize(config, running_strategy_state(STRATEGY_ID, strategy), 1.0)
    quote = sized_quote(record.asset_id, 2.0, D("0.001"), D("0.001"))
    state = ExecutionPipeline.process_quote(state, quote, context_factory).state
    assert state.oms.working_orders_for(record.asset_id)
    return state


def test_a_split_and_an_exchange_pass_over_a_swap_working_at_the_venue() -> None:
    state = _working()
    asset = swap().asset_id
    split = ExecutionPipeline.apply_split(state, Split(SHARE.asset_id, D("2")), 3.0)
    assert split.oms.working_orders_for(asset) == state.oms.working_orders_for(asset)
    exchange = ExecutionPipeline.apply_exchange(
        state, HoldingExchange(SHARE.asset_id, cash_per_unit=D("10")), 3.0
    )
    assert exchange.oms.working_orders_for(asset) == state.oms.working_orders_for(asset)
