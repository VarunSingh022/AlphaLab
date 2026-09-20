"""The quantity grid: how much of a thing may be traded at once.

A lot size is not a multiplier and not a tick size. It constrains *quantity*
where the multiplier scales *value* and the tick constrains *price*, and the
three are independent: an instrument can have a lot size of 1 and a multiplier
of 1,000, or a lot of 100 and a multiplier of 1.

Markets differ here more than anywhere else in this package. A US equity trades
in single shares with odd lots routinely accepted; an Indian derivative trades
only in exchange-declared lots that the exchange revises; a Japanese equity
trades in units of 100; a crypto venue quotes a step size with eight decimals.
None of those is the default, so :class:`LotSpecification` has none.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from alphalab.conventions.exceptions import ConventionInputError, ConventionViolationError

__all__ = ["LotSpecification", "lots_in", "round_down_to_lot"]


@dataclass(frozen=True, slots=True)
class LotSpecification:
    """The quantity increment an instrument trades in.

    Attributes:
        lot_size: The quantity step. ``Decimal("1")`` for a market trading in
            whole single units, ``Decimal("0.00001")`` for a crypto venue's step
            size, ``Decimal("50")`` for an index derivative quoted in lots of
            fifty.
        minimum_quantity: The smallest tradable quantity, which is not always
            one lot -- a venue may require a minimum of five lots. Must itself
            be a whole number of lots.

    Raises:
        ConventionInputError: If either value is not positive, or the minimum is
            not a whole number of lots.
    """

    lot_size: Decimal
    minimum_quantity: Decimal

    def __post_init__(self) -> None:
        if self.lot_size <= Decimal("0"):
            raise ConventionInputError(
                f"lot_size is {self.lot_size}; a non-positive step admits every quantity and "
                "constrains nothing."
            )
        if self.minimum_quantity <= Decimal("0"):
            raise ConventionInputError(
                f"minimum_quantity is {self.minimum_quantity}; a market with no minimum states "
                "one lot, not zero."
            )
        if self.minimum_quantity % self.lot_size != Decimal("0"):
            raise ConventionInputError(
                f"minimum_quantity {self.minimum_quantity} is not a whole number of "
                f"{self.lot_size} lots, so the smallest tradable quantity is not tradable."
            )

    @classmethod
    def single_units(cls) -> LotSpecification:
        """One unit per lot, minimum one. A US-style cash equity, stated rather
        than assumed -- it is a real convention that happens to be the simplest,
        not the absence of one."""

        return cls(lot_size=Decimal("1"), minimum_quantity=Decimal("1"))

    def admits(self, quantity: Decimal) -> bool:
        """Whether ``quantity`` is tradable: a whole number of lots, at or above
        the minimum. Sign is ignored -- a short of 50 is as tradable as a long
        of 50, and direction is not this type's question."""

        magnitude = abs(quantity)
        return magnitude >= self.minimum_quantity and magnitude % self.lot_size == Decimal("0")

    def require(self, quantity: Decimal) -> Decimal:
        """``quantity`` if it is tradable, else a refusal naming why.

        Raises:
            ConventionViolationError: If it is below the minimum or not a whole
                number of lots. Rounding it silently is what this refuses: an
                order for 150 where the lot is 100 is either 100 or 200, and
                which one is the caller's decision.
        """

        magnitude = abs(quantity)
        if magnitude < self.minimum_quantity:
            raise ConventionViolationError(
                f"{quantity} is below the minimum tradable quantity of {self.minimum_quantity}."
            )
        if magnitude % self.lot_size != Decimal("0"):
            raise ConventionViolationError(
                f"{quantity} is not a whole number of {self.lot_size} lots. Rounding it here "
                "would choose between two different orders on the caller's behalf."
            )
        return quantity


def lots_in(quantity: Decimal, specification: LotSpecification) -> Decimal:
    """How many lots ``quantity`` is, signed the way ``quantity`` is.

    Raises:
        ConventionViolationError: If ``quantity`` is not a whole number of lots.
            A fractional lot count is not a quantity any venue accepts, and
            returning one invites it to be multiplied by a lot value.
    """

    if quantity % specification.lot_size != Decimal("0"):
        raise ConventionViolationError(
            f"{quantity} is not a whole number of {specification.lot_size} lots, so it is not "
            "a lot count."
        )
    return quantity / specification.lot_size


def round_down_to_lot(quantity: Decimal, specification: LotSpecification) -> Decimal:
    """The largest tradable quantity no further from zero than ``quantity``.

    Always toward zero, so a long shrinks and a short shrinks: rounding a
    position *up* in magnitude would add exposure the caller did not ask for.
    Returns zero when the magnitude is below the minimum, which is the honest
    answer -- there is no tradable quantity there.
    """

    magnitude = abs(quantity)
    lots = (magnitude / specification.lot_size).quantize(Decimal("1"), rounding=ROUND_FLOOR)
    rounded = lots * specification.lot_size
    if rounded < specification.minimum_quantity:
        return Decimal("0")
    return -rounded if quantity < Decimal("0") else rounded
