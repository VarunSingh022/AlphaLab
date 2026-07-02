"""Pure functional Allocation Engine."""

import uuid
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal

from alphalab.allocation.allocator import IntentAllocator
from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.constraints import AllocationConstraints
from alphalab.allocation.events import (
    AllocationCompleted,
    AllocationRejected,
    AllocationStarted,
    BudgetExceeded,
    NettingCompleted,
)
from alphalab.allocation.netting import NettingEngine
from alphalab.allocation.request import OrderRequest, OrderSide
from alphalab.allocation.sizing import SizingModel
from alphalab.allocation.state import AllocationState
from alphalab.allocation.validation import validate_intent, validate_net_quantity
from alphalab.strategy.events import Intent


class AllocationEngine:
    """Stateless engine responsible for sizing, netting, and budgeting."""

    @staticmethod
    def _create_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def initialize(budget: CapitalBudget) -> AllocationState:
        """Returns a fresh allocation state initialized with provided budget."""
        return AllocationState(budget=budget)

    @staticmethod
    def allocate(
        state: AllocationState,
        intents: tuple[Intent, ...],
        market_prices: Mapping[str, Decimal],
        sizing_model: SizingModel,
        constraints: AllocationConstraints,
        timestamp: float,
    ) -> tuple[AllocationState, tuple[OrderRequest, ...]]:
        """
        Processes a batch of intents, sizes them, applies cross-strategy netting,
        checks capital budgets, and emits netted OrderRequests.
        """
        events = list(state.events)
        events.append(AllocationStarted(AllocationEngine._create_id(), timestamp, len(intents)))

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
                events.append(AllocationRejected(AllocationEngine._create_id(), timestamp, str(e)))

        if not valid_intents:
            return replace(state, events=tuple(events)), ()

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
                events.append(AllocationRejected(AllocationEngine._create_id(), timestamp, str(e)))
                continue

            if constraints.enforce_integer_quantities:
                net_qty = net_qty.to_integral_value()

            side = OrderSide.BUY if net_qty > Decimal("0") else OrderSide.SELL
            abs_qty = abs(net_qty)
            price = market_prices.get(asset_id, Decimal("0.00"))

            notional = abs_qty * price
            total_notional += notional

            events.append(
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
            events.append(
                BudgetExceeded(
                    AllocationEngine._create_id(), timestamp, reason, total_notional, available_cap
                )
            )
            # Strict rejection mode: if batch breaches budget, drop batch.
            return replace(state, events=tuple(events)), ()

        # 6. Finalization
        events.append(
            AllocationCompleted(
                AllocationEngine._create_id(), timestamp, len(orders), total_notional
            )
        )

        new_state = replace(
            state,
            history=(*state.history, *orders),
            events=tuple(events),
            notional_allocated=state.notional_allocated + total_notional,
        )

        return new_state, tuple(orders)
