"""
AlphaLab Examples
=================

Example 67 : The Cheapest Split, an Estimated Urgency, Icebergs that Vary

Difficulty : Advanced

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 63 (execution algorithms)
✓ Example 64 (smart routing)

Topics
------

• The greedy sweep and the optimal split of one order across three venues --
  a per-trade fee paid on a remainder, a venue filled past its marginal cost
• What "optimal" claims (a fixed charge plus a convex cost per venue) and the
  cost it refuses to call optimal
• An urgency estimated from risk aversion, volatility and impact -- the
  Almgren-Chriss curvature -- instead of a number typed in
• Iceberg tranches drawn around the displayed size from a seed: varied,
  bounded, and the same again on a rerun

What this shows
---------------

A split that sweeps venues in the order of their all-in price at full size can
pay a venue's whole per-trade fee on a remainder, and can fill a venue whose
price concedes with size past the point where another is cheaper at the
margin. The optimal split
searches every set of venues that charge a fixed fee and fills the rest by
marginal cost, which is exact when each venue's cost is a fixed charge plus a
convex function of the quantity -- the shape every cost model here has. Asked
for a cost whose marginal falls, it refuses rather than returning a split it
cannot stand behind.

Run

    python examples/67_optimal_split_and_urgency.py
"""

import textwrap
from decimal import Decimal
from uuid import UUID

from alphalab.core.capabilities import (
    ANY_LISTING_VENUE,
    AccountCapability,
    Capability,
    CapabilityDeclaration,
    ExecutionRequirements,
    MarketCapability,
    Support,
)
from alphalab.core.enums import AssetType, OrderType, Side, TimeInForce
from alphalab.core.order_request import OrderRequest
from alphalab.execution import (
    AlgorithmTerms,
    CostContext,
    ExecutionCostModel,
    ExecutionValidationError,
    Iceberg,
    LinearImpact,
    NoImpact,
    NoSlippage,
    NoTax,
    PerTradeFee,
    QuotedHalfSpread,
    RouteRequest,
    RoutingObjective,
    RoutingPolicy,
    SplitMethod,
    TrancheRandomization,
    VenueProfile,
    VenueQuote,
    estimate_urgency,
    record_child_execution,
    release_children,
    select_route,
    start_algorithm,
)
from alphalab.execution.commission import FixedCommission

D = Decimal
NO = Support.UNSUPPORTED
AT = 1_767_225_600.0

DECLARATION = CapabilityDeclaration(
    adapter_id="adapter-example",
    supported_features=frozenset({Capability.CANCEL_REPLACE}),
    unsupported_features=frozenset(),
    markets=(
        MarketCapability(
            AssetType.EQUITY,
            ANY_LISTING_VENUE,
            frozenset({OrderType.MARKET, OrderType.LIMIT}),
            frozenset({TimeInForce.DAY, TimeInForce.IOC}),
            NO,
            NO,
            NO,
            NO,
        ),
    ),
    unsupported_asset_classes=frozenset(),
    accounts=(AccountCapability("ACC-1", frozenset({AssetType.EQUITY}), NO, NO),),
)
REQUIREMENTS = ExecutionRequirements(
    asset_class=AssetType.EQUITY,
    listing_venue="XNAS",
    account_id="ACC-1",
    order_types=frozenset({OrderType.LIMIT}),
    time_in_force=frozenset({TimeInForce.IOC}),
    short_selling=False,
    fractional_quantities=False,
    extended_hours=False,
    bracket_orders=False,
    margin=False,
    features=frozenset(),
)


def rule(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def costs(per_trade: str = "0", impact: str = "0") -> ExecutionCostModel:
    return ExecutionCostModel(
        spread_model=QuotedHalfSpread(),
        slippage_model=NoSlippage(),
        impact_model=LinearImpact(D(impact)) if impact != "0" else NoImpact(),
        commission_model=FixedCommission(D("0")),
        fee_model=PerTradeFee(D(per_trade)),
        tax_model=NoTax(),
    )


def quote(venue: str, ask: str, size: int) -> VenueQuote:
    bid = str(D(ask) - D("0.04"))
    shown = D(size)
    return VenueQuote(venue, "ACME", D(bid), D(ask), shown, shown, "USD", AT - 1, "example tape")


def policy(method: SplitMethod) -> RoutingPolicy:
    return RoutingPolicy(
        objective=RoutingObjective.LOWEST_ALL_IN_COST,
        max_quote_age_seconds=5.0,
        max_latency_seconds=None,
        allow_split=True,
        allow_partial=False,
        excluded_venues=frozenset(),
        split_method=method,
    )


class FallingImpact:
    """An impact per unit of ``100 / sqrt(q)``: the more you buy, the cheaper each unit."""

    def impact(self, context: CostContext) -> Decimal:
        return D("100") / context.quantity.sqrt()


def main() -> None:
    print("=" * 72)
    print("AlphaLab Example 67 : The Cheapest Split, an Estimated Urgency, Icebergs")
    print("=" * 72)

    # ----------------------------------------------------------------- #
    rule("One order, three venues, two ways to split it")

    quotes = [
        quote("NORTH", "100.00", 100),
        quote("SOUTH", "100.01", 100),
        quote("EAST", "100.06", 300),
    ]
    profiles = [
        VenueProfile("NORTH", DECLARATION, costs(per_trade="5.00"), D("0.001")),
        VenueProfile("SOUTH", DECLARATION, costs(impact="0.0002"), D("0.001")),
        VenueProfile("EAST", DECLARATION, costs(), D("0.001")),
    ]
    print("  NORTH asks 100.00 for 100 and charges 5.00 a trade. SOUTH asks 100.01 for")
    print("  100, and concedes more the larger the share of those 100 taken. EAST asks")
    print("  100.06 for 300 and charges nothing.")
    for shares in ("60", "120", "180", "250"):
        request = RouteRequest(
            f"ORD-67-{shares}", "ACME", Side.BUY, D(shares), None, "USD", REQUIREMENTS, D("1"), AT
        )
        print(f"  buying {shares}:")
        for method in (SplitMethod.GREEDY_SWEEP, SplitMethod.OPTIMAL):
            decision = select_route(request, quotes, profiles, policy(method))
            legs = ", ".join(f"{leg.venue} {leg.quantity}" for leg in decision.legs)
            total = sum((leg.all_in_price * leg.quantity for leg in decision.legs), D("0"))
            print(f"    {method.name:<12} {legs:<30} all-in {total:,.2f}")
    print()
    print("  The sweep ranks each venue by its all-in price at its full displayed size:")
    print("  SOUTH, then NORTH -- whose fee is spread over 100 shares -- then EAST.")
    print("  Buying 120, it takes 20 from NORTH and pays the whole fee on those 20 --")
    print("  0.25 a share -- where EAST asks only 0.06 a share more than NORTH; the")
    print("  optimal split buys them at EAST. Buying 180, the sweep fills SOUTH to the")
    print("  last share because SOUTH's average price is the lowest, though SOUTH's last")
    print("  20 shares cost more than NORTH's next 20, whose fee is paid either way.")
    print("  Buying 60 or 250, there is nothing to choose between.")

    # ----------------------------------------------------------------- #
    rule("What it will not call optimal")

    falling = ExecutionCostModel(
        spread_model=QuotedHalfSpread(),
        slippage_model=NoSlippage(),
        impact_model=FallingImpact(),
        commission_model=FixedCommission(D("0")),
        fee_model=PerTradeFee(D("0")),
        tax_model=NoTax(),
    )
    try:
        select_route(
            request,
            quotes,
            [VenueProfile("NORTH", DECLARATION, falling, D("0.001")), *profiles[1:]],
            policy(SplitMethod.OPTIMAL),
        )
        print("  NOT REFUSED")
    except ExecutionValidationError as error:
        print("  a cost whose marginal falls: refused --")
        print(textwrap.fill(str(error), width=74, initial_indent="    ", subsequent_indent="    "))

    # ----------------------------------------------------------------- #
    rule("An urgency estimated, not typed in")

    for risk_aversion in ("0", "0.000002", "0.00002"):
        estimate = estimate_urgency(
            risk_aversion=D(risk_aversion),
            volatility=D("0.95"),
            temporary_impact=D("0.0000025"),
            permanent_impact=D("0.00000025"),
            horizon=D("5"),
            slices=5,
        )
        halfway = estimate.urgency.fraction(D("0.5"))
        print(
            f"  risk aversion {risk_aversion:<9} kappa*T {estimate.urgency.kappa:>7.4f}   "
            f"done by halfway {halfway:.1%}"
        )
    print()
    print("  Almgren and Chriss's worked example: a risk-neutral trader works the order")
    print("  evenly; the more a trader minds the variance of the proceeds, the more of")
    print("  the order goes early. Every input is recorded beside the estimate.")

    # ----------------------------------------------------------------- #
    rule("Iceberg tranches that vary, and reproduce")

    parent = OrderRequest(
        order_id=str(UUID("67676767-6767-4767-8767-676767676767")),
        strategy_id="DESK",
        asset_id="ACME",
        side=Side.BUY,
        quantity=D("100"),
        price=D("100"),
        timestamp=0.0,
    )
    terms = AlgorithmTerms(0.0, 300.0, D("1"), OrderType.MARKET, None)

    def tranches(algorithm: Iceberg) -> list[str]:
        state = start_algorithm(parent, algorithm, terms)
        sizes: list[str] = []
        for step in range(1_000):
            state, released = release_children(state, 1.0)
            if not released:
                break
            (child,) = released
            sizes.append(str(child.quantity))
            state = record_child_execution(state, child.child_id, f"X-{step}", child.quantity, 1.0)
        return sizes

    print(f"  fixed, 10 shown    : {' '.join(tranches(Iceberg(D('10'))))}")
    varied = Iceberg(D("10"), TrancheRandomization(D("3"), seed=67))
    print(f"  10 +/- 3, seed 67  : {' '.join(tranches(varied))}")
    print(f"  the same, rerun    : {' '.join(tranches(varied))}")
    print(
        f"  10 +/- 3, seed 68  : "
        f"{' '.join(tranches(Iceberg(D('10'), TrancheRandomization(D('3'), seed=68))))}"
    )
    print()
    print("  Each tranche is drawn from SHA-256 over the seed, the parent and the")
    print("  tranche's index, so a run reproduces from its configuration and two")
    print("  parents worked with one seed do not show the same pattern.")


if __name__ == "__main__":
    main()
