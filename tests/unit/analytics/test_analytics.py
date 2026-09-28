"""Comprehensive tests validating risk and return calculations."""

from decimal import Decimal

import pytest

from alphalab.analytics import (
    AnalyticsEngine,
    AnalyticsValidationError,
    PortfolioSnapshot,
    ReportGenerated,
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
from alphalab.core.contribution import StrategyContribution

#: A sole contributor. The quantity only sets the weight, and a single
#: contribution always weighs 1 whatever its magnitude.
ONE = Decimal("1")


def test_validation_nan() -> None:
    with pytest.raises(AnalyticsValidationError, match="NaN"):
        validate_returns((0.01, float("nan"), 0.05))


def test_validation_capital() -> None:
    with pytest.raises(AnalyticsValidationError, match="Negative"):
        validate_capital(Decimal("-100.00"))


def test_total_return() -> None:
    assert total_return(Decimal("100.00"), Decimal("150.00")) == 0.50
    assert total_return(Decimal("100.00"), Decimal("50.00")) == -0.50
    # Undefined from a non-positive start: None, not 0.0 (v3.10, ANA-004).
    assert total_return(Decimal("0.00"), Decimal("150.00")) is None


def test_cagr() -> None:
    # 100 -> 200 in 2 years is roughly 41.4%
    res = cagr(Decimal("100.00"), Decimal("200.00"), 2.0)
    assert res is not None and round(res, 4) == 0.4142
    # A total loss is -100% a year, not a flat 0% (v3.10).
    assert cagr(Decimal("100.00"), Decimal("0.00"), 2.0) == -1.0
    assert cagr(Decimal("100.00"), Decimal("200.00"), 0.0) is None


def test_geometric_return() -> None:
    # +10%, -10% => 1.1 * 0.9 = 0.99 => sqrt(0.99) - 1 = -0.00501
    ret = geometric_return((0.10, -0.10))
    assert ret is not None and round(ret, 5) == -0.00501
    assert geometric_return(()) is None


def test_annualized_volatility() -> None:
    returns = (0.01, -0.01, 0.02, -0.02)
    # Stdev is ~0.018257. Ann Vol (252) = ~0.2898. The periods are stated.
    vol = annualized_volatility(returns, 252)
    assert vol is not None and round(vol, 4) == 0.2898
    assert annualized_volatility((0.01,), 252) is None


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
    sharpe = sharpe_ratio(returns, risk_free_rate=0.0, periods=252)
    assert sharpe is not None and round(sharpe, 2) == 11.59
    # A constant series has no dispersion and no Sharpe ratio.
    assert sharpe_ratio((0.01, 0.01, 0.01), 0.0, 252) is None


def test_sortino_ratio() -> None:
    returns = (0.01, 0.02, -0.01, -0.05, 0.03)
    sortino = sortino_ratio(returns, 0.0, 252)
    # Mean = 0.00, so the ratio is (a float-precision) zero: a real measurement.
    assert sortino == pytest.approx(0.0, abs=1e-12)
    # No downside: undefined, not 0.0 (v3.10, ANA-004).
    assert sortino_ratio((0.01, 0.02), 0.0, 252) is None


def test_calmar_ratio() -> None:
    assert calmar_ratio(0.20, 0.10) == 2.0
    assert calmar_ratio(0.20, 0.0) is None
    assert calmar_ratio(None, 0.10) is None


def test_var_and_cvar() -> None:
    returns = tuple(float(x) / 100.0 for x in range(-10, 11))
    # 21 returns, -0.10 to 0.10. 95% Var means the worst 5%.
    # 5th percentile of 21 elements is the 2nd element (-0.09)
    # Unrounded since v3.10 (ANA-005): the interpolation's float result.
    var = value_at_risk(returns, 0.95)
    assert var == pytest.approx(-0.09, abs=1e-15)

    # CVaR is mean of returns <= VaR. The tail is chosen against the unrounded
    # threshold, so -0.09 itself (a hair above it in float) is not in it.
    cvar = conditional_var(returns, 0.95)
    assert cvar is not None and var is not None
    assert cvar <= var
    assert value_at_risk((), 0.95) is None
    assert conditional_var((), 0.95) is None


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
    assert metrics.avg_win == Decimal("150")
    assert metrics.avg_loss == Decimal("-100")
    # Gross Profit = 300, Gross Loss = 200 -> PF = 1.5
    assert metrics.profit_factor == 1.5
    # Expectancy = mean realized P&L per closing trade = 100 / 4 = 25
    assert metrics.expectancy == Decimal("25")
    assert metrics.turnover == 10.0
    assert metrics.fills == 4 and metrics.closed_trades == 4


def test_an_opening_fill_is_not_a_losing_trade() -> None:
    """ANA-003: one winning round trip is a 100% win rate, not 50%."""

    metrics = calculate_trade_metrics(
        (Decimal("0"), Decimal("40")),
        (None, 3600.0),
        total_traded_notional=Decimal("2000"),
        average_equity=Decimal("1000"),
    )

    assert metrics.fills == 2
    assert metrics.closed_trades == 1
    assert metrics.win_rate == 1.0
    assert metrics.loss_rate == 0.0
    # No losses: the profit factor is undefined, never infinite (ANA-004).
    assert metrics.profit_factor is None


def test_attribution() -> None:
    trades = (
        TradeRecord(
            "T1",
            "AAPL",
            "TECH",
            Decimal("100"),
            Decimal("1000"),
            10.0,
            (StrategyContribution("STRAT1", ONE),),
        ),
        TradeRecord(
            "T2",
            "AAPL",
            "TECH",
            Decimal("-50"),
            Decimal("1000"),
            10.0,
            (StrategyContribution("STRAT2", ONE),),
        ),
        TradeRecord(
            "T3",
            "MSFT",
            "TECH",
            Decimal("200"),
            Decimal("1000"),
            10.0,
            (StrategyContribution("STRAT1", ONE),),
        ),
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

    rs = rolling_sharpe(returns, 3, 0.0, 252)
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
        TradeRecord(
            "T1",
            "AAPL",
            "SEC1",
            Decimal("10.00"),
            Decimal("100"),
            60.0,
            (StrategyContribution("S1", ONE),),
        ),
        TradeRecord(
            "T2",
            "AAPL",
            "SEC1",
            Decimal("40.00"),
            Decimal("400"),
            60.0,
            (StrategyContribution("S1", ONE),),
        ),
        TradeRecord(
            "T3",
            "AAPL",
            "SEC1",
            Decimal("-60.00"),
            Decimal("600"),
            60.0,
            (StrategyContribution("S1", ONE),),
        ),
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


DAY = 86400.0


def _point(timestamp: float, equity: str) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        timestamp, Decimal(equity), Decimal(equity), Decimal("0"), Decimal("0")
    )


def test_the_report_takes_one_equity_point_per_instant() -> None:
    """ANA-001: several snapshots at one instant are one point, the last one."""

    snapshots = (
        _point(0.0, "1000"),
        _point(DAY, "1100"),  # an intermediate state within the instant
        _point(DAY, "1010"),  # the book after everything at DAY
        _point(2 * DAY, "1020"),
    )
    state = AnalyticsEngine.compile_report(AnalyticsEngine.initialize(), snapshots, (), 3 * DAY)
    report = latest_performance_summary(state)

    assert report is not None
    assert report.returns.period_returns == pytest.approx((0.01, 10 / 1010))
    event = state.events[0]
    assert isinstance(event, ReportGenerated)
    assert event.num_snapshots == 3


def test_the_report_records_an_observed_or_declared_basis() -> None:
    """ANA-001/ANA-002: annualization and span come from the curve unless declared."""

    from alphalab.analytics import Periodicity

    snapshots = tuple(_point(index * DAY, str(1000 + index)) for index in range(11))

    observed = latest_performance_summary(
        AnalyticsEngine.compile_report(AnalyticsEngine.initialize(), snapshots, (), 11 * DAY)
    )
    assert observed is not None
    assert observed.returns.periodicity is Periodicity.OBSERVED
    # Ten daily returns over ten days: 365.25 periods a year, observed.
    assert observed.returns.periods_per_year == pytest.approx(365.25)
    assert observed.returns.years_elapsed == pytest.approx(10 / 365.25)

    declared = latest_performance_summary(
        AnalyticsEngine.compile_report(
            AnalyticsEngine.initialize(),
            snapshots,
            (),
            11 * DAY,
            years_elapsed=1.0,
            periods_per_year=252.0,
        )
    )
    assert declared is not None
    assert declared.returns.periodicity is Periodicity.DECLARED
    assert declared.returns.periods_per_year == 252.0
    assert declared.returns.years_elapsed == 1.0
    assert declared.risk.sharpe_ratio != observed.risk.sharpe_ratio


def test_a_curve_at_one_instant_has_no_annualized_figure() -> None:
    from alphalab.analytics import Periodicity

    state = AnalyticsEngine.compile_report(
        AnalyticsEngine.initialize(), (_point(5.0, "1000"), _point(5.0, "1001")), (), 6.0
    )
    report = latest_performance_summary(state)

    assert report is not None
    assert report.returns.periodicity is Periodicity.UNDEFINED
    assert report.returns.cagr is None
    assert report.risk.sharpe_ratio is None
    assert report.risk.annualized_volatility is None


def test_a_curve_that_goes_back_in_time_is_refused() -> None:
    with pytest.raises(AnalyticsValidationError, match="back in time"):
        AnalyticsEngine.compile_report(
            AnalyticsEngine.initialize(), (_point(5.0, "1000"), _point(4.0, "1001")), (), 6.0
        )


def test_attribution_returns_plain_dicts() -> None:
    """AttributionMetrics fields are ordinary dicts (consistent with the rest of
    AlphaLab's frozen dataclasses) so the report can be serialized (D2)."""
    trades = (
        TradeRecord(
            "T1",
            "AAPL",
            "TECH",
            Decimal("100"),
            Decimal("1000"),
            10.0,
            (StrategyContribution("S1", ONE),),
        ),
        TradeRecord(
            "T2",
            "MSFT",
            "TECH",
            Decimal("-25"),
            Decimal("500"),
            10.0,
            (StrategyContribution("S1", ONE),),
        ),
    )
    attr = calculate_attribution(trades)
    assert type(attr.pnl_by_strategy) is dict
    assert type(attr.pnl_by_asset) is dict
    assert type(attr.pnl_by_sector) is dict
    assert attr.pnl_by_strategy == {"S1": Decimal("75")}
    assert attr.pnl_by_asset == {"AAPL": Decimal("100"), "MSFT": Decimal("-25")}


def test_performance_report_serializes_deterministically() -> None:
    """A PerformanceReport built by the engine can be serialized by
    alphalab.persistence, deterministically and idempotently (regression for D2)."""
    from alphalab.persistence.serializer import deserialize, serialize

    state = AnalyticsEngine.initialize()
    snapshots = (
        PortfolioSnapshot(
            100.0, Decimal("1000.00"), Decimal("1000.00"), Decimal("0"), Decimal("0")
        ),
        PortfolioSnapshot(
            101.0, Decimal("1050.00"), Decimal("1000.00"), Decimal("50"), Decimal("0")
        ),
        PortfolioSnapshot(
            102.0, Decimal("1040.00"), Decimal("1000.00"), Decimal("40"), Decimal("0")
        ),
    )
    trades = (
        TradeRecord(
            "T1",
            "AAPL",
            "TECH",
            Decimal("30.00"),
            Decimal("300"),
            60.0,
            (StrategyContribution("S1", ONE),),
        ),
        TradeRecord(
            "T2",
            "MSFT",
            "FIN",
            Decimal("-10.00"),
            Decimal("200"),
            60.0,
            (StrategyContribution("S1", ONE),),
        ),
    )
    state = AnalyticsEngine.compile_report(state, snapshots, trades, 200.0)
    report = state.reports[-1]

    s1 = serialize(report)
    s2 = serialize(report)
    assert s1 == s2  # deterministic + repeated serialization identical

    primitives = deserialize(s1)
    assert primitives["ending_capital"] == "1040.00"
    assert primitives["attribution"]["pnl_by_asset"]["AAPL"] == "30.00"

    # serialized -> deserialized -> reserialized is stable for the primitive form
    assert serialize(deserialize(serialize(primitives))) == serialize(primitives)
