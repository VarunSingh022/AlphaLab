"""The universal capability model: declarations, scope, the tri-state, and compatibility."""

from __future__ import annotations

import dataclasses
import json
from decimal import Decimal
from typing import Any

import pytest

from alphalab.core.capabilities import (
    ANY_LISTING_VENUE,
    CAPABILITY_LEVELS,
    ORDER_TYPE_CAPABILITIES,
    VENUE_FEATURES,
    AccountCapability,
    Capability,
    CapabilityDeclaration,
    CapabilityLevel,
    Compatibility,
    ExecutionRequirements,
    MarketCapability,
    RequirementDimension,
    Support,
    check_compatibility,
    order_requirements,
    supports,
)
from alphalab.core.enums import AssetType, OrderType, Side, TimeInForce
from alphalab.core.exceptions import DomainValidationError
from alphalab.persistence.serializer import serialize

A = AssetType
T = OrderType
F = TimeInForce
YES, NO, SILENT = Support.SUPPORTED, Support.UNSUPPORTED, Support.UNDECLARED


_EQUITIES = MarketCapability(
    asset_class=A.EQUITY,
    listing_venue=ANY_LISTING_VENUE,
    order_types=frozenset({T.MARKET, T.LIMIT, T.STOP, T.STOP_LIMIT}),
    time_in_force=frozenset({F.DAY, F.GTC, F.IOC}),
    short_selling=YES,
    fractional_quantities=YES,
    extended_hours=YES,
    bracket_orders=YES,
)

_ACCOUNT = AccountCapability(
    account_id="ACC-1",
    asset_classes=frozenset({A.EQUITY, A.OPTION}),
    margin=YES,
    short_selling=YES,
)

_REQUIREMENTS = ExecutionRequirements(
    asset_class=A.EQUITY,
    listing_venue="XNAS",
    account_id="ACC-1",
    order_types=frozenset({T.LIMIT}),
    time_in_force=frozenset({F.DAY}),
    short_selling=False,
    fractional_quantities=False,
    extended_hours=False,
    bracket_orders=False,
    margin=False,
    features=frozenset(),
)


def _equities(venue: str = ANY_LISTING_VENUE, **overrides: Any) -> MarketCapability:
    return dataclasses.replace(_EQUITIES, listing_venue=venue, **overrides)


def _options() -> MarketCapability:
    return MarketCapability(
        asset_class=A.OPTION,
        listing_venue=ANY_LISTING_VENUE,
        order_types=frozenset({T.LIMIT}),
        time_in_force=frozenset({F.DAY}),
        short_selling=NO,
        fractional_quantities=NO,
        extended_hours=NO,
        bracket_orders=NO,
    )


def _account(**overrides: Any) -> AccountCapability:
    return dataclasses.replace(_ACCOUNT, **overrides)


def _declaration(**overrides: Any) -> CapabilityDeclaration:
    base = CapabilityDeclaration(
        adapter_id="adapter-a",
        supported_features=frozenset({Capability.CANCEL_REPLACE, Capability.STREAMING}),
        unsupported_features=frozenset(),
        markets=(_equities(), _options()),
        unsupported_asset_classes=frozenset({A.FUTURE}),
        accounts=(_account(),),
    )
    return dataclasses.replace(base, **overrides)


def _requirements(**overrides: Any) -> ExecutionRequirements:
    return dataclasses.replace(_REQUIREMENTS, **overrides)


# --------------------------------------------------------------------------- #
# The vocabulary and its levels
# --------------------------------------------------------------------------- #


def test_every_capability_the_contract_names_exists_and_has_a_level() -> None:
    assert {c.name for c in Capability} >= {
        "MARKET_ORDERS",
        "LIMIT_ORDERS",
        "STOP_ORDERS",
        "SHORTING",
        "OPTIONS",
        "FUTURES",
        "FRACTIONAL",
        "MARGIN",
        "STREAMING",
        "CANCEL_REPLACE",
        "BRACKET_ORDERS",
        "EXTENDED_HOURS",
    }
    assert set(CAPABILITY_LEVELS) == set(Capability)
    assert {Capability.STREAMING, Capability.CANCEL_REPLACE} == VENUE_FEATURES
    assert CAPABILITY_LEVELS[Capability.SHORTING] == (
        CapabilityLevel.MARKET,
        CapabilityLevel.ACCOUNT,
    )
    assert CAPABILITY_LEVELS[Capability.MARGIN] == (CapabilityLevel.ACCOUNT,)
    assert set(ORDER_TYPE_CAPABILITIES) == set(OrderType)


# --------------------------------------------------------------------------- #
# Declarations refuse what cannot be true
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"adapter_id": " "}, "adapter_id cannot be empty"),
        ({"supported_features": frozenset({Capability.MARGIN})}, "not venue-level"),
        (
            {
                "supported_features": frozenset({Capability.STREAMING}),
                "unsupported_features": frozenset({Capability.STREAMING}),
            },
            "both supported and unsupported",
        ),
        ({"markets": (_equities(), _equities())}, "declared twice"),
        ({"unsupported_asset_classes": frozenset({A.EQUITY})}, "does not offer"),
        ({"accounts": (_account(), _account())}, "declared twice"),
    ],
)
def test_a_contradictory_declaration_is_refused(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(DomainValidationError, match=message):
        _declaration(**overrides)


def test_a_market_that_accepts_no_order_is_refused() -> None:
    with pytest.raises(DomainValidationError, match="no order type"):
        _equities(order_types=frozenset())
    with pytest.raises(DomainValidationError, match="no time-in-force"):
        _equities(time_in_force=frozenset())
    with pytest.raises(DomainValidationError, match="listing_venue"):
        _equities(venue="")


def test_entries_are_held_sorted_so_listing_order_is_not_identity() -> None:
    forward = _declaration(markets=(_equities(), _equities("XNAS"), _options()))
    backward = _declaration(markets=(_options(), _equities("XNAS"), _equities()))
    assert forward == backward
    assert forward.declaration_id == backward.declaration_id
    assert [m.key for m in forward.markets] == sorted(m.key for m in forward.markets)


@pytest.mark.parametrize(
    "overrides",
    [
        {"adapter_id": "adapter-b"},
        {"supported_features": frozenset({Capability.STREAMING})},
        {"unsupported_asset_classes": frozenset()},
        {"markets": (_equities(short_selling=NO), _options())},
        {"markets": (_equities(),)},
        {"accounts": (_account(margin=NO),)},
        {"accounts": ()},
    ],
)
def test_every_field_is_in_the_identity(overrides: dict[str, Any]) -> None:
    assert _declaration(**overrides).declaration_id != _declaration().declaration_id


def test_an_exact_venue_entry_wins_over_the_any_venue_entry() -> None:
    declaration = _declaration(
        markets=(_equities(), _equities("XLON", order_types=frozenset({T.LIMIT})), _options())
    )
    london = declaration.market_for(A.EQUITY, "XLON")
    elsewhere = declaration.market_for(A.EQUITY, "XNAS")
    assert london is not None and london.listing_venue == "XLON"
    assert elsewhere is not None and elsewhere.listing_venue == ANY_LISTING_VENUE
    assert declaration.market_for(A.CRYPTO, "XNAS") is None


def test_serialization_is_deterministic_and_sorted() -> None:
    declaration = _declaration()
    payload = json.loads(serialize(declaration))
    assert payload["supported_features"] == ["cancel_replace", "streaming"]
    assert payload["markets"][0]["order_types"] == sorted(payload["markets"][0]["order_types"])
    assert payload["declaration_id"] == declaration.declaration_id
    assert serialize(declaration) == serialize(dataclasses.replace(declaration))


# --------------------------------------------------------------------------- #
# supports(): one capability, one scope, three answers
# --------------------------------------------------------------------------- #


def test_venue_features_are_answered_by_the_connection() -> None:
    declaration = _declaration(
        supported_features=frozenset({Capability.CANCEL_REPLACE}),
        unsupported_features=frozenset({Capability.STREAMING}),
    )
    assert supports(declaration, Capability.CANCEL_REPLACE) is YES
    assert supports(declaration, Capability.STREAMING) is NO
    silent = _declaration(supported_features=frozenset())
    assert supports(silent, Capability.STREAMING) is SILENT


@pytest.mark.parametrize(
    ("capability", "venue", "expected"),
    [
        (Capability.MARKET_ORDERS, "XNAS", YES),
        (Capability.LIMIT_ORDERS, "XNAS", YES),
        (Capability.STOP_ORDERS, "XNAS", YES),
        (Capability.FRACTIONAL, "XNAS", YES),
        (Capability.EXTENDED_HOURS, "XNAS", YES),
        (Capability.BRACKET_ORDERS, "XNAS", YES),
    ],
)
def test_market_capabilities_are_answered_per_market(
    capability: Capability, venue: str, expected: Support
) -> None:
    assert supports(_declaration(), capability, asset_class=A.EQUITY, listing_venue=venue) is (
        expected
    )


def test_an_order_type_absent_from_a_declared_market_is_unsupported() -> None:
    assert (
        supports(
            _declaration(),
            Capability.MARKET_ORDERS,
            asset_class=A.OPTION,
            listing_venue="OPRA",
        )
        is NO
    )


def test_an_undeclared_market_is_undeclared_and_a_withdrawn_one_unsupported() -> None:
    declaration = _declaration()
    assert (
        supports(declaration, Capability.LIMIT_ORDERS, asset_class=A.CRYPTO, listing_venue="X")
        is SILENT
    )
    assert (
        supports(declaration, Capability.LIMIT_ORDERS, asset_class=A.FUTURE, listing_venue="X")
        is NO
    )


def test_shorting_needs_the_market_and_the_account() -> None:
    def shorting(market: Support, account: Support, account_id: str = "ACC-1") -> Support:
        declaration = _declaration(
            markets=(_equities(short_selling=market),),
            accounts=(_account(short_selling=account),),
        )
        return supports(
            declaration,
            Capability.SHORTING,
            asset_class=A.EQUITY,
            listing_venue="XNAS",
            account_id=account_id,
        )

    assert shorting(YES, YES) is YES
    assert shorting(YES, NO) is NO
    assert shorting(NO, YES) is NO
    assert shorting(YES, SILENT) is SILENT
    assert shorting(SILENT, NO) is NO, "an explicit no outranks silence"
    assert shorting(YES, YES, account_id="ACC-UNKNOWN") is SILENT


def test_options_and_futures_are_answered_from_the_class_they_name() -> None:
    declaration = _declaration()
    assert supports(declaration, Capability.OPTIONS, listing_venue="OPRA", account_id="ACC-1") is (
        YES
    )
    assert supports(declaration, Capability.FUTURES, listing_venue="XCME", account_id="ACC-1") is (
        NO
    )
    narrow = _declaration(accounts=(_account(asset_classes=frozenset({A.EQUITY})),))
    assert supports(narrow, Capability.OPTIONS, listing_venue="OPRA", account_id="ACC-1") is NO
    with pytest.raises(DomainValidationError, match="contradicts"):
        supports(
            declaration,
            Capability.OPTIONS,
            asset_class=A.EQUITY,
            listing_venue="OPRA",
            account_id="ACC-1",
        )


def test_margin_is_an_account_permission() -> None:
    assert supports(_declaration(), Capability.MARGIN, account_id="ACC-1") is YES
    no_margin = _declaration(accounts=(_account(margin=NO),))
    assert supports(no_margin, Capability.MARGIN, account_id="ACC-1") is NO
    assert supports(_declaration(), Capability.MARGIN, account_id="NOBODY") is SILENT


def test_a_capability_asked_without_the_scope_it_needs_is_refused() -> None:
    with pytest.raises(DomainValidationError, match="per market"):
        supports(_declaration(), Capability.LIMIT_ORDERS)
    with pytest.raises(DomainValidationError, match="per account"):
        supports(_declaration(), Capability.MARGIN)
    with pytest.raises(DomainValidationError, match="per account"):
        supports(_declaration(), Capability.SHORTING, asset_class=A.EQUITY, listing_venue="X")


# --------------------------------------------------------------------------- #
# Compatibility
# --------------------------------------------------------------------------- #


def test_a_supported_order_is_compatible_and_every_check_is_listed() -> None:
    report = check_compatibility(_requirements(), _declaration())
    assert report.status is Compatibility.COMPATIBLE
    assert report.compatible
    assert report.gaps == ()
    dimensions = [check.dimension for check in report.checks]
    assert dimensions == [
        RequirementDimension.MARKET,
        RequirementDimension.ORDER_TYPE,
        RequirementDimension.TIME_IN_FORCE,
        RequirementDimension.ACCOUNT,
    ]


def test_an_order_type_the_market_refuses_is_incompatible() -> None:
    report = check_compatibility(
        _requirements(asset_class=A.OPTION, order_types=frozenset({T.MARKET})), _declaration()
    )
    assert report.status is Compatibility.INCOMPATIBLE
    assert [check.requirement for check in report.unsupported] == ["order_type=market"]


def test_anything_undeclared_is_undetermined_never_compatible() -> None:
    silent_market = check_compatibility(
        _requirements(asset_class=A.CRYPTO),
        _declaration(accounts=(_account(asset_classes=frozenset({A.EQUITY, A.CRYPTO})),)),
    )
    silent_account = check_compatibility(_requirements(account_id="ACC-9"), _declaration())
    silent_feature = check_compatibility(
        _requirements(features=frozenset({Capability.STREAMING})),
        _declaration(supported_features=frozenset()),
    )
    silent_flag = check_compatibility(
        _requirements(extended_hours=True),
        _declaration(markets=(_equities(extended_hours=SILENT),)),
    )
    for report in (silent_market, silent_account, silent_feature, silent_flag):
        assert report.status is Compatibility.UNDETERMINED
        assert not report.compatible
        assert report.undeclared
        assert not report.unsupported


def test_unsupported_outranks_undeclared() -> None:
    report = check_compatibility(
        _requirements(order_types=frozenset({T.STOP}), account_id="ACC-9"),
        _declaration(markets=(_equities(order_types=frozenset({T.LIMIT})),)),
    )
    assert report.status is Compatibility.INCOMPATIBLE
    assert report.unsupported and report.undeclared


def test_a_withdrawn_asset_class_makes_every_market_requirement_unsupported() -> None:
    report = check_compatibility(
        _requirements(asset_class=A.FUTURE, order_types=frozenset({T.LIMIT, T.MARKET})),
        _declaration(),
    )
    market_level = [c for c in report.checks if c.level is CapabilityLevel.MARKET]
    assert market_level and all(c.support is NO for c in market_level)


def test_capability_combinations_report_each_requirement_separately() -> None:
    requirements = _requirements(
        order_types=frozenset({T.LIMIT, T.STOP_LIMIT}),
        time_in_force=frozenset({F.DAY, F.FOK}),
        short_selling=True,
        fractional_quantities=True,
        extended_hours=True,
        bracket_orders=True,
        margin=True,
        features=frozenset({Capability.CANCEL_REPLACE}),
    )
    declaration = _declaration(
        markets=(
            _equities(
                order_types=frozenset({T.LIMIT}),
                time_in_force=frozenset({F.DAY}),
                fractional_quantities=NO,
            ),
        ),
        accounts=(_account(margin=SILENT),),
    )
    report = check_compatibility(requirements, declaration)
    unsupported = {check.requirement for check in report.unsupported}
    undeclared = {check.requirement for check in report.undeclared}
    assert unsupported == {
        "order_type=stop_limit",
        "time_in_force=fill_or_kill",
        "fractional_quantities",
    }
    assert undeclared == {"margin"}
    assert report.status is Compatibility.INCOMPATIBLE
    short_checks = [c for c in report.checks if c.capability is Capability.SHORTING]
    assert {c.level for c in short_checks} == {CapabilityLevel.MARKET, CapabilityLevel.ACCOUNT}


def test_the_report_is_deterministic_and_identified() -> None:
    first = check_compatibility(_requirements(), _declaration())
    second = check_compatibility(_requirements(), _declaration())
    assert first == second
    assert first.report_id == second.report_id
    other = check_compatibility(_requirements(order_types=frozenset({T.STOP})), _declaration())
    assert other.report_id != first.report_id
    payload = json.loads(serialize(first))
    assert payload["status"] == "compatible"
    assert payload["report_id"] == first.report_id


def test_requirements_refuse_what_names_nothing() -> None:
    with pytest.raises(DomainValidationError, match="no order type"):
        _requirements(order_types=frozenset())
    with pytest.raises(DomainValidationError, match="no time-in-force"):
        _requirements(time_in_force=frozenset())
    with pytest.raises(DomainValidationError, match="not venue-level"):
        _requirements(features=frozenset({Capability.MARGIN}))
    with pytest.raises(DomainValidationError, match="account_id"):
        _requirements(account_id="")


# --------------------------------------------------------------------------- #
# Requirements derived from one order
# --------------------------------------------------------------------------- #


def _derived(side: Side, quantity: str, position: str) -> ExecutionRequirements:
    return order_requirements(
        asset_class=A.EQUITY,
        listing_venue="XNAS",
        account_id="ACC-1",
        order_type=T.LIMIT,
        time_in_force=F.DAY,
        side=side,
        quantity=Decimal(quantity),
        position_before=Decimal(position),
        extended_hours=False,
        bracket_orders=False,
        margin=False,
        features=frozenset(),
    )


def test_a_sale_beyond_the_position_needs_shorting_and_one_within_it_does_not() -> None:
    assert _derived(Side.SELL, "10", "5").short_selling
    assert not _derived(Side.SELL, "5", "5").short_selling
    assert not _derived(Side.BUY, "10", "-50").short_selling
    assert _derived(Side.SELL, "1", "0").short_selling


def test_a_fractional_quantity_needs_fractional_support() -> None:
    assert _derived(Side.BUY, "10.5", "0").fractional_quantities
    assert not _derived(Side.BUY, "10.000", "0").fractional_quantities


def test_an_order_for_nothing_is_refused() -> None:
    with pytest.raises(DomainValidationError, match="not an order"):
        _derived(Side.BUY, "0", "0")
