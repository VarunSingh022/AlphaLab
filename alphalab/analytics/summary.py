"""Trade performance summaries.

What counts as a trade
----------------------
The execution path writes one :class:`~alphalab.analytics.attribution.TradeRecord`
per *fill*, and an opening fill realizes nothing. Until v3.10 every record was
counted, and an opening fill -- realized P&L zero -- was classified as a loss:
one winning round trip (open, then close at a profit) reported a 50% win rate
and a profit factor computed over a loss that never happened.

Trade statistics are now computed over **closing** fills only: those that
reduced or closed a position, which is exactly the set with a holding period
(an opening or increasing fill has none) or with realized P&L. The count basis
is recorded on the result as :attr:`TradeMetrics.closed_trades`, beside
:attr:`TradeMetrics.fills`, so a reader can see what the rates were taken over.
A closing fill that realized exactly zero is a *scratch*: neither a win nor a
loss.
"""

from dataclasses import dataclass
from decimal import Decimal

from alphalab.common.arithmetic import ACCOUNTING_CONTEXT

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class TradeMetrics:
    """Immutable aggregate statistics over the fills that closed or reduced a position.

    Every statistic is ``None`` when it is undefined -- a win rate over no
    closing trades, a profit factor with no losses, a turnover over no equity --
    rather than a zero that would read as a measurement.

    Attributes:
        fills: How many fill records were supplied.
        closed_trades: How many of them closed or reduced a position -- the
            count every rate below is taken over.

        Both are ``None`` on a report written before v3.10, which recorded
        neither (and took its rates over every fill).
    """

    win_rate: float | None
    loss_rate: float | None
    avg_win: Decimal | None
    avg_loss: Decimal | None
    profit_factor: float | None
    expectancy: Decimal | None
    avg_holding_period: float | None
    turnover: float | None
    fills: int | None = None
    closed_trades: int | None = None


def calculate_trade_metrics(
    profits: tuple[Decimal, ...],
    holding_periods: tuple[float | None, ...],
    total_traded_notional: Decimal,
    average_equity: Decimal,
) -> TradeMetrics:
    """Computes trade efficiency and expectancy metrics over closing fills.

    ``profits[i]`` and ``holding_periods[i]`` describe the same fill. A fill is
    a closing one when it has a holding period or realized something; an
    opening or increasing fill -- no holding period, nothing realized -- is left
    out of every rate, and ``avg_holding_period`` averages only the periods that
    were measured.

    Turnover is taken over *every* fill's notional: an opening trade turns the
    book over as much as a closing one.

    Raises:
        ValueError: If the two sequences differ in length.
    """

    if len(profits) != len(holding_periods):
        raise ValueError(
            f"{len(profits)} profits and {len(holding_periods)} holding periods do not "
            "describe the same fills."
        )

    closing = [
        profit
        for profit, held in zip(profits, holding_periods, strict=True)
        if held is not None or profit != _ZERO
    ]
    measured = tuple(period for period in holding_periods if period is not None)
    avg_hold = sum(measured) / len(measured) if measured else None

    turnover = (
        float(ACCOUNTING_CONTEXT.divide(total_traded_notional, average_equity))
        if average_equity > _ZERO
        else None
    )

    if not closing:
        return TradeMetrics(None, None, None, None, None, None, avg_hold, turnover, len(profits), 0)

    ctx = ACCOUNTING_CONTEXT
    wins = [p for p in closing if p > _ZERO]
    losses = [p for p in closing if p < _ZERO]
    count = len(closing)

    win_rate = len(wins) / count
    loss_rate = len(losses) / count

    gross_profit = sum(wins, _ZERO)
    gross_loss = abs(sum(losses, _ZERO))
    avg_win = ctx.divide(gross_profit, Decimal(len(wins))) if wins else None
    avg_loss = ctx.divide(-gross_loss, Decimal(len(losses))) if losses else None

    # Undefined when nothing was lost: the ratio is infinite (or 0/0), and
    # neither is a number a report can carry. Until v3.10 it was float("inf").
    profit_factor = float(ctx.divide(gross_profit, gross_loss)) if gross_loss > _ZERO else None

    # The mean realized P&L per closing trade, which is what win rate times
    # average win plus loss rate times average loss is, computed without the
    # float round trip the v3.9 formula took through the rates.
    expectancy = ctx.divide(sum(closing, _ZERO), Decimal(count))

    return TradeMetrics(
        win_rate=win_rate,
        loss_rate=loss_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        expectancy=expectancy,
        avg_holding_period=avg_hold,
        turnover=turnover,
        fills=len(profits),
        closed_trades=count,
    )
