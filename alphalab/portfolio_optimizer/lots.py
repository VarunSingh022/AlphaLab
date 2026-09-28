"""Turning constructed weights into quantities a venue will accept (ledger OFE-002).

:func:`~alphalab.portfolio_optimizer.construction.construct` answers in weights:
fractions of capital, real numbers. A venue trades quantities: whole lots of an
instrument, at least its minimum, each unit worth its price times its
multiplier. :func:`round_to_lots` is the step between, stated rather than left
to every caller -- and reported, because rounding moves a portfolio away from
the one that was optimized and by how much is part of the answer.

The rule
--------

For an asset with weight ``w``, at price ``p`` with multiplier ``m``, out of
``capital``: the quantity ``w * capital / (p * m)`` is computed at the
accounting precision, then rounded **toward zero** -- first to the caller's
``quantum``, then to the instrument's lot, where one is declared. Toward zero,
because rounding a position *up* would add exposure the construction did not
choose, and could breach a constraint it certified; a long shrinks and a short
shrinks. An asset whose weight is not zero but whose rounded quantity is -- less
than one lot, or than the minimum -- is named in
:attr:`LotRounding.below_one_lot`, not silently dropped.

Rounding toward zero keeps every per-asset bound and cap the construction
certified: ``|rounded| <= |target|`` asset by asset. It does not keep an
equality -- a budget of exactly one is under-spent by the residuals -- and it
does not re-optimize: the rounded portfolio is the nearest tradable one below
the optimum, asset by asset, not the best tradable one. Choosing lots jointly
is an integer program, which this library does not solve (ledger OFE-002: an
explicit boundary, with cardinality).

A weight is a float; it enters decimal arithmetic by its shortest round-trip
spelling (``repr``), so the weight ``0.1`` is the decimal ``0.1``, not the
binary fraction nearest it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from types import MappingProxyType

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT
from alphalab.conventions.economics import InstrumentEconomics
from alphalab.conventions.exceptions import ConventionViolationError
from alphalab.conventions.lot import round_down_to_lot
from alphalab.portfolio_optimizer.exceptions import ConstructionInputError

__all__ = ["LotRounding", "round_to_lots"]


@dataclass(frozen=True, slots=True)
class LotRounding:
    """What rounding a constructed portfolio to tradable quantities did.

    Every mapping covers every asset of the weights rounded, in sorted order.

    Attributes:
        quantities: Asset -> the signed tradable quantity.
        notionals: Asset -> ``quantity * price * multiplier``, signed.
        weights: Asset -> the weight the quantity is: its notional over the
            capital.
        residuals: Asset -> the target weight less the rounded one. Same sign as
            the target (or zero): rounding is toward zero.
        below_one_lot: Assets whose target weight was not zero and whose
            rounded quantity is.
    """

    quantities: Mapping[str, Decimal]
    notionals: Mapping[str, Decimal]
    weights: Mapping[str, float]
    residuals: Mapping[str, float]
    below_one_lot: tuple[str, ...]

    @property
    def residual_weight(self) -> float:
        """``sum_i |residual_i|``: how much of the portfolio rounding left out."""

        return math.fsum(abs(value) for value in self.residuals.values())


def round_to_lots(
    weights: Mapping[str, float],
    *,
    capital: Decimal,
    prices: Mapping[str, Decimal],
    economics: Mapping[str, InstrumentEconomics],
    quantum: Decimal,
) -> LotRounding:
    """Round ``weights`` of ``capital`` to tradable quantities. See the module.

    Args:
        weights: Asset -> weight, as :attr:`ConstructionResult.weights` gives it.
        capital: What the weights are fractions of, in the prices' currency.
        prices: Asset -> the price one unit is sized at. Positive: a weight
            cannot be turned into a quantity at a price of zero or below.
            Required for every asset with a non-zero weight.
        economics: Asset -> the instrument's declared economics, for its
            multiplier and lot (:data:`~alphalab.conventions.economics.CASH_EQUITY`
            for a fully paid unit of one with no grid). Required for every
            asset with a non-zero weight; nothing is assumed of the others.
        quantum: The finest quantity step for an asset with no declared lot,
            and the step every quantity is first rounded to. Positive.

    Raises:
        ConstructionInputError: If a weight is not finite; the capital, the
            quantum or a needed price is not a positive finite ``Decimal``; a
            needed price or economics is missing; ``prices`` or ``economics``
            names an asset that is not among the weights; or a quantity has
            more digits than can be counted in lots exactly.
    """

    if not isinstance(capital, Decimal) or not capital.is_finite() or capital <= 0:
        raise ConstructionInputError(f"capital is {capital!r}; it must be a positive Decimal.")
    if not isinstance(quantum, Decimal) or not quantum.is_finite() or quantum <= 0:
        raise ConstructionInputError(f"quantum is {quantum!r}; it must be a positive Decimal.")
    universe = sorted(weights)
    for name, given in (("prices", prices), ("economics", economics)):
        stray = sorted(set(given) - set(universe))
        if stray:
            raise ConstructionInputError(f"{name} names assets that are not weighted: {stray}.")
    ctx = ACCOUNTING_CONTEXT
    quantities: dict[str, Decimal] = {}
    notionals: dict[str, Decimal] = {}
    rounded_weights: dict[str, float] = {}
    residuals: dict[str, float] = {}
    below: list[str] = []
    for asset in universe:
        weight = weights[asset]
        if isinstance(weight, bool) or not isinstance(weight, int | float):
            raise ConstructionInputError(f"The weight of {asset!r} is {weight!r}, not a number.")
        weight = float(weight)
        if not math.isfinite(weight):
            raise ConstructionInputError(f"The weight of {asset!r} is {weight!r}; it is finite.")
        if weight == 0.0:
            quantities[asset] = Decimal(0)
            notionals[asset] = Decimal(0)
            rounded_weights[asset] = 0.0
            residuals[asset] = 0.0
            continue
        price = prices.get(asset)
        declared = economics.get(asset)
        if price is None or declared is None:
            raise ConstructionInputError(
                f"{asset!r} is weighted {weight!r} but has no "
                f"{'price' if price is None else 'economics'} to size it by."
            )
        if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
            raise ConstructionInputError(
                f"The price of {asset!r} is {price!r}; a weight is sized only at a positive "
                "price -- state a quantity instead."
            )
        if not isinstance(declared, InstrumentEconomics):
            raise ConstructionInputError(
                f"The economics of {asset!r} are {declared!r}, not InstrumentEconomics."
            )
        unit = ctx.multiply(price, declared.multiplier)
        try:
            target = ctx.divide(ctx.multiply(capital, Decimal(repr(weight))), unit)
            quantity = target.quantize(quantum, rounding=ROUND_DOWN, context=ctx)
            if declared.lot is not None:
                quantity = round_down_to_lot(quantity, declared.lot)
        except (InvalidOperation, ConventionViolationError) as exc:
            raise ConstructionInputError(
                f"{asset!r}: {weight!r} of {capital} at {price} cannot be counted in "
                f"{quantum} steps exactly ({exc})."
            ) from exc
        notional = ctx.multiply(quantity, unit)
        quantities[asset] = quantity
        notionals[asset] = notional
        rounded = float(ctx.divide(notional, capital))
        rounded_weights[asset] = rounded
        residuals[asset] = weight - rounded
        if quantity == 0:
            below.append(asset)
    return LotRounding(
        quantities=MappingProxyType(quantities),
        notionals=MappingProxyType(notionals),
        weights=MappingProxyType(rounded_weights),
        residuals=MappingProxyType(residuals),
        below_one_lot=tuple(below),
    )
