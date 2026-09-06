"""Strategy-level P&L describes strategies that exist.

Until v2.6 ``IntentAllocator.size_intents`` dropped ``strategy_id`` one
statement before netting, and every emitted request was stamped
``"ALLOC-NETTED"`` -- including a request produced from a single intent with no
netting of any kind. That value travelled into the OMS strategy index, the
execution report and the analytics record, so ``pnl_by_strategy`` reported the
whole of a run's P&L against a strategy that does not exist.

The information was never missing, only discarded. These tests pin that it now
survives, that the split is signed, and that the parts sum exactly.
"""

from decimal import Decimal
from uuid import uuid4

import pytest

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.allocation.engine import AllocationEngine
from alphalab.allocation.sizing import FixedQuantitySizing
from alphalab.analytics.attribution import (
    TradeRecord,
    calculate_attribution,
    split_realized_pnl,
)
from alphalab.core.contribution import StrategyContribution
from alphalab.core.order_request import OrderRequest
from alphalab.execution.fill import FillStatus
from alphalab.runtime.execution_pipeline import ExecutionPipeline, ExecutionPipelineState
from alphalab.strategy.events import Intent
from tests.integration.harness import (
    ScriptedStrategy,
    context_factory,
    permissive_risk_limits,
    pipeline_config,
    quote,
)

PRICE = Decimal("100")


def _allocate(*intents: Intent) -> tuple[OrderRequest, ...]:
    state = AllocationEngine.initialize(
        CapitalBudget(global_capital=Decimal("100000000"), maximum_exposure=Decimal("100000000"))
    )
    _, orders = AllocationEngine.allocate(
        state,
        intents,
        {"AAPL": PRICE},
        FixedQuantitySizing(),
        AllocationConstraints(),
        1.0,
    )
    return orders


def _contribs(order: OrderRequest) -> dict[str, Decimal]:
    return {c.strategy_id: c.quantity for c in order.contributions}


# ---------------------------------------------------------------------------
# Contributions survive sizing and netting
# ---------------------------------------------------------------------------


def test_a_single_strategy_owns_its_whole_order() -> None:
    (order,) = _allocate(Intent("MOMENTUM", "AAPL", Decimal("60")))

    assert _contribs(order) == {"MOMENTUM": Decimal("60.000000")}
    assert order.strategy_id == "", "a netted request declares no single owner"


def test_two_same_sign_strategies_both_appear() -> None:
    (order,) = _allocate(
        Intent("MOMENTUM", "AAPL", Decimal("60")), Intent("MEANREV", "AAPL", Decimal("40"))
    )

    assert order.quantity == Decimal("100.000000")
    assert _contribs(order) == {
        "MOMENTUM": Decimal("60.000000"),
        "MEANREV": Decimal("40.000000"),
    }


def test_opposing_strategies_keep_their_signs() -> None:
    (order,) = _allocate(
        Intent("MOMENTUM", "AAPL", Decimal("60")), Intent("MEANREV", "AAPL", Decimal("-40"))
    )

    assert order.quantity == Decimal("20.000000")
    assert _contribs(order) == {
        "MOMENTUM": Decimal("60.000000"),
        "MEANREV": Decimal("-40.000000"),
    }


def test_completely_offsetting_strategies_produce_no_order() -> None:
    """Nothing is held, so there is nothing to attribute -- and no zero divisor."""

    assert (
        _allocate(
            Intent("MOMENTUM", "AAPL", Decimal("60")),
            Intent("MEANREV", "AAPL", Decimal("-60")),
        )
        == ()
    )


def test_one_strategy_expressing_two_intents_contributes_their_sum() -> None:
    (order,) = _allocate(
        Intent("MOMENTUM", "AAPL", Decimal("60")),
        Intent("MOMENTUM", "AAPL", Decimal("15")),
    )

    assert _contribs(order) == {"MOMENTUM": Decimal("75.000000")}


def test_contributions_are_ordered_by_strategy_id_whatever_the_intent_order() -> None:
    """Canonical ordering is what keeps a backtest and its replay comparable."""

    forward = _allocate(
        Intent("AAA", "AAPL", Decimal("10")),
        Intent("ZZZ", "AAPL", Decimal("20")),
        Intent("MMM", "AAPL", Decimal("30")),
    )
    reverse = _allocate(
        Intent("MMM", "AAPL", Decimal("30")),
        Intent("ZZZ", "AAPL", Decimal("20")),
        Intent("AAA", "AAPL", Decimal("10")),
    )

    names = [c.strategy_id for c in forward[0].contributions]
    assert names == ["AAA", "MMM", "ZZZ"]
    assert forward[0].contributions == reverse[0].contributions


# ---------------------------------------------------------------------------
# The split
# ---------------------------------------------------------------------------


def test_a_sole_contributor_takes_the_whole_pnl() -> None:
    parts = split_realized_pnl(
        Decimal("1000.00"), (StrategyContribution("MOMENTUM", Decimal("60")),)
    )

    assert parts == (("MOMENTUM", Decimal("1000.00")),)


def test_two_same_sign_contributors_split_by_share() -> None:
    parts = dict(
        split_realized_pnl(
            Decimal("1000.00"),
            (
                StrategyContribution("MEANREV", Decimal("40")),
                StrategyContribution("MOMENTUM", Decimal("60")),
            ),
        )
    )

    assert parts == {"MEANREV": Decimal("400.00"), "MOMENTUM": Decimal("600.00")}


def test_opposing_contributors_get_signed_weights_summing_to_one() -> None:
    """``+60`` against ``-40`` nets ``+20``: weights ``3.0`` and ``-2.0``."""

    parts = dict(
        split_realized_pnl(
            Decimal("100.00"),
            (
                StrategyContribution("MEANREV", Decimal("-40")),
                StrategyContribution("MOMENTUM", Decimal("60")),
            ),
        )
    )

    assert parts == {"MEANREV": Decimal("-200.00"), "MOMENTUM": Decimal("300.00")}
    assert sum(parts.values()) == Decimal("100.00")


def test_a_near_complete_cross_amplifies_and_still_sums_exactly() -> None:
    """``+60`` against ``-59`` nets ``1``: 59 units crossed internally, one held."""

    parts = dict(
        split_realized_pnl(
            Decimal("10.00"),
            (
                StrategyContribution("A", Decimal("60")),
                StrategyContribution("B", Decimal("-59")),
            ),
        )
    )

    assert parts["A"] == Decimal("600.00")
    assert parts["B"] == Decimal("-590.00")
    assert sum(parts.values()) == Decimal("10.00")


@pytest.mark.parametrize(
    "pnl",
    [Decimal("100.00"), Decimal("0.01"), Decimal("-7.77"), Decimal("1000000.03"), Decimal("0.00")],
)
def test_an_uneven_split_still_sums_to_the_exact_total(pnl: Decimal) -> None:
    """Three-way thirds do not divide; the last share closes the gap exactly."""

    parts = split_realized_pnl(
        pnl,
        (
            StrategyContribution("A", Decimal("1")),
            StrategyContribution("B", Decimal("1")),
            StrategyContribution("C", Decimal("1")),
        ),
    )

    assert sum(share for _, share in parts) == pnl


def test_a_record_with_no_contributions_attributes_to_nobody() -> None:
    assert split_realized_pnl(Decimal("100.00"), ()) == ()


def test_attribution_sums_to_the_total_realized_pnl() -> None:
    trades = (
        TradeRecord(
            "T1",
            "AAPL",
            None,
            Decimal("1000.00"),
            Decimal("10000"),
            None,
            (
                StrategyContribution("MEANREV", Decimal("40")),
                StrategyContribution("MOMENTUM", Decimal("60")),
            ),
        ),
    )
    metrics = calculate_attribution(trades)

    assert metrics.pnl_by_strategy == {
        "MEANREV": Decimal("400.00"),
        "MOMENTUM": Decimal("600.00"),
    }
    assert sum(metrics.pnl_by_strategy.values()) == Decimal("1000.00")


# ---------------------------------------------------------------------------
# End to end, through the real pipeline
# ---------------------------------------------------------------------------


def _two_strategy_run(
    plans: dict[str, dict[float, Decimal]], mids: dict[float, Decimal]
) -> tuple[ExecutionPipelineState, str]:
    """Drive several strategies over one asset through the real pipeline."""

    from dataclasses import replace as dc_replace

    from alphalab.strategy.runtime import create_runtime, register_strategy
    from alphalab.strategy.supervisor import RuntimeSupervisor

    asset_id = str(uuid4())
    runtime = create_runtime()
    for strategy_id, plan in plans.items():
        runtime = register_strategy(
            runtime, strategy_id, ScriptedStrategy(strategy_id, asset_id, plan)
        )
    running = {}
    for strategy_id, strategy_state in runtime.strategies.items():
        configured, _ = RuntimeSupervisor.configure(strategy_state, {}, 1.0)
        initialized, _ = RuntimeSupervisor.initialize(configured, 1.1)
        subscribed, _ = RuntimeSupervisor.subscribe(initialized, frozenset({"quotes"}), 1.2)
        started, _ = RuntimeSupervisor.start(subscribed, 1.3)
        running[strategy_id] = started
    runtime = dc_replace(runtime, strategies=running)

    config = pipeline_config(
        next(iter(plans)), Decimal("100000000"), None, permissive_risk_limits(Decimal("100000000"))
    )
    state = ExecutionPipeline.initialize(config, runtime, 1.0)
    for timestamp, mid in sorted(mids.items()):
        state = ExecutionPipeline.process_quote(
            state, quote(asset_id, timestamp, mid), context_factory
        ).state
    return state, asset_id


def test_a_netted_round_trip_attributes_to_the_real_strategies() -> None:
    """The measured v2.5.0 defect: this reported ``{'ALLOC-NETTED': 1000.00}``."""

    state, _ = _two_strategy_run(
        {
            "MOMENTUM": {2.0: Decimal("60"), 3.0: Decimal("-60")},
            "MEANREV": {2.0: Decimal("40"), 3.0: Decimal("-40")},
        },
        {2.0: Decimal("100"), 3.0: Decimal("110")},
    )
    metrics = calculate_attribution(tuple(state.trade_records))

    assert "ALLOC-NETTED" not in metrics.pnl_by_strategy
    assert set(metrics.pnl_by_strategy) == {"MOMENTUM", "MEANREV"}
    assert metrics.pnl_by_strategy["MOMENTUM"] == Decimal("600.00")
    assert metrics.pnl_by_strategy["MEANREV"] == Decimal("400.00")
    assert sum(metrics.pnl_by_strategy.values()) == Decimal("1000.00")


def test_a_partial_fill_applies_the_same_weights_to_what_executed() -> None:
    """Contributions are ratios, so a partial fill needs no special handling."""

    from alphalab.execution.policy import LiquidityCappedFill
    from tests.integration.harness import (
        ScriptedStrategy,
        running_strategy_state,
        sized_quote,
    )

    strategy_id, asset_id = "MOMENTUM", str(uuid4())
    config = pipeline_config(strategy_id, Decimal("1000000"))
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(
            strategy_id, ScriptedStrategy(strategy_id, asset_id, {2.0: Decimal("100")})
        ),
        1.0,
    )
    result = ExecutionPipeline.process_quote(
        state,
        sized_quote(asset_id, 2.0, PRICE, Decimal("40")),
        context_factory,
        fill_policy=LiquidityCappedFill(),
    )
    (record,) = tuple(result.state.trade_records)

    assert record.contributions == (StrategyContribution("MOMENTUM", Decimal("100.000000")),)
    assert record.notional_value == Decimal("4000.00")


def test_a_cancelled_order_produces_no_record() -> None:
    from tests.integration.harness import ScriptedStrategy, running_strategy_state

    strategy_id, asset_id = "MOMENTUM", str(uuid4())
    config = pipeline_config(strategy_id, Decimal("1000000"))
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(
            strategy_id, ScriptedStrategy(strategy_id, asset_id, {2.0: Decimal("100")})
        ),
        1.0,
    )
    result = ExecutionPipeline.process_quote(
        state, quote(asset_id, 2.0, PRICE), context_factory, fill_status=FillStatus.NO_FILL
    )

    assert tuple(result.state.trade_records) == ()


def test_a_risk_rejected_request_produces_no_record() -> None:
    from tests.integration.harness import ScriptedStrategy, running_strategy_state

    strategy_id, asset_id = "MOMENTUM", str(uuid4())
    config = pipeline_config(
        strategy_id, Decimal("1000000"), None, permissive_risk_limits(Decimal("1"))
    )
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(
            strategy_id, ScriptedStrategy(strategy_id, asset_id, {2.0: Decimal("100")})
        ),
        1.0,
    )
    result = ExecutionPipeline.process_quote(state, quote(asset_id, 2.0, PRICE), context_factory)

    assert tuple(result.state.trade_records) == ()


def test_a_reversal_attributes_to_whoever_asked_for_the_reversing_trade() -> None:
    state, _ = _two_strategy_run(
        {"MOMENTUM": {2.0: Decimal("10"), 3.0: Decimal("-25")}},
        {2.0: Decimal("100"), 3.0: Decimal("110")},
    )
    records = tuple(state.trade_records)

    assert all(r.contributions[0].strategy_id == "MOMENTUM" for r in records)
    metrics = calculate_attribution(records)
    assert set(metrics.pnl_by_strategy) == {"MOMENTUM"}


# ---------------------------------------------------------------------------
# The ordering the ledger depends on
# ---------------------------------------------------------------------------


def test_contributions_are_read_before_the_ledger_entry_is_retired() -> None:
    """A fill that fully consumes its reservation still carries its attribution.

    ``_apply_reports`` builds the record and only then retires the ledger. If
    that order were reversed the record would carry an empty tuple and
    attribution would silently vanish -- a defect that no other assertion in
    this suite would catch.
    """

    from tests.integration.harness import ScriptedStrategy, running_strategy_state

    strategy_id, asset_id = "MOMENTUM", str(uuid4())
    config = pipeline_config(strategy_id, Decimal("1000000"))
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(
            strategy_id, ScriptedStrategy(strategy_id, asset_id, {2.0: Decimal("100")})
        ),
        1.0,
    )
    result = ExecutionPipeline.process_quote(state, quote(asset_id, 2.0, PRICE), context_factory)
    (record,) = tuple(result.state.trade_records)

    assert record.contributions == (StrategyContribution("MOMENTUM", Decimal("100.000000")),)
    assert dict(result.state.allocation.contributions) == {}, "the ledger is retired after"


def test_the_contribution_ledger_is_empty_once_every_order_is_terminal() -> None:
    from tests.integration.harness import ScriptedStrategy, running_strategy_state

    strategy_id, asset_id = "MOMENTUM", str(uuid4())
    plan = {float(t): (Decimal("10") if t % 2 == 0 else Decimal("-10")) for t in range(2, 22)}
    config = pipeline_config(strategy_id, Decimal("1000000"))
    state = ExecutionPipeline.initialize(
        config,
        running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, plan)),
        1.0,
    )
    for timestamp in range(2, 22):
        state = ExecutionPipeline.process_quote(
            state, quote(asset_id, float(timestamp), PRICE), context_factory
        ).state

    assert dict(state.allocation.contributions) == {}
    assert dict(state.allocation.reservations) == {}
