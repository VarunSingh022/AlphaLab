"""An order that ends without trading still ends, and its ledger entry with it.

``_release_if_terminal`` retires both allocation ledgers -- the capital
reservation and the contribution entry recording which strategies asked -- and
until v2.8 it was the only thing that retired contributions at all. It is called
from inside ``_apply_reports``'s ``for report in reports`` loop, so an order
reaching a terminal state by any route that produces no report never reached it.

Four such routes existed. ``NO_FILL``, ``REJECTED`` and ``EXPIRED`` produce no
report, so the loop body never ran even though ``_close_unfilled_order`` had
already moved the order to a terminal state. A partially filled order was
cancelled by ``_withdraw_partial_remainder``, which freed the reservation and
nothing else. Measured at v2.7.0 and again on this build before the fix, twenty
events on each path::

    NO_FILL                     reservations 0   contributions 20   oms CANCELLED
    REJECTED                    reservations 0   contributions 20   oms REJECTED
    EXPIRED                     reservations 0   contributions 20   oms EXPIRED
    PARTIAL_FILL -> withdrawn   reservations 0   contributions 20   oms CANCELLED
    FULL_FILL (control)         reservations 0   contributions  0   oms FILLED

Every terminal transition now goes through ``_release_if_terminal``, so
"terminal" has one meaning and one consequence. These tests drive the real
pipeline rather than calling the retirement helper, because the defect was never
in the helper -- it was in which paths reached it.

Companion to ``test_contributions_are_retired``, which covers the two pre-OMS
paths where there is no order to be terminal.
"""

from decimal import Decimal
from uuid import uuid4

import pytest

from alphalab.execution.fill import FillStatus
from alphalab.execution.policy import LiquidityCappedFill
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.slippage import PercentageSlippage
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineState
from alphalab.strategy.state import RuntimeState as StrategyRuntimeState
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    quote,
    running_strategy_state,
    sized_quote,
)

EVENTS = 20

#: Slippage wide enough that a capped fill prices away from the reference, which
#: is what leaves a partially filled order with a remainder to withdraw.
_ADVERSE = ExecutionSimulator(slippage_model=PercentageSlippage(Decimal("0.10")))


def _running(strategy_id: str, asset_id: str) -> StrategyRuntimeState:
    plan = {2.0 + index: Decimal("1") for index in range(EVENTS)}
    return running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, plan))


def _run(
    *,
    fill_status: FillStatus | None = None,
    partial: bool = False,
    events: int = EVENTS,
) -> ExecutionPipelineState:
    """Drive the real pipeline to the requested terminal outcome, ``events`` times."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())
    state = ExecutionPipeline.initialize(
        pipeline_config(strategy_id, simulator=_ADVERSE if partial else None),
        _running(strategy_id, asset_id),
        1.0,
    )
    for index in range(events):
        if partial:
            market = sized_quote(asset_id, 2.0 + index, Decimal("100"), Decimal("0.4"))
            state = ExecutionPipeline.process_quote(
                state, market, context_factory, fill_policy=LiquidityCappedFill()
            ).state
        else:
            market = quote(asset_id, 2.0 + index, Decimal("100"))
            assert fill_status is not None
            state = ExecutionPipeline.process_quote(
                state, market, context_factory, fill_status=fill_status
            ).state
    return state


def _statuses(state: ExecutionPipelineState) -> set[str]:
    return {order.status.name for order in state.oms.orders.orders()}


# --------------------------------------------------------------------------- #
# The three non-trading terminal outcomes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("fill_status", "expected_status"),
    [
        (FillStatus.NO_FILL, "CANCELLED"),
        (FillStatus.REJECTED, "REJECTED"),
        (FillStatus.EXPIRED, "EXPIRED"),
    ],
)
def test_a_non_trading_outcome_retires_its_contributions(
    fill_status: FillStatus, expected_status: str
) -> None:
    state = _run(fill_status=fill_status)

    assert _statuses(state) == {expected_status}
    assert len(state.fills) == 0
    assert dict(state.allocation.contributions) == {}


@pytest.mark.parametrize(
    "fill_status", [FillStatus.NO_FILL, FillStatus.REJECTED, FillStatus.EXPIRED]
)
def test_a_non_trading_outcome_still_releases_its_reservation(
    fill_status: FillStatus,
) -> None:
    """The half that already worked must keep working."""

    state = _run(fill_status=fill_status)

    assert dict(state.allocation.reservations) == {}
    assert state.allocation.notional_allocated == Decimal("0.00")


# --------------------------------------------------------------------------- #
# The withdrawn remainder of a partial fill
# --------------------------------------------------------------------------- #


def test_a_withdrawn_partial_fill_retires_its_contributions() -> None:
    """The fourth path: it traded, so it is terminal by cancellation, not by report."""

    state = _run(partial=True)

    assert _statuses(state) == {"CANCELLED"}
    assert len(state.fills) == EVENTS, "a partial fill is still a fill"
    assert dict(state.allocation.reservations) == {}
    assert dict(state.allocation.contributions) == {}


# --------------------------------------------------------------------------- #
# Boundedness, and the control
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "fill_status", [FillStatus.NO_FILL, FillStatus.REJECTED, FillStatus.EXPIRED]
)
def test_neither_ledger_grows_with_the_event_count(fill_status: FillStatus) -> None:
    short = _run(fill_status=fill_status, events=4)
    long = _run(fill_status=fill_status, events=40)

    assert len(short.allocation.contributions) == len(long.allocation.contributions) == 0
    assert len(short.allocation.reservations) == len(long.allocation.reservations) == 0


def test_a_full_fill_still_retires_exactly_where_it_always_did() -> None:
    state = _run(fill_status=FillStatus.FULL_FILL)

    assert _statuses(state) == {"FILLED"}
    assert len(state.fills) == EVENTS
    assert dict(state.allocation.reservations) == {}
    assert dict(state.allocation.contributions) == {}


# --------------------------------------------------------------------------- #
# Exactly once, and nothing else moved
# --------------------------------------------------------------------------- #


def test_retirement_happens_once_and_never_raises_on_a_second_pass() -> None:
    """Both underlying operations are idempotent, so a repeat is a no-op.

    ``_release_if_terminal`` is reachable more than once for one order -- once
    per report and again at withdrawal -- so this is the property that makes
    calling it at every terminal transition safe.
    """

    from alphalab.allocation.engine import AllocationEngine
    from alphalab.runtime.execution_pipeline import _release_if_terminal

    state = _run(fill_status=FillStatus.NO_FILL, events=1)
    order = next(iter(state.oms.orders.orders()))

    again = _release_if_terminal(state, order.order_id, 99.0)

    assert dict(again.allocation.contributions) == {}
    assert dict(again.allocation.reservations) == {}
    assert again.allocation.notional_allocated == state.allocation.notional_allocated
    assert AllocationEngine.contributions_for(state.allocation, str(order.order_id.value)) == ()


def test_attribution_is_unchanged_because_it_is_read_before_retirement() -> None:
    """Retiring on more paths must not retire before post-trade attribution reads."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())
    plan = {2.0: Decimal("1")}
    state = ExecutionPipeline.initialize(
        pipeline_config(strategy_id),
        running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, plan)),
        1.0,
    )

    result = ExecutionPipeline.process_quote(
        state, quote(asset_id, 2.0, Decimal("100")), context_factory
    )

    record = result.state.trade_records[-1]
    assert [contribution.strategy_id for contribution in record.contributions] == [strategy_id]
    assert dict(result.state.allocation.contributions) == {}, "retired after it was read"


def test_a_withdrawn_partial_fill_still_attributes_the_part_that_traded() -> None:
    state = _run(partial=True, events=1)

    record = state.trade_records[-1]
    assert record.contributions, "the executed quantity still names who asked for it"
    assert dict(state.allocation.contributions) == {}


def test_no_fill_or_order_is_created_or_destroyed_by_the_change() -> None:
    """Retiring a ledger entry must move no money and no order.

    Pins the figures the change must not touch, on the path it changed most.
    """

    state = _run(partial=True)

    assert len(state.fills) == EVENTS
    assert len(state.trades) == EVENTS
    assert len(state.trade_records) == EVENTS
    assert len(list(state.oms.orders.orders())) == EVENTS
    assert state.portfolio.positions
    assert state.portfolio_snapshots[-1].total_equity == state.portfolio_snapshots[-1].cash + sum(
        (position.market_value for position in state.portfolio.positions.values()),
        Decimal("0.00"),
    )
