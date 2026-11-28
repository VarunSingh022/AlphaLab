"""A survivorship-biased control is caught by what it traded (plan GR-5, ADR-0059).

Four instruments and one index. Three are members from the start. One of them,
a loser, falls and is removed -- announced ten records before it takes effect.
A fourth, a winner, rises, and is added once it has -- also announced before it
takes effect. Two strategies trade the index:

* the **point-in-time** strategy reads membership through its context,
  ``context.universe.members``, as known at its own instant: it holds what is a
  member now and will still be one at the next record, so it leaves the loser
  between the announcement and the removal, and joins the winner when it is
  added;
* the **control** holds today's survivors -- the membership at the end, read
  with hindsight -- for the whole history, as a backtest over a current
  constituent list does.

``research.bias.membership_review`` finds nothing in the point-in-time run and
names the control's trade in the winner, made before its addition was
announced. The difference between the two runs' returns is the bias, measured.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

from alphalab.backtesting.dataset import MarketDataset
from alphalab.backtesting.engine import BacktestEngine
from alphalab.backtesting.state import BacktestResult
from alphalab.instrument.membership import HistoricalUniverse, MembershipChange, MembershipKind
from alphalab.instrument.registry import InstrumentRegistry, register_universe
from alphalab.research.bias import MembershipFindingKind, membership_review
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import Intent, IntentKind
from alphalab.strategy.protocol import BaseStrategy
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.state import RuntimeState
from alphalab.strategy.supervisor import RuntimeSupervisor
from tests.integration.harness import (
    backtest_config,
    context_factory,
    equity,
    registry_of,
    sized_quote,
)

STRATEGY_ID = "S"
INDEX = "INDEX"
STEADY, SECOND, LOSER, WINNER = (equity(symbol) for symbol in ("STDY", "SCND", "LOSE", "WINR"))
FIRST, LAST = 2, 121
REMOVED_AT, REMOVAL_ANNOUNCED = 60.0, 50.0
ADDED_AT, ADDITION_ANNOUNCED = 80.0, 70.0


def _history() -> HistoricalUniverse:
    founders = [
        MembershipChange(INDEX, record.asset_id, MembershipKind.ADDITION, 0.0, 0.0)
        for record in (STEADY, SECOND, LOSER)
    ]
    return HistoricalUniverse(
        INDEX,
        (
            *founders,
            MembershipChange(
                INDEX, LOSER.asset_id, MembershipKind.REMOVAL, REMOVED_AT, REMOVAL_ANNOUNCED
            ),
            MembershipChange(
                INDEX, WINNER.asset_id, MembershipKind.ADDITION, ADDED_AT, ADDITION_ANNOUNCED
            ),
        ),
    )


def _registry() -> InstrumentRegistry:
    return register_universe(registry_of(STEADY, SECOND, LOSER, WINNER), _history())


def _price(asset_id: str, t: int) -> Decimal:
    if asset_id == LOSER.asset_id:
        return Decimal(100) - Decimal(min(t, 60)) * Decimal("0.8")
    if asset_id == WINNER.asset_id:
        return Decimal(50) + Decimal(min(t, 80)) * Decimal("0.6")
    if asset_id == STEADY.asset_id:
        return Decimal(100) + Decimal(t % 5) / 10
    return Decimal(80) + Decimal(t % 3) / 10


def _dataset() -> MarketDataset:
    assets = [record.asset_id for record in (STEADY, SECOND, LOSER, WINNER)]
    return MarketDataset.of(
        "GR-5",
        [
            sized_quote(asset_id, float(t), _price(asset_id, t), Decimal(1000))
            for t in range(FIRST, LAST + 1)
            for asset_id in assets
        ],
    )


class _PointInTime(BaseStrategy):
    """Holds one unit of what is a member now and, as announced, at the next record."""

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        now = event.quote.timestamp
        asset = event.quote.asset_id
        hold = asset in context.universe.members(INDEX, now) and asset in context.universe.members(
            INDEX, now + 1.0
        )
        return (_target(asset, now, hold),)


class _Survivors(BaseStrategy):
    """Holds one unit of today's survivors -- the end's members, read with hindsight."""

    def __init__(self, survivors: frozenset[str]) -> None:
        self._survivors = survivors

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        asset = event.quote.asset_id
        return (_target(asset, event.quote.timestamp, asset in self._survivors),)


def _target(asset: str, now: float, hold: bool) -> Intent:
    return Intent(
        STRATEGY_ID,
        asset,
        Decimal(1) if hold else Decimal(0),
        timestamp=now,
        kind=IntentKind.TARGET_QUANTITY,
    )


def _running(strategy: BaseStrategy) -> RuntimeState:
    state = register_strategy(create_runtime(), STRATEGY_ID, strategy)
    entry = state.strategies[STRATEGY_ID]
    entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"quotes"}), 1.2)
    entry, _ = RuntimeSupervisor.start(entry, 1.3)
    return replace(state, strategies={STRATEGY_ID: entry})


def _run(strategy: BaseStrategy) -> BacktestResult:
    config = backtest_config(STRATEGY_ID)
    config = replace(config, pipeline=replace(config.pipeline, instruments=_registry()))
    return BacktestEngine.run(config, _dataset(), _running(strategy), context_factory)


def _total_return(result: BacktestResult) -> Decimal:
    curve = result.run.pipeline.portfolio_snapshots
    return curve[-1].total_equity / curve[0].total_equity - 1


def test_the_point_in_time_run_trades_only_members_as_they_were_known() -> None:
    result = _run(_PointInTime())
    review = membership_review(result.run.pipeline.fills, _history())

    assert review.trades > 0
    assert review.clean, review.findings
    loser = [f for f in result.run.pipeline.fills if f.asset_id == LOSER.asset_id]
    winner = [f for f in result.run.pipeline.fills if f.asset_id == WINNER.asset_id]
    # It leaves the loser after the removal was announced, before it took effect,
    # and joins the winner when it is added -- never before.
    assert [fill.side.value for fill in loser] == ["buy", "sell"]
    assert REMOVAL_ANNOUNCED <= loser[-1].filled_at < REMOVED_AT
    assert [fill.filled_at for fill in winner] == [ADDED_AT]


def test_the_survivorship_biased_control_is_named_by_its_trades() -> None:
    survivors = _history().members(float(LAST), as_of=float(LAST))
    assert survivors == {STEADY.asset_id, SECOND.asset_id, WINNER.asset_id}
    result = _run(_Survivors(survivors))
    review = membership_review(result.run.pipeline.fills, _history())

    assert not review.clean
    (finding,) = review.findings
    assert finding.asset_id == WINNER.asset_id
    assert finding.kind is MembershipFindingKind.BEFORE_ANNOUNCEMENT
    assert finding.at == float(FIRST)
    assert finding.announced_at == ADDITION_ANNOUNCED
    assert finding.side == "buy"


def test_the_bias_is_the_difference_the_control_shows() -> None:
    point_in_time = _run(_PointInTime())
    control = _run(_Survivors(_history().members(float(LAST), as_of=float(LAST))))

    # Today's survivors held through the past miss the loser's fall while the
    # point-in-time run held it (57 records at 0.8) and catch the winner's rise
    # before it joined (78 records at 0.6): the control looks better than any
    # strategy could have done with what was known, by exactly that.
    loser_fall = (59 - FIRST) * Decimal("0.8")
    winner_rise = (80 - FIRST) * Decimal("0.6")
    assert _gain(control) - _gain(point_in_time) == loser_fall + winner_rise == Decimal("92.4")
    assert _total_return(control) > _total_return(point_in_time)


def _gain(result: BacktestResult) -> Decimal:
    curve = result.run.pipeline.portfolio_snapshots
    return curve[-1].total_equity - curve[0].total_equity


def test_a_strategy_never_sees_a_change_before_it_is_announced() -> None:
    from alphalab.runtime.context_views import UniverseView

    registry = _registry()
    before = UniverseView(registry, ADDITION_ANNOUNCED - 1.0)
    after = UniverseView(registry, ADDITION_ANNOUNCED)

    assert WINNER.asset_id not in before.members(INDEX, ADDED_AT)
    assert WINNER.asset_id in after.members(INDEX, ADDED_AT)
    assert WINNER.asset_id not in after.members(INDEX, ADDED_AT - 1.0)
