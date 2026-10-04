"""Risk budgets: limits on where a portfolio's risk comes from, along five dimensions.

A **risk budget** says how much of a portfolio's risk a bucket may carry -- this
strategy, this sector, this country, this currency, this asset. It is not a
capital budget and it is not an exposure limit, and the three are kept apart
because each can be met while another is broken:

=========================== =================================================================
capital allocation          how much money a strategy, account or market is given --
                            :mod:`alphalab.allocation.capital`, and
                            :class:`~alphalab.allocation.budget.CapitalBudget` on the
                            execution path
exposure                    how much is held: signed and gross market value, in the
                            reporting currency -- :attr:`BucketRisk.net_exposure`
contribution                how much of the portfolio's volatility a bucket accounts for,
                            by Euler's theorem -- :attr:`BucketRisk.contribution`
risk budget                 the target, maximum or minimum for a contribution --
                            :class:`BudgetLimit`
pre-trade limit             whether one order may pass -- :mod:`alphalab.risk`, untouched
=========================== =================================================================

A book of equal exposures can hold almost all of its risk in one name, and a
strategy given a tenth of the capital can carry half the risk. That is what this
module measures.

One model, five dimensions
--------------------------

Every figure is computed once, at the level of an **exposure line** -- one
strategy's holding of one asset, in the reporting currency -- and every
dimension is a grouping of those lines:

* ``ASSET`` groups by the line's asset; ``STRATEGY`` by the strategy that holds
  it; ``CURRENCY`` by the currency the holding is denominated in. All three are
  carried by the line itself, from the authorities that own them.
* ``SECTOR`` and ``COUNTRY`` group by a
  :class:`~alphalab.analytics.risk_model.Classification` the caller supplies --
  AlphaLab ships no taxonomy and holds no country anywhere. An asset the
  classification does not cover is **refused**, never put in an "unknown"
  bucket: a limit on every other bucket would be wrong by an amount nobody could
  see.

A line's contribution is its weight times its asset's marginal contribution,
``(value / capital) * (C w)_i / sigma``. Because volatility is homogeneous of
degree one in the line weights, the contributions of *all* lines sum to the
portfolio's volatility, and so do the buckets of every dimension -- each is a
partition of the same lines. :attr:`DimensionRisk.residual` reports what
floating point left over, computed with ``math.fsum``.

Currency, stated precisely
--------------------------

Every exposure is in the reporting currency before it gets here; the conversion
is :class:`~alphalab.portfolio.fx.FxRates`'s, performed by whoever produced the
lines (:func:`alphalab.portfolio.multi_strategy.value_book` records every one).
The covariance must describe returns **measured in that reporting currency** --
a euro stock's dollar return includes the euro's move -- and a covariance in
another currency is refused rather than mixed. So the ``CURRENCY`` dimension is
the risk carried by holdings *denominated in* each currency, measured in the
reporting currency. It is not the risk of exchange-rate moves alone: that is
measured by putting the exchange rates' returns in as factors
(:func:`~alphalab.analytics.risk_model.currency_loadings`, v3.13) and dividing
the volatility among them (:func:`~alphalab.analytics.risk_model.factor_risk`).
A currency bucket's :attr:`BucketRisk.native_exposure` is reported in that
currency, apart from its translated value.

Tolerance, stated
-----------------

Whether a contribution of ``0.2500000000000001`` breaches a limit of ``0.25`` is
a policy, not a measurement. :attr:`RiskBudget.tolerance` is that policy, and it
has no default: within it of a limit is ``AT_LIMIT``, beyond it ``BREACHED``.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal
from enum import Enum, auto
from typing import Final, Protocol

from alphalab.analytics.exceptions import AnalyticsValidationError
from alphalab.analytics.risk_model import (
    Classification,
    CovarianceMatrix,
    RiskContributions,
    euler_decomposition,
)
from alphalab.common.arithmetic import canonical_text

__all__ = [
    "RISK_BUDGET_REPORT_SCHEME",
    "RISK_BUDGET_SCHEME",
    "BucketRisk",
    "BudgetBasis",
    "BudgetCheck",
    "BudgetLimit",
    "BudgetStatus",
    "DimensionRisk",
    "ExposureLine",
    "LineRisk",
    "RiskBudget",
    "RiskBudgetReport",
    "RiskDimension",
    "evaluate_risk_budget",
]

RISK_BUDGET_SCHEME: Final = "alphalab.risk_budget.v1"
#: Version 2 (v3.11, ledger DET-006): the capital and every line value --
#: ``Decimal`` amounts -- render by value.
RISK_BUDGET_REPORT_SCHEME: Final = "alphalab.risk_budget_report.v2"

#: Money divided into a weight, in an explicit context rather than the thread's.
_CONTEXT: Final = Context(prec=28, rounding=ROUND_HALF_EVEN)


class RiskDimension(Enum):
    """A dimension a portfolio's risk is grouped along."""

    ASSET = auto()
    STRATEGY = auto()
    SECTOR = auto()
    COUNTRY = auto()
    CURRENCY = auto()


#: The classification dimension name each classified risk dimension reads.
_CLASSIFIED: Final = {"sector": RiskDimension.SECTOR, "country": RiskDimension.COUNTRY}


class BudgetBasis(Enum):
    """What a limit is stated in."""

    #: A contribution to volatility, in return units per the covariance's
    #: period: ``0.02`` is two percent of capital, one standard deviation.
    ABSOLUTE = auto()

    #: A share of the portfolio's volatility: ``contribution / volatility``.
    #: ``0.25`` is a quarter of the risk. Shares can be negative (a hedge) and
    #: can exceed one when other buckets hedge.
    RELATIVE = auto()


class BudgetStatus(Enum):
    """Where a bucket stands against a limit."""

    #: Strictly inside every stated end, by more than the tolerance.
    WITHIN = auto()

    #: Within the tolerance of a stated end: not breached, and no headroom.
    AT_LIMIT = auto()

    #: Above a maximum by more than the tolerance.
    BREACHED = auto()

    #: Below a minimum by more than the tolerance.
    BELOW_MINIMUM = auto()


def _finite(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AnalyticsValidationError(f"{what} must be a real number, got {value!r}.")
    number = float(value)
    if not math.isfinite(number):
        raise AnalyticsValidationError(f"{what} is {number!r}; it must be finite.")
    return number


def _optional(value: object, what: str) -> float | None:
    return None if value is None else _finite(value, what)


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalyticsValidationError(
            f"{what} must be a non-blank, unpadded string, got {value!r}."
        )
    return value


def _digest(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BudgetLimit:
    """One budget line: a bucket, the basis it is stated in, and its bounds.

    Attributes:
        dimension: Which grouping.
        bucket: The bucket's label -- an asset id, a strategy id, a sector, a
            country, a currency code.
        basis: Absolute contribution or relative share.
        maximum: The most the bucket may carry, or ``None``.
        minimum: The least it must carry, or ``None`` -- a floor on a
            diversifying sleeve's share, say.
        target: The budget it is meant to carry, or ``None``. A target is
            reported against, never enforced: its deviation is a measurement.
    """

    dimension: RiskDimension
    bucket: str
    basis: BudgetBasis
    maximum: float | None
    minimum: float | None
    target: float | None

    def __post_init__(self) -> None:
        _text(self.bucket, "BudgetLimit.bucket")
        for name in ("maximum", "minimum", "target"):
            object.__setattr__(self, name, _optional(getattr(self, name), f"BudgetLimit.{name}"))
        if self.maximum is None and self.minimum is None and self.target is None:
            raise AnalyticsValidationError(
                f"The limit on {self.dimension.name} {self.bucket!r} states no maximum, minimum or "
                "target, so it budgets nothing."
            )
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise AnalyticsValidationError(
                f"The limit on {self.dimension.name} {self.bucket!r} has minimum {self.minimum!r} "
                f"above maximum {self.maximum!r}."
            )
        if self.target is not None and (
            (self.minimum is not None and self.target < self.minimum)
            or (self.maximum is not None and self.target > self.maximum)
        ):
            raise AnalyticsValidationError(
                f"The target of {self.dimension.name} {self.bucket!r} lies outside its own limits."
            )

    @property
    def key(self) -> tuple[str, str, str]:
        """``(dimension, bucket, basis)``: what makes two limits the same limit."""

        return (self.dimension.name, self.bucket, self.basis.name)

    def rendering(self) -> str:
        return (
            f"limit={self.dimension.name}|{self.bucket!r}|{self.basis.name}|"
            f"max={self.maximum!r}|min={self.minimum!r}|target={self.target!r}"
        )


@dataclass(frozen=True, slots=True)
class RiskBudget:
    """A named set of risk limits, and the tolerance they are judged with.

    Attributes:
        name: What the budget is called.
        limits: The limits, held in canonical order. One per
            ``(dimension, bucket, basis)``.
        tolerance: How close to a stated end counts as *at* it rather than
            beyond it, in the limit's own units. Required and non-negative:
            ``0`` is an exact comparison, which is a choice like any other.
    """

    name: str
    limits: tuple[BudgetLimit, ...]
    tolerance: float

    def __post_init__(self) -> None:
        _text(self.name, "RiskBudget.name")
        ordered = tuple(sorted(self.limits, key=lambda limit: limit.key))
        keys = [limit.key for limit in ordered]
        if len(set(keys)) != len(keys):
            repeated = sorted({key for key in keys if keys.count(key) > 1})
            raise AnalyticsValidationError(f"The same limit is stated twice: {repeated}.")
        object.__setattr__(self, "limits", ordered)
        tolerance = _finite(self.tolerance, "RiskBudget.tolerance")
        if tolerance < 0.0:
            raise AnalyticsValidationError(
                f"RiskBudget.tolerance is {tolerance!r}; it is a magnitude."
            )
        object.__setattr__(self, "tolerance", tolerance)

    @property
    def budget_id(self) -> str:
        """The derived identity of this budget."""

        return _digest(
            [
                RISK_BUDGET_SCHEME,
                f"name={self.name!r}",
                f"tolerance={self.tolerance!r}",
                *(limit.rendering() for limit in self.limits),
            ]
        )

    @property
    def dimensions(self) -> tuple[RiskDimension, ...]:
        """Every dimension a limit is stated along, in enum order."""

        present = {limit.dimension for limit in self.limits}
        return tuple(dimension for dimension in RiskDimension if dimension in present)


class ExposureLine(Protocol):
    """One strategy's holding of one asset, valued in the reporting currency.

    Structural, so this package reads the lines
    :func:`alphalab.portfolio.multi_strategy.value_book` produces without
    importing :mod:`alphalab.portfolio` -- which keeps ``analytics`` free of
    ``portfolio``, as :class:`~alphalab.analytics.decomposition.PositionRisk`
    always has.
    """

    @property
    def strategy_id(self) -> str:
        """The strategy holding it; ``""`` when no strategy is attributed."""
        ...

    @property
    def asset_id(self) -> str:
        """The asset held."""
        ...

    @property
    def currency(self) -> str:
        """The currency the holding is denominated in."""
        ...

    @property
    def market_value(self) -> Decimal:
        """Signed value in :attr:`currency`."""
        ...

    @property
    def reporting_value(self) -> Decimal:
        """Signed value in the reporting currency."""
        ...


@dataclass(frozen=True, slots=True)
class LineRisk:
    """One exposure line's share of the portfolio's risk.

    Attributes:
        strategy_id: Who holds it (``""`` when unattributed).
        asset_id: What is held.
        currency: What it is denominated in.
        native_value: Signed value in :attr:`currency`.
        value: Signed value in the reporting currency.
        weight: ``value / capital``.
        contribution: ``weight * marginal contribution of the asset``.
    """

    strategy_id: str
    asset_id: str
    currency: str
    native_value: Decimal
    value: Decimal
    weight: float
    contribution: float


@dataclass(frozen=True, slots=True)
class BucketRisk:
    """One bucket of one dimension: its exposure and its risk.

    Attributes:
        bucket: The label.
        net_exposure: Sum of signed line values, in the reporting currency.
        gross_exposure: Sum of absolute line values, in the reporting currency.
        capital_share: ``net_exposure / capital``: the bucket's weight.
        contribution: Its contribution to portfolio volatility.
        share: ``contribution / volatility``.
        lines: How many exposure lines it holds.
        native_exposure: For a ``CURRENCY`` bucket, the net exposure in that
            currency itself; ``None`` for every other dimension, where the
            lines may be in several currencies and have no native total.
    """

    bucket: str
    net_exposure: Decimal
    gross_exposure: Decimal
    capital_share: float
    contribution: float
    share: float
    lines: int
    native_exposure: Decimal | None


@dataclass(frozen=True, slots=True)
class DimensionRisk:
    """Every bucket of one dimension, and how exactly they reconcile.

    Attributes:
        dimension: The dimension.
        buckets: One per label present, sorted by label.
        total_contribution: ``fsum`` of the bucket contributions.
        residual: ``total_contribution - volatility``: floating point's share.
        source: Where the labels came from: ``"line"`` for a dimension the
            lines carry, or the classification's source.
        classification_id: The classification read, for ``SECTOR`` and
            ``COUNTRY``; ``None`` otherwise.
    """

    dimension: RiskDimension
    buckets: tuple[BucketRisk, ...]
    total_contribution: float
    residual: float
    source: str
    classification_id: str | None

    def bucket(self, label: str) -> BucketRisk | None:
        """One bucket by label, or ``None`` when nothing is held in it."""

        for entry in self.buckets:
            if entry.bucket == label:
                return entry
        return None


@dataclass(frozen=True, slots=True)
class BudgetCheck:
    """One limit, judged.

    Attributes:
        limit: The limit.
        present: Whether the bucket holds any exposure. An absent bucket uses
            nothing, and is judged at zero.
        used: The bucket's contribution (``ABSOLUTE``) or share (``RELATIVE``).
        remaining: ``maximum - used``, or ``None`` without a maximum. Negative
            when breached.
        above_minimum: ``used - minimum``, or ``None`` without a minimum.
        deviation: ``used - target``, or ``None`` without a target.
        status: The verdict against the maximum and minimum.
    """

    limit: BudgetLimit
    present: bool
    used: float
    remaining: float | None
    above_minimum: float | None
    deviation: float | None
    status: BudgetStatus


@dataclass(frozen=True, slots=True)
class RiskBudgetReport:
    """A portfolio's risk, grouped along every available dimension, and judged.

    Attributes:
        budget_id: The budget judged.
        reporting_currency: What every exposure is in.
        period: The covariance's period: what every risk figure is per.
        capital: The capital weights are fractions of.
        covariance_id: The covariance used.
        volatility: Portfolio volatility, ``sqrt(w' C w)``.
        risk: The asset-level Euler decomposition, from
            :func:`~alphalab.analytics.risk_model.euler_decomposition`.
        lines: Every line's risk, sorted by strategy then asset.
        dimensions: Every dimension the inputs support, in
            :class:`RiskDimension` order: always ``ASSET`` and ``CURRENCY``;
            ``STRATEGY`` when every line is attributed; ``SECTOR`` and
            ``COUNTRY`` when classified. A tuple rather than a mapping keyed by
            the enum, so the report serializes as deterministic JSON.
        checks: Every limit, judged, in the budget's order.
    """

    budget_id: str
    reporting_currency: str
    period: str
    capital: Decimal
    covariance_id: str
    volatility: float
    risk: RiskContributions
    lines: tuple[LineRisk, ...]
    dimensions: tuple[DimensionRisk, ...]
    checks: tuple[BudgetCheck, ...]

    def dimension(self, which: RiskDimension) -> DimensionRisk:
        """One dimension's buckets.

        Raises:
            AnalyticsValidationError: If the inputs did not support it.
        """

        for entry in self.dimensions:
            if entry.dimension is which:
                return entry
        raise AnalyticsValidationError(
            f"This report has no {which.name} dimension: the inputs did not support it."
        )

    @property
    def breaches(self) -> tuple[BudgetCheck, ...]:
        """The checks that are ``BREACHED`` or ``BELOW_MINIMUM``."""

        return tuple(
            check
            for check in self.checks
            if check.status in (BudgetStatus.BREACHED, BudgetStatus.BELOW_MINIMUM)
        )

    @property
    def report_id(self) -> str:
        """The derived identity of this evaluation: inputs, lines and verdicts."""

        return _digest(
            [
                RISK_BUDGET_REPORT_SCHEME,
                f"budget={self.budget_id}",
                f"covariance={self.covariance_id}",
                f"currency={self.reporting_currency!r}",
                f"capital={canonical_text(self.capital)}",
                f"volatility={self.volatility!r}",
                *(
                    f"line={line.strategy_id!r}|{line.asset_id!r}|{line.currency!r}|"
                    f"{canonical_text(line.native_value)}|{canonical_text(line.value)}"
                    for line in self.lines
                ),
                *(
                    f"source={risk.dimension.name}|{risk.source!r}|{risk.classification_id}"
                    for risk in self.dimensions
                ),
                *(
                    f"check={'|'.join(check.limit.key)}={check.status.name}"
                    for check in self.checks
                ),
            ]
        )


def _judge(limit: BudgetLimit, present: bool, used: float, tolerance: float) -> BudgetCheck:
    status = BudgetStatus.WITHIN
    if limit.maximum is not None:
        if used > limit.maximum + tolerance:
            status = BudgetStatus.BREACHED
        elif used >= limit.maximum - tolerance:
            status = BudgetStatus.AT_LIMIT
    if status is not BudgetStatus.BREACHED and limit.minimum is not None:
        if used < limit.minimum - tolerance:
            status = BudgetStatus.BELOW_MINIMUM
        elif used <= limit.minimum + tolerance:
            status = BudgetStatus.AT_LIMIT
    return BudgetCheck(
        limit=limit,
        present=present,
        used=used,
        remaining=None if limit.maximum is None else limit.maximum - used,
        above_minimum=None if limit.minimum is None else used - limit.minimum,
        deviation=None if limit.target is None else used - limit.target,
        status=status,
    )


def _group(
    dimension: RiskDimension,
    lines: Sequence[LineRisk],
    labels: Sequence[str],
    capital: Decimal,
    volatility: float,
    source: str,
    classification_id: str | None,
) -> DimensionRisk:
    members: dict[str, list[LineRisk]] = {}
    for line, label in zip(lines, labels, strict=True):
        members.setdefault(label, []).append(line)
    buckets = []
    for label in sorted(members):
        group = members[label]
        net = sum((line.value for line in group), Decimal(0))
        contribution = math.fsum(line.contribution for line in group)
        buckets.append(
            BucketRisk(
                bucket=label,
                net_exposure=net,
                gross_exposure=sum((abs(line.value) for line in group), Decimal(0)),
                capital_share=float(_CONTEXT.divide(net, capital)),
                contribution=contribution,
                share=contribution / volatility,
                lines=len(group),
                native_exposure=(
                    sum((line.native_value for line in group), Decimal(0))
                    if dimension is RiskDimension.CURRENCY
                    else None
                ),
            )
        )
    total = math.fsum(bucket.contribution for bucket in buckets)
    return DimensionRisk(
        dimension=dimension,
        buckets=tuple(buckets),
        total_contribution=total,
        residual=total - volatility,
        source=source,
        classification_id=classification_id,
    )


def evaluate_risk_budget(
    lines: Iterable[ExposureLine],
    *,
    capital: Decimal,
    reporting_currency: str,
    covariance: CovarianceMatrix,
    budget: RiskBudget,
    classifications: Sequence[Classification],
) -> RiskBudgetReport:
    """Decompose a book's risk along every available dimension and judge it.

    Args:
        lines: Exposure lines, each one strategy's holding of one asset, valued
            in ``reporting_currency``. One line per ``(strategy, asset)``.
        capital: What weights are fractions of -- normally the book's net asset
            value -- in ``reporting_currency``. Positive.
        reporting_currency: The currency every line's ``reporting_value`` is
            in, and the covariance's returns are measured in.
        covariance: Covers every held asset.
        budget: The limits.
        classifications: A ``"sector"`` and/or ``"country"`` classification,
            each covering every held asset. Pass ``()`` when there are none; a
            budget with a sector or country limit then refuses.

    Raises:
        AnalyticsValidationError: On no lines, a duplicated line, a
            non-positive capital, a covariance in another currency or missing a
            held asset, a classification of an unsupported dimension or not
            covering a held asset, a limit along a dimension the inputs do not
            support, or a book with zero volatility -- whose risk cannot be
            apportioned.
    """

    _text(reporting_currency, "reporting_currency")
    if not isinstance(capital, Decimal) or not capital.is_finite() or capital <= 0:
        raise AnalyticsValidationError(
            f"capital is {capital!r}; weights are fractions of a positive capital, and a risk "
            "budget against no capital is undefined."
        )
    if covariance.currency != reporting_currency:
        raise AnalyticsValidationError(
            f"The covariance describes returns measured in {covariance.currency!r} and the book "
            f"is reported in {reporting_currency!r}. A contribution computed from the pair would "
            "be in no unit at all; supply a covariance of returns measured in the reporting "
            "currency."
        )

    held: list[LineRisk] = []
    seen: set[tuple[str, str]] = set()
    raw = sorted(lines, key=lambda line: (line.strategy_id, line.asset_id))
    if not raw:
        raise AnalyticsValidationError(
            "A risk budget over no exposure is undefined, which is a refusal rather than zero."
        )
    totals: dict[str, Decimal] = {}
    for line in raw:
        key = (line.strategy_id, line.asset_id)
        if key in seen:
            raise AnalyticsValidationError(
                f"Strategy {line.strategy_id!r} holds {line.asset_id!r} on two lines. One holding "
                "counted twice would double its risk and its budget use together."
            )
        seen.add(key)
        _text(line.asset_id, "exposure line asset")
        _text(line.currency, f"currency of {line.asset_id!r}")
        for amount in (line.market_value, line.reporting_value):
            if not isinstance(amount, Decimal) or not amount.is_finite():
                raise AnalyticsValidationError(
                    f"The line {key} carries {amount!r}; values are finite Decimals."
                )
        totals[line.asset_id] = totals.get(line.asset_id, Decimal(0)) + line.reporting_value

    weights = {asset: float(_CONTEXT.divide(total, capital)) for asset, total in totals.items()}
    try:
        risk = euler_decomposition(weights, covariance)
    except AnalyticsValidationError as error:
        raise AnalyticsValidationError(f"The book's risk cannot be budgeted: {error}") from error
    volatility = risk.volatility
    for line in raw:
        weight = float(_CONTEXT.divide(line.reporting_value, capital))
        held.append(
            LineRisk(
                strategy_id=line.strategy_id,
                asset_id=line.asset_id,
                currency=line.currency,
                native_value=line.market_value,
                value=line.reporting_value,
                weight=weight,
                contribution=weight * risk.marginal[line.asset_id],
            )
        )

    dimensions: dict[RiskDimension, DimensionRisk] = {}
    dimensions[RiskDimension.ASSET] = _group(
        RiskDimension.ASSET,
        held,
        [line.asset_id for line in held],
        capital,
        volatility,
        "line",
        None,
    )
    if all(entry.strategy_id for entry in held):
        for entry in held:
            _text(entry.strategy_id, "exposure line strategy")
        dimensions[RiskDimension.STRATEGY] = _group(
            RiskDimension.STRATEGY,
            held,
            [line.strategy_id for line in held],
            capital,
            volatility,
            "line",
            None,
        )
    assets = {line.asset_id for line in held}
    supplied: set[RiskDimension] = set()
    for classification in classifications:
        dimension = _CLASSIFIED.get(classification.dimension)
        if dimension is None:
            raise AnalyticsValidationError(
                f"A {classification.dimension!r} classification has no risk dimension; risk "
                "budgets group by asset, strategy, sector, country and currency."
            )
        if dimension in supplied:
            raise AnalyticsValidationError(
                f"Two {classification.dimension} classifications were given."
            )
        supplied.add(dimension)
        classification.require_covers(assets, f"The {dimension.name} risk dimension")
        dimensions[dimension] = _group(
            dimension,
            held,
            [classification.labels[line.asset_id] for line in held],
            capital,
            volatility,
            classification.source,
            classification.classification_id,
        )
    dimensions[RiskDimension.CURRENCY] = _group(
        RiskDimension.CURRENCY,
        held,
        [line.currency for line in held],
        capital,
        volatility,
        "line",
        None,
    )

    missing = [dimension.name for dimension in budget.dimensions if dimension not in dimensions]
    if missing:
        raise AnalyticsValidationError(
            f"The budget limits {missing}, which these inputs cannot measure: a strategy limit "
            "needs every line attributed to a strategy, and a sector or country limit needs a "
            "classification covering every held asset."
        )

    labelled = {
        dimension: {entry.bucket: entry for entry in risk.buckets}
        for dimension, risk in dimensions.items()
    }
    checks = []
    for limit in budget.limits:
        bucket = labelled[limit.dimension].get(limit.bucket)
        if bucket is None:
            used = 0.0
        elif limit.basis is BudgetBasis.ABSOLUTE:
            used = bucket.contribution
        else:
            used = bucket.share
        checks.append(_judge(limit, bucket is not None, used, budget.tolerance))

    ordered = tuple(dimensions[dimension] for dimension in RiskDimension if dimension in dimensions)
    return RiskBudgetReport(
        budget_id=budget.budget_id,
        reporting_currency=reporting_currency,
        period=covariance.period,
        capital=capital,
        covariance_id=covariance.covariance_id,
        volatility=volatility,
        risk=risk,
        lines=tuple(held),
        dimensions=ordered,
        checks=tuple(checks),
    )
