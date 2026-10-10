"""The canonical path's accounting, held to a ledger kept independently of it (v4.0).

A differential, generated test: random price paths over three assets, random
target positions -- long, short, flips through zero -- under each fill timing,
an immediate or a liquidity-capped fill policy, and three commission rates. The
reference ledger below is written from the fills alone, in plain ``Decimal``
arithmetic, and shares no code with ``alphalab.portfolio``.

What it holds the engine to, on every seed:

* **cash** to the cent: each fill's notional rounded to the minor unit (half to
  even), less its commission;
* **positions** exactly;
* **equity** as cash plus each position marked at its last price;
* **conservation**: equity less starting cash is realized plus unrealized P&L
  less commission, exactly;
* **realized P&L** within a few cents of average-cost arithmetic. Not exactly,
  by design: the engine carries a position's cost basis as money at the minor
  unit and relieves it in proportion on each reduction, so a position reduced
  several times realizes the basis it still holds, to the cent, and money is
  conserved across the round trip; a per-fill average-cost formula rounds each
  realization on its own instead;
* **whole units**: a run that sizes orders in whole units fills in whole units
  (ledger EXE-011) -- before v4.0 a liquidity-capped partial fill took 2.5
  shares of a whole-share order.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import replace
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

import pytest

from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting import BacktestEngine, ImmediateFill, LiquidityCappedFill
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.state import BacktestResult
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.policy import FillPolicy, FillTiming
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.market.quote import Quote
from alphalab.strategy import (
    BaseStrategy,
    Intent,
    IntentKind,
    StrategyContext,
    create_runtime,
    start_strategy,
)
from tests.integration.harness import backtest_config, context_factory, sized_quote

STRATEGY = "LEDGER"
ASSETS = tuple(f"00000000-0000-0000-0000-0000000000a{index}" for index in range(1, 4))
CENT = Decimal("0.01")


class Script(BaseStrategy):
    """Targets the plan names, at the quotes the plan names."""

    def __init__(self, plan: dict[tuple[str, float], Decimal]) -> None:
        self._plan = plan

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        quote = event.quote
        target = self._plan.get((quote.asset_id, quote.timestamp))
        if target is None:
            return ()
        return (
            Intent(
                strategy_id=STRATEGY,
                instrument=quote.asset_id,
                target=target,
                timestamp=quote.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )


def _scenario(seed: int) -> tuple[list[Quote], dict[tuple[str, float], Decimal], dict[str, Any]]:
    rng = random.Random(seed)
    prices = {asset: Decimal(rng.randint(20, 200)) for asset in ASSETS}
    quotes: list[Quote] = []
    plan: dict[tuple[str, float], Decimal] = {}
    instant = 2.0
    for _ in range(rng.randint(5, 20)):
        for asset in ASSETS:
            if rng.random() < 0.7:
                step = Decimal(rng.randint(-500, 500)) / 100
                prices[asset] = max(Decimal("1"), prices[asset] + step)
                size = Decimal(rng.choice([1, 5, 50, 1000]))
                quotes.append(sized_quote(asset, instant, prices[asset], size))
                if rng.random() < 0.5:
                    plan[(asset, instant)] = Decimal(rng.randint(-40, 40))
                instant += 1.0
    policies: tuple[FillPolicy, ...] = (
        ImmediateFill(),
        LiquidityCappedFill(participation_rate=Decimal("0.5")),
    )
    policy = rng.choice(policies)
    choices = {
        "commission": Decimal(rng.choice(["0", "0.01", "0.05"])),
        "policy": policy,
        "timing": rng.choice([FillTiming.SAME_EVENT, FillTiming.NEXT_EVENT]),
    }
    return quotes, plan, choices


def _run(seed: int, whole_units: bool = True) -> tuple[BacktestResult, list[Quote]]:
    quotes, plan, choices = _scenario(seed)
    config = backtest_config(
        STRATEGY,
        seed=seed,
        fill_policy=choices["policy"],
        simulator=ExecutionSimulator(commission_model=PerShareCommission(choices["commission"])),
    )
    config = replace(
        config,
        pipeline=replace(
            config.pipeline,
            fill_timing=choices["timing"],
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=whole_units
            ),
        ),
    )
    runtime = start_strategy(
        create_runtime(), STRATEGY, Script(plan), config={}, subscriptions={"quotes"}, at=1.0
    )
    return BacktestEngine.run(
        config, MarketDataset.of("LEDGER", quotes), runtime, context_factory
    ), quotes


@pytest.mark.parametrize("seed", range(60))
def test_the_engine_books_what_an_independent_ledger_books(seed: int) -> None:
    result, quotes = _run(seed)
    assert result.strategy_failures == ()

    cash = result.config.pipeline.starting_cash
    held = dict.fromkeys(ASSETS, Decimal(0))
    cost = dict.fromkeys(ASSETS, Decimal(0))
    realized = paid = Decimal(0)
    for fill in result.fills:
        signed = fill.quantity if fill.side.value == "buy" else -fill.quantity
        asset, price, before = fill.asset_id, fill.price, held[fill.asset_id]
        cash -= (signed * price).quantize(CENT, ROUND_HALF_EVEN) + fill.commission
        paid += fill.commission
        if before == 0 or (before > 0) == (signed > 0):
            cost[asset] = (cost[asset] * abs(before) + price * abs(signed)) / (
                abs(before) + abs(signed)
            )
        else:
            closing = min(abs(signed), abs(before))
            direction = 1 if before > 0 else -1
            realized += (closing * (price - cost[asset]) * direction).quantize(CENT)
            if abs(signed) > abs(before):
                cost[asset] = price
        held[asset] = before + signed

    valuation = result.valuation
    book = result.state.portfolio.positions
    last = {quote.asset_id: quote.bid for quote in quotes}
    marked = sum((held[asset] * last[asset] for asset in ASSETS if held[asset]), Decimal(0))

    assert valuation.cash == cash
    assert {a: (book[a].quantity if a in book else Decimal(0)) for a in ASSETS} == held
    assert valuation.equity == valuation.cash + marked
    start = result.config.pipeline.starting_cash
    assert valuation.equity - start == valuation.realized_pnl + valuation.unrealized_pnl - paid
    assert abs(valuation.realized_pnl - realized) <= Decimal("0.05")
    assert all(fill.quantity == fill.quantity.to_integral_value() for fill in result.fills)


def _one_capped_fill(whole_units: bool, size: str) -> BacktestResult:
    """One buy of 10 against a quote showing ``size``, at a 50% participation cap."""

    asset = ASSETS[0]
    quotes = [sized_quote(asset, 2.0, Decimal("100"), Decimal(size))]
    config = backtest_config(
        STRATEGY, fill_policy=LiquidityCappedFill(participation_rate=Decimal("0.5"))
    )
    config = replace(
        config,
        pipeline=replace(
            config.pipeline,
            allocation_constraints=AllocationConstraints(
                allow_shorting=False, enforce_integer_quantities=whole_units
            ),
        ),
    )
    runtime = start_strategy(
        create_runtime(),
        STRATEGY,
        Script({(asset, 2.0): Decimal("10")}),
        config={},
        subscriptions={"quotes"},
        at=1.0,
    )
    return BacktestEngine.run(config, MarketDataset.of("ONE", quotes), runtime, context_factory)


def test_a_capped_partial_fill_is_floored_to_whole_units() -> None:
    """EXE-011: half of 5 shown is 2.5; a whole-unit run takes 2."""

    (fill,) = _one_capped_fill(whole_units=True, size="5").fills
    assert fill.quantity == Decimal("2")


def test_a_cap_below_one_unit_fills_nothing_in_a_whole_unit_run() -> None:
    assert _one_capped_fill(whole_units=True, size="1").fills == ()


def test_a_run_not_in_whole_units_still_takes_its_share() -> None:
    """The floor is the whole-unit contract's, not a change to the policy."""

    (fill,) = _one_capped_fill(whole_units=False, size="5").fills
    assert fill.quantity == Decimal("2.5")
