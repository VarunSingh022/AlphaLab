"""
AlphaLab Examples
=================

Example 64 : Smart Routing from Local Venue Evidence

Difficulty : Advanced

Estimated Time : 12 minutes

Prerequisites
-------------

✓ Example 25 (execution simulation and cost models)
✓ Example 61 (broker capabilities)

Topics
------

• A route chosen from evidence the caller supplies about each venue: a quote,
  a capability declaration, a cost model and a latency -- nothing discovered,
  nothing fetched, no clock read
• Every venue judged and explained: ELIGIBLE, INELIGIBLE, INSUFFICIENT_EVIDENCE
  or EXCLUDED, each with the reason
• Two objectives -- the quoted touch, or the all-in price with every cash cost
  -- and a tie-break order that is part of the policy's identity
• A single-venue route, a split swept across the ranking and re-priced at the
  quantities actually taken, and a partial route only where the policy allows
• Stale quotes, look-ahead quotes, a quote in another currency, a latency cap,
  a limit the touch is outside, and a capability nobody declared
• INFEASIBLE kept apart from INSUFFICIENT_EVIDENCE: a route that might exist
  is never reported as one that does not
• Decision identities that ignore listing order, and the policy in a
  strategy's research record

What this shows
---------------

"Smart routing" is three things, and only one of them is decided here: which
venue, or which venues in what quantities, given what each venue quoted, can do
and costs. Turning that decision into child orders is the runtime's, and
sending them is an adapter's. The decision is a function of its arguments:
listing the venues in another order, or running it in another process, gives
the same route with the same identity, and its explanation names every venue
considered and why it stood where it did. Whether an order may be routed away
from a venue at all -- by market rules or a best-execution obligation -- is not
something AlphaLab knows; it compares only the venues it is given.

Run

    python examples/64_smart_routing.py
"""

from dataclasses import replace
from decimal import Decimal

from _execution_world import (
    ACCOUNT_ID,
    ASSET_ID,
    LISTING,
    OPEN,
    PROFILES,
    banner,
    number,
    quotes,
    say,
    section,
    short,
)

from alphalab.broker import order_requirements
from alphalab.core.enums import AssetType, OrderType, Side, TimeInForce
from alphalab.execution import (
    RouteDecision,
    RouteRequest,
    RoutingObjective,
    RoutingPolicy,
    VenueProfile,
    VenueQuote,
    select_route,
)
from alphalab.lifecycle import research_configuration_with_execution

DECIDED = OPEN + 5.0
QUOTED = OPEN + 4.5

CHEAPEST = RoutingPolicy(
    objective=RoutingObjective.LOWEST_ALL_IN_COST,
    max_quote_age_seconds=2.0,
    max_latency_seconds=None,
    allow_split=False,
    allow_partial=False,
    excluded_venues=frozenset(),
)


def request(
    quantity: str,
    *,
    side: Side = Side.BUY,
    order_type: OrderType = OrderType.LIMIT,
    limit: str | None = "100.05",
    position_before: str = "0",
) -> RouteRequest:
    requirements = order_requirements(
        asset_class=AssetType.EQUITY,
        listing_venue=LISTING,
        account_id=ACCOUNT_ID,
        order_type=order_type,
        time_in_force=TimeInForce.DAY,
        side=side,
        quantity=Decimal(quantity),
        position_before=Decimal(position_before),
        extended_hours=False,
        bracket_orders=False,
        margin=False,
        features=frozenset(),
    )
    return RouteRequest(
        order_id=f"CHILD-{side}-{quantity}",
        asset_id=ASSET_ID,
        side=side,
        quantity=Decimal(quantity),
        limit_price=None if limit is None else Decimal(limit),
        currency="USD",
        requirements=requirements,
        quantity_increment=Decimal("1"),
        decided_at=DECIDED,
    )


def table(decision: RouteDecision) -> None:
    """Every venue considered, ranked first, with what it showed and what it would cost."""

    print(
        f"    {'venue':<13}{'status':<22}{'touch':>8}{'shows':>7}{'fill':>9}"
        f"{'all-in':>11}{'rank':>5}"
    )
    for c in sorted(decision.candidates, key=lambda c: (c.rank is None, c.rank or 0, c.venue)):
        all_in = "n/a" if c.all_in_price is None else f"{c.all_in_price:.4f}"
        print(
            f"    {c.venue:<13}{c.status!s:<22}{number(c.quoted_price):>8}"
            f"{number(c.displayed_quantity):>7}{number(c.expected_price):>9}{all_in:>11}"
            f"{'' if c.rank is None else c.rank:>5}"
        )


def verdict(decision: RouteDecision) -> None:
    legs = " + ".join(f"{number(leg.quantity)} at {leg.venue}" for leg in decision.legs)
    print(f"    {decision.status}: {legs or 'no leg'}")
    say(decision.reason, indent="      ")


def reasons(decision: RouteDecision, *venues: str) -> None:
    for c in decision.candidates:
        if c.venue in venues:
            say(f"{c.venue}: {c.reason}", indent="      ")


def the_evidence() -> None:
    section("The evidence each venue is judged on")
    for quote, profile in zip(quotes(QUOTED), PROFILES, strict=True):
        print(
            f"  {quote.venue:<13}{number(quote.bid)} / {number(quote.ask)} x "
            f"{number(quote.ask_size):<7}latency {number(profile.latency_seconds)}s  "
            f"{profile.capabilities.adapter_id}"
        )
    say(
        "Each venue's expected fill is priced by its own cost model from its quote midpoint: "
        "the half-spread a marketable order pays and a proportional fee -- one basis point at "
        "NORTH, half a basis point at SOUTH, none at EAST. EAST takes no limit orders."
    )


def one_venue_or_many() -> None:
    section("A limit buy of 1,000: one venue, or a split")
    limit_buy = request("1000")
    single = select_route(limit_buy, quotes(QUOTED), PROFILES, CHEAPEST)
    print("  lowest all-in cost, one venue:")
    table(single)
    verdict(single)
    reasons(single, "VENUE-EAST")

    split = select_route(limit_buy, quotes(QUOTED), PROFILES, replace(CHEAPEST, allow_split=True))
    print("  lowest all-in cost, split allowed:")
    verdict(split)
    for leg in split.legs:
        print(
            f"      {leg.venue:<13}{number(leg.quantity):>6} expected {leg.expected_price:.4f}, "
            f"all-in {leg.all_in_price:.6f}, fee {leg.expected_costs.fees:.4f}"
        )
    say(
        "SOUTH is cheaper per share but shows only 300, so without a split the route goes "
        "whole to NORTH, the best-ranked venue that can take all of it. With a split the "
        "sweep takes SOUTH's 300 first and NORTH's 700 after, each leg re-priced at the "
        "quantity it actually takes. The sweep is greedy, which is optimal while costs are "
        "linear in quantity and is not claimed optimal otherwise."
    )

    market_buy = request("1000", order_type=OrderType.MARKET, limit=None)
    by_touch = select_route(
        market_buy,
        quotes(QUOTED),
        PROFILES,
        replace(CHEAPEST, objective=RoutingObjective.BEST_QUOTED_PRICE),
    )
    print("  a market buy of 1,000, best quoted price:")
    table(by_touch)
    verdict(by_touch)
    say(
        "SOUTH and EAST quote the same touch; the policy's tie-break -- the objective's "
        "price, then lower latency with unknown last, then the venue identifier -- ranks "
        f"EAST first. It is part of the policy's identity: {', '.join(CHEAPEST.tie_break)}."
    )


def ruled_out_or_unknown() -> None:
    section("Ruled out, or not known: the evidence decides which")
    stale = tuple(
        replace(quote, as_of=OPEN + 1.0) if quote.venue == "VENUE-NORTH" else quote
        for quote in quotes(QUOTED)
    )
    euro = VenueQuote(
        "VENUE-WEST",
        ASSET_ID,
        Decimal("92.10"),
        Decimal("92.12"),
        Decimal("5000"),
        Decimal("5000"),
        "EUR",
        QUOTED,
        "example tape",
    )
    ahead = replace(quotes(QUOTED)[1], as_of=DECIDED + 1.0)
    for label, decision, venues in (
        (
            "NORTH's quote is four seconds old; the policy accepts two",
            select_route(request("1000"), stale, PROFILES, CHEAPEST),
            ("VENUE-NORTH",),
        ),
        (
            "a fourth venue quotes in euros and has no profile",
            select_route(request("250"), (*quotes(QUOTED), euro), PROFILES, CHEAPEST),
            ("VENUE-WEST",),
        ),
        (
            "SOUTH's quote is dated after the decision",
            select_route(
                request("250"), (quotes(QUOTED)[0], ahead, quotes(QUOTED)[2]), PROFILES, CHEAPEST
            ),
            ("VENUE-SOUTH",),
        ),
        (
            "latency capped at three milliseconds",
            select_route(
                request("250"),
                quotes(QUOTED),
                PROFILES,
                replace(CHEAPEST, max_latency_seconds=Decimal("0.003")),
            ),
            ("VENUE-NORTH",),
        ),
        (
            "a buy limited at 100.015: NORTH's touch is outside it",
            select_route(request("250", limit="100.015"), quotes(QUOTED), PROFILES, CHEAPEST),
            ("VENUE-NORTH",),
        ),
        (
            "a short sale of 250: SOUTH never said whether it permits one",
            select_route(
                request("250", side=Side.SELL, limit="99.95"), quotes(QUOTED), PROFILES, CHEAPEST
            ),
            ("VENUE-SOUTH",),
        ),
    ):
        print(f"  {label}:")
        verdict(decision)
        reasons(decision, *venues)


def infeasible_or_unknown() -> None:
    section("INFEASIBLE is not INSUFFICIENT_EVIDENCE")
    big = request("5000")
    ruled_out = select_route(big, quotes(QUOTED), PROFILES, CHEAPEST)
    print("  5,000 at a limit, no split: every venue positively too small or unable")
    verdict(ruled_out)
    partial = select_route(
        big, quotes(QUOTED), PROFILES, replace(CHEAPEST, allow_split=True, allow_partial=True)
    )
    print("  the same, split and partial allowed:")
    verdict(partial)
    print(
        f"      routed {number(partial.routed_quantity)}, left {number(partial.unrouted_quantity)}"
    )
    unknown = select_route(
        big,
        quotes(QUOTED),
        (*PROFILES, VenueProfile("VENUE-WEST", PROFILES[0].capabilities, PROFILES[0].costs, None)),
        CHEAPEST,
    )
    print("  the same, with a fourth venue profiled but not quoted:")
    verdict(unknown)
    say(
        "When every venue is ruled out, no route exists and the decision says INFEASIBLE. "
        "When one could not be assessed, a route may exist that the evidence could not "
        "show, and the decision says so rather than claiming there is none."
    )


def identities() -> None:
    section("Identity: a function of the evidence, not of how it was listed")
    split_policy = replace(CHEAPEST, allow_split=True)
    one = select_route(request("1000"), quotes(QUOTED), PROFILES, split_policy)
    other = select_route(
        request("1000"), tuple(reversed(quotes(QUOTED))), tuple(reversed(PROFILES)), split_policy
    )
    print(f"  decision, venues listed forwards   {short(one.decision_id)}")
    print(f"  decision, venues listed backwards  {short(other.decision_id)}")
    print(f"  the evidence it read               {short(one.evidence_id)}")
    print(f"  the policy                         {short(split_policy.policy_id)}")
    print("  the explanation it carries:")
    for line in one.explanation():
        say(line, indent="    ")
    research = research_configuration_with_execution(
        {"signal": "momentum-and-reversion"},
        algorithms={},
        routing={"child_orders": split_policy},
        study=None,
    )
    key = "routing.child_orders.policy"
    print(f"  in the research record: {key} = {short(research.settings[key])}")


def main() -> None:
    banner(64, "Smart Routing from Local Venue Evidence")
    the_evidence()
    one_venue_or_many()
    ruled_out_or_unknown()
    infeasible_or_unknown()
    identities()
    print()


if __name__ == "__main__":
    main()
