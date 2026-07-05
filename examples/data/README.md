# AlphaLab Example Datasets

This directory contains small synthetic datasets used by the AlphaLab example suite.

The data is intentionally simple and exists only to demonstrate the public APIs of AlphaLab.

These datasets are **not intended for production research**.

---

## Files

sample_prices.csv

Daily closing prices.

Used by

- Research
- Replay
- Universal Data

---

sample_ohlcv.csv

Daily OHLCV bars.

Used by

- Market Data
- Strategy Studio

---

sample_trades.csv

Example executed trades.

Used by

- Research
- Replay

---

sample_portfolio.csv

Target portfolio weights.

Used by

- Portfolio Optimizer

---

All datasets reference the same fictional strategy and symbols.

---

AlphaLab Example Datasets
Purpose
This directory contains deterministic, synthetic datasets specifically designed to support the official AlphaLab v1.0.0 example suite. They are engineered to ensure smooth executions of 01_research.py through 10_complete_pipeline.py without external dependencies or data cleaning requirements.
Dataset Descriptions
sample_prices.csv: A minimal, stripped-down time-series defining only the closing prices. Ideal for lightweight strategy execution and basic research scripts.
sample_ohlcv.csv: Contains complete Open, High, Low, Close, and Volume fields over an identical time horizon. Fully compliant with rigorous internal validity checks (e.g., bounds constraints such as Low <= Open/Close <= High).
sample_trades.csv: Contains a realistic sequence of simulated strategy execution records—including winning, losing, and break-even scenarios—with mathematically verified PnL values tied strictly to the asset OHLC limits.
sample_portfolio.csv: Provides a deterministic baseline weight distribution for optimization routines (summing exactly to 1.0).
Shared Identifiers
To prevent configuration mismatches, the following identifiers are used consistently throughout the example suite and the datasets:
Strategy ID: MEAN_REV_V1
Project ID: PROJECT-001
Dataset ID: DATASET-001
Portfolio ID: PORT-001
Backtest ID: BT-001
Replay Session: REPLAY-001
Workbench ID: WORKBENCH-001
Data Generation Philosophy
The data is entirely synthetic and strictly non-monotonic to simulate realistic market fluctuations. It spans an exact period of 30 trading days formatted in ISO-8601 UTC standards to guarantee cross-platform and cross-timezone parsing consistency across various engines without invoking network or web requests.
Metadata
Version: AlphaLab v1.0.0
Status: Stable
Dataset Type: Synthetic, Deterministic, Educational
License: MIT
Generated For: Official AlphaLab Example Suite
Compatible Examples:
01_research.py
02_backtest.py
03_replay.py
04_market_data.py
05_broker_connection.py
06_portfolio_optimizer.py
07_universal_data.py
08_strategy_studio.py
09_workbench.py
10_complete_pipeline.py