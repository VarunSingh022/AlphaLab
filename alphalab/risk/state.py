"""Global immutable state container for the Risk Engine."""

from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.common.append_log import AppendOnlyLog
from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.risk.decision import RiskDecision
from alphalab.risk.events import RiskEvent
from alphalab.risk.exposure import ExposureStatus
from alphalab.risk.limits import RiskLimits
from alphalab.risk.margin import MarginStatus

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class RiskState:
    """Deterministic snapshot of account risk limits and utilization.

    Every monetary figure is in the account's base currency.

    Attributes:
        daily_loss: How far net asset value has fallen since the current trading
            day began (never negative). Maintained by
            :meth:`~alphalab.risk.engine.RiskEngine.mark` against the day the
            :class:`~alphalab.risk.limits.DailyLossLimit` declares; zero when
            the account has no daily loss limit. Until v3.10 nothing maintained
            it, so it was always zero and the limit was never enforced.
        trading_day: The ISO date of the trading day ``daily_loss`` is measured
            within, in the limit's zone; ``None`` before the first mark or with
            no daily loss limit.
        day_start_nav: Net asset value when that trading day began: the value
            the book closed the previous day at, or its first mark.
    """

    active_limits: RiskLimits
    margin: MarginStatus = field(default_factory=MarginStatus)
    exposure: ExposureStatus = field(default_factory=ExposureStatus)

    cash: Decimal = Decimal("0.00")
    buying_power: Decimal = Decimal("0.00")
    peak_nav: Decimal = Decimal("0.00")
    current_nav: Decimal = Decimal("0.00")
    daily_loss: Decimal = Decimal("0.00")

    history: AppendOnlyLog[RiskDecision] = field(default_factory=AppendOnlyLog)
    events: AppendOnlyLog[RiskEvent] = field(default_factory=AppendOnlyLog)

    trading_day: str | None = None
    day_start_nav: Decimal | None = None

    @property
    def current_drawdown_pct(self) -> Decimal:
        """Fall from the high-water mark as a fraction of it; zero with no peak.

        Exact in the accounting context. Until v3.10 it was rounded to four
        decimal places before the drawdown limit compared it, which let a
        drawdown up to half a basis point past the limit through.
        """

        if self.peak_nav <= _ZERO:
            return _ZERO
        ctx = ACCOUNTING_CONTEXT
        drawdown = ctx.divide(ctx.subtract(self.peak_nav, self.current_nav), self.peak_nav)
        return max(_ZERO, drawdown)

    @property
    def current_leverage(self) -> Decimal:
        """Gross exposure over net asset value; zero with no positive NAV."""

        if self.current_nav <= _ZERO:
            return _ZERO
        return ACCOUNTING_CONTEXT.divide(self.exposure.gross_exposure, self.current_nav)
