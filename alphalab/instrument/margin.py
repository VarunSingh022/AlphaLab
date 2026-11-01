"""What holding an instrument requires as margin, declared on the instrument (ledger FUT-017).

A venue or a broker requires collateral against a position: an initial
requirement to open or increase it, a maintenance requirement below which the
account is called, and a haircut on what a holding counts for as collateral.
Each is a convention of the instrument's market and of whoever extends the
credit, so it is **declared**, per instrument, on its
:class:`~alphalab.instrument.record.InstrumentRecord` -- as its settlement rule
and its contract terms are -- and nothing is inferred from an exchange or an
asset class.

:class:`MarginTerms` states one requirement for one instrument: a rate of the
position's market value, or a fixed amount per contract. It is **not** a
portfolio margin model -- SPAN, TIMS, a broker's risk-based house rules, which
net offsetting positions -- and is not presented as one; such a model would be
a further, declared contract. What it is: the requirement each position
carries on its own, summed, which is what a strategic or Reg-T-style account
charges and what a run can state exactly.

What the execution path does with it is in
:func:`alphalab.runtime.execution_pipeline.margin_position`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_EVEN, Decimal
from enum import Enum, auto
from typing import Any, Final, Protocol, runtime_checkable

from alphalab.instrument.exceptions import InstrumentInputError
from alphalab.options.binomial import BinomialLattice, binomial_value
from alphalab.options.carry import Carry, CarryKind
from alphalab.options.contract import OptionContract
from alphalab.options.enums import ExerciseStyle, OptionType
from alphalab.options.exceptions import OptionInputError
from alphalab.options.pricing import black_scholes_value, time_to_expiry_years
from alphalab.options.volatility_surface import (
    ExpiryInterpolation,
    VolatilitySurface,
    VolPoint,
    implied_vol_across_expiries,
)

__all__ = [
    "MarginBasis",
    "MarginExposure",
    "MarginModel",
    "MarginRequirement",
    "MarginScenario",
    "MarginTerms",
    "OptionPricing",
    "OptionRisk",
    "PerPositionMargin",
    "ScenarioMargin",
    "margin_from_primitives",
    "margin_model_from_primitives",
    "margin_model_to_primitives",
    "margin_to_primitives",
]

_ZERO = Decimal("0")
#: A surface's volatility, as the run reads it: to the tenth of a basis point.
_VOL_QUANT: Final = Decimal("0.000001")
_ONE = Decimal("1")


class MarginBasis(Enum):
    """What a requirement is a multiple of."""

    #: A rate of the position's absolute market value in the base currency:
    #: ``0.25`` is a quarter of what the position is worth.
    VALUE_RATE = auto()
    #: An amount per contract held, long or short, in the instrument's currency:
    #: a futures exchange's flat initial and maintenance margin.
    PER_CONTRACT = auto()


def _decimal(value: Any, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise InstrumentInputError(f"{name} is a finite Decimal, got {value!r}.")
    return value


@dataclass(frozen=True, slots=True)
class MarginTerms:
    """One instrument's margin requirement and collateral value.

    Attributes:
        basis: What ``initial`` and ``maintenance`` multiply -- see
            :class:`MarginBasis`.
        initial: The requirement to open or increase a position, and the level
            a forced liquidation restores the account to.
        maintenance: The requirement below which the account is in breach; at
            most ``initial`` and above zero.
        haircut: The share of a long position's market value it does not count
            for as collateral, in ``[0, 1)``. Zero by default: a position counts
            at its market value.
        priority: Which positions a forced liquidation reduces first: the
            lowest priority first, then the largest requirement, then the asset
            identifier, so the order is always the same.
        purchase_on_margin: Whether a fully paid instrument may be bought on
            margin (v4.2.0, ledger FUT-018): a purchase then spends its initial
            requirement of buying power, not its notional, and what the cash does
            not cover is booked as a financed debit, which a
            :class:`~alphalab.backtesting.schedule.ScheduledInterest` charges for.
            ``False`` by default: a purchase is paid from cash, as in FUT-017.
    """

    basis: MarginBasis
    initial: Decimal
    maintenance: Decimal
    haircut: Decimal = _ZERO
    priority: int = 0
    purchase_on_margin: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.basis, MarginBasis):
            raise InstrumentInputError(f"basis is a MarginBasis, got {self.basis!r}.")
        initial = _decimal(self.initial, "An initial margin requirement")
        maintenance = _decimal(self.maintenance, "A maintenance margin requirement")
        if not _ZERO < maintenance <= initial:
            raise InstrumentInputError(
                f"A maintenance requirement is above zero and at most the initial one; got "
                f"{maintenance} against {initial}."
            )
        haircut = _decimal(self.haircut, "A collateral haircut")
        if not _ZERO <= haircut < _ONE:
            raise InstrumentInputError(f"A haircut is in [0, 1), got {haircut}.")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise InstrumentInputError(f"priority is an integer, got {self.priority!r}.")
        if not isinstance(self.purchase_on_margin, bool):
            raise InstrumentInputError(
                f"purchase_on_margin is a bool, got {self.purchase_on_margin!r}."
            )

    def requirement(self, quantity: Decimal, value: Decimal, initial: bool) -> Decimal:
        """What ``quantity`` worth ``value`` (absolute, base currency) requires.

        ``value`` is read for :attr:`MarginBasis.VALUE_RATE`; ``quantity`` --
        contracts, in the instrument's currency, which the caller converts --
        for :attr:`MarginBasis.PER_CONTRACT`.
        """

        rate = self.initial if initial else self.maintenance
        if self.basis is MarginBasis.VALUE_RATE:
            return abs(value) * rate
        return abs(quantity) * rate


def margin_to_primitives(terms: MarginTerms | None) -> dict[str, Any] | None:
    """The terms as plain primitives, the basis by name and amounts as text."""

    if terms is None:
        return None
    written: dict[str, Any] = {
        "basis": terms.basis.name,
        "initial": str(terms.initial),
        "maintenance": str(terms.maintenance),
        "haircut": str(terms.haircut),
        "priority": terms.priority,
    }
    if terms.purchase_on_margin:
        # Written only when declared, so terms declaring no margin purchase
        # write exactly what FUT-017's did (FUT-018).
        written["purchase_on_margin"] = True
    return written


def margin_from_primitives(value: Any, where: str = "margin") -> MarginTerms | None:
    """Read terms :func:`margin_to_primitives` wrote.

    Raises:
        InstrumentInputError: If the value is not that shape or does not validate.
    """

    if value is None:
        return None
    try:
        return MarginTerms(
            MarginBasis[value["basis"]],
            Decimal(value["initial"]),
            Decimal(value["maintenance"]),
            Decimal(value["haircut"]),
            int(value["priority"]),
            bool(value.get("purchase_on_margin", False)),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise InstrumentInputError(f"{where} is not margin terms: {exc}.") from exc


# ---------------------------------------------------------------------------
# The account's margin model (ledger FUT-019)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OptionRisk:
    """What a margin model needs to reprice one option position (FUT-026, phase 3).

    Supplied by the execution path beside an option's exposure when the account's
    model reprices it: the contract's terms in effect now and the market's prices
    now, never a figure the model would have to assume.

    Attributes:
        underlying: The deliverable instrument whose move the scenarios state.
        underlying_price: Its price now, in the option's currency.
        strike: The strike, per unit of the underlying.
        option_type: Call or put.
        style: European or American exercise.
        expires_at: The contract's expiry instant.
        as_of: The instant the book is margined at.
        units: The units of the underlying the position controls, signed:
            contracts held times the units each delivers; negative short.
        to_base: One unit of the option's currency in the base currency.
    """

    underlying: str
    underlying_price: Decimal
    strike: Decimal
    option_type: OptionType
    style: ExerciseStyle
    expires_at: float
    as_of: float
    units: Decimal
    to_base: Decimal


@dataclass(frozen=True, slots=True)
class MarginExposure:
    """One margined position, as a margin model reads it.

    Attributes:
        asset_id: The position.
        value: Its signed market value in the base currency: negative short.
        initial: What its own terms require at the initial rate.
        maintenance: What they require at maintenance.
        option: An option's pricing facts, when the account's model reprices it
            (FUT-026); ``None`` otherwise.
    """

    asset_id: str
    value: Decimal
    initial: Decimal
    maintenance: Decimal
    option: OptionRisk | None = None


#: Each model value is kept to four places per unit, as the pricer's price is.
_VALUE_QUANT: Final = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class OptionPricing:
    """How a scenario model reprices one option: the declared inputs (FUT-026, phase 3).

    Attributes:
        volatility: The annual volatility the option is valued at, above zero --
            an implied volatility the caller observed, declared -- or ``None`` to
            read it from the model's surface for the underlying in effect at the
            margined instant (FUT-032).
        rate: The continuously compounded risk-free rate.
        carry: What holding the underlying earns or costs: a dividend yield, a
            foreign rate, or a futures underlying's none.
        lattice_steps: For an American option, the binomial lattice's steps; a
            European option is valued in closed form and declares none.
    """

    volatility: Decimal | None
    rate: Decimal
    carry: Carry
    lattice_steps: int | None = None

    def __post_init__(self) -> None:
        if self.volatility is not None:
            volatility = _decimal(self.volatility, "A volatility")
            if volatility <= 0:
                raise InstrumentInputError(f"A volatility is above zero, got {volatility}.")
        _decimal(self.rate, "A rate")
        if not isinstance(self.carry, Carry):
            raise InstrumentInputError(f"carry is a Carry, got {self.carry!r}.")
        steps = self.lattice_steps
        if steps is not None and (isinstance(steps, bool) or not isinstance(steps, int)):
            raise InstrumentInputError(f"lattice_steps is an int or None, got {steps!r}.")
        if steps is not None and steps < 1:
            raise InstrumentInputError(f"A lattice takes at least one step, got {steps}.")

    def value(
        self,
        risk: OptionRisk,
        spot: Decimal,
        shift: Decimal,
        volatility: Decimal | None = None,
    ) -> Decimal:
        """One unit's model value at ``spot`` with the volatility shifted by ``shift``.

        ``volatility`` is the one the option is valued at when it declares none
        of its own -- read from a surface (FUT-032).

        Expired, the value is what exercise pays. American exercise is valued on
        the declared lattice, European in closed form; either is refused where
        its inputs are not declared.
        """

        intrinsic = (
            max(spot - risk.strike, Decimal("0"))
            if risk.option_type is OptionType.CALL
            else max(risk.strike - spot, Decimal("0"))
        )
        if risk.as_of >= risk.expires_at:
            return intrinsic
        contract = OptionContract(
            risk.underlying, risk.strike, risk.expires_at, risk.option_type, risk.style, 1
        )
        base = self.volatility if self.volatility is not None else volatility
        if base is None:
            raise InstrumentInputError("An option repriced from a surface is given its volatility.")
        sigma = float(base + shift)
        if risk.style is ExerciseStyle.AMERICAN:
            if self.lattice_steps is None:
                raise InstrumentInputError(
                    "An American option is repriced on a lattice; declare its lattice_steps."
                )
            raw = binomial_value(
                contract,
                float(spot),
                sigma,
                float(self.rate),
                risk.as_of,
                carry=self.carry,
                lattice=BinomialLattice(self.lattice_steps, ()),
            )
        elif risk.style is ExerciseStyle.EUROPEAN:
            years = time_to_expiry_years(contract, risk.as_of)
            raw = black_scholes_value(
                contract, float(spot), sigma, float(self.rate), years, carry=self.carry
            )
        else:
            raise InstrumentInputError(
                f"A {risk.style.name} option is not repriced: declare its own moves instead."
            )
        if not math.isfinite(raw):
            raise InstrumentInputError("An option's model value is not a finite number.")
        return Decimal(repr(max(raw, 0.0))).quantize(_VALUE_QUANT, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True, slots=True)
class MarginRequirement:
    """What a book requires, in the base currency."""

    initial: Decimal
    maintenance: Decimal


@runtime_checkable
class MarginModel(Protocol):
    """How an account's margin is computed from its margined positions.

    A model is declared -- a clearing house's or a broker's published
    methodology -- and never inferred: positions are offset only as the model's
    own inputs say. It is handed every margined position at once, so it may net
    them, and returns the book's requirement.
    """

    def requirement(self, exposures: tuple[MarginExposure, ...]) -> MarginRequirement:
        """The book's initial and maintenance requirement."""
        ...


@dataclass(frozen=True, slots=True)
class PerPositionMargin:
    """Each position's own requirement, summed: no offsets (FUT-017's margin, declared)."""

    def requirement(self, exposures: tuple[MarginExposure, ...]) -> MarginRequirement:
        return MarginRequirement(
            sum((e.initial for e in exposures), _ZERO),
            sum((e.maintenance for e in exposures), _ZERO),
        )


@dataclass(frozen=True, slots=True)
class MarginScenario:
    """One declared market scenario: each covered instrument's relative price move.

    Attributes:
        name: What it is, for the record.
        moves: ``asset_id`` -> the fraction its price moves in the scenario:
            ``-0.15`` is a fall of fifteen per cent.
    """

    name: str
    moves: Mapping[str, Decimal]
    #: Each repriced option's volatility shift in the scenario, absolute -- 0.05
    #: is five points up -- for the options the model reprices (FUT-026); an
    #: option the scenario names none for keeps its volatility.
    volatility_shifts: Mapping[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise InstrumentInputError("A margin scenario is named.")
        shifts = dict(self.volatility_shifts)
        for asset_id, shift in shifts.items():
            if not isinstance(shift, Decimal) or not shift.is_finite():
                raise InstrumentInputError(
                    f"Scenario {self.name!r} shifts {asset_id}'s volatility by {shift!r}: a "
                    "finite Decimal."
                )
        object.__setattr__(self, "volatility_shifts", dict(sorted(shifts.items())))
        moves = dict(self.moves)
        if not moves:
            raise InstrumentInputError(f"Scenario {self.name!r} moves no instrument.")
        for asset_id, move in moves.items():
            if not isinstance(move, Decimal) or not move.is_finite() or move <= -_ONE:
                raise InstrumentInputError(
                    f"Scenario {self.name!r} moves {asset_id} by {move!r}: a move is a finite "
                    "Decimal above -1."
                )
        object.__setattr__(self, "moves", dict(sorted(moves.items())))


@dataclass(frozen=True, slots=True)
class ScenarioMargin:
    """A risk-based margin model over a declared scenario set (FUT-019).

    The positions in the instruments the scenarios cover are margined together:
    their initial requirement is the worst loss any scenario gives them --
    every covered position revalued by its declared move at once, so a hedge
    offsets only as far as the scenarios say it does -- and never less than
    ``floor_rate`` of their gross value, so no offset takes a requirement to
    nothing. Their maintenance requirement is ``maintenance_ratio`` of it. A
    position the scenarios do not cover is margined by its own terms and
    offsets nothing: an instrument is in the set only by declaration.

    Attributes:
        scenarios: At least one; every scenario moves the same instruments.
        maintenance_ratio: Maintenance as a share of initial, in ``(0, 1]``.
        floor_rate: The least the covered positions require, as a share of
            their gross value, in ``[0, 1]``.
    """

    scenarios: tuple[MarginScenario, ...]
    maintenance_ratio: Decimal
    floor_rate: Decimal = _ZERO
    #: Each option the model reprices from its underlying's move, and how
    #: (FUT-026, phase 3); empty, every covered instrument moves by its own move.
    repriced: Mapping[str, OptionPricing] = field(default_factory=dict)
    #: Each underlying's dated volatility surfaces (FUT-032, phase 4): what a
    #: repriced option declaring no volatility of its own is valued at.
    surfaces: tuple[VolatilitySurface, ...] = ()
    #: Seconds after the margined instant each repriced option is valued at in
    #: a scenario (FUT-032): its time decay over the horizon. ``None``: now.
    horizon: float | None = None
    _covered: frozenset[str] = field(init=False, repr=False, compare=False)
    _surfaces: Mapping[str, tuple[VolatilitySurface, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        scenarios = tuple(self.scenarios)
        if not scenarios or not all(isinstance(s, MarginScenario) for s in scenarios):
            raise InstrumentInputError("A scenario margin model declares at least one scenario.")
        covered = set(scenarios[0].moves)
        for scenario in scenarios[1:]:
            if set(scenario.moves) != covered:
                raise InstrumentInputError(
                    f"Scenario {scenario.name!r} moves {sorted(scenario.moves)}, and "
                    f"{scenarios[0].name!r} moves {sorted(covered)}: every scenario states a "
                    "move for every covered instrument, so none is offset by a scenario that "
                    "forgot it."
                )
        if len({s.name for s in scenarios}) != len(scenarios):
            raise InstrumentInputError("Each margin scenario has its own name.")
        ratio = _decimal(self.maintenance_ratio, "A maintenance ratio")
        if not _ZERO < ratio <= _ONE:
            raise InstrumentInputError(f"A maintenance ratio is in (0, 1], got {ratio}.")
        floor = _decimal(self.floor_rate, "A floor rate")
        if not _ZERO <= floor <= _ONE:
            raise InstrumentInputError(f"A floor rate is in [0, 1], got {floor}.")
        repriced = dict(sorted(dict(self.repriced).items()))
        if not all(isinstance(p, OptionPricing) for p in repriced.values()):
            raise InstrumentInputError("A repriced option declares its OptionPricing.")
        twice = sorted(set(repriced) & covered)
        if twice:
            raise InstrumentInputError(
                f"{twice} are both moved and repriced: an option is moved by its own declared "
                "move or repriced from its underlying's, not both."
            )
        for scenario in scenarios:
            stray = sorted(set(scenario.volatility_shifts) - set(repriced))
            if stray:
                raise InstrumentInputError(
                    f"Scenario {scenario.name!r} shifts the volatility of {stray}, which the "
                    "model does not reprice."
                )
            for asset_id, pricing in repriced.items():
                if pricing.volatility is None:
                    continue  # read from a surface when margined
                if pricing.volatility + scenario.volatility_shifts.get(asset_id, _ZERO) <= 0:
                    raise InstrumentInputError(
                        f"Scenario {scenario.name!r} shifts {asset_id}'s volatility to zero or "
                        "below; a volatility is above zero."
                    )
        surfaces = tuple(sorted(self.surfaces, key=lambda v: (v.underlying_asset_id, v.timestamp)))
        if not all(isinstance(v, VolatilitySurface) for v in surfaces):
            raise InstrumentInputError("A model's surfaces are options VolatilitySurfaces.")
        by_underlying: dict[str, list[VolatilitySurface]] = {}
        for surface in surfaces:
            _require_surface(surface)
            published = by_underlying.setdefault(surface.underlying_asset_id, [])
            if published and published[-1].timestamp == surface.timestamp:
                raise InstrumentInputError(
                    f"Two surfaces for {surface.underlying_asset_id} are as of "
                    f"{surface.timestamp!r}: a correction is published at its own instant."
                )
            published.append(surface)
        if any(p.volatility is None for p in repriced.values()) and not surfaces:
            raise InstrumentInputError(
                "A repriced option declaring no volatility reads one from a surface, and the "
                "model declares none: no volatility is assumed."
            )
        if self.horizon is not None and (
            not isinstance(self.horizon, int | float)
            or not math.isfinite(self.horizon)
            or self.horizon <= 0
        ):
            raise InstrumentInputError(
                f"A scenario horizon is a positive number of seconds, got {self.horizon!r}."
            )
        object.__setattr__(self, "scenarios", scenarios)
        object.__setattr__(self, "repriced", repriced)
        object.__setattr__(self, "surfaces", surfaces)
        object.__setattr__(self, "_surfaces", {u: tuple(v) for u, v in by_underlying.items()})
        object.__setattr__(self, "_covered", frozenset((*covered, *repriced)))

    @property
    def covered(self) -> frozenset[str]:
        """The instruments the scenarios move or reprice."""

        return self._covered

    def surface_volatility(self, risk: OptionRisk) -> Decimal | None:
        """The volatility the surface in effect at ``risk.as_of`` gives the option, or ``None``.

        The latest surface for its underlying as of the margined instant or
        before -- never a later one (FUT-032) -- read at the option's strike and
        expiry as :func:`~alphalab.options.volatility_surface.implied_vol_across_expiries`
        reads it: linear between quoted strikes, linear in total variance
        between quoted expiries. ``None`` where that refuses: a strike or an
        expiry beyond the quotes, or quotes admitting a calendar arbitrage --
        nothing is extrapolated, and no volatility assumed.
        """

        found = None
        for surface in self._surfaces.get(risk.underlying, ()):
            if surface.timestamp > risk.as_of:
                break
            found = surface
        if found is None:
            return None
        try:
            volatility = implied_vol_across_expiries(
                found,
                float(risk.strike),
                risk.expires_at,
                method=ExpiryInterpolation.TOTAL_VARIANCE_LINEAR,
            )
        except OptionInputError:
            return None
        return Decimal(repr(volatility)).quantize(_VOL_QUANT, rounding=ROUND_HALF_EVEN)

    def requirement(self, exposures: tuple[MarginExposure, ...]) -> MarginRequirement:
        covered = self._covered
        repriced = self.repriced
        inside: list[MarginExposure] = []
        outside: list[MarginExposure] = []
        resolved: dict[str, Decimal | None] = {}
        for e in exposures:
            # A repriced option whose facts the execution path could not state --
            # its underlying not yet priced -- is margined by its own terms; so is
            # one whose surface gives it no volatility (FUT-032): none is assumed.
            priced = e.asset_id in covered and (e.option is not None or e.asset_id not in repriced)
            if priced and e.option is not None and e.asset_id in repriced:
                pricing = repriced[e.asset_id]
                vol = pricing.volatility
                if vol is None:
                    vol = self.surface_volatility(e.option)
                    if vol is not None and any(
                        vol + s.volatility_shifts.get(e.asset_id, _ZERO) <= 0
                        for s in self.scenarios
                    ):
                        vol = None
                    priced = vol is not None
                resolved[e.asset_id] = vol
            (inside if priced else outside).append(e)
        initial = sum((e.initial for e in outside), _ZERO)
        maintenance = sum((e.maintenance for e in outside), _ZERO)
        if inside:
            moved = [(e.value, e.asset_id) for e in inside if e.asset_id not in repriced]
            options = [
                self._priced(e, resolved[e.asset_id]) for e in inside if e.asset_id in repriced
            ]
            horizon = self.horizon
            worst = _ZERO
            for scenario in self.scenarios:
                moves = scenario.moves
                loss = _ZERO
                for value, asset_id in moved:
                    loss -= value * moves[asset_id]
                for asset_id, risk, pricing, now, vol in options:
                    spot = risk.underlying_price * (_ONE + moves[risk.underlying])
                    shift = scenario.volatility_shifts.get(asset_id, _ZERO)
                    later = risk if horizon is None else replace(risk, as_of=risk.as_of + horizon)
                    change = pricing.value(later, spot, shift, vol) - now
                    loss -= change * risk.units * risk.to_base
                worst = max(worst, loss)
            gross = _ZERO
            for e in inside:
                gross += abs(e.value)
            scanned = max(worst, gross * self.floor_rate)
            initial += scanned
            maintenance += scanned * self.maintenance_ratio
        return MarginRequirement(initial, maintenance)

    def _priced(
        self, exposure: MarginExposure, vol: Decimal | None
    ) -> tuple[str, OptionRisk, OptionPricing, Decimal, Decimal | None]:
        """A repriced option's facts, inputs, model value now, and the volatility it uses."""

        risk = exposure.option
        assert risk is not None  # kept outside the scan otherwise
        if risk.underlying not in self.scenarios[0].moves:
            raise InstrumentInputError(
                f"{exposure.asset_id} is repriced from {risk.underlying}, which no scenario moves."
            )
        pricing = self.repriced[exposure.asset_id]
        now = pricing.value(risk, risk.underlying_price, _ZERO, vol)
        return exposure.asset_id, risk, pricing, now, vol


def margin_model_to_primitives(model: MarginModel | None) -> dict[str, Any] | None:
    """A declared model as plain primitives; ``None`` for none.

    Raises:
        InstrumentInputError: For a model of a type this build cannot write.
    """

    if model is None:
        return None
    if isinstance(model, PerPositionMargin):
        return {"model": "per_position"}
    if isinstance(model, ScenarioMargin):
        return {
            "model": "scenario",
            "maintenance_ratio": str(model.maintenance_ratio),
            "floor_rate": str(model.floor_rate),
            "scenarios": [
                {
                    "name": s.name,
                    "moves": {a: str(m) for a, m in s.moves.items()},
                    # Written only when declared (FUT-026), so a model without
                    # them keeps its form.
                    **(
                        {}
                        if not s.volatility_shifts
                        else {
                            "volatility_shifts": {a: str(v) for a, v in s.volatility_shifts.items()}
                        }
                    ),
                }
                for s in model.scenarios
            ],
            **(
                {}
                if not model.repriced
                else {
                    "repriced": {
                        a: {
                            "volatility": None if p.volatility is None else str(p.volatility),
                            "rate": str(p.rate),
                            "carry": p.carry.kind.name,
                            "carry_yield": repr(p.carry.yield_rate),
                            "lattice_steps": p.lattice_steps,
                        }
                        for a, p in model.repriced.items()
                    }
                }
            ),
            # Phase 4 (FUT-032): written only when declared.
            **(
                {}
                if not model.surfaces
                else {"surfaces": [_surface_primitives(v) for v in model.surfaces]}
            ),
            **({} if model.horizon is None else {"horizon": model.horizon}),
        }
    raise InstrumentInputError(
        f"A {type(model).__name__} margin model cannot be written: a registry snapshot "
        "persists PerPositionMargin and ScenarioMargin, and a host's own model is supplied "
        "back with the registry instead."
    )


def _require_surface(surface: VolatilitySurface) -> None:
    """A surface quotes at least one point, each a positive strike and volatility."""

    if not surface.points:
        raise InstrumentInputError(
            f"The surface for {surface.underlying_asset_id} quotes no volatility."
        )
    seen = set()
    for point in surface.points:
        if (
            not (
                math.isfinite(point.strike)
                and math.isfinite(point.expiry)
                and math.isfinite(point.implied_vol)
            )
            or point.strike <= 0
            or point.implied_vol <= 0
        ):
            raise InstrumentInputError(
                f"A surface point is a positive strike and volatility at an instant, got {point}."
            )
        if (point.strike, point.expiry) in seen:
            raise InstrumentInputError(
                f"A surface quotes strike {point.strike} at expiry {point.expiry} once."
            )
        seen.add((point.strike, point.expiry))


def _surface_primitives(surface: VolatilitySurface) -> dict[str, Any]:
    points = sorted(surface.points, key=lambda p: (p.expiry, p.strike))
    return {
        "underlying": surface.underlying_asset_id,
        "timestamp": surface.timestamp,
        "points": [[repr(p.strike), repr(p.expiry), repr(p.implied_vol)] for p in points],
    }


def _surface_from(value: Any) -> VolatilitySurface:
    return VolatilitySurface(
        value["underlying"],
        float(value["timestamp"]),
        tuple(VolPoint(float(k), float(e), float(v)) for k, e, v in value["points"]),
    )


def margin_model_from_primitives(value: Any, where: str = "margin_model") -> MarginModel | None:
    """Read a model :func:`margin_model_to_primitives` wrote.

    Raises:
        InstrumentInputError: If the value is not one of those shapes.
    """

    if value is None:
        return None
    try:
        kind = value["model"]
        if kind == "per_position":
            return PerPositionMargin()
        if kind == "scenario":
            return ScenarioMargin(
                tuple(
                    MarginScenario(
                        s["name"],
                        {a: Decimal(m) for a, m in s["moves"].items()},
                        {a: Decimal(v) for a, v in s.get("volatility_shifts", {}).items()},
                    )
                    for s in value["scenarios"]
                ),
                Decimal(value["maintenance_ratio"]),
                Decimal(value["floor_rate"]),
                {
                    a: OptionPricing(
                        None if p["volatility"] is None else Decimal(p["volatility"]),
                        Decimal(p["rate"]),
                        Carry(CarryKind[p["carry"]], float(p["carry_yield"])),
                        p["lattice_steps"],
                    )
                    for a, p in value.get("repriced", {}).items()
                },
                tuple(_surface_from(v) for v in value.get("surfaces", ())),
                value.get("horizon"),
            )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise InstrumentInputError(f"{where} is not a margin model: {exc}.") from exc
    raise InstrumentInputError(f"{where} names no margin model this build reads: {kind!r}.")
