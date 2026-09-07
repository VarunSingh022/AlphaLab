"""A venue fill delivered twice is applied once.

``ExecutionPipeline.apply_execution_report`` is the seam a real venue arrives
through, and its own docstring promised that a venue fill and a simulated one
reach the OMS, the portfolio, the allocation ledger and the analytics record by
the same route. It listed four things that were identical and omitted the fifth
that was not: ``_apply_reports`` never wrote to
:class:`~alphalab.execution.state.ExecutionState`. The simulated path recorded a
report because :meth:`~alphalab.execution.engine.ExecutionEngine.simulate` runs
first; the venue path recorded nothing, so the ``reports`` map -- keyed by
execution id, which is exactly the ledger a duplicate check reads -- stayed empty
on the one path where redelivery happens.

One report, one ``execution_id``, delivered twice therefore applied twice.
Measured on v2.8.0 for a partial fill: cash 999,600 -> 999,200, position 4 -> 8,
``filled_quantity`` 4 -> 8, two pipeline fills for one venue execution. A *full*
fill was caught, but only incidentally -- the second application asked the OMS to
fill an order already ``FILLED`` and got ``InvalidTransitionError`` -- so the
protection was a side effect of a state machine rather than an answer to the
question, and it did not exist for the case that actually matters.

:mod:`alphalab.broker.reconciliation` had the answer all along: a repeated
``execution_id`` is ``DUPLICATE``, "ignored, and the state is returned
unchanged... a no-op rather than an error because a reconnect makes it routine".
It classifies against ``BrokerState`` and the pipeline never consulted it. The
pipeline now enforces the same rule over its own ledger, without importing the
broker package.

The identity is the ``execution_id`` and nothing else, which
``test_two_reports_differing_only_in_execution_id_both_apply`` holds: a
legitimate sequence of partial fills can repeat every other field.
"""

import uuid
from dataclasses import replace
from decimal import Decimal

import pytest

from alphalab.execution.fill import FillStatus
from alphalab.execution.report import ExecutionReport
from alphalab.oms.order import Order as OMSOrder
from alphalab.oms.status import OrderStatus
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineState,
    ExecutionRouting,
)
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    running_strategy_state,
    sized_quote,
)

STRATEGY_ID = str(uuid.uuid4())
ASSET_ID = str(uuid.uuid4())


def _working(quantity: str = "10") -> tuple[ExecutionPipelineState, OMSOrder]:
    """A pipeline with one order left working by EXTERNAL routing, as live does."""

    config = replace(pipeline_config(STRATEGY_ID), routing=ExecutionRouting.EXTERNAL)
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(
            STRATEGY_ID, ScriptedStrategy(STRATEGY_ID, ASSET_ID, {2.0: Decimal(quantity)})
        ),
        1.0,
    )
    state = ExecutionPipeline.process_quote(
        state, sized_quote(ASSET_ID, 2.0, Decimal("100"), Decimal("100")), context_factory
    ).state
    return state, next(iter(state.oms.orders.open_orders()))


def _report(
    order: OMSOrder,
    execution_id: str,
    quantity: str,
    status: FillStatus = FillStatus.PARTIAL_FILL,
) -> ExecutionReport:
    """A venue fill, as ``execution_report_from_broker`` would build one."""

    return ExecutionReport(
        execution_id=execution_id,
        order_id=str(order.order_id.value),
        asset_id=ASSET_ID,
        strategy_id="",
        timestamp=3.0,
        fill_price=Decimal("100"),
        fill_quantity=Decimal(quantity),
        commission=Decimal("0"),
        slippage=Decimal("0"),
        liquidity_flag="",
        venue="LIVE",
        currency="USD",
        status=status,
    )


def _financials(state: ExecutionPipelineState, order: OMSOrder) -> dict[str, object]:
    """Every value a duplicated execution would move."""

    current = state.oms.orders.find(order.order_id)
    position = state.portfolio.positions.get(ASSET_ID)
    return {
        "cash": state.portfolio.cash.balance("USD"),
        "position": position.quantity if position is not None else Decimal("0"),
        "realized_pnl": state.portfolio.realized_pnl,
        "commission_paid": state.portfolio.commission_paid,
        "filled_quantity": current.filled_quantity,
        "remaining_quantity": current.remaining_quantity,
        "average_fill_price": current.average_fill_price,
        "status": current.status,
        "fills": len(state.fills),
        "trades": len(state.trades),
        "trade_records": len(state.trade_records),
        "ledger": len(state.execution.reports),
        "history": len(state.execution.history),
    }


# ---------------------------------------------------------------------------
# First delivery: applied, and recorded
# ---------------------------------------------------------------------------


def test_a_venue_fill_is_applied_once() -> None:
    state, order = _working()

    applied, fills, trades = ExecutionPipeline.apply_execution_report(
        state, order, _report(order, str(uuid.uuid4()), "4")
    )

    assert len(fills) == 1
    assert len(trades) == 1
    assert applied.portfolio.cash.balance("USD") == Decimal("999600.00")
    assert applied.portfolio.positions[ASSET_ID].quantity == Decimal("4")
    assert applied.oms.orders.find(order.order_id).filled_quantity == Decimal("4")


def test_the_venue_path_now_records_the_report_in_the_execution_ledger() -> None:
    """The defect's root cause: this map was empty on the one path that redelivers."""

    state, order = _working()
    execution_id = str(uuid.uuid4())

    assert len(state.execution.reports) == 0

    applied, _, _ = ExecutionPipeline.apply_execution_report(
        state, order, _report(order, execution_id, "4")
    )

    assert set(applied.execution.reports) == {execution_id}
    assert applied.execution.reports[execution_id].fill_quantity == Decimal("4")


def test_the_venue_path_records_the_history_and_event_a_simulated_fill_does() -> None:
    state, order = _working()

    applied, _, _ = ExecutionPipeline.apply_execution_report(
        state, order, _report(order, str(uuid.uuid4()), "4")
    )

    assert len(applied.execution.history) == 1
    assert [type(e).__name__ for e in applied.execution.events] == ["ExecutionPartiallyFilled"]


def test_a_full_venue_fill_records_the_completed_event() -> None:
    state, order = _working("5")

    applied, _, _ = ExecutionPipeline.apply_execution_report(
        state, order, _report(order, str(uuid.uuid4()), "5", FillStatus.FULL_FILL)
    )

    assert [type(e).__name__ for e in applied.execution.events] == ["ExecutionCompleted"]
    assert applied.oms.orders.find(order.order_id).status is OrderStatus.FILLED


# ---------------------------------------------------------------------------
# Redelivery: a no-op, for a partial fill above all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quantity", "status"),
    [("4", FillStatus.PARTIAL_FILL), ("10", FillStatus.FULL_FILL)],
)
def test_redelivering_one_report_changes_nothing(quantity: str, status: FillStatus) -> None:
    """The whole defect, in one assertion, for both fill statuses.

    The partial case is the one that mattered: a full fill was already refused by
    the OMS state machine, and a partial fill was not refused by anything.
    """

    state, order = _working()
    report = _report(order, str(uuid.uuid4()), quantity, status)

    first, _, _ = ExecutionPipeline.apply_execution_report(state, order, report)
    before = _financials(first, order)

    again, fills, trades = ExecutionPipeline.apply_execution_report(
        first, first.oms.orders.find(order.order_id), report
    )

    assert _financials(again, order) == before
    assert fills == ()
    assert trades == ()


def test_redelivery_does_not_raise() -> None:
    """A reconnect makes redelivery routine, so it must not be an error."""

    state, order = _working()
    report = _report(order, str(uuid.uuid4()), "10", FillStatus.FULL_FILL)
    first, _, _ = ExecutionPipeline.apply_execution_report(state, order, report)

    again, _, _ = ExecutionPipeline.apply_execution_report(
        first, first.oms.orders.find(order.order_id), report
    )

    assert again.oms.orders.find(order.order_id).status is OrderStatus.FILLED


def test_redelivery_does_not_grow_the_ledger() -> None:
    state, order = _working()
    report = _report(order, str(uuid.uuid4()), "4")
    first, _, _ = ExecutionPipeline.apply_execution_report(state, order, report)

    again, _, _ = ExecutionPipeline.apply_execution_report(
        first, first.oms.orders.find(order.order_id), report
    )

    assert len(again.execution.reports) == len(first.execution.reports) == 1
    assert len(again.execution.history) == len(first.execution.history) == 1
    assert len(again.execution.events) == len(first.execution.events) == 1


def test_redelivery_returns_the_state_it_was_given() -> None:
    """Nothing downstream is replayed, so no new state object is built."""

    state, order = _working()
    report = _report(order, str(uuid.uuid4()), "4")
    first, _, _ = ExecutionPipeline.apply_execution_report(state, order, report)

    again, _, _ = ExecutionPipeline.apply_execution_report(
        first, first.oms.orders.find(order.order_id), report
    )

    assert again is first


def test_redelivery_leaves_the_allocation_ledgers_alone() -> None:
    state, order = _working()
    report = _report(order, str(uuid.uuid4()), "4")
    first, _, _ = ExecutionPipeline.apply_execution_report(state, order, report)

    again, _, _ = ExecutionPipeline.apply_execution_report(
        first, first.oms.orders.find(order.order_id), report
    )

    assert dict(again.allocation.reservations) == dict(first.allocation.reservations)
    assert dict(again.allocation.contributions) == dict(first.allocation.contributions)
    assert again.allocation.notional_allocated == first.allocation.notional_allocated


# ---------------------------------------------------------------------------
# The partial-fill sequence the design review specified
# ---------------------------------------------------------------------------


def test_the_partial_fill_redelivery_sequence() -> None:
    """E1 applies, E1 is a no-op, E2 applies, and both stay no-ops afterwards."""

    state, order = _working()
    first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())

    def deliver(
        current: ExecutionPipelineState, execution_id: str, quantity: str
    ) -> tuple[ExecutionPipelineState, int]:
        nxt, fills, _ = ExecutionPipeline.apply_execution_report(
            current,
            current.oms.orders.find(order.order_id),
            _report(order, execution_id, quantity),
        )
        return nxt, len(fills)

    state, applied_e1 = deliver(state, first_id, "4")
    after_e1 = _financials(state, order)

    state, replayed_e1 = deliver(state, first_id, "4")
    assert _financials(state, order) == after_e1

    state, applied_e2 = deliver(state, second_id, "3")
    after_e2 = _financials(state, order)

    state, replayed_e1_again = deliver(state, first_id, "4")
    assert _financials(state, order) == after_e2

    state, replayed_e2 = deliver(state, second_id, "3")
    assert _financials(state, order) == after_e2

    assert (applied_e1, replayed_e1, applied_e2, replayed_e1_again, replayed_e2) == (1, 0, 1, 0, 0)
    assert set(state.execution.reports) == {first_id, second_id}
    assert state.oms.orders.find(order.order_id).filled_quantity == Decimal("7")
    assert state.portfolio.positions[ASSET_ID].quantity == Decimal("7")
    assert len(state.fills) == 2


# ---------------------------------------------------------------------------
# The identity is the execution_id, and nothing else
# ---------------------------------------------------------------------------


def test_two_reports_differing_only_in_execution_id_both_apply() -> None:
    """Deduplication must be by identity, never by content.

    A venue filling one order in equal slices at one price reports exactly this,
    and refusing the second would lose a real fill.
    """

    state, order = _working()
    first = _report(order, str(uuid.uuid4()), "4")
    second = _report(order, str(uuid.uuid4()), "4")

    assert first.execution_id != second.execution_id
    assert (first.order_id, first.asset_id, first.timestamp) == (
        second.order_id,
        second.asset_id,
        second.timestamp,
    )
    assert (first.fill_price, first.fill_quantity, first.commission, first.status) == (
        second.fill_price,
        second.fill_quantity,
        second.commission,
        second.status,
    )

    applied, fills_one, _ = ExecutionPipeline.apply_execution_report(state, order, first)
    applied, fills_two, _ = ExecutionPipeline.apply_execution_report(
        applied, applied.oms.orders.find(order.order_id), second
    )

    assert len(fills_one) == 1
    assert len(fills_two) == 1
    assert len(applied.execution.reports) == 2
    assert applied.oms.orders.find(order.order_id).filled_quantity == Decimal("8")
    assert applied.portfolio.positions[ASSET_ID].quantity == Decimal("8")
    assert applied.portfolio.cash.balance("USD") == Decimal("999200.00")


def test_the_guard_reads_the_execution_ledger_and_no_second_index() -> None:
    """One ledger, one identity key. A duplicate is invisible until it is recorded."""

    state, order = _working()
    execution_id = str(uuid.uuid4())
    report = _report(order, execution_id, "4")

    assert execution_id not in state.execution.reports
    applied, _, _ = ExecutionPipeline.apply_execution_report(state, order, report)
    assert execution_id in applied.execution.reports

    again, fills, _ = ExecutionPipeline.apply_execution_report(
        applied, applied.oms.orders.find(order.order_id), report
    )
    assert fills == ()
    assert again is applied


# ---------------------------------------------------------------------------
# The simulated path is untouched
# ---------------------------------------------------------------------------


def test_the_simulated_path_still_records_every_fill() -> None:
    """It recorded reports before this change and must still record them once each.

    Its reports are stored by ``ExecutionEngine.simulate`` *before* the economics
    are applied, so it cannot be guarded on ledger membership -- and needs no
    guard, because the simulator mints a fresh execution id per event.
    """

    state = ExecutionPipeline.initialize(
        pipeline_config(STRATEGY_ID),
        running_strategy_state(
            STRATEGY_ID,
            ScriptedStrategy(STRATEGY_ID, ASSET_ID, {2.0 + i: Decimal("5") for i in range(3)}),
        ),
        1.0,
    )
    for index in range(3):
        state = ExecutionPipeline.process_quote(
            state,
            sized_quote(ASSET_ID, 2.0 + index, Decimal("100"), Decimal("100")),
            context_factory,
        ).state

    assert len(state.execution.reports) == 3
    assert len(state.execution.history) == 3
    assert len(state.fills) == 3


def test_the_pipeline_does_not_import_the_broker_package_to_deduplicate() -> None:
    """The pipeline enforces its own state invariant; the dependency runs the other way.

    Asserted over the module's imports rather than its text, because the
    docstrings discuss ``broker.reconciliation`` deliberately -- the rule is
    mirrored from it, and saying so is the point. What must not exist is the
    dependency.
    """

    import ast
    import inspect

    from alphalab.runtime import execution_pipeline

    tree = ast.parse(inspect.getsource(execution_pipeline))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported, "the module imports nothing; the walk is wrong, not the module"
    assert not [name for name in imported if name.startswith("alphalab.broker")]
    assert not hasattr(execution_pipeline, "classify_execution")
