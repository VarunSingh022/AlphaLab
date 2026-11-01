"""Example 79 does what it prints: a bid into a reopening auction (phase 5).

The uncross is recomputed by hand from the example's declared book and the
strategy's own bid, and what the strategy was shown at each bar from the
declared publication instants, so the test fails if the engine and the
arithmetic part company, not if the prose changes (ledgers FUT-040 and EXE-014).
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from alphalab.backtesting import BacktestEngine, RunSchedule
from alphalab.instrument import declare_trading_controls

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "79_bid_into_a_reopening_auction.py"
D = Decimal


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_79", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_strategy_is_shown_each_publication_from_its_instant(example: ModuleType) -> None:
    strategy = example.AuctionBidder()
    example.run(strategy)
    shown = {round((t - example.T0) / example.MINUTE): s for t, s in strategy.seen.items()}
    books, indications = example.BOOKS, example.INDICATIONS
    assert shown[1] is None and shown[5] is None  # before the halt; once it is held
    assert (shown[2].book, shown[2].indication) == (books[0], indications[0])
    assert shown[3].book == books[0] and shown[3].withdrawn
    assert (shown[4].book, shown[4].indication) == (books[1], indications[2])
    assert shown[4].book_age == pytest.approx(0.6 * example.MINUTE)


def test_the_uncross_is_the_books_with_the_bid_and_never_the_indication(
    example: ModuleType,
) -> None:
    result = example.run()
    book = example.BOOKS[1]
    bid = (D("102"), D("100"))  # a tick above the indicated 101

    def matched(price: Decimal) -> tuple[Decimal, Decimal]:
        demand = sum((q for p, q in (*book.bids, bid) if p >= price), D("0"))
        supply = sum((q for p, q in book.offers if p <= price), D("0"))
        return min(demand, supply), demand - supply

    prices = sorted({p for p, _ in (*book.bids, *book.offers, bid)})
    best = max(prices, key=lambda p: (matched(p)[0], -abs(matched(p)[1]), p))
    held = result.state.execution.reopened[example.ACME.asset_id]
    assert (held.price, held.matched, held.imbalance) == (best, *matched(best))
    assert (best, *matched(best)) == (D("101"), D("3500"), D("600"))
    # Buyers at 102 are filled first; the published 3,000 and the bid fit in 3,500.
    (fill,) = result.fills
    assert (fill.quantity, fill.price) == (D("100"), D("101"))


def test_without_the_indications_the_uncross_is_the_same_and_nothing_is_bid(
    example: ModuleType,
) -> None:
    config = example.config()
    registry = config.pipeline.instruments
    unindicated = declare_trading_controls(registry, replace(registry.controls, indications=()))
    bare = replace(config, pipeline=replace(config.pipeline, instruments=unindicated))
    result = BacktestEngine.run(
        bare, example.data(), example.running(), example.CONTEXTS, schedule=RunSchedule.of()
    )
    with_them = example.run().state.execution.reopened[example.ACME.asset_id]
    without = result.state.execution.reopened[example.ACME.asset_id]
    assert result.fills == ()
    assert without.price == with_them.price
    assert not any("indications" in c for c in result.execution_assumptions.venue_conditions)


def test_the_result_states_the_venue_conditions_it_rested_on(example: ModuleType) -> None:
    conditions = example.run().execution_assumptions.venue_conditions
    assert len(conditions) == 3
    assert all("declared by the caller, not verified against a venue" in c for c in conditions)
    example.main()
