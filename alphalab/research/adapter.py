"""Adapter translating AlphaLab domain objects to Research Payloads."""

from typing import Any

from alphalab.research.protocol import ResearchPayload, TradePayload


class ResearchAdapter:
    """Stateless translator mapping generic framework outputs to the Research Engine."""

    @staticmethod
    def to_research_payload(
        strategy_id: str,
        returns: tuple[float, ...],
        trades: tuple[dict[str, Any], ...],
        parameters: dict[str, float],
        market_regimes: tuple[str, ...],
        aum: float,
    ) -> ResearchPayload:

        parsed_trades = tuple(
            TradePayload(
                trade_id=str(t.get("trade_id", "")),
                symbol=str(t.get("symbol", "")),
                entry_price=float(t.get("entry_price", 0.0)),
                exit_price=float(t.get("exit_price", 0.0)),
                quantity=float(t.get("quantity", 0.0)),
                pnl=float(t.get("pnl", 0.0)),
                duration_seconds=float(t.get("duration_seconds", 0.0)),
            )
            for t in trades
        )

        return ResearchPayload(
            strategy_id=strategy_id,
            returns=returns,
            trades=parsed_trades,
            parameters=parameters,
            market_regimes=market_regimes,
            aum=aum,
        )
