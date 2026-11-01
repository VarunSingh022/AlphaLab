"""Example 77 does what it prints: a short squeezed by its lender, figure for figure (phase 3).

Every figure is recomputed from the example's declared terms -- the lender's
entries, the venue's halt, the quotes and the day's cutoff -- so the test fails
if the engine and the arithmetic part company, not if the prose changes
(ledgers FUT-022, FUT-023, FUT-027, FUT-029).
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from types import ModuleType

import pytest

from alphalab.lifecycle import digest_run
from alphalab.portfolio.types import TransactionType
from alphalab.risk.events import BuyIn

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "77_a_short_squeezed_by_its_lender.py"
D = Decimal


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_77", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_recall_is_bought_in_at_the_reopening_auction(example: ModuleType) -> None:
    result = example.run()
    trades = [
        (t.quantity, t.price, (t.timestamp - example.T0) / example.HOUR)
        for t in result.state.portfolio.ledger.transactions
        if t.type in (TransactionType.BUY, TransactionType.SELL)
    ]
    first_after_halt = 6  # the halt runs from hour 3.5 to 5.5
    assert trades == [
        (D("-1000"), D(example.PRICES[0]), 0.0),
        (D("600"), D(example.PRICES[first_after_halt]), float(first_after_halt)),
    ]
    (bought,) = [e for e in result.state.risk.events if isinstance(e, BuyIn)]
    assert (bought.excess, bought.available) == (D("600"), D("400"))
    assert bought.timestamp == example.at(3)


def test_the_bid_is_amended_in_place_and_keeps_its_id(example: ModuleType) -> None:
    result = example.run()
    limits = [o for o in result.state.oms.orders.orders() if o.limit_price is not None]
    # One bid, never replaced: reduced in place from 600 to 300, still working.
    (bid,) = limits
    assert bid.is_open and (bid.quantity, bid.limit_price) == (D("300"), D("90"))


def test_the_days_fee_is_on_the_short_and_mark_at_its_cutoff(example: ModuleType) -> None:
    result = example.run()
    (fee,) = [
        t for t in result.state.portfolio.ledger.transactions if t.type is TransactionType.FEE
    ]
    # 20:30 UTC is hour 7.5: 400 still short, marked at hour 7's quote.
    mark = D(example.PRICES[7])
    expected = -(D("400") * mark * D("0.02") / 360).quantize(D("0.01"), ROUND_HALF_EVEN)
    assert fee.quantity == expected


def test_the_run_replays_and_resumes_to_one_result(example: ModuleType) -> None:
    replay = example.replayed()
    assert replay.fills == example.run().fills
    assert digest_run(example.replayed(pause=5)).result_id == digest_run(replay).result_id
