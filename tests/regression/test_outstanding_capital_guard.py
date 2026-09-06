"""Capital committed to unsettled orders counts against the budget.

Before v2.6 the budget guard compared only the batch's own notional against
``available_global_capital``. ``notional_allocated`` -- the total of every
reservation still held -- did not appear in the expression, so the whole budget
was re-offered on every market event however much was already committed.

Under ``SIMULATED`` routing that was invisible, because a reservation is
consumed or released inside the event that created it. Under ``EXTERNAL``
routing an accepted order stays working and keeps its reservation by design
(ADR-0012), and nothing stopped the next event committing the budget again.

Risk cannot catch this and is not asked to: ``_sync_risk_from_portfolio``
recomputes exposure from settled positions and buying power from the cash ledger
on every event, so with no fill it sees a flat, fully funded account. Allocation
owns unsettled commitment; risk owns settled exposure. See ADR-0015 decision 2.
"""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.allocation.engine import AllocationEngine
from alphalab.allocation.events import BudgetExceeded
from alphalab.allocation.sizing import FixedQuantitySizing
from alphalab.allocation.state import AllocationState
from alphalab.core.order_request import OrderRequest
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineState,
    ExecutionRouting,
)
from alphalab.strategy.events import Intent
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    quote,
    running_strategy_state,
)

BUDGET = Decimal("1000000")
REFERENCE = Decimal("100")
SHARES = Decimal("9000")  # 900,000 notional: fits once, not twice


def _budget_exceeded(state: ExecutionPipelineState) -> list[BudgetExceeded]:
    return [event for event in state.allocation.events if isinstance(event, BudgetExceeded)]


# ---------------------------------------------------------------------------
# The EXTERNAL reproduction from the archaeology
# ---------------------------------------------------------------------------


def _external_run(events: int) -> ExecutionPipelineState:
    strategy_id, asset_id = "S1", str(uuid4())
    plan = {float(t): SHARES for t in range(2, 2 + events)}
    config = replace(pipeline_config(strategy_id, BUDGET), routing=ExecutionRouting.EXTERNAL)
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, plan)),
        1.0,
    )
    for timestamp in range(2, 2 + events):
        state = ExecutionPipeline.process_quote(
            state, quote(asset_id, float(timestamp), REFERENCE), context_factory
        ).state
    return state


def test_six_external_events_no_longer_commit_five_times_the_budget() -> None:
    """The measured v2.5.0 defect: 6 working orders, 5,400,000 reserved, 0 refusals."""

    state = _external_run(6)

    assert state.portfolio.positions == {}, "no fill occurred; this is pure commitment"
    assert state.allocation.notional_allocated == Decimal("900000.000000")
    assert len(state.allocation.reservations) == 1
    assert len(_budget_exceeded(state)) == 5


def test_the_second_over_budget_allocation_is_refused() -> None:
    """The first 900,000 fits; the second would make 1,800,000 and does not."""

    first = _external_run(1)
    second = _external_run(2)

    assert first.allocation.notional_allocated == Decimal("900000.000000")
    assert not _budget_exceeded(first)

    assert second.allocation.notional_allocated == Decimal("900000.000000")
    assert len(_budget_exceeded(second)) == 1


def test_outstanding_commitment_never_exceeds_the_configured_budget() -> None:
    for events in range(1, 8):
        state = _external_run(events)
        assert state.allocation.notional_allocated <= BUDGET


def test_risk_still_approves_because_it_owns_settled_exposure_only() -> None:
    """The guard is allocation's, and risk is deliberately unaware of it."""

    state = _external_run(2)

    assert state.risk.buying_power == BUDGET, "no fill, so cash is untouched"
    assert state.risk.exposure.gross_exposure == Decimal("0.00")


# ---------------------------------------------------------------------------
# Atomic rejection, and what the event reports
# ---------------------------------------------------------------------------


def _allocate(
    state: AllocationState, shares: Decimal, timestamp: float = 1.0
) -> tuple[AllocationState, tuple[OrderRequest, ...]]:
    return AllocationEngine.allocate(
        state,
        (Intent("STRAT-A", "AAPL", shares),),
        {"AAPL": REFERENCE},
        FixedQuantitySizing(),
        AllocationConstraints(),
        timestamp,
    )


def _committed_state() -> AllocationState:
    state = AllocationEngine.initialize(
        CapitalBudget(global_capital=BUDGET, maximum_exposure=BUDGET * Decimal("10"))
    )
    committed, orders = _allocate(state, SHARES)
    assert orders
    return committed


def test_a_refused_batch_changes_no_prior_reservation() -> None:
    """``history``, ``reservations`` and the total are untouched; only events grow."""

    before = _committed_state()
    after, orders = _allocate(before, SHARES, timestamp=2.0)

    assert orders == ()
    assert after.notional_allocated == before.notional_allocated
    assert dict(after.reservations) == dict(before.reservations)
    assert after.history.to_tuple() == before.history.to_tuple()
    assert len(after.events) > len(before.events)


def test_the_event_reports_the_remaining_budget_not_the_configured_one() -> None:
    """900,000 is committed, so 100,000 remains -- and that is what is reported."""

    after, _ = _allocate(_committed_state(), SHARES, timestamp=2.0)
    event = [e for e in after.events if isinstance(e, BudgetExceeded)][-1]

    assert event.requested_notional == Decimal("900000.000000")
    assert event.available_budget == Decimal("100000.000000")
    assert event.available_budget != BUDGET


def test_a_batch_that_fits_the_remaining_budget_is_accepted() -> None:
    """The guard refuses what does not fit, not everything after the first."""

    after, orders = _allocate(_committed_state(), Decimal("500"), timestamp=2.0)

    assert len(orders) == 1
    assert after.notional_allocated == Decimal("950000.000000")


def test_releasing_a_commitment_restores_the_budget() -> None:
    """Commitment is outstanding, not historical: freeing it frees the budget."""

    committed = _committed_state()
    order_id = committed.history.to_tuple()[0].order_id
    released = AllocationEngine.release_reservation(committed, order_id, 3.0)

    after, orders = _allocate(released, SHARES, timestamp=4.0)

    assert len(orders) == 1
    assert not [e for e in after.events if isinstance(e, BudgetExceeded)][1:]


def test_the_maximum_exposure_ceiling_also_counts_outstanding_commitment() -> None:
    state = AllocationEngine.initialize(
        CapitalBudget(global_capital=BUDGET * Decimal("100"), maximum_exposure=Decimal("1000000"))
    )
    committed, orders = _allocate(state, SHARES)
    assert orders

    after, refused = _allocate(committed, SHARES, timestamp=2.0)

    assert refused == ()
    assert after.notional_allocated == Decimal("900000.000000")


def test_the_guard_does_not_partially_size_a_batch() -> None:
    """Whole-batch rejection: no order is emitted at a reduced quantity."""

    after, orders = _allocate(_committed_state(), SHARES, timestamp=2.0)

    assert orders == ()
    assert len(after.history.to_tuple()) == 1, "only the accepted batch is in history"
