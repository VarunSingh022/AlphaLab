"""Global immutable state container for the Risk Engine."""

from dataclasses import dataclass, field
from decimal import Decimal

from alphalab.risk.decision import RiskDecision
from alphalab.risk.events import RiskEvent
from alphalab.risk.exposure import ExposureStatus
from alphalab.risk.limits import RiskLimits
from alphalab.risk.margin import MarginStatus


@dataclass(frozen=True, slots=True)
class RiskState:
    """Deterministic snapshot of account risk limits and utilization."""

    active_limits: RiskLimits
    margin: MarginStatus = field(default_factory=MarginStatus)
    exposure: ExposureStatus = field(default_factory=ExposureStatus)

    cash: Decimal = Decimal("0.00")
    buying_power: Decimal = Decimal("0.00")
    peak_nav: Decimal = Decimal("0.00")
    current_nav: Decimal = Decimal("0.00")
    daily_loss: Decimal = Decimal("0.00")

    history: tuple[RiskDecision, ...] = field(default_factory=tuple)
    events: tuple[RiskEvent, ...] = field(default_factory=tuple)

    @property
    def current_drawdown_pct(self) -> Decimal:
        """Calculates current high-water mark drawdown."""
        if self.peak_nav <= Decimal("0.00"):
            return Decimal("0.00")
        drawdown = (self.peak_nav - self.current_nav) / self.peak_nav
        return max(Decimal("0.00"), drawdown.quantize(Decimal("0.0001")))

    @property
    def current_leverage(self) -> Decimal:
        """Calculates gross leverage."""
        if self.current_nav <= Decimal("0.00"):
            return Decimal("0.00")
        return (self.exposure.gross_exposure / self.current_nav).quantize(Decimal("0.0001"))
