"""One scenario contract, reusable by every portfolio and strategy class.

A :class:`Scenario` is a named, ordered list of shocks and nothing else. It
holds no market data, no portfolio, no clock and no state, which is what lets
the same scenario object be applied to a backtest book, a live book, an
optimizer's target and a hand-built one, and give the same answer to each.

Applying returns; it does not mutate
-------------------------------------

:meth:`Scenario.apply` takes a :class:`~alphalab.scenario.exposure.ScenarioState`
and returns a :class:`ScenarioResult` carrying a **new** state. The state passed
in is unchanged -- every type in this package is a frozen dataclass and every
transformation goes through :func:`dataclasses.replace`, so scenario leakage into
the base book is a structural impossibility rather than a rule to remember. This
is the immutable convention the rest of AlphaLab follows: an engine returns new
state, it never edits what it was given.

Composition
-----------

Scenarios compose with :meth:`Scenario.then`, which concatenates the shock lists
and names the result. Order is preserved and *matters*: a price shock followed
by an FX shock is not arithmetically the same as the reverse when both scope the
same exposure, because each multiplies what the previous one produced. The order
a scenario was written in is the order it applies in, and :meth:`Scenario.identity`
includes it.

Identity
--------

:meth:`Scenario.identity` is a SHA-256 over the canonical rendering of the name
and the shocks, following ``alphalab.research.study``'s rule that an identity is
*derived from content*, never minted and never taken from a clock. Two scenarios
that would produce identical results anywhere produce the same identity, in any
process, on any machine -- which is what makes a stress result quotable and
cacheable.

Refusing rather than skipping
------------------------------

A shock the state cannot express raises
:class:`~alphalab.scenario.exceptions.UnsupportedShockError`. A volatility shock
on exposures with no volatility, a liquidity shock on exposures with no
liquidity, an FX shock on a currency with no rate, and a scope that reaches
nothing are all refusals. A stress test that quietly dropped a leg would report a
smaller loss under the scenario's own name, and nothing downstream could tell.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from alphalab.scenario.exceptions import (
    ScenarioValidationError,
    UnsupportedShockError,
)
from alphalab.scenario.exposure import ScenarioExposure, ScenarioState
from alphalab.scenario.shock import ScopeKind, Shock, ShockKind, ShockScope, everything

__all__ = [
    "Scenario",
    "ScenarioResult",
    "apply_all",
    "scenario",
]


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """What one scenario did to one book.

    Attributes:
        scenario_name: The scenario's name.
        scenario_identity: Its derived identity, so the numbers below can be
            traced to the exact shock list that produced them.
        base_state: The state as it was. Returned rather than assumed, so a
            caller holding only the result can still see what it was measured
            against.
        shocked_state: The new state. The base is not modified.
        base_value: Market value before, in the base currency.
        shocked_value: Market value after.
        change_by_asset: Per-asset change in base-currency value, ordered by
            asset. These sum to ``shocked_value - base_value`` exactly.
        reached: How many exposures each shock reached, in the scenario's shock
            order. A shock that reached fewer than expected is visible here.
    """

    scenario_name: str
    scenario_identity: str
    base_state: ScenarioState
    shocked_state: ScenarioState
    base_value: Decimal
    shocked_value: Decimal
    change_by_asset: Mapping[str, Decimal]
    reached: tuple[int, ...]

    @property
    def change(self) -> Decimal:
        """``shocked_value - base_value``, in the base currency."""

        return self.shocked_value - self.base_value

    @property
    def relative_change(self) -> Decimal:
        """The change as a fraction of the base value.

        Raises:
            ScenarioValidationError: If the base value is zero. A relative
                change against nothing is undefined, not infinite and not zero.
        """

        if self.base_value == 0:
            raise ScenarioValidationError(
                f"{self.scenario_name} was applied to a book worth zero, so a relative "
                "change has no denominator. The absolute change is "
                f"{self.change}."
            )
        return self.change / self.base_value


@dataclass(frozen=True, slots=True)
class Scenario:
    """A named, ordered list of shocks.

    Attributes:
        name: What this scenario is called. Free text, and part of the identity.
        shocks: Applied in order.
    """

    name: str
    shocks: tuple[Shock, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ScenarioValidationError(
                "A scenario has no name. A stress result carries its scenario's name, "
                "and an unnamed one is not attributable to anything."
            )
        if not self.shocks:
            raise ScenarioValidationError(
                f"Scenario {self.name!r} carries no shocks, so applying it would report "
                "the book unchanged under a scenario's name. Name at least one shock."
            )

    def identity(self) -> str:
        """SHA-256 of the canonical rendering. Derived, never minted."""

        canonical = "|".join((self.name, *(shock.identity() for shock in self.shocks)))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def then(self, other: Scenario) -> Scenario:
        """This scenario's shocks followed by ``other``'s.

        Neither operand is modified. The composed name is ``"a+b"``, which keeps
        the identity readable and, because identity is derived from it, keeps
        ``a.then(b)`` and ``b.then(a)`` distinct -- as they must be, since the
        shocks apply in order.
        """

        return Scenario(f"{self.name}+{other.name}", (*self.shocks, *other.shocks))

    # ------------------------------------------------------------------ #

    def apply(self, state: ScenarioState) -> ScenarioResult:
        """Apply every shock, in order, and report the result.

        ``state`` is not modified.

        Raises:
            UnsupportedShockError: If a shock reaches no exposure, or reaches
                one that cannot express it.
            ScenarioValidationError: If an exposure's currency has no rate.
        """

        base_value = state.value()
        current = state
        reached: list[int] = []

        for shock in self.shocks:
            current, count = _apply_one(current, shock)
            reached.append(count)

        shocked_value = current.value()

        before = {
            exposure.asset_id: exposure.market_value * state.rate_for(exposure.currency)
            for exposure in state.exposures
        }
        after = {
            exposure.asset_id: exposure.market_value * current.rate_for(exposure.currency)
            for exposure in current.exposures
        }
        change = {asset_id: after[asset_id] - before[asset_id] for asset_id in sorted(before)}

        return ScenarioResult(
            scenario_name=self.name,
            scenario_identity=self.identity(),
            base_state=state,
            shocked_state=current,
            base_value=base_value,
            shocked_value=shocked_value,
            change_by_asset=change,
            reached=tuple(reached),
        )


def _in_scope(exposure: ScenarioExposure, scope: ShockScope) -> bool:
    if scope.kind is ScopeKind.ALL:
        return True
    if scope.kind is ScopeKind.ASSETS:
        return exposure.asset_id in scope.selector
    if scope.kind is ScopeKind.SECTOR:
        return exposure.sector is not None and exposure.sector in scope.selector
    return exposure.currency in scope.selector


def _apply_one(state: ScenarioState, shock: Shock) -> tuple[ScenarioState, int]:
    """One shock against one state, returning the new state and its reach."""

    if shock.kind is ShockKind.FX:
        return _apply_fx(state, shock)

    selected = [exposure for exposure in state.exposures if _in_scope(exposure, shock.scope)]
    if not selected:
        raise UnsupportedShockError(
            f"A {shock.kind.name} shock scoped to {shock.scope.identity()} reached no "
            "exposure in this book. A scenario that touches nothing would report no loss "
            "under its own name; check the scope, or drop the shock."
        )

    updated: list[ScenarioExposure] = []
    for exposure in state.exposures:
        if not _in_scope(exposure, shock.scope):
            updated.append(exposure)
            continue
        updated.append(_shock_exposure(exposure, shock))

    return state.with_exposures(updated), len(selected)


def _shock_exposure(exposure: ScenarioExposure, shock: Shock) -> ScenarioExposure:
    if shock.kind is ShockKind.PRICE:
        return replace(exposure, price=exposure.price * shock.multiplier)

    if shock.kind is ShockKind.VOLATILITY:
        if exposure.volatility is None:
            raise UnsupportedShockError(
                f"{exposure.asset_id} carries no volatility, so a VOLATILITY shock has "
                "nothing to multiply. Project a volatility onto the exposure, or scope "
                "the shock away from this asset -- it is not applied as a no-op, because "
                "the result would understate the scenario."
            )
        return replace(exposure, volatility=exposure.volatility * float(shock.multiplier))

    if exposure.available_liquidity is None:
        raise UnsupportedShockError(
            f"{exposure.asset_id} carries no available_liquidity, so a LIQUIDITY shock "
            "has nothing to multiply. Project one onto the exposure, or scope the shock "
            "away from this asset."
        )
    return replace(exposure, available_liquidity=exposure.available_liquidity * shock.multiplier)


def _apply_fx(state: ScenarioState, shock: Shock) -> tuple[ScenarioState, int]:
    """Move exchange rates, not exposures.

    An FX shock is the one kind that does not touch a position: it changes what
    a foreign holding is worth in the reporting currency, which is a property of
    the rate. Applying it to prices instead would also change the holding's
    value in its *own* currency, which an exchange-rate move does not do.

    A shock scoped to the reporting currency is refused. Everything is quoted
    against it, so "the base currency fell" is not expressible as one rate move
    and would silently do nothing.
    """

    scope = shock.scope
    if scope.kind is ScopeKind.ALL:
        currencies = sorted(
            {
                exposure.currency
                for exposure in state.exposures
                if exposure.currency != state.base_currency
            }
        )
    elif scope.kind is ScopeKind.CURRENCY:
        currencies = sorted(scope.selector)
    else:
        raise UnsupportedShockError(
            f"An FX shock is scoped by currency, not by {scope.kind.name}. A rate belongs "
            "to a currency pair; scoping one to an asset or a sector would move the pair "
            "for some holdings in it and not others."
        )

    if state.base_currency in currencies:
        raise UnsupportedShockError(
            f"An FX shock names the reporting currency {state.base_currency}. Every rate "
            "in this state is quoted against it, so a move in it is not one rate move; "
            "shock the other side of each pair instead."
        )

    held = {exposure.currency for exposure in state.exposures}
    reaching = [currency for currency in currencies if currency in held]
    if not reaching:
        raise UnsupportedShockError(
            f"An FX shock on {currencies} reaches no exposure: the book settles only in "
            f"{sorted(held)}. It would report no loss under the scenario's name."
        )

    rates = dict(state.rates)
    for currency in reaching:
        # rate_for refuses a currency with no rate, which is what makes an FX
        # shock on an unpriced pair an error rather than a silent one-for-one.
        rates[currency] = state.rate_for(currency) * shock.multiplier

    return state.with_rates(rates), len(
        [exposure for exposure in state.exposures if exposure.currency in reaching]
    )


def scenario(
    name: str,
    *,
    price_shock: Decimal | None = None,
    volatility_shock: Decimal | None = None,
    fx_shock: Decimal | None = None,
    liquidity_shock: Decimal | None = None,
    scope: ShockScope | None = None,
) -> Scenario:
    """Build a scenario from the four shock magnitudes, the short way.

    The convenience form of :class:`Scenario`::

        scenario("mild", price_shock=Decimal("-0.10"))

    Every magnitude is optional and ``None`` means *this scenario has no shock
    of that kind* -- not a shock of zero, which would still have to reach an
    exposure and would still be reported as applied. ``scope`` applies to all of
    the shocks named in one call; a scenario whose legs need different scopes is
    built from :class:`Shock` values directly, or composed with
    :meth:`Scenario.then`.

    Shocks are ordered price, volatility, FX, liquidity when more than one is
    given, which is stated because order is part of the identity.

    Raises:
        ScenarioValidationError: If no magnitude at all is supplied.
    """

    where = everything() if scope is None else scope
    pairs = (
        (ShockKind.PRICE, price_shock),
        (ShockKind.VOLATILITY, volatility_shock),
        (ShockKind.FX, fx_shock),
        (ShockKind.LIQUIDITY, liquidity_shock),
    )
    shocks = tuple(
        Shock(kind, magnitude, where) for kind, magnitude in pairs if magnitude is not None
    )
    if not shocks:
        raise ScenarioValidationError(
            f"scenario({name!r}) was given no shock. Name at least one of price_shock, "
            "volatility_shock, fx_shock or liquidity_shock."
        )
    return Scenario(name, shocks)


def apply_all(scenarios: Sequence[Scenario], state: ScenarioState) -> tuple[ScenarioResult, ...]:
    """Apply several scenarios to the *same* base state, independently.

    Each result is measured against the unshocked book, not against the previous
    scenario's output -- which is what a comparison across scenarios means. To
    chain them instead, compose with :meth:`Scenario.then` and apply once.
    """

    return tuple(item.apply(state) for item in scenarios)
