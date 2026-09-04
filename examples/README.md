# AlphaLab Example Datasets

This directory contains the synthetic datasets used by the AlphaLab example suite. The example scripts and these datasets date from **v1.0.0**, exercise the standalone engine APIs, and are not part of the automated test suite. As of v2.0.0, `03_replay.py` fails on a pre-existing empty-dataset issue; the other nine scripts run.

These datasets are intentionally small, deterministic, and internally consistent so that every example can be executed without external market data providers.

---

# Purpose

The datasets demonstrate the recommended usage of AlphaLab's public APIs.

They are designed for:

- Documentation
- Tutorials
- Unit examples
- Integration examples
- API demonstrations

These datasets are **not intended for production trading, financial research, or performance benchmarking**.

---

# Dataset Overview

## sample_prices.csv

Daily closing prices for a small universe of synthetic U.S. equities.

Used by:

- Research
- Replay

---

## sample_ohlcv.csv

Daily Open, High, Low, Close, and Volume bars.

Used by:

- Market Data
- Universal Data
- Strategy Studio
- Complete Pipeline

The Close column exactly matches `sample_prices.csv`.

---

## sample_trades.csv

Synthetic historical executions produced by the example strategy.

Used by:

- Research
- Replay
- Backtesting examples

Contains a mix of profitable, losing, and break-even trades to better illustrate research metrics.

---

## sample_portfolio.csv

Example target portfolio allocations.

Used by:

- Portfolio Optimizer
- Complete Pipeline

Portfolio weights always sum exactly to **1.0000**.

---

# Shared Identifiers

The examples use consistent identifiers across every dataset.

| Object | Identifier |
|---------|------------|
| Strategy | `MEAN_REV_V1` |
| Project | `PROJECT-001` |
| Dataset | `DATASET-001` |
| Portfolio | `PORT-001` |
| Backtest | `BT-001` |
| Replay Session | `REPLAY-001` |
| Workbench | `WORKBENCH-001` |

---

# Data Generation Philosophy

The datasets are intentionally:

- Synthetic
- Deterministic
- Human-readable
- Small enough for manual inspection
- Consistent across all examples

The objective is to teach AlphaLab workflows rather than simulate real financial markets.

---

# Metadata

| Property | Value |
|----------|-------|
| Version | AlphaLab v1.0.0 |
| Status | Stable |
| Dataset Type | Synthetic |
| Deterministic | Yes |
| Intended Use | Educational |
| License | MIT |
| Generated For | Official AlphaLab Example Suite |

---

# Compatible Examples

These datasets are compatible with:

- `01_research.py`
- `02_backtest.py`
- `03_replay.py`
- `04_market_data.py`
- `05_broker_connection.py`
- `06_portfolio_optimizer.py`
- `07_universal_data.py`
- `08_strategy_studio.py`
- `09_workbench.py`
- `10_complete_pipeline.py`

---

Happy researching with AlphaLab!
