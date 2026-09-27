"""What a broker can do, declared at the level it is true, and checked against what an order needs.

v3.5 gave a deployment specification a capability contract --
:class:`~alphalab.lifecycle.specification.BrokerRequirements` against
:class:`~alphalab.lifecycle.specification.BrokerCapabilities` -- and it answers
the question it was built for: can this broker run this *deployment*? It cannot
answer the question an order asks, for three reasons that are all about scope:

* it is **one broker-wide record**. "Supports stop orders" is true of a broker's
  equities and false of its options at most brokers, and a single set of order
  types either overstates one or understates the other;
* it has **no account**. Whether an account may sell short or trade on margin is
  a permission of the account, not a feature of the broker;
* it is **two-valued**. A capability nobody declared and one declared absent are
  both ``False``, so "the broker says no" and "nobody said" read the same.

This module is the v3.9 answer, and it lives in :mod:`alphalab.core` rather than
beside the v3.5 types for the reason ADR-0008 gives about every canonical type:
the execution contract that reads it -- the broker boundary, the routing and
algorithm semantics in :mod:`alphalab.execution`, the runtime's routing gate and
the lifecycle's portability check -- sits on both sides of the package graph,
and only a type in ``core`` can be read by all of them. The v3.5 record is now a
*projection* of a declaration here
(:func:`alphalab.lifecycle.specification.broker_capabilities_from`), so an
application declares once.

Three levels, kept apart
------------------------

=========== ================================================================
**Venue**   The adapter's connection as a whole: whether it pushes execution
            reports (``STREAMING``) and whether a working order can be
            amended in place (``CANCEL_REPLACE``).
**Market**  One asset class, on one listing venue or on every venue of that
            class (:data:`ANY_LISTING_VENUE`): the order types and
            times-in-force it accepts, and whether it permits short sales,
            fractional quantities, extended hours and bracket orders.
**Account** One account's permissions: the asset classes it may trade, and
            whether it may borrow (margin) or sell short.
=========== ================================================================

A capability that spans levels is checked at each: a short sale needs a market
that permits shorting *and* an account permitted to do it. :data:`CAPABILITY_LEVELS`
states where each capability in the flat vocabulary is decided.

Nothing absent is supported
---------------------------

Every declared answer is a :class:`Support`, and ``UNDECLARED`` is a value a
declarer can state as well as the answer for anything not mentioned. A market
nobody declared is ``UNDECLARED``; an asset class the broker says it does not
offer is ``UNSUPPORTED``; an order type missing from a declared market's list is
``UNSUPPORTED``, because a market states every order type it accepts. A
compatibility check is ``COMPATIBLE`` only when every requirement is
``SUPPORTED``; anything ``UNSUPPORTED`` makes it ``INCOMPATIBLE``, and anything
``UNDECLARED`` with nothing unsupported makes it ``UNDETERMINED`` -- never
compatible. That is v3.5's rule, "a missing observation is not a healthy one",
applied to capabilities, and v3.6's ``NOT_VERIFIED`` applied to a single order.

Supplied, never discovered
--------------------------

AlphaLab queries nothing to build a declaration: an application that has an
adapter knows what its venue supports and says so here, which is the division
:class:`~alphalab.crypto.venue.VenueSpecification` draws for exchange metadata
and :class:`~alphalab.conventions.market.MarketConvention` draws for contract
terms. ``adapter_id`` is an opaque label the application chooses; nothing here
names, reaches or knows a vendor.

Identity
--------

:attr:`CapabilityDeclaration.declaration_id` is a SHA-256 digest over a canonical
rendering tagged :data:`CAPABILITY_DECLARATION_SCHEME` -- the construction every
identity in :mod:`alphalab.lifecycle` and the v3.8 risk model uses: ``label=value``
lines, sets sorted, strings rendered with ``repr`` so a delimiter inside a label
cannot forge another line. Markets and accounts are held sorted, so two
declarations listing the same entries in different orders are equal and
identify themselves identically, in any process.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_left
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum, unique
from types import MappingProxyType
from typing import Any, Final

from alphalab.core.enums import AssetType, OrderType, Side, TimeInForce
from alphalab.core.exceptions import DomainValidationError

__all__ = [
    "ANY_LISTING_VENUE",
    "CAPABILITY_DECLARATION_SCHEME",
    "CAPABILITY_LEVELS",
    "COMPATIBILITY_REPORT_SCHEME",
    "EXECUTION_REQUIREMENTS_SCHEME",
    "ORDER_TYPE_CAPABILITIES",
    "VENUE_FEATURES",
    "AccountCapability",
    "Capability",
    "CapabilityCheck",
    "CapabilityDeclaration",
    "CapabilityLevel",
    "Compatibility",
    "CompatibilityReport",
    "ExecutionRequirements",
    "MarketCapability",
    "RequirementDimension",
    "Support",
    "check_compatibility",
    "order_requirements",
    "supports",
]

#: Scheme tag of a declaration's identity. Changing it is an ADR.
CAPABILITY_DECLARATION_SCHEME: Final = "alphalab.capability_declaration.v1"

#: Scheme tag of a requirements identity.
EXECUTION_REQUIREMENTS_SCHEME: Final = "alphalab.execution_requirements.v1"

#: Scheme tag of a compatibility report's identity.
COMPATIBILITY_REPORT_SCHEME: Final = "alphalab.compatibility_report.v1"

#: A market capability that applies on every listing venue of its asset class.
#:
#: A named value rather than an empty string or ``None``, because a declaration
#: that covers every venue is a broad statement and should read as one.
ANY_LISTING_VENUE: Final = "*"


@unique
class Support(StrEnum):
    """One declared answer, or the answer for something nobody declared."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNDECLARED = "undeclared"


@unique
class CapabilityLevel(StrEnum):
    """Where a capability is decided."""

    #: The adapter's connection as a whole.
    VENUE = "venue"

    #: One asset class, on one listing venue or all of them.
    MARKET = "market"

    #: One account's permissions.
    ACCOUNT = "account"


@unique
class Capability(StrEnum):
    """The flat capability vocabulary an application inspects.

    Each member is decided at the level or levels :data:`CAPABILITY_LEVELS`
    names; :func:`supports` answers one member for one scope.
    """

    MARKET_ORDERS = "market_orders"
    LIMIT_ORDERS = "limit_orders"
    STOP_ORDERS = "stop_orders"
    STOP_LIMIT_ORDERS = "stop_limit_orders"
    SHORTING = "shorting"
    OPTIONS = "options"
    FUTURES = "futures"
    FRACTIONAL = "fractional"
    MARGIN = "margin"
    STREAMING = "streaming"
    CANCEL_REPLACE = "cancel_replace"
    BRACKET_ORDERS = "bracket_orders"
    EXTENDED_HOURS = "extended_hours"


_V = CapabilityLevel.VENUE
_M = CapabilityLevel.MARKET
_A = CapabilityLevel.ACCOUNT

#: Where each capability is decided. A capability with two levels needs both.
CAPABILITY_LEVELS: Final[Mapping[Capability, tuple[CapabilityLevel, ...]]] = MappingProxyType(
    {
        Capability.MARKET_ORDERS: (_M,),
        Capability.LIMIT_ORDERS: (_M,),
        Capability.STOP_ORDERS: (_M,),
        Capability.STOP_LIMIT_ORDERS: (_M,),
        Capability.SHORTING: (_M, _A),
        Capability.OPTIONS: (_M, _A),
        Capability.FUTURES: (_M, _A),
        Capability.FRACTIONAL: (_M,),
        Capability.MARGIN: (_A,),
        Capability.STREAMING: (_V,),
        Capability.CANCEL_REPLACE: (_V,),
        Capability.BRACKET_ORDERS: (_M,),
        Capability.EXTENDED_HOURS: (_M,),
    }
)

#: The capabilities a venue declares for its connection as a whole.
VENUE_FEATURES: Final = frozenset(
    capability for capability, levels in CAPABILITY_LEVELS.items() if levels == (_V,)
)

#: The flat name of the capability each order type needs.
ORDER_TYPE_CAPABILITIES: Final[Mapping[OrderType, Capability]] = MappingProxyType(
    {
        OrderType.MARKET: Capability.MARKET_ORDERS,
        OrderType.LIMIT: Capability.LIMIT_ORDERS,
        OrderType.STOP: Capability.STOP_ORDERS,
        OrderType.STOP_LIMIT: Capability.STOP_LIMIT_ORDERS,
    }
)

#: The two asset classes the flat vocabulary names individually.
_ASSET_CLASS_CAPABILITIES: Final[Mapping[AssetType, Capability]] = MappingProxyType(
    {AssetType.OPTION: Capability.OPTIONS, AssetType.FUTURE: Capability.FUTURES}
)


def _digest(lines: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{field} cannot be empty.")


def _names(values: Iterable[StrEnum]) -> str:
    return ",".join(sorted(str(value) for value in values))


def _combine(answers: Iterable[Support]) -> Support:
    """``UNSUPPORTED`` if any is; else ``UNDECLARED`` if any is; else ``SUPPORTED``."""

    found = set(answers)
    if Support.UNSUPPORTED in found:
        return Support.UNSUPPORTED
    if Support.UNDECLARED in found:
        return Support.UNDECLARED
    return Support.SUPPORTED


# --------------------------------------------------------------------------- #
# The declaration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MarketCapability:
    """What a broker offers for one asset class, on one listing venue or all of them.

    Attributes:
        asset_class: The asset class.
        listing_venue: Where the instruments are listed -- the *listing* sense of
            venue that ``test_venue_concepts_stay_distinct.py`` pins and
            :attr:`~alphalab.lifecycle.specification.MarketRequirements.venues`
            carries -- or :data:`ANY_LISTING_VENUE`. An exact venue entry wins
            over the any-venue entry for the same asset class.
        order_types: Every order type accepted. Complete: one not listed is
            ``UNSUPPORTED``, not undeclared.
        time_in_force: Every time-in-force accepted. Complete in the same sense.
        short_selling: Whether the broker lends and permits short sales here.
        fractional_quantities: Whether quantities that are not whole units are
            accepted.
        extended_hours: Whether orders may work outside the regular session.
        bracket_orders: Whether an order may carry attached take-profit and
            stop-loss legs.

    Raises:
        DomainValidationError: If the venue is blank, or no order type or no
            time-in-force is listed -- a market that accepts no order is not
            offered, and saying so is
            :attr:`CapabilityDeclaration.unsupported_asset_classes`.
    """

    asset_class: AssetType
    listing_venue: str
    order_types: frozenset[OrderType]
    time_in_force: frozenset[TimeInForce]
    short_selling: Support
    fractional_quantities: Support
    extended_hours: Support
    bracket_orders: Support

    def __post_init__(self) -> None:
        _require_text(self.listing_venue, "MarketCapability.listing_venue")
        if not self.order_types:
            raise DomainValidationError(
                f"The {self.asset_class} market on {self.listing_venue!r} lists no order type. "
                "A market that accepts no order is not offered; declare the asset class "
                "unsupported instead."
            )
        if not self.time_in_force:
            raise DomainValidationError(
                f"The {self.asset_class} market on {self.listing_venue!r} lists no "
                "time-in-force, so no order could be sent to it."
            )
        object.__setattr__(self, "order_types", frozenset(self.order_types))
        object.__setattr__(self, "time_in_force", frozenset(self.time_in_force))

    @property
    def key(self) -> tuple[str, str]:
        """``(asset_class, listing_venue)``: what a declaration holds one entry per."""

        return (str(self.asset_class), self.listing_venue)

    def _render(self) -> str:
        return (
            f"market={self.asset_class}|venue={self.listing_venue!r}"
            f"|order_types={_names(self.order_types)}"
            f"|time_in_force={_names(self.time_in_force)}"
            f"|short_selling={self.short_selling}"
            f"|fractional={self.fractional_quantities}"
            f"|extended_hours={self.extended_hours}"
            f"|bracket_orders={self.bracket_orders}"
        )

    def __serializable__(self) -> dict[str, Any]:
        """Sets as sorted lists, so the projection is the same in every process."""

        return {
            "asset_class": str(self.asset_class),
            "listing_venue": self.listing_venue,
            "order_types": sorted(str(value) for value in self.order_types),
            "time_in_force": sorted(str(value) for value in self.time_in_force),
            "short_selling": str(self.short_selling),
            "fractional_quantities": str(self.fractional_quantities),
            "extended_hours": str(self.extended_hours),
            "bracket_orders": str(self.bracket_orders),
        }


@dataclass(frozen=True, slots=True)
class AccountCapability:
    """What one account at a broker is permitted to do.

    Attributes:
        account_id: The account, as the application identifies it.
        asset_classes: Every asset class the account may trade. Complete: one
            not listed is ``UNSUPPORTED`` for this account, whatever the broker
            offers.
        margin: Whether the account may borrow to trade.
        short_selling: Whether the account may sell what it does not hold.
    """

    account_id: str
    asset_classes: frozenset[AssetType]
    margin: Support
    short_selling: Support

    def __post_init__(self) -> None:
        _require_text(self.account_id, "AccountCapability.account_id")
        object.__setattr__(self, "asset_classes", frozenset(self.asset_classes))

    def _render(self) -> str:
        return (
            f"account={self.account_id!r}|asset_classes={_names(self.asset_classes)}"
            f"|margin={self.margin}|short_selling={self.short_selling}"
        )

    def __serializable__(self) -> dict[str, Any]:
        """Sets as sorted lists."""

        return {
            "account_id": self.account_id,
            "asset_classes": sorted(str(value) for value in self.asset_classes),
            "margin": str(self.margin),
            "short_selling": str(self.short_selling),
        }


@dataclass(frozen=True, slots=True)
class CapabilityDeclaration:
    """Everything one adapter declares its venue can do. Supplied, never discovered.

    Attributes:
        adapter_id: An opaque label the application chooses for the adapter and
            the venue connection behind it. Never interpreted.
        supported_features: Venue-level capabilities the connection has --
            members of :data:`VENUE_FEATURES` only.
        unsupported_features: Venue-level capabilities it lacks. A feature in
            neither set is ``UNDECLARED``.
        markets: One entry per ``(asset_class, listing_venue)``. Held sorted.
        unsupported_asset_classes: Asset classes the broker does not offer at
            all. An asset class in neither this set nor any market is
            ``UNDECLARED``.
        accounts: One entry per account. Held sorted by ``account_id``.

    Raises:
        DomainValidationError: If the label is blank; a feature is not a venue
            feature or is declared both ways; two markets share a key; a market
            is declared for an asset class the broker does not offer; or two
            accounts share an identifier.
    """

    adapter_id: str
    supported_features: frozenset[Capability]
    unsupported_features: frozenset[Capability]
    markets: tuple[MarketCapability, ...]
    unsupported_asset_classes: frozenset[AssetType]
    accounts: tuple[AccountCapability, ...]
    _identity: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_text(self.adapter_id, "CapabilityDeclaration.adapter_id")
        supported = frozenset(self.supported_features)
        unsupported = frozenset(self.unsupported_features)
        stray = sorted(str(feature) for feature in (supported | unsupported) - VENUE_FEATURES)
        if stray:
            raise DomainValidationError(
                f"{stray} are not venue-level capabilities. The venue level declares "
                f"{sorted(str(feature) for feature in VENUE_FEATURES)}; everything else is "
                "declared on a market or an account, where it is true."
            )
        both = sorted(str(feature) for feature in supported & unsupported)
        if both:
            raise DomainValidationError(f"{both} are declared both supported and unsupported.")

        markets = tuple(sorted(self.markets, key=lambda market: market.key))
        keys = [market.key for market in markets]
        repeated = sorted(key for key, count in Counter(keys).items() if count > 1)
        if repeated:
            raise DomainValidationError(
                f"The markets {repeated} are declared twice. One entry per asset class and "
                "listing venue, or two answers to one question."
            )
        withdrawn = frozenset(self.unsupported_asset_classes)
        contradicted = sorted(
            {str(market.asset_class) for market in markets if market.asset_class in withdrawn}
        )
        if contradicted:
            raise DomainValidationError(
                f"{contradicted} are declared as markets and as asset classes the broker does "
                "not offer."
            )

        accounts = tuple(sorted(self.accounts, key=lambda account: account.account_id))
        identifiers = [account.account_id for account in accounts]
        duplicated = sorted(name for name, count in Counter(identifiers).items() if count > 1)
        if duplicated:
            raise DomainValidationError(f"The accounts {duplicated} are declared twice.")

        object.__setattr__(self, "supported_features", supported)
        object.__setattr__(self, "unsupported_features", unsupported)
        object.__setattr__(self, "markets", markets)
        object.__setattr__(self, "unsupported_asset_classes", withdrawn)
        object.__setattr__(self, "accounts", accounts)
        # Computed once. Every compatibility report names the declaration it
        # read, and rendering every market and account per check made a run of
        # checks quadratic in the size of the declaration.
        object.__setattr__(self, "_identity", self._render_identity())

    def market_for(self, asset_class: AssetType, listing_venue: str) -> MarketCapability | None:
        """The entry for a market: the exact venue's, else the any-venue one, else ``None``.

        Found by bisection over the sorted entries, so a lookup costs the
        logarithm of the declaration, not its length.
        """

        for venue in (listing_venue, ANY_LISTING_VENUE):
            key = (str(asset_class), venue)
            position = bisect_left(self.markets, key, key=lambda market: market.key)
            if position < len(self.markets) and self.markets[position].key == key:
                return self.markets[position]
        return None

    def account_for(self, account_id: str) -> AccountCapability | None:
        """The entry for an account, or ``None`` when it was not declared."""

        position = bisect_left(self.accounts, account_id, key=lambda account: account.account_id)
        if position < len(self.accounts) and self.accounts[position].account_id == account_id:
            return self.accounts[position]
        return None

    def feature(self, capability: Capability) -> Support:
        """How the connection answers one venue-level capability."""

        if capability not in VENUE_FEATURES:
            levels = [str(level) for level in CAPABILITY_LEVELS[capability]]
            raise DomainValidationError(
                f"{capability} is decided at {levels}, not by the connection."
            )
        if capability in self.supported_features:
            return Support.SUPPORTED
        if capability in self.unsupported_features:
            return Support.UNSUPPORTED
        return Support.UNDECLARED

    def offers(self, asset_class: AssetType) -> Support:
        """Whether the broker offers an asset class on any listing venue."""

        if asset_class in self.unsupported_asset_classes:
            return Support.UNSUPPORTED
        if any(market.asset_class is asset_class for market in self.markets):
            return Support.SUPPORTED
        return Support.UNDECLARED

    @property
    def declaration_id(self) -> str:
        """SHA-256 over the canonical rendering. Order-independent and process-stable."""

        return self._identity

    def _render_identity(self) -> str:
        return _digest(
            [
                CAPABILITY_DECLARATION_SCHEME,
                f"adapter={self.adapter_id!r}",
                f"features.supported={_names(self.supported_features)}",
                f"features.unsupported={_names(self.unsupported_features)}",
                f"asset_classes.unsupported={_names(self.unsupported_asset_classes)}",
                *(market._render() for market in self.markets),
                *(account._render() for account in self.accounts),
            ]
        )

    def __serializable__(self) -> dict[str, Any]:
        """Sets as sorted lists, entries in their held order, and the identity."""

        return {
            "adapter_id": self.adapter_id,
            "supported_features": sorted(str(value) for value in self.supported_features),
            "unsupported_features": sorted(str(value) for value in self.unsupported_features),
            "markets": [market.__serializable__() for market in self.markets],
            "unsupported_asset_classes": sorted(
                str(value) for value in self.unsupported_asset_classes
            ),
            "accounts": [account.__serializable__() for account in self.accounts],
            "declaration_id": self.declaration_id,
        }


# --------------------------------------------------------------------------- #
# Requirements
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ExecutionRequirements:
    """What an order -- or a strategy's whole order flow -- needs from a broker.

    One type for both: an order needs one order type and one time-in-force, and
    a flow needs the set of everything it sends. :func:`order_requirements`
    derives one for a single order.

    Attributes:
        asset_class: The asset class traded.
        listing_venue: Where the instrument is listed. The same sense as
            :attr:`MarketCapability.listing_venue`; a flow across several
            venues is several requirements.
        account_id: The account the orders are sent under.
        order_types: Every order type sent.
        time_in_force: Every time-in-force used.
        short_selling: Whether a sale takes a position below zero.
        fractional_quantities: Whether a quantity is not a whole unit.
        extended_hours: Whether an order works outside the regular session.
        bracket_orders: Whether an order carries attached exit legs.
        margin: Whether the flow borrows.
        features: Venue-level capabilities the flow depends on --
            ``CANCEL_REPLACE`` for an algorithm that amends its orders,
            ``STREAMING`` for one that cannot poll.

    Raises:
        DomainValidationError: If a label is blank, no order type or no
            time-in-force is named, or a feature is not a venue feature.
    """

    asset_class: AssetType
    listing_venue: str
    account_id: str
    order_types: frozenset[OrderType]
    time_in_force: frozenset[TimeInForce]
    short_selling: bool
    fractional_quantities: bool
    extended_hours: bool
    bracket_orders: bool
    margin: bool
    features: frozenset[Capability]

    def __post_init__(self) -> None:
        _require_text(self.listing_venue, "ExecutionRequirements.listing_venue")
        _require_text(self.account_id, "ExecutionRequirements.account_id")
        if not self.order_types:
            raise DomainValidationError(
                "ExecutionRequirements names no order type; it sends nothing."
            )
        if not self.time_in_force:
            raise DomainValidationError("ExecutionRequirements names no time-in-force.")
        stray = sorted(str(feature) for feature in set(self.features) - VENUE_FEATURES)
        if stray:
            raise DomainValidationError(
                f"{stray} are not venue-level capabilities; state them through the fields "
                "that name them."
            )
        object.__setattr__(self, "order_types", frozenset(self.order_types))
        object.__setattr__(self, "time_in_force", frozenset(self.time_in_force))
        object.__setattr__(self, "features", frozenset(self.features))

    @property
    def requirements_id(self) -> str:
        """SHA-256 over the canonical rendering."""

        return _digest(
            [
                EXECUTION_REQUIREMENTS_SCHEME,
                f"asset_class={self.asset_class}",
                f"listing_venue={self.listing_venue!r}",
                f"account={self.account_id!r}",
                f"order_types={_names(self.order_types)}",
                f"time_in_force={_names(self.time_in_force)}",
                f"short_selling={self.short_selling}",
                f"fractional={self.fractional_quantities}",
                f"extended_hours={self.extended_hours}",
                f"bracket_orders={self.bracket_orders}",
                f"margin={self.margin}",
                f"features={_names(self.features)}",
            ]
        )

    def __serializable__(self) -> dict[str, Any]:
        """Sets as sorted lists."""

        return {
            "asset_class": str(self.asset_class),
            "listing_venue": self.listing_venue,
            "account_id": self.account_id,
            "order_types": sorted(str(value) for value in self.order_types),
            "time_in_force": sorted(str(value) for value in self.time_in_force),
            "short_selling": self.short_selling,
            "fractional_quantities": self.fractional_quantities,
            "extended_hours": self.extended_hours,
            "bracket_orders": self.bracket_orders,
            "margin": self.margin,
            "features": sorted(str(value) for value in self.features),
            "requirements_id": self.requirements_id,
        }


def order_requirements(
    *,
    asset_class: AssetType,
    listing_venue: str,
    account_id: str,
    order_type: OrderType,
    time_in_force: TimeInForce,
    side: Side,
    quantity: Decimal,
    position_before: Decimal,
    extended_hours: bool,
    bracket_orders: bool,
    margin: bool,
    features: frozenset[Capability],
) -> ExecutionRequirements:
    """What one order needs, derived where the order itself says so.

    Two requirements are *derived* rather than stated, because the order
    decides them and a caller restating them could restate them wrongly:

    * **short selling** -- a sale whose quantity exceeds the position held
      before it takes the position below zero;
    * **fractional quantities** -- a quantity that is not a whole number of
      units.

    Everything else is the caller's to state, keyword by keyword, with no
    default: whether an order works outside the session or carries exit legs is
    a property of the order the caller is building, not something this function
    can see.

    Raises:
        DomainValidationError: If ``quantity`` is not positive.
    """

    if quantity <= 0:
        raise DomainValidationError(f"An order for {quantity} needs nothing; it is not an order.")
    return ExecutionRequirements(
        asset_class=asset_class,
        listing_venue=listing_venue,
        account_id=account_id,
        order_types=frozenset({order_type}),
        time_in_force=frozenset({time_in_force}),
        short_selling=side is Side.SELL and position_before - quantity < 0,
        fractional_quantities=quantity != quantity.to_integral_value(),
        extended_hours=extended_hours,
        bracket_orders=bracket_orders,
        margin=margin,
        features=features,
    )


# --------------------------------------------------------------------------- #
# The check
# --------------------------------------------------------------------------- #


@unique
class RequirementDimension(StrEnum):
    """Which kind of requirement a check is about."""

    #: Whether the broker offers the asset class on the listing venue at all.
    MARKET = "market"

    #: An order type.
    ORDER_TYPE = "order_type"

    #: A time-in-force.
    TIME_IN_FORCE = "time_in_force"

    #: Fractional quantities, extended hours, bracket orders, and the venue
    #: features.
    EXECUTION_FEATURE = "execution_feature"

    #: Whether the account is declared and permitted the asset class.
    ACCOUNT = "account"

    #: What exposes the account to more than it holds: short sales and margin.
    RISK = "risk"


@unique
class Compatibility(StrEnum):
    """The answer to "can this declared broker do what this requires?"."""

    #: Every requirement is supported.
    COMPATIBLE = "compatible"

    #: At least one requirement is declared unsupported.
    INCOMPATIBLE = "incompatible"

    #: Nothing is unsupported and something was never declared. Not compatible.
    UNDETERMINED = "undetermined"


@dataclass(frozen=True, slots=True)
class CapabilityCheck:
    """One requirement, checked at one level.

    Attributes:
        dimension: What kind of requirement.
        level: Where it was decided.
        requirement: What was required, in words a log can carry --
            ``"order_type=limit"``, ``"short_selling"``.
        capability: The flat-vocabulary name, where the requirement has one.
        support: The answer.
        detail: Why, in one sentence.
    """

    dimension: RequirementDimension
    level: CapabilityLevel
    requirement: str
    capability: Capability | None
    support: Support
    detail: str


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    """Every requirement checked against one declaration, and the verdict they imply.

    Attributes:
        requirements: What was required.
        adapter_id: Which declaration answered.
        declaration_id: That declaration's identity, so a report can be traced to
            the exact declaration it read.
        checks: One per requirement per level, in a fixed order: market,
            order types, times-in-force, execution features, account, risk.
    """

    requirements: ExecutionRequirements
    adapter_id: str
    declaration_id: str
    checks: tuple[CapabilityCheck, ...]

    @property
    def status(self) -> Compatibility:
        """``COMPATIBLE`` only when every check is ``SUPPORTED``."""

        overall = _combine(check.support for check in self.checks)
        if overall is Support.SUPPORTED:
            return Compatibility.COMPATIBLE
        if overall is Support.UNSUPPORTED:
            return Compatibility.INCOMPATIBLE
        return Compatibility.UNDETERMINED

    @property
    def compatible(self) -> bool:
        """Whether the broker can do everything required. ``UNDETERMINED`` is not."""

        return self.status is Compatibility.COMPATIBLE

    @property
    def unsupported(self) -> tuple[CapabilityCheck, ...]:
        """The checks the declaration answered no."""

        return tuple(check for check in self.checks if check.support is Support.UNSUPPORTED)

    @property
    def undeclared(self) -> tuple[CapabilityCheck, ...]:
        """The checks nobody answered."""

        return tuple(check for check in self.checks if check.support is Support.UNDECLARED)

    @property
    def gaps(self) -> tuple[str, ...]:
        """Every unsupported or undeclared requirement, as a sentence."""

        return tuple(
            check.detail for check in self.checks if check.support is not Support.SUPPORTED
        )

    @property
    def report_id(self) -> str:
        """SHA-256 over the requirements, the declaration and every answer."""

        return _digest(
            [
                COMPATIBILITY_REPORT_SCHEME,
                f"requirements={self.requirements.requirements_id}",
                f"declaration={self.declaration_id}",
                *(
                    f"check={check.dimension}|{check.level}|{check.requirement!r}|{check.support}"
                    for check in self.checks
                ),
            ]
        )

    def __serializable__(self) -> dict[str, Any]:
        """The report, its verdict and its identity."""

        return {
            "requirements": self.requirements.__serializable__(),
            "adapter_id": self.adapter_id,
            "declaration_id": self.declaration_id,
            "checks": [
                {
                    "dimension": str(check.dimension),
                    "level": str(check.level),
                    "requirement": check.requirement,
                    "capability": None if check.capability is None else str(check.capability),
                    "support": str(check.support),
                    "detail": check.detail,
                }
                for check in self.checks
            ],
            "status": str(self.status),
            "report_id": self.report_id,
        }


def _check(
    dimension: RequirementDimension,
    level: CapabilityLevel,
    requirement: str,
    capability: Capability | None,
    support: Support,
    detail: str,
) -> CapabilityCheck:
    return CapabilityCheck(dimension, level, requirement, capability, support, detail)


def _market_checks(
    requirements: ExecutionRequirements, declaration: CapabilityDeclaration
) -> list[CapabilityCheck]:
    asset = requirements.asset_class
    venue = requirements.listing_venue
    where = f"{asset} on {venue!r}"
    named = _ASSET_CLASS_CAPABILITIES.get(asset)
    market = declaration.market_for(asset, venue)
    checks: list[CapabilityCheck] = []

    if asset in declaration.unsupported_asset_classes:
        coverage = Support.UNSUPPORTED
        why = f"the broker declares that it does not offer {asset}."
    elif market is None:
        coverage = Support.UNDECLARED
        why = f"the declaration says nothing about {where}."
    else:
        coverage = Support.SUPPORTED
        why = f"{where} is declared."
    checks.append(
        _check(RequirementDimension.MARKET, _M, f"market={asset}|{venue}", named, coverage, why)
    )

    for order_type in sorted(requirements.order_types):
        support = (
            (Support.SUPPORTED if order_type in market.order_types else Support.UNSUPPORTED)
            if market is not None
            else coverage
        )
        checks.append(
            _check(
                RequirementDimension.ORDER_TYPE,
                _M,
                f"order_type={order_type}",
                ORDER_TYPE_CAPABILITIES[order_type],
                support,
                f"{order_type} orders for {where}: {support}.",
            )
        )
    for tif in sorted(requirements.time_in_force):
        support = (
            (Support.SUPPORTED if tif in market.time_in_force else Support.UNSUPPORTED)
            if market is not None
            else coverage
        )
        checks.append(
            _check(
                RequirementDimension.TIME_IN_FORCE,
                _M,
                f"time_in_force={tif}",
                None,
                support,
                f"time-in-force {tif} for {where}: {support}.",
            )
        )

    offered: Mapping[Capability, Support] = (
        {}
        if market is None
        else {
            Capability.FRACTIONAL: market.fractional_quantities,
            Capability.EXTENDED_HOURS: market.extended_hours,
            Capability.BRACKET_ORDERS: market.bracket_orders,
        }
    )
    for needed, name, capability in (
        (requirements.fractional_quantities, "fractional_quantities", Capability.FRACTIONAL),
        (requirements.extended_hours, "extended_hours", Capability.EXTENDED_HOURS),
        (requirements.bracket_orders, "bracket_orders", Capability.BRACKET_ORDERS),
    ):
        if not needed:
            continue
        support = offered.get(capability, coverage)
        checks.append(
            _check(
                RequirementDimension.EXECUTION_FEATURE,
                _M,
                name,
                capability,
                support,
                f"{name.replace('_', ' ')} for {where}: {support}.",
            )
        )
    if requirements.short_selling:
        support = market.short_selling if market is not None else coverage
        checks.append(
            _check(
                RequirementDimension.RISK,
                _M,
                "short_selling",
                Capability.SHORTING,
                support,
                f"short sales of {where}: {support}.",
            )
        )
    return checks


def _venue_checks(
    requirements: ExecutionRequirements, declaration: CapabilityDeclaration
) -> list[CapabilityCheck]:
    checks: list[CapabilityCheck] = []
    for feature in sorted(requirements.features):
        support = declaration.feature(feature)
        checks.append(
            _check(
                RequirementDimension.EXECUTION_FEATURE,
                _V,
                str(feature),
                feature,
                support,
                f"the connection's {str(feature).replace('_', ' ')}: {support}.",
            )
        )
    return checks


def _account_checks(
    requirements: ExecutionRequirements, declaration: CapabilityDeclaration
) -> list[CapabilityCheck]:
    account = declaration.account_for(requirements.account_id)
    name = requirements.account_id
    asset = requirements.asset_class
    if account is None:
        permission = Support.UNDECLARED
        why = f"account {name!r} is not declared, so nothing it may do is known."
    elif asset in account.asset_classes:
        permission = Support.SUPPORTED
        why = f"account {name!r} may trade {asset}."
    else:
        permission = Support.UNSUPPORTED
        why = f"account {name!r} is not permitted {asset}."
    checks = [
        _check(
            RequirementDimension.ACCOUNT,
            _A,
            f"account={name}|{asset}",
            _ASSET_CLASS_CAPABILITIES.get(asset),
            permission,
            why,
        )
    ]
    for wanted, label, capability in (
        (requirements.short_selling, "short_selling", Capability.SHORTING),
        (requirements.margin, "margin", Capability.MARGIN),
    ):
        if not wanted:
            continue
        if account is None:
            support = Support.UNDECLARED
        elif capability is Capability.MARGIN:
            support = account.margin
        else:
            support = account.short_selling
        checks.append(
            _check(
                RequirementDimension.RISK,
                _A,
                label,
                capability,
                support,
                f"account {name!r} {label.replace('_', ' ')}: {support}.",
            )
        )
    return checks


def check_compatibility(
    requirements: ExecutionRequirements, declaration: CapabilityDeclaration
) -> CompatibilityReport:
    """Check every requirement against a declaration, at every level it is decided.

    Pure and total: every requirement produces a check, nothing absent reads as
    supported, and the same pair always produces an equal report with the same
    :attr:`~CompatibilityReport.report_id`.
    """

    checks = [
        *_market_checks(requirements, declaration),
        *_venue_checks(requirements, declaration),
        *_account_checks(requirements, declaration),
    ]
    order = {dimension: index for index, dimension in enumerate(RequirementDimension)}
    levels = {level: index for index, level in enumerate(CapabilityLevel)}
    checks.sort(key=lambda check: (order[check.dimension], levels[check.level], check.requirement))
    return CompatibilityReport(
        requirements=requirements,
        adapter_id=declaration.adapter_id,
        declaration_id=declaration.declaration_id,
        checks=tuple(checks),
    )


def supports(
    declaration: CapabilityDeclaration,
    capability: Capability,
    *,
    asset_class: AssetType | None = None,
    listing_venue: str | None = None,
    account_id: str | None = None,
) -> Support:
    """How a declaration answers one capability of the flat vocabulary, for one scope.

    The scope a capability needs is the one :data:`CAPABILITY_LEVELS` names: a
    market-level capability needs ``asset_class`` and ``listing_venue``, an
    account-level one needs ``account_id``, and one decided at two levels needs
    both -- and the answer is the combination: unsupported if either level says
    so, undeclared if either is silent, supported only if both are.

    ``OPTIONS`` and ``FUTURES`` are answered from the asset class they name, so
    they need a ``listing_venue`` and an ``account_id`` but not an
    ``asset_class``.

    Raises:
        DomainValidationError: If the scope the capability needs is missing.
    """

    levels = CAPABILITY_LEVELS[capability]
    answers: list[Support] = []
    named_class = next(
        (asset for asset, name in _ASSET_CLASS_CAPABILITIES.items() if name is capability), None
    )
    if named_class is not None and asset_class is not None and asset_class is not named_class:
        raise DomainValidationError(
            f"{capability} names the {named_class} asset class; asset_class={asset_class} "
            "contradicts it."
        )
    scope_class = named_class if named_class is not None else asset_class

    if _V in levels:
        answers.append(declaration.feature(capability))

    if _M in levels:
        if scope_class is None or listing_venue is None:
            raise DomainValidationError(
                f"{capability} is decided per market; name the asset class and the listing venue."
            )
        if scope_class in declaration.unsupported_asset_classes:
            answers.append(Support.UNSUPPORTED)
        else:
            market = declaration.market_for(scope_class, listing_venue)
            if market is None:
                answers.append(Support.UNDECLARED)
            elif named_class is not None:
                answers.append(Support.SUPPORTED)
            elif capability is Capability.SHORTING:
                answers.append(market.short_selling)
            elif capability is Capability.FRACTIONAL:
                answers.append(market.fractional_quantities)
            elif capability is Capability.EXTENDED_HOURS:
                answers.append(market.extended_hours)
            elif capability is Capability.BRACKET_ORDERS:
                answers.append(market.bracket_orders)
            else:
                order_type = next(
                    kind for kind, name in ORDER_TYPE_CAPABILITIES.items() if name is capability
                )
                answers.append(
                    Support.SUPPORTED if order_type in market.order_types else Support.UNSUPPORTED
                )

    if _A in levels:
        if account_id is None:
            raise DomainValidationError(f"{capability} is decided per account; name the account.")
        account = declaration.account_for(account_id)
        if account is None:
            answers.append(Support.UNDECLARED)
        elif capability is Capability.MARGIN:
            answers.append(account.margin)
        elif capability is Capability.SHORTING:
            answers.append(account.short_selling)
        else:
            assert scope_class is not None
            answers.append(
                Support.SUPPORTED if scope_class in account.asset_classes else Support.UNSUPPORTED
            )

    return _combine(answers)
