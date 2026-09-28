"""Pure functional Risk Engine controlling order approvals."""

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal

from alphalab.common.arithmetic import in_accounting_context
from alphalab.common.ids import new_id
from alphalab.core.order_request import OrderRequest
from alphalab.risk.checks import (
    check_buying_power,
    check_daily_loss,
    check_drawdown,
    check_exposure,
    check_leverage,
    check_margin,
    check_net_exposure,
    check_order_size,
    check_position_limit,
    daily_loss_breach,
    drawdown_breach,
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
from alphalab.risk.models import RiskViolation
from alphalab.risk.projection import NO_WORKING_ORDERS, RiskProjection, WorkingExposure, project
from alphalab.risk.state import RiskState
from alphalab.risk.validation import validate_order_request


class RiskEngine:
    """Stateless engine responsible for generating deterministic risk decisions."""

    @staticmethod
    def _create_id() -> str:
        return str(new_id())

    @staticmethod
    def reset(limits: RiskLimits) -> RiskState:
        """Returns a fresh risk state initialized with provided limits."""
        return RiskState(active_limits=limits)

    @staticmethod
    @in_accounting_context
    def evaluate(
        state: RiskState,
        request: OrderRequest,
        timestamp: float,
        *,
        position: Decimal = Decimal("0"),
        price: Decimal | None = None,
        working: Mapping[str, WorkingExposure] = NO_WORKING_ORDERS,
    ) -> tuple[RiskState, RiskDecision]:
        """Judge ``request`` against every limit, on the book it would leave.

        Args:
            state: The gate's view of the book, every figure in the base
                currency.
            request: The order.
            timestamp: When it is judged.
            position: The asset's current signed filled quantity.
            price: The order's price in the base currency; the request's own
                price when ``None`` (a single-currency book).
            working: Every asset with working orders, and what they commit. See
                :mod:`alphalab.risk.projection`.
        """

        validate_order_request(request)

        start_event = RiskCheckStarted(RiskEngine._create_id(), timestamp, request)
        events = state.events.append(start_event)

        projection = project(
            state,
            request,
            position=position,
            price=request.price if price is None else price,
            working=working,
        )

        checks = (
            check_order_size(request, state, projection),
            check_position_limit(request, state, projection),
            check_margin(request, state, projection),
            check_exposure(request, state, projection),
            check_net_exposure(request, state, projection),
            check_drawdown(state, projection),
            check_buying_power(request, state, projection),
            check_daily_loss(state, projection),
            check_leverage(request, state, projection),
        )
        violations = tuple(result for result in checks if result is not None)
        breaches = RiskEngine.breaches(state)

        decision_id = RiskEngine._create_id()

        if violations:
            decision = RiskEngine.reject(
                decision_id, request, timestamp, violations, state, breaches
            )
            reject_event = RiskRejected(
                RiskEngine._create_id(), timestamp, decision_id, request.order_id, decision.reason
            )
            events = events.append(reject_event)
        else:
            decision = RiskEngine.approve(
                decision_id, request, timestamp, state, projection, breaches
            )
            approve_event = RiskApproved(
                RiskEngine._create_id(), timestamp, decision_id, request.order_id
            )
            events = events.append(approve_event)

        new_state = replace(
            state,
            history=state.history.append(decision),
            events=events,
        )
        return new_state, decision

    @staticmethod
    def breaches(state: RiskState) -> tuple[RiskViolation, ...]:
        """The account-level limits the account is currently in breach of."""

        return tuple(
            breach
            for breach in (drawdown_breach(state), daily_loss_breach(state))
            if breach is not None
        )

    @staticmethod
    def approve(
        decision_id: str,
        request: OrderRequest,
        timestamp: float,
        state: RiskState,
        projection: RiskProjection | None = None,
        breaches: tuple[RiskViolation, ...] = (),
    ) -> RiskDecision:
        """Constructs an approved Risk Decision.

        ``required_margin`` is what the order commits of buying power: the value
        of the part that grows a position, taken from ``projection``. Without
        one it is the order's full notional, which is what the gate charged
        every order until v3.10.
        """

        committed = (
            projection.increasing_notional if projection is not None else request.notional_value
        )
        available = (
            projection.available_buying_power if projection is not None else state.buying_power
        )
        return RiskDecision(
            decision_id=decision_id,
            timestamp=timestamp,
            order_id=request.order_id,
            approved=True,
            reason="All risk checks passed.",
            required_margin=committed,
            remaining_buying_power=available - committed,
            exposure=state.exposure,
            breaches=breaches,
        )

    @staticmethod
    def reject(
        decision_id: str,
        request: OrderRequest,
        timestamp: float,
        violations: tuple[RiskViolation, ...],
        state: RiskState,
        breaches: tuple[RiskViolation, ...] = (),
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
            breaches=breaches,
        )

    @staticmethod
    @in_accounting_context
    def mark(state: RiskState, *, nav: Decimal, cash: Decimal, timestamp: float) -> RiskState:
        """Record the book's net asset value and cash at ``timestamp``.

        Moves the high-water mark, and maintains the daily loss against the
        trading day the :class:`~alphalab.risk.limits.DailyLossLimit` declares:
        when ``timestamp`` falls in a new trading day, that day starts from the
        value the book closed the previous one at (its first mark, the first
        time). The daily loss is how far NAV has fallen since, never negative.
        With no daily loss limit there is no day to measure and it stays zero.

        Buying power is the cash available, never negative.
        """

        limit = state.active_limits.daily_loss
        trading_day = state.trading_day
        day_start = state.day_start_nav
        daily_loss = Decimal("0.00")
        if limit is not None:
            today = limit.trading_day(timestamp).isoformat()
            if trading_day != today:
                day_start = state.current_nav if trading_day is not None else nav
                trading_day = today
            assert day_start is not None  # set with the trading day, just above
            daily_loss = max(Decimal("0.00"), day_start - nav)

        return replace(
            state,
            cash=cash,
            buying_power=max(Decimal("0.00"), cash),
            current_nav=nav,
            peak_nav=max(state.peak_nav, nav),
            daily_loss=daily_loss,
            trading_day=trading_day,
            day_start_nav=day_start,
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
            events=state.events.append(event),
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
            events=state.events.append(event),
        )
