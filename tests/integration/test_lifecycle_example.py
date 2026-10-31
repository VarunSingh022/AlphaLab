"""Example 75 does what it prints: an option's life, figure for figure (v4.1.1, ledger FUT-013).

Every figure is recomputed here from the example's own price functions and the
contracts' declared terms, so the test fails if the engine and the arithmetic
part company -- not if the example's prose changes.
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from alphalab.lifecycle import digest_run

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "75_exercise_assign_and_deliver.py"


@pytest.fixture(scope="module")
def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_75", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_movement_of_cash_and_shares_is_the_contracts(example: ModuleType) -> None:
    result = example.run()
    last = len(example.SESSIONS) - 1
    # Bought when momentum first turned up -- the fourth session -- and filled at
    # the options' bars of that close, in the order the run reaches them.
    call_premium = example.call_close(3) * 2 * 100
    put_premium = example.put_close(3) * 1 * 10
    strike_paid = Decimal("100") * 100 * 2  # one call exercised early, one at expiry
    dividend = example.DIVIDEND * 100  # on the 100 shares delivered before the ex-date
    put_paid = max(Decimal("2000") - example.index_close(last), Decimal("0")) * 10
    expected = example.START_CASH - call_premium - put_premium - strike_paid + dividend + put_paid
    portfolio = result.state.portfolio
    assert portfolio.cash.balance("GBP") == expected
    shares = portfolio.positions[example.STOCK.asset_id]
    assert shares.quantity == Decimal("200") and shares.average_cost == Decimal("100")
    assert example.CALL.asset_id not in portfolio.positions
    assert example.PUT.asset_id not in portfolio.positions
    valuation = result.valuation
    assert valuation.equity == (
        example.START_CASH
        + valuation.realized_pnl
        + valuation.unrealized_pnl
        - valuation.commission_paid
    )


def test_the_early_exercise_takes_effect_at_the_decision_close_before_the_dividend(
    example: ModuleType,
) -> None:
    scheduled = example.early_exercise()
    decision = example.SESSIONS[example.SESSIONS.index(example.EX_DIVIDEND) - 1]
    assert scheduled.at == example.session_close(example.MARKET, decision)
    result = example.run()
    (exercise,) = [r for r in result.state.trade_records if r.trade_id.startswith("EXERCISE")]
    assert exercise.trade_id.endswith("pre-dividend")
    # The dividend reached the delivered shares: realized P&L holds it.
    premiums = example.call_close(3) * 100 * 2
    put_gain = max(Decimal("2000") - example.index_close(len(example.SESSIONS) - 1), Decimal("0"))
    assert result.valuation.realized_pnl == (
        -premiums + example.DIVIDEND * 100 + put_gain * 10 - example.put_close(3) * 10
    )


def test_what_the_contracts_cannot_do_is_refused(example: ModuleType) -> None:
    found = example.refusals()
    assert len(found) == 3
    assert "BERMUDAN" in found[0] and "EUROPEAN" in found[2]


def test_the_replay_and_a_resumed_replay_are_the_run(example: ModuleType) -> None:
    result = example.run()
    replay = example.replayed()
    assert replay.fills == result.fills and replay.valuation == result.valuation
    for pause in (10, 40, 70):
        assert digest_run(example.replayed(pause)).result_id == digest_run(replay).result_id
