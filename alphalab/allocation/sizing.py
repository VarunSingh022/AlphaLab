"""Position sizing models translating Strategy Intents into trade quantities."""

from decimal import Decimal
from typing import Protocol

from alphalab.allocation.budget import CapitalBudget
from alphalab.strategy.events import Intent


class SizingModel(Protocol):
    """Protocol for sizing algorithms."""

    def calculate(self, intent: Intent, budget: CapitalBudget, price: Decimal) -> Decimal: ...


class FixedQuantitySizing:
    """Sizes positions based on absolute quantities requested by the strategy."""

    def calculate(self, intent: Intent, budget: CapitalBudget, price: Decimal) -> Decimal:
        return (intent.target * intent.strength).quantize(Decimal("0.000001"))


class FixedDollarSizing:
    """Sizes positions based on a fixed dollar amount requested by the strategy."""

    def calculate(self, intent: Intent, budget: CapitalBudget, price: Decimal) -> Decimal:
        if price <= Decimal("0.00"):
            return Decimal("0.00")
        raw_qty = (intent.target * intent.strength) / price
        return raw_qty.quantize(Decimal("0.000001"))


class TargetWeightSizing:
    """Sizes positions based on a target weight of available capital."""

    def calculate(self, intent: Intent, budget: CapitalBudget, price: Decimal) -> Decimal:
        if price <= Decimal("0.00"):
            return Decimal("0.00")

        capital = budget.available_strategy_capital(intent.strategy_id)
        target_dollar = capital * intent.target * intent.strength
        return (target_dollar / price).quantize(Decimal("0.000001"))


class EqualWeightSizing:
    """Sizes positions equally, disregarding the Intent's specific target magnitude."""

    __slots__ = ("_num_assets",)

    def __init__(self, num_assets: int) -> None:
        self._num_assets = max(1, num_assets)

    def calculate(self, intent: Intent, budget: CapitalBudget, price: Decimal) -> Decimal:
        if price <= Decimal("0.00"):
            return Decimal("0.00")

        capital = budget.available_strategy_capital(intent.strategy_id)
        target_dollar = (capital / Decimal(str(self._num_assets))) * intent.strength
        # Maintain directional sign of the intent's target even if ignoring magnitude
        sign = Decimal("1") if intent.target >= Decimal("0") else Decimal("-1")
        return (sign * target_dollar / price).quantize(Decimal("0.000001"))


class VolatilityTargetSizing:
    """Sizes positions inversely proportional to asset volatility."""

    __slots__ = ("_asset_vols", "_target_vol")

    def __init__(self, target_vol: Decimal, asset_vols: dict[str, Decimal]) -> None:
        self._target_vol = target_vol
        self._asset_vols = asset_vols

    def calculate(self, intent: Intent, budget: CapitalBudget, price: Decimal) -> Decimal:
        if price <= Decimal("0.00"):
            return Decimal("0.00")

        vol = self._asset_vols.get(intent.instrument, Decimal("0.01"))
        if vol <= Decimal("0.00"):
            vol = Decimal("0.01")

        capital = budget.available_strategy_capital(intent.strategy_id)

        # Volatility scaling: (Target Vol / Asset Vol) * Capital
        exposure = (self._target_vol / vol) * capital * intent.strength
        sign = Decimal("1") if intent.target >= Decimal("0") else Decimal("-1")

        return (sign * exposure / price).quantize(Decimal("0.000001"))
