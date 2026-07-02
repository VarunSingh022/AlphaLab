"""Netting Engine for resolving multi-strategy conflicts."""

from decimal import Decimal


class NettingEngine:
    """Pure functional netting engine across strategy requests."""

    @staticmethod
    def net_quantities(sized_intents: list[tuple[str, Decimal]]) -> dict[str, Decimal]:
        """
        Aggregates identical asset requests across strategies.
        Returns net quantities per asset.
        """
        net_positions: dict[str, Decimal] = {}

        for asset_id, qty in sized_intents:
            current = net_positions.get(asset_id, Decimal("0.00"))
            net_positions[asset_id] = current + qty

        return net_positions
