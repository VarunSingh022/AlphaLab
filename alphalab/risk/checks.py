"""Pure risk check functions over a projected book, returning a violation or ``None``.

Each check reads the order, the gate's state and the order's
:class:`~alphalab.risk.projection.RiskProjection`. Every limit is inclusive: a
figure *equal* to its limit is allowed, and anything beyond it is refused.

Reduce-only
-----------
The position, exposure, leverage, margin, drawdown and daily-loss limits refuse
only an order that makes their measure worse. An order that reduces a position
-- including a liquidation during a drawdown or daily-loss breach -- is never
refused by them; see :mod:`alphalab.risk.projection`. The order-size limit is
the exception, deliberately: it caps what one order may do, in either direction.
"""

from decimal import Decimal

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.core.order_request import OrderRequest
from alphalab.risk.models import RiskSeverity, RiskViolation
from alphalab.risk.projection import BucketExposure, RiskProjection
from alphalab.risk.state import RiskState

_ZERO = Decimal("0")


def check_order_size(
    request: OrderRequest, state: RiskState, projection: RiskProjection
) -> RiskViolation | None:
    """The order's quantity and its notional in the base currency."""

    limit = state.active_limits.order_size
    if request.quantity > limit.max_quantity:
        return RiskViolation(
            rule="OrderSizeQuantity",
            description="Order quantity exceeds max allowed.",
            severity=RiskSeverity.HIGH,
            current_value=request.quantity,
            allowed_value=limit.max_quantity,
        )
    notional = ACCOUNTING_CONTEXT.multiply(request.quantity, projection.price)
    if notional > limit.max_notional:
        return RiskViolation(
            rule="OrderSizeNotional",
            description="Order notional value exceeds max allowed.",
            severity=RiskSeverity.HIGH,
            current_value=notional,
            allowed_value=limit.max_notional,
        )
    return None


def check_position_limit(
    request: OrderRequest, state: RiskState, projection: RiskProjection
) -> RiskViolation | None:
    """The projected signed position: in units, then in base-currency value.

    Until v3.10 this added the asset's *market value* to the order's *quantity*
    and compared the sum with ``max_quantity`` -- a figure in no unit at all.
    """

    if not projection.increases_exposure:
        return None
    limit = state.active_limits.position
    magnitude = abs(projection.projected_position)
    if magnitude > limit.max_quantity:
        return RiskViolation(
            rule="PositionLimit",
            description="Projected position exceeds maximum allowed.",
            severity=RiskSeverity.HIGH,
            current_value=magnitude,
            allowed_value=limit.max_quantity,
        )
    value = projection.projected_position_value
    if value > limit.max_notional:
        return RiskViolation(
            rule="PositionNotionalLimit",
            description="Projected position value exceeds maximum allowed.",
            severity=RiskSeverity.HIGH,
            current_value=value,
            allowed_value=limit.max_notional,
        )
    return None


def check_margin(
    request: OrderRequest, state: RiskState, projection: RiskProjection
) -> RiskViolation | None:
    """Projected margin utilization under a full-notional margin requirement.

    The requirement is the book's gross exposure -- every position margined at
    its full value, as a cash account is -- and the margin available is net
    asset value, so utilization is projected gross exposure over NAV.
    Asset-class-aware margin (a future's initial margin, a portfolio-margin
    account) is declared per instrument from v3.11 (ledger RSK-006).

    With no positive NAV the utilization is undefined, and an order that grows
    gross exposure is refused. Until v3.10 a zero margin base read as zero
    utilization and passed every order.
    """

    if projection.projected_gross <= projection.committed_gross:
        return None
    limit = state.active_limits.margin
    if projection.nav <= _ZERO:
        return RiskViolation(
            rule="MarginLimit",
            description="Margin utilization is undefined with no positive net asset value.",
            severity=RiskSeverity.CRITICAL,
            current_value=projection.projected_gross,
            allowed_value=_ZERO,
        )
    utilization = ACCOUNTING_CONTEXT.divide(projection.projected_gross, projection.nav)
    if utilization > limit.max_margin_utilization:
        return RiskViolation(
            rule="MarginLimit",
            description="Projected margin utilization exceeds limit.",
            severity=RiskSeverity.CRITICAL,
            current_value=utilization,
            allowed_value=limit.max_margin_utilization,
        )
    return None


def check_exposure(
    request: OrderRequest, state: RiskState, projection: RiskProjection
) -> RiskViolation | None:
    """Projected gross exposure: long value plus the magnitude of short value."""

    if projection.projected_gross <= projection.committed_gross:
        return None
    limit = state.active_limits.exposure
    if projection.projected_gross > limit.max_gross_exposure:
        return RiskViolation(
            rule="GrossExposureLimit",
            description="Projected gross exposure exceeds limit.",
            severity=RiskSeverity.HIGH,
            current_value=projection.projected_gross,
            allowed_value=limit.max_gross_exposure,
        )
    return None


def check_net_exposure(
    request: OrderRequest, state: RiskState, projection: RiskProjection
) -> RiskViolation | None:
    """The magnitude of projected net exposure: long value plus (negative) short value.

    Declared since v1 and read by nothing until v3.10 (ledger KD-003).
    """

    projected = abs(projection.projected_net)
    if projected <= abs(projection.committed_net):
        return None
    limit = state.active_limits.exposure
    if projected > limit.max_net_exposure:
        return RiskViolation(
            rule="NetExposureLimit",
            description="Projected net exposure exceeds limit.",
            severity=RiskSeverity.HIGH,
            current_value=projected,
            allowed_value=limit.max_net_exposure,
        )
    return None


def check_classification(
    state: RiskState, projection: RiskProjection, buckets: tuple[BucketExposure, ...]
) -> RiskViolation | None:
    """Each bucket the order's instrument is in, against the limits that bind it (OFE-001).

    Reduce-only: a bucket whose gross exposure the order does not grow is not
    refused. An instrument carrying no label along a limited dimension is
    refused only when the dimension's limit says so and the order grows its
    position.
    """

    limits = state.active_limits
    for bucket in buckets:
        binding = limits.bucket_limits(bucket.dimension, bucket.label)
        if bucket.label is None:
            if projection.increases_exposure and any(
                limit.refuse_unclassified for limit in binding
            ):
                return RiskViolation(
                    rule="ClassificationUnclassified",
                    description=(
                        f"The instrument carries no {bucket.dimension}, and the "
                        f"{bucket.dimension} limit refuses an unclassified instrument."
                    ),
                    severity=RiskSeverity.HIGH,
                    current_value=projection.projected_position_value,
                    allowed_value=_ZERO,
                )
            continue
        if bucket.projected_gross <= bucket.committed_gross:
            continue
        for limit in binding:
            if limit.max_gross is not None and bucket.projected_gross > limit.max_gross:
                return RiskViolation(
                    rule="ClassificationLimit",
                    description=(
                        f"Projected gross exposure of {bucket.dimension} {bucket.label!r} "
                        "exceeds its limit."
                    ),
                    severity=RiskSeverity.HIGH,
                    current_value=bucket.projected_gross,
                    allowed_value=limit.max_gross,
                )
            if limit.max_share is not None:
                if projection.nav <= _ZERO:
                    return RiskViolation(
                        rule="ClassificationShareLimit",
                        description=(
                            f"{bucket.dimension} {bucket.label!r}'s share of net asset value "
                            "is undefined with no positive net asset value."
                        ),
                        severity=RiskSeverity.HIGH,
                        current_value=bucket.projected_gross,
                        allowed_value=_ZERO,
                    )
                share = ACCOUNTING_CONTEXT.divide(bucket.projected_gross, projection.nav)
                if share > limit.max_share:
                    return RiskViolation(
                        rule="ClassificationShareLimit",
                        description=(
                            f"Projected gross exposure of {bucket.dimension} "
                            f"{bucket.label!r} exceeds its share of net asset value."
                        ),
                        severity=RiskSeverity.HIGH,
                        current_value=share,
                        allowed_value=limit.max_share,
                    )
    return None


def check_drawdown(state: RiskState, projection: RiskProjection) -> RiskViolation | None:
    """In a drawdown breach, refuse what grows a position; let reductions through."""

    breach = drawdown_breach(state)
    if breach is None or not projection.increases_exposure:
        return None
    return breach


def drawdown_breach(state: RiskState) -> RiskViolation | None:
    """The drawdown breach the account is in, whatever order is being judged."""

    limit = state.active_limits.drawdown
    drawdown = state.current_drawdown_pct
    if drawdown > limit.max_drawdown_pct:
        return RiskViolation(
            rule="DrawdownLimit",
            description="Account has breached maximum drawdown limit.",
            severity=RiskSeverity.CRITICAL,
            current_value=drawdown,
            allowed_value=limit.max_drawdown_pct,
        )
    return None


def check_buying_power(
    request: OrderRequest, state: RiskState, projection: RiskProjection
) -> RiskViolation | None:
    """Charge buying power only for the part of the order that grows a position.

    A sale that reduces a long, or a purchase that covers a short, spends no
    buying power. Until v3.10 every order was charged its full notional, so a
    fully invested account could not sell (ledger RSK-001).
    """

    required = projection.increasing_notional
    if required <= _ZERO:
        return None
    available = projection.available_buying_power
    if required > available:
        return RiskViolation(
            rule="BuyingPowerLimit",
            description="Insufficient buying power for order.",
            severity=RiskSeverity.HIGH,
            current_value=required,
            allowed_value=available,
        )
    return None


def check_daily_loss(state: RiskState, projection: RiskProjection) -> RiskViolation | None:
    """In a daily-loss breach, refuse what grows a position; let reductions through."""

    breach = daily_loss_breach(state)
    if breach is None or not projection.increases_exposure:
        return None
    return breach


def daily_loss_breach(state: RiskState) -> RiskViolation | None:
    """The daily-loss breach the account is in, whatever order is being judged."""

    limit = state.active_limits.daily_loss
    if limit is None or state.daily_loss <= limit.max_daily_loss:
        return None
    return RiskViolation(
        rule="DailyLossLimit",
        description="Account has breached maximum daily loss.",
        severity=RiskSeverity.CRITICAL,
        current_value=state.daily_loss,
        allowed_value=limit.max_daily_loss,
    )


def check_leverage(
    request: OrderRequest, state: RiskState, projection: RiskProjection
) -> RiskViolation | None:
    """Projected gross exposure over net asset value.

    With no positive NAV leverage is undefined, and an order that grows gross
    exposure is refused rather than passed.
    """

    if projection.projected_gross <= projection.committed_gross:
        return None
    limit = state.active_limits.leverage
    if projection.nav <= _ZERO:
        return RiskViolation(
            rule="LeverageLimit",
            description="Leverage is undefined with no positive net asset value.",
            severity=RiskSeverity.HIGH,
            current_value=projection.projected_gross,
            allowed_value=_ZERO,
        )
    leverage = ACCOUNTING_CONTEXT.divide(projection.projected_gross, projection.nav)
    if leverage > limit.max_leverage:
        return RiskViolation(
            rule="LeverageLimit",
            description="Projected leverage exceeds maximum allowed.",
            severity=RiskSeverity.HIGH,
            current_value=leverage,
            allowed_value=limit.max_leverage,
        )
    return None
