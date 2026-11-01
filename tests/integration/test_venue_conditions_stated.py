"""A result states the venue conditions its registry declared (phase 5 execution-realism audit).

Bands, halts, reopening auctions and the published interest they uncross from,
a margin model and each instrument's margin, borrow, tick and settlement terms
shape what a simulated run does; each is the caller's declaration, not a venue's
verified behaviour. :attr:`ExecutionAssumptions.venue_conditions` says which a
run held, in a sentence each, beside its other assumptions -- and, being a
statement rather than a setting, moves no identity a fingerprint records.

The run is tests/integration/test_surface_feeds.py's book of written puts.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

from alphalab.instrument.controls import (
    AuctionInterest,
    AuctionTieBreak,
    AuctionUncross,
    BandAction,
    PriceBand,
    TradingControls,
    TradingHalt,
)
from alphalab.instrument.record import InstrumentRecord
from alphalab.instrument.registry import declare_trading_controls
from alphalab.runtime.assumptions import execution_assumptions
from tests.integration import test_surface_feeds as feeds
from tests.integration.harness import registry_of

D = Decimal
SHARE, PUT, T0 = feeds.SHARE, feeds.PUT, feeds.T0
NOT_VERIFIED = "declared by the caller, not verified against a venue"


def stated(config: Any) -> tuple[str, ...]:
    return execution_assumptions(config.pipeline, config.fill_policy).venue_conditions


def test_a_registry_declaring_no_condition_states_none() -> None:
    config = feeds.config(feeds.model())
    bare = InstrumentRecord("PLAIN", SHARE.asset_type, "XNYS", "USD")
    plain = replace(config, pipeline=replace(config.pipeline, instruments=registry_of(bare)))
    assert stated(plain) == ()
    assert stated(replace(config, pipeline=replace(config.pipeline, instruments=None))) == ()


def test_the_margin_model_and_each_instruments_terms_are_stated() -> None:
    conditions = stated(feeds.config(feeds.model()))
    assert conditions == (
        f"margin model ScenarioMargin {NOT_VERIFIED}",
        f"margin terms on 2 instruments {NOT_VERIFIED}",
    )


def test_bands_halts_auctions_and_published_interest_are_stated() -> None:
    controls = TradingControls(
        bands=(PriceBand(SHARE.asset_id, T0, None, D("80"), D("120"), BandAction.REFUSE),),
        halts=(
            TradingHalt(
                T0 + 10,
                T0 + 20,
                asset_id=SHARE.asset_id,
                auction=True,
                uncross=AuctionUncross(AuctionTieBreak.HIGHEST),
            ),
            TradingHalt(T0 + 70, T0 + 80, asset_id=SHARE.asset_id, auction=True),
            TradingHalt(T0 + 100, None, asset_id=PUT.asset_id),
        ),
        auction_books=(
            AuctionInterest(SHARE.asset_id, T0 + 5, bids=((D("100"), D("10")),)),
            AuctionInterest(SHARE.asset_id, T0 + 15, bids=((D("101"), D("10")),)),
        ),
    )
    config = feeds.config(feeds.model())
    registry = declare_trading_controls(config.pipeline.instruments, controls)
    conditions = stated(replace(config, pipeline=replace(config.pipeline, instruments=registry)))
    assert conditions[:3] == (
        f"1 price band {NOT_VERIFIED}",
        "3 trading halts, 2 reopening by auction (1 uncrossed from published interest, "
        f"1 at the first print) {NOT_VERIFIED}",
        f"published auction interest for 1 instrument (2 books) {NOT_VERIFIED}; no depth "
        "beyond it is inferred",
    )


def test_a_result_carries_them_and_no_identity_moves() -> None:
    result = feeds.run()
    assumptions = result.execution_assumptions
    assert assumptions.venue_conditions == stated(feeds.config(feeds.model()))
    assert not any("venue" in key for key in assumptions.settings())
