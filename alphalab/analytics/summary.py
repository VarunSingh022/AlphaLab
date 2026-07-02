"""Trade performance summaries."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class TradeMetrics:
    """Immutable aggregate statistics over historical trade executions."""

    win_rate: float
    loss_rate: float
    avg_win: Decimal
    avg_loss: Decimal
    profit_factor: float
    expectancy: Decimal
    avg_holding_period: float
    turnover: float


def calculate_trade_metrics(
    profits: tuple[Decimal, ...],
    holding_periods: tuple[float, ...],
    total_traded_notional: Decimal,
    average_equity: Decimal,
) -> TradeMetrics:
    """Computes comprehensive trade efficiency and expectancy metrics."""
    if not profits:
        return TradeMetrics(0.0, 0.0, Decimal("0"), Decimal("0"), 0.0, Decimal("0"), 0.0, 0.0)

    wins = [p for p in profits if p > Decimal("0")]
    losses = [p for p in profits if p <= Decimal("0")]

    num_trades = len(profits)
    win_rate = len(wins) / num_trades
    loss_rate = len(losses) / num_trades

    avg_win = sum(wins, Decimal("0")) / len(wins) if wins else Decimal("0")
    avg_loss = sum(losses, Decimal("0")) / len(losses) if losses else Decimal("0")

    gross_profit = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))

    if gross_loss == Decimal("0"):
        profit_factor = float("inf") if gross_profit > Decimal("0") else 0.0
    else:
        profit_factor = float(gross_profit / gross_loss)

    expectancy = (Decimal(str(win_rate)) * avg_win) + (Decimal(str(loss_rate)) * avg_loss)
    avg_hold = sum(holding_periods) / len(holding_periods) if holding_periods else 0.0

    turnover = (
        float(total_traded_notional / average_equity) if average_equity > Decimal("0") else 0.0
    )

    return TradeMetrics(
        win_rate=win_rate,
        loss_rate=loss_rate,
        avg_win=avg_win.quantize(Decimal("0.0001")),
        avg_loss=avg_loss.quantize(Decimal("0.0001")),
        profit_factor=profit_factor,
        expectancy=expectancy.quantize(Decimal("0.0001")),
        avg_holding_period=avg_hold,
        turnover=turnover,
    )
