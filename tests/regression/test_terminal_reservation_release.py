"""A terminal order holds no reservation, whatever price it filled at.

A reservation is ``quantity * reference_price``; consumption is
``fill_quantity * execution_price``. Those are different units of account, so
``min(reserved, executed_notional)`` strands a residual when the fill was
cheaper than the reference and silently discards the excess when it was dearer.

Before v2.6 nothing freed the stranded residual: a *partially* filled order is
withdrawn by ``_withdraw_partial_remainder``, but an order filled in full
reaches ``FILLED`` -- terminal -- still holding capital committed to nothing,
and every later event added more. The leak was invisible only because nothing
read ``notional_allocated`` to decide anything; ADR-0015 makes it load-bearing.

These tests are deliberately not written in terms of slippage. Slippage is the
most common *source* of a price divergence; the condition is the divergence,
whichever side produced it -- which is why the EXTERNAL cases below configure no
slippage model at all and let the venue name the price.
"""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from alphalab.allocation.engine import AllocationEngine
from alphalab.allocation.exceptions import UnknownReservationError
from alphalab.execution.fill import FillStatus
from alphalab.execution.policy import LiquidityCappedFill
from alphalab.execution.report import ExecutionReport
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.slippage import PercentageSlippage
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineResult,
    ExecutionPipelineState,
    ExecutionRouting,
)
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    permissive_risk_limits,
    pipeline_config,
    quote,
    running_strategy_state,
    sized_quote,
)

REFERENCE = Decimal("100")
_ADVERSE = ExecutionSimulator(slippage_model=PercentageSlippage(Decimal("0.01")))


def _run(
    plan: dict[float, Decimal],
    *,
    simulator: ExecutionSimulator | None = None,
    routing: ExecutionRouting = ExecutionRouting.SIMULATED,
    risk_limits: object | None = None,
    **kwargs: object,
) -> tuple[ExecutionPipelineResult, str]:
    strategy_id, asset_id = "S1", str(uuid4())
    config = replace(
        pipeline_config(
            strategy_id,
            Decimal("1000000"),
            simulator,
            risk_limits,  # type: ignore[arg-type]
        ),
        routing=routing,
    )
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, plan)),
        1.0,
    )
    result = ExecutionPipeline.process_quote(
        state,
        quote(asset_id, 2.0, REFERENCE),
        context_factory,
        **kwargs,  # type: ignore[arg-type]
    )
    return result, asset_id


def _releases(state: ExecutionPipelineState) -> int:
    return len(
        [
            event
            for event in state.allocation.events
            if type(event).__name__ == "AllocationReservationReleased"
        ]
    )


def _drained(state: ExecutionPipelineState) -> bool:
    return (
        state.allocation.notional_allocated == Decimal("0.00")
        and len(state.allocation.reservations) == 0
    )


# ---------------------------------------------------------------------------
# Price divergence, both directions, both routings
# ---------------------------------------------------------------------------


def test_a_full_fill_below_the_reference_price_releases_its_residual() -> None:
    """The defect: a SELL filling under the reference stranded the difference."""

    result, _ = _run({2.0: Decimal("-100")}, simulator=_ADVERSE)
    order = result.state.oms.orders.find(result.oms_orders[0].order_id)

    assert order.status.name == "FILLED"
    assert order.average_fill_price == Decimal("99.0000")
    assert _drained(result.state)


def test_a_full_fill_above_the_reference_price_leaves_nothing_held() -> None:
    """The mirror case, which ``min()`` already covered -- and still must."""

    result, _ = _run({2.0: Decimal("100")}, simulator=_ADVERSE)
    order = result.state.oms.orders.find(result.oms_orders[0].order_id)

    assert order.status.name == "FILLED"
    assert order.average_fill_price == Decimal("101.0000")
    assert _drained(result.state)


def test_zero_price_divergence_is_unchanged() -> None:
    result, _ = _run({2.0: Decimal("-100")})

    assert _drained(result.state)
    assert _releases(result.state) == 0, "nothing was stranded, so nothing is released"


def test_a_venue_fill_below_the_reference_releases_its_residual() -> None:
    """EXTERNAL, through the broker seam, with **no slippage model at all**."""

    result, asset_id = _run({2.0: Decimal("-100")}, routing=ExecutionRouting.EXTERNAL)
    order = result.oms_orders[0]

    assert not _drained(result.state), "a working order legitimately holds its capital"

    report = ExecutionReport(
        execution_id=str(uuid4()),
        order_id=str(order.order_id.value),
        asset_id=asset_id,
        strategy_id=order.strategy_id,
        timestamp=3.0,
        fill_price=Decimal("99"),
        fill_quantity=order.remaining_quantity,
        commission=Decimal("0"),
        slippage=Decimal("0"),
        liquidity_flag="TAKER",
        venue="SIM",
        currency="USD",
        status=FillStatus.FULL_FILL,
    )
    applied, _, _ = ExecutionPipeline.apply_execution_report(result.state, order, report)

    assert applied.oms.orders.find(order.order_id).status.name == "FILLED"
    assert _drained(applied)


# ---------------------------------------------------------------------------
# Exactly once, on every path that already released
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [FillStatus.NO_FILL, FillStatus.REJECTED, FillStatus.EXPIRED])
def test_a_non_trading_outcome_still_releases_exactly_once(status: FillStatus) -> None:
    """The terminal release must not double up with the existing one."""

    result, _ = _run({2.0: Decimal("-100")}, fill_status=status)

    assert _drained(result.state)
    assert _releases(result.state) == 1, f"{status.name}: expected exactly one release"


def test_a_risk_rejected_request_still_releases_exactly_once() -> None:
    result, _ = _run({2.0: Decimal("-100")}, risk_limits=permissive_risk_limits(Decimal("1")))

    assert result.risk_decisions and not result.risk_decisions[0].approved
    assert _drained(result.state)
    assert _releases(result.state) == 1


def test_a_partial_fill_keeps_its_v2_5_behaviour() -> None:
    """The remainder is cancelled and released once, exactly as in v2.5."""

    strategy_id, asset_id = "S1", str(uuid4())
    config = pipeline_config(strategy_id, Decimal("1000000"), _ADVERSE)
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(
            strategy_id, ScriptedStrategy(strategy_id, asset_id, {2.0: Decimal("-100")})
        ),
        1.0,
    )
    result = ExecutionPipeline.process_quote(
        state,
        sized_quote(asset_id, 2.0, REFERENCE, Decimal("40")),
        context_factory,
        fill_policy=LiquidityCappedFill(),
    )
    order = result.state.oms.orders.find(result.oms_orders[0].order_id)

    assert order.status.name == "CANCELLED"
    assert order.filled_quantity == Decimal("40")
    assert _drained(result.state)
    assert _releases(result.state) == 1


def test_releasing_an_order_that_holds_nothing_cannot_raise_through_the_pipeline() -> None:
    """Idempotence is the membership guard, and the engine still refuses a repeat."""

    result, _ = _run({2.0: Decimal("-100")}, simulator=_ADVERSE)
    order_id = str(result.oms_orders[0].order_id.value)

    assert order_id not in result.state.allocation.reservations
    with pytest.raises(UnknownReservationError):
        AllocationEngine.release_reservation(result.state.allocation, order_id, 9.0)


# ---------------------------------------------------------------------------
# It does not accumulate
# ---------------------------------------------------------------------------


def test_many_round_trips_at_a_divergent_price_leave_nothing_committed() -> None:
    """Thirty round trips previously stranded 3,000 against a 1,000,000 budget."""

    strategy_id, asset_id = "S1", str(uuid4())
    plan = {float(t): (Decimal("100") if t % 2 == 0 else Decimal("-100")) for t in range(2, 62)}
    config = pipeline_config(strategy_id, Decimal("1000000"), _ADVERSE)
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, plan)),
        1.0,
    )
    for timestamp in range(2, 62):
        state = ExecutionPipeline.process_quote(
            state, quote(asset_id, float(timestamp), REFERENCE), context_factory
        ).state

    assert state.portfolio.positions == {}
    assert state.allocation.notional_allocated == Decimal("0.00")
    assert dict(state.allocation.reservations) == {}


def test_the_accounting_identity_scenario_no_longer_strands_capital() -> None:
    """The v2.5 test that exercised this path and passed while leaking.

    ``test_the_accounting_identity_holds_with_commission_and_slippage`` left
    ``0.0425`` held against a ``FILLED`` order and asserted nothing about it.
    """

    from alphalab.execution.commission import PerShareCommission
    from alphalab.execution.slippage import FixedSlippage
    from tests.integration.test_backtest_pipeline import _identity_holds
    from tests.integration.test_backtest_pipeline import _run as _backtest

    simulator = ExecutionSimulator(
        commission_model=PerShareCommission(Decimal("0.0035")),
        slippage_model=FixedSlippage(Decimal("0.0100")),
    )
    result, _ = _backtest({2.0: Decimal("10.5"), 4.0: Decimal("-4.25")}, simulator=simulator)

    assert _identity_holds(result)
    assert result.state.allocation.notional_allocated == Decimal("0.00")
    assert dict(result.state.allocation.reservations) == {}
