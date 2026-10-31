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

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from typing import Any

from alphalab.instrument.exceptions import InstrumentInputError

__all__ = ["MarginBasis", "MarginTerms", "margin_from_primitives", "margin_to_primitives"]

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
    """

    basis: MarginBasis
    initial: Decimal
    maintenance: Decimal
    haircut: Decimal = _ZERO
    priority: int = 0

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
    return {
        "basis": terms.basis.name,
        "initial": str(terms.initial),
        "maintenance": str(terms.maintenance),
        "haircut": str(terms.haircut),
        "priority": terms.priority,
    }


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
        )
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        raise InstrumentInputError(f"{where} is not margin terms: {exc}.") from exc
