"""Netting Engine for resolving multi-strategy conflicts."""

from collections.abc import Sequence
from decimal import Decimal

from alphalab.common.order_terms import OrderTerms
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

    @staticmethod
    def net_by_terms(
        sized_intents: Sequence[tuple[str, str, OrderTerms, Decimal]],
    ) -> dict[tuple[str, OrderTerms], Decimal]:
        """Net quantities per asset *and terms*, in the order each pair first appears.

        Since v3.11 an intent carries its order's terms (ledger EXE-003), and only
        intents with equal terms net: a buy at a limit and a sell at the market
        are two orders, not one market order for the difference. Every intent
        before v3.11 had the same terms, so this nets exactly as
        :meth:`net_quantities` does.
        """

        net: dict[tuple[str, OrderTerms], Decimal] = {}
        for _strategy_id, asset_id, terms, qty in sized_intents:
            key = (asset_id, terms)
            net[key] = net.get(key, Decimal("0.00")) + qty
        return net

    @staticmethod
    def contributions_by_terms(
        sized_intents: Sequence[tuple[str, str, OrderTerms, Decimal]],
    ) -> dict[tuple[str, OrderTerms], tuple[StrategyContribution, ...]]:
        """Who asked for each (asset, terms) order, and for how much, before netting."""

        by_key: dict[tuple[str, OrderTerms], list[tuple[str, Decimal]]] = {}
        for strategy_id, asset_id, terms, qty in sized_intents:
            by_key.setdefault((asset_id, terms), []).append((strategy_id, qty))
        return {key: contributions_from(rows) for key, rows in by_key.items()}
