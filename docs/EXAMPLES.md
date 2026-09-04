# AlphaLab Examples

## Overview

The examples directory contains complete, executable demonstrations of AlphaLab functionality.

Each example focuses on a single subsystem while following the same engineering principles used throughout the framework.

Examples are intended to be read sequentially by new users and used as reference implementations by contributors.

> The example scripts were written for v1.0.0 and exercise the **standalone**
> engine APIs. They are not part of the automated test suite. As of v2.0.0,
> `03_replay.py` fails on a pre-existing empty-dataset issue; the other nine run.
> For the integrated market-to-analytics path, see
> `alphalab.runtime.ExecutionPipeline` and its tests under
> `tests/integration/` and `tests/regression/`.

---

# Learning Path

The `examples/` directory contains:

| File | Description |
|------|-------------|
| `01_research.py` | Research engine |
| `02_backtest.py` | Backtest workflow |
| `03_replay.py` | Historical replay *(currently broken)* |
| `04_market_data.py` | Market data providers |
| `05_broker_connection.py` | Broker integration adapters |
| `06_portfolio_optimizer.py` | Portfolio construction |
| `07_universal_data.py` | Universal Data Engine |
| `08_strategy_studio.py` | Strategy Studio orchestration |
| `09_workbench.py` | Workbench workspace |
| `10_complete_pipeline.py` | Multi-engine walkthrough |

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