"""
AlphaLab Examples
=================

Example 61 : A Universal Broker Capability Model

Difficulty : Intermediate

Estimated Time : 10 minutes

Prerequisites
-------------

✓ Example 05 (the broker boundary)
✓ Example 42 (deployment specifications and broker capabilities)

Topics
------

• Three adapters' capabilities declared at the level each is true -- the
  venue connection, a market (asset class on a listing venue), an account
• Three answers, not two: SUPPORTED, UNSUPPORTED and UNDECLARED, and why
  "nobody said" is never read as "yes"
• What an order needs, derived from the order where the order decides it --
  a short sale, a fractional quantity -- and checked at every level
• A compatibility report per venue: COMPATIBLE, INCOMPATIBLE or UNDETERMINED,
  each check named with its dimension, level and reason
• Declarations refused when they contradict themselves
• Content identities that ignore listing order and change with any answer
• The v3.5 deployment record projected from the same declaration, so an
  application declares once -- and a projection refused where it would have
  to invent an answer

What this shows
---------------

"Does this broker support short selling?" has no single answer: a broker may
permit it on equities and not on options, and an account may not be permitted
it at all. A capability declaration therefore states each answer where it is
true, and a compatibility check reads each requirement at the level that
decides it. An answer nobody gave is UNDECLARED, and a check that meets one is
UNDETERMINED -- never compatible. The adapters are labels an application
chooses: AlphaLab names no vendor, queries nothing and holds no credential.

Run

    python examples/61_broker_capability_model.py
"""

from dataclasses import replace
from decimal import Decimal

from _execution_world import (
    ACCOUNT_ID,
    DECLARATIONS,
    EAST,
    LISTING,
    NO,
    NORTH,
    SOUTH,
    YES,
    banner,
    refusal,
    say,
    section,
    short,
)

from alphalab.broker import (
    ANY_LISTING_VENUE,
    AccountCapability,
    Capability,
    CapabilityDeclaration,
    CompatibilityReport,
    ExecutionRequirements,
    MarketCapability,
    check_compatibility,
    order_requirements,
    supports,
)
from alphalab.core import DomainValidationError
from alphalab.core.enums import AssetType, OrderType, Side, TimeInForce
from alphalab.lifecycle import LifecycleInputError, broker_capabilities_from


def requirement(
    *,
    side: Side = Side.BUY,
    quantity: str = "1500",
    order_type: OrderType = OrderType.LIMIT,
    asset_class: AssetType = AssetType.EQUITY,
    position_before: str = "0",
    margin: bool = False,
    extended_hours: bool = False,
    features: frozenset[Capability] = frozenset(),
) -> ExecutionRequirements:
    """What one order needs. Shorting and fractional quantities are derived, not stated."""

    return order_requirements(
        asset_class=asset_class,
        listing_venue=LISTING,
        account_id=ACCOUNT_ID,
        order_type=order_type,
        time_in_force=TimeInForce.DAY,
        side=side,
        quantity=Decimal(quantity),
        position_before=Decimal(position_before),
        extended_hours=extended_hours,
        bracket_orders=False,
        margin=margin,
        features=features,
    )


ORDERS = {
    "buy 1,500 at a limit": requirement(),
    "sell 800 short, limit": requirement(side=Side.SELL, quantity="800"),
    "sell 800 of 2,000 held": requirement(side=Side.SELL, quantity="800", position_before="2000"),
    "buy 12.5 at market": requirement(quantity="12.5", order_type=OrderType.MARKET),
    "buy 1,500 on margin": requirement(margin=True),
    "buy after the close": requirement(extended_hours=True),
    "limit, streamed reports": requirement(features=frozenset({Capability.STREAMING})),
    "buy 10 option contracts": requirement(quantity="10", asset_class=AssetType.OPTION),
    "buy 2 futures": requirement(quantity="2", asset_class=AssetType.FUTURE),
}


def declarations() -> None:
    section("Three adapters, each declared where its answers are true")
    for venue, declared in DECLARATIONS.items():
        (market,) = declared.markets
        (account,) = declared.accounts
        print(f"  {venue:<12} adapter {declared.adapter_id!r}, id {short(declared.declaration_id)}")
        features = ", ".join(
            f"{feature}={declared.feature(feature)}"
            for feature in (Capability.STREAMING, Capability.CANCEL_REPLACE)
        )
        print(f"    venue    {features}")
        types = ", ".join(sorted(str(order_type) for order_type in market.order_types))
        print(
            f"    market   {market.asset_class} on {market.listing_venue!r}: {types}; "
            f"short sales {market.short_selling}"
        )
        print(
            f"    account  {account.account_id}: margin {account.margin}, "
            f"short sales {account.short_selling}"
        )
    say(
        "The market entry covers equities on every listing venue ('*'). Options are declared "
        "absent -- UNSUPPORTED. The account may trade futures, but no adapter declares a "
        "futures market, so every futures question is UNDECLARED. SOUTH's adapter says nothing "
        "about short sales: its answer is UNDECLARED, a value a declarer can state as well as "
        "the default for silence."
    )


def status_table() -> dict[str, dict[str, CompatibilityReport]]:
    section("What each order needs, checked against each venue")
    reports = {
        label: {
            venue: check_compatibility(needs, declared) for venue, declared in DECLARATIONS.items()
        }
        for label, needs in ORDERS.items()
    }
    print(f"  {'order':<26}" + "".join(f"{venue:>15}" for venue in DECLARATIONS))
    for label, by_venue in reports.items():
        print(f"  {label:<26}" + "".join(f"{r.status!s:>15}" for r in by_venue.values()))
    print()
    needs = ORDERS["sell 800 short, limit"]
    held = ORDERS["sell 800 of 2,000 held"]
    print(f"  derived: 'sell 800 short' needs short selling = {needs.short_selling}")
    print(f"  derived: 'sell 800 of 2,000 held' needs short selling = {held.short_selling}")
    print(
        "  derived: 'buy 12.5' needs fractional quantities = "
        f"{ORDERS['buy 12.5 at market'].fractional_quantities}"
    )
    return reports


def why(reports: dict[str, dict[str, CompatibilityReport]]) -> None:
    section("Why, check by check")
    for label, venue in (
        ("sell 800 short, limit", "VENUE-SOUTH"),
        ("buy 1,500 at a limit", "VENUE-EAST"),
        ("buy 1,500 on margin", "VENUE-NORTH"),
        ("buy 2 futures", "VENUE-NORTH"),
    ):
        report = reports[label][venue]
        print(f"  {label} at {venue}: {report.status}")
        for check in report.checks:
            if check.support is not YES:
                print(f"    {check.dimension}/{check.level} {check.requirement}: {check.support}")
                print(f"      {check.detail}")
    say(
        "A short sale is decided twice -- the market must permit it and so must the account -- "
        "and margin is the account's alone. Only an all-SUPPORTED report is COMPATIBLE; one "
        "UNSUPPORTED answer makes it INCOMPATIBLE, and UNDECLARED with nothing unsupported makes "
        "it UNDETERMINED. Nothing is sent on an UNDETERMINED report: the runtime's routing gate "
        "refuses it as it refuses INCOMPATIBLE, and route selection reports that venue as "
        "lacking evidence rather than as ruled out (example 64)."
    )


def flat_questions() -> None:
    section("One flat question, answered for a stated scope")
    print(f"  {'equities on XNAS':<16}" + "".join(f"{venue:>13}" for venue in DECLARATIONS))
    for capability in (
        Capability.LIMIT_ORDERS,
        Capability.SHORTING,
        Capability.MARGIN,
        Capability.STREAMING,
        Capability.OPTIONS,
        Capability.FUTURES,
    ):
        answers = []
        for declared in (NORTH, SOUTH, EAST):
            if capability in (Capability.OPTIONS, Capability.FUTURES):
                answer = supports(
                    declared, capability, listing_venue=LISTING, account_id=ACCOUNT_ID
                )
            else:
                answer = supports(
                    declared,
                    capability,
                    asset_class=AssetType.EQUITY,
                    listing_venue=LISTING,
                    account_id=ACCOUNT_ID,
                )
            answers.append(f"{answer!s:>13}")
        print(f"  {capability:<16}" + "".join(answers))
    try:
        supports(NORTH, Capability.SHORTING)
    except DomainValidationError as error:
        refusal("shorting, with no market or account named", error)


def contradictions() -> None:
    section("Declarations that contradict themselves are refused")
    (market,) = NORTH.markets
    try:
        replace(NORTH, supported_features=frozenset({Capability.SHORTING}))
    except DomainValidationError as error:
        refusal("short selling declared for the whole connection", error)
    try:
        replace(NORTH, markets=(market, replace(market, short_selling=NO)))
    except DomainValidationError as error:
        refusal("one market declared twice, with two answers", error)
    try:
        replace(NORTH, unsupported_asset_classes=frozenset({AssetType.EQUITY}))
    except DomainValidationError as error:
        refusal("equities declared as a market and as not offered", error)


def identities() -> None:
    section("Identity: what is declared, not how it was listed")
    extra = AccountCapability("DESK-ACCOUNT-2", frozenset({AssetType.EQUITY}), NO, NO)
    futures = MarketCapability(
        AssetType.FUTURE,
        ANY_LISTING_VENUE,
        frozenset({OrderType.LIMIT}),
        frozenset({TimeInForce.DAY}),
        NO,
        NO,
        NO,
        NO,
    )
    one = replace(NORTH, markets=(*NORTH.markets, futures), accounts=(*NORTH.accounts, extra))
    other = CapabilityDeclaration(
        adapter_id=NORTH.adapter_id,
        supported_features=NORTH.supported_features,
        unsupported_features=NORTH.unsupported_features,
        markets=(futures, *NORTH.markets),
        unsupported_asset_classes=NORTH.unsupported_asset_classes,
        accounts=(extra, *NORTH.accounts),
    )
    changed = replace(one, accounts=(replace(extra, short_selling=YES), *NORTH.accounts))
    print(f"  entries listed one way     {short(one.declaration_id)}")
    print(f"  the same, listed reversed  {short(other.declaration_id)}")
    print(f"  one answer changed         {short(changed.declaration_id)}")
    report = check_compatibility(ORDERS["buy 1,500 at a limit"], NORTH)
    again = check_compatibility(requirement(), NORTH)
    print(f"  a report, and its rerun    {short(report.report_id)}  {short(again.report_id)}")
    say(
        "Every identity is a SHA-256 over a canonical rendering with a scheme tag, sets sorted "
        "and strings quoted -- the construction every AlphaLab identity uses -- so it is the "
        "same in every process, whatever the hash seed."
    )


def declared_once() -> None:
    section("Declared once: the v3.5 deployment record, projected")
    record = broker_capabilities_from(
        NORTH,
        listing_venues=frozenset({LISTING}),
        asset_classes=frozenset({AssetType.EQUITY}),
        account_id=ACCOUNT_ID,
    )
    types = ", ".join(sorted(str(order_type) for order_type in record.order_types))
    print(f"  NORTH as a BrokerCapabilities: {types}")
    print(
        f"    short selling {record.short_selling}, fractional {record.fractional_quantities}, "
        f"asset classes {sorted(str(a) for a in record.asset_classes)}"
    )
    try:
        broker_capabilities_from(
            SOUTH,
            listing_venues=frozenset({LISTING}),
            asset_classes=frozenset({AssetType.EQUITY}),
            account_id=ACCOUNT_ID,
        )
    except LifecycleInputError as error:
        refusal("SOUTH as a BrokerCapabilities", error)
    say(
        "The v3.5 record is broker-wide and two-valued, so it cannot say 'unknown'. Projecting "
        "SOUTH's silence as either True or False would invent an answer its adapter never gave; "
        "the projection names the gap instead, and SOUTH's declaration is what has to change."
    )


def main() -> None:
    banner(61, "A Universal Broker Capability Model")
    declarations()
    reports = status_table()
    why(reports)
    flat_questions()
    contradictions()
    identities()
    declared_once()
    print()


if __name__ == "__main__":
    main()
