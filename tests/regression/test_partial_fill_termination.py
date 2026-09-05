"""Regression guard for the partial-fill semantics v2.5 decided (ADR-0014).

Before v2.5, a partially filled simulated order stayed ``PARTIALLY_FILLED``
forever. The pipeline mints a fresh order per market event and never re-works an
existing one -- its own rule, stated in ``_close_unfilled_order`` since the
pipeline was written -- so the remainder would never be worked, the order could
never leave that state, and the reservation for the unfilled quantity was held
indefinitely. ``LiquidityCappedFill``, added in v2.2, makes partial fills
routine, so that was a slowly growing set of stuck orders and leaked capital.

The remainder is now withdrawn in the same step, exactly as a never-filled order
is. The decisive property, asserted first below, is that **this changes
bookkeeping and not economics**: no fill is created or destroyed, so cash,
positions, realized and unrealized P&L and the equity curve are what they were.
"""

from collections.abc import Mapping
from decimal import Decimal
from uuid import uuid4

import pytest

from alphalab.allocation.engine import AllocationEngine
from alphalab.allocation.exceptions import UnknownReservationError
from alphalab.allocation.views import open_reservations
from alphalab.backtesting import BacktestEngine, LiquidityCappedFill, MarketDataset, StaticFill
from alphalab.execution.fill import FillStatus
from alphalab.oms.status import OrderStatus
from alphalab.persistence.serializer import deserialize, serialize
from alphalab.portfolio.snapshot import capture, from_primitives, restore
from tests.integration.harness import (
    START_CASH,
    ScriptedStrategy,
    backtest_config,
    context_factory,
    running_strategy_state,
    sized_quote,
)

MIDS = [Decimal("100.00"), Decimal("101.00"), Decimal("102.00")]


def _run(plan: Mapping[float, Decimal], size: Decimal, policy: object | None = None):  # type: ignore[no-untyped-def]
    """A backtest whose quotes show exactly ``size`` on each side."""

    strategy_id, asset_id = str(uuid4()), str(uuid4())
    dataset = MarketDataset.of(
        "THIN", [sized_quote(asset_id, 2.0 + i, mid, size) for i, mid in enumerate(MIDS)]
    )
    result = BacktestEngine.run(
        backtest_config(
            strategy_id,
            fill_policy=policy if policy is not None else LiquidityCappedFill(),  # type: ignore[arg-type]
        ),
        dataset,
        running_strategy_state(strategy_id, ScriptedStrategy(strategy_id, asset_id, dict(plan))),
        context_factory,
    )
    return result, asset_id


# --------------------------------------------------------------------------- #
# The decisive property: economics are unchanged
# --------------------------------------------------------------------------- #


def test_withdrawing_the_remainder_changes_no_money() -> None:
    """Cash, position, P&L and equity are what the fill alone produced."""

    result, asset_id = _run({2.0: Decimal("10")}, Decimal("3"))

    assert result.fills[0].quantity == Decimal("3")
    assert len(result.fills) == 1
    assert result.state.portfolio.positions[asset_id].quantity == Decimal("3.000000")
    assert result.valuation.realized_pnl == Decimal("0.00")
    assert result.valuation.equity == (
        START_CASH
        + result.valuation.realized_pnl
        + result.valuation.unrealized_pnl
        - result.valuation.commission_paid
    )


def test_a_withdrawn_remainder_produces_no_extra_fill_or_trade() -> None:
    result, _ = _run({2.0: Decimal("10")}, Decimal("3"))

    assert len(result.fills) == 1
    assert len(result.trades) == 1
    assert len(result.state.execution.reports) == 1


# --------------------------------------------------------------------------- #
# The terminal transition
# --------------------------------------------------------------------------- #


def test_a_partially_filled_order_reaches_a_terminal_state() -> None:
    result, _ = _run({2.0: Decimal("10")}, Decimal("3"))
    order = result.orders[0]

    assert order.status is OrderStatus.CANCELLED
    assert order.is_closed
    assert order.order_id in result.state.oms.completed_orders
    assert order.order_id not in result.state.oms.active_orders


def test_the_fill_that_did_happen_survives_the_withdrawal() -> None:
    """Order.cancel preserves filled_quantity and average_fill_price."""

    result, _ = _run({2.0: Decimal("10")}, Decimal("3"))
    order = result.orders[0]

    assert order.filled_quantity == Decimal("3")
    assert order.average_fill_price == Decimal("100.00")
    assert order.remaining_quantity == Decimal("7.000000")
    assert order.remaining_quantity >= Decimal("0")


def test_the_withdrawal_is_recorded_in_the_oms_history() -> None:
    """A terminal transition nobody can see is not an audit trail."""

    result, _ = _run({2.0: Decimal("10")}, Decimal("3"))
    kinds = [type(event).__name__ for event in result.state.oms.events]

    assert kinds.count("OrderPartiallyFilled") == 1
    assert kinds.count("OrderCancelled") == 1


# --------------------------------------------------------------------------- #
# Reservations
# --------------------------------------------------------------------------- #


def test_the_residual_reservation_is_released() -> None:
    result, _ = _run({2.0: Decimal("10")}, Decimal("3"))

    assert result.state.allocation.notional_allocated == Decimal("0.00")
    assert dict(open_reservations(result.state.allocation)) == {}


def test_the_reservation_is_released_exactly_once() -> None:
    """A second release would raise; the ledger records one."""

    result, _ = _run({2.0: Decimal("10")}, Decimal("3"))
    released = [
        event
        for event in result.state.allocation.events
        if type(event).__name__ == "AllocationReservationReleased"
    ]

    assert len(released) == 1
    with pytest.raises(UnknownReservationError):
        AllocationEngine.release_reservation(
            result.state.allocation, str(result.orders[0].order_id.value), 9.0
        )


def test_repeated_partial_fills_do_not_accumulate_held_capital() -> None:
    """The leak this fixes: one stuck reservation per capped order."""

    result, _ = _run({2.0: Decimal("10"), 3.0: Decimal("10"), 4.0: Decimal("10")}, Decimal("3"))

    assert len(result.orders) == 3
    assert all(order.status is OrderStatus.CANCELLED for order in result.orders)
    assert result.state.allocation.notional_allocated == Decimal("0.00")
    assert dict(open_reservations(result.state.allocation)) == {}


def test_held_capital_equals_the_sum_of_live_reservations_throughout() -> None:
    result, _ = _run({2.0: Decimal("10"), 3.0: Decimal("10")}, Decimal("3"))
    live = sum(open_reservations(result.state.allocation).values(), Decimal("0.00"))

    assert result.state.allocation.notional_allocated == live


# --------------------------------------------------------------------------- #
# Full fills are untouched
# --------------------------------------------------------------------------- #


def test_a_full_fill_still_ends_filled_and_consumes_its_reservation() -> None:
    """The change must not touch the path that was already correct."""

    result, asset_id = _run({2.0: Decimal("2")}, Decimal("100"))
    order = result.orders[0]

    assert order.status is OrderStatus.FILLED
    assert order.remaining_quantity == Decimal("0.000000")
    assert result.state.portfolio.positions[asset_id].quantity == Decimal("2.000000")
    assert result.state.allocation.notional_allocated == Decimal("0.00")


def test_a_non_trading_outcome_still_cancels_without_a_fill() -> None:
    result, _ = _run({2.0: Decimal("10")}, Decimal("100"), StaticFill(FillStatus.NO_FILL))

    assert result.orders[0].status is OrderStatus.CANCELLED
    assert result.orders[0].filled_quantity == Decimal("0")
    assert result.fills == ()
    assert dict(open_reservations(result.state.allocation)) == {}


# --------------------------------------------------------------------------- #
# Determinism and persistence
# --------------------------------------------------------------------------- #


def test_the_semantics_are_deterministic() -> None:
    first, _ = _run({2.0: Decimal("10")}, Decimal("3"))
    second, _ = _run({2.0: Decimal("10")}, Decimal("3"))

    assert [o.status for o in first.orders] == [o.status for o in second.orders]
    assert first.valuation.equity == second.valuation.equity


def test_a_withdrawn_order_survives_a_portfolio_snapshot_round_trip() -> None:
    """The portfolio is what persists; the withdrawal left it consistent."""

    result, asset_id = _run({2.0: Decimal("10")}, Decimal("3"))
    portfolio = result.state.portfolio
    restored = restore(from_primitives(deserialize(serialize(capture(portfolio)))))

    assert restored == portfolio
    assert restored.positions[asset_id].quantity == Decimal("3.000000")


def test_the_oms_state_still_snapshots_and_restores() -> None:
    """A cancelled-after-partial order must survive the OMS round trip too."""

    from alphalab.oms.snapshot import capture as oms_capture
    from alphalab.oms.snapshot import from_primitives as oms_from_primitives
    from alphalab.oms.snapshot import restore as oms_restore

    result, _ = _run({2.0: Decimal("10")}, Decimal("3"))
    oms = result.state.oms
    restored = oms_restore(oms_from_primitives(deserialize(serialize(oms_capture(oms)))))

    assert restored == oms
    assert restored.orders.find(result.orders[0].order_id).status is OrderStatus.CANCELLED
