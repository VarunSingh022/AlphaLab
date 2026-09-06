"""A request that never becomes an order still ends, and its ledger entry with it.

Allocation opens two entries per emitted request: a reservation for the capital
it holds, and a contribution entry recording which strategies asked for it.
``AllocationState.contributions`` describes its own lifetime as running "from
the moment allocation emits a request until that request's order reaches a
terminal state" -- which assumes every request becomes an order.

Two do not. A request for an asset the run never priced is dropped before the
OMS, and a request risk refuses never reaches it either. ``_release_if_terminal``
retires both ledgers, but it returns early unless the order is in the OMS book,
so for those two paths the reservation was freed and the contribution entry was
immortal.

Measured at v2.7.0, forty events on each path::

    unpriced      reservations 0   contributions 40
    risk-rejected reservations 0   contributions 40

Growth with no bound and no reader. Both are now retired where the request's
life actually ends. The invariant is restated in terms of the *request*'s
lifecycle rather than its order's, because a request that never becomes an order
still finishes.
"""

from decimal import Decimal
from uuid import uuid4

from alphalab.execution.fill import FillStatus
from alphalab.runtime.execution_pipeline import ExecutionPipeline
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    permissive_risk_limits,
    pipeline_config,
    quote,
    running_strategy_state,
)

EVENTS = 40


def _running(strategy_id: str, asset_id: str, plan, asset_for=None) -> StrategyRuntimeState:  # type: ignore[no-untyped-def]
    return running_strategy_state(
        strategy_id, ScriptedStrategy(strategy_id, asset_id, plan, asset_for)
    )


def _drive(state, asset_id: str, events: int, **kwargs):  # type: ignore[no-untyped-def]
    for index in range(events):
        state = ExecutionPipeline.process_quote(
            state, quote(asset_id, 2.0 + index, Decimal("100")), context_factory, **kwargs
        ).state
    return state


# --------------------------------------------------------------------------- #
# The two paths that leaked
# --------------------------------------------------------------------------- #


def test_unpriced_requests_retire_their_contributions() -> None:
    strategy_id, priced, never = str(uuid4()), str(uuid4()), str(uuid4())
    plan = {2.0 + index: Decimal("1") for index in range(EVENTS)}
    asset_for = {2.0 + index: never for index in range(EVENTS)}

    state = ExecutionPipeline.initialize(
        pipeline_config(strategy_id), _running(strategy_id, priced, plan, asset_for), 1.0
    )
    state = _drive(state, priced, EVENTS)

    assert len(state.fills) == 0
    assert dict(state.allocation.reservations) == {}
    assert dict(state.allocation.contributions) == {}
    assert state.unpriced_assets[never].occurrences == EVENTS


def test_risk_rejected_requests_retire_their_contributions() -> None:
    strategy_id, asset_id = str(uuid4()), str(uuid4())
    plan = {2.0 + index: Decimal("50") for index in range(EVENTS)}

    state = ExecutionPipeline.initialize(
        pipeline_config(strategy_id, risk_limits=permissive_risk_limits(Decimal("1"))),
        _running(strategy_id, asset_id, plan),
        1.0,
    )
    rejections = 0
    for index in range(EVENTS):
        result = ExecutionPipeline.process_quote(
            state, quote(asset_id, 2.0 + index, Decimal("100")), context_factory
        )
        rejections += sum(1 for decision in result.risk_decisions if not decision.approved)
        state = result.state

    assert rejections == EVENTS
    assert len(state.fills) == 0
    assert dict(state.allocation.reservations) == {}
    assert dict(state.allocation.contributions) == {}


def test_neither_ledger_grows_with_the_event_count() -> None:
    """The point of the fix: length is independent of how long the run went on."""

    strategy_id, priced, never = str(uuid4()), str(uuid4()), str(uuid4())

    def run(events: int) -> int:
        plan = {2.0 + index: Decimal("1") for index in range(events)}
        asset_for = {2.0 + index: never for index in range(events)}
        state = ExecutionPipeline.initialize(
            pipeline_config(strategy_id), _running(strategy_id, priced, plan, asset_for), 1.0
        )
        return len(_drive(state, priced, events).allocation.contributions)

    assert run(5) == run(50) == 0


# --------------------------------------------------------------------------- #
# What must not change
# --------------------------------------------------------------------------- #


def test_a_filled_order_still_retires_its_contributions_where_it_always_did() -> None:
    strategy_id, asset_id = str(uuid4()), str(uuid4())
    plan = {2.0 + index: Decimal("1") for index in range(4)}

    state = ExecutionPipeline.initialize(
        pipeline_config(strategy_id), _running(strategy_id, asset_id, plan), 1.0
    )
    state = _drive(state, asset_id, 4)

    assert len(state.fills) == 4
    assert dict(state.allocation.reservations) == {}
    assert dict(state.allocation.contributions) == {}


def test_attribution_still_reads_the_contributions_before_they_are_retired() -> None:
    """Retiring earlier must not retire before post-trade attribution reads it."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())
    plan = {2.0: Decimal("1")}

    state = ExecutionPipeline.initialize(
        pipeline_config(strategy_id), _running(strategy_id, asset_id, plan), 1.0
    )
    result = ExecutionPipeline.process_quote(
        state, quote(asset_id, 2.0, Decimal("100")), context_factory
    )

    record = result.state.trade_records[-1]
    assert [contribution.strategy_id for contribution in record.contributions] == [strategy_id]


def test_the_reservation_ledger_is_unaffected_by_the_change() -> None:
    """Reservations were already released on both paths; that is unchanged."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())
    plan = {2.0 + index: Decimal("1") for index in range(3)}

    state = ExecutionPipeline.initialize(
        pipeline_config(strategy_id), _running(strategy_id, asset_id, plan), 1.0
    )
    state = _drive(state, asset_id, 3, fill_status=FillStatus.NO_FILL)

    assert dict(state.allocation.reservations) == {}
    assert state.allocation.notional_allocated == Decimal("0.00")
