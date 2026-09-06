"""Netting Engine for resolving multi-strategy conflicts."""

from collections.abc import Sequence
from decimal import Decimal

from alphalab.core.contribution import StrategyContribution, contributions_from


class NettingEngine:
    """Pure functional netting engine across strategy requests."""

    @staticmethod
    def net_quantities(sized_intents: Sequence[tuple[str, str, Decimal]]) -> dict[str, Decimal]:
        """
        Aggregates identical asset requests across strategies.
        Returns net quantities per asset.
        """
        net_positions: dict[str, Decimal] = {}

        for _strategy_id, asset_id, qty in sized_intents:
            current = net_positions.get(asset_id, Decimal("0.00"))
            net_positions[asset_id] = current + qty

        return net_positions

    @staticmethod
    def contributions_by_asset(
        sized_intents: Sequence[tuple[str, str, Decimal]],
    ) -> dict[str, tuple[StrategyContribution, ...]]:
        """Who asked for each asset, and for how much, before netting.

        The companion to :meth:`net_quantities`: that one says *what* the batch
        nets to, this one says *who* it nets from. Both read the same triples,
        so a contribution can never describe an order the netting did not
        produce.
        """

        by_asset: dict[str, list[tuple[str, Decimal]]] = {}
        for strategy_id, asset_id, qty in sized_intents:
            by_asset.setdefault(asset_id, []).append((strategy_id, qty))
        return {asset_id: contributions_from(rows) for asset_id, rows in by_asset.items()}
