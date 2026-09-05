# AlphaLab Examples

## Overview

The examples directory contains complete, executable demonstrations of AlphaLab functionality.

Each example focuses on a single subsystem while following the same engineering principles used throughout the framework.

Examples are intended to be read sequentially by new users and used as reference implementations by contributors.

> Examples `01`–`10` were written for v1.0.0 and exercise the **standalone**
> engine APIs. `11_unified_backtest.py` (v2.2) drives the integrated execution
> path end to end. None are part of the automated test suite, though all eleven
> run. For the integrated market-to-analytics path see `alphalab.backtesting`,
> `alphalab.runtime.ExecutionPipeline`, and their tests under
> `tests/integration/` and `tests/regression/`.

---

# Learning Path

The `examples/` directory contains:

| File | Description |
|------|-------------|
| `01_research.py` | Research engine |
| `02_backtest.py` | Strategy Studio backtest bookkeeping |
| `03_replay.py` | Historical replay cursor |
| `04_market_data.py` | Market data providers |
| `05_broker_connection.py` | Broker integration adapters |
| `06_portfolio_optimizer.py` | Portfolio construction |
| `07_universal_data.py` | Universal Data Engine |
| `08_strategy_studio.py` | Strategy Studio orchestration |
| `09_workbench.py` | Workbench workspace |
| `10_complete_pipeline.py` | Multi-engine walkthrough |
| `11_unified_backtest.py` | Dataset → orders → fills → P&L → analytics, plus replay parity |

---

# Universal Data

Learn how AlphaLab transforms heterogeneous market data into canonical datasets.

Topics include

- CSV ingestion
- JSON ingestion
- Schema detection
- Column mapping
- Symbol normalization
- Timestamp normalization

---

# Research

Examples demonstrate

- Performance metrics
- Walk-forward analysis
- Bootstrap statistics
- Monte Carlo simulation
- Regime analysis
- Capacity estimation

---

# Portfolio Optimization

Examples include

- Equal Weight
- Risk Parity
- Maximum Sharpe
- Minimum Variance
- Constraint handling
- Rebalancing

---

# Replay

Replay examples demonstrate deterministic historical simulation.

Topics include

- Event replay
- Historical execution
- Performance validation

---

# Market Data

Examples demonstrate

- Yahoo Finance
- Polygon
- Databento
- Binance
- NSE

All provider outputs are normalized through the Universal Data Engine.

---

# Broker Integrations

Supported examples include

- Paper Trading
- Alpaca
- Interactive Brokers
- Zerodha

---

# Strategy Studio

Examples demonstrate complete research workflows.

Typical pipeline

```

Acquire Data

↓

Normalize Dataset

↓

Research

↓

Portfolio Optimization

↓

Replay

↓

Reporting

```

---

# Workbench

Workbench examples illustrate

- Projects
- Sessions
- Pipelines
- Reports
- Dashboards

---

# Additional engines

The feature store, factor library, machine learning, cloud research, enterprise,
and other engines added in v1.34.0–v2.0.0 do not yet have dedicated example
scripts. Their usage is covered by the unit tests under `tests/unit/<package>/`
and by the benchmarks under `benchmarks/`.

---

# Philosophy

Examples are intentionally concise.

They demonstrate recommended usage patterns rather than every available API.

They should remain synchronized with the public interfaces of AlphaLab.