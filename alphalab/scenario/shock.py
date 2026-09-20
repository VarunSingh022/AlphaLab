"""The four shocks, what each one multiplies, and who it reaches.

A shock is a *relative* change and nothing else: ``-0.30`` is "thirty percent
down", whatever the thing is denominated in. Relative rather than absolute
because a scenario has to mean the same thing applied to a book of 10 and a book
of 10 million, and an absolute shock does not.

Each shock names a :class:`ShockScope`, which decides which exposures it reaches.
A scope that matches nothing is an error, not a no-op: a 2008 scenario scoped to
a sector the book does not hold has not stressed the book, and reporting "no
loss" would be a true statement about a scenario nobody meant to run.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto

from alphalab.scenario.exceptions import ScenarioValidationError

__all__ = [
    "ScopeKind",
    "Shock",
    "ShockKind",
    "ShockScope",
    "everything",
    "of_assets",
    "of_currency",
    "of_sector",
]


class ShockKind(Enum):
    """What a shock moves."""

    #: Multiplies the price of every exposure in scope.
    PRICE = auto()

    #: Multiplies the volatility carried on every exposure in scope. Requires
    #: the exposures to carry one; they refuse otherwise.
    VOLATILITY = auto()

    #: Multiplies the exchange rate of a currency against the reporting
    #: currency. Reaches exposures by the currency they settle in, never by
    #: asset, and never touches an exposure already in the reporting currency.
    FX = auto()

    #: Multiplies the available liquidity of every exposure in scope. Requires
    #: the exposures to carry it.
    LIQUIDITY = auto()


class ScopeKind(Enum):
    """How a shock selects the exposures it reaches."""

    ALL = auto()
    ASSETS = auto()
    SECTOR = auto()
    CURRENCY = auto()


@dataclass(frozen=True, slots=True)
class ShockScope:
    """Which exposures a shock applies to.

    Built through :func:`everything`, :func:`of_assets`, :func:`of_sector` or
    :func:`of_currency` rather than directly, so that a scope and its selector
    cannot disagree.
    """

    kind: ScopeKind
    selector: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind is ScopeKind.ALL and self.selector:
            raise ScenarioValidationError(
                f"A scope of ALL selects everything and cannot also name {self.selector}."
            )
        if self.kind is not ScopeKind.ALL and not self.selector:
            raise ScenarioValidationError(
                f"A {self.kind.name} scope names nothing, so it would reach no exposure. "
                "Name what it applies to, or use everything()."
            )

    def identity(self) -> str:
        """``"ALL"`` or ``"SECTOR:Energy,Financials"`` -- stable and sorted."""

        if self.kind is ScopeKind.ALL:
            return "ALL"
        return f"{self.kind.name}:{','.join(self.selector)}"


def everything() -> ShockScope:
    """Every exposure in the state."""

    return ShockScope(ScopeKind.ALL)


def of_assets(*asset_ids: str) -> ShockScope:
    """Only the named assets."""

    return ShockScope(ScopeKind.ASSETS, tuple(sorted(set(asset_ids))))


def of_sector(*sectors: str) -> ShockScope:
    """Only exposures classified into the named sectors.

    Reaches an exposure only when it carries a sector. AlphaLab has no security
    master, so an exposure's sector is whatever the caller projected onto it;
    one with ``None`` is never matched, and is never assumed to be outside the
    scope either -- :meth:`~alphalab.scenario.scenario.Scenario.apply` reports
    how many exposures a scope reached so an under-reaching sector shock is
    visible rather than silent.
    """

    return ShockScope(ScopeKind.SECTOR, tuple(sorted(set(sectors))))


def of_currency(*currencies: str) -> ShockScope:
    """Only exposures settling in the named currencies."""

    return ShockScope(ScopeKind.CURRENCY, tuple(sorted(set(currencies))))


@dataclass(frozen=True, slots=True)
class Shock:
    """One relative move, of one kind, over one scope.

    Attributes:
        kind: What it moves.
        magnitude: The relative change. ``-0.30`` is a thirty percent fall,
            ``0.5`` a fifty percent rise. A magnitude of ``-1`` takes the
            quantity to zero and is the floor: ``-1.5`` would mean a negative
            price, a negative volatility or negative liquidity, none of which
            exists, so it is refused rather than clamped.
        scope: Which exposures it reaches.
    """

    kind: ShockKind
    magnitude: Decimal
    scope: ShockScope

    def __post_init__(self) -> None:
        if self.magnitude < Decimal("-1"):
            raise ScenarioValidationError(
                f"A {self.kind.name} shock of {self.magnitude} takes the quantity below "
                "zero. Prices, volatilities and liquidity are non-negative, so this is a "
                "refusal rather than a clamp -- a clamped shock would report a loss "
                "smaller than the one that was asked for."
            )

    @property
    def multiplier(self) -> Decimal:
        """``1 + magnitude`` -- what the shocked quantity is multiplied by."""

        return Decimal("1") + self.magnitude

    def identity(self) -> str:
        """``"PRICE:-0.30:ALL"`` -- stable across processes."""

        return f"{self.kind.name}:{self.magnitude:f}:{self.scope.identity()}"
