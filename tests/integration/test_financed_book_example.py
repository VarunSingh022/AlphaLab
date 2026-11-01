"""Example 76 does what it prints: a financed long-short book, figure for figure (v4.2.0).

Every figure is recomputed here from the example's declared terms -- the
quotes, the debit tiers, the lender's rates, the merger's ratio and the margin
scenarios -- so the test fails if the engine and the arithmetic part company,
not if the example's prose changes (ledgers FUT-018 to FUT-021).
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from types import ModuleType

import pytest

from alphalab.lifecycle import digest_run
from alphalab.portfolio.events import PositionExchanged
from alphalab.portfolio.fx import NO_RATES
from alphalab.portfolio.types import TransactionType
from alphalab.risk.events import MarginCall
from alphalab.runtime.execution_pipeline import margin_position

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "76_a_financed_long_short_book.py"
D = Decimal
CENT = D("0.01")


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_76", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def price(example: ModuleType, asset_id: str, hour: int) -> Decimal:
    return D(example.PRICES[asset_id][hour])


def test_the_trades_are_the_strategys_at_the_venues_prices(example: ModuleType) -> None:
    result = example.run()
    long, short = example.LONG.asset_id, example.SHORT.asset_id
    trades = [
        (t.asset_id, t.quantity, t.price, (t.timestamp - example.T0) / example.HOUR)
        for t in result.state.portfolio.ledger.transactions
        if t.type in (TransactionType.BUY, TransactionType.SELL)
    ]
    # The bid placed in the halt at 95 never trades: it is withdrawn at hour 3
    # and the re-quote at 103 fills there, after the halt, inside the band.
    assert trades == [
        (short, D("-1000"), price(example, short, 0), 0.0),
        (long, D("1500"), price(example, long, 1), 1.0),
        (long, D("100"), price(example, long, 3), 3.0),
    ]
    band = example.CONTROLS.band(long, example.at(3))
    assert band is not None and band.admits(price(example, long, 3))


def test_the_merger_carries_the_short_and_its_basis(example: ModuleType) -> None:
    portfolio = example.run().state.portfolio
    (merged,) = [e for e in portfolio.events if isinstance(e, PositionExchanged)]
    assert merged.quantity == D("-1000")
    assert merged.received == {example.SHORTNEW.asset_id: D("-500")}
    assert example.SHORT.asset_id not in portfolio.positions
    carried = portfolio.positions[example.SHORTNEW.asset_id]
    # One for two, the whole basis carried: 50 a share becomes 100.
    assert carried.quantity == D("-500")
    assert carried.average_cost == price(example, example.SHORT.asset_id, 0) * 2


def test_the_debit_pays_its_tiers_and_the_short_its_lenders_rate(example: ModuleType) -> None:
    portfolio = example.run().state.portfolio
    long, short, new = (example.LONG.asset_id, example.SHORT.asset_id, example.SHORTNEW.asset_id)
    debit = -(
        example.CASH
        + 1000 * price(example, short, 0)
        - 1500 * price(example, long, 1)
        - 100 * price(example, long, 3)
    )
    assert debit == D("11800")
    day = D("1") / D("360")  # one day, ACT/360
    interest = (D("10000") * D("0.065") + (debit - D("10000")) * D("0.055")) * day
    # At hour 4 the lender's second rate is in force, on SHORTNEW's value then.
    fee = 500 * price(example, new, 4) * D("0.06") * day
    flows = {
        t.type: t.quantity
        for t in portfolio.ledger.transactions
        if t.type in (TransactionType.INTEREST, TransactionType.FEE)
    }
    assert flows == {
        TransactionType.INTEREST: -interest.quantize(CENT, ROUND_HALF_EVEN),
        TransactionType.FEE: -fee.quantize(CENT, ROUND_HALF_EVEN),
    }
    assert portfolio.cash.balance("USD") == -debit + sum(flows.values(), D("0"))


def test_the_pair_is_margined_by_its_worst_scenario(example: ModuleType) -> None:
    state = example.run().state
    figures = margin_position(state, NO_RATES, example.at(4))
    assert figures is not None
    long_value = 1600 * price(example, example.LONG.asset_id, 4)
    short_value = 500 * price(example, example.SHORTNEW.asset_id, 4)
    # Both down 15%: the long loses more than the short gains. The pair moving
    # apart costs less; the floor, 4% of the gross, less still.
    worst = D("0.15") * (long_value - short_value)
    assert worst > D("0.05") * (long_value + short_value) > D("0.04") * (long_value + short_value)
    assert figures.initial == worst
    assert sum((h.initial for h in figures.holdings), D("0")) == D("0.5") * (
        long_value + short_value
    )
    assert not [e for e in state.risk.events if isinstance(e, MarginCall)]


def test_the_book_replays_and_resumes_to_the_same_result(example: ModuleType) -> None:
    result = example.run()
    replayed = example.replayed()
    assert replayed.fills == result.fills
    resumed = example.replayed(pause=6)
    assert digest_run(resumed).result_id == digest_run(replayed).result_id


def test_the_example_prints_its_story(
    example: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    example.main()
    out = capsys.readouterr().out
    assert "became -500 SHORTNEW" in out
    assert "17,685.00 under the scenarios, against 107,450.00" in out
    assert "margin calls: 0" in out
