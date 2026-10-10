"""``start_strategy`` and ``context_factory``: the public path's two conveniences (v4.0).

Both remove hand-written boilerplate every example repeated, and neither adds an
authority: ``start_strategy`` is the supervisor's four transitions in order, and
``context_factory`` builds the context the pipeline overlays.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest

from alphalab.common.ids import current_id_position, id_scope, new_id
from alphalab.strategy import (
    BaseStrategy,
    DiscardingLogger,
    FixedClock,
    Intent,
    NoMarket,
    NoOrders,
    NoPortfolio,
    NoRiskView,
    RuntimeSupervisor,
    StrategyContext,
    StrategyStatus,
    StrategyValidationError,
    context_factory,
    create_runtime,
    register_strategy,
    start_strategy,
)


class Quiet(BaseStrategy):
    def on_bar(self, context: StrategyContext, event: Any) -> Iterable[Intent]:
        return ()


def test_start_strategy_is_the_supervisor_sequence() -> None:
    by_hand = register_strategy(create_runtime(), "A", Quiet())
    entry = by_hand.strategies["A"]
    entry, _ = RuntimeSupervisor.configure(entry, {"k": 1}, 5.0)
    entry, _ = RuntimeSupervisor.initialize(entry, 5.0)
    entry, _ = RuntimeSupervisor.subscribe(entry, frozenset({"bars"}), 5.0)
    entry, _ = RuntimeSupervisor.start(entry, 5.0)

    started = start_strategy(
        create_runtime(), "A", Quiet(), config={"k": 1}, subscriptions={"bars"}, at=5.0
    ).strategies["A"]

    assert started.status is StrategyStatus.RUNNING is entry.status
    assert started.config == entry.config
    assert started.subscriptions == entry.subscriptions == frozenset({"bars"})


def test_start_strategy_keeps_every_other_strategy() -> None:
    """Examples replaced the whole mapping with one entry; two strategies lost one."""

    state = start_strategy(
        create_runtime(), "A", Quiet(), config={}, subscriptions={"bars"}, at=1.0
    )
    state = start_strategy(state, "B", Quiet(), config={}, subscriptions={"bars"}, at=1.0)

    assert list(state.strategies) == ["A", "B"]
    assert all(entry.status is StrategyStatus.RUNNING for entry in state.strategies.values())


def test_start_strategy_draws_nothing_from_the_callers_identifier_stream() -> None:
    """Set up inside a run's seeded scope, it must not shift the run's identifiers."""

    with id_scope(7):
        before = current_id_position()
        start_strategy(create_runtime(), "A", Quiet(), config={}, subscriptions={"bars"}, at=1.0)
        assert current_id_position() == before
        inside = new_id()
    with id_scope(7):
        assert new_id() == inside


def test_start_strategy_refuses_an_identity_already_registered() -> None:
    """``register_strategy`` replaces an entry; starting a second one under its name is refused."""

    state = start_strategy(
        create_runtime(), "A", Quiet(), config={}, subscriptions={"bars"}, at=1.0
    )

    with pytest.raises(StrategyValidationError, match="already registered as 'A'"):
        start_strategy(state, "A", Quiet(), config={}, subscriptions={"bars"}, at=2.0)
    assert state.strategies["A"].status is StrategyStatus.RUNNING


def test_the_context_factory_carries_the_callers_clock_and_logger() -> None:
    clock, logger = FixedClock(1_700_000_000.0), DiscardingLogger()
    context = context_factory(clock, logger)("S-1")

    assert context.clock is clock and context.clock.now() == 1_700_000_000.0
    assert context.logger is logger
    assert dict(context.config) == {"strategy_id": "S-1"}
    assert isinstance(context.portfolio, NoPortfolio)
    assert isinstance(context.market, NoMarket)
    assert isinstance(context.risk_view, NoRiskView)
    assert isinstance(context.orders, NoOrders)
    with pytest.raises(TypeError):
        context.config["strategy_id"] = "other"


def test_the_discarding_logger_keeps_nothing() -> None:
    logger = DiscardingLogger()

    logger.info("x")
    logger.error("y")
    assert not hasattr(logger, "__dict__")


def test_a_fixed_clock_answers_one_instant_and_is_a_value() -> None:
    clock = FixedClock(1.5)

    assert [clock.now(), clock.now()] == [1.5, 1.5]
    assert clock == FixedClock(1.5) and clock != FixedClock(2.5)


def test_start_strategy_names_a_bare_string_of_subscriptions() -> None:
    """``"bars"`` is an ``Iterable[str]`` of four one-letter topics; say so, not ``'b'``."""

    with pytest.raises(StrategyValidationError, match=r"subscriptions is the string 'bars'"):
        start_strategy(
            create_runtime(),
            "S-1",
            Quiet(),
            config={},
            subscriptions="bars",
            at=1.0,
        )
