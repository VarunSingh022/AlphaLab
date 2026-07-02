"""High-performance benchmarking suite for the functional Analytics Engine."""

import time
from decimal import Decimal

from alphalab.analytics import (
    AnalyticsEngine,
    PortfolioSnapshot,
    TradeRecord,
)


def run_benchmark() -> None:
    state = AnalyticsEngine.initialize()

    N_SNAPS = 100_000
    N_TRADES = 10_000

    print("Starting Analytics Engine Benchmark...")
    print(f"Generating synthetic series of {N_SNAPS} snapshots and {N_TRADES} trades...")

    # 1. Synthesize Data
    snapshots = tuple(
        PortfolioSnapshot(
            timestamp=float(i),
            total_equity=Decimal("1000000.00") + Decimal(str(i * 10)),
            cash=Decimal("500000.00"),
            long_exposure=Decimal("500000.00") + Decimal(str(i * 10)),
            short_exposure=Decimal("0.00"),
        )
        for i in range(N_SNAPS)
    )

    trades = tuple(
        TradeRecord(
            trade_id=f"T{i}",
            strategy_id="STRAT-1",
            asset_id="AAPL",
            sector_id="TECH",
            realized_pnl=Decimal("10.00") if i % 2 == 0 else Decimal("-5.00"),
            notional_value=Decimal("1000.00"),
            holding_period_seconds=3600.0,
        )
        for i in range(N_TRADES)
    )

    print("Executing full metrics compilation pipeline...")

    # 2. Extract strictly deterministically
    start = time.perf_counter()

    state = AnalyticsEngine.compile_report(
        state=state,
        snapshots=snapshots,
        trades=trades,
        timestamp=float(N_SNAPS + 1),
    )

    duration = time.perf_counter() - start

    print(f"Analytics Compilation Time: {duration:.4f}s")
    print(f"Throughput: {N_SNAPS / duration:.2f} snapshots processed/sec")

    report = state.reports[-1]
    print(f"Sample Metric -> Sharpe Ratio: {report.risk.sharpe_ratio:.4f}")
    print(f"Sample Metric -> Max Drawdown: {report.drawdowns.max_drawdown:.4%}")
    print(f"Sample Metric -> Trade Win Rate: {report.trades.win_rate:.2%}")


if __name__ == "__main__":
    run_benchmark()
