"""Pure functional Risk Engine controlling order approvals."""

import uuid
from dataclasses import replace

from alphalab.risk.checks import (
    check_buying_power,
    check_daily_loss,
    check_drawdown,
    check_exposure,
    check_leverage,
    check_margin,
    check_order_size,
    check_position_limit,
)
from alphalab.risk.decision import RiskDecision
from alphalab.risk.events import (
    ExposureUpdated,
    MarginUpdated,
    RiskApproved,
    RiskCheckStarted,
    RiskRejected,
)
from alphalab.risk.exposure import ExposureStatus
from alphalab.risk.limits import RiskLimits
from alphalab.risk.margin import MarginStatus
from alphalab.risk.models import OrderRequest, RiskViolation
from alphalab.risk.state import RiskState
from alphalab.risk.validation import validate_order_request


class RiskEngine:
    """Stateless engine responsible for generating deterministic risk decisions."""

    @staticmethod
    def _create_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def reset(limits: RiskLimits) -> RiskState:
        """Returns a fresh risk state initialized with provided limits."""
        return RiskState(active_limits=limits)

    @staticmethod
    def evaluate(
        state: RiskState, request: OrderRequest, timestamp: float
    ) -> tuple[RiskState, RiskDecision]:
        """Runs all configured risk checks and returns an updated state with a firm decision."""
        validate_order_request(request)

        # Emit start event
        start_event = RiskCheckStarted(RiskEngine._create_id(), timestamp, request)
        events = (*state.events, start_event)

        # Run independent checks
        violations: list[RiskViolation] = []

        checks = [
            check_order_size(request, state),
            check_position_limit(request, state),
            check_margin(request, state),
            check_exposure(request, state),
            check_drawdown(state),
            check_buying_power(request, state),
            check_daily_loss(state),
            check_leverage(request, state),
        ]

        for result in checks:
            if result is not None:
                violations.append(result)

        decision_id = RiskEngine._create_id()

        if violations:
            decision = RiskEngine.reject(decision_id, request, timestamp, tuple(violations), state)
            reject_event = RiskRejected(
                RiskEngine._create_id(), timestamp, decision_id, request.order_id, decision.reason
            )
            events = (*events, reject_event)
        else:
            decision = RiskEngine.approve(decision_id, request, timestamp, state)
            approve_event = RiskApproved(
                RiskEngine._create_id(), timestamp, decision_id, request.order_id
            )
            events = (*events, approve_event)

        new_state = replace(
            state,
            history=(*state.history, decision),
            events=events,
        )
        return new_state, decision

    @staticmethod
    def approve(
        decision_id: str, request: OrderRequest, timestamp: float, state: RiskState
    ) -> RiskDecision:
        """Constructs an approved Risk Decision."""
        return RiskDecision(
            decision_id=decision_id,
            timestamp=timestamp,
            order_id=request.order_id,
            approved=True,
            reason="All risk checks passed.",
            required_margin=request.notional_value,
            remaining_buying_power=state.buying_power - request.notional_value,
            exposure=state.exposure,
        )

    @staticmethod
    def reject(
        decision_id: str,
        request: OrderRequest,
        timestamp: float,
        violations: tuple[RiskViolation, ...],
        state: RiskState,
    ) -> RiskDecision:
        """Constructs a rejected Risk Decision containing constraint violations."""
        reason = f"Rejected due to {len(violations)} constraint violations."
        return RiskDecision(
            decision_id=decision_id,
            timestamp=timestamp,
            order_id=request.order_id,
            approved=False,
            reason=reason,
            violations=violations,
            required_margin=state.margin.margin_used,
            remaining_buying_power=state.buying_power,
            exposure=state.exposure,
        )

    @staticmethod
    def update_margin(state: RiskState, margin: MarginStatus, timestamp: float) -> RiskState:
        """Integrates a new margin snapshot from the Portfolio Engine."""
        event = MarginUpdated(
            RiskEngine._create_id(), timestamp, margin.margin_used, margin.available_margin
        )
        return replace(
            state,
            margin=margin,
            events=(*state.events, event),
        )

    @staticmethod
    def update_exposure(state: RiskState, exposure: ExposureStatus, timestamp: float) -> RiskState:
        """Integrates a new exposure snapshot from the Portfolio Engine."""
        event = ExposureUpdated(
            RiskEngine._create_id(), timestamp, exposure.gross_exposure, exposure.net_exposure
        )
        return replace(
            state,
            exposure=exposure,
            events=(*state.events, event),
        )
