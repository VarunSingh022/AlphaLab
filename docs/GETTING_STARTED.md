# Getting Started with AlphaLab

Welcome to AlphaLab.

This guide will help you install the framework, understand its structure, and execute your first workflow.

By the end of this guide you will have

- Installed AlphaLab
- Verified your environment
- Explored the project structure
- Understood the architecture
- Run your first example
- Learned where to go next

---

# Prerequisites

AlphaLab currently requires

- Python 3.12+
- Git
- pip

A virtual environment is strongly recommended.

---

# Clone the Repository

```bash
git clone https://github.com/VarunSingh022/AlphaLab.git

cd AlphaLab
```

---

# Create a Virtual Environment

macOS / Linux

```bash
python -m venv .venv

source .venv/bin/activate
```

Windows

```powershell
python -m venv .venv

.venv\Scripts\activate
```

---

# Install AlphaLab

Install the project in editable mode.

```bash
pip install -e ".[dev]"
```

This installs AlphaLab together with development dependencies.

---

# Verify Installation

Run the validation suite.

```bash
ruff check .

mypy .

pytest
```

Expected output

```
All checks passed.

Success: no issues found.

All tests passed.
```

If all commands complete successfully, your environment is correctly configured.

---

# Repository Overview

```
AlphaLab/

alphalab/     framework source (50 packages)
benchmarks/   54 performance benchmarks
configs/      reference configuration files
docs/         technical documentation and 39 ADRs
examples/     45 runnable examples
tests/        5399 tests — unit, integration, regression
```

### alphalab/

Contains the framework source code.

---

### tests/

Contains the complete test suite: unit, integration and regression. It reports
**0 skipped and 0 warnings**, and a standing test enforces both.

---

### docs/

Contains technical documentation.

---

### examples/

Contains executable demonstrations.

---

### benchmarks/

Contains performance benchmarks.

---

# Understanding the Architecture

AlphaLab is a **library**. There is no server, daemon, or CLI, and it has zero
runtime dependencies — you import packages and call their pure engine APIs.

Three kinds of package:

- **The execution path** — `alphalab.runtime.ExecutionPipeline` wires mark to
  market → strategy → allocation → risk → OMS → execution → portfolio →
  analytics as pure functions over one immutable state, and
  `alphalab.runtime.run.RunEngine` owns the run over it. Four drivers feed it:
  `BacktestEngine` walks a dataset, `ReplayBacktest` walks it through
  `alphalab.replay`'s cursor, `TradingSession` reads any `MarketDataSource`, and
  `LiveSession` drives the settle/advance/route cycle against a venue. All four
  call the same step, so a backtest and a replay of one dataset produce identical
  orders, fills and P&L.
- **The lifecycle path** — `alphalab.lifecycle` takes a research candidate to a
  deployment and back, and refuses a run that would serve a version the
  deployment ledger does not name.
- **Standalone engines** — `portfolio_optimizer`, the learning and asset-class
  engines, `workbench`, `reporting`, and the rest. Each is deterministic and
  individually tested, and they are deliberately not chained together (ADR-0009).

```
market event → mark → strategy → allocation → risk → OMS → execution
             → portfolio → analytics        ← runtime.ExecutionPipeline
                                            ← runtime.run.RunEngine owns the run

research candidate → evidence → model version → strategy version
             → promotion → deployment        ← alphalab.lifecycle
                                              authorize_run joins the two
```

---

# First Example

Navigate to the examples directory.

```
examples/
```

Choose one of the introductory examples.

Example workflow

```
Load Dataset

↓

Run Research

↓

Optimize Portfolio

↓

Generate Report
```

Each example demonstrates one complete workflow.

---

# Understanding the Packages

Major packages include

```
runtime/        the execution step and the run
strategy/       what a strategy is, and the registry that maps identity to code
oms/            order lifecycle
portfolio/      cash, positions, P&L, FX
market/         canonical market model and normalization
instrument/     canonical instrument identity
lifecycle/      research candidate → deployment → rollback
research/       research workflows and scores
portfolio_optimizer/  portfolio construction
data/  marketdata/    wire records and provider clients
broker/  brokers/     one venue, and many
studio/  workbench/   orchestration and presentation
```

Each package owns one business capability. The complete list, with which of the
two paths reaches each, is in `../README.md`.

---

# Running Tests

Execute the full test suite.

```bash
pytest
```

Run a specific package.

```bash
pytest tests/unit/research
```

Run one file.

```bash
pytest tests/unit/research/test_research.py
```

---

# Code Quality

Run Ruff.

```bash
ruff check .
```

Automatically fix formatting.

```bash
ruff check . --fix
```

Run MyPy.

```bash
mypy .
```

All three commands should succeed before committing changes.

---

# Exploring Documentation

Recommended reading order

1. README.md
2. docs/GETTING_STARTED.md
3. docs/ARCHITECTURE.md
4. docs/SYSTEM_DESIGN.md
5. docs/ENGINEERING_GUIDELINES.md

---

# Common Development Workflow

Typical research workflow — you invoke each engine and pass its immutable output
to the next:

```
Acquire Data

↓

Normalize Dataset

↓

Run Research

↓

Optimize Portfolio

↓

Generate Report
```

Every stage is deterministic and uses immutable state. The market-data →
analytics segment *is* chained, by `alphalab.backtesting` over
`ExecutionPipeline`, with `replay`, a live source and a venue as alternative
drivers of the same step. The research → model → deployment sequence is chained
too, by `alphalab.lifecycle`. Research, optimization and reporting around them
are separate engines, and nothing chains those automatically — deliberately
(ADR-0009).

---

# Learning by Examples

The fastest way to learn AlphaLab is by reading and running the examples.

Each example focuses on one subsystem.

Examples include

- Universal Data Engine
- Research Engine
- Portfolio Optimizer
- Market Data
- The two broker boundaries
- Strategy Studio
- Workbench
- The unified backtest, the lifecycle, durable run state and multi-currency
  settlement (examples 11–14)
- Universal data ingestion and research from a canonical dataset
  (examples 15–16)
- Feature engineering, factor research, signal diagnostics, walk-forward
  validation, time-series cross-validation, robustness testing, overfitting
  diagnostics and the complete research pipeline (examples 17–24)
- Execution cost simulation, capacity modelling, portfolio attribution, risk
  decomposition, stress testing and the scenario engine (examples 25–30)
- Market conventions, exchange calendars and sessions, futures chains and rolls,
  continuous futures, options chains and Greeks, implied volatility and the
  volatility surface, FX research, crypto perpetuals and 24/7 coverage, the
  fixed-income foundation, and a multi-asset book (examples 31–40)
- The strategy progression from research to live money, deployment
  specifications, runtime health from supplied observations, the
  expected/paper/live comparison, and reconciliation against a normalized broker
  state (examples 41–45)

---

# Running Benchmarks

Performance benchmarks are located in

```
benchmarks/
```

Benchmarks measure execution speed and scalability.

They complement—but do not replace—the unit test suite.

---

# Contributing

Interested in contributing?

Read

```
CONTRIBUTING.md
```

and

```
docs/ENGINEERING_GUIDELINES.md
```

before submitting changes.

---

# Getting Help

If you encounter issues

- Read the documentation
- Search existing GitHub Issues
- Open a Discussion
- Create an Issue

Please include enough information to reproduce the problem.

---

# Where to Go Next

After completing this guide, consider exploring

- The execution path — `examples/11_unified_backtest.py`, then
  `alphalab.runtime.ExecutionPipeline` and `alphalab.runtime.run.RunEngine`
- The lifecycle path — `examples/12_model_lifecycle.py`, then
  `alphalab.lifecycle`
- Durable run state — `examples/13_durable_run_state.py`
- Multi-currency settlement and the FX feed — `examples/14_multi_currency_settlement.py`
- The standalone engines — Research, Universal Data, Portfolio Optimizer,
  Strategy Studio, Workbench

`../nowandfuture.md` is the long-form reference for who owns what, which
invariants are frozen, and what must not be changed casually.

---

# Next Steps

Congratulations!

You have successfully set up AlphaLab and are ready to begin building quantitative research workflows.

The advanced capabilities — machine learning, distributed research, cloud
execution and enterprise governance — all ship as standalone packages today; their
usage is covered by the unit tests under `tests/unit/<package>/` and the
benchmarks under `benchmarks/`.

Welcome to AlphaLab.