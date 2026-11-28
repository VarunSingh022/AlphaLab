"""Plan scenario S-05: FX carry with forwards, end to end (ADR-0067, WS-27).

``examples/84_carry_a_book_of_fx_forwards.py`` holds a million Australian and a
million New Zealand dollars a month forward through 2025, rolling each forward
as a swap; its book is ``examples/_fx_carry_book.py``'s. Held here, each figure
computed by the test rather than read from the example's own helpers:

* **Currencies, not pairs.** Every deal is booked as the two currencies it
  exchanges: 46 deals, 92 legs, no position.
* **Value dates follow both calendars.** Each deal's value date, counted again
  here from its trade day by its pair's rule, is the one the run booked. By
  hand: the New Zealand forward struck on 2 January is valued 7 February,
  Waitangi Day moving it from the 6th; the Australian one struck on 5 May is
  valued 10 June, the King's Birthday moving it from the 9th; and the
  Australian one struck on 9 October is valued 14 November, Columbus Day in
  New York having moved its spot.
* **Each roll settles with the forward it closes.** Eleven a pair, each near
  leg valued on the maturing forward's value date. On that day the currency
  arrives and leaves, and the dollars the roll gains arrive: spot less the
  forward's price, less two commissions.
* **Cash in each currency equals the tally of the fills**, commission
  included, and the ledger implies the pending index the run kept.
* **What the book earned adds up.** The forwards' discount and spot's moves,
  less commission, are the dollars gained, valued at the supplied closing
  rates.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from alphalab.conventions.business_days import forward_value_date, spot_value_date
from alphalab.portfolio.engine import PortfolioEngine
from alphalab.portfolio.settlement import SETTLEMENT_CASH, VALUE_DATE, pending_from
from alphalab.portfolio.types import TransactionType

ROOT = Path(__file__).resolve().parents[2]
DEFINITION = ROOT / "examples" / "_fx_carry_book.py"
D = Decimal


@pytest.fixture(scope="module")
def book() -> ModuleType:
    """The book's module, loaded under the name the example imports it by."""

    name = "_fx_carry_book"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, DEFINITION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ran(book: ModuleType) -> tuple[Any, Any]:
    result, strategy = book.run()
    return result, strategy


def _legs(result: Any) -> list[Any]:
    return [
        t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.FX_LEG
    ]


def _pair_of(book: ModuleType, asset_id: str) -> Any:
    return next(p for p in book.PAIRS if asset_id in (p.spot.asset_id, p.forward.asset_id))


def test_every_deal_is_booked_as_two_currencies_and_no_position(ran: tuple[Any, Any]) -> None:
    result, strategy = ran
    assert len(result.fills) == 46 and len(_legs(result)) == 92
    assert result.state.portfolio.positions == {}
    assert not result.state.strategy.events  # nothing refused, nothing rejected
    assert len(strategy.rolls) == 22


def test_value_dates_follow_both_calendars(book: ModuleType, ran: tuple[Any, Any]) -> None:
    result, _ = ran
    booked: dict[tuple[str, float], str] = {}
    for leg in _legs(result):
        booked.setdefault((leg.asset_id, leg.timestamp), leg.metadata[VALUE_DATE])
    for fill in result.fills:
        pair = _pair_of(book, fill.asset_id)
        day = datetime.fromtimestamp(fill.filled_at, UTC).date()
        if fill.asset_id == pair.forward.asset_id:
            expected = forward_value_date(
                day, pair.rule, book.ONE_MONTH, book.CONVENTION, end_of_month=True
            )
        else:
            expected = spot_value_date(day, pair.rule)
        assert booked[(fill.asset_id, fill.filled_at)] == expected.isoformat()
    # By hand, three a holiday moved.
    forwards = {
        (_pair_of(book, f.asset_id).base, datetime.fromtimestamp(f.filled_at, UTC).date()): booked[
            (f.asset_id, f.filled_at)
        ]
        for f in result.fills
        if f.asset_id == _pair_of(book, f.asset_id).forward.asset_id
    }
    assert forwards[("NZD", date(2025, 1, 2))] == "2025-02-07"  # Waitangi Day, 6 February
    assert forwards[("AUD", date(2025, 5, 5))] == "2025-06-10"  # King's Birthday, 9 June
    assert forwards[("AUD", date(2025, 10, 9))] == "2025-11-14"  # Columbus Day moved spot


def test_each_roll_settles_with_the_forward_it_closes(
    book: ModuleType, ran: tuple[Any, Any]
) -> None:
    result, strategy = ran
    legs = _legs(result)
    dated = {(leg.asset_id, leg.timestamp): leg.metadata[VALUE_DATE] for leg in legs}
    for pair in book.PAIRS:
        rolls = [roll for roll in strategy.rolls if roll.base == pair.base]
        assert len(rolls) == 11
        assert all(earlier.far == later.near for earlier, later in pairwise(rolls))
        for roll in rolls:
            day = roll.near.isoformat()
            due = [
                leg
                for leg in legs
                if leg.metadata[VALUE_DATE] == day and _pair_of(book, leg.asset_id) is pair
            ]
            # The maturing forward's two legs and the near leg's two, on one day.
            assert len(due) == 4
            # The currency arrives and leaves on it ...
            assert (
                sum(D(leg.metadata[SETTLEMENT_CASH]) for leg in due if leg.currency == pair.base)
                == 0
            )
            # ... and the dollars it gains arrive: sold at spot, bought forward, by hand.
            forward = next(
                f
                for f in result.fills
                if f.asset_id == pair.forward.asset_id and dated[(f.asset_id, f.filled_at)] == day
            )
            near = next(
                f
                for f in result.fills
                if f.asset_id == pair.spot.asset_id
                and datetime.fromtimestamp(f.filled_at, UTC).date() == roll.struck
            )
            dollars = sum(
                (D(leg.metadata[SETTLEMENT_CASH]) for leg in due if leg.currency == "USD"), D("0")
            )
            assert dollars == (near.price - forward.price) * book.NOTIONAL - 2 * book.COMMISSION


def test_cash_in_each_currency_equals_the_tally_of_the_fills(
    book: ModuleType, ran: tuple[Any, Any]
) -> None:
    result, _ = ran
    cash = {"USD": book.START_CASH, "AUD": D("0"), "NZD": D("0")}
    for fill in result.fills:
        base = _pair_of(book, fill.asset_id).base
        signed = fill.quantity if fill.side.value == "buy" else -fill.quantity
        cash[base] += signed
        cash["USD"] -= signed * fill.price
        cash["USD"] -= D("10.00")
    portfolio = result.state.portfolio
    assert {c: portfolio.cash.balance(c) for c in cash} == cash
    assert pending_from(portfolio.ledger.transactions) == portfolio.unsettled
    # After the last value date everything has settled: settled is trade-date.
    later = datetime(2026, 3, 2, tzinfo=UTC).timestamp()
    for currency, amount in cash.items():
        position = PortfolioEngine.cash_position(portfolio, currency, later)
        assert position.settled == position.trade_date == amount


def test_what_the_book_earned_adds_up(book: ModuleType, ran: tuple[Any, Any]) -> None:
    result, _ = ran
    rates = book.closing_rates()
    valuation = result.valuation_in("USD", rates)
    portfolio = result.state.portfolio
    last = {pair.base: book.SPOTS[pair.base][-1] for pair in book.PAIRS}
    assert valuation.equity == portfolio.cash.balance("USD") + sum(
        portfolio.cash.balance(base) * spot for base, spot in last.items()
    )
    earned = D("0")
    for pair in book.PAIRS:
        bought = [f for f in result.fills if f.asset_id == pair.forward.asset_id]
        sold = [f for f in result.fills if f.asset_id == pair.spot.asset_id]
        exits = [f.price for f in sold] + [last[pair.base]]
        earned += sum(
            ((exit_ - f.price) * book.NOTIONAL for f, exit_ in zip(bought, exits, strict=True)),
            D("0"),
        )
    commission = D("10.00") * len(result.fills)
    assert valuation.equity - book.START_CASH == earned - commission
