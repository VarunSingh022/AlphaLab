"""Point-in-time fundamentals: statements as they were knowable, never as restated.

A financial statement describes a fiscal period, is published some weeks after
it ends, reaches a researcher some time after that, and may be restated a year
later. Every one of those instants matters, and a fundamentals database that
stores one number per line item per period has already thrown three of them
away: the figure it holds is usually the *latest* restatement, stamped with the
*period end*, which makes every backtest built on it look ahead twice -- once to
the publication it had not yet happened, and once to the correction nobody had
yet made.

This module keeps them apart:

========================== =================================================
Reporting / fiscal period  :class:`FiscalPeriod` -- fiscal year, quarter, and
                           the span it covers
Publication                ``FundamentalObservation.published_at`` -- when
                           the issuer made it public
Availability               ``stamp.available_at`` -- when a researcher could
                           read it, never earlier than publication
Effective period           the fiscal period itself; a restatement is
                           effective for the period it restates
Restatement                ``revision`` -- ``0`` as originally published, a
                           new record for each restatement, never an edit
========================== =================================================

and every query takes a research instant and a
:class:`~alphalab.alt_data.observation_set.VintagePolicy`: the figure as it was
knowable then (``AS_KNOWN``), or as it was first published (``ORIGINAL``).
There is no policy that reads today's restated figure at a past instant.
:func:`restatements` compares original and final figures across a whole set,
under that name, which is the one honest place for hindsight.

What is computed, and what is refused
-------------------------------------

Trailing twelve months sums the four most recent *consecutive* quarters knowable
at the instant, and refuses a balance-sheet item -- a level at a date is not a
flow, and four of them summed measure nothing. Valuation reads a price the
caller supplies with the instant it was observed, and refuses one observed
after the research instant. Ratios and valuation metrics are ``None`` with a
reason when undefined -- a price-to-earnings ratio over negative earnings, an
enterprise value with no debt figure -- and never zero.

Units are checked, because a figure in another currency is a wrong number rather
than an error. A monetary figure's unit is its currency code (``"USD"``), a
per-share figure's is ``"<currency>/share"``, a share count's is ``"shares"``;
valuation and ratios refuse inputs whose units do not agree.

Arithmetic is exact ``Decimal`` in an explicit context (28 digits, half-even),
never the calling thread's current context, so a caller who changed their
context cannot change a published ratio.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Context, Decimal
from enum import Enum, auto
from types import MappingProxyType
from typing import Final

from alphalab.alt_data.exceptions import AltDataInputError
from alphalab.alt_data.identity import digest_lines, stamp_lines
from alphalab.alt_data.observation_set import (
    ObservationSet,
    ObservationView,
    VintagePolicy,
    latest_vintages,
    original_vintages,
)
from alphalab.alt_data.source import ObservationSource
from alphalab.alt_data.validation import (
    require_finite_instant,
    require_finite_value,
    require_identifier,
    require_label,
    require_revision,
)
from alphalab.common.point_in_time import PointInTimeStamp, VisibilityRule

__all__ = [
    "BOOK_EQUITY",
    "CASH",
    "CURRENT_ASSETS",
    "CURRENT_LIABILITIES",
    "DIVIDENDS_PER_SHARE",
    "EARNINGS",
    "EARNINGS_PER_SHARE",
    "EBITDA",
    "FREE_CASH_FLOW",
    "FUNDAMENTAL_KEY_SCHEME",
    "GROSS_PROFIT",
    "OPERATING_INCOME",
    "REVENUE",
    "SHARES_OUTSTANDING",
    "TOTAL_ASSETS",
    "TOTAL_DEBT",
    "AggregateStep",
    "Aggregation",
    "FinancialRatios",
    "FinancialStatement",
    "FiscalPeriod",
    "FundamentalInput",
    "FundamentalInputs",
    "FundamentalObservation",
    "GrowthValue",
    "Restatement",
    "StatementType",
    "TrailingValue",
    "ValuationMetrics",
    "aggregate_timeline",
    "canonical_fundamental_key",
    "currency_of_per_share_unit",
    "financial_ratios",
    "fundamental_as_of",
    "fundamental_inputs_as_of",
    "latest_fundamental",
    "restatements",
    "statement_as_of",
    "trailing_twelve_months",
    "valuation_metrics",
    "year_over_year_growth",
]

#: Scheme tag, and the first line of every canonical fundamental key.
FUNDAMENTAL_KEY_SCHEME: Final = "alphalab.fundamental.v1"

#: The one arithmetic context every figure here is computed in.
_CONTEXT: Final = Context(prec=28, rounding=ROUND_HALF_EVEN)

_ZERO: Final = Decimal(0)

# Canonical input names the valuation and ratio functions read. A caller maps
# their source's line items onto these with :class:`FundamentalInput`; there is
# no default mapping, because line-item names are a source's vocabulary.

#: Share count, unit ``"shares"``.
SHARES_OUTSTANDING: Final = "shares_outstanding"
#: Net income attributable to common shareholders; a flow.
EARNINGS: Final = "earnings"
#: Earnings per share, unit ``"<currency>/share"``.
EARNINGS_PER_SHARE: Final = "earnings_per_share"
#: Revenue; a flow.
REVENUE: Final = "revenue"
#: Gross profit; a flow.
GROSS_PROFIT: Final = "gross_profit"
#: Operating income; a flow.
OPERATING_INCOME: Final = "operating_income"
#: EBITDA; a flow.
EBITDA: Final = "ebitda"
#: Free cash flow; a flow.
FREE_CASH_FLOW: Final = "free_cash_flow"
#: Common shareholders' equity; a level.
BOOK_EQUITY: Final = "book_equity"
#: Total assets; a level.
TOTAL_ASSETS: Final = "total_assets"
#: Total debt; a level.
TOTAL_DEBT: Final = "total_debt"
#: Cash and equivalents; a level.
CASH: Final = "cash"
#: Current assets; a level.
CURRENT_ASSETS: Final = "current_assets"
#: Current liabilities; a level.
CURRENT_LIABILITIES: Final = "current_liabilities"
#: Dividends per share, unit ``"<currency>/share"``.
DIVIDENDS_PER_SHARE: Final = "dividends_per_share"


class StatementType(Enum):
    """Which financial statement a line item belongs to."""

    #: Flows over the period: revenue, earnings, earnings per share.
    INCOME_STATEMENT = auto()
    #: Levels at the period's end: assets, equity, debt, cash, share counts.
    BALANCE_SHEET = auto()
    #: Flows over the period: operating and free cash flow.
    CASH_FLOW_STATEMENT = auto()

    @property
    def is_duration(self) -> bool:
        """Whether the statement's items are flows over the period rather than levels."""

        return self is not StatementType.BALANCE_SHEET


@dataclass(frozen=True, slots=True)
class FiscalPeriod:
    """A fiscal year or quarter, and the span of time it covers.

    Fiscal and calendar periods are different things: a fiscal year ending in
    September is ``FY2024`` for a year that is mostly calendar 2024's first
    three quarters and 2023's last. The span is therefore stated rather than
    derived from the label.

    Attributes:
        fiscal_year: The issuer's fiscal year.
        fiscal_quarter: ``1`` to ``4``, or ``None`` for the full fiscal year.
        start: Unix seconds at which the period begins.
        end: Unix seconds at which it ends.

    Raises:
        AltDataInputError: If the year is not an integer, the quarter is out of
            range, or the span is not a positive length.
    """

    fiscal_year: int
    fiscal_quarter: int | None
    start: float
    end: float

    def __post_init__(self) -> None:
        if isinstance(self.fiscal_year, bool) or not isinstance(self.fiscal_year, int):
            raise AltDataInputError(f"fiscal_year is {self.fiscal_year!r}; it is an integer.")
        if self.fiscal_quarter is not None and (
            isinstance(self.fiscal_quarter, bool)
            or not isinstance(self.fiscal_quarter, int)
            or not 1 <= self.fiscal_quarter <= 4
        ):
            raise AltDataInputError(
                f"fiscal_quarter is {self.fiscal_quarter!r}; a quarter is 1 to 4, or None for "
                "the full fiscal year."
            )
        require_finite_instant(self.start, "FiscalPeriod.start")
        require_finite_instant(self.end, "FiscalPeriod.end")
        if self.end <= self.start:
            raise AltDataInputError(
                f"Fiscal period {self.label} ends at {self.end!r}, not after it starts at "
                f"{self.start!r}."
            )

    @property
    def label(self) -> str:
        """``"FY2024"`` for a year, ``"FY2024Q2"`` for a quarter."""

        if self.fiscal_quarter is None:
            return f"FY{self.fiscal_year}"
        return f"FY{self.fiscal_year}Q{self.fiscal_quarter}"

    @property
    def is_annual(self) -> bool:
        """Whether this is a full fiscal year."""

        return self.fiscal_quarter is None

    def quarter_ordinal(self) -> int:
        """A count of quarters that increases by one from each quarter to the next.

        Raises:
            AltDataInputError: If the period is a full year.
        """

        if self.fiscal_quarter is None:
            raise AltDataInputError(f"{self.label} is a full year and has no quarter ordinal.")
        return self.fiscal_year * 4 + (self.fiscal_quarter - 1)


@dataclass(frozen=True, slots=True)
class FundamentalObservation:
    """One line item of one statement for one period, as one publication stated it.

    Attributes:
        subject: The issuer, by the caller's key.
        statement: Which statement the line item belongs to.
        line_item: What it is, as an identifier -- ``"revenue"``,
            ``"eps_diluted"``, ``"total_equity"``. A source's vocabulary.
        fiscal_period: The period the figure describes.
        value: The figure, exactly as published.
        unit: ``"USD"`` for money, ``"USD/share"`` per share, ``"shares"`` for a
            count. Required.
        published_at: When the issuer made the figure public -- a filing's
            acceptance, a release's timestamp. Never before the period ends.
        stamp: ``observed_at`` is the period's end; ``available_at``, when a
            researcher could read it, is never before ``published_at``.
        source: Which source delivered it, at which version.
        revision: ``0`` as originally published; ``n`` for the ``n``-th
            restatement, which is a separate record with its own publication.
        record_id: The derived, content-addressed identity, computed on
            construction.

    Raises:
        AltDataInputError: If a field fails its shape; if the observation
            instant is not the period's end; if the figure was published before
            the period ended; or if it became available before it was
            published.
    """

    subject: str
    statement: StatementType
    line_item: str
    fiscal_period: FiscalPeriod
    value: Decimal
    unit: str
    published_at: float
    stamp: PointInTimeStamp
    source: ObservationSource
    revision: int
    record_id: str = field(init=False)

    def __post_init__(self) -> None:
        require_label(self.subject, "FundamentalObservation.subject")
        require_identifier(self.line_item, "FundamentalObservation.line_item")
        require_finite_value(self.value, "FundamentalObservation.value")
        require_label(self.unit, "FundamentalObservation.unit")
        require_finite_instant(self.published_at, "FundamentalObservation.published_at")
        require_revision(self.revision, "FundamentalObservation.revision")
        what = f"{self.subject!r} {self.line_item} {self.fiscal_period.label}"
        if self.stamp.observed_at != self.fiscal_period.end:
            raise AltDataInputError(
                f"{what} is observed at {self.stamp.observed_at!r}; a statement figure "
                f"describes its period and is observed at the period's end, "
                f"{self.fiscal_period.end!r}."
            )
        if self.published_at < self.fiscal_period.end:
            raise AltDataInputError(
                f"{what} is published at {self.published_at!r}, before the period ends at "
                f"{self.fiscal_period.end!r}. Results cannot be reported for a period that "
                "has not finished."
            )
        if self.stamp.available_at is not None and self.stamp.available_at < self.published_at:
            raise AltDataInputError(
                f"{what} is available at {self.stamp.available_at!r}, before it was published "
                f"at {self.published_at!r}."
            )
        object.__setattr__(self, "record_id", digest_lines(_fundamental_lines(self)))

    @property
    def record_scheme(self) -> str:
        """The scheme this record kind's identities are derived under."""

        return FUNDAMENTAL_KEY_SCHEME

    @property
    def series_key(self) -> tuple[str, ...]:
        """Which line item of which statement, for which issuer, from which source."""

        return (
            self.source.source_id,
            repr(self.source.version),
            self.subject,
            self.statement.name,
            self.line_item,
        )

    @property
    def vintage_key(self) -> tuple[str, ...]:
        """The series and the fiscal period -- what a restatement shares with its original."""

        return (*self.series_key, self.fiscal_period.label)


def canonical_fundamental_key(observation: FundamentalObservation) -> str:
    """Render the canonical key a fundamental observation's identity is derived from."""

    return "\n".join(_fundamental_lines(observation))


def _fundamental_lines(observation: FundamentalObservation) -> list[str]:
    period = observation.fiscal_period
    return [
        FUNDAMENTAL_KEY_SCHEME,
        f"subject={observation.subject!r}",
        f"statement={observation.statement.name}",
        f"line_item={observation.line_item!r}",
        f"fiscal_period={period.label!r}[{period.start!r},{period.end!r}]",
        f"value={str(observation.value)!r}",
        f"unit={observation.unit!r}",
        f"published_at={observation.published_at!r}",
        f"revision={observation.revision!r}",
        *observation.source.record_lines,
        *stamp_lines(observation.stamp),
    ]


# --------------------------------------------------------------------------- #
# Point-in-time reads
# --------------------------------------------------------------------------- #


def _series_keys(
    view: ObservationView[FundamentalObservation],
    subject: str,
    statement: StatementType,
    line_item: str | None,
) -> list[tuple[str, ...]]:
    return [
        key
        for key in view.series_keys
        if key[2] == subject
        and key[3] == statement.name
        and (line_item is None or key[4] == line_item)
    ]


def _chosen(
    records: Iterable[FundamentalObservation], policy: VintagePolicy
) -> tuple[FundamentalObservation, ...]:
    if policy is VintagePolicy.AS_KNOWN:
        return latest_vintages(records)
    return original_vintages(records)


def fundamental_as_of(
    view: ObservationView[FundamentalObservation],
    subject: str,
    statement: StatementType,
    line_item: str,
    fiscal_period_label: str,
    as_of: float,
    policy: VintagePolicy,
) -> FundamentalObservation | None:
    """One line item for one period, as ``policy`` reads it at ``as_of``.

    ``None`` when nothing for that period was knowable then -- including when
    the figure exists in the set but was published later.
    """

    for key in _series_keys(view, subject, statement, line_item):
        found = view.vintage_as_of((*key, fiscal_period_label), as_of, policy)
        if found is not None:
            return found
    return None


def latest_fundamental(
    view: ObservationView[FundamentalObservation],
    subject: str,
    statement: StatementType,
    line_item: str,
    as_of: float,
    policy: VintagePolicy,
    *,
    annual: bool | None,
) -> FundamentalObservation | None:
    """The most recent period's figure knowable at ``as_of``.

    Args:
        annual: ``True`` for full fiscal years only, ``False`` for quarters only,
            ``None`` for whichever period is most recent. Required: whether the
            latest figure is a quarter's or a year's changes what it measures.
    """

    candidates: list[FundamentalObservation] = []
    for key in _series_keys(view, subject, statement, line_item):
        candidates.extend(_chosen(view.visible_in_series(key, as_of), policy))
    return _latest_period(candidates, annual)


def _latest_period(
    records: Iterable[FundamentalObservation], annual: bool | None
) -> FundamentalObservation | None:
    """The record for the most recent period, among records already chosen per period."""

    candidates = [
        record for record in records if annual is None or record.fiscal_period.is_annual is annual
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda record: (record.fiscal_period.end, record.fiscal_period.start, record.record_id),
    )


@dataclass(frozen=True, slots=True)
class FinancialStatement:
    """One statement for one period, assembled as it was readable at an instant.

    Attributes:
        subject: The issuer.
        statement: Which statement.
        fiscal_period: The period.
        as_of: The research instant.
        policy: Which revision of each item was read.
        items: Line item to the record read for it.
    """

    subject: str
    statement: StatementType
    fiscal_period: FiscalPeriod
    as_of: float
    policy: VintagePolicy
    items: Mapping[str, FundamentalObservation]

    def value(self, line_item: str) -> Decimal:
        """The figure for ``line_item``.

        Raises:
            AltDataInputError: If the statement, as readable then, has no such
                item. Absent is not zero.
        """

        found = self.items.get(line_item)
        if found is None:
            raise AltDataInputError(
                f"{self.subject!r} {self.statement.name} {self.fiscal_period.label} as of "
                f"{self.as_of!r} has no {line_item!r}; it has {sorted(self.items)}."
            )
        return found.value

    @property
    def restated_items(self) -> tuple[str, ...]:
        """Line items whose figure read here is a restatement, sorted."""

        return tuple(sorted(name for name, record in self.items.items() if record.revision > 0))


def statement_as_of(
    view: ObservationView[FundamentalObservation],
    subject: str,
    statement: StatementType,
    fiscal_period_label: str,
    as_of: float,
    policy: VintagePolicy,
) -> FinancialStatement | None:
    """Every line item of one statement for one period, as readable at ``as_of``.

    ``None`` when no item of that statement and period was knowable then.

    Raises:
        AltDataInputError: If two items read for the period disagree about the
            span it covers -- a source error that would otherwise let one
            statement describe two periods.
    """

    items: dict[str, FundamentalObservation] = {}
    for key in _series_keys(view, subject, statement, None):
        found = view.vintage_as_of((*key, fiscal_period_label), as_of, policy)
        if found is not None:
            items[found.line_item] = found
    if not items:
        return None
    periods = {record.fiscal_period for record in items.values()}
    if len(periods) != 1:
        raise AltDataInputError(
            f"{subject!r} {statement.name} {fiscal_period_label}: the items disagree about the "
            f"span the period covers: {sorted((p.start, p.end) for p in periods)}."
        )
    return FinancialStatement(
        subject=subject,
        statement=statement,
        fiscal_period=periods.pop(),
        as_of=as_of,
        policy=policy,
        items=MappingProxyType(dict(sorted(items.items()))),
    )


@dataclass(frozen=True, slots=True)
class TrailingValue:
    """A trailing-twelve-month figure, or why there is none.

    Attributes:
        subject: The issuer.
        line_item: The flow summed.
        as_of: The research instant.
        value: The sum of the four quarters, or ``None``.
        unit: Their shared unit, or ``None``.
        periods: The quarters summed, oldest first -- or, when undefined, the
            quarters that were knowable.
        record_ids: The records behind ``value``.
        reason: Empty when ``value`` is defined; otherwise why not.
    """

    subject: str
    line_item: str
    as_of: float
    value: Decimal | None
    unit: str | None
    periods: tuple[str, ...]
    record_ids: tuple[str, ...]
    reason: str


def trailing_twelve_months(
    view: ObservationView[FundamentalObservation],
    subject: str,
    statement: StatementType,
    line_item: str,
    as_of: float,
    policy: VintagePolicy,
) -> TrailingValue:
    """The sum of the four most recent consecutive quarters knowable at ``as_of``.

    Raises:
        AltDataInputError: If ``statement`` is the balance sheet. A balance is a
            level at a date; four of them summed measure nothing.
    """

    _require_flow(statement, line_item)
    chosen: list[FundamentalObservation] = []
    for key in _series_keys(view, subject, statement, line_item):
        chosen.extend(_chosen(view.visible_in_series(key, as_of), policy))
    return _trailing(subject, line_item, as_of, chosen)


def _require_flow(statement: StatementType, line_item: str) -> None:
    if not statement.is_duration:
        raise AltDataInputError(
            f"{line_item!r} is a balance-sheet item: a level at the period's end, not a flow "
            "over it. Summing four quarters of it measures nothing; read the latest figure."
        )


def _trailing(
    subject: str, line_item: str, as_of: float, chosen: Iterable[FundamentalObservation]
) -> TrailingValue:
    """Twelve months from records already chosen per period -- the one implementation."""

    quarters = sorted(
        (record for record in chosen if not record.fiscal_period.is_annual),
        key=lambda record: record.fiscal_period.quarter_ordinal(),
    )
    latest = quarters[-4:]
    labels = tuple(record.fiscal_period.label for record in latest)

    def undefined(reason: str) -> TrailingValue:
        return TrailingValue(subject, line_item, as_of, None, None, labels, (), reason)

    if len(latest) < 4:
        return undefined(
            f"only {len(latest)} quarter(s) of {line_item!r} were knowable at {as_of!r}; "
            "twelve months needs four"
        )
    ordinals = [record.fiscal_period.quarter_ordinal() for record in latest]
    if ordinals != list(range(ordinals[0], ordinals[0] + 4)):
        return undefined(
            f"the four most recent quarters knowable at {as_of!r} are not consecutive: "
            f"{list(labels)}"
        )
    units = {record.unit for record in latest}
    if len(units) != 1:
        return undefined(f"the four quarters are in different units: {sorted(units)}")
    total = _ZERO
    for record in latest:
        total = _CONTEXT.add(total, record.value)
    return TrailingValue(
        subject,
        line_item,
        as_of,
        total,
        units.pop(),
        labels,
        tuple(record.record_id for record in latest),
        "",
    )


@dataclass(frozen=True, slots=True)
class GrowthValue:
    """Year-over-year growth of one line item, or why there is none.

    Attributes:
        subject: The issuer.
        line_item: The item.
        as_of: The research instant.
        current: The latest period's record knowable then, or ``None``.
        prior: The same period one fiscal year earlier, as knowable then.
        growth: ``current / prior - 1``, or ``None``.
        reason: Empty when defined; otherwise why not.
    """

    subject: str
    line_item: str
    as_of: float
    current: FundamentalObservation | None
    prior: FundamentalObservation | None
    growth: Decimal | None
    reason: str


def year_over_year_growth(
    view: ObservationView[FundamentalObservation],
    subject: str,
    statement: StatementType,
    line_item: str,
    as_of: float,
    policy: VintagePolicy,
    *,
    annual: bool,
) -> GrowthValue:
    """The latest period against the same period a fiscal year earlier, both as knowable then.

    The prior period is read under the *same* instant and policy: comparing a
    restated prior year with an originally published current year would
    measure the restatement as growth.
    """

    current = latest_fundamental(view, subject, statement, line_item, as_of, policy, annual=annual)
    if current is None:
        return GrowthValue(
            subject, line_item, as_of, None, None, None, f"no {line_item!r} knowable at {as_of!r}"
        )
    period = current.fiscal_period
    prior_label = (
        f"FY{period.fiscal_year - 1}"
        if period.fiscal_quarter is None
        else f"FY{period.fiscal_year - 1}Q{period.fiscal_quarter}"
    )
    prior = fundamental_as_of(view, subject, statement, line_item, prior_label, as_of, policy)
    if prior is None:
        return GrowthValue(
            subject,
            line_item,
            as_of,
            current,
            None,
            None,
            f"{prior_label} was not knowable at {as_of!r}",
        )
    if prior.unit != current.unit:
        return GrowthValue(
            subject,
            line_item,
            as_of,
            current,
            prior,
            None,
            f"{prior_label} is in {prior.unit!r} and {period.label} in {current.unit!r}",
        )
    if prior.value <= _ZERO:
        return GrowthValue(
            subject,
            line_item,
            as_of,
            current,
            prior,
            None,
            f"growth off a non-positive base ({prior.value}) is undefined",
        )
    growth = _CONTEXT.subtract(_CONTEXT.divide(current.value, prior.value), Decimal(1))
    return GrowthValue(subject, line_item, as_of, current, prior, growth, "")


# --------------------------------------------------------------------------- #
# Inputs, valuation and ratios
# --------------------------------------------------------------------------- #


class Aggregation(Enum):
    """How a line item's history becomes one input figure at an instant."""

    #: The most recent period's figure, quarter or year. Right for a level --
    #: equity, debt, a share count -- and a quarter's flow for a flow.
    LATEST = auto()

    #: The most recent full fiscal year's figure.
    LATEST_ANNUAL = auto()

    #: The four most recent consecutive quarters summed. Flows only.
    TRAILING_TWELVE_MONTHS = auto()


@dataclass(frozen=True, slots=True)
class FundamentalInput:
    """Which line item feeds a named input, and how.

    Attributes:
        name: The input's name -- one of this module's canonical names, such as
            :data:`EARNINGS`, or any identifier a caller's own computation reads.
        statement: The statement the line item is on.
        line_item: The source's name for it.
        aggregation: How its history becomes one figure.

    Raises:
        AltDataInputError: If a name is not an identifier, or a balance-sheet
            item is asked for trailing twelve months.
    """

    name: str
    statement: StatementType
    line_item: str
    aggregation: Aggregation

    def __post_init__(self) -> None:
        require_identifier(self.name, "FundamentalInput.name")
        require_identifier(self.line_item, "FundamentalInput.line_item")
        if (
            self.aggregation is Aggregation.TRAILING_TWELVE_MONTHS
            and not self.statement.is_duration
        ):
            raise AltDataInputError(
                f"Input {self.name!r} asks for trailing twelve months of the balance-sheet "
                f"item {self.line_item!r}, which is a level and cannot be summed."
            )


@dataclass(frozen=True, slots=True)
class FundamentalInputs:
    """Every named input for one issuer, as knowable at one instant.

    Attributes:
        subject: The issuer.
        as_of: The research instant.
        visibility: The rule the view applied.
        policy: Which revision of each figure was read.
        values: Input name to figure, for the inputs that were defined.
        units: Input name to its unit.
        record_ids: Input name to the records behind it.
        observed_at: Input name to the end of the latest period behind it, so a
            caller can judge how old a figure is.
        missing: Input name to why it was not defined.
    """

    subject: str
    as_of: float
    visibility: VisibilityRule
    policy: VintagePolicy
    values: Mapping[str, Decimal]
    units: Mapping[str, str]
    record_ids: Mapping[str, tuple[str, ...]]
    observed_at: Mapping[str, float]
    missing: Mapping[str, str]

    def require(self, name: str) -> Decimal:
        """The named input, or a refusal carrying why it is missing.

        Raises:
            AltDataInputError: If the input is not defined.
        """

        found = self.values.get(name)
        if found is None:
            reason = self.missing.get(name, "it was not requested")
            raise AltDataInputError(
                f"Input {name!r} for {self.subject!r} as of {self.as_of!r} is not defined: "
                f"{reason}."
            )
        return found


def fundamental_inputs_as_of(
    view: ObservationView[FundamentalObservation],
    subject: str,
    as_of: float,
    policy: VintagePolicy,
    inputs: Sequence[FundamentalInput],
) -> FundamentalInputs:
    """Read every input for ``subject`` as it was knowable at ``as_of``.

    Raises:
        AltDataInputError: If no input is named, or two share a name.
    """

    if not inputs:
        raise AltDataInputError("Name at least one fundamental input to read.")
    names = [spec.name for spec in inputs]
    if len(set(names)) != len(names):
        raise AltDataInputError(f"Two inputs share a name: {sorted(names)}.")

    values: dict[str, Decimal] = {}
    units: dict[str, str] = {}
    sources: dict[str, tuple[str, ...]] = {}
    observed: dict[str, float] = {}
    missing: dict[str, str] = {}
    for spec in inputs:
        if spec.aggregation is Aggregation.TRAILING_TWELVE_MONTHS:
            trailing = trailing_twelve_months(
                view, subject, spec.statement, spec.line_item, as_of, policy
            )
            if trailing.value is None or trailing.unit is None:
                missing[spec.name] = trailing.reason
                continue
            values[spec.name] = trailing.value
            units[spec.name] = trailing.unit
            sources[spec.name] = trailing.record_ids
            latest = latest_fundamental(
                view, subject, spec.statement, spec.line_item, as_of, policy, annual=False
            )
            if latest is not None:
                observed[spec.name] = latest.fiscal_period.end
            continue
        record = latest_fundamental(
            view,
            subject,
            spec.statement,
            spec.line_item,
            as_of,
            policy,
            annual=True if spec.aggregation is Aggregation.LATEST_ANNUAL else None,
        )
        if record is None:
            missing[spec.name] = f"no {spec.line_item!r} was knowable at {as_of!r}"
            continue
        values[spec.name] = record.value
        units[spec.name] = record.unit
        sources[spec.name] = (record.record_id,)
        observed[spec.name] = record.fiscal_period.end

    return FundamentalInputs(
        subject=subject,
        as_of=as_of,
        visibility=view.visibility,
        policy=policy,
        values=MappingProxyType(values),
        units=MappingProxyType(units),
        record_ids=MappingProxyType(sources),
        observed_at=MappingProxyType(observed),
        missing=MappingProxyType(missing),
    )


@dataclass(frozen=True, slots=True)
class AggregateStep:
    """One change in what an aggregated fundamental input reads as.

    Attributes:
        known_at: The knowledge instant at which the input changed.
        value: What it reads as from then on, or ``None`` when undefined.
        unit: The value's unit, or ``None``.
        observed_at: The end of the latest period behind the value, or
            ``None`` -- what a caller measures the figure's age from.
        record_ids: The records behind the value.
        reason: Empty when defined; otherwise why not.
    """

    known_at: float
    value: Decimal | None
    unit: str | None
    observed_at: float | None
    record_ids: tuple[str, ...]
    reason: str


def aggregate_timeline(
    view: ObservationView[FundamentalObservation],
    subject: str,
    spec: FundamentalInput,
    policy: VintagePolicy,
) -> tuple[AggregateStep, ...]:
    """How one input read over time, as each figure behind it became knowable.

    One :class:`AggregateStep` for each knowledge instant at which the input's
    value, or the records behind it, changed. The input at a research instant is
    the last step at or before it -- one bisection -- which is how a feature
    frame samples an issuer's fundamentals at every bar without re-reading the
    set at each. The aggregation is the same one :func:`fundamental_inputs_as_of`
    applies, over the same records chosen per period, so the two cannot
    disagree about a figure.

    Raises:
        AltDataInputError: If ``spec`` asks for trailing twelve months of a
            balance-sheet item.
    """

    if spec.aggregation is Aggregation.TRAILING_TWELVE_MONTHS:
        _require_flow(spec.statement, spec.line_item)
    arrivals: list[tuple[float, FundamentalObservation]] = []
    for key in _series_keys(view, subject, spec.statement, spec.line_item):
        index = view.series[key]
        arrivals.extend(zip(index.known_instants, index.records, strict=True))
    arrivals.sort(key=lambda entry: (entry[0], entry[1].record_id))

    per_period: dict[str, FundamentalObservation] = {}
    steps: list[AggregateStep] = []
    position = 0
    while position < len(arrivals):
        instant = arrivals[position][0]
        while position < len(arrivals) and arrivals[position][0] == instant:
            record = arrivals[position][1]
            position += 1
            if policy is VintagePolicy.ORIGINAL and record.revision != 0:
                continue
            label = record.fiscal_period.label
            held = per_period.get(label)
            newer = policy is VintagePolicy.AS_KNOWN and held is not None
            if held is None or (newer and record.revision > held.revision):
                per_period[label] = record
        step = _aggregate_step(subject, spec, instant, per_period.values())
        if not steps or (steps[-1].value, steps[-1].record_ids) != (step.value, step.record_ids):
            steps.append(step)
    return tuple(steps)


def _aggregate_step(
    subject: str,
    spec: FundamentalInput,
    instant: float,
    chosen: Iterable[FundamentalObservation],
) -> AggregateStep:
    if spec.aggregation is Aggregation.TRAILING_TWELVE_MONTHS:
        rows = list(chosen)
        trailing = _trailing(subject, spec.line_item, instant, rows)
        latest = _latest_period(rows, annual=False)
        return AggregateStep(
            known_at=instant,
            value=trailing.value,
            unit=trailing.unit,
            observed_at=None if latest is None else latest.fiscal_period.end,
            record_ids=trailing.record_ids,
            reason=trailing.reason,
        )
    record = _latest_period(
        chosen, annual=True if spec.aggregation is Aggregation.LATEST_ANNUAL else None
    )
    if record is None:
        return AggregateStep(
            instant, None, None, None, (), f"no {spec.line_item!r} was knowable at {instant!r}"
        )
    return AggregateStep(
        instant, record.value, record.unit, record.fiscal_period.end, (record.record_id,), ""
    )


def currency_of_per_share_unit(unit: str) -> str:
    """The currency of a ``"<currency>/share"`` unit.

    Raises:
        AltDataInputError: If ``unit`` is not of that form. A per-share figure
            whose currency is assumed is compared with figures in another.
    """

    currency, separator, per = unit.partition("/")
    if not separator or per != "share" or not currency:
        raise AltDataInputError(
            f"A per-share unit is '<currency>/share', and {unit!r} is not. A price whose "
            "currency is assumed is compared with figures in another."
        )
    return currency


def _ratio(
    numerator: Decimal, denominator: Decimal, *, require_positive_denominator: bool
) -> Decimal | None:
    if denominator == _ZERO or (require_positive_denominator and denominator < _ZERO):
        return None
    return _CONTEXT.divide(numerator, denominator)


@dataclass(frozen=True, slots=True)
class ValuationMetrics:
    """Market-price-based valuation of one issuer at one instant.

    Every metric is ``None`` when undefined, with the reason in
    :attr:`undefined`.

    Attributes:
        subject: The issuer.
        as_of: The research instant.
        currency: The price's currency, which every monetary input shared.
        price: The price per share supplied.
        price_observed_at: When that price was observed; never after ``as_of``.
        market_capitalization: Price times shares outstanding.
        enterprise_value: Market capitalization plus debt less cash.
        earnings_yield: Earnings over market capitalization. Defined for
            losses, which is why research prefers it to its inverse.
        price_to_earnings: Market capitalization over positive earnings.
        book_to_market: Book equity over market capitalization.
        price_to_book: Market capitalization over positive book equity.
        sales_to_price: Revenue over market capitalization.
        enterprise_value_to_ebitda: Enterprise value over positive EBITDA.
        enterprise_value_to_sales: Enterprise value over positive revenue.
        free_cash_flow_yield: Free cash flow over market capitalization.
        dividend_yield: Dividends per share over price.
        undefined: Metric name to why it is ``None``.
        record_ids: Every fundamental record behind the metrics, sorted.
    """

    subject: str
    as_of: float
    currency: str
    price: Decimal
    price_observed_at: float
    market_capitalization: Decimal | None
    enterprise_value: Decimal | None
    earnings_yield: Decimal | None
    price_to_earnings: Decimal | None
    book_to_market: Decimal | None
    price_to_book: Decimal | None
    sales_to_price: Decimal | None
    enterprise_value_to_ebitda: Decimal | None
    enterprise_value_to_sales: Decimal | None
    free_cash_flow_yield: Decimal | None
    dividend_yield: Decimal | None
    undefined: Mapping[str, str]
    record_ids: tuple[str, ...]


def _require_unit(inputs: FundamentalInputs, name: str, expected: str) -> None:
    unit = inputs.units.get(name)
    if unit is not None and unit != expected:
        raise AltDataInputError(
            f"Input {name!r} for {inputs.subject!r} is in {unit!r}, and valuation needs "
            f"{expected!r}. A figure in another unit is a wrong number, not a conversion."
        )


def valuation_metrics(
    inputs: FundamentalInputs, price: Decimal, price_unit: str, price_observed_at: float
) -> ValuationMetrics:
    """Valuation metrics from point-in-time fundamentals and a supplied price.

    The price is the caller's -- AlphaLab ships no market data -- and so is the
    instant it was observed, which must not be after the research instant: a
    valuation that divides this quarter's earnings by next week's price looks
    ahead through the denominator.

    Reads the canonical inputs :data:`SHARES_OUTSTANDING` (required),
    :data:`EARNINGS`, :data:`BOOK_EQUITY`, :data:`REVENUE`, :data:`EBITDA`,
    :data:`FREE_CASH_FLOW`, :data:`TOTAL_DEBT`, :data:`CASH` and
    :data:`DIVIDENDS_PER_SHARE`, each where present.

    Raises:
        AltDataInputError: If the price is not positive or its unit is not
            ``"<currency>/share"``; if it was observed after ``inputs.as_of``;
            if the share count is missing; or if any input's unit disagrees
            with the price's currency.
    """

    require_finite_value(price, "price")
    require_finite_instant(price_observed_at, "price_observed_at")
    if price <= _ZERO:
        raise AltDataInputError(f"A price of {price} is not a price.")
    currency = currency_of_per_share_unit(price_unit)
    if price_observed_at > inputs.as_of:
        raise AltDataInputError(
            f"The price was observed at {price_observed_at!r}, after the research instant "
            f"{inputs.as_of!r}. Valuing a company at a price nobody had seen yet is a "
            "look-ahead through the denominator."
        )
    _require_unit(inputs, SHARES_OUTSTANDING, "shares")
    for name in (EARNINGS, BOOK_EQUITY, REVENUE, EBITDA, FREE_CASH_FLOW, TOTAL_DEBT, CASH):
        _require_unit(inputs, name, currency)
    _require_unit(inputs, DIVIDENDS_PER_SHARE, f"{currency}/share")
    shares = inputs.require(SHARES_OUTSTANDING)
    if shares <= _ZERO:
        raise AltDataInputError(f"{inputs.subject!r} reports {shares} shares outstanding.")

    undefined: dict[str, str] = {}
    cap = _CONTEXT.multiply(price, shares)

    def read(name: str, metric: str) -> Decimal | None:
        found = inputs.values.get(name)
        if found is None:
            undefined[metric] = f"{name} is not defined: " + inputs.missing.get(
                name, "it was not requested"
            )
        return found

    earnings = read(EARNINGS, "earnings_yield")
    earnings_yield = None if earnings is None else _CONTEXT.divide(earnings, cap)
    price_to_earnings = None
    if earnings is not None:
        price_to_earnings = _ratio(cap, earnings, require_positive_denominator=True)
        if price_to_earnings is None:
            undefined["price_to_earnings"] = f"earnings of {earnings} are not positive"
    else:
        undefined["price_to_earnings"] = undefined["earnings_yield"]

    book = read(BOOK_EQUITY, "book_to_market")
    book_to_market = None if book is None else _CONTEXT.divide(book, cap)
    price_to_book = None
    if book is not None:
        price_to_book = _ratio(cap, book, require_positive_denominator=True)
        if price_to_book is None:
            undefined["price_to_book"] = f"book equity of {book} is not positive"
    else:
        undefined["price_to_book"] = undefined["book_to_market"]

    revenue = read(REVENUE, "sales_to_price")
    sales_to_price = None if revenue is None else _CONTEXT.divide(revenue, cap)

    debt = inputs.values.get(TOTAL_DEBT)
    cash = inputs.values.get(CASH)
    enterprise_value = None
    if debt is None or cash is None:
        absent = [name for name, value in ((TOTAL_DEBT, debt), (CASH, cash)) if value is None]
        undefined["enterprise_value"] = (
            f"{' and '.join(absent)} not defined; an enterprise value without them would "
            "treat the missing figure as zero"
        )
    else:
        enterprise_value = _CONTEXT.subtract(_CONTEXT.add(cap, debt), cash)

    def enterprise_multiple(name: str, metric: str) -> Decimal | None:
        denominator = read(name, metric)
        if denominator is None:
            return None
        if enterprise_value is None:
            undefined[metric] = undefined["enterprise_value"]
            return None
        result = _ratio(enterprise_value, denominator, require_positive_denominator=True)
        if result is None:
            undefined[metric] = f"{name} of {denominator} is not positive"
        return result

    ev_to_ebitda = enterprise_multiple(EBITDA, "enterprise_value_to_ebitda")
    ev_to_sales = enterprise_multiple(REVENUE, "enterprise_value_to_sales")

    free_cash_flow = read(FREE_CASH_FLOW, "free_cash_flow_yield")
    fcf_yield = None if free_cash_flow is None else _CONTEXT.divide(free_cash_flow, cap)

    dividends = read(DIVIDENDS_PER_SHARE, "dividend_yield")
    dividend_yield = None if dividends is None else _CONTEXT.divide(dividends, price)

    used = sorted({identity for ids in inputs.record_ids.values() for identity in ids})
    return ValuationMetrics(
        subject=inputs.subject,
        as_of=inputs.as_of,
        currency=currency,
        price=price,
        price_observed_at=price_observed_at,
        market_capitalization=cap,
        enterprise_value=enterprise_value,
        earnings_yield=earnings_yield,
        price_to_earnings=price_to_earnings,
        book_to_market=book_to_market,
        price_to_book=price_to_book,
        sales_to_price=sales_to_price,
        enterprise_value_to_ebitda=ev_to_ebitda,
        enterprise_value_to_sales=ev_to_sales,
        free_cash_flow_yield=fcf_yield,
        dividend_yield=dividend_yield,
        undefined=MappingProxyType(dict(sorted(undefined.items()))),
        record_ids=tuple(used),
    )


@dataclass(frozen=True, slots=True)
class FinancialRatios:
    """Profitability, leverage and liquidity ratios of one issuer at one instant.

    Every ratio is ``None`` when undefined, with the reason in
    :attr:`undefined`.

    Attributes:
        subject: The issuer.
        as_of: The research instant.
        gross_margin: Gross profit over positive revenue.
        operating_margin: Operating income over positive revenue.
        net_margin: Earnings over positive revenue.
        return_on_equity: Earnings over positive book equity.
        return_on_assets: Earnings over positive total assets.
        debt_to_equity: Total debt over positive book equity.
        current_ratio: Current assets over positive current liabilities.
        undefined: Ratio name to why it is ``None``.
        record_ids: Every record behind the ratios, sorted.
    """

    subject: str
    as_of: float
    gross_margin: Decimal | None
    operating_margin: Decimal | None
    net_margin: Decimal | None
    return_on_equity: Decimal | None
    return_on_assets: Decimal | None
    debt_to_equity: Decimal | None
    current_ratio: Decimal | None
    undefined: Mapping[str, str]
    record_ids: tuple[str, ...]


def financial_ratios(inputs: FundamentalInputs) -> FinancialRatios:
    """Ratios from point-in-time fundamentals. Needs no price.

    Raises:
        AltDataInputError: If a ratio's numerator and denominator are in
            different units -- a margin of dollars over euros is not a margin.
    """

    undefined: dict[str, str] = {}

    def ratio(metric: str, numerator_name: str, denominator_name: str) -> Decimal | None:
        numerator = inputs.values.get(numerator_name)
        denominator = inputs.values.get(denominator_name)
        absent = [
            name
            for name, value in ((numerator_name, numerator), (denominator_name, denominator))
            if value is None
        ]
        if absent or numerator is None or denominator is None:
            undefined[metric] = " and ".join(absent) + " not defined"
            return None
        if inputs.units[numerator_name] != inputs.units[denominator_name]:
            raise AltDataInputError(
                f"{metric} for {inputs.subject!r} divides {numerator_name} in "
                f"{inputs.units[numerator_name]!r} by {denominator_name} in "
                f"{inputs.units[denominator_name]!r}."
            )
        result = _ratio(numerator, denominator, require_positive_denominator=True)
        if result is None:
            undefined[metric] = f"{denominator_name} of {denominator} is not positive"
        return result

    used = sorted({identity for ids in inputs.record_ids.values() for identity in ids})
    return FinancialRatios(
        subject=inputs.subject,
        as_of=inputs.as_of,
        gross_margin=ratio("gross_margin", GROSS_PROFIT, REVENUE),
        operating_margin=ratio("operating_margin", OPERATING_INCOME, REVENUE),
        net_margin=ratio("net_margin", EARNINGS, REVENUE),
        return_on_equity=ratio("return_on_equity", EARNINGS, BOOK_EQUITY),
        return_on_assets=ratio("return_on_assets", EARNINGS, TOTAL_ASSETS),
        debt_to_equity=ratio("debt_to_equity", TOTAL_DEBT, BOOK_EQUITY),
        current_ratio=ratio("current_ratio", CURRENT_ASSETS, CURRENT_LIABILITIES),
        undefined=MappingProxyType(dict(sorted(undefined.items()))),
        record_ids=tuple(used),
    )


# --------------------------------------------------------------------------- #
# Restatement analysis
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Restatement:
    """One figure as first published against its latest restatement in a set.

    Attributes:
        original: The record as first published.
        latest: The highest revision in the set.
        change: ``latest.value - original.value``.
        revisions: How many restatements the set holds for the figure.
    """

    original: FundamentalObservation
    latest: FundamentalObservation
    change: Decimal
    revisions: int


def restatements(obs_set: ObservationSet[FundamentalObservation]) -> tuple[Restatement, ...]:
    """Every figure in a set that was restated, original against latest.

    This is hindsight, by design and by name: it compares what was first
    published with what the set eventually holds, across the whole set and at
    no research instant. It is how restatement bias is *measured*; no query
    that answers "what was knowable then?" uses it.

    Figures whose original is not in the set are left out: there is no first
    publication to compare against.
    """

    grouped: dict[tuple[str, ...], list[FundamentalObservation]] = {}
    for record in obs_set.records:
        grouped.setdefault(record.vintage_key, []).append(record)
    found: list[Restatement] = []
    for vintage in sorted(grouped):
        rows = sorted(grouped[vintage], key=lambda record: record.revision)
        if len(rows) < 2 or rows[0].revision != 0:
            continue
        original, latest = rows[0], rows[-1]
        found.append(
            Restatement(
                original=original,
                latest=latest,
                change=_CONTEXT.subtract(latest.value, original.value),
                revisions=len(rows) - 1,
            )
        )
    return tuple(found)
