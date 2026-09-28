"""What allocation's constraints and sizing models mean, pinned (ledger ALC-001..003).

Four defects the pre-v4 audit found in allocation, each probed before the fix:

* **ALC-001.** ``AllocationConstraints(allow_shorting=False)`` refused every
  sale, including one that closes a long, because allocation nets deltas and
  never saw a position -- so long-only was unusable, and the examples turned it
  off. Long-only is now judged against the committed position (filled plus
  working orders), which the pipeline supplies as plain numbers.
* **ALC-002.** :class:`~alphalab.strategy.events.Intent` called itself a
  "target position/weight" while allocation read ``target`` as an order delta.
  The contract now says delta, and :class:`~alphalab.strategy.events.IntentKind`
  carries it in the data.
* **ALC-003.** A sizing model returned a quantity of zero for an unpriced
  instrument -- the intent vanished, and never reached the run's unpriced-asset
  record -- and ``VolatilityTargetSizing`` sized a missing volatility as 1%.
  Both now refuse, and allocation records each refusal and sizes the rest.
* With ``enforce_integer_quantities`` a delta that rounded to zero emitted a
  zero-quantity SELL, which the risk gate refuses by raising; and long-only was
  checked on the unrounded delta.
"""

from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from alphalab.allocation import (
    AllocationEngine,
    CapitalBudget,
    EqualWeightSizing,
    FixedDollarSizing,
    FixedQuantitySizing,
    TargetWeightSizing,
    VolatilityTargetSizing,
    validate_intent,
)
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.allocation.events import AllocationRejected
from alphalab.allocation.exceptions import AllocationValidationError, SizingRefusedError
from alphalab.allocation.sizing import SizingModel
from alphalab.allocation.state import AllocationState
from alphalab.core.enums import Side
from alphalab.core.order_request import OrderRequest
from alphalab.execution.fill import FillStatus
from alphalab.execution.report import ExecutionReport
from alphalab.runtime.execution_pipeline import (
    ExecutionPipeline,
    ExecutionPipelineState,
    ExecutionRouting,
    UnpricedReason,
)
from alphalab.strategy import Intent, IntentKind
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    pipeline_config,
    quote,
    running_strategy_state,
)

BUDGET = CapitalBudget(
    global_capital=Decimal("1000000"),
    maximum_exposure=Decimal("10000000"),
    strategy_budgets={"S1": Decimal("1000000"), "S2": Decimal("1000000")},
)
PRICES = {"AAPL": Decimal("100")}
LONG_ONLY = AllocationConstraints(allow_shorting=False)


def _allocate(
    intents: Iterable[Intent],
    *,
    sizing: SizingModel | None = None,
    constraints: AllocationConstraints = LONG_ONLY,
    prices: Mapping[str, Decimal] = PRICES,
    **kwargs: Any,
) -> tuple[AllocationState, tuple[OrderRequest, ...]]:
    return AllocationEngine.allocate(
        AllocationEngine.initialize(BUDGET),
        tuple(intents),
        prices,
        sizing if sizing is not None else FixedQuantitySizing(),
        constraints,
        1.0,
        **kwargs,
    )


def _rejections(state: AllocationState) -> list[str]:
    return [event.reason for event in state.events if isinstance(event, AllocationRejected)]


# --------------------------------------------------------------------------- #
# ALC-001: long-only is about positions
# --------------------------------------------------------------------------- #


def test_long_only_lets_a_sale_close_a_long() -> None:
    state, orders = _allocate(
        [Intent("S1", "AAPL", Decimal("-10"))], positions={"AAPL": Decimal("10")}
    )

    assert [(order.side, order.quantity) for order in orders] == [(Side.SELL, Decimal("10"))]
    assert _rejections(state) == []


def test_long_only_refuses_a_sale_that_would_leave_a_short() -> None:
    state, orders = _allocate(
        [Intent("S1", "AAPL", Decimal("-11"))], positions={"AAPL": Decimal("10")}
    )

    assert orders == ()
    (reason,) = _rejections(state)
    assert "would leave -1" in reason and "AAPL" in reason


def test_an_empty_positions_mapping_says_flat() -> None:
    state, orders = _allocate([Intent("S1", "AAPL", Decimal("-1"))], positions={})

    assert orders == ()
    assert "a short" in _rejections(state)[0]


def test_unknown_positions_refuse_every_sale_rather_than_guess() -> None:
    """``None`` is not flat: without positions a close cannot be told from a short."""

    state, orders = _allocate([Intent("S1", "AAPL", Decimal("-1"))])

    assert orders == ()
    assert "long-only enforced" in _rejections(state)[0]


def test_long_only_judges_the_netted_delta() -> None:
    """Two strategies' deltas net first; long-only reads the order that would be sent."""

    _, orders = _allocate(
        [Intent("S1", "AAPL", Decimal("-15")), Intent("S2", "AAPL", Decimal("6"))],
        positions={"AAPL": Decimal("10")},
    )

    assert [(order.side, order.quantity) for order in orders] == [(Side.SELL, Decimal("9"))]


def test_shorting_permitted_ignores_positions() -> None:
    _, orders = _allocate(
        [Intent("S1", "AAPL", Decimal("-5"))],
        constraints=AllocationConstraints(allow_shorting=True),
        positions={},
    )

    assert [(order.side, order.quantity) for order in orders] == [(Side.SELL, Decimal("5"))]


# --------------------------------------------------------------------------- #
# Integer quantities: round first, and never emit a zero-quantity order
# --------------------------------------------------------------------------- #

INTEGER_LONG_ONLY = AllocationConstraints(allow_shorting=False, enforce_integer_quantities=True)


def test_a_delta_that_rounds_to_zero_emits_no_order() -> None:
    """It emitted a zero-quantity SELL, which the risk gate refuses by raising."""

    _, orders = _allocate(
        [Intent("S1", "AAPL", Decimal("0.4"))],
        constraints=AllocationConstraints(enforce_integer_quantities=True),
    )

    assert orders == ()


def test_long_only_is_checked_on_the_rounded_quantity() -> None:
    """-10.4 rounds to -10, which closes a 10 long exactly."""

    _, orders = _allocate(
        [Intent("S1", "AAPL", Decimal("-10.4"))],
        constraints=INTEGER_LONG_ONLY,
        positions={"AAPL": Decimal("10")},
    )

    assert [(order.side, order.quantity) for order in orders] == [(Side.SELL, Decimal("10"))]


# --------------------------------------------------------------------------- #
# ALC-003: sizing refuses what it cannot size, and allocation records it
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "sizing",
    [FixedDollarSizing(), TargetWeightSizing(), EqualWeightSizing(2)],
    ids=["fixed-dollar", "target-weight", "equal-weight"],
)
@pytest.mark.parametrize("price", [Decimal("0"), Decimal("-1")])
def test_a_price_based_model_refuses_a_non_positive_price(sizing, price) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SizingRefusedError, match="no positive price"):
        sizing.calculate(Intent("S1", "AAPL", Decimal("0.1")), BUDGET, price)


def test_an_unpriced_intent_is_recorded_and_the_rest_of_the_batch_sized() -> None:
    state, orders = _allocate(
        [Intent("S1", "MSFT", Decimal("0.1")), Intent("S1", "AAPL", Decimal("0.1"))],
        sizing=TargetWeightSizing(),
        constraints=AllocationConstraints(),
    )

    assert [order.asset_id for order in orders] == ["AAPL"]
    (reason,) = _rejections(state)
    assert "MSFT" in reason and "no positive price" in reason


def test_volatility_target_sizing_refuses_a_missing_volatility() -> None:
    """It sized a missing volatility as 1%: 100 shares from a number nobody supplied."""

    sizing = VolatilityTargetSizing(Decimal("0.10"), {})

    with pytest.raises(SizingRefusedError, match="no volatility"):
        sizing.calculate(Intent("S1", "AAPL", Decimal("1")), BUDGET, Decimal("100"))


@pytest.mark.parametrize("vol", [Decimal("0"), Decimal("-0.2"), Decimal("NaN")])
def test_volatility_target_sizing_refuses_a_non_positive_volatility(vol: Decimal) -> None:
    sizing = VolatilityTargetSizing(Decimal("0.10"), {"AAPL": vol})

    with pytest.raises(SizingRefusedError, match="a volatility of"):
        sizing.calculate(Intent("S1", "AAPL", Decimal("1")), BUDGET, Decimal("100"))


@pytest.mark.parametrize("target", [Decimal("0"), Decimal("-0.1"), Decimal("Infinity")])
def test_volatility_target_sizing_refuses_a_meaningless_target(target: Decimal) -> None:
    with pytest.raises(AllocationValidationError, match="target_vol"):
        VolatilityTargetSizing(target, {"AAPL": Decimal("0.2")})


def test_volatility_target_sizing_copies_its_volatilities() -> None:
    vols = {"AAPL": Decimal("0.20")}
    sizing = VolatilityTargetSizing(Decimal("0.10"), vols)
    vols["AAPL"] = Decimal("0.01")

    quantity = sizing.calculate(Intent("S1", "AAPL", Decimal("1")), BUDGET, Decimal("100"))
    assert quantity == Decimal("5000.000000")  # 0.10 / 0.20 * 1,000,000 / 100


def test_equal_weight_sizing_refuses_fewer_than_one_asset() -> None:
    """It silently raised the count to one."""

    with pytest.raises(AllocationValidationError, match="at least one asset"):
        EqualWeightSizing(0)


# --------------------------------------------------------------------------- #
# ALC-002: an intent is a delta, and says so
# --------------------------------------------------------------------------- #


def test_an_intent_is_a_delta_by_construction() -> None:
    assert Intent("S1", "AAPL", Decimal("1")).kind is IntentKind.DELTA
    assert [member.value for member in IntentKind] == ["delta"]


def test_emitting_the_same_intent_twice_asks_twice() -> None:
    """The semantics the contract now states, rather than a target reached once."""

    state = AllocationEngine.initialize(BUDGET)
    intent = Intent("S1", "AAPL", Decimal("0.01"))
    for _ in range(2):
        state, orders = AllocationEngine.allocate(
            state, (intent,), PRICES, TargetWeightSizing(), AllocationConstraints(), 1.0
        )
        assert [order.quantity for order in orders] == [Decimal("100.000000")]


def test_allocation_refuses_an_intent_of_another_kind() -> None:
    impostor = replace(Intent("S1", "AAPL", Decimal("1")), kind="target")  # type: ignore[arg-type]

    with pytest.raises(AllocationValidationError, match="order deltas"):
        validate_intent(impostor)


@pytest.mark.parametrize("target", [Decimal("Infinity"), Decimal("NaN")])
def test_a_non_finite_target_is_refused(target: Decimal) -> None:
    with pytest.raises(AllocationValidationError, match="finite"):
        validate_intent(Intent("S1", "AAPL", target))


# --------------------------------------------------------------------------- #
# Through the pipeline
# --------------------------------------------------------------------------- #


def _pipeline(
    plan: Mapping[float, Decimal],
    *,
    asset_for: Mapping[float, str] | None = None,
    routing: ExecutionRouting = ExecutionRouting.SIMULATED,
    sizing: SizingModel | None = None,
) -> tuple[ExecutionPipelineState, str]:
    strategy_id = str(uuid4())
    asset_id = str(uuid4())
    config = pipeline_config(strategy_id)
    config = replace(
        config,
        allocation_constraints=AllocationConstraints(allow_shorting=False),
        routing=routing,
        sizing_model=sizing if sizing is not None else config.sizing_model,
    )
    strategy = ScriptedStrategy(strategy_id, asset_id, plan, asset_for)
    state = ExecutionPipeline.initialize(config, running_strategy_state(strategy_id, strategy), 1.0)
    return state, asset_id


def _run(
    state: ExecutionPipelineState, asset_id: str, timestamps: Iterable[float]
) -> ExecutionPipelineState:
    for timestamp in timestamps:
        state = ExecutionPipeline.process_quote(
            state, quote(asset_id, timestamp, Decimal("100")), context_factory
        ).state
    return state


def test_a_long_only_run_can_sell_what_it_bought_and_no_more() -> None:
    state, asset_id = _pipeline({2.0: Decimal("10"), 3.0: Decimal("-10"), 4.0: Decimal("-1")})
    state = _run(state, asset_id, (2.0, 3.0, 4.0))

    assert [fill.quantity for fill in state.fills] == [Decimal("10"), Decimal("10")]
    assert asset_id not in state.portfolio.positions or (
        state.portfolio.positions[asset_id].quantity == 0
    )
    assert "a short" in _rejections(state.allocation)[-1]


def test_a_working_sale_counts_against_long_only() -> None:
    """External routing leaves the first sale working; the second would short."""

    state, asset_id = _pipeline(
        {2.0: Decimal("10"), 3.0: Decimal("-10"), 4.0: Decimal("-10")},
        routing=ExecutionRouting.EXTERNAL,
    )
    # Buy 10 and fill it by hand, as a venue would, then let the sales work.
    state = _run(state, asset_id, (2.0,))
    (order,) = state.oms.orders.open_orders()
    fill = ExecutionReport(
        execution_id="VENUE-1",
        order_id=str(order.order_id.value),
        asset_id=asset_id,
        strategy_id="",
        timestamp=2.5,
        fill_price=Decimal("100"),
        fill_quantity=Decimal("10"),
        commission=Decimal("0"),
        slippage=Decimal("0"),
        liquidity_flag="",
        venue="LIVE",
        currency="USD",
        status=FillStatus.FULL_FILL,
    )
    state, _, _ = ExecutionPipeline.apply_execution_report(state, order, fill)
    assert state.portfolio.positions[asset_id].quantity == Decimal("10")
    state = _run(state, asset_id, (3.0, 4.0))

    working = [state.oms.orders.find(order_id) for order_id in state.oms.active_orders]
    assert [(order.side.name, order.quantity) for order in working] == [("SELL", Decimal("10"))]
    assert "committed position of 0" in _rejections(state.allocation)[-1]


def test_an_unsizable_unpriced_intent_reaches_the_unpriced_record() -> None:
    """Sized to zero it left no trace; now the run can say why it did not trade."""

    never = str(uuid4())
    state, asset_id = _pipeline(
        {2.0: Decimal("0.1"), 3.0: Decimal("0.1")},
        asset_for={2.0: never, 3.0: never},
        sizing=TargetWeightSizing(),
    )
    state = _run(state, asset_id, (2.0, 3.0))

    entry = state.unpriced_assets[never]
    assert entry.reason is UnpricedReason.NO_PRICE_OBSERVED
    assert entry.occurrences == 2
    assert (entry.first_timestamp, entry.last_timestamp) == (2.0, 3.0)
    assert state.fills == ()
