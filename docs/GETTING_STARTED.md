# Getting Started with AlphaLab

Welcome to AlphaLab.

This guide will help you install the framework, understand its structure, and execute your first workflow.

By the end of this guide you will have

- Installed AlphaLab
- Verified your environment
- Explored the project structure
- Understood the architecture
- Built, run, checked and reproduced your first strategy
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

Each should succeed: Ruff reports `All checks passed!`, mypy
`Success: no issues found`, and pytest a summary line with every test passed and
none skipped. CI runs the suite with `python -m pytest -W error`, which turns any
warning into a failure; so can you.

---

# Repository Overview

```
AlphaLab/

alphalab/     framework source, one package per subsystem
benchmarks/   performance benchmarks, each judging its own ceiling
configs/      reference configuration files
docs/         technical documentation and the ADRs
examples/     runnable examples, one subsystem each
tests/        unit, integration and regression tests
```

The counts are in `README.md`'s status table, which the release checklist
updates; this overview gave counts from 2.x until v3.13.

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
  engines, `reporting`, and the rest. Each is deterministic and
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

# Build your first strategy

The complete, runnable version of this walk-through is
[`examples/70_build_a_strategy.py`](../examples/70_build_a_strategy.py); run it
with `python examples/70_build_a_strategy.py`. Every name used below is part of
the public API (`docs/api/PUBLIC_API.md`).

## 1. Data with an identity

A dataset is ingested, validated and given a version derived from its content.
Rows already in memory go through `alphalab.api.ingest_rows`, files through
`ingest_csv`; both take an `IngestionRequest` that states every decision the
ingestion may not make on its own -- the frequency, which end of its interval a
bar is stamped at, the asset class, the price basis, and a `CleaningPolicy`.
Start from `REFUSE_EVERYTHING`: a duplicate, an out-of-order row, an impossible
bar, a missing or non-numeric price each stop the ingestion and say why. A
policy that drops such rows records each drop in the dataset's provenance.

Then declare what the symbols *are*: an `InstrumentRecord` (symbol, asset type,
listing exchange, currency) registered in an `InstrumentRegistry`, and a
`NormalizationPolicy` naming the registry, the venue, the currency and the bar
interval. The execution path trades a derived `asset_id`, never a provider's
symbol.

## 2. A strategy is a class with a hook

Subclass `alphalab.strategy.BaseStrategy` and implement the hook for what you
subscribe to -- `on_bar`, `on_quote`, `on_tick` -- returning `Intent`s. A
target-position intent (`IntentKind.TARGET_QUANTITY`) states the position you
want; allocation turns the difference from what is held into an order, so
stating the same target again asks for nothing. Check your parameters in the
constructor, and read prices from the event, which is the instant you are
deciding at.

If the strategy keeps memory -- a window of closes, a counter -- declare it with
the three `StrategyStateProtocol` members (`strategy_state_version`,
`capture_state`, `restore_state`) so a run snapshot carries it.

## 3. Start it, and state the run

```python
runtime = start_strategy(
    create_runtime(),
    "MY-STRATEGY",
    my_strategy,
    config={},
    subscriptions={"bars"},
    at=first_instant - 1.0,
)
contexts = context_factory(FixedClock(first_instant), DiscardingLogger())
```

`start_strategy` registers the strategy and takes it to `RUNNING` through the
supervisor. A `RunConfig` then states everything the run assumes, with nothing
defaulted that could change a number: the account and its currency, starting
cash, a `CapitalBudget`, `AllocationConstraints` (shorting, whole units),
`RiskLimits`, an `ExecutionSimulator` with its commission and slippage models,
the `FillTiming` -- `SAME_EVENT` fills at the price that decided the order,
`NEXT_EVENT` at the next one -- and a seed.

## 4. Run it and read what it did

```python
result = backtest(run_config, dataset, runtime, contexts, normalization)
```

`result.fills` are the executions (side, quantity, price, commission);
`result.orders` the orders; `result.valuation` the cash, equity and realized and
unrealized P&L; `result.state.portfolio.positions` what is held;
`result.report` the performance report; `result.strategy_failures` any hook that
raised; `result.execution_assumptions` the timing, costs and fill policy the run
assumed, so a result says how optimistic it is.

Check the numbers you can check by hand. Example 70 recomputes every fill, the
cash, the realized P&L and the commission in plain arithmetic and asserts the
engine agrees, runs flat prices to show no order is placed, and feeds it invalid
parameters and data to show each is refused.

## 5. Reproduce it

`alphalab.lifecycle.digest_run(result)` names a run by digests: the same data,
configuration and seed give the same `result_id`. A run can be stopped, captured
(`alphalab.runtime.run_snapshot.capture`), serialized, restored with freshly
built objects (`restore`) and continued under `BacktestEngine.resume`; the
continued run has the same `result_id` as one that never stopped. A restore
refuses a sizing model, simulator or fill policy configured otherwise than the
one the run was captured with.

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
research/       research workflows, measurements and the parameter search
portfolio_optimizer/  portfolio construction
data/  marketdata/    wire records, the HTTP and WebSocket transports
broker/  brokers/     one venue, and many
```

Presentation, orchestration, identity and credentials are the host
application's: `studio`, `workbench` and `enterprise` left the library in v3.11
(ADR-0046).

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
- Target weights on a lot grid, and decisions on complete instants
  (examples 08–09, which until v3.11 demonstrated the Strategy Studio and the
  Workbench, both now the host application's)
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
- Strategy fingerprints, reproducible research artifacts, certification
  primitives and portability across declared environments (examples 46–49)
- Point-in-time events, alternative data with provenance, fundamentals, regime
  detection, and adaptive strategies with exact replay (examples 50–55)
- Portfolio construction, risk budgeting, a multi-strategy portfolio,
  cross-strategy risk and capital allocation (examples 56–60)
- Broker capabilities, the normalized execution lifecycle, execution
  algorithms, smart routing and execution analytics end to end
  (examples 61–65)
- American options, the optimal split and urgency, a rerun from a manifest,
  and cron timers (examples 66–69)
- Building, checking and reproducing a strategy through the public API
  (example 70)

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

- Your first strategy, end to end — `examples/70_build_a_strategy.py`
- The execution path — `examples/11_unified_backtest.py`, then
  `alphalab.runtime.ExecutionPipeline` and `alphalab.runtime.run.RunEngine`
- The lifecycle path — `examples/12_model_lifecycle.py`, then
  `alphalab.lifecycle`
- Durable run state — `examples/13_durable_run_state.py`
- Multi-currency settlement and the FX feed — `examples/14_multi_currency_settlement.py`
- The standalone engines — Research, Universal Data, Portfolio Optimizer

`../nowandfuture.md` is the long-form reference for who owns what, which
invariants are frozen, and what must not be changed casually.

---

# Next Steps

Congratulations!

You have set up AlphaLab and built a strategy on it.

The advanced capabilities — machine learning, distributed research and cloud
execution — ship as standalone packages; their
usage is covered by the unit tests under `tests/unit/<package>/` and the
benchmarks under `benchmarks/`.

Welcome to AlphaLab.