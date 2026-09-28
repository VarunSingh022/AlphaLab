"""Immutable risk limit definitions.

Every monetary limit is stated in the account's **base currency**: the gate
converts an order's notional into it before comparing, so a limit means the
same thing whatever the instrument trades in.

What each limit measures (v3.10)
--------------------------------
Until v3.10 several limits were declared and not enforced as documented; the
pre-v4 audit (ledger KD-001..003) found them:

* :class:`PositionLimit` added an asset's *market value* to an order's
  *quantity* and compared the sum with ``max_quantity``. It now limits the
  projected signed position -- in quantity against ``max_quantity`` and in
  base-currency value against ``max_notional``.
* :class:`DailyLossLimit` was never enforced: nothing maintained the daily loss
  it read, so it read zero. A daily loss needs a day, and a day needs a place:
  the limit now declares the IANA zone (and the local time) its trading day
  starts in, and the gate measures the loss since that boundary. No zone is
  assumed -- UTC is a choice like any other, and the one a desk in Tokyo or
  New York did not make.
* :attr:`ExposureLimit.max_net_exposure` was read by nothing, with no sign
  convention anywhere. Net exposure is long market value plus short market
  value (short negative), in the base currency; the gate limits its magnitude.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from alphalab.data.exceptions import DataValidationError
from alphalab.data.time import resolve_zone
from alphalab.risk.exceptions import RiskValidationError


@dataclass(frozen=True, slots=True)
class PositionLimit:
    """The largest position one asset may reach, after the order and working orders.

    Attributes:
        max_quantity: Largest absolute signed position, in units.
        max_notional: Largest absolute position value, in the base currency.
    """

    max_quantity: Decimal
    max_notional: Decimal


@dataclass(frozen=True, slots=True)
class OrderSizeLimit:
    """The largest single order: quantity in units, notional in the base currency."""

    max_quantity: Decimal
    max_notional: Decimal


@dataclass(frozen=True, slots=True)
class ExposureLimit:
    """Book-level exposure caps, in the base currency.

    Attributes:
        max_gross_exposure: Largest long value plus the magnitude of short value.
        max_net_exposure: Largest magnitude of long value plus (negative) short
            value.
    """

    max_gross_exposure: Decimal
    max_net_exposure: Decimal


@dataclass(frozen=True, slots=True)
class LeverageLimit:
    max_leverage: Decimal


@dataclass(frozen=True, slots=True)
class MarginLimit:
    max_margin_utilization: Decimal


@dataclass(frozen=True, slots=True)
class DailyLossLimit:
    """The most equity the book may lose within one trading day.

    Attributes:
        max_daily_loss: Largest fall in net asset value since the trading day's
            start, in the base currency. Non-negative.
        zone: The IANA time zone the trading day is kept in (``"America/New_York"``,
            ``"Asia/Tokyo"``, ``"UTC"``). **Required**: a day's boundary is a
            place's, and no place is assumed.
        day_start: The local time a trading day begins at, in ``zone``.
            Midnight unless stated -- the calendar day of the declared place; an
            FX desk that rolls at 17:00 New York states ``time(17)``.

    Raises:
        RiskValidationError: If the loss is negative or not finite, or the zone
            is not an IANA zone this interpreter knows.
    """

    max_daily_loss: Decimal
    zone: str
    day_start: time = time(0)

    def __post_init__(self) -> None:
        if not self.max_daily_loss.is_finite() or self.max_daily_loss < 0:
            raise RiskValidationError(
                f"max_daily_loss must be a finite, non-negative amount, got {self.max_daily_loss}."
            )
        if not self.zone.strip():
            raise RiskValidationError(
                "A daily loss limit names the time zone its trading day is kept in. "
                "No zone is assumed: the day that ends at midnight in UTC is not the "
                "day a desk in Tokyo or New York is measuring."
            )
        try:
            resolve_zone(self.zone)
        except DataValidationError as exc:
            raise RiskValidationError(
                f"{self.zone!r} is not an IANA time zone this interpreter knows: {exc}"
            ) from exc
        if self.day_start.tzinfo is not None:
            raise RiskValidationError(
                "day_start is a local time in the declared zone and carries no zone of its own."
            )

    def __serializable__(self) -> dict[str, str]:
        """Serialize ``day_start`` as its ISO local time, which JSON can carry."""

        return {
            "max_daily_loss": str(self.max_daily_loss),
            "zone": self.zone,
            "day_start": self.day_start.isoformat(),
        }

    def trading_day(self, timestamp: float) -> date:
        """The trading day ``timestamp`` (Unix seconds) falls in, in the declared zone."""

        local = datetime.fromtimestamp(timestamp, tz=UTC).astimezone(resolve_zone(self.zone))
        offset = timedelta(
            hours=self.day_start.hour,
            minutes=self.day_start.minute,
            seconds=self.day_start.second,
            microseconds=self.day_start.microsecond,
        )
        return (local.replace(tzinfo=None) - offset).date()


@dataclass(frozen=True, slots=True)
class DrawdownLimit:
    max_drawdown_pct: Decimal


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Aggregated risk limits configuration for an account or strategy.

    ``daily_loss`` is ``None`` when the account has no daily loss limit -- which
    must be said, not implied: the field has no default.
    """

    order_size: OrderSizeLimit
    position: PositionLimit
    exposure: ExposureLimit
    leverage: LeverageLimit
    margin: MarginLimit
    daily_loss: DailyLossLimit | None
    drawdown: DrawdownLimit
