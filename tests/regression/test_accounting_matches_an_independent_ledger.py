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

Since v4.3 the same ledger holds daily bars too, under ``SAME_EVENT``,
``NEXT_EVENT`` and ``NEXT_OPEN``, under each remainder policy -- cancelled,
resting two events, resting three days -- and under each latency pricing -- a
stamp, or half a minute or a day and a half kept from the market -- (ADR-0060),
with gapped opens. Each fill's price is also held to its timing: the deciding
bar's close, the next bar's close, or the next bar's open, of the bar it
filled at; and no fill comes before its order's latency has passed. A fill
priced away from its event's mark -- at an open -- values its position at the
fill price until the next event of any asset re-marks it (pending marks,
PRF-001), so each scenario ends with two
quiet instants: the last order can fill at the first, and the second re-marks
it.
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
from alphalab.execution.latency import ConstantLatency
from alphalab.execution.policy import FillPolicy, FillTiming, LatencyPricing, RestRemainder
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.market.bar import Bar, TimeFrame
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


class BarScript(BaseStrategy):
    """Targets the plan names, at the bars the plan names."""

    def __init__(self, plan: dict[tuple[str, float], Decimal]) -> None:
        self._plan = plan

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        target = self._plan.get((bar.asset_id, bar.timestamp))
        if target is None:
            return ()
        return (
            Intent(
                strategy_id=STRATEGY,
                instrument=bar.asset_id,
                target=target,
                timestamp=bar.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )


DAY = 86_400.0


def _bar_scenario(seed: int) -> tuple[list[Bar], dict[tuple[str, float], Decimal], dict[str, Any]]:
    rng = random.Random(seed)
    closes = {asset: Decimal(rng.randint(20, 200)) for asset in ASSETS}
    bars: list[Bar] = []
    plan: dict[tuple[str, float], Decimal] = {}
    instant = DAY
    for _ in range(rng.randint(5, 20)):
        for asset in ASSETS:
            if rng.random() < 0.7:
                opened = max(Decimal("1"), closes[asset] + Decimal(rng.randint(-300, 300)) / 100)
                closed = max(Decimal("1"), opened + Decimal(rng.randint(-500, 500)) / 100)
                high = max(opened, closed) + Decimal(rng.randint(0, 200)) / 100
                low = max(Decimal("0.5"), min(opened, closed) - Decimal(rng.randint(0, 200)) / 100)
                volume = Decimal(rng.choice([1, 5, 50, 1000]))
                bars.append(
                    Bar(asset, instant, opened, high, low, closed, volume, None, None, TimeFrame.D1)
                )
                closes[asset] = closed
                if rng.random() < 0.5:
                    plan[(asset, instant)] = Decimal(rng.randint(-40, 40))
        instant += DAY
    for _ in range(2):
        # Quiet: no order is placed, every asset is priced.
        for asset in ASSETS:
            bars.append(
                Bar(asset, instant, *(closes[asset],) * 4, Decimal(1000), None, None, TimeFrame.D1)
            )
        instant += DAY
    policies: tuple[FillPolicy, ...] = (
        ImmediateFill(),
        LiquidityCappedFill(participation_rate=Decimal("0.5")),
    )
    choices = {
        "commission": Decimal(rng.choice(["0", "0.01", "0.05"])),
        "policy": rng.choice(policies),
        "timing": rng.choice(list(FillTiming)),
    }
    # Drawn last, so every earlier choice of a seed is what it was (ADR-0060).
    remainders = (None, RestRemainder(events=2), RestRemainder(seconds=3 * DAY))
    choices["remainder"] = rng.choice(remainders)
    # Drawn after it, on the same terms: a stamp and no latency, or a latency
    # that keeps an order from the market for half a minute -- to the next bar --
    # or a day and a half, which passes it by one of its asset's bars.
    pricings = (
        (LatencyPricing.STAMP, 0.0),
        (LatencyPricing.POST_LATENCY_EVENT, 30.0),
        (LatencyPricing.POST_LATENCY_EVENT, 1.5 * DAY),
    )
    choices["latency"] = rng.choice(pricings)
    return bars, plan, choices


@pytest.mark.parametrize("seed", range(30))
def test_the_engine_books_what_an_independent_ledger_books_on_bars(seed: int) -> None:
    bars, plan, choices = _bar_scenario(seed)
    pricing, latency = choices["latency"]
    config = backtest_config(
        STRATEGY,
        seed=seed,
        fill_policy=choices["policy"],
        simulator=ExecutionSimulator(
            commission_model=PerShareCommission(choices["commission"]),
            latency_model=ConstantLatency(latency),
        ),
    )
    config = replace(
        config,
        pipeline=replace(
            config.pipeline,
            fill_timing=choices["timing"],
            remainder=choices["remainder"],
            latency_pricing=pricing,
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=True
            ),
        ),
    )
    runtime = start_strategy(
        create_runtime(), STRATEGY, BarScript(plan), config={}, subscriptions={"bars"}, at=1.0
    )
    result = BacktestEngine.run(config, MarketDataset.of("BARS", bars), runtime, context_factory)
    assert result.strategy_failures == ()

    by_key = {(bar.asset_id, bar.timestamp): bar for bar in bars}
    cash = config.pipeline.starting_cash
    held = dict.fromkeys(ASSETS, Decimal(0))
    paid = Decimal(0)
    placed = {str(o.order_id.value): o.created_at for o in result.state.oms.orders.orders()}
    for fill in result.fills:
        bar = by_key[(fill.asset_id, fill.filled_at)]
        expected = bar.open if choices["timing"] is FillTiming.NEXT_OPEN else bar.close
        assert fill.price == expected, (choices["timing"], fill)
        # No order meets the market before its latency has passed (ADR-0060).
        decided = placed[str(fill.order_id)]
        assert fill.filled_at >= decided + latency, (choices["latency"], fill)
        signed = fill.quantity if fill.side.value == "buy" else -fill.quantity
        cash -= (signed * fill.price).quantize(CENT, ROUND_HALF_EVEN) + fill.commission
        paid += fill.commission
        held[fill.asset_id] += signed

    last = {bar.asset_id: bar.close for bar in bars}
    marked = sum((held[asset] * last[asset] for asset in ASSETS if held[asset]), Decimal(0))
    valuation = result.valuation
    book = result.state.portfolio.positions
    assert valuation.cash == cash
    assert {a: (book[a].quantity if a in book else Decimal(0)) for a in ASSETS} == held
    assert valuation.equity == valuation.cash + marked
    start = config.pipeline.starting_cash
    assert valuation.equity - start == valuation.realized_pnl + valuation.unrealized_pnl - paid
