"""AlphaLab Risk Engine."""

from alphalab.core.order_request import OrderRequest
from alphalab.risk.checks import (
    check_buying_power,
    check_classification,
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
from alphalab.risk.engine import RiskEngine
from alphalab.risk.events import (
    BuyingPowerUpdated,
    DrawdownTriggered,
    ExposureUpdated,
    MarginUpdated,
    RiskApproved,
    RiskCheckStarted,
    RiskEvent,
    RiskRejected,
)
from alphalab.risk.exceptions import RiskConfigurationError, RiskError, RiskValidationError
from alphalab.risk.exposure import ExposureStatus
from alphalab.risk.limits import (
    ClassificationLimit,
    DailyLossLimit,
    DrawdownLimit,
    ExposureLimit,
    LeverageLimit,
    MarginLimit,
    OrderSizeLimit,
    PositionLimit,
    RiskLimits,
)
from alphalab.risk.margin import MarginStatus
from alphalab.risk.models import RiskSeverity, RiskViolation
from alphalab.risk.projection import (
    NO_WORKING_ORDERS,
    BucketExposure,
    RiskProjection,
    WorkingExposure,
    project,
)
from alphalab.risk.state import RiskState
from alphalab.risk.validation import validate_order_request
from alphalab.risk.views import (
    active_limits,
    current_exposure,
    latest_decision,
    margin_status,
    risk_history,
    violations,
)

__all__ = [
    "NO_WORKING_ORDERS",
    "BucketExposure",
    "BuyingPowerUpdated",
    "ClassificationLimit",
    "DailyLossLimit",
    "DrawdownLimit",
    "DrawdownTriggered",
    "ExposureLimit",
    "ExposureStatus",
    "ExposureUpdated",
    "LeverageLimit",
    "MarginLimit",
    "MarginStatus",
    "MarginUpdated",
    "OrderRequest",
    "OrderSizeLimit",
    "PositionLimit",
    "RiskApproved",
    "RiskCheckStarted",
    "RiskConfigurationError",
    "RiskDecision",
    "RiskEngine",
    "RiskError",
    "RiskEvent",
    "RiskLimits",
    "RiskProjection",
    "RiskRejected",
    "RiskSeverity",
    "RiskState",
    "RiskValidationError",
    "RiskViolation",
    "WorkingExposure",
    "active_limits",
    "check_buying_power",
    "check_classification",
    "check_daily_loss",
    "check_drawdown",
    "check_exposure",
    "check_leverage",
    "check_margin",
    "check_net_exposure",
    "check_order_size",
    "check_position_limit",
    "current_exposure",
    "daily_loss_breach",
    "drawdown_breach",
    "latest_decision",
    "margin_status",
    "project",
    "risk_history",
    "validate_order_request",
    "violations",
]
