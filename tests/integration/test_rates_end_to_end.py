"""S-06 end to end: a duration-neutral curve book, run as an experiment (v4.3, ADR-0065).

Example 85's experiment (``examples/_rates_book.py``) holds a two-year
Treasury note against a ten-year payer swap through a synthetic 2025:

* **The curves** are bootstrapped every week under their named interpolation,
  and each reprices its quotes. The note is valued at ACT/ACT (ICMA), as its
  curve's notes are.
* **Duration-neutral by construction.** Sized by ``duration``, the book's
  value is unmoved by a parallel basis point on both curves at inception.
  Sized by ``notional``, it is short a ten-year's duration against a
  two-year's.
* **Run as an experiment**, each sizing a counted trial. The level rose over
  the year, so the notional-sized book, short duration, earned more than the
  duration-neutral one.
* **Every payment is booked by the run**, and the duration book's cash is its
  trades, the note's coupon and the swap's netted coupons, to the cent, by
  hand.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from types import ModuleType

import pytest

from alphalab.backtesting import BacktestEngine
from alphalab.core.enums import Side
from alphalab.lifecycle.experiments import TrialOutcome
from alphalab.rates import Interpolation
from alphalab.strategy import (
    DiscardingLogger,
    FixedClock,
    context_factory,
    create_runtime,
    start_strategy,
)

ROOT = Path(__file__).resolve().parents[2]
DEFINITION = ROOT / "examples" / "_rates_book.py"
D = Decimal


@pytest.fixture(scope="module")
def book() -> ModuleType:
    """The book's module, loaded under the name the example imports it by."""

    name = "_rates_book"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, DEFINITION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def cents(amount: Decimal) -> Decimal:
    return amount.quantize(D("0.01"), rounding=ROUND_HALF_EVEN)


@pytest.mark.parametrize("day", [date(2025, 1, 15), date(2025, 7, 9), date(2025, 12, 31)])
def test_each_curve_is_bootstrapped_under_its_named_interpolation_and_reprices(
    book: ModuleType, day: date
) -> None:
    for bootstrap, tolerance in ((book.swap_curve(day), 1e-12), (book.treasury_curve(day), 1e-10)):
        assert bootstrap.curve.interpolation is Interpolation.LOG_LINEAR_DISCOUNT
        assert bootstrap.curve.derivation_id == bootstrap.bootstrap_id
        assert bootstrap.largest_error < tolerance


def test_the_duration_book_is_unmoved_by_a_parallel_basis_point_and_the_notional_book_is_not(
    book: ModuleType,
) -> None:
    note, swap = book.NOTE_DV01, book.SWAP_DV01
    assert note < 0 and swap < 0  # both lose as rates rise
    face = float(book.FACE)
    neutral = face * note - float(book.swap_notional("duration")) * swap
    # Rounded to a whole unit of notional: within one unit's DV01 of zero.
    assert abs(neutral) <= abs(swap)
    short = face * note - float(book.swap_notional("notional")) * swap
    assert short > 0.5 * abs(face * swap)  # a ten-year's duration paid against a two-year's


def test_each_sizing_is_a_counted_trial_and_the_short_duration_book_gained_as_rates_rose(
    book: ModuleType,
) -> None:
    where = book.ledger()
    record = book.run(where)
    assert record.counts.recorded == 2 and record.counts.completed == 2
    growth = {}
    for trial in book.SPEC.trials():
        held = where.find(trial.trial_id)
        assert held is not None and held.outcome is TrialOutcome.COMPLETED
        growth[trial.configuration.rule] = book.growth(where, trial)
    level_moved = book.STATES[book.LAST][0] - book.STATES[book.FIRST][0]
    assert level_moved > 0.002  # this path's level rose 86 basis points
    assert growth["notional"] > growth["duration"]


def test_every_payment_is_booked_and_the_duration_books_cash_reconciles(book: ModuleType) -> None:
    notional = book.swap_notional("duration")
    strategy = book.CurveBook(notional)
    result = BacktestEngine.run(
        book.run_config(),
        book.DATASET,
        start_strategy(
            create_runtime(),
            book.STRATEGY_ID,
            strategy,
            config={},
            subscriptions={"quotes"},
            at=book.at(book.FIRST, 0),
        ),
        context_factory(FixedClock(book.at(book.FIRST, 0)), DiscardingLogger()),
        schedule=book.SCHEDULE,
    )
    fills = {fill.asset_id: fill for fill in result.fills}
    note, swap = fills[book.NOTE.asset_id], fills[book.SWAP.asset_id]
    assert (note.side, note.quantity) == (Side.BUY, book.FACE)
    assert (swap.side, swap.quantity) == (Side.SELL, notional)
    # The note bought at its first mark per 100, a multiplier of 0.01; the swap's
    # receiver sold at its first value, a hair below nothing.
    cash = (
        book.START_CASH
        - cents(note.quantity * note.price * D("0.01"))
        + cents(swap.quantity * swap.price)
    )
    cash -= note.commission + swap.commission
    (coupon,) = book.NOTE_COUPONS
    cash += cents(book.FACE * coupon.per_unit)
    swap_coupons = [
        d for d in book.SCHEDULE.entries if getattr(d.item, "asset_id", None) == book.SWAP.asset_id
    ]
    assert len(swap_coupons) == 3  # April, July (netted) and October
    for entry in swap_coupons:
        cash += cents(-notional * entry.item.per_unit)
    assert result.state.portfolio.cash.balance("USD") == cash
    positions = result.state.portfolio.positions
    assert positions[book.NOTE.asset_id].quantity == book.FACE
    assert positions[book.SWAP.asset_id].quantity == -notional
