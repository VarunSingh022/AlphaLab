"""Pure functional Allocation Engine."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import Decimal

from alphalab.allocation.allocator import IntentAllocator
from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.allocation.events import (
    AllocationCompleted,
    AllocationExecutionApplied,
    AllocationRejected,
    AllocationReservationReleased,
    AllocationStarted,
    BudgetExceeded,
    NettingCompleted,
)
from alphalab.allocation.exceptions import UnknownReservationError
from alphalab.allocation.netting import NettingEngine
from alphalab.allocation.sizing import SizingModel
from alphalab.allocation.state import AllocationState
from alphalab.allocation.validation import validate_intent, validate_net_quantity
from alphalab.common.ids import new_id
from alphalab.core.enums import Side
from alphalab.core.order_request import OrderRequest
from alphalab.strategy.events import Intent


class AllocationEngine:
    """Stateless engine responsible for sizing, netting, and budgeting."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def initialize(budget: CapitalBudget) -> AllocationState:
        """Returns a fresh allocation state initialized with provided budget."""
        return AllocationState(budget=budget)

    @staticmethod
    def allocate(
        state: AllocationState,
        intents: Sequence[Intent],
        market_prices: Mapping[str, Decimal],
        sizing_model: SizingModel,
        constraints: AllocationConstraints,
        timestamp: float,
    ) -> tuple[AllocationState, tuple[OrderRequest, ...]]:
        """
        Processes a batch of intents, sizes them, applies cross-strategy netting,
        checks capital budgets, and emits netted OrderRequests.
        """
        events = state.events.append(
            AllocationStarted(AllocationEngine._create_id(), timestamp, len(intents))
        )

        # 1. Validation
        valid_intents = []
        for intent in intents:
            try:
                validate_intent(intent)
                # Reject duplicates dynamically in batch
                if intent in valid_intents:
                    continue
                valid_intents.append(intent)
            except Exception as e:
                events = events.append(
                    AllocationRejected(AllocationEngine._create_id(), timestamp, str(e))
                )

        if not valid_intents:
            return replace(state, events=events), ()

        # 2. Sizing
        sized_deltas = IntentAllocator.size_intents(
            tuple(valid_intents), state.budget, market_prices, sizing_model
        )

        # 3. Netting
        net_quantities = NettingEngine.net_quantities(sized_deltas)

        # 4. Enforce constraints & Budget Pre-check
        total_notional = Decimal("0.00")
        orders: list[OrderRequest] = []

        for asset_id, net_qty in net_quantities.items():
            if net_qty == Decimal("0.00"):
                continue

            try:
                validate_net_quantity(net_qty, enforce_long_only=not constraints.allow_shorting)
            except Exception as e:
                events = events.append(
                    AllocationRejected(AllocationEngine._create_id(), timestamp, str(e))
                )
                continue

            if constraints.enforce_integer_quantities:
                net_qty = net_qty.to_integral_value()

            side = Side.BUY if net_qty > Decimal("0") else Side.SELL
            abs_qty = abs(net_qty)
            price = market_prices.get(asset_id, Decimal("0.00"))

            notional = abs_qty * price
            total_notional += notional

            events = events.append(
                NettingCompleted(
                    AllocationEngine._create_id(), timestamp, asset_id, abs_qty, side.name
                )
            )

            orders.append(
                OrderRequest(
                    order_id=AllocationEngine._create_id(),
                    strategy_id="ALLOC-NETTED",
                    asset_id=asset_id,
                    side=side,
                    quantity=abs_qty,
                    price=price,
                    timestamp=timestamp,
                )
            )

        # 5. Budget Application
        available_cap = state.budget.available_global_capital
        if total_notional > available_cap or total_notional > state.budget.maximum_exposure:
            reason = "Requested notional exceeds global capital or exposure limits."
            events = events.append(
                BudgetExceeded(
                    AllocationEngine._create_id(), timestamp, reason, total_notional, available_cap
                )
            )
            # Strict rejection mode: if batch breaches budget, drop batch.
            return replace(state, events=events), ()

        # 6. Finalization
        events = events.append(
            AllocationCompleted(
                AllocationEngine._create_id(), timestamp, len(orders), total_notional
            )
        )

        # Every emitted request reserves its own notional, so the capital held
        # against it can later be consumed or released by order id.
        reservations = state.reservations
        for order in orders:
            reservations = reservations.set(order.order_id, order.quantity * order.price)

        new_state = replace(
            state,
            history=state.history.extend(orders),
            events=events,
            notional_allocated=state.notional_allocated + total_notional,
            reservations=reservations,
        )

        return new_state, tuple(orders)

    @staticmethod
    def reserved_notional(state: AllocationState, order_id: str) -> Decimal:
        """Capital still held against ``order_id``; zero if it holds none."""

        return state.reservations.get(order_id, Decimal("0.00"))

    @staticmethod
    def apply_execution(
        state: AllocationState, order_id: str, executed_notional: Decimal, timestamp: float
    ) -> AllocationState:
        """Consume executed notional from the capital reserved for an order.

        A fill consumes the reservation up to what it executed. A fully
        executed order's entry is dropped from the ledger; a partial fill
        leaves the residual reserved, because the order is still working and
        that capital is still committed.
        """

        reserved = state.reservations.get(order_id)
        if reserved is None:
            # The order holds no reservation (it was released, or fully
            # consumed by earlier fills). Record the execution; nothing to free.
            consumed = Decimal("0.00")
            reservations = state.reservations
        else:
            consumed = min(reserved, executed_notional)
            remaining = reserved - consumed
            reservations = (
                state.reservations.delete(order_id)
                if remaining <= Decimal("0.00")
                else state.reservations.set(order_id, remaining)
            )

        evt = AllocationExecutionApplied(
            AllocationEngine._create_id(), timestamp, order_id, executed_notional
        )
        return replace(
            state,
            notional_allocated=state.notional_allocated - consumed,
            reservations=reservations,
            events=state.events.append(evt),
        )

    @staticmethod
    def release_reservation(
        state: AllocationState, order_id: str, timestamp: float
    ) -> AllocationState:
        """Release whatever capital ``order_id`` still holds.

        Called once, at the point a request's lifecycle ends without further
        execution: risk rejected it, it was dropped before reaching the OMS, or
        the venue returned a non-trading outcome. The amount comes from the
        ledger rather than from the caller, so a release can neither free more
        than was reserved nor free the same reservation twice.

        Raises:
            UnknownReservationError: if the order holds no live reservation.
        """

        released = state.reservations.get(order_id)
        if released is None:
            raise UnknownReservationError(
                f"Order {order_id} holds no allocation reservation to release."
            )

        evt = AllocationReservationReleased(
            AllocationEngine._create_id(), timestamp, order_id, released
        )
        return replace(
            state,
            notional_allocated=state.notional_allocated - released,
            reservations=state.reservations.delete(order_id),
            events=state.events.append(evt),
        )
