"""The optimal split of an order across venues (ledger BRK-005, v3.13).

Until v3.13 a split route was a greedy sweep, ranked by each venue's all-in price
at its full displayed size: not optimal under a per-trade fee or a non-linear
impact. ``SplitMethod.OPTIMAL`` claims the lowest total all-in cost over whole
increments when every venue's cost is a fixed charge plus a convex function of
quantity. The claim is checked here the only way that does not trust the method:
against every allocation, enumerated.
"""

from __future__ import annotations

import dataclasses
import itertools
import random
from decimal import Decimal
from typing import Any

import pytest

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
from alphalab.execution import (
    MAX_SPLIT_FIXED_CHARGE_VENUES,
    CostContext,
    ExecutionCostModel,
    LinearImpact,
    NoImpact,
    NoSlippage,
    NoTax,
    PerTradeFee,
    QuotedHalfSpread,
    SplitMethod,
)
from alphalab.execution.commission import FixedCommission
from alphalab.execution.exceptions import ExecutionValidationError
from alphalab.execution.routing import (
    RouteDecision,
    RouteRequest,
    RouteStatus,
    RoutingObjective,
    RoutingPolicy,
    VenueProfile,
    VenueQuote,
    _price_leg,
    select_route,
)

D = Decimal
AT = 1_000.0
NO = Support.UNSUPPORTED

DECLARATION = CapabilityDeclaration(
    adapter_id="venue-adapter",
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


def _costs(per_trade: str = "0", impact: str = "0") -> ExecutionCostModel:
    return ExecutionCostModel(
        spread_model=QuotedHalfSpread(),
        slippage_model=NoSlippage(),
        impact_model=LinearImpact(D(impact)) if impact != "0" else NoImpact(),
        commission_model=FixedCommission(D("0")),
        fee_model=PerTradeFee(D(per_trade)),
        tax_model=NoTax(),
    )


def _quote(venue: str, bid: str, ask: str, size: int) -> VenueQuote:
    return VenueQuote(venue, "ASSET", D(bid), D(ask), D(size), D(size), "USD", AT - 1, "feed")


def _profile(venue: str, costs: ExecutionCostModel) -> VenueProfile:
    return VenueProfile(venue, DECLARATION, costs, D("0.001"))


def _request(quantity: int, side: Side = Side.BUY) -> RouteRequest:
    return RouteRequest("ORD-1", "ASSET", side, D(quantity), None, "USD", REQUIREMENTS, D("1"), AT)


def _policy(**overrides: Any) -> RoutingPolicy:
    base = RoutingPolicy(
        objective=RoutingObjective.LOWEST_ALL_IN_COST,
        max_quote_age_seconds=5.0,
        max_latency_seconds=None,
        allow_split=True,
        allow_partial=False,
        excluded_venues=frozenset(),
        split_method=SplitMethod.OPTIMAL,
    )
    return dataclasses.replace(base, **overrides)


def _directed_total(
    allocation: dict[str, int],
    request: RouteRequest,
    quotes: dict[str, VenueQuote],
    profiles: dict[str, VenueProfile],
) -> Decimal:
    """What an allocation costs a buyer, or fails to bring in for a seller."""

    total = D("0")
    for venue, units in allocation.items():
        if units == 0:
            continue
        fill_price, costs, _ = _price_leg(profiles[venue], quotes[venue], request, D(units))
        paid = fill_price * D(units)
        if request.side is Side.BUY:
            total += paid + costs.cash_charged
        else:
            total += costs.cash_charged - paid
    return total


def _brute_force(
    request: RouteRequest, quotes: dict[str, VenueQuote], profiles: dict[str, VenueProfile]
) -> Decimal:
    venues = sorted(quotes)
    sizes = [
        int((quotes[v].ask_size if request.side is Side.BUY else quotes[v].bid_size) or 0)
        for v in venues
    ]
    wanted = int(request.quantity)
    best: Decimal | None = None
    for split in itertools.product(*(range(size + 1) for size in sizes)):
        if sum(split) != wanted:
            continue
        total = _directed_total(dict(zip(venues, split, strict=True)), request, quotes, profiles)
        if best is None or total < best:
            best = total
    assert best is not None
    return best


def _decision_total(
    decision: RouteDecision,
    request: RouteRequest,
    quotes: dict[str, VenueQuote],
    profiles: dict[str, VenueProfile],
) -> Decimal:
    return _directed_total(
        {leg.venue: int(leg.quantity) for leg in decision.legs}, request, quotes, profiles
    )


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
def test_the_optimal_split_is_the_cheapest_of_every_allocation(side: Side) -> None:
    rng = random.Random(20261004)
    for _ in range(60):
        count = rng.randint(1, 4)
        quotes: dict[str, VenueQuote] = {}
        profiles: dict[str, VenueProfile] = {}
        for index in range(count):
            venue = f"V{index}"
            mid = D(rng.choice(["100.00", "100.02", "100.05", "100.10"]))
            half = D(rng.choice(["0.01", "0.02", "0.05"]))
            size = rng.randint(1, 9)
            quotes[venue] = _quote(venue, str(mid - half), str(mid + half), size)
            profiles[venue] = _profile(
                venue,
                _costs(
                    per_trade=rng.choice(["0", "0", "1", "5"]),
                    impact=rng.choice(["0", "0", "0.001", "0.004"]),
                ),
            )
        capacity = sum(int(q.ask_size or 0) for q in quotes.values())
        request = _request(rng.randint(1, capacity), side)
        decision = select_route(request, list(quotes.values()), list(profiles.values()), _policy())
        assert decision.status is RouteStatus.ROUTED
        assert decision.routed_quantity == request.quantity
        assert _decision_total(decision, request, quotes, profiles) == _brute_force(
            request, quotes, profiles
        )


def test_the_greedy_sweep_pays_a_fee_the_optimal_split_does_not() -> None:
    """A venue cheapest at its full size takes only one unit and charges its fee anyway."""

    quotes = {"A": _quote("A", "99.98", "100.00", 10), "B": _quote("B", "99.98", "100.02", 10)}
    profiles = {"A": _profile("A", _costs(per_trade="0.10")), "B": _profile("B", _costs())}
    request = _request(11)
    optimal = select_route(request, list(quotes.values()), list(profiles.values()), _policy())
    greedy = select_route(
        request,
        list(quotes.values()),
        list(profiles.values()),
        _policy(split_method=SplitMethod.GREEDY_SWEEP),
    )
    assert _decision_total(optimal, request, quotes, profiles) <= _decision_total(
        greedy, request, quotes, profiles
    )
    assert _decision_total(optimal, request, quotes, profiles) == _brute_force(
        request, quotes, profiles
    )


def test_an_optimal_split_needs_a_split_and_the_all_in_objective() -> None:
    with pytest.raises(ExecutionValidationError, match="may not split"):
        _policy(allow_split=False)
    with pytest.raises(ExecutionValidationError, match="all-in cost"):
        _policy(objective=RoutingObjective.BEST_QUOTED_PRICE)


def test_a_policy_that_does_not_name_the_method_keeps_its_identity() -> None:
    sweep = _policy(split_method=SplitMethod.GREEDY_SWEEP)
    implicit = RoutingPolicy(
        objective=RoutingObjective.LOWEST_ALL_IN_COST,
        max_quote_age_seconds=5.0,
        max_latency_seconds=None,
        allow_split=True,
        allow_partial=False,
        excluded_venues=frozenset(),
    )
    assert implicit.policy_id == sweep.policy_id
    assert _policy().policy_id != sweep.policy_id


@dataclasses.dataclass(frozen=True)
class _DiscountedImpact:
    """An impact per unit of ``100 / sqrt(q)``: a total of ``100 sqrt(q)``, concave.

    (``1 / q`` would total a constant -- a fixed charge, which is allowed.) The
    fall is checked where the search looks, above the cash-rounding tolerance,
    so the concavity here is strong enough to be seen at any increment of the
    venue's fifty.
    """

    def impact(self, context: CostContext) -> Decimal:
        return D("100") / context.quantity.sqrt()


def test_a_cost_whose_marginal_falls_is_refused() -> None:
    concave = ExecutionCostModel(
        spread_model=QuotedHalfSpread(),
        slippage_model=NoSlippage(),
        impact_model=_DiscountedImpact(),
        commission_model=FixedCommission(D("0")),
        fee_model=PerTradeFee(D("0")),
        tax_model=NoTax(),
    )
    quotes = [_quote("A", "99.98", "100.00", 50), _quote("B", "99.98", "100.02", 50)]
    profiles = [_profile("A", concave), _profile("B", _costs(impact="0.01"))]
    with pytest.raises(ExecutionValidationError, match="not convex"):
        select_route(_request(60), quotes, profiles, _policy())


def test_too_many_venues_with_a_fixed_charge_are_refused() -> None:
    count = MAX_SPLIT_FIXED_CHARGE_VENUES + 1
    quotes = [_quote(f"V{i}", "99.98", "100.00", 5) for i in range(count)]
    profiles = [_profile(f"V{i}", _costs(per_trade="1")) for i in range(count)]
    with pytest.raises(ExecutionValidationError, match="fixed charge"):
        select_route(_request(20), quotes, profiles, _policy())


def test_liquidity_short_of_the_order_takes_everything_when_partial_is_allowed() -> None:
    quotes = [_quote("A", "99.98", "100.00", 3), _quote("B", "99.98", "100.02", 4)]
    profiles = [_profile("A", _costs(per_trade="1")), _profile("B", _costs())]
    decision = select_route(_request(10), quotes, profiles, _policy(allow_partial=True))
    assert decision.status is RouteStatus.PARTIAL
    assert decision.routed_quantity == D("7")
    refused = select_route(_request(10), quotes, profiles, _policy())
    assert refused.status is RouteStatus.INFEASIBLE


def test_the_decision_does_not_depend_on_the_order_evidence_was_listed_in() -> None:
    quotes = [_quote("A", "99.98", "100.00", 6), _quote("B", "99.97", "100.01", 6)]
    profiles = [_profile("A", _costs(per_trade="1", impact="0.004")), _profile("B", _costs())]
    first = select_route(_request(9), quotes, profiles, _policy())
    second = select_route(_request(9), quotes[::-1], profiles[::-1], _policy())
    assert first.decision_id == second.decision_id
