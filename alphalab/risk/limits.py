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

Classification buckets (v3.12)
------------------------------
:class:`ClassificationLimit` bounds what one *bucket* of a classification
dimension may hold -- every instrument of one sector, one issuer, one country
(ledger OFE-001). The gate does not read the instrument registry: the caller
says which bucket the order's instrument is in and what the bucket holds,
before and after (:class:`~alphalab.risk.projection.BucketExposure`), as it
says the order's price.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from alphalab.data.exceptions import DataValidationError
from alphalab.data.time import resolve_zone
from alphalab.instrument.classification import normalize_dimension
from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.instrument.record import normalize_sector_label
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
class ClassificationLimit:
    """The most one bucket of a classification dimension may hold (ledger OFE-001).

    A bucket is every instrument carrying one label along one dimension --
    every ``"Energy"`` stock, every bond of one issuer, everything exposed to
    one country. What it holds is its **gross** exposure counting working
    orders: long value plus the magnitude of short value, in the base currency.
    Reduce-only, as every exposure limit is: an order that does not grow its
    bucket's gross exposure is never refused by it.

    Attributes:
        dimension: ``"sector"`` -- ``InstrumentRecord.sector`` -- or a dimension
            declared through :func:`~alphalab.instrument.registry.classify_dimension`.
        max_gross: The largest gross exposure a bucket may hold. ``None``: no
            amount ceiling.
        max_share: The largest share of net asset value a bucket's gross
            exposure may be -- ``Decimal("0.25")`` for a quarter. ``None``: no
            share ceiling. With no positive net asset value the share is
            undefined, and an order growing the bucket is refused.
        label: The one bucket this limit binds, or ``None`` for every bucket of
            the dimension. A bucket a labelled limit names is bound by it
            **instead of** the dimension's unlabelled ones -- how one issuer is
            allowed more, or less, than the rest.
        refuse_unclassified: Whether an order growing exposure in an instrument
            that carries no label along the dimension is refused. ``False``
            leaves such an instrument outside every bucket, which is stated
            here because an issuer limit a bond escapes by being unclassified
            has a hole in it. Read on unlabelled limits only.

    Raises:
        RiskValidationError: If the dimension or label is not one, neither
            ceiling is stated, a ceiling is negative or not finite, or a
            labelled limit asks to refuse the unclassified.
    """

    dimension: str
    max_gross: Decimal | None = None
    max_share: Decimal | None = None
    label: str | None = None
    refuse_unclassified: bool = False

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "dimension", normalize_dimension(self.dimension))
            if self.label is not None:
                object.__setattr__(self, "label", normalize_sector_label(self.label, "label"))
        except InstrumentInputError as exc:
            raise RiskValidationError(f"A classification limit is malformed: {exc}") from exc
        if self.max_gross is None and self.max_share is None:
            raise RiskValidationError(
                f"A limit on {self.dimension} states a max_gross, a max_share or both."
            )
        for name, ceiling in (("max_gross", self.max_gross), ("max_share", self.max_share)):
            if ceiling is not None and (not ceiling.is_finite() or ceiling < 0):
                raise RiskValidationError(
                    f"{name} must be a finite, non-negative amount, got {ceiling}."
                )
        if self.refuse_unclassified and self.label is not None:
            raise RiskValidationError(
                "refuse_unclassified is a rule for a whole dimension; a limit on one label "
                "binds only instruments that carry it."
            )

    def binds(self, dimension: str, label: str | None) -> bool:
        """Whether this limit is one that names ``label`` along ``dimension``."""

        return self.dimension == dimension and self.label == label


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
    #: Limits on the buckets of classification dimensions (v3.12, ledger
    #: OFE-001). Empty by default: no bucket is limited, as before.
    classification: tuple[ClassificationLimit, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.classification, tuple):
            object.__setattr__(self, "classification", tuple(self.classification))
        for limit in self.classification:
            if not isinstance(limit, ClassificationLimit):
                raise RiskValidationError(
                    f"classification holds ClassificationLimit values, got {limit!r}."
                )

    def bucket_limits(self, dimension: str, label: str | None) -> tuple[ClassificationLimit, ...]:
        """The limits binding the bucket ``label`` of ``dimension``.

        Those naming the label when any does, else the dimension's unlabelled
        ones. ``label=None`` -- an unclassified instrument -- reads the
        unlabelled ones, whose ``refuse_unclassified`` decides it.
        """

        named = tuple(limit for limit in self.classification if limit.binds(dimension, label))
        if named or label is None:
            return named
        return tuple(limit for limit in self.classification if limit.binds(dimension, None))
