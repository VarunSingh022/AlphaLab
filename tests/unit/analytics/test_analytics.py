"""Comprehensive tests validating risk and return calculations."""

from decimal import Decimal

import pytest

from alphalab.analytics import (
    AnalyticsEngine,
    AnalyticsValidationError,
    PortfolioSnapshot,
    TradeRecord,
    annualized_volatility,
    cagr,
    calculate_attribution,
    calculate_drawdowns,
    calculate_exposure,
    calculate_trade_metrics,
    calmar_ratio,
    conditional_var,
    geometric_return,
    latest_performance_summary,
    rolling_return,
    rolling_sharpe,
    sharpe_ratio,
    sortino_ratio,
    total_return,
    validate_capital,
    validate_returns,
    value_at_risk,
)


def test_validation_nan() -> None:
    with pytest.raises(AnalyticsValidationError, match="NaN"):
        validate_returns((0.01, float("nan"), 0.05))


def test_validation_capital() -> None:
    with pytest.raises(AnalyticsValidationError, match="Negative"):
        validate_capital(Decimal("-100.00"))


def test_total_return() -> None:
    assert total_return(Decimal("100.00"), Decimal("150.00")) == 0.50
    assert total_return(Decimal("100.00"), Decimal("50.00")) == -0.50
    assert total_return(Decimal("0.00"), Decimal("150.00")) == 0.0


def test_cagr() -> None:
    # 100 -> 200 in 2 years is roughly 41.4%
    res = cagr(Decimal("100.00"), Decimal("200.00"), 2.0)
    assert round(res, 4) == 0.4142


def test_geometric_return() -> None:
    # +10%, -10% => 1.1 * 0.9 = 0.99 => sqrt(0.99) - 1 = -0.00501
    ret = geometric_return((0.10, -0.10))
    assert round(ret, 5) == -0.00501
    assert geometric_return(()) == 0.0


def test_annualized_volatility() -> None:
    returns = (0.01, -0.01, 0.02, -0.02)
    # Stdev is ~0.018257. Ann Vol (252) = ~0.2898
    vol = annualized_volatility(returns)
    assert round(vol, 4) == 0.2898
    assert annualized_volatility((0.01,)) == 0.0


def test_drawdowns() -> None:
    curve = (100.0, 110.0, 99.0, 90.0, 120.0)
    # Peak starts 100, then 110.
    # At 99, dd is (110-99)/110 = 0.1
    # At 90, dd is (110-90)/110 = 0.1818
    # Max DD should be ~0.1818
    dd_metrics = calculate_drawdowns(curve)
    assert round(dd_metrics.max_drawdown, 4) == 0.1818
    assert len(dd_metrics.drawdowns) == 5
    assert dd_metrics.ulcer_index > 0.0


def test_sharpe_ratio() -> None:
    returns = (0.01, 0.02, 0.01, -0.01, 0.01)
    # Mean = 0.008, Stdev = ~0.01095, Sharpe = ~11.59
    sharpe = sharpe_ratio(returns, risk_free_rate=0.0)
    assert round(sharpe, 2) == 11.59


def test_sortino_ratio() -> None:
    returns = (0.01, 0.02, -0.01, -0.05, 0.03)
    sortino = sortino_ratio(returns)
    # Mean = 0.00, Sortino = 0.0
    assert sortino == 0.0


def test_calmar_ratio() -> None:
    assert calmar_ratio(0.20, 0.10) == 2.0
    assert calmar_ratio(0.20, 0.0) == 0.0


def test_var_and_cvar() -> None:
    returns = tuple(float(x) / 100.0 for x in range(-10, 11))
    # 21 returns, -0.10 to 0.10. 95% Var means the worst 5%.
    # 5th percentile of 21 elements is the 2nd element (-0.09)
    var = value_at_risk(returns, 0.95)
    assert var == -0.09

    # CVaR is mean of returns <= -0.09 -> (-0.10 + -0.09) / 2 = -0.095
    cvar = conditional_var(returns, 0.95)
    assert cvar == -0.095


def test_exposure() -> None:
    exp = calculate_exposure(long_val=Decimal("100"), short_val=Decimal("-50"), cash=Decimal("50"))
    assert exp.gross == Decimal("150")
    assert exp.net == Decimal("50")
    # Total Equity = cash (50) + net (50) = 100
    assert exp.cash_pct == 0.50
    assert exp.leverage == 1.50


def test_trade_metrics() -> None:
    profits = (Decimal("100"), Decimal("-50"), Decimal("200"), Decimal("-150"))
    periods = (100.0, 200.0, 100.0, 200.0)

    metrics = calculate_trade_metrics(
        profits, periods, total_traded_notional=Decimal("10000"), average_equity=Decimal("1000")
    )

    assert metrics.win_rate == 0.50
    assert metrics.loss_rate == 0.50
    assert metrics.avg_win == Decimal("150.0000")
    assert metrics.avg_loss == Decimal("-100.0000")
    # Gross Profit = 300, Gross Loss = 200 -> PF = 1.5
    assert metrics.profit_factor == 1.5
    # Expectancy = (0.5 * 150) + (0.5 * -100) = 75 - 50 = 25
    assert metrics.expectancy == Decimal("25.0000")
    assert metrics.turnover == 10.0


def test_attribution() -> None:
    trades = (
        TradeRecord("T1", "STRAT1", "AAPL", "TECH", Decimal("100"), Decimal("1000"), 10.0),
        TradeRecord("T2", "STRAT2", "AAPL", "TECH", Decimal("-50"), Decimal("1000"), 10.0),
        TradeRecord("T3", "STRAT1", "MSFT", "TECH", Decimal("200"), Decimal("1000"), 10.0),
    )
    attr = calculate_attribution(trades)

    assert attr.pnl_by_strategy["STRAT1"] == Decimal("300")
    assert attr.pnl_by_strategy["STRAT2"] == Decimal("-50")
    assert attr.pnl_by_asset["AAPL"] == Decimal("50")
    assert attr.pnl_by_sector["TECH"] == Decimal("250")


def test_rolling_windows() -> None:
    returns = (0.01, 0.02, -0.01, 0.03, 0.01)

    rr = rolling_return(returns, 3)
    assert len(rr) == 3
    # First window: 0.01, 0.02, -0.01 => (1.01 * 1.02 * 0.99) ^ (1/3) - 1

    rs = rolling_sharpe(returns, 3)
    assert len(rs) == 3


def test_engine_integration() -> None:
    state = AnalyticsEngine.initialize()

    snapshots = (
        PortfolioSnapshot(
            100.0, Decimal("1000.00"), Decimal("1000.00"), Decimal("0"), Decimal("0")
        ),
        PortfolioSnapshot(
            101.0, Decimal("1010.00"), Decimal("1000.00"), Decimal("10"), Decimal("0")
        ),
        PortfolioSnapshot(
            102.0, Decimal("1050.00"), Decimal("1000.00"), Decimal("50"), Decimal("0")
        ),
        PortfolioSnapshot(
            103.0, Decimal("990.00"), Decimal("1000.00"), Decimal("-10"), Decimal("0")
        ),
    )

    trades = (
        TradeRecord("T1", "S1", "AAPL", "SEC1", Decimal("10.00"), Decimal("100"), 60.0),
        TradeRecord("T2", "S1", "AAPL", "SEC1", Decimal("40.00"), Decimal("400"), 60.0),
        TradeRecord("T3", "S1", "AAPL", "SEC1", Decimal("-60.00"), Decimal("600"), 60.0),
    )

    state = AnalyticsEngine.compile_report(state, snapshots, trades, 200.0)

    report = latest_performance_summary(state)
    assert report is not None
    assert report.returns.total_return == -0.01  # 1000 to 990
    assert report.ending_capital == Decimal("990.00")
    assert report.trades.win_rate == pytest.approx(0.666, 0.01)

    # Immutability
    assert len(state.reports) == 1
    assert len(state.events) == 1
