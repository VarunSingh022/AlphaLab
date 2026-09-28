"""Smart routing: choosing where an order should execute, from evidence supplied about each venue.

Three things are called "routing" and only the first is decided here:

======================= ===========================================================
**Route selection**     Which venue, or which venues in what quantities, given
                        what each venue quotes, can do and costs.
                        :func:`select_route`. AlphaLab's.
**Route execution**     Turning the selection into instructions -- one
                        :class:`RouteLeg` per venue, each a quantity at an expected
                        price. AlphaLab's, and it is the decision's ``legs``.
**Venue connectivity**  Sending an instruction to a venue. An adapter's, reached
                        through :mod:`alphalab.broker`; nothing here opens a
                        connection, knows a venue's protocol or names a vendor.
======================= ===========================================================

Where it is technically and legally applicable
----------------------------------------------

Whether an order may be routed away from a particular venue at all -- by market
rules, by a best-execution obligation, by the instrument being listed in one
place -- is not something this module knows or checks. It compares the venues a
caller *supplies* as candidates, for an instrument the caller says they can all
trade. Routing across venues is meaningful for some asset classes and some
jurisdictions and not others; supplying one venue is always valid, and is the
right answer wherever there is only one.

Everything a decision reads is supplied
---------------------------------------

A decision is a function of its arguments. There is no venue discovery, no
network state, no clock -- ``decided_at`` is supplied and is what quote age and
look-ahead are judged against -- and no configuration outside
:class:`RoutingPolicy`. Each venue is described by:

* a :class:`VenueQuote` -- what it showed, when, in what currency, from where;
* a :class:`VenueProfile` -- its :class:`~alphalab.core.capabilities.CapabilityDeclaration`,
  its :class:`~alphalab.execution.costs.ExecutionCostModel` (the v3.3 authority,
  reused rather than restated: fees, commission, spread, impact) and its expected
  latency, or ``None`` when nobody knows it.

Pricing follows the convention the execution simulator has always used: the
reference price is the venue's quote midpoint and the cost model's spread role
adds the half-spread a marketable order pays, so a venue's expected fill price is
what its own cost model says, and the spread is counted once.

How candidates are judged and ranked
------------------------------------

Every venue becomes a :class:`RouteCandidate` with a status and a reason:
``EXCLUDED`` by the policy; ``INSUFFICIENT_EVIDENCE`` when a quote, a side of it,
its size, a fresh enough timestamp, a latency the policy needs, or a cost the
cost model could compute is missing; ``INELIGIBLE`` when it is known that the
venue cannot take the order -- a capability it lacks, a quote in another
currency, a price outside the limit, no displayed size, latency over the cap;
``ELIGIBLE`` otherwise. Nothing ineligible or unevidenced is ever selected, and
no missing figure is filled in.

Eligible candidates are ranked by the policy's objective -- the quoted touch, or
the **all-in** price per unit (the expected fill price with every cash cost
spread over the quantity; a cost for a buy, net proceeds for a sale) -- then by
lower latency with unknown latency last, then by venue identifier. The tie-break
order is part of the policy and of its identity, so two runs cannot break a tie
two ways.

Liquidity selection
-------------------

A single-venue route takes the best-ranked venue that can fill the whole order.
A split route (``allow_split``) sweeps the ranking, taking each venue's
executable quantity until the order is filled, and re-prices each leg at the
quantity it actually takes. The sweep is greedy by each venue's all-in price at
its full displayed size: optimal when costs are linear in quantity, and not
claimed optimal under per-trade fees or non-linear impact, where splitting can
cost more than the ranking suggests -- the legs' own prices show what it costs.
If the eligible liquidity cannot fill the order, the route is ``PARTIAL`` when
the policy allows it and not otherwise; if nothing can be selected, the decision
is ``INFEASIBLE`` when every venue was positively ruled out and
``INSUFFICIENT_EVIDENCE`` when any venue simply could not be assessed -- a route
that might exist is not reported as one that does not.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal
from enum import StrEnum, unique
from typing import Final

from alphalab.common.arithmetic import canonical_text
from alphalab.core.capabilities import (
    CapabilityDeclaration,
    CompatibilityReport,
    ExecutionRequirements,
    check_compatibility,
)
from alphalab.core.enums import Side
from alphalab.execution.costs import CostContext, ExecutionCostModel, ExecutionCosts
from alphalab.execution.exceptions import ExecutionValidationError

__all__ = [
    "ROUTE_DECISION_SCHEME",
    "ROUTING_POLICY_SCHEME",
    "CandidateStatus",
    "RouteCandidate",
    "RouteDecision",
    "RouteLeg",
    "RouteRequest",
    "RouteStatus",
    "RoutingObjective",
    "RoutingPolicy",
    "VenueProfile",
    "VenueQuote",
    "select_route",
]

#: Scheme tag of a routing policy's identity. Version 2 (v3.11, ledger DET-006)
#: renders every ``Decimal`` by value, so ``0.5`` and ``0.50`` state one policy.
ROUTING_POLICY_SCHEME: Final = "alphalab.routing_policy.v2"

#: Scheme tag of a route decision's identity; version 2 as the policy's.
ROUTE_DECISION_SCHEME: Final = "alphalab.route_decision.v2"

#: Fixed arithmetic, never the caller's thread context.
_CONTEXT: Final = Context(prec=34, rounding=ROUND_HALF_EVEN)

_ZERO = Decimal("0")
_TWO = Decimal("2")

#: The tie-break order, stated once, rendered into every policy's identity.
_TIE_BREAK: Final = ("objective_price", "latency_unknown_last", "venue_identifier")


def _digest(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise ExecutionValidationError(f"{field} cannot be empty.")


def _value(amount: Decimal | None) -> str:
    """An amount in an identity: by value (:func:`canonical_text`), or ``None``."""

    return "None" if amount is None else canonical_text(amount)


@dataclass(frozen=True, slots=True)
class VenueQuote:
    """What one venue showed for one instrument, at one instant. Supplied, never fetched.

    Attributes:
        venue: The *execution* venue -- where an order would trade -- in the
            sense ``test_venue_concepts_stay_distinct.py`` keeps apart from a
            listing exchange and from market-data attribution.
        asset_id: The instrument.
        bid: Best bid, or ``None`` when the venue showed none.
        ask: Best ask, or ``None``.
        bid_size: Quantity at the bid, or ``None`` when not shown.
        ask_size: Quantity at the ask, or ``None``.
        currency: What the prices are quoted in.
        as_of: When the venue showed it, in Unix seconds.
        source: Where the quote came from. Required: an unattributed quote is
            evidence nobody can check.
    """

    venue: str
    asset_id: str
    bid: Decimal | None
    ask: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    currency: str
    as_of: float
    source: str

    def __post_init__(self) -> None:
        for name in ("venue", "asset_id", "currency", "source"):
            _require_text(getattr(self, name), f"VenueQuote.{name}")
        if not math.isfinite(self.as_of):
            raise ExecutionValidationError(f"A quote as of {self.as_of!r} has no instant.")
        for name in ("bid", "ask"):
            value: Decimal | None = getattr(self, name)
            if value is not None and value <= _ZERO:
                raise ExecutionValidationError(f"A {name} of {value} is not a price.")
        for name in ("bid_size", "ask_size"):
            size: Decimal | None = getattr(self, name)
            if size is not None and size < _ZERO:
                raise ExecutionValidationError(f"A {name} of {size} is not a size.")
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ExecutionValidationError(
                f"{self.venue} quotes {self.asset_id} crossed, bid {self.bid} over ask {self.ask}."
            )

    def _render(self) -> str:
        return (
            f"quote={self.venue!r}|{self.asset_id!r}|{_value(self.bid)}|{_value(self.ask)}"
            f"|{_value(self.bid_size)}|{_value(self.ask_size)}|{self.currency!r}|{self.as_of!r}"
            f"|{self.source!r}"
        )


@dataclass(frozen=True, slots=True)
class VenueProfile:
    """What a venue can do, what executing there costs, and how long it takes.

    Attributes:
        venue: The execution venue, matching :attr:`VenueQuote.venue`.
        capabilities: What the connection to it declares it can do.
        costs: The v3.3 cost model for executing there.
        latency_seconds: Expected order-to-venue latency, supplied; ``None`` when
            nobody knows it, which is not the same as zero.
    """

    venue: str
    capabilities: CapabilityDeclaration
    costs: ExecutionCostModel
    latency_seconds: Decimal | None

    def __post_init__(self) -> None:
        _require_text(self.venue, "VenueProfile.venue")
        if self.latency_seconds is not None and self.latency_seconds < _ZERO:
            raise ExecutionValidationError(f"A latency of {self.latency_seconds}s is not a delay.")


@unique
class RoutingObjective(StrEnum):
    """What a route is chosen to minimise."""

    #: The quoted touch: lowest ask to buy, highest bid to sell. Costs are
    #: computed and reported, and do not decide.
    BEST_QUOTED_PRICE = "best_quoted_price"

    #: The expected fill price with every cash cost spread over the quantity:
    #: the lowest cost to buy, the highest net proceeds to sell.
    LOWEST_ALL_IN_COST = "lowest_all_in_cost"


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    """Everything that decides a route and is not evidence about a venue.

    Attributes:
        objective: What the ranking minimises.
        max_quote_age_seconds: How old a quote may be at ``decided_at``. No
            default: a second is stale for one strategy and fresh for another.
        max_latency_seconds: A venue slower than this is ineligible, and one
            whose latency is unknown cannot be shown to meet it. ``None`` sets
            no cap.
        allow_split: Whether the order may be divided across venues.
        allow_partial: Whether a route may cover less than the order when the
            eligible liquidity is short.
        excluded_venues: Venues never to be selected.
    """

    objective: RoutingObjective
    max_quote_age_seconds: float
    max_latency_seconds: Decimal | None
    allow_split: bool
    allow_partial: bool
    excluded_venues: frozenset[str]

    def __post_init__(self) -> None:
        if not self.max_quote_age_seconds >= 0:
            raise ExecutionValidationError(
                f"max_quote_age_seconds={self.max_quote_age_seconds!r}; a negative budget "
                "makes every quote stale."
            )
        if self.max_latency_seconds is not None and self.max_latency_seconds < _ZERO:
            raise ExecutionValidationError(
                f"A latency cap of {self.max_latency_seconds} is negative."
            )
        object.__setattr__(self, "excluded_venues", frozenset(self.excluded_venues))

    @property
    def tie_break(self) -> tuple[str, ...]:
        """The ranking's order of keys, fixed for every policy."""

        return _TIE_BREAK

    @property
    def policy_id(self) -> str:
        """SHA-256 over every field and the tie-break order."""

        return _digest(
            [
                ROUTING_POLICY_SCHEME,
                f"objective={self.objective}",
                f"max_quote_age_seconds={self.max_quote_age_seconds!r}",
                f"max_latency_seconds={_value(self.max_latency_seconds)}",
                f"allow_split={self.allow_split}",
                f"allow_partial={self.allow_partial}",
                f"excluded={','.join(sorted(repr(v) for v in self.excluded_venues))}",
                f"tie_break={','.join(_TIE_BREAK)}",
            ]
        )


@dataclass(frozen=True, slots=True)
class RouteRequest:
    """One order to route.

    Attributes:
        order_id: The order this decision is for -- provenance, not identity.
        asset_id: The instrument.
        side: Buy or sell.
        quantity: How much.
        limit_price: No venue whose touch is worse than this is eligible;
            ``None`` for a marketable order.
        currency: The currency prices are compared in. A venue quoting another
            is ineligible: nothing here converts, because a route chosen through
            an exchange rate is a currency decision, not a routing one.
        requirements: What the order needs, checked against every venue's
            declaration.
        quantity_increment: Executable quantities are floored to it.
        decided_at: The instant the decision is made at. Quote age and
            look-ahead are judged against it; nothing reads a clock.
    """

    order_id: str
    asset_id: str
    side: Side
    quantity: Decimal
    limit_price: Decimal | None
    currency: str
    requirements: ExecutionRequirements
    quantity_increment: Decimal
    decided_at: float

    def __post_init__(self) -> None:
        for name in ("order_id", "asset_id", "currency"):
            _require_text(getattr(self, name), f"RouteRequest.{name}")
        if self.quantity <= _ZERO:
            raise ExecutionValidationError(f"A route for {self.quantity} routes nothing.")
        if self.quantity_increment <= _ZERO:
            raise ExecutionValidationError(
                f"An increment of {self.quantity_increment} is not a unit."
            )
        if self.limit_price is not None and self.limit_price <= _ZERO:
            raise ExecutionValidationError(f"A limit of {self.limit_price} is not a price.")
        if not math.isfinite(self.decided_at):
            raise ExecutionValidationError(f"decided_at={self.decided_at!r} is not an instant.")

    def _render(self) -> str:
        return (
            f"request={self.order_id!r}|{self.asset_id!r}|{self.side}|{_value(self.quantity)}"
            f"|{_value(self.limit_price)}|{self.currency!r}|{self.requirements.requirements_id}"
            f"|{_value(self.quantity_increment)}|{self.decided_at!r}"
        )


@unique
class CandidateStatus(StrEnum):
    """How one venue stood when the route was chosen."""

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    EXCLUDED = "excluded"


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    """One venue, judged. Every figure is ``None`` when the evidence for it was missing.

    Attributes:
        venue: The execution venue.
        status: How it stood.
        reason: Why, in one sentence.
        quoted_price: The touch the order would take: ask to buy, bid to sell.
        displayed_quantity: What the venue showed at that touch.
        executable_quantity: What of the order it could take, floored to the
            increment. Zero unless eligible.
        expected_price: The fill price its cost model expects at that quantity.
        all_in_price: ``expected_price`` with the cash costs per unit included.
        expected_costs: The itemized costs, from the venue's own model.
        latency_seconds: Its declared latency.
        compatibility: The capability check, when one was made.
        rank: Its place among eligible venues, from 1.
    """

    venue: str
    status: CandidateStatus
    reason: str
    quoted_price: Decimal | None
    displayed_quantity: Decimal | None
    executable_quantity: Decimal
    expected_price: Decimal | None
    all_in_price: Decimal | None
    expected_costs: ExecutionCosts | None
    latency_seconds: Decimal | None
    compatibility: CompatibilityReport | None
    rank: int | None


@dataclass(frozen=True, slots=True)
class RouteLeg:
    """One instruction of a route: this quantity, at this venue, expected at this price.

    Attributes:
        venue: Where.
        quantity: How much.
        quoted_price: The touch it takes.
        expected_price: Its cost model's fill price at this quantity.
        expected_costs: Itemized, at this quantity.
        all_in_price: Per unit, cash costs included.
    """

    venue: str
    quantity: Decimal
    quoted_price: Decimal
    expected_price: Decimal
    expected_costs: ExecutionCosts
    all_in_price: Decimal


@unique
class RouteStatus(StrEnum):
    """What the decision was able to do."""

    #: The whole order is routed.
    ROUTED = "routed"

    #: Part of it is routed; the policy allowed a short route.
    PARTIAL = "partial"

    #: Every venue was positively ruled out, or the liquidity could not cover
    #: the order and the policy did not allow a partial route.
    INFEASIBLE = "infeasible"

    #: No route was chosen and at least one venue could not be assessed. A
    #: route may exist; the evidence to find it was not supplied.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """A route, every venue considered, and why.

    Attributes:
        request: What was routed.
        policy: How.
        status: What could be done.
        legs: The instructions, in ranked order.
        candidates: Every venue considered, by venue identifier.
        reason: The decision in one sentence.
        evidence_id: SHA-256 over every quote supplied.
    """

    request: RouteRequest
    policy: RoutingPolicy
    status: RouteStatus
    legs: tuple[RouteLeg, ...]
    candidates: tuple[RouteCandidate, ...]
    reason: str
    evidence_id: str

    @property
    def routed_quantity(self) -> Decimal:
        """What the legs cover."""

        total = _ZERO
        for leg in self.legs:
            total = _CONTEXT.add(total, leg.quantity)
        return total

    @property
    def unrouted_quantity(self) -> Decimal:
        """What no leg covers."""

        return _CONTEXT.subtract(self.request.quantity, self.routed_quantity)

    @property
    def decision_id(self) -> str:
        """SHA-256 over request, policy, evidence and outcome: reproducible from its inputs."""

        return _digest(
            [
                ROUTE_DECISION_SCHEME,
                self.request._render(),
                f"policy={self.policy.policy_id}",
                f"evidence={self.evidence_id}",
                f"status={self.status}",
                *(
                    f"candidate={c.venue!r}|{c.status}|{_value(c.executable_quantity)}"
                    f"|{_value(c.quoted_price)}|{_value(c.expected_price)}"
                    f"|{_value(c.all_in_price)}|{c.rank}"
                    for c in self.candidates
                ),
                *(
                    f"leg={leg.venue!r}|{_value(leg.quantity)}|{_value(leg.expected_price)}"
                    f"|{_value(leg.all_in_price)}"
                    for leg in self.legs
                ),
            ]
        )

    def explanation(self) -> tuple[str, ...]:
        """The decision in words: the verdict, then every venue and where it stood."""

        lines = [f"{self.status}: {self.reason}"]
        for candidate in sorted(
            self.candidates, key=lambda c: (c.rank is None, c.rank or 0, c.venue)
        ):
            position = f"#{candidate.rank}" if candidate.rank is not None else "--"
            lines.append(f"{position} {candidate.venue}: {candidate.status} -- {candidate.reason}")
        return tuple(lines)


def _floor_to(quantity: Decimal, increment: Decimal) -> Decimal:
    units = _CONTEXT.divide(quantity, increment).to_integral_value(rounding=ROUND_FLOOR)
    return _CONTEXT.multiply(units, increment)


def _all_in(side: Side, fill_price: Decimal, costs: ExecutionCosts, quantity: Decimal) -> Decimal:
    """Per-unit price with cash costs included: paid to buy, received to sell."""

    per_unit_cash = _CONTEXT.divide(costs.cash_charged, quantity)
    if side is Side.BUY:
        return _CONTEXT.add(fill_price, per_unit_cash)
    return _CONTEXT.subtract(fill_price, per_unit_cash)


def _price_leg(
    profile: VenueProfile, quote: VenueQuote, request: RouteRequest, quantity: Decimal
) -> tuple[Decimal, ExecutionCosts, Decimal]:
    """The expected fill price, costs and all-in price for ``quantity`` at one venue.

    Raises whatever the venue's cost model raises when it lacks what it needs.
    """

    assert quote.bid is not None and quote.ask is not None
    mid = _CONTEXT.divide(_CONTEXT.add(quote.bid, quote.ask), _TWO)
    shown = quote.ask_size if request.side is Side.BUY else quote.bid_size
    context = CostContext(
        asset_id=request.asset_id,
        side=request.side,
        quantity=quantity,
        reference_price=mid,
        currency=request.currency,
        venue=profile.venue,
        timestamp=request.decided_at,
        bid=quote.bid,
        ask=quote.ask,
        available_liquidity=shown,
    )
    costs = profile.costs.quote(context)
    fill_price = profile.costs.fill_price(context, costs)
    return fill_price, costs, _all_in(request.side, fill_price, costs, quantity)


def _candidate(
    venue: str,
    status: CandidateStatus,
    reason: str,
    *,
    quoted: Decimal | None = None,
    shown: Decimal | None = None,
    latency: Decimal | None = None,
    compatibility: CompatibilityReport | None = None,
) -> RouteCandidate:
    return RouteCandidate(
        venue=venue,
        status=status,
        reason=reason,
        quoted_price=quoted,
        displayed_quantity=shown,
        executable_quantity=_ZERO,
        expected_price=None,
        all_in_price=None,
        expected_costs=None,
        latency_seconds=latency,
        compatibility=compatibility,
        rank=None,
    )


def _judge(
    venue: str,
    quote: VenueQuote | None,
    profile: VenueProfile | None,
    request: RouteRequest,
    policy: RoutingPolicy,
) -> RouteCandidate:
    INSUFFICIENT, INELIGIBLE = CandidateStatus.INSUFFICIENT_EVIDENCE, CandidateStatus.INELIGIBLE
    if venue in policy.excluded_venues:
        return _candidate(venue, CandidateStatus.EXCLUDED, "The policy excludes this venue.")
    if profile is None:
        return _candidate(
            venue,
            INSUFFICIENT,
            "No profile was supplied: its capabilities, costs and latency are unknown.",
        )
    latency = profile.latency_seconds
    if quote is None:
        return _candidate(
            venue, INSUFFICIENT, "No quote was supplied for this venue.", latency=latency
        )
    if quote.currency != request.currency:
        return _candidate(
            venue,
            INELIGIBLE,
            f"It quotes in {quote.currency} and the order is compared in {request.currency}; "
            "nothing here converts.",
            latency=latency,
        )
    if quote.as_of > request.decided_at:
        return _candidate(
            venue,
            INSUFFICIENT,
            f"Its quote is dated {quote.as_of!r}, after the decision at {request.decided_at!r}: "
            "a look-ahead, not evidence.",
            latency=latency,
        )
    if request.decided_at - quote.as_of > policy.max_quote_age_seconds:
        return _candidate(
            venue,
            INSUFFICIENT,
            f"Its quote is {request.decided_at - quote.as_of!r}s old, beyond the "
            f"{policy.max_quote_age_seconds!r}s the policy accepts.",
            latency=latency,
        )
    quoted = quote.ask if request.side is Side.BUY else quote.bid
    shown = quote.ask_size if request.side is Side.BUY else quote.bid_size
    if quote.bid is None or quote.ask is None or quoted is None:
        return _candidate(
            venue,
            INSUFFICIENT,
            "Its quote lacks a side, so neither the touch nor the midpoint the costs are "
            "priced from is known.",
            latency=latency,
        )
    if shown is None:
        return _candidate(
            venue,
            INSUFFICIENT,
            "Its quote shows no size at the touch, so how much it could take is unknown.",
            quoted=quoted,
            latency=latency,
        )

    compatibility = check_compatibility(request.requirements, profile.capabilities)
    if not compatibility.compatible:
        status = INELIGIBLE if compatibility.unsupported else INSUFFICIENT
        return _candidate(
            venue,
            status,
            f"Its declared capabilities are {compatibility.status}: "
            f"{'; '.join(compatibility.gaps)}",
            quoted=quoted,
            shown=shown,
            latency=latency,
            compatibility=compatibility,
        )
    if policy.max_latency_seconds is not None:
        if latency is None:
            return _candidate(
                venue,
                INSUFFICIENT,
                "The policy caps latency and this venue's is unknown, so it cannot be shown "
                "to meet the cap.",
                quoted=quoted,
                shown=shown,
                compatibility=compatibility,
            )
        if latency > policy.max_latency_seconds:
            return _candidate(
                venue,
                INELIGIBLE,
                f"Its latency of {latency}s exceeds the {policy.max_latency_seconds}s cap.",
                quoted=quoted,
                shown=shown,
                latency=latency,
                compatibility=compatibility,
            )
    limit = request.limit_price
    if limit is not None and (
        (request.side is Side.BUY and quoted > limit)
        or (request.side is Side.SELL and quoted < limit)
    ):
        return _candidate(
            venue,
            INELIGIBLE,
            f"Its touch of {quoted} is outside the limit of {limit}.",
            quoted=quoted,
            shown=shown,
            latency=latency,
            compatibility=compatibility,
        )
    executable = _floor_to(min(request.quantity, shown), request.quantity_increment)
    if executable <= _ZERO:
        return _candidate(
            venue,
            INELIGIBLE,
            f"It shows {shown} at the touch, less than one increment of "
            f"{request.quantity_increment}.",
            quoted=quoted,
            shown=shown,
            latency=latency,
            compatibility=compatibility,
        )
    try:
        expected, costs, all_in = _price_leg(profile, quote, request, executable)
    except ExecutionValidationError as refusal:
        return _candidate(
            venue,
            INSUFFICIENT,
            f"Its cost model could not price the order: {refusal}",
            quoted=quoted,
            shown=shown,
            latency=latency,
            compatibility=compatibility,
        )
    return RouteCandidate(
        venue=venue,
        status=CandidateStatus.ELIGIBLE,
        reason=f"It can take {executable} at an all-in {all_in} per unit.",
        quoted_price=quoted,
        displayed_quantity=shown,
        executable_quantity=executable,
        expected_price=expected,
        all_in_price=all_in,
        expected_costs=costs,
        latency_seconds=latency,
        compatibility=compatibility,
        rank=None,
    )


def _ranking_key(
    candidate: RouteCandidate, side: Side, objective: RoutingObjective
) -> tuple[Decimal, int, Decimal, str]:
    price = (
        candidate.quoted_price
        if objective is RoutingObjective.BEST_QUOTED_PRICE
        else candidate.all_in_price
    )
    assert price is not None
    # Lower is better: a buyer wants the lowest price, a seller the highest.
    directed = price if side is Side.BUY else -price
    unknown = candidate.latency_seconds is None
    return (directed, 1 if unknown else 0, candidate.latency_seconds or _ZERO, candidate.venue)


def select_route(
    request: RouteRequest,
    quotes: Sequence[VenueQuote],
    profiles: Sequence[VenueProfile],
    policy: RoutingPolicy,
) -> RouteDecision:
    """Choose a route from the supplied evidence, and say why.

    Every venue named by a quote or a profile is judged; see the module
    docstring for how. Pure: the same arguments always produce an equal decision
    with the same :attr:`~RouteDecision.decision_id`, whatever order the quotes
    and profiles were listed in.

    Raises:
        ExecutionValidationError: If a quote is for another instrument, or two
            quotes or two profiles describe one venue -- one venue, one piece of
            evidence, or a choice between them would be a guess.
    """

    for quote in quotes:
        if quote.asset_id != request.asset_id:
            raise ExecutionValidationError(
                f"A quote from {quote.venue} is for {quote.asset_id}, not {request.asset_id}."
            )
    by_quote = _unique(((q.venue, q) for q in quotes), "quote")
    by_profile = _unique(((p.venue, p) for p in profiles), "profile")
    venues = sorted({*by_quote, *by_profile})
    judged = [
        _judge(venue, by_quote.get(venue), by_profile.get(venue), request, policy)
        for venue in venues
    ]
    eligible = sorted(
        (c for c in judged if c.status is CandidateStatus.ELIGIBLE),
        key=lambda c: _ranking_key(c, request.side, policy.objective),
    )
    ranks = {candidate.venue: position for position, candidate in enumerate(eligible, start=1)}
    candidates = tuple(_with_rank(candidate, ranks.get(candidate.venue)) for candidate in judged)
    ranked = [_with_rank(c, ranks[c.venue]) for c in eligible]
    unassessed = any(c.status is CandidateStatus.INSUFFICIENT_EVIDENCE for c in judged)
    evidence_id = _digest(["evidence", *sorted(q._render() for q in quotes)])

    status, legs, reason = _choose(request, policy, ranked, by_quote, by_profile, unassessed)
    return RouteDecision(
        request=request,
        policy=policy,
        status=status,
        legs=legs,
        candidates=candidates,
        reason=reason,
        evidence_id=evidence_id,
    )


def _unique[T](pairs: Iterable[tuple[str, T]], noun: str) -> Mapping[str, T]:
    found: dict[str, T] = {}
    for venue, value in pairs:
        if venue in found:
            raise ExecutionValidationError(
                f"Two {noun}s describe venue {venue!r}; one venue, one {noun}."
            )
        found[venue] = value
    return found


def _with_rank(candidate: RouteCandidate, rank: int | None) -> RouteCandidate:
    return RouteCandidate(
        venue=candidate.venue,
        status=candidate.status,
        reason=candidate.reason,
        quoted_price=candidate.quoted_price,
        displayed_quantity=candidate.displayed_quantity,
        executable_quantity=candidate.executable_quantity,
        expected_price=candidate.expected_price,
        all_in_price=candidate.all_in_price,
        expected_costs=candidate.expected_costs,
        latency_seconds=candidate.latency_seconds,
        compatibility=candidate.compatibility,
        rank=rank,
    )


def _leg(
    candidate: RouteCandidate,
    quantity: Decimal,
    request: RouteRequest,
    quote: VenueQuote,
    profile: VenueProfile,
) -> RouteLeg:
    if quantity == candidate.executable_quantity:
        assert candidate.expected_price is not None and candidate.all_in_price is not None
        assert candidate.expected_costs is not None and candidate.quoted_price is not None
        return RouteLeg(
            candidate.venue,
            quantity,
            candidate.quoted_price,
            candidate.expected_price,
            candidate.expected_costs,
            candidate.all_in_price,
        )
    expected, costs, all_in = _price_leg(profile, quote, request, quantity)
    assert candidate.quoted_price is not None
    return RouteLeg(candidate.venue, quantity, candidate.quoted_price, expected, costs, all_in)


def _choose(
    request: RouteRequest,
    policy: RoutingPolicy,
    ranked: list[RouteCandidate],
    quotes: Mapping[str, VenueQuote],
    profiles: Mapping[str, VenueProfile],
    unassessed: bool,
) -> tuple[RouteStatus, tuple[RouteLeg, ...], str]:
    nothing = RouteStatus.INSUFFICIENT_EVIDENCE if unassessed else RouteStatus.INFEASIBLE
    if not ranked:
        return (
            nothing,
            (),
            "No venue is eligible"
            + ("; at least one could not be assessed." if unassessed else "."),
        )

    def leg(candidate: RouteCandidate, quantity: Decimal) -> RouteLeg:
        return _leg(
            candidate, quantity, request, quotes[candidate.venue], profiles[candidate.venue]
        )

    if not policy.allow_split:
        whole = [c for c in ranked if c.executable_quantity == request.quantity]
        if whole:
            best = whole[0]
            return (
                RouteStatus.ROUTED,
                (leg(best, request.quantity),),
                f"{best.venue} is the best-ranked venue that can take the whole order.",
            )
        if policy.allow_partial:
            best = ranked[0]
            return (
                RouteStatus.PARTIAL,
                (leg(best, best.executable_quantity),),
                f"No venue can take the whole order; {best.venue}, ranked first, takes "
                f"{best.executable_quantity}.",
            )
        return (
            nothing,
            (),
            "No single venue can take the whole order, and the policy allows neither a "
            "split nor a partial route.",
        )

    legs: list[RouteLeg] = []
    left = request.quantity
    for candidate in ranked:
        if left <= _ZERO:
            break
        take = min(candidate.executable_quantity, left)
        legs.append(leg(candidate, take))
        left = _CONTEXT.subtract(left, take)
    if left <= _ZERO:
        return (
            RouteStatus.ROUTED,
            tuple(legs),
            f"Swept across {len(legs)} venue(s) in ranked order.",
        )
    if policy.allow_partial:
        return (
            RouteStatus.PARTIAL,
            tuple(legs),
            f"The eligible venues show {request.quantity - left} of {request.quantity}; the "
            "policy allows a partial route.",
        )
    return (
        nothing,
        (),
        f"The eligible venues show {request.quantity - left} of {request.quantity}, and the "
        "policy does not allow a partial route.",
    )
