"""Position sizing models translating Strategy Intents into trade quantities.

Every model sizes a **signed order delta** -- how much to buy (positive) or sell
(negative) now -- and none of them reads the current position: emitting the same
intent twice asks for the quantity twice. That is what an
:class:`~alphalab.strategy.events.Intent` of kind
:attr:`~alphalab.strategy.events.IntentKind.DELTA` means (ledger ALC-002).

A model that needs a price refuses a non-positive one, and
:class:`VolatilityTargetSizing` refuses an instrument with no positive
volatility, by raising :class:`~alphalab.allocation.exceptions.SizingRefusedError`.
Until v3.10 they returned zero -- so the intent vanished, and for an unpriced
instrument never reached the run's unpriced-asset record -- or sized against an
assumed volatility of 1% (ledger ALC-003).
"""

from collections.abc import Mapping
from decimal import Decimal
from types import MappingProxyType
from typing import Protocol

from alphalab.allocation.budget import CapitalBudget
from alphalab.allocation.exceptions import AllocationValidationError, SizingRefusedError
from alphalab.strategy.events import Intent

#: The quantum every model sizes to.
QUANTITY_QUANTUM = Decimal("0.000001")


class SizingModel(Protocol):
    """Protocol for sizing algorithms.

    ``price`` is the instrument's market price in its own currency, and **zero
    when the run has observed none** -- which a model that needs a price must
    refuse with :class:`~alphalab.allocation.exceptions.SizingRefusedError`
    rather than size against.
    """

    def calculate(self, intent: Intent, budget: CapitalBudget, price: Decimal) -> Decimal: ...


def _require_price(intent: Intent, price: Decimal) -> Decimal:
    if not price.is_finite() or price <= 0:
        raise SizingRefusedError(
            f"Cannot size {intent.strategy_id}'s intent for {intent.instrument}: it has no "
            f"positive price (got {price}), and a quantity sized from a notional needs one."
        )
    return price


def _sign(intent: Intent) -> Decimal:
    return Decimal("1") if intent.target >= 0 else Decimal("-1")


class FixedQuantitySizing:
    """Sizes the delta as the quantity the intent states, scaled by its strength."""

    def calculate(self, intent: Intent, budget: CapitalBudget, price: Decimal) -> Decimal:
        return (intent.target * intent.strength).quantize(QUANTITY_QUANTUM)


class FixedDollarSizing:
    """Sizes the delta as the notional the intent states, in the instrument's currency."""

    def calculate(self, intent: Intent, budget: CapitalBudget, price: Decimal) -> Decimal:
        raw_qty = (intent.target * intent.strength) / _require_price(intent, price)
        return raw_qty.quantize(QUANTITY_QUANTUM)


class TargetWeightSizing:
    """Sizes a delta worth ``target`` of the strategy's available capital.

    Despite the name it is a delta, not a target: it does not read the current
    position, so an intent emitted on every event buys on every event. A
    position-aware target intent is planned for v3.11 (ledger ALC-002).
    """

    def calculate(self, intent: Intent, budget: CapitalBudget, price: Decimal) -> Decimal:
        price = _require_price(intent, price)
        capital = budget.available_strategy_capital(intent.strategy_id)
        target_dollar = capital * intent.target * intent.strength
        return (target_dollar / price).quantize(QUANTITY_QUANTUM)


class EqualWeightSizing:
    """Sizes a delta worth an equal share of the strategy's capital.

    The intent's magnitude is disregarded; its sign gives the direction.

    Raises:
        AllocationValidationError: If ``num_assets`` is less than one. Until
            v3.10 it was silently raised to one.
    """

    __slots__ = ("_num_assets",)

    def __init__(self, num_assets: int) -> None:
        if num_assets < 1:
            raise AllocationValidationError(
                f"EqualWeightSizing shares capital between at least one asset, got {num_assets}."
            )
        self._num_assets = num_assets

    def calculate(self, intent: Intent, budget: CapitalBudget, price: Decimal) -> Decimal:
        price = _require_price(intent, price)
        capital = budget.available_strategy_capital(intent.strategy_id)
        target_dollar = (capital / Decimal(self._num_assets)) * intent.strength
        return (_sign(intent) * target_dollar / price).quantize(QUANTITY_QUANTUM)


class VolatilityTargetSizing:
    """Sizes a delta inversely proportional to the instrument's volatility.

    ``asset_vols`` is copied at construction, so a mapping the caller changes
    later does not change what this model sizes.

    Raises:
        AllocationValidationError: If ``target_vol`` is not a finite, positive
            number.
    """

    __slots__ = ("_asset_vols", "_target_vol")

    def __init__(self, target_vol: Decimal, asset_vols: Mapping[str, Decimal]) -> None:
        if not target_vol.is_finite() or target_vol <= 0:
            raise AllocationValidationError(
                f"target_vol must be a finite, positive volatility, got {target_vol}."
            )
        self._target_vol = target_vol
        self._asset_vols: Mapping[str, Decimal] = MappingProxyType(dict(asset_vols))

    def calculate(self, intent: Intent, budget: CapitalBudget, price: Decimal) -> Decimal:
        price = _require_price(intent, price)
        vol = self._asset_vols.get(intent.instrument)
        if vol is None or not vol.is_finite() or vol <= 0:
            stated = "no volatility" if vol is None else f"a volatility of {vol}"
            raise SizingRefusedError(
                f"Cannot size {intent.strategy_id}'s intent for {intent.instrument}: the model "
                f"was given {stated} for it. None is assumed -- until v3.10 a missing one "
                "was taken as 1%, which sizes a position from a number nobody supplied."
            )

        capital = budget.available_strategy_capital(intent.strategy_id)

        # Volatility scaling: (Target Vol / Asset Vol) * Capital
        exposure = (self._target_vol / vol) * capital * intent.strength
        return (_sign(intent) * exposure / price).quantize(QUANTITY_QUANTUM)
