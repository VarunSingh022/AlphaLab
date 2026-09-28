"""Execution quality: what execution cost, measured against references that are named.

v3.3 made a fill's *assumed* costs explicit -- :class:`~alphalab.execution.costs.ExecutionCosts`
itemizes what a cost model charged, and execution attribution reports it. This
module measures what execution *achieved*: given the canonical order, its
canonical fills and the market references a caller supplies, how far did the
fills land from where the decision was made, how completely and how cleanly did
the order fill, how quickly did each step happen, and how often did a venue
refuse. It reuses what exists and defines no second fill:

======================== ===========================================================
The order                :class:`~alphalab.core.order_request.OrderRequest` -- side,
                         quantity, the price it was sized at, and the strategies
                         that asked for it
The fills                :class:`~alphalab.execution.report.ExecutionReport` -- the
                         canonical fill evidence the portfolio consumes, with its
                         ``commission`` as the one cash-cost channel
The strategy split       :func:`~alphalab.core.contribution.split_by_contribution`,
                         the rule P&L attribution has used since v2.6
Currency conversion      any :class:`~alphalab.common.currency.CurrencyConverter` --
                         :class:`~alphalab.portfolio.fx.FxRates` satisfies it -- with
                         every conversion kept
Averages and quantiles   :mod:`alphalab.common.statistics`
======================== ===========================================================

Every measurement states its basis
----------------------------------

**Implementation shortfall** (Perold, 1988) is the cost of the order relative
to a *paper* trade of the whole quantity at the **decision price**. Signs are
costs: positive means worse than the reference, for a buy (paid more) and for a
sale (received less) alike::

    execution cost    = sum over fills of  q_i * (p_i - P_decision) * s
    explicit costs    = sum over fills of the report's commission (every cash cost)
    opportunity cost  = unfilled quantity * (P_end - P_decision) * s
    total             = execution + explicit + opportunity
    in basis points   = total / (ordered quantity * P_decision) * 10,000

where ``s`` is +1 to buy and -1 to sell. With an **arrival price** the execution
cost splits into *delay* (``filled * (P_arrival - P_decision) * s``) and *trading*
(``sum q_i * (p_i - P_arrival) * s``), which sum to it exactly. The opportunity
cost needs ``P_end`` -- the benchmark price when the order stopped working -- and
is ``None`` without one, which makes the total ``None``: an unfilled order's
shortfall is not known without it, and zero would claim it cost nothing.

**Slippage** is the average fill price against *one named reference* --
decision, arrival or interval VWAP -- per unit, in money and in basis points of
the reference. It is a measurement, and it is not
:attr:`ExecutionReport.slippage <alphalab.execution.report.ExecutionReport.slippage>`,
which is the concession a cost model *assumed* (and zero -- unmeasured -- for a
venue fill). ``test_shared_names_stay_distinct.py`` keeps the two apart.

**Fill quality** is three things, each stated: *completeness* (filled over
ordered quantity), *price quality* (improvement on the limit, and the effective
spread against the quote midpoint at each fill when those midpoints are
supplied), and *fragmentation* (how many fills it took).

**Latency** runs between two named :class:`LifecycleMark` instants of one order,
in seconds, and says which clock stamped each end. Across two clocks it includes
whatever offset lies between them, and says so; an end before its start is
reported inconsistent, never as a negative latency.

**Rejection rate** is venue rejections over *resolved* submissions -- orders the
venue answered one way or the other -- among those submitted in a stated window,
optionally at one venue. An order AlphaLab refused before sending (risk,
capability, routing) was never submitted and is in neither count; one still
awaiting an answer is counted as unresolved and is in neither.

**Venue quality** is the execution-side view of a venue: what it filled, how far
from the reference, how often it refused and how fast it answered -- measured
from outcomes. It is not a routing decision's *expectation* of the same venue;
:mod:`alphalab.execution.routing` predicts, this measures, and comparing the two
is a caller's question.

Nothing is fabricated
---------------------

A measurement whose evidence is missing is ``None`` with the reason in
``unavailable``, and a result that rests on a caller-supplied reference names
it in ``assumptions``. Money is never summed across currencies: per-currency
totals always, a reporting-currency total only through a converter, at a stated
conversion instant, with every conversion recorded -- and absent when any figure
could not be converted.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal
from enum import StrEnum, unique
from types import MappingProxyType
from typing import Final

from alphalab.common.arithmetic import canonical_text
from alphalab.common.currency import ConversionRecord, CurrencyConverter
from alphalab.core.contribution import split_by_contribution
from alphalab.core.enums import OrderStatus, Side
from alphalab.core.order_request import OrderRequest
from alphalab.execution.exceptions import ExecutionValidationError
from alphalab.execution.report import ExecutionReport

__all__ = [
    "EXECUTION_QUALITY_SCHEME",
    "ClockSource",
    "ExecutionBenchmarks",
    "ExecutionQualityReport",
    "FillQuality",
    "ImplementationShortfall",
    "LatencyMeasurement",
    "LifecycleMark",
    "OrderExecution",
    "OrderOutcome",
    "OrderTimeline",
    "ReferencePrice",
    "RejectionRate",
    "SlippageMeasurement",
    "TimelineMark",
    "VenueQuality",
    "execution_quality_report",
    "fill_quality",
    "implementation_shortfall",
    "measure_latency",
    "measure_slippage",
    "rejection_rate",
    "venue_quality",
]

#: Scheme tag of an execution-quality report's identity. Version 2 (v3.11, ledger
#: DET-006) renders every ``Decimal`` by value.
EXECUTION_QUALITY_SCHEME: Final = "alphalab.execution_quality.v2"

#: Fixed arithmetic, never the caller's thread context.
_CONTEXT: Final = Context(prec=34, rounding=ROUND_HALF_EVEN)

#: The precision a strategy's share of a measured cost is carried to. Measured
#: amounts are analytics, not ledger entries, and are otherwise unrounded; the
#: split rounds every share but the last, which takes the remainder, so shares
#: still sum exactly to the amount split.
_SHARE_QUANTUM: Final = Decimal("0.0001")

_ZERO = Decimal("0")
_BPS = Decimal("10000")

#: Statuses in which the venue has not yet answered a submission.
_UNRESOLVED: Final = frozenset({OrderStatus.NEW, OrderStatus.PENDING})


def _digest(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _value(amount: Decimal | None) -> str:
    """An amount in an identity: by value (:func:`canonical_text`), or ``None``."""

    return "None" if amount is None else canonical_text(amount)


def _add(left: Decimal, right: Decimal) -> Decimal:
    return _CONTEXT.add(left, right)


def _sub(left: Decimal, right: Decimal) -> Decimal:
    return _CONTEXT.subtract(left, right)


def _mul(left: Decimal, right: Decimal) -> Decimal:
    return _CONTEXT.multiply(left, right)


def _div(left: Decimal, right: Decimal) -> Decimal:
    return _CONTEXT.divide(left, right)


def _sum(values: Iterable[Decimal]) -> Decimal:
    total = _ZERO
    for value in values:
        total = _add(total, value)
    return total


def _sign(side: Side) -> Decimal:
    return Decimal(1) if side is Side.BUY else Decimal(-1)


def _instant(value: float) -> Decimal:
    return Decimal(repr(value))


def _repeated(keys: Iterable[str]) -> list[str]:
    """Keys that occur more than once, sorted. One pass, however many keys."""

    return sorted(key for key, count in Counter(keys).items() if count > 1)


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #


@unique
class ReferencePrice(StrEnum):
    """A price an execution is measured against."""

    #: The price the decision was made at.
    DECISION = "decision"

    #: The market price when the order reached the market.
    ARRIVAL = "arrival"

    #: The market's volume-weighted average price over the execution interval.
    INTERVAL_VWAP = "interval_vwap"


@dataclass(frozen=True, slots=True)
class ExecutionBenchmarks:
    """The market references one order is measured against. Supplied; each may be absent.

    Attributes:
        decision_price: The price the decision was made at. ``None`` means use
            the order's own :attr:`~alphalab.core.order_request.OrderRequest.price`
            -- the reference price allocation sized it at -- and the result says
            it did.
        arrival_price: The market price when the order reached the market.
        interval_vwap: The market VWAP over the interval the order worked.
        end_price: The price when the order stopped working, for the
            opportunity cost of what did not fill.
        source: Where these came from. Required: a benchmark nobody can
            attribute is a number a result cannot be checked against.
    """

    decision_price: Decimal | None
    arrival_price: Decimal | None
    interval_vwap: Decimal | None
    end_price: Decimal | None
    source: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ExecutionValidationError("Execution benchmarks name no source.")
        for name in ("decision_price", "arrival_price", "interval_vwap", "end_price"):
            value: Decimal | None = getattr(self, name)
            if value is not None and value <= _ZERO:
                raise ExecutionValidationError(f"A {name} of {value} is not a price.")


@unique
class LifecycleMark(StrEnum):
    """An instant in one order's life that latency can be measured between."""

    DECISION = "decision"
    SUBMISSION = "submission"
    ACKNOWLEDGEMENT = "acknowledgement"
    FIRST_FILL = "first_fill"
    LAST_FILL = "last_fill"
    TERMINAL = "terminal"


@unique
class ClockSource(StrEnum):
    """Whose clock stamped an instant."""

    #: AlphaLab's: a record's timestamp, or a ``now`` a caller supplied.
    LOCAL = "local"

    #: The venue's, as it reported the instant.
    VENUE = "venue"


@dataclass(frozen=True, slots=True)
class TimelineMark:
    """One instant of an order's life, and whose clock stamped it."""

    mark: LifecycleMark
    at: float
    clock: ClockSource

    def __post_init__(self) -> None:
        if not math.isfinite(self.at):
            raise ExecutionValidationError(f"A {self.mark} mark at {self.at!r} has no instant.")


@dataclass(frozen=True, slots=True)
class OrderTimeline:
    """The instants of one order's life that were recorded, at most one per mark."""

    order_id: str
    venue: str
    marks: tuple[TimelineMark, ...]

    def __post_init__(self) -> None:
        repeated = _repeated(str(mark.mark) for mark in self.marks)
        if repeated:
            raise ExecutionValidationError(
                f"Order {self.order_id}'s timeline records {repeated} more than once."
            )

    def mark(self, which: LifecycleMark) -> TimelineMark | None:
        """The recorded instant for one mark, or ``None``."""

        return next((mark for mark in self.marks if mark.mark is which), None)


@dataclass(frozen=True, slots=True)
class OrderExecution:
    """Everything measured about one order, from the canonical types.

    Attributes:
        order: The canonical order -- decision, side, quantity, strategies.
        fills: Its canonical fills. Every one must name this order, its asset
            and ``currency``; no execution id may appear twice, and together they
            may not exceed the order.
        currency: The execution currency: what the order traded and settled
            in, and what every measured amount is expressed in.
        outcome: The order's status when measured.
        benchmarks: The references it is measured against.
        limit_price: Its limit, when it had one.
        timeline: The instants recorded for it, or ``None``.
        fill_midpoints: The quote midpoint at each fill, by execution id, for the
            effective spread; ``None`` when not recorded.
        account_id: The account it traded in, for provenance; ``None`` when not
            known.

    Raises:
        ExecutionValidationError: If a fill names another order, asset or
            currency, an execution id repeats, or the fills exceed the order.
    """

    order: OrderRequest
    fills: tuple[ExecutionReport, ...]
    currency: str
    outcome: OrderStatus
    benchmarks: ExecutionBenchmarks
    limit_price: Decimal | None
    timeline: OrderTimeline | None
    fill_midpoints: Mapping[str, Decimal] | None
    account_id: str | None

    def __post_init__(self) -> None:
        if not self.currency.strip():
            raise ExecutionValidationError("An order execution names no execution currency.")
        if self.order.quantity <= _ZERO:
            raise ExecutionValidationError(
                f"Order {self.order.order_id} is for {self.order.quantity}; nothing to measure."
            )
        repeated = _repeated(fill.execution_id for fill in self.fills)
        if repeated:
            raise ExecutionValidationError(
                f"Fills {repeated} appear twice for order {self.order.order_id}. Measuring a "
                "fill twice would double its cost while every ratio still looked plausible."
            )
        for fill in self.fills:
            if fill.order_id != self.order.order_id:
                raise ExecutionValidationError(
                    f"Fill {fill.execution_id} is for order {fill.order_id}, not "
                    f"{self.order.order_id}."
                )
            if fill.asset_id != self.order.asset_id:
                raise ExecutionValidationError(
                    f"Fill {fill.execution_id} traded {fill.asset_id}, not {self.order.asset_id}."
                )
            if fill.currency != self.currency:
                raise ExecutionValidationError(
                    f"Fill {fill.execution_id} settled in {fill.currency} and the order is "
                    f"measured in {self.currency}; one order is measured in one currency."
                )
        if self.filled_quantity > self.order.quantity:
            raise ExecutionValidationError(
                f"Order {self.order.order_id}'s fills total {self.filled_quantity} of "
                f"{self.order.quantity}: an overfill is not a fill to measure."
            )

    @property
    def filled_quantity(self) -> Decimal:
        """Everything that filled."""

        return _sum(fill.fill_quantity for fill in self.fills)

    @property
    def average_fill_price(self) -> Decimal | None:
        """The quantity-weighted average fill price, or ``None`` with no fill."""

        filled = self.filled_quantity
        if filled == _ZERO:
            return None
        return _div(_sum(_mul(f.fill_quantity, f.fill_price) for f in self.fills), filled)

    @property
    def venues(self) -> tuple[str, ...]:
        """Every venue a fill executed at, sorted."""

        return tuple(sorted({fill.venue for fill in self.fills}))

    def decision_price(self) -> tuple[Decimal, str]:
        """The decision price and where it came from."""

        if self.benchmarks.decision_price is not None:
            return (
                self.benchmarks.decision_price,
                f"decision price supplied by {self.benchmarks.source!r}",
            )
        return (
            self.order.price,
            "decision price is the order's reference price -- what allocation sized it at",
        )


# --------------------------------------------------------------------------- #
# Implementation shortfall
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ImplementationShortfall:
    """One order's cost against a paper trade at the decision price. Positive is a cost.

    Every amount is in :attr:`currency`, the order's execution currency.

    Attributes:
        order_id: The order.
        asset_id: What it traded.
        side: Its direction.
        currency: What every amount here is in.
        ordered_quantity: The order's quantity.
        filled_quantity: What filled.
        unfilled_quantity: What did not.
        decision_price: The reference the paper trade is priced at.
        arrival_price: The market at arrival, when supplied.
        end_price: The price the unfilled remainder is costed at, when supplied.
        average_fill_price: ``None`` with no fill.
        paper_notional: ``ordered_quantity * decision_price``.
        execution_cost: Fills against the decision price.
        delay_cost: Arrival against decision, on the filled quantity; ``None``
            without an arrival price.
        trading_cost: Fills against arrival; ``None`` without an arrival price.
        explicit_costs: Every cash cost the fills carried.
        opportunity_cost: The unfilled quantity against ``end_price``; zero when
            everything filled, ``None`` when something did not and no end price
            was supplied.
        total: The sum, or ``None`` when the opportunity cost is unknown.
        total_bps: ``total`` in basis points of the paper notional.
        strategy_shares: ``total`` divided among the strategies that asked for
            the order; empty when the total is unknown or the order names none.
        execution_ids: The fills measured, in order.
        venues: Where they executed.
        assumptions: What the figures rest on beyond the evidence itself.
        unavailable: Each figure that could not be computed, and why.
    """

    order_id: str
    asset_id: str
    side: Side
    currency: str
    ordered_quantity: Decimal
    filled_quantity: Decimal
    unfilled_quantity: Decimal
    decision_price: Decimal
    arrival_price: Decimal | None
    end_price: Decimal | None
    average_fill_price: Decimal | None
    paper_notional: Decimal
    execution_cost: Decimal
    delay_cost: Decimal | None
    trading_cost: Decimal | None
    explicit_costs: Decimal
    opportunity_cost: Decimal | None
    total: Decimal | None
    total_bps: Decimal | None
    strategy_shares: tuple[tuple[str, Decimal], ...]
    execution_ids: tuple[str, ...]
    venues: tuple[str, ...]
    assumptions: tuple[str, ...]
    unavailable: Mapping[str, str]


def implementation_shortfall(execution: OrderExecution) -> ImplementationShortfall:
    """Measure one order's implementation shortfall. See the module docstring for the basis."""

    order = execution.order
    sign = _sign(order.side)
    decision, decision_basis = execution.decision_price()
    benchmarks = execution.benchmarks
    filled = execution.filled_quantity
    unfilled = _sub(order.quantity, filled)
    assumptions = [decision_basis]
    unavailable: dict[str, str] = {}

    execution_cost = _sum(
        _mul(_mul(fill.fill_quantity, _sub(fill.fill_price, decision)), sign)
        for fill in execution.fills
    )
    explicit = _sum(fill.commission for fill in execution.fills)

    delay: Decimal | None = None
    trading: Decimal | None = None
    if benchmarks.arrival_price is None:
        reason = "no arrival price was supplied"
        unavailable["delay_cost"] = reason
        unavailable["trading_cost"] = reason
    else:
        arrival = benchmarks.arrival_price
        assumptions.append(f"arrival price supplied by {benchmarks.source!r}")
        delay = _mul(_mul(filled, _sub(arrival, decision)), sign)
        trading = _sum(
            _mul(_mul(fill.fill_quantity, _sub(fill.fill_price, arrival)), sign)
            for fill in execution.fills
        )

    opportunity: Decimal | None
    if unfilled == _ZERO:
        opportunity = _ZERO
    elif benchmarks.end_price is None:
        opportunity = None
        unavailable["opportunity_cost"] = (
            f"{unfilled} did not fill and no end price was supplied to cost it at"
        )
    else:
        opportunity = _mul(_mul(unfilled, _sub(benchmarks.end_price, decision)), sign)
        assumptions.append(f"end price supplied by {benchmarks.source!r}")

    paper = _mul(order.quantity, decision)
    total = None if opportunity is None else _add(_add(execution_cost, explicit), opportunity)
    if total is None:
        unavailable["total"] = "the opportunity cost is unknown"
    total_bps = None if total is None else _mul(_div(total, paper), _BPS)
    shares = (
        () if total is None else split_by_contribution(total, order.contributions, _SHARE_QUANTUM)
    )
    if total is not None and not order.contributions:
        unavailable["strategy_shares"] = "the order names no contributing strategy"

    return ImplementationShortfall(
        order_id=order.order_id,
        asset_id=order.asset_id,
        side=order.side,
        currency=execution.currency,
        ordered_quantity=order.quantity,
        filled_quantity=filled,
        unfilled_quantity=unfilled,
        decision_price=decision,
        arrival_price=benchmarks.arrival_price,
        end_price=benchmarks.end_price,
        average_fill_price=execution.average_fill_price,
        paper_notional=paper,
        execution_cost=execution_cost,
        delay_cost=delay,
        trading_cost=trading,
        explicit_costs=explicit,
        opportunity_cost=opportunity,
        total=total,
        total_bps=total_bps,
        strategy_shares=shares,
        execution_ids=tuple(fill.execution_id for fill in execution.fills),
        venues=execution.venues,
        assumptions=tuple(assumptions),
        unavailable=MappingProxyType(unavailable),
    )


# --------------------------------------------------------------------------- #
# Slippage
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SlippageMeasurement:
    """The average fill against one named reference. Positive is worse than the reference.

    Attributes:
        order_id: The order.
        reference: Which reference.
        reference_price: Its value, or ``None`` when not supplied.
        average_fill_price: ``None`` with no fill.
        filled_quantity: What the average is over.
        currency: What the amounts are in.
        per_unit: ``(average - reference) * s``.
        amount: ``per_unit * filled_quantity``.
        bps: ``per_unit / reference * 10,000``.
        reason: Why it is unavailable; empty when it is not.
    """

    order_id: str
    reference: ReferencePrice
    reference_price: Decimal | None
    average_fill_price: Decimal | None
    filled_quantity: Decimal
    currency: str
    per_unit: Decimal | None
    amount: Decimal | None
    bps: Decimal | None
    reason: str

    @property
    def available(self) -> bool:
        """Whether the measurement could be made."""

        return self.per_unit is not None


def _reference_value(execution: OrderExecution, reference: ReferencePrice) -> Decimal | None:
    if reference is ReferencePrice.DECISION:
        return execution.decision_price()[0]
    if reference is ReferencePrice.ARRIVAL:
        return execution.benchmarks.arrival_price
    return execution.benchmarks.interval_vwap


def measure_slippage(execution: OrderExecution, reference: ReferencePrice) -> SlippageMeasurement:
    """Measure the average fill against ``reference``. See the module docstring."""

    value = _reference_value(execution, reference)
    average = execution.average_fill_price
    filled = execution.filled_quantity
    if value is None or average is None:
        missing = "no fill" if average is None else f"no {reference} price was supplied"
        return SlippageMeasurement(
            execution.order.order_id,
            reference,
            value,
            average,
            filled,
            execution.currency,
            None,
            None,
            None,
            f"Slippage against {reference} is unavailable: {missing}.",
        )
    per_unit = _mul(_sub(average, value), _sign(execution.order.side))
    return SlippageMeasurement(
        execution.order.order_id,
        reference,
        value,
        average,
        filled,
        execution.currency,
        per_unit,
        _mul(per_unit, filled),
        _mul(_div(per_unit, value), _BPS),
        "",
    )


# --------------------------------------------------------------------------- #
# Fill quality
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FillQuality:
    """How completely, at what price quality, and in how many pieces an order filled.

    Attributes:
        order_id: The order.
        outcome: Its status when measured.
        ordered_quantity: What was ordered.
        filled_quantity: What filled.
        fill_ratio: ``filled / ordered`` -- completeness.
        fill_count: How many fills -- fragmentation.
        average_fill_price: ``None`` with no fill.
        limit_improvement_per_unit: How far inside its limit the average filled,
            ``(limit - average) * s``; ``None`` without a limit or a fill.
        effective_spread_bps: The quantity-weighted effective spread,
            ``2 * s * (p_i - m_i) / m_i`` against the midpoint ``m_i`` at each fill,
            in basis points; ``None`` unless every fill's midpoint was supplied.
        unavailable: Each figure not computed, and why.
    """

    order_id: str
    outcome: OrderStatus
    ordered_quantity: Decimal
    filled_quantity: Decimal
    fill_ratio: Decimal
    fill_count: int
    average_fill_price: Decimal | None
    limit_improvement_per_unit: Decimal | None
    effective_spread_bps: Decimal | None
    unavailable: Mapping[str, str]


def fill_quality(execution: OrderExecution) -> FillQuality:
    """Measure completeness, price quality and fragmentation. See the module docstring."""

    order = execution.order
    sign = _sign(order.side)
    filled = execution.filled_quantity
    average = execution.average_fill_price
    unavailable: dict[str, str] = {}

    improvement: Decimal | None = None
    if execution.limit_price is None:
        unavailable["limit_improvement_per_unit"] = "the order had no limit"
    elif average is None:
        unavailable["limit_improvement_per_unit"] = "nothing filled"
    else:
        improvement = _mul(_sub(execution.limit_price, average), sign)

    spread: Decimal | None = None
    midpoints = execution.fill_midpoints
    if not execution.fills:
        unavailable["effective_spread_bps"] = "nothing filled"
    elif midpoints is None or any(f.execution_id not in midpoints for f in execution.fills):
        unavailable["effective_spread_bps"] = "the quote midpoint at every fill was not supplied"
    else:
        weighted = _sum(
            _mul(
                fill.fill_quantity,
                _div(
                    _mul(
                        _mul(Decimal(2), sign), _sub(fill.fill_price, midpoints[fill.execution_id])
                    ),
                    midpoints[fill.execution_id],
                ),
            )
            for fill in execution.fills
        )
        spread = _mul(_div(weighted, filled), _BPS)

    return FillQuality(
        order_id=order.order_id,
        outcome=execution.outcome,
        ordered_quantity=order.quantity,
        filled_quantity=filled,
        fill_ratio=_div(filled, order.quantity),
        fill_count=len(execution.fills),
        average_fill_price=average,
        limit_improvement_per_unit=improvement,
        effective_spread_bps=spread,
        unavailable=MappingProxyType(unavailable),
    )


# --------------------------------------------------------------------------- #
# Latency
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LatencyMeasurement:
    """The time between two named instants of one order, in seconds.

    Attributes:
        order_id: The order.
        venue: Where it was sent.
        start: The mark measured from.
        end: The mark measured to.
        seconds: ``end - start``, or ``None`` when a mark is missing or the two
            are inconsistent.
        start_clock: Whose clock stamped the start; ``None`` when missing.
        end_clock: Whose clock stamped the end.
        mixed_clocks: Whether the two ends were stamped by different clocks, so
            the figure includes whatever offset lies between them.
        reason: Why ``seconds`` is ``None``; empty otherwise.
    """

    order_id: str
    venue: str
    start: LifecycleMark
    end: LifecycleMark
    seconds: Decimal | None
    start_clock: ClockSource | None
    end_clock: ClockSource | None
    mixed_clocks: bool
    reason: str


def measure_latency(
    timeline: OrderTimeline, start: LifecycleMark, end: LifecycleMark
) -> LatencyMeasurement:
    """The seconds from ``start`` to ``end`` on one order's timeline. See the module docstring."""

    first, last = timeline.mark(start), timeline.mark(end)
    clocks = (
        None if first is None else first.clock,
        None if last is None else last.clock,
    )
    mixed = first is not None and last is not None and first.clock is not last.clock
    if first is None or last is None:
        missing = [str(mark) for mark, found in ((start, first), (end, last)) if found is None]
        return LatencyMeasurement(
            timeline.order_id,
            timeline.venue,
            start,
            end,
            None,
            *clocks,
            mixed,
            f"Not recorded: {missing}.",
        )
    if last.at < first.at:
        return LatencyMeasurement(
            timeline.order_id,
            timeline.venue,
            start,
            end,
            None,
            *clocks,
            mixed,
            f"Inconsistent: {end} at {last.at!r} precedes {start} at {first.at!r}"
            + (", across two clocks" if mixed else "")
            + ". A negative latency is not reported as one.",
        )
    return LatencyMeasurement(
        timeline.order_id,
        timeline.venue,
        start,
        end,
        _sub(_instant(last.at), _instant(first.at)),
        *clocks,
        mixed,
        "",
    )


# --------------------------------------------------------------------------- #
# Rejection rate
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class OrderOutcome:
    """One submission and how the venue answered it.

    Attributes:
        order_id: The order.
        venue: Where it was submitted.
        submitted_at: When.
        status: Where it stands: ``NEW`` or ``PENDING`` while the venue has not
            answered, ``REJECTED`` if it refused, anything else if it accepted.
    """

    order_id: str
    venue: str
    submitted_at: float
    status: OrderStatus


@dataclass(frozen=True, slots=True)
class RejectionRate:
    """Venue rejections over resolved submissions, in a stated window.

    Attributes:
        window_start: Submissions from here, inclusive.
        window_end: To here, exclusive.
        venue: The one venue counted, or ``None`` for all.
        submitted: Submissions in scope.
        resolved: Those the venue answered.
        rejected: Those it refused.
        unresolved: Those still unanswered -- in neither count.
        rate: ``rejected / resolved``, or ``None`` when nothing was resolved.
    """

    window_start: float
    window_end: float
    venue: str | None
    submitted: int
    resolved: int
    rejected: int
    unresolved: int
    rate: Decimal | None


def _rate(
    scoped: Sequence[OrderOutcome], window_start: float, window_end: float, venue: str | None
) -> RejectionRate:
    unresolved = sum(1 for outcome in scoped if outcome.status in _UNRESOLVED)
    rejected = sum(1 for outcome in scoped if outcome.status is OrderStatus.REJECTED)
    resolved = len(scoped) - unresolved
    return RejectionRate(
        window_start=window_start,
        window_end=window_end,
        venue=venue,
        submitted=len(scoped),
        resolved=resolved,
        rejected=rejected,
        unresolved=unresolved,
        rate=None if resolved == 0 else _div(Decimal(rejected), Decimal(resolved)),
    )


def _in_window(
    outcomes: Sequence[OrderOutcome], window_start: float, window_end: float
) -> list[OrderOutcome]:
    if not window_end > window_start:
        raise ExecutionValidationError(f"A window from {window_start} to {window_end} is empty.")
    repeated = _repeated(outcome.order_id for outcome in outcomes)
    if repeated:
        raise ExecutionValidationError(f"Orders {repeated} are counted twice.")
    return [o for o in outcomes if window_start <= o.submitted_at < window_end]


def rejection_rate(
    outcomes: Sequence[OrderOutcome],
    *,
    window_start: float,
    window_end: float,
    venue: str | None,
) -> RejectionRate:
    """Rejections over resolved submissions. See the module docstring for the definition.

    Raises:
        ExecutionValidationError: If the window is empty or an order appears twice.
    """

    scoped = _in_window(outcomes, window_start, window_end)
    if venue is not None:
        scoped = [outcome for outcome in scoped if outcome.venue == venue]
    return _rate(scoped, window_start, window_end, venue)


# --------------------------------------------------------------------------- #
# Venue quality
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class VenueQuality:
    """What one venue achieved, measured from outcomes.

    Attributes:
        venue: The execution venue.
        currency: What its amounts are in, or ``None`` for a venue that filled
            nothing and so has no amounts. A venue whose fills settled in two
            currencies is reported once per currency.
        fills: How many fills it produced.
        filled_quantity: What it filled.
        slippage_reference: The reference slippage is measured against.
        slippage_bps: Quantity-weighted slippage of its fills, in basis points of
            each fill's order reference; ``None`` when no fill had a reference.
        unmeasured_fills: Fills whose order had no such reference.
        rejection_rate: Its rejections over resolved submissions.
        acknowledgement_seconds_median: Median submission-to-acknowledgement
            latency over its orders with both marks; ``None`` when none had them.
    """

    venue: str
    currency: str | None
    fills: int
    filled_quantity: Decimal
    slippage_reference: ReferencePrice
    slippage_bps: Decimal | None
    unmeasured_fills: int
    rejection_rate: RejectionRate
    acknowledgement_seconds_median: Decimal | None


def _median(values: Sequence[Decimal]) -> Decimal | None:
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return _div(_add(ordered[middle - 1], ordered[middle]), Decimal(2))


def venue_quality(
    executions: Sequence[OrderExecution],
    outcomes: Sequence[OrderOutcome],
    *,
    reference: ReferencePrice,
    window_start: float,
    window_end: float,
) -> tuple[VenueQuality, ...]:
    """Per-venue execution statistics, sorted by venue then currency.

    Fills are grouped by :attr:`~alphalab.execution.report.ExecutionReport.venue`
    -- where they executed -- so a split order contributes to each venue it
    touched. Slippage is each fill against its own order's ``reference``;
    rejections come from ``outcomes`` in the window; latency from each
    execution's timeline, attributed to the timeline's venue. One pass over the
    fills and one over the outcomes, grouped once -- never a rescan per venue.
    """

    weighted: dict[tuple[str, str], Decimal] = {}
    measured: dict[tuple[str, str], Decimal] = {}
    quantity: dict[tuple[str, str], Decimal] = {}
    counts: dict[tuple[str, str], int] = {}
    unmeasured: dict[tuple[str, str], int] = {}
    latencies: dict[str, list[Decimal]] = {}

    for execution in executions:
        value = _reference_value(execution, reference)
        sign = _sign(execution.order.side)
        for fill in execution.fills:
            key = (fill.venue, execution.currency)
            counts[key] = counts.get(key, 0) + 1
            quantity[key] = _add(quantity.get(key, _ZERO), fill.fill_quantity)
            if value is None:
                unmeasured[key] = unmeasured.get(key, 0) + 1
                continue
            bps = _mul(_div(_mul(_sub(fill.fill_price, value), sign), value), _BPS)
            weighted[key] = _add(weighted.get(key, _ZERO), _mul(bps, fill.fill_quantity))
            measured[key] = _add(measured.get(key, _ZERO), fill.fill_quantity)
        if execution.timeline is not None:
            latency = measure_latency(
                execution.timeline, LifecycleMark.SUBMISSION, LifecycleMark.ACKNOWLEDGEMENT
            )
            if latency.seconds is not None:
                latencies.setdefault(execution.timeline.venue, []).append(latency.seconds)

    by_venue: dict[str, list[OrderOutcome]] = {}
    for outcome in _in_window(outcomes, window_start, window_end):
        by_venue.setdefault(outcome.venue, []).append(outcome)
    filled_venues = {venue for venue, _ in counts}
    keys: list[tuple[str, str | None]] = sorted(counts)
    keys.extend((venue, None) for venue in sorted(set(by_venue) - filled_venues))
    keys.sort(key=lambda key: (key[0], key[1] or ""))
    results: list[VenueQuality] = []
    for venue, currency in keys:
        key = (venue, currency or "")
        filled_measured = measured.get(key, _ZERO)
        results.append(
            VenueQuality(
                venue=venue,
                currency=currency,
                fills=counts.get(key, 0),
                filled_quantity=quantity.get(key, _ZERO),
                slippage_reference=reference,
                slippage_bps=(
                    None
                    if filled_measured == _ZERO
                    else _div(weighted.get(key, _ZERO), filled_measured)
                ),
                unmeasured_fills=unmeasured.get(key, 0),
                rejection_rate=_rate(by_venue.get(venue, []), window_start, window_end, venue),
                acknowledgement_seconds_median=_median(latencies.get(venue, [])),
            )
        )
    return tuple(results)


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ExecutionQualityReport:
    """Every order's shortfall, totalled per currency and, through a converter, once.

    Attributes:
        shortfalls: One per order, in the order given.
        total_by_currency: Summed within each execution currency, never across.
        reporting_currency: The currency asked for, or ``None``.
        reporting_total: Everything converted and summed, or ``None`` when no
            converter was supplied or any order's total is unknown.
        conversions: Every conversion made, in order.
        conversion_at: The instant every conversion was made at.
        strategy_totals: Each strategy's share, per execution currency.
        incomplete_orders: Orders whose total could not be computed.
        assumptions: What the report rests on, beyond its evidence.
    """

    shortfalls: tuple[ImplementationShortfall, ...]
    total_by_currency: Mapping[str, Decimal]
    reporting_currency: str | None
    reporting_total: Decimal | None
    conversions: tuple[ConversionRecord, ...]
    conversion_at: float | None
    strategy_totals: Mapping[str, Mapping[str, Decimal]]
    incomplete_orders: tuple[str, ...]
    assumptions: tuple[str, ...]

    @property
    def report_id(self) -> str:
        """SHA-256 over every figure the report states."""

        return _digest(
            [
                EXECUTION_QUALITY_SCHEME,
                *(
                    f"order={s.order_id!r}|{s.currency!r}|{_value(s.execution_cost)}|"
                    f"{_value(s.explicit_costs)}|{_value(s.opportunity_cost)}|{_value(s.total)}|"
                    f"{','.join(s.execution_ids)}"
                    for s in self.shortfalls
                ),
                *(f"currency={c!r}|{_value(t)}" for c, t in sorted(self.total_by_currency.items())),
                f"reporting={self.reporting_currency!r}|{_value(self.reporting_total)}",
                f"conversion_at={self.conversion_at!r}",
                *(
                    f"conversion={_value(c.amount)}|{c.rate.base!r}|{c.rate.quote!r}|"
                    f"{_value(c.rate.rate)}|{c.rate.as_of!r}|{c.rate.source!r}|"
                    f"{_value(c.converted)}"
                    for c in self.conversions
                ),
                *(
                    f"strategy={currency!r}|{strategy!r}|{_value(amount)}"
                    for currency, shares in sorted(self.strategy_totals.items())
                    for strategy, amount in sorted(shares.items())
                ),
            ]
        )


def execution_quality_report(
    executions: Sequence[OrderExecution],
    *,
    reporting_currency: str | None,
    converter: CurrencyConverter | None,
    conversion_at: float | None,
) -> ExecutionQualityReport:
    """Measure every order's shortfall and total them, per currency and in one.

    A reporting-currency total needs all three of ``reporting_currency``,
    ``converter`` and ``conversion_at``, and every order's total: each order's
    total is converted once, at ``conversion_at``, by the converter, which
    refuses -- raises -- a rate it does not have rather than inventing one.

    Raises:
        ExecutionValidationError: If an order appears twice, or a reporting
            currency is asked for without a converter or an instant.
    """

    repeated = _repeated(execution.order.order_id for execution in executions)
    if repeated:
        raise ExecutionValidationError(f"Orders {repeated} are measured twice.")
    if reporting_currency is not None and (converter is None or conversion_at is None):
        raise ExecutionValidationError(
            f"A total in {reporting_currency} needs a converter and the instant to convert at; "
            "without them there is no rate, and none is invented."
        )

    shortfalls = tuple(implementation_shortfall(execution) for execution in executions)
    by_currency: dict[str, Decimal] = {}
    strategies: dict[str, dict[str, Decimal]] = {}
    incomplete: list[str] = []
    for shortfall in shortfalls:
        if shortfall.total is None:
            incomplete.append(shortfall.order_id)
            continue
        by_currency[shortfall.currency] = _add(
            by_currency.get(shortfall.currency, _ZERO), shortfall.total
        )
        bucket = strategies.setdefault(shortfall.currency, {})
        for strategy, share in shortfall.strategy_shares:
            bucket[strategy] = _add(bucket.get(strategy, _ZERO), share)

    conversions: list[ConversionRecord] = []
    reporting_total: Decimal | None = None
    assumptions: list[str] = []
    if reporting_currency is not None and not incomplete:
        assert converter is not None and conversion_at is not None
        reporting_total = _ZERO
        for shortfall in shortfalls:
            assert shortfall.total is not None
            record = converter.convert(
                shortfall.total, shortfall.currency, reporting_currency, conversion_at
            )
            conversions.append(record)
            reporting_total = _add(reporting_total, record.converted)
        assumptions.append(
            f"every total converted into {reporting_currency} at {conversion_at!r} by the "
            "supplied converter; each conversion is recorded"
        )
    elif reporting_currency is not None:
        assumptions.append(
            f"no {reporting_currency} total: orders {incomplete} have no total to convert"
        )

    return ExecutionQualityReport(
        shortfalls=shortfalls,
        total_by_currency=MappingProxyType(dict(sorted(by_currency.items()))),
        reporting_currency=reporting_currency,
        reporting_total=reporting_total,
        conversions=tuple(conversions),
        conversion_at=conversion_at,
        strategy_totals=MappingProxyType(
            {
                currency: MappingProxyType(dict(sorted(bucket.items())))
                for currency, bucket in sorted(strategies.items())
            }
        ),
        incomplete_orders=tuple(incomplete),
        assumptions=tuple(assumptions),
    )
