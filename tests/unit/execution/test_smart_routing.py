"""Smart routing: evidence, eligibility, ranking, liquidity selection and reproducibility."""

from __future__ import annotations

import dataclasses
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
from alphalab.execution.commission import FixedCommission
from alphalab.execution.costs import (
    CostContext,
    ExecutionCostModel,
    NoImpact,
    NoSlippage,
    NoTax,
    PerTradeFee,
    ProportionalFee,
    QuotedHalfSpread,
)
from alphalab.execution.exceptions import ExecutionValidationError
from alphalab.execution.routing import (
    CandidateStatus,
    RouteDecision,
    RouteRequest,
    RouteStatus,
    RoutingObjective,
    RoutingPolicy,
    VenueProfile,
    VenueQuote,
    select_route,
)

D = Decimal
YES, NO, SILENT = Support.SUPPORTED, Support.UNSUPPORTED, Support.UNDECLARED
AT = 1_000.0


def _declaration(
    tif: frozenset[TimeInForce] = frozenset({TimeInForce.DAY, TimeInForce.IOC}),
) -> CapabilityDeclaration:
    return CapabilityDeclaration(
        adapter_id="venue-adapter",
        supported_features=frozenset({Capability.CANCEL_REPLACE}),
        unsupported_features=frozenset(),
        markets=(
            MarketCapability(
                AssetType.EQUITY,
                ANY_LISTING_VENUE,
                frozenset({OrderType.MARKET, OrderType.LIMIT}),
                tif,
                NO,
                NO,
                NO,
                NO,
            ),
        ),
        unsupported_asset_classes=frozenset(),
        accounts=(AccountCapability("ACC-1", frozenset({AssetType.EQUITY}), NO, NO),),
    )


def _costs(fee: str = "0", per_trade: str | None = None) -> ExecutionCostModel:
    return ExecutionCostModel(
        spread_model=QuotedHalfSpread(),
        slippage_model=NoSlippage(),
        impact_model=NoImpact(),
        commission_model=FixedCommission(D("0")),
        fee_model=PerTradeFee(D(per_trade)) if per_trade is not None else ProportionalFee(D(fee)),
        tax_model=NoTax(),
    )


def _quote(
    venue: str,
    bid: str | None = "99.98",
    ask: str | None = "100.02",
    size: str | None = "1000",
    as_of: float = AT - 1,
    currency: str = "USD",
) -> VenueQuote:
    return VenueQuote(
        venue=venue,
        asset_id="ASSET",
        bid=None if bid is None else D(bid),
        ask=None if ask is None else D(ask),
        bid_size=None if size is None else D(size),
        ask_size=None if size is None else D(size),
        currency=currency,
        as_of=as_of,
        source="consolidated-feed",
    )


def _profile(
    venue: str,
    latency: str | None = "0.001",
    costs: ExecutionCostModel | None = None,
    declaration: CapabilityDeclaration | None = None,
) -> VenueProfile:
    return VenueProfile(
        venue,
        declaration or _declaration(),
        costs or _costs(),
        None if latency is None else D(latency),
    )


_REQUIREMENTS = ExecutionRequirements(
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


def _request(quantity: str = "500", side: Side = Side.BUY, **overrides: Any) -> RouteRequest:
    base = RouteRequest(
        order_id="ORD-1",
        asset_id="ASSET",
        side=side,
        quantity=D(quantity),
        limit_price=None,
        currency="USD",
        requirements=_REQUIREMENTS,
        quantity_increment=D("1"),
        decided_at=AT,
    )
    return dataclasses.replace(base, **overrides)


def _policy(**overrides: Any) -> RoutingPolicy:
    base = RoutingPolicy(
        objective=RoutingObjective.LOWEST_ALL_IN_COST,
        max_quote_age_seconds=5.0,
        max_latency_seconds=None,
        allow_split=False,
        allow_partial=False,
        excluded_venues=frozenset(),
    )
    return dataclasses.replace(base, **overrides)


def _status(decision: RouteDecision, venue: str) -> CandidateStatus:
    return next(c.status for c in decision.candidates if c.venue == venue)


# --------------------------------------------------------------------------- #
# One venue, and the basic arithmetic
# --------------------------------------------------------------------------- #


def test_one_eligible_venue_takes_the_whole_order_at_its_cost_models_price() -> None:
    decision = select_route(
        _request(), [_quote("A")], [_profile("A", costs=_costs("0.0001"))], _policy()
    )
    assert decision.status is RouteStatus.ROUTED
    (leg,) = decision.legs
    # Midpoint 100.00, half the quoted spread 0.02: the spread is counted once.
    assert (leg.venue, leg.quantity, leg.quoted_price, leg.expected_price) == (
        "A",
        D("500"),
        D("100.02"),
        D("100.02"),
    )
    # 0.0001 of 50,010 is 5.001, charged to the cent: 0.01 a unit.
    assert leg.expected_costs.fees == D("5.00")
    assert leg.all_in_price == D("100.03")
    assert decision.routed_quantity == 500 and decision.unrouted_quantity == 0


def test_a_sale_is_ranked_by_what_it_receives() -> None:
    quotes = [_quote("A", bid="99.99", ask="100.03"), _quote("B", bid="99.97", ask="100.01")]
    decision = select_route(
        _request(side=Side.SELL), quotes, [_profile("A"), _profile("B")], _policy()
    )
    assert decision.legs[0].venue == "A"
    assert decision.legs[0].quoted_price == D("99.99")


# --------------------------------------------------------------------------- #
# Ranking: price, cost, liquidity
# --------------------------------------------------------------------------- #


def test_the_objective_decides_between_price_and_all_in_cost() -> None:
    quotes = [_quote("CHEAP-TOUCH", ask="100.01", bid="99.99"), _quote("CHEAP-ALL-IN")]
    profiles = [
        _profile("CHEAP-TOUCH", costs=_costs("0.001")),
        _profile("CHEAP-ALL-IN", costs=_costs("0")),
    ]
    by_price = select_route(
        _request(), quotes, profiles, _policy(objective=RoutingObjective.BEST_QUOTED_PRICE)
    )
    by_cost = select_route(_request(), quotes, profiles, _policy())
    assert by_price.legs[0].venue == "CHEAP-TOUCH"
    assert by_cost.legs[0].venue == "CHEAP-ALL-IN"


def test_a_single_venue_route_takes_the_best_venue_that_can_fill_the_whole_order() -> None:
    quotes = [
        _quote("BEST-SMALL", ask="100.00", bid="99.98", size="100"),
        _quote("DEEP", size="1000"),
    ]
    decision = select_route(
        _request(), quotes, [_profile("BEST-SMALL"), _profile("DEEP")], _policy()
    )
    assert [leg.venue for leg in decision.legs] == ["DEEP"]
    ranks = {c.venue: c.rank for c in decision.candidates}
    assert ranks == {"BEST-SMALL": 1, "DEEP": 2}


def test_a_split_route_sweeps_the_ranking_and_reprices_each_leg_at_its_quantity() -> None:
    quotes = [_quote("A", ask="100.00", bid="99.98", size="300"), _quote("B", size="400")]
    profiles = [
        _profile("A", costs=_costs(per_trade="3")),
        _profile("B", costs=_costs(per_trade="3")),
    ]
    decision = select_route(_request(), quotes, profiles, _policy(allow_split=True))
    assert decision.status is RouteStatus.ROUTED
    assert [(leg.venue, leg.quantity) for leg in decision.legs] == [
        ("A", D("300")),
        ("B", D("200")),
    ]
    # B was ranked at 400 units; its leg takes 200, so the per-trade fee is
    # spread over 200 and the leg says so.
    b_leg = decision.legs[1]
    assert b_leg.all_in_price == D("100.02") + D("3") / D("200")


def test_short_liquidity_is_partial_only_when_the_policy_allows_it() -> None:
    quotes = [_quote("A", size="200"), _quote("B", size="100")]
    profiles = [_profile("A"), _profile("B")]
    refused = select_route(_request(), quotes, profiles, _policy(allow_split=True))
    assert refused.status is RouteStatus.INFEASIBLE and refused.legs == ()
    partial = select_route(
        _request(), quotes, profiles, _policy(allow_split=True, allow_partial=True)
    )
    assert partial.status is RouteStatus.PARTIAL
    assert partial.routed_quantity == D("300") and partial.unrouted_quantity == D("200")
    single = select_route(_request(), quotes, profiles, _policy(allow_partial=True))
    assert single.status is RouteStatus.PARTIAL
    assert [(leg.venue, leg.quantity) for leg in single.legs] == [("A", D("200"))]


# --------------------------------------------------------------------------- #
# Constraints
# --------------------------------------------------------------------------- #


def test_constraints_rule_venues_out_and_say_why() -> None:
    quotes = [
        _quote("EXCLUDED"),
        _quote("SLOW"),
        _quote("EURO", currency="EUR"),
        _quote("PRICEY", bid="100.10", ask="100.20"),
        _quote("THIN", size="0.5"),
        _quote("OK"),
    ]
    profiles = [
        _profile("EXCLUDED"),
        _profile("SLOW", latency="0.5"),
        _profile("EURO"),
        _profile("PRICEY"),
        _profile("THIN"),
        _profile("OK"),
    ]
    decision = select_route(
        _request(limit_price=D("100.05")),
        quotes,
        profiles,
        _policy(excluded_venues=frozenset({"EXCLUDED"}), max_latency_seconds=D("0.1")),
    )
    assert _status(decision, "EXCLUDED") is CandidateStatus.EXCLUDED
    for venue in ("SLOW", "EURO", "PRICEY", "THIN"):
        assert _status(decision, venue) is CandidateStatus.INELIGIBLE, venue
    assert [leg.venue for leg in decision.legs] == ["OK"]


def test_a_missing_capability_rules_a_venue_out_and_an_undeclared_one_leaves_it_unassessed() -> (
    None
):
    quotes = [_quote("NO-IOC"), _quote("SILENT")]
    silent = dataclasses.replace(_declaration(), accounts=())
    profiles = [
        _profile("NO-IOC", declaration=_declaration(frozenset({TimeInForce.DAY}))),
        _profile("SILENT", declaration=silent),
    ]
    decision = select_route(_request(), quotes, profiles, _policy())
    assert _status(decision, "NO-IOC") is CandidateStatus.INELIGIBLE
    assert _status(decision, "SILENT") is CandidateStatus.INSUFFICIENT_EVIDENCE
    assert decision.status is RouteStatus.INSUFFICIENT_EVIDENCE
    assert all(c.compatibility is not None for c in decision.candidates)


# --------------------------------------------------------------------------- #
# Evidence that is missing is never filled in
# --------------------------------------------------------------------------- #


class _RefusingSpread:
    def half_spread(self, context: CostContext) -> Decimal:
        raise ExecutionValidationError("no spread was observed for this venue")


@pytest.mark.parametrize(
    ("quote", "profile", "why"),
    [
        (_quote("V", ask=None), _profile("V"), "lacks a side"),
        (_quote("V", size=None), _profile("V"), "no size"),
        (_quote("V", as_of=AT - 60), _profile("V"), "old"),
        (_quote("V", as_of=AT + 1), _profile("V"), "look-ahead"),
        (None, _profile("V"), "No quote"),
        (_quote("V"), None, "No profile"),
        (
            _quote("V"),
            _profile("V", costs=dataclasses.replace(_costs(), spread_model=_RefusingSpread())),
            "could not price",
        ),
    ],
)
def test_missing_evidence_leaves_a_venue_unassessed(
    quote: VenueQuote | None, profile: VenueProfile | None, why: str
) -> None:
    decision = select_route(
        _request(),
        [] if quote is None else [quote],
        [] if profile is None else [profile],
        _policy(),
    )
    (candidate,) = decision.candidates
    assert candidate.status is CandidateStatus.INSUFFICIENT_EVIDENCE
    assert why in candidate.reason
    assert decision.status is RouteStatus.INSUFFICIENT_EVIDENCE
    assert decision.legs == ()


def test_an_unknown_latency_cannot_be_shown_to_meet_a_cap() -> None:
    decision = select_route(
        _request(),
        [_quote("V")],
        [_profile("V", latency=None)],
        _policy(max_latency_seconds=D("1")),
    )
    assert _status(decision, "V") is CandidateStatus.INSUFFICIENT_EVIDENCE


def test_infeasible_means_every_venue_was_positively_ruled_out() -> None:
    decision = select_route(
        _request(), [_quote("EURO", currency="EUR")], [_profile("EURO")], _policy()
    )
    assert decision.status is RouteStatus.INFEASIBLE


def test_nothing_at_all_is_insufficient_evidence_not_infeasible() -> None:
    assert select_route(_request(), [], [], _policy()).status is RouteStatus.INFEASIBLE
    decision = select_route(_request(), [_quote("A")], [], _policy())
    assert decision.status is RouteStatus.INSUFFICIENT_EVIDENCE


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


def test_ties_break_by_latency_then_by_venue_identifier_and_unknown_latency_last() -> None:
    quotes = [_quote(v) for v in ("C", "B", "A", "Z")]
    profiles = [
        _profile("C", latency="0.001"),
        _profile("B", latency="0.001"),
        _profile("A", latency="0.002"),
        _profile("Z", latency=None),
    ]
    decision = select_route(_request(quantity="10"), quotes, profiles, _policy())
    ranking = sorted((c.rank, c.venue) for c in decision.candidates if c.rank is not None)
    assert [venue for _, venue in ranking] == ["B", "C", "A", "Z"]
    assert decision.legs[0].venue == "B"
    assert decision.policy.tie_break == (
        "objective_price",
        "latency_unknown_last",
        "venue_identifier",
    )


def test_the_decision_does_not_depend_on_listing_order() -> None:
    quotes = [
        _quote("A", size="200"),
        _quote("B", ask="100.01", bid="99.99", size="200"),
        _quote("C"),
    ]
    profiles = [_profile("A"), _profile("B"), _profile("C", latency="0.01")]
    first = select_route(_request(), quotes, profiles, _policy(allow_split=True))
    second = select_route(_request(), quotes[::-1], profiles[::-1], _policy(allow_split=True))
    assert first == second
    assert first.decision_id == second.decision_id
    other = select_route(_request(quantity="501"), quotes, profiles, _policy(allow_split=True))
    assert other.decision_id != first.decision_id


def test_the_policy_identity_sees_every_field() -> None:
    base = _policy()
    variants = [
        _policy(objective=RoutingObjective.BEST_QUOTED_PRICE),
        _policy(max_quote_age_seconds=6.0),
        _policy(max_latency_seconds=D("1")),
        _policy(allow_split=True),
        _policy(allow_partial=True),
        _policy(excluded_venues=frozenset({"X"})),
    ]
    assert len({base.policy_id, *(v.policy_id for v in variants)}) == 1 + len(variants)


def test_the_explanation_names_every_venue_and_the_verdict() -> None:
    decision = select_route(
        _request(),
        [_quote("A"), _quote("B", currency="EUR")],
        [_profile("A"), _profile("B")],
        _policy(),
    )
    lines = decision.explanation()
    assert lines[0].startswith("routed:")
    assert any(line.startswith("#1 A: eligible") for line in lines)
    assert any("B: ineligible" in line for line in lines)


# --------------------------------------------------------------------------- #
# Inputs that cannot be evidence
# --------------------------------------------------------------------------- #


def test_evidence_that_contradicts_itself_is_refused() -> None:
    with pytest.raises(ExecutionValidationError, match="one venue, one quote"):
        select_route(_request(), [_quote("A"), _quote("A")], [_profile("A")], _policy())
    with pytest.raises(ExecutionValidationError, match="one venue, one profile"):
        select_route(_request(), [_quote("A")], [_profile("A"), _profile("A")], _policy())
    with pytest.raises(ExecutionValidationError, match="is for OTHER"):
        select_route(
            _request(), [dataclasses.replace(_quote("A"), asset_id="OTHER")], [], _policy()
        )
    with pytest.raises(ExecutionValidationError, match="crossed"):
        _quote("A", bid="100.05", ask="100.00")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"quantity": D("0")}, "routes nothing"),
        ({"quantity_increment": D("0")}, "not a unit"),
        ({"limit_price": D("-1")}, "not a price"),
        ({"decided_at": float("nan")}, "not an instant"),
    ],
)
def test_a_request_that_cannot_be_routed_is_refused(
    overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ExecutionValidationError, match=message):
        _request(**overrides)


def test_a_policy_budget_cannot_be_negative() -> None:
    with pytest.raises(ExecutionValidationError, match="stale"):
        _policy(max_quote_age_seconds=-1.0)
    with pytest.raises(ExecutionValidationError, match="negative"):
        _policy(max_latency_seconds=D("-1"))
