"""The three reservation invariants, and the lifecycle phase each one governs.

A reservation is created by :meth:`AllocationEngine.allocate` -- *before* risk
runs and *before* anything reaches the OMS. That ordering is deliberate
(ADR-0015 decision 3), and it means a reservation legitimately exists for a
request that has no OMS order at all. An invariant phrased as "every reservation
belongs to an open order" is therefore **false** in a state the architecture
creates on purpose, which is why the three invariants below are scoped
differently:

``I1`` is local to :class:`~alphalab.allocation.state.AllocationState` and holds
everywhere. ``I2`` quantifies over *terminal orders* rather than over
reservations, which makes it vacuously true in the pre-OMS window and silent
about working orders -- so it is universal, and it is the one that catches the
stranded reservation of ADR-0015 decision 2. ``I3`` is a **postcondition** of a
completed pipeline step, not a universal invariant; a companion test below pins
that it is false inside the pre-OMS window, so a later refactor cannot quietly
promote it and then "fix" the architecture to satisfy it.

See ADR-0015 for the three-phase lifecycle table these tests encode.
"""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from alphalab.allocation.engine import AllocationEngine
from alphalab.execution.fill import FillStatus
from alphalab.execution.policy import LiquidityCappedFill
from alphalab.execution.report import ExecutionReport
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.slippage import PercentageSlippage
from alphalab.market.engine import MarketEngine
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineState,
    ExecutionRouting,
)
from alphalab.strategy.engine import StrategyEngine
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

# ---------------------------------------------------------------------------
# The invariants
# ---------------------------------------------------------------------------


def i1_holds(state: ExecutionPipelineState) -> bool:
    """``notional_allocated`` is exactly the sum of the ledger it totals."""

    return state.allocation.notional_allocated == sum(
        state.allocation.reservations.values(), Decimal("0")
    )


def i2_holds(state: ExecutionPipelineState) -> bool:
    """No *terminal* OMS order holds an allocation reservation.

    Quantified over terminal orders, not over reservations: that is what makes
    it silent in the pre-OMS window and about working orders.
    """

    held = state.allocation.reservations.to_dict()
    return not [
        order
        for order in state.oms.orders.orders()
        if order.is_closed and str(order.order_id.value) in held
    ]


def i3_holds(state: ExecutionPipelineState) -> bool:
    """Every reservation belongs to an OMS order. **Postcondition only.**"""

    known = {str(order.order_id.value) for order in state.oms.orders.orders()}
    return all(order_id in known for order_id in state.allocation.reservations.to_dict())


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

_ZERO_SLIPPAGE = ExecutionSimulator()
_ADVERSE = ExecutionSimulator(slippage_model=PercentageSlippage(Decimal("0.01")))


def _pipeline(
    *,
    simulator: ExecutionSimulator = _ZERO_SLIPPAGE,
    routing: ExecutionRouting = ExecutionRouting.SIMULATED,
    risk_limits: object | None = None,
    plan: dict[float, Decimal] | None = None,
    asset_for: dict[float, str] | None = None,
) -> tuple[ExecutionPipelineState, str]:
    """A one-strategy pipeline that sells 100 at ``t=2.0`` unless told otherwise."""

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
    strategy = ScriptedStrategy(strategy_id, asset_id, plan or {2.0: Decimal("-100")}, asset_for)
    state = ExecutionPipeline.initialize(config, running_strategy_state(strategy_id, strategy), 1.0)
    return state, asset_id


def _advance(
    state: ExecutionPipelineState, asset_id: str, **kwargs: object
) -> ExecutionPipelineState:
    return ExecutionPipeline.process_quote(
        state,
        quote(asset_id, 2.0, REFERENCE),
        context_factory,
        **kwargs,  # type: ignore[arg-type]
    ).state


# ---------------------------------------------------------------------------
# I3 is a postcondition, not a universal invariant
# ---------------------------------------------------------------------------


def _pre_oms_state() -> ExecutionPipelineState:
    """The state between ``allocate`` and ``_process_requests``.

    Reproduces what ``process_market_event`` does up to -- but not including --
    the per-request loop. No public pipeline function returns a state here; it is
    built by hand precisely because the window is otherwise unobservable.
    """

    state, asset_id = _pipeline()
    market = MarketEngine.publish_quote(state.market, quote(asset_id, 2.0, REFERENCE))
    event = market.events[-1]
    prices = {asset_id: REFERENCE}
    strategy, intents = StrategyEngine.process_event(state.strategy, event, context_factory, 2.0)
    allocation, _ = AllocationEngine.allocate(
        state.allocation,
        intents,
        prices,
        state.config.sizing_model,
        state.config.allocation_constraints,
        2.0,
    )
    return replace(
        state, market=market, strategy=strategy, allocation=allocation, market_prices=prices
    )


def test_a_reservation_exists_before_risk_and_before_the_oms() -> None:
    """The pre-OMS window is legitimate and deliberate: ADR-0015 decision 3."""

    state = _pre_oms_state()

    assert len(state.allocation.reservations) == 1
    assert state.oms.orders.orders() == ()


def test_i3_is_false_in_the_pre_oms_window_and_that_is_correct() -> None:
    """``I3`` must never be promoted to a universal invariant.

    If this test ever fails, someone has moved reservation creation after risk
    or after OMS submission -- which ADR-0015 decision 3 rules out -- rather
    than scoping the invariant correctly.
    """

    assert not i3_holds(_pre_oms_state())


def test_i1_and_i2_are_universal_and_hold_in_the_pre_oms_window() -> None:
    """The two universal invariants survive the window ``I3`` does not."""

    state = _pre_oms_state()

    assert i1_holds(state)
    assert i2_holds(state)


def test_a_completed_step_closes_the_pre_oms_window() -> None:
    """Every request is released or submitted by the time the step returns."""

    state, asset_id = _pipeline()
    final = _advance(state, asset_id)

    assert i3_holds(final)
    assert len(final.oms.orders.orders()) == 1


# ---------------------------------------------------------------------------
# The nine lifecycle phases of ADR-0015
# ---------------------------------------------------------------------------


def _phase_fresh() -> ExecutionPipelineState:
    return _pipeline()[0]


def _phase_zero_divergence() -> ExecutionPipelineState:
    state, asset_id = _pipeline()
    return _advance(state, asset_id)


def _phase_adverse_divergence() -> ExecutionPipelineState:
    state, asset_id = _pipeline(simulator=_ADVERSE)
    return _advance(state, asset_id)


def _phase_external_working() -> ExecutionPipelineState:
    state, asset_id = _pipeline(routing=ExecutionRouting.EXTERNAL)
    return _advance(state, asset_id)


def _phase_external_divergent_fill() -> ExecutionPipelineState:
    """A venue fill below the reference, through the documented broker seam.

    The simulator has no slippage model at all: this reaches the same defect
    with nothing but a price the venue chose.
    """

    state, asset_id = _pipeline(routing=ExecutionRouting.EXTERNAL)
    result = ExecutionPipeline.process_quote(
        state, quote(asset_id, 2.0, REFERENCE), context_factory
    )
    order = result.oms_orders[0]
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
    return applied


def _phase_risk_rejected() -> ExecutionPipelineState:
    state, asset_id = _pipeline(risk_limits=permissive_risk_limits(Decimal("1")))
    return _advance(state, asset_id)


def _phase_unpriced() -> ExecutionPipelineState:
    state, asset_id = _pipeline(asset_for={2.0: str(uuid4())})
    return _advance(state, asset_id)


def _phase_no_fill() -> ExecutionPipelineState:
    state, asset_id = _pipeline()
    return _advance(state, asset_id, fill_status=FillStatus.NO_FILL)


def _phase_partial_fill() -> ExecutionPipelineState:
    state, asset_id = _pipeline(simulator=_ADVERSE)
    return ExecutionPipeline.process_quote(
        state,
        sized_quote(asset_id, 2.0, REFERENCE, Decimal("40")),
        context_factory,
        fill_policy=LiquidityCappedFill(),
    ).state


#: The nine phases, with the terminal-or-open OMS status each one ends in.
_PHASES = (
    pytest.param("fresh initialize", _phase_fresh, (), id="fresh initialize"),
    pytest.param(
        "full fill, zero price divergence",
        _phase_zero_divergence,
        ("FILLED",),
        id="full fill, zero price divergence",
    ),
    pytest.param(
        "full fill, adverse price divergence",
        _phase_adverse_divergence,
        ("FILLED",),
        id="full fill, adverse price divergence",
    ),
    pytest.param(
        "EXTERNAL working order",
        _phase_external_working,
        ("ACCEPTED",),
        id="EXTERNAL working order",
    ),
    pytest.param(
        "EXTERNAL broker fill, divergent price",
        _phase_external_divergent_fill,
        ("FILLED",),
        id="EXTERNAL broker fill, divergent price",
    ),
    pytest.param("risk rejection", _phase_risk_rejected, (), id="risk rejection"),
    pytest.param("unpriced request", _phase_unpriced, (), id="unpriced request"),
    pytest.param("NO_FILL to CANCELLED", _phase_no_fill, ("CANCELLED",), id="NO_FILL to CANCELLED"),
    pytest.param(
        "partial fill to CANCELLED",
        _phase_partial_fill,
        ("CANCELLED",),
        id="partial fill to CANCELLED",
    ),
)


@pytest.mark.parametrize(("label", "build", "statuses"), _PHASES)
def test_every_lifecycle_phase_satisfies_all_three_invariants(
    label: str, build: object, statuses: tuple[str, ...]
) -> None:
    """All three invariants hold on every state a public pipeline call returns.

    ``I3`` is applicable here -- and only here -- because each of these states is
    the result of a *completed* step. The pre-OMS window is covered by its own
    tests above, where ``I3`` is deliberately false.
    """

    state = build()  # type: ignore[operator]

    assert i1_holds(state), f"{label}: notional_allocated disagrees with the ledger"
    assert i2_holds(state), f"{label}: a terminal order still holds a reservation"
    assert i3_holds(state), f"{label}: a reservation belongs to no OMS order"

    assert tuple(sorted({o.status.name for o in state.oms.orders.orders()})) == statuses


def test_a_working_external_order_legitimately_holds_its_reservation() -> None:
    """``I2`` must not reject the case ADR-0012 introduced on purpose."""

    state = _phase_external_working()

    assert len(state.allocation.reservations) == 1
    assert state.allocation.notional_allocated > Decimal("0")
    assert i2_holds(state)
