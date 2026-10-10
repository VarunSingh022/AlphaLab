"""What each strategy has committed of its ceiling (ledger OFE-003).

A strategy's ceiling -- :meth:`~alphalab.allocation.budget.CapitalBudget.strategy_ceiling`
-- bounds the capital it **commits**, which is two things:

* **deployed**: what its positions cost, per asset, in the budget's currency.
  A fill that grows a position deploys what the growth cost; one that shrinks
  it releases the same fraction of what the position cost; one that crosses
  zero releases the old side and deploys the new one. Positions are therefore
  held at cost, not at market: a ceiling bounds the capital a strategy put in,
  so a position that rises in value does not lock its strategy out, and the
  ledger changes only when the strategy's own quantities do -- never on a price
  -- which keeps it off the per-event path entirely.
* **reserved**: what each of its working orders will deploy, valued when the
  order was allocated. A fill converts reservation into deployment, up to what
  the fill deployed; whatever an order still reserves when its life ends is
  released.

Only exposure is capital. A strategy's order that reduces what it holds --
counting its filled position and its share of its working orders -- commits
nothing, so a strategy at its ceiling can always sell, and can rotate a book by
selling first.

A strategy's share of a netted order is its own sized quantity, before netting,
because its fills are divided by contribution and it ends up holding exactly
that (:func:`~alphalab.core.contribution.split_by_contribution`): netting
against another strategy changes what the venue sees, not what this strategy
holds. While two of a strategy's working orders offset each other, the ledger
can count both sides until they fill -- it errs toward refusing, never toward
overcommitting.

All of this is kept only when the budget enforces ceilings; a run that does not
carries an empty ledger and does no arithmetic for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.common.persistent_map import PersistentMap

__all__ = ["StrategyCapital", "adding_exposure"]

_ZERO: Final = Decimal("0")


def adding_exposure(held: Decimal, delta: Decimal) -> Decimal:
    """How many units ``delta`` adds to the magnitude of a position of ``held``.

    Zero for a delta that reduces the position or leaves it the same size; the
    whole new size for one that crosses zero, since the old side is released.
    """

    after = ACCOUNTING_CONTEXT.add(held, delta)
    if held == 0 or ((held > 0) == (after > 0) and after != 0):
        return max(_ZERO, ACCOUNTING_CONTEXT.subtract(abs(after), abs(held)))
    # Crossed zero, or closed: the new side is all exposure added.
    return abs(after)


@dataclass(frozen=True, slots=True)
class StrategyCapital:
    """One strategy's committed capital: its positions at cost and its working orders' reservations.

    Attributes:
        deployed: Asset to what the strategy's position in it cost, in the
            budget's currency. An asset it no longer holds is absent.
        reserved: Working order id to what that order will deploy for this
            strategy. An order whose life has ended is absent.
        deployed_total: The sum of ``deployed``, kept rather than recomputed so
            a ceiling check costs the same however many assets are held.
        reserved_total: The sum of ``reserved``, likewise.
    """

    deployed: PersistentMap[str, Decimal] = field(default_factory=PersistentMap)
    reserved: PersistentMap[str, Decimal] = field(default_factory=PersistentMap)
    deployed_total: Decimal = _ZERO
    reserved_total: Decimal = _ZERO

    @property
    def committed(self) -> Decimal:
        """Everything this strategy has committed against its ceiling."""

        return ACCOUNTING_CONTEXT.add(self.deployed_total, self.reserved_total)

    def reserve(self, order_id: str, amount: Decimal) -> StrategyCapital:
        """Reserve ``amount`` for a working order this strategy took part in."""

        if amount <= 0:
            return self
        ctx = ACCOUNTING_CONTEXT
        held = self.reserved.get(order_id, _ZERO)
        return StrategyCapital(
            self.deployed,
            self.reserved.set(order_id, ctx.add(held, amount)),
            self.deployed_total,
            ctx.add(self.reserved_total, amount),
        )

    def release(self, order_id: str) -> StrategyCapital:
        """Release whatever a working order still reserves; nothing if it reserves nothing."""

        amount = self.reserved.get(order_id)
        if amount is None:
            return self
        return StrategyCapital(
            self.deployed,
            self.reserved.delete(order_id),
            self.deployed_total,
            ACCOUNTING_CONTEXT.subtract(self.reserved_total, amount),
        )

    def fill(
        self, order_id: str, asset_id: str, held: Decimal, quantity: Decimal, unit_value: Decimal
    ) -> StrategyCapital:
        """Book this strategy's share of a fill: ``quantity`` of ``asset_id``, held ``held`` before.

        ``unit_value`` is one unit's executed value in the budget's currency,
        a magnitude. What the fill deploys is converted from the order's
        reservation, up to what it reserves.
        """

        ctx = ACCOUNTING_CONTEXT
        after = ctx.add(held, quantity)
        cost = self.deployed.get(asset_id, _ZERO)
        added = adding_exposure(held, quantity)
        if held != 0 and ((held > 0) != (after > 0) or after == 0):
            # Closed or crossed: the old side is released whole.
            cost = _ZERO
        elif abs(after) < abs(held):
            # Reduced: release the same fraction of what the position cost.
            cost = ctx.divide(ctx.multiply(cost, abs(after)), abs(held))
        deploys = ctx.multiply(added, unit_value)
        cost = ctx.add(cost, deploys)
        before = self.deployed.get(asset_id)
        if cost != 0:
            deployed = self.deployed.set(asset_id, cost)
        elif before is not None:
            deployed = self.deployed.delete(asset_id)
        else:
            deployed = self.deployed
        before = _ZERO if before is None else before
        deployed_total = ctx.add(ctx.subtract(self.deployed_total, before), cost)

        reserved, reserved_total = self.reserved, self.reserved_total
        held_back = reserved.get(order_id)
        if held_back is not None and deploys > 0:
            converted = min(held_back, deploys)
            remaining = ctx.subtract(held_back, converted)
            reserved = (
                reserved.set(order_id, remaining) if remaining > 0 else reserved.delete(order_id)
            )
            reserved_total = ctx.subtract(reserved_total, converted)
        return StrategyCapital(deployed, reserved, deployed_total, reserved_total)
