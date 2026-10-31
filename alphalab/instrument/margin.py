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

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from typing import Any, Protocol, runtime_checkable

from alphalab.instrument.exceptions import InstrumentInputError

__all__ = [
    "MarginBasis",
    "MarginExposure",
    "MarginModel",
    "MarginRequirement",
    "MarginScenario",
    "MarginTerms",
    "PerPositionMargin",
    "ScenarioMargin",
    "margin_from_primitives",
    "margin_model_from_primitives",
    "margin_model_to_primitives",
    "margin_to_primitives",
]

_ZERO = Decimal("0")
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
class MarginExposure:
    """One margined position, as a margin model reads it.

    Attributes:
        asset_id: The position.
        value: Its signed market value in the base currency: negative short.
        initial: What its own terms require at the initial rate.
        maintenance: What they require at maintenance.
    """

    asset_id: str
    value: Decimal
    initial: Decimal
    maintenance: Decimal


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

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise InstrumentInputError("A margin scenario is named.")
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
        object.__setattr__(self, "scenarios", scenarios)

    @property
    def covered(self) -> frozenset[str]:
        """The instruments the scenarios move."""

        return frozenset(self.scenarios[0].moves)

    def requirement(self, exposures: tuple[MarginExposure, ...]) -> MarginRequirement:
        covered = self.covered
        inside = [e for e in exposures if e.asset_id in covered]
        outside = [e for e in exposures if e.asset_id not in covered]
        initial = sum((e.initial for e in outside), _ZERO)
        maintenance = sum((e.maintenance for e in outside), _ZERO)
        if inside:
            worst = _ZERO
            for scenario in self.scenarios:
                loss = -sum((e.value * scenario.moves[e.asset_id] for e in inside), _ZERO)
                worst = max(worst, loss)
            gross = sum((abs(e.value) for e in inside), _ZERO)
            scanned = max(worst, gross * self.floor_rate)
            initial += scanned
            maintenance += scanned * self.maintenance_ratio
        return MarginRequirement(initial, maintenance)


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
                {"name": s.name, "moves": {a: str(m) for a, m in s.moves.items()}}
                for s in model.scenarios
            ],
        }
    raise InstrumentInputError(
        f"A {type(model).__name__} margin model cannot be written: a registry snapshot "
        "persists PerPositionMargin and ScenarioMargin, and a host's own model is supplied "
        "back with the registry instead."
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
                    MarginScenario(s["name"], {a: Decimal(m) for a, m in s["moves"].items()})
                    for s in value["scenarios"]
                ),
                Decimal(value["maintenance_ratio"]),
                Decimal(value["floor_rate"]),
            )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise InstrumentInputError(f"{where} is not a margin model: {exc}.") from exc
    raise InstrumentInputError(f"{where} names no margin model this build reads: {kind!r}.")
