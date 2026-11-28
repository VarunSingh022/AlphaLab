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

M8's other execution modes are held the same way (ADR-0061, ADR-0062). A bar
run can be drawn with two declared venues, each charging its own commission,
and a router. Each order goes to the venue its terms name, or else to the
cheapest one, a tie going to the first code. Each fill is charged its venue's
commission, and its report and routing record name that venue; a run that
declares no venue records no routing. A run of packages -- pairs and triplets
of legs at signed ratios, between single orders -- books every leg as the
ledger books any fill. Each package fills at one instant, every leg at its
ratio, or no leg fills at all (leg atomicity).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

import pytest

from alphalab.allocation.constraints import AllocationConstraints
from alphalab.backtesting import BacktestEngine, ImmediateFill, LiquidityCappedFill
from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.state import BacktestResult
from alphalab.common.order_terms import MARKET, OrderTerms
from alphalab.core.fill import Fill
from alphalab.execution.commission import PerShareCommission
from alphalab.execution.latency import ConstantLatency
from alphalab.execution.policy import FillPolicy, FillTiming, LatencyPricing, RestRemainder
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.venue_models import CheapestVenue, VenueExecution
from alphalab.market.bar import Bar, TimeFrame
from alphalab.market.quote import Quote
from alphalab.oms.order import Order
from alphalab.runtime.run import RunConfig
from alphalab.strategy import (
    BaseStrategy,
    Intent,
    IntentKind,
    Leg,
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
    """Targets the plan names, at the bars the plan names, on the terms it names."""

    def __init__(
        self,
        plan: dict[tuple[str, float], Decimal],
        terms: dict[tuple[str, float], OrderTerms] | None = None,
    ) -> None:
        self._plan = plan
        self._terms = {} if terms is None else terms

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        key = (bar.asset_id, bar.timestamp)
        target = self._plan.get(key)
        if target is None:
            return ()
        return (
            Intent(
                strategy_id=STRATEGY,
                instrument=bar.asset_id,
                target=target,
                terms=self._terms.get(key, MARKET),
                timestamp=bar.timestamp,
                kind=IntentKind.TARGET_QUANTITY,
            ),
        )


DAY = 86_400.0
VENUES = ("ALPHA", "BETA")
COMMISSIONS = ("0", "0.01", "0.05")


def _bar(rng: random.Random, asset: str, instant: float, close: Decimal) -> Bar:
    """A daily bar opening a gap away from ``close``, its range around its open and close."""

    opened = max(Decimal("1"), close + Decimal(rng.randint(-300, 300)) / 100)
    closed = max(Decimal("1"), opened + Decimal(rng.randint(-500, 500)) / 100)
    high = max(opened, closed) + Decimal(rng.randint(0, 200)) / 100
    low = max(Decimal("0.5"), min(opened, closed) - Decimal(rng.randint(0, 200)) / 100)
    volume = Decimal(rng.choice([1, 5, 50, 1000]))
    return Bar(asset, instant, opened, high, low, closed, volume, None, None, TimeFrame.D1)


def _quiet(bars: list[Bar], closes: dict[str, Decimal], instant: float) -> None:
    """Two quiet instants: no order is placed, every asset is priced (pending marks)."""

    for _ in range(2):
        for asset in ASSETS:
            bars.append(
                Bar(asset, instant, *(closes[asset],) * 4, Decimal(1000), None, None, TimeFrame.D1)
            )
        instant += DAY


def _bar_scenario(seed: int) -> tuple[list[Bar], dict[tuple[str, float], Decimal], dict[str, Any]]:
    rng = random.Random(seed)
    closes = {asset: Decimal(rng.randint(20, 200)) for asset in ASSETS}
    bars: list[Bar] = []
    plan: dict[tuple[str, float], Decimal] = {}
    instant = DAY
    for _ in range(rng.randint(5, 20)):
        for asset in ASSETS:
            if rng.random() < 0.7:
                bars.append(_bar(rng, asset, instant, closes[asset]))
                closes[asset] = bars[-1].close
                if rng.random() < 0.5:
                    plan[(asset, instant)] = Decimal(rng.randint(-40, 40))
        instant += DAY
    _quiet(bars, closes, instant)
    policies: tuple[FillPolicy, ...] = (
        ImmediateFill(),
        LiquidityCappedFill(participation_rate=Decimal("0.5")),
    )
    choices = {
        "commission": Decimal(rng.choice(COMMISSIONS)),
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
    # Drawn last again, on the same terms (ADR-0061): one model, or two venues,
    # each order naming one of them or neither.
    choices["venues"] = _venue_rates(rng)
    named: dict[tuple[str, float], str] = {}
    for key in plan:
        venue = _named(rng, choices)
        if venue is not None:
            named[key] = venue
    choices["named"] = named
    return bars, plan, choices


def _venue_rates(rng: random.Random) -> dict[str, Decimal] | None:
    """No venue, or two declared, each with its own commission per share."""

    if rng.random() < 0.5:
        return None
    return {code: Decimal(rng.choice(COMMISSIONS)) for code in VENUES}


def _named(rng: random.Random, choices: dict[str, Any]) -> str | None:
    """The venue an order's terms name: one of those declared, or none, left to the router."""

    if choices["venues"] is None:
        return None
    return rng.choice((None, *VENUES))


def _ledger_config(seed: int, choices: dict[str, Any]) -> RunConfig:
    """The drawn timing, policy, remainder, latency and venues, in whole units."""

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
    declared: dict[str, Any] = {}
    rates: dict[str, Decimal] | None = choices["venues"]
    if rates is not None:
        # Each venue's own commission, at the run's latency, and the cheapest of
        # them for an order whose terms name none (ADR-0061).
        declared["venues"] = {
            code: VenueExecution(
                ExecutionSimulator(
                    commission_model=PerShareCommission(rate),
                    latency_model=ConstantLatency(latency),
                )
            )
            for code, rate in rates.items()
        }
        declared["router"] = CheapestVenue()
    return replace(
        config,
        pipeline=replace(
            config.pipeline,
            fill_timing=choices["timing"],
            remainder=choices["remainder"],
            latency_pricing=pricing,
            allocation_constraints=AllocationConstraints(
                allow_shorting=True, enforce_integer_quantities=True
            ),
            **declared,
        ),
    )


def _held_to_the_ledger(
    result: BacktestResult,
    bars: list[Bar],
    choices: dict[str, Any],
    named: Callable[[Order], str | None],
) -> None:
    """Hold a bar run to the ledger: each fill's price, latency and venue, and the money.

    ``named`` is the venue an order's terms named, read from the scenario, not the run.
    """

    _, latency = choices["latency"]
    rates: dict[str, Decimal] | None = choices["venues"]
    by_key = {(bar.asset_id, bar.timestamp): bar for bar in bars}
    orders = {str(order.order_id.value): order for order in result.state.oms.orders.orders()}

    def routed(order: Order) -> str:
        # The venue its terms name; else the cheapest by commission, a tie to the
        # first code (ADR-0061).
        assert rates is not None
        return named(order) or min(VENUES, key=rates.__getitem__)

    start = result.config.pipeline.starting_cash
    cash = start
    held = dict.fromkeys(ASSETS, Decimal(0))
    paid = Decimal(0)
    for fill in result.fills:
        order = orders[str(fill.order_id)]
        bar = by_key[(fill.asset_id, fill.filled_at)]
        expected = bar.open if choices["timing"] is FillTiming.NEXT_OPEN else bar.close
        assert fill.price == expected, (choices["timing"], fill)
        # No order meets the market before its latency has passed (ADR-0060).
        assert fill.filled_at >= order.created_at + latency, (choices["latency"], fill)
        if rates is not None:
            # Charged by the venue it was routed to, at that venue's rate.
            assert fill.commission == fill.quantity * rates[routed(order)], (rates, fill)
        signed = fill.quantity if fill.side.value == "buy" else -fill.quantity
        cash -= (signed * fill.price).quantize(CENT, ROUND_HALF_EVEN) + fill.commission
        paid += fill.commission
        held[fill.asset_id] += signed
    for order in orders.values():
        if rates is None:
            assert "venue" not in order.metadata, order  # one model: nothing is routed
        else:
            assert order.metadata["venue"] == routed(order), order
            assert order.metadata["venue_by"] == ("terms" if named(order) else "router"), order
    if rates is not None:
        for report in result.state.execution.history:
            assert report.venue == orders[report.order_id].metadata["venue"], report

    last = {bar.asset_id: bar.close for bar in bars}
    marked = sum((held[asset] * last[asset] for asset in ASSETS if held[asset]), Decimal(0))
    valuation = result.valuation
    book = result.state.portfolio.positions
    assert valuation.cash == cash
    assert {a: (book[a].quantity if a in book else Decimal(0)) for a in ASSETS} == held
    assert valuation.equity == valuation.cash + marked
    assert valuation.equity - start == valuation.realized_pnl + valuation.unrealized_pnl - paid


@pytest.mark.parametrize("seed", range(30))
def test_the_engine_books_what_an_independent_ledger_books_on_bars(seed: int) -> None:
    bars, plan, choices = _bar_scenario(seed)
    named: dict[tuple[str, float], str] = choices["named"]
    terms = {key: OrderTerms(venue=venue) for key, venue in named.items()}
    config = _ledger_config(seed, choices)
    runtime = start_strategy(
        create_runtime(),
        STRATEGY,
        BarScript(plan, terms),
        config={},
        subscriptions={"bars"},
        at=1.0,
    )
    result = BacktestEngine.run(config, MarketDataset.of("BARS", bars), runtime, context_factory)
    assert result.strategy_failures == ()
    _held_to_the_ledger(
        result, bars, choices, lambda order: named.get((order.asset_id, order.created_at))
    )


@dataclass(frozen=True, slots=True)
class Decision:
    """A package of ``legs`` labelled ``label``, ``target`` of them; with no legs, one target."""

    label: str
    target: Decimal
    legs: tuple[Leg, ...]
    terms: OrderTerms


class PackageScript(BaseStrategy):
    """The packages and single targets the plan names, at the bars it names."""

    def __init__(self, plan: dict[tuple[str, float], Decision]) -> None:
        self._plan = plan

    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        bar = event.bar
        decision = self._plan.get((bar.asset_id, bar.timestamp))
        if decision is None:
            return ()
        if not decision.legs:
            return (
                Intent(
                    STRATEGY,
                    bar.asset_id,
                    decision.target,
                    terms=decision.terms,
                    timestamp=bar.timestamp,
                    kind=IntentKind.TARGET_QUANTITY,
                ),
            )
        return (
            Intent(
                STRATEGY,
                decision.label,
                decision.target,
                terms=decision.terms,
                timestamp=bar.timestamp,
                kind=IntentKind.PACKAGE,
                legs=decision.legs,
            ),
        )


SIGNED = (-3, -2, -1, 1, 2, 3)


def _decision(rng: random.Random, index: int, choices: dict[str, Any]) -> Decision:
    """A package of two or three legs at signed ratios, or now and then a single target."""

    venue = _named(rng, choices)
    terms = MARKET if venue is None else OrderTerms(venue=venue)
    if rng.random() < 0.3:
        return Decision(f"S{index}", Decimal(rng.randint(-40, 40)), (), terms)
    assets = rng.sample(ASSETS, rng.choice([2, 3]))
    legs = tuple(Leg(asset, Decimal(rng.choice(SIGNED))) for asset in assets)
    return Decision(f"P{index}", Decimal(rng.choice(SIGNED)), legs, terms)


def _package_scenario(
    seed: int,
) -> tuple[list[Bar], dict[tuple[str, float], Decision], dict[str, Any]]:
    rng = random.Random(seed)
    policies: tuple[FillPolicy, ...] = (
        ImmediateFill(),
        LiquidityCappedFill(participation_rate=Decimal("0.5")),
    )
    choices: dict[str, Any] = {
        "commission": Decimal(rng.choice(COMMISSIONS)),
        "policy": rng.choice(policies),
        "timing": rng.choice(list(FillTiming)),
        "remainder": rng.choice((None, RestRemainder(events=2), RestRemainder(seconds=3 * DAY))),
        "latency": rng.choice(
            (
                (LatencyPricing.STAMP, 0.0),
                (LatencyPricing.POST_LATENCY_EVENT, 30.0),
                (LatencyPricing.POST_LATENCY_EVENT, 1.5 * DAY),
            )
        ),
        "venues": _venue_rates(rng),
    }
    closes = {asset: Decimal(rng.randint(20, 200)) for asset in ASSETS}
    bars: list[Bar] = []
    plan: dict[tuple[str, float], Decision] = {}
    instant = DAY
    for _ in range(rng.randint(5, 20)):
        for asset in ASSETS:
            # Most instants price every leg; one that does not keeps a package waiting.
            if rng.random() < 0.8:
                bars.append(_bar(rng, asset, instant, closes[asset]))
                closes[asset] = bars[-1].close
                if rng.random() < 0.5:
                    plan[(asset, instant)] = _decision(rng, len(plan), choices)
        instant += DAY
    _quiet(bars, closes, instant)
    return bars, plan, choices


@pytest.mark.parametrize("seed", range(30))
def test_the_engine_books_packages_as_an_independent_ledger_does(seed: int) -> None:
    bars, plan, choices = _package_scenario(seed)
    by_label = {decision.label: decision for decision in plan.values()}
    config = _ledger_config(seed, choices)
    runtime = start_strategy(
        create_runtime(),
        STRATEGY,
        PackageScript(plan),
        config={},
        subscriptions={"bars"},
        at=1.0,
    )
    result = BacktestEngine.run(
        config, MarketDataset.of("PACKAGES", bars), runtime, context_factory
    )
    assert result.strategy_failures == ()

    def decided(order: Order) -> Decision:
        label = order.metadata.get("package_label")
        return plan[(order.asset_id, order.created_at)] if label is None else by_label[label]

    _held_to_the_ledger(result, bars, choices, lambda order: decided(order).terms.venue)

    # Its turn (ADR-0062 section 5, ADR-0060): the first instant at or after its
    # decision -- after it, under a held timing -- and after its latency, that
    # priced every leg. Nothing caps an immediate fill, so it fills there.
    instants = sorted({bar.timestamp for bar in bars})
    priced: dict[float, set[str]] = {}
    for bar in bars:
        priced.setdefault(bar.timestamp, set()).add(bar.asset_id)
    _, latency = choices["latency"]
    held = choices["timing"] is not FillTiming.SAME_EVENT

    def first_turn(legs: list[Order]) -> float | None:
        decided_at = legs[0].created_at
        for instant in instants:
            if instant < decided_at + latency or (held and instant == decided_at):
                continue
            if instant >= decided_at and all(leg.asset_id in priced[instant] for leg in legs):
                return instant
        return None

    # Leg atomicity (ADR-0062): a package fills at one instant, every leg at its
    # ratio, or no leg of it fills at all.
    fills: dict[str, list[Fill]] = {}
    for fill in result.fills:
        fills.setdefault(str(fill.order_id), []).append(fill)
    packages: dict[str, list[Order]] = {}
    for order in result.state.oms.orders.orders():
        if "package_id" in order.metadata:
            packages.setdefault(order.metadata["package_id"], []).append(order)
    for legs in packages.values():
        decision = decided(legs[0])
        ratios = {leg.instrument: leg.ratio for leg in decision.legs}
        assert sorted(order.asset_id for order in legs) == sorted(ratios), decision
        filled = [fill for order in legs for fill in fills.get(str(order.order_id.value), ())]
        if isinstance(choices["policy"], ImmediateFill):
            turn = first_turn(legs)
            expected = [] if turn is None else [turn]
            assert sorted({fill.filled_at for fill in filled}) == expected, (decision, turn)
        if not filled:
            continue  # withdrawn, expired or still waiting: no leg traded
        assert len({fill.filled_at for fill in filled}) == 1, filled
        for order in legs:
            signed = sum(
                (
                    fill.quantity if fill.side.value == "buy" else -fill.quantity
                    for fill in fills.get(str(order.order_id.value), ())
                ),
                Decimal(0),
            )
            assert signed == decision.target * ratios[order.asset_id], (decision, order)
