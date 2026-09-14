# AlphaLab Example Datasets

Four small synthetic CSV files supporting the example suite. They exist only to
demonstrate the public APIs without needing a market-data provider, and they are
**not** suitable for research, backtesting conclusions, or production trading.

Everything here is generated, deterministic, and small enough to read by eye.

---

## Files

| File | Shape | Rows |
|---|---|---|
| `sample_prices.csv` | `dataset_id, symbol, timestamp, price` | 90 |
| `sample_ohlcv.csv` | `dataset_id, symbol, timestamp, open, high, low, close, volume` | 90 |
| `sample_trades.csv` | `strategy_id, project_id, backtest_id, trade_id, symbol, entry_timestamp, exit_timestamp, entry_price, exit_price, quantity, pnl` | 5 |
| `sample_portfolio.csv` | `portfolio_id, symbol, weight` | 3 |

### `sample_prices.csv`

Daily closing prices for three synthetic U.S. equities — `AAPL`, `MSFT`, `SPY` —
over 30 trading days, `2025-01-02` to `2025-02-13`, timestamped in ISO-8601 UTC.
Three symbols × 30 days = 90 rows.

### `sample_ohlcv.csv`

The same three symbols over the same 30 days, with full OHLCV. The bounds hold
(`low <= open, close <= high`) and the `close` column matches
`sample_prices.csv` exactly, so an example can move between the two without the
numbers changing.

### `sample_trades.csv`

Five closed round trips attributed to `MEAN_REV_V1`, deliberately mixed so
research metrics have something to report: two winners (+390.00, +92.00), two
losers (−105.00, −130.00) and one break-even (0.00). Every `pnl` is consistent
with its own entry price, exit price and quantity.

### `sample_portfolio.csv`

One target allocation for `PORT-001`: `AAPL` 0.4000, `MSFT` 0.3500, `SPY`
0.2500. The weights sum to exactly **1.0000**.

---

## Shared identifiers

The same identifiers appear across every dataset and every example that reads
them, so nothing has to be re-keyed between stages:

| Object | Identifier |
|---------|------------|
| Strategy | `MEAN_REV_V1` |
| Project | `PROJECT-001` |
| Dataset | `DATASET-001` |
| Portfolio | `PORT-001` |
| Backtest | `BT-001` |
| Replay session | `REPLAY-001` |
| Workbench | `WORKBENCH-001` |

---

## Which examples use them

Read directly by `02_backtest.py`, `07_universal_data.py`,
`08_strategy_studio.py` and `10_complete_pipeline.py`.

Examples `11`–`14` — the unified backtest, the model lifecycle, durable run state
and multi-currency settlement — construct their own in-memory data and use
nothing here.

---

## Design philosophy

Synthetic, deterministic, human-readable, consistent across every example, and
small enough to inspect by hand. The objective is to teach AlphaLab workflows,
not to simulate a real market.

| Property | Value |
|----------|-------|
| Dataset type | Synthetic |
| Deterministic | Yes |
| Intended use | Educational |
| External dependencies | None |
| License | MIT |
