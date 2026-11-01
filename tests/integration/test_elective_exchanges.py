"""Holder elections and announcement instants (phase 3 FUT-024; phase 4 FUT-030).

An :class:`ElectiveExchange` offers a holding's strategies alternatives -- here,
half a NEWCO share or 120 in cash for each OLDCO -- with a default for a holder
that does not elect. A strategy elects with ``IntentKind.ELECT`` between the
announcement and the deadline; the holding is exchanged by the alternative its
holders elected, or by the default. A contract's adjustment announced before it
takes effect is seen by a strategy from its announcement, never before.

OLDCO is bought at 100 on 2.0; the action is announced at 2.2, closes its
elections at 2.8, and takes effect at 3.0. Quotes are every 0.1 from 2.0.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from alphalab.backtesting import (
    BacktestResult,
    ElectiveExchange,
    PausedReplay,
    ReplayBacktest,
    RunSchedule,
    ScheduleValidationError,
)
from alphalab.backtesting.replay_snapshot import capture as capture_replay
from alphalab.backtesting.replay_snapshot import from_primitives as replay_from_primitives
from alphalab.backtesting.replay_snapshot import restore as restore_replay
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.terms import Deliverable, OptionTerms, TermsAdjustment
from alphalab.lifecycle import digest_run
from alphalab.persistence import deserialize, serialize
from alphalab.portfolio.corporate_actions import ExchangeLeg, HoldingExchange
from alphalab.runtime.run_snapshot import RunObjects
from alphalab.runtime.snapshot import RuntimeObjects
from alphalab.strategy.context import StrategyContext
from alphalab.strategy.events import ElectionMade, Intent, IntentKind
from alphalab.strategy.exceptions import InvalidIntentError
from alphalab.strategy.runtime import create_runtime, register_strategy
from alphalab.strategy.supervisor import RuntimeSupervisor
from alphalab.strategy.validation import validate_intent
from tests.integration import test_holding_exchanges as exchanges
from tests.integration import test_margin_and_liquidation as margin
from tests.integration.harness import context_factory

D = Decimal
OLD, NEW = exchanges.OLD, exchanges.NEW
STOCK = HoldingExchange(OLD.asset_id, (ExchangeLeg(NEW.asset_id, D("0.5"), D("1")),))
CASH = HoldingExchange(OLD.asset_id, cash_per_unit=D("120"))
REF = "OLDCO tender 2024"
STEPS = [round(2.0 + n / 10, 1) for n in range(12)]  # 2.0 to 3.1


def action(**kw: Any) -> ElectiveExchange:
    fields: dict[str, Any] = {
        "alternatives": (STOCK, CASH),
        "default": 0,
        "announced_at": 2.2,
        "deadline": 2.8,
        "at": 3.0,
        "reference": REF,
    }
    fields.update(kw)
    return ElectiveExchange(**fields)


class Elector(margin.Trader):
    """Buys by its plan; elects ``choices`` -- instant to alternative -- in the action."""

    def __init__(
        self, plan: dict[tuple[float, str], Decimal], choices: dict[float, int], ref: str = REF
    ) -> None:
        super().__init__(plan)
        self._wanted = plan
        self._choices = choices
        self._ref = ref
        self.sid = "S"

    def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        at, asset = event.quote.timestamp, event.quote.asset_id
        out: list[Intent] = []
        wanted = self._wanted.get((at, asset))
        if wanted is not None:
            out.append(Intent(self.sid, asset, wanted, timestamp=at))
        choice = self._choices.get(at) if asset == OLD.asset_id else None
        if choice is not None:
            out.append(
                Intent(
                    self.sid,
                    OLD.asset_id,
                    D(choice),
                    timestamp=at,
                    kind=IntentKind.ELECT,
                    metadata={"action": self._ref},
                )
            )
        return tuple(out)


def strategies(*electors: tuple[str, Elector]) -> Any:
    state = create_runtime()
    for sid, elector in electors:
        elector.sid = sid
        state = register_strategy(state, sid, elector)
    running = {}
    for sid, entry in state.strategies.items():
        entry, _ = RuntimeSupervisor.configure(entry, {}, 1.0)
        entry, _ = RuntimeSupervisor.initialize(entry, 1.1)
        entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"*"}), 1.2)
        running[sid], _ = RuntimeSupervisor.start(entry, 1.3)
    return replace(state, strategies=running)


def run(choices: dict[float, int], exchange: ElectiveExchange | None = None) -> BacktestResult:
    elector = Elector(exchanges.BUY, choices)
    return exchanges.run(
        STOCK,
        OLD,
        NEW,
        mids={OLD.asset_id: dict.fromkeys(STEPS, "100"), NEW.asset_id: {3.1: "200"}},
        strategies=strategies(("S", elector)),
        schedule=RunSchedule.of(exchanges=[exchange or action()]),
    )


def took_cash(result: BacktestResult) -> bool:
    assert exchanges.position(result, OLD) is None
    return exchanges.position(result, NEW) is None


# --------------------------------------------------------------------------- #
# An election, counted or not
# --------------------------------------------------------------------------- #


def test_a_holder_electing_cash_is_paid_cash() -> None:
    result = run({2.5: 1})
    assert took_cash(result)
    assert exchanges.usd(result) == exchanges.START - D("10000") + D("12000")
    (made,) = [e for e in result.state.strategy.events if isinstance(e, ElectionMade)]
    assert (made.timestamp, made.action, made.alternative) == (2.5, REF, 1)


def test_a_holder_not_electing_receives_the_default() -> None:
    result = run({})
    assert not took_cash(result)
    assert exchanges.position(result, NEW).quantity == D("50.0")


@pytest.mark.parametrize(
    ("choices", "why"),
    [
        ({2.1: 1}, "before the announcement"),
        ({2.9: 1}, "after the deadline"),
        ({2.5: 7}, "an alternative not offered"),
    ],
)
def test_an_election_the_action_does_not_count_leaves_the_default(
    choices: dict[float, int], why: str
) -> None:
    assert not took_cash(run(choices)), why


def test_the_latest_election_before_the_deadline_counts() -> None:
    assert not took_cash(run({2.3: 1, 2.6: 0}))
    assert took_cash(run({2.3: 0, 2.6: 1}))


def test_an_election_for_another_action_is_not_counted() -> None:
    elector = Elector(exchanges.BUY, {2.5: 1}, ref="another action")
    result = exchanges.run(
        STOCK,
        OLD,
        NEW,
        mids={OLD.asset_id: dict.fromkeys(STEPS, "100"), NEW.asset_id: {3.1: "200"}},
        strategies=strategies(("S", elector)),
        schedule=RunSchedule.of(exchanges=[action()]),
    )
    assert not took_cash(result)


def test_holders_electing_differently_each_take_their_own_on_the_one_holding() -> None:
    half = {(2.0, OLD.asset_id): D("50")}
    result = exchanges.run(
        STOCK,
        OLD,
        NEW,
        mids={OLD.asset_id: dict.fromkeys(STEPS, "100"), NEW.asset_id: {3.1: "200"}},
        strategies=strategies(("A", Elector(half, {2.5: 1})), ("B", Elector(half, {2.5: 0}))),
        schedule=RunSchedule.of(exchanges=[action()]),
    )
    # Each holder's share by its own election (FUT-030; under FUT-024 the
    # default took the whole holding): A's 50 for cash, B's 50 for 25 NEWCO.
    assert not took_cash(result)
    assert exchanges.usd(result) == exchanges.START - D("10000") + D("6000")
    assert exchanges.position(result, NEW).quantity == D("25")
    agreed = exchanges.run(
        STOCK,
        OLD,
        NEW,
        mids={OLD.asset_id: dict.fromkeys(STEPS, "100"), NEW.asset_id: {3.1: "200"}},
        strategies=strategies(("A", Elector(half, {2.5: 1})), ("B", Elector(half, {2.6: 1}))),
        schedule=RunSchedule.of(exchanges=[action()]),
    )
    assert took_cash(agreed)


def test_elected_cash_paid_later_is_an_entitlement_until_it_is_paid() -> None:
    from alphalab.portfolio.engine import PortfolioEngine

    result = run({2.5: 1}, action(payment_at=3.05))
    position = PortfolioEngine.cash_position(result.state.portfolio, "USD", 3.02)
    assert position.unsettled_credits == D("12000.00")


# --------------------------------------------------------------------------- #
# Announcement instants on a contract's terms
# --------------------------------------------------------------------------- #


def test_a_strategy_sees_a_contract_adjustment_from_its_announcement() -> None:
    from tests.integration import test_option_lifecycle as lifecycle

    adjusted = TermsAdjustment(
        5.0, D("50"), Deliverable(lifecycle.SHARE.asset_id, D("200")), announced_at=3.0
    )
    terms = lifecycle.AM_CALL.terms
    assert isinstance(terms, OptionTerms)
    call = replace(lifecycle.AM_CALL, terms=replace(terms, adjustments=(adjusted,)))

    class Reader(margin.Trader):
        def __init__(self) -> None:
            super().__init__({})
            self.seen: list[tuple[float, int]] = []

        def on_quote(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
            record = context.universe.instrument(call.asset_id)
            assert record is not None and isinstance(record.terms, OptionTerms)
            self.seen.append((event.quote.timestamp, len(record.terms.adjustments)))
            return ()

    reader = Reader()
    exchanges.run(
        STOCK,
        lifecycle.SHARE,
        call,
        mids={lifecycle.SHARE.asset_id: {2.0: "100", 3.0: "100", 4.0: "100"}},
        strategies=strategies(("S", reader)),  # type: ignore[arg-type]
        schedule=RunSchedule.of(),
    )
    assert reader.seen == [(2.0, 0), (3.0, 1), (4.0, 1)]


# --------------------------------------------------------------------------- #
# Replay and declarations
# --------------------------------------------------------------------------- #


def test_an_election_replays_and_resumes_from_every_pause() -> None:
    schedule = RunSchedule.of(exchanges=[action()])
    cfg = exchanges.config(OLD, NEW)
    dataset = margin.prices({OLD.asset_id: dict.fromkeys(STEPS, "100"), NEW.asset_id: {3.1: "200"}})

    def fresh() -> Any:
        return strategies(("S", Elector(exchanges.BUY, {2.5: 1})))

    whole = ReplayBacktest.run(cfg, dataset, fresh(), context_factory, schedule=schedule)
    assert took_cash(whole.backtest)
    reference = digest_run(whole.backtest).result_id
    for boundary in range(1, len(dataset.records)):
        paused = ReplayBacktest.run(
            cfg, dataset, fresh(), context_factory, schedule=schedule,
            pause_when=margin._at(boundary),
        )  # fmt: skip
        assert isinstance(paused, PausedReplay)
        payload = deserialize(serialize(capture_replay(paused)))
        objects = RunObjects(
            RuntimeObjects(
                cfg.pipeline.sizing_model,
                cfg.pipeline.simulator,
                {"S": Elector(exchanges.BUY, {2.5: 1})},
                cfg.pipeline.instruments,
            ),
            cfg.fill_policy,
        )
        restored = restore_replay(replay_from_primitives(payload), objects, dataset, schedule)
        resumed = ReplayBacktest.resume(restored, context_factory).backtest
        assert digest_run(resumed).result_id == reference, boundary


@pytest.mark.parametrize(
    "build",
    [
        lambda: action(alternatives=(STOCK,)),
        lambda: action(alternatives=(STOCK, HoldingExchange(NEW.asset_id, cash_per_unit=D("1")))),
        lambda: action(default=2),
        lambda: action(announced_at=2.9),
        lambda: action(deadline=3.1),
        lambda: action(payment_at=2.9),
        lambda: action(reference=""),
    ],
)
def test_an_elective_exchange_that_cannot_hold_is_refused(build: Any) -> None:
    with pytest.raises(ScheduleValidationError):
        build()


def test_an_adjustment_announced_after_it_takes_effect_is_refused() -> None:
    from tests.integration import test_option_lifecycle as lifecycle

    with pytest.raises(InstrumentInputError, match="announced"):
        TermsAdjustment(
            5.0, D("50"), Deliverable(lifecycle.SHARE.asset_id, D("200")), announced_at=6.0
        )


@pytest.mark.parametrize(
    "intent",
    [
        Intent("S", "X", D("1.5"), kind=IntentKind.ELECT, metadata={"action": REF}),
        Intent("S", "X", D("-1"), kind=IntentKind.ELECT, metadata={"action": REF}),
        Intent("S", "X", D("1"), kind=IntentKind.ELECT),
    ],
)
def test_an_election_that_names_no_action_or_alternative_is_invalid(intent: Intent) -> None:
    with pytest.raises(InvalidIntentError):
        validate_intent(intent)
