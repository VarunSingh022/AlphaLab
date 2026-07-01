"""Pure functional execution engine."""

import uuid
from dataclasses import replace
from decimal import Decimal

from alphalab.execution.events import (
    ExecutionCompleted,
    ExecutionExpired,
    ExecutionPartiallyFilled,
    ExecutionRejected,
    ExecutionSubmitted,
)
from alphalab.execution.fill import FillStatus, OrderInstruction
from alphalab.execution.report import ExecutionReport
from alphalab.execution.simulator import ExecutionSimulator
from alphalab.execution.state import ExecutionState


class ExecutionEngine:
    """Stateless functional execution engine."""

    @staticmethod
    def _create_event_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def execute(
        state: ExecutionState,
        report: ExecutionReport,
    ) -> ExecutionState:
        """Applies a full fill execution report to the state."""
        new_reports = dict(state.reports)
        new_reports[report.execution_id] = report

        event = ExecutionCompleted(
            event_id=ExecutionEngine._create_event_id(),
            timestamp=report.timestamp,
            order_id=report.order_id,
            execution_id=report.execution_id,
            fill_price=report.fill_price,
            fill_quantity=report.fill_quantity,
        )

        return replace(
            state,
            reports=new_reports,
            history=(*state.history, report),
            events=(*state.events, event),
        )

    @staticmethod
    def partial_fill(
        state: ExecutionState,
        report: ExecutionReport,
        remaining_quantity: Decimal,
    ) -> ExecutionState:
        """Applies a partial fill execution report to the state."""
        new_reports = dict(state.reports)
        new_reports[report.execution_id] = report

        event = ExecutionPartiallyFilled(
            event_id=ExecutionEngine._create_event_id(),
            timestamp=report.timestamp,
            order_id=report.order_id,
            execution_id=report.execution_id,
            fill_price=report.fill_price,
            fill_quantity=report.fill_quantity,
            remaining_quantity=remaining_quantity,
        )

        return replace(
            state,
            reports=new_reports,
            history=(*state.history, report),
            events=(*state.events, event),
        )

    @staticmethod
    def reject(
        state: ExecutionState,
        instruction: OrderInstruction,
        reason: str,
        timestamp: float,
    ) -> ExecutionState:
        """Records an execution rejection."""
        event = ExecutionRejected(
            event_id=ExecutionEngine._create_event_id(),
            timestamp=timestamp,
            order_id=instruction.order_id,
            reason=reason,
        )
        return replace(state, events=(*state.events, event))

    @staticmethod
    def expire(
        state: ExecutionState,
        instruction: OrderInstruction,
        timestamp: float,
    ) -> ExecutionState:
        """Records an execution expiration."""
        event = ExecutionExpired(
            event_id=ExecutionEngine._create_event_id(),
            timestamp=timestamp,
            order_id=instruction.order_id,
        )
        return replace(state, events=(*state.events, event))

    @staticmethod
    def simulate(
        state: ExecutionState,
        simulator: ExecutionSimulator,
        instruction: OrderInstruction,
        fill_quantity: Decimal,
        market_price: Decimal,
        timestamp: float,
        status: FillStatus,
    ) -> ExecutionState:
        """Fully simulates an execution and updates the state deterministically."""
        # 1. Log submission event
        sub_event = ExecutionSubmitted(
            event_id=ExecutionEngine._create_event_id(),
            timestamp=timestamp,
            order_id=instruction.order_id,
            asset_id=instruction.asset_id,
            quantity=instruction.quantity,
            price=instruction.price,
        )

        state_with_sub = replace(state, events=(*state.events, sub_event))

        # 2. Simulate
        report = simulator.simulate_fill(
            instruction=instruction,
            fill_quantity=fill_quantity,
            market_price=market_price,
            timestamp=timestamp,
            status=status,
        )

        # 3. Apply state change
        if status == FillStatus.FULL_FILL:
            return ExecutionEngine.execute(state_with_sub, report)
        elif status == FillStatus.PARTIAL_FILL:
            remaining = instruction.quantity - fill_quantity
            return ExecutionEngine.partial_fill(state_with_sub, report, remaining)
        elif status == FillStatus.REJECTED:
            return ExecutionEngine.reject(
                state_with_sub,
                instruction,
                "Rejected by simulator",
                timestamp,
            )
        elif status == FillStatus.EXPIRED:
            return ExecutionEngine.expire(state_with_sub, instruction, timestamp)

        return state_with_sub
