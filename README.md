<div align="center">

# AlphaLab

### Institutional-Grade Quantitative Research & Algorithmic Trading Framework

**Deterministic • Event-Driven • Immutable • Fully Typed • Production-Oriented**

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)]()
[![Version](https://img.shields.io/badge/Version-2.1.0-blue)]()
[![License](https://img.shields.io/badge/License-MIT-green.svg)]()
[![Tests](https://img.shields.io/badge/Tests-1221%20Passing-success)]()
[![Typing](https://img.shields.io/badge/MyPy-Strict-blue)]()
[![Style](https://img.shields.io/badge/Ruff-Clean-red)]()

*A modular Python library for quantitative research, systematic strategy development, portfolio optimization, market simulation, broker integration, and production tooling.*

</div>

---

# What is AlphaLab?

AlphaLab is an open-source Python **library** for building deterministic quantitative research and algorithmic trading components.

It is a library, not a running application: there is no server, daemon, scheduler process, or CLI. You import the packages you need and call their pure, immutable engine APIs from your own code.

AlphaLab ships two kinds of package:

- **The integrated execution path.** `alphalab.runtime.ExecutionPipeline` is the one spine that wires several domain engines together — market data → strategy → allocation → risk → OMS → execution simulator → portfolio → analytics — as a chain of pure functions over one immutable state snapshot. This is the concrete backtest / execution path.
- **Standalone engine libraries.** Most other packages (research, portfolio optimizer, replay, feature store, factor library, ML / deep learning / RL, options / futures / crypto / macro, alternative data, cloud research, cluster scheduler, experiment tracking, model registry, research assistant, deployment manager, enterprise, studio, workbench) are independent, deterministic, individually tested libraries. They share the engineering model but are **not** currently fused into a single runtime.

The framework is designed for researchers, quantitative developers, students, and engineering teams building reproducible trading infrastructure.

---

# Release Status

**Current Release:** **v2.1.0**

| Metric | Status |
|---------|--------|
| Python | 3.12+ |
| Version | 2.1.0 |
| Tests | **1273 Passing** |
| Static Typing | **Strict MyPy** (840 source files) |
| Linting | **Ruff Clean** |
| Package Build | ✅ Passing |
| Wheel Validation | ✅ Passing |
| Source Distribution | ✅ Passing |
| License | MIT |

v2.1.0 — "Execution + Portfolio Correctness" — adds mark-to-market to the execution
pipeline, separates the portfolio's cash / realized P&L / unrealized P&L / commission
accounting behind an explicit accounting identity, makes execution invariants explicit,
and fixes the O(N²) event accumulation that had stopped the risk benchmark from
completing. It contains breaking public API changes — see `CHANGELOG.md`.

---

# Core Principles

AlphaLab is built around a consistent engineering philosophy.

- Immutable domain models
- Deterministic execution
- Event-driven architecture
- Pure functional engine APIs
- Strict static typing
- Modular package boundaries
- Production-oriented design
- Reproducible research workflows

---

# Architecture

## The integrated execution path

`alphalab.runtime.ExecutionPipeline` is the concrete, wired-together spine. One
market event flows through each stage as a pure function over an immutable
`ExecutionPipelineState`:

```text
Market event (Quote / Bar / Tick)
        │
        ▼
Strategy   →  Intents
        │
        ▼
Allocation →  sized OrderRequests            (core.OrderRequest, core.enums.Side)
        │
        ▼
Risk       →  RiskDecision (approve / reject)
        │
        ▼
OMS        →  Order lifecycle                (oms.order.Order — canonical)
        │
        ▼
Execution simulator → ExecutionReport (deterministic fills, commission)
        │
        ▼
Portfolio  →  cash, positions, realized P&L  (Fill / Trade — float timestamps)
        │
        ▼
Analytics  →  PerformanceReport (compiled on demand)
```

The caller owns the event loop and feeds events in one at a time.

## Standalone engine libraries

Everything below is importable, deterministic, and independently tested, but is
**not** currently connected into `ExecutionPipeline` or into one another:

| Area | Packages |
|---|---|
| Research & simulation | `research`, `replay`, `reporting` |
| Portfolio construction | `portfolio_optimizer`, `optimizer` |
| Data surface (overlapping) | `data`, `marketdata`, `feed` |
| Features & factors | `feature_store`, `factor_library`, `alt_data` |
| Learning | `ml`, `deep_learning`, `reinforcement_learning` |
| Asset classes | `options`, `futures`, `crypto`, `macro` |
| Scale-out | `cloud_research`, `cluster_scheduler`, `distributed` |
| Lifecycle | `experiment_tracking`, `model_registry`, `deployment_manager` |
| Workflow & governance | `studio`, `workbench`, `research_assistant`, `enterprise` |
| Live / ops surface | `live`, `production`, `broker`, `brokers`, `integrations` |

> The Workbench → Studio → live-markets flow shown in `docs/` is a design target,
> not a single runtime that exists today. `replay` is a standalone engine and does
> **not** drive `ExecutionPipeline`. Mark-to-market repricing is not implemented.

---

# Getting Started

## Installation

```bash
git clone https://github.com/VarunSingh022/AlphaLab.git

cd AlphaLab

python -m venv .venv

source .venv/bin/activate

pip install -e ".[dev]"
```

---

# Learn AlphaLab

The recommended way to learn the framework is through the curated examples.

| Example | File | Description |
|---------|------|-------------|
| 01 | `01_research.py` | Research Engine |
| 02 | `02_backtest.py` | Backtest workflow |
| 03 | `03_replay.py` | Historical replay *(currently broken — pre-existing dataset issue)* |
| 04 | `04_market_data.py` | Market Data |
| 05 | `05_broker_connection.py` | Broker Integrations |
| 06 | `06_portfolio_optimizer.py` | Portfolio Optimizer |
| 07 | `07_universal_data.py` | Universal Data Engine |
| 08 | `08_strategy_studio.py` | Strategy Studio |
| 09 | `09_workbench.py` | Workbench |
| 10 | `10_complete_pipeline.py` | Multi-engine walkthrough |

Run any example:

```bash
python examples/01_research.py
```

The example scripts date from v1.0.0 and exercise the standalone engine APIs; they
are not part of the automated test suite.

---

# Documentation

The complete documentation is available in the `docs/` directory.

| Document | Description |
|----------|-------------|
| Getting Started | Installation and first steps |
| Architecture | Framework architecture |
| ADR | Architectural Decision Records |
| Examples | Example walkthroughs |
| Engineering | Engineering guidelines |
| Roadmap | Future development |

---

# Repository

```text
alphalab/
├── core/            Canonical domain models — Side, OrderRequest, Fill, Trade, ids
├── runtime/         ExecutionPipeline (the integrated execution spine) + runtime engine
├── strategy/        Strategy protocol, engine, supervisor
├── allocation/      Intent sizing / netting → OrderRequest
├── risk/            Pre-trade risk checks and limits
├── oms/             Order lifecycle (oms.order.Order is canonical)
├── execution/       Deterministic execution simulator, commission models
├── portfolio/       Cash ledger, positions, NAV, realized P&L
├── analytics/       Performance report, attribution
├── market/          In-memory market state and events
├── common/          Shared version, events, serialization, constants
├── research/  replay/  portfolio_optimizer/  reporting/       Standalone engines
├── feature_store/  factor_library/  alt_data/                 Standalone engines
├── ml/  deep_learning/  reinforcement_learning/               Standalone engines
├── options/  futures/  crypto/  macro/                        Standalone engines
├── cloud_research/  cluster_scheduler/  distributed/          Standalone engines
├── experiment_tracking/  model_registry/  deployment_manager/ Standalone engines
├── studio/  workbench/  research_assistant/  enterprise/      Standalone engines
├── data/  marketdata/  feed/                                  Data-surface (overlapping; see docs)
├── live/  production/  broker/  brokers/  integrations/       Live/ops surface (deferred)
├── kernel/  plugins/  scheduler/  persistence/  optimizer/    Infrastructure
└── ...
```

Additional directories:

```text
docs/          Documentation

examples/      Runnable examples

benchmarks/    Performance benchmarks

tests/         Automated test suite

configs/       Reference configuration files
```

---

# Quality Assurance

AlphaLab is continuously validated through automated tooling.

- ✅ 1221 passing tests (1207 unit, 4 integration, 10 regression)
- ✅ Strict MyPy type checking (833 source files)
- ✅ Ruff linting and formatting
- ✅ Source distribution validation
- ✅ Wheel validation
- ✅ Python packaging verification

---

# Roadmap

## Delivered

**v1.0.0** — architectural foundation: core domain models, strategy runtime,
replay engine, portfolio optimizer, broker integration scaffolding, production
runtime, Strategy Studio, Workbench.

**v1.34.0 – v1.46.0** — engine series: feature store, factor library, options,
futures, crypto, macro, alternative data, machine learning, deep learning,
reinforcement learning, cloud research, cluster scheduler, experiment tracking.

**v2.0.0** — model registry, AI research assistant, deployment manager, AlphaLab
Enterprise; canonical execution domain-model unification (`core.enums.Side`,
`core.OrderRequest`, `oms.order.Order`, float `Fill`/`Trade` timestamps);
portfolio close/reduce cash-accounting fix; `PerformanceReport` serialization fix.

**v2.1.0** — execution and portfolio correctness: mark-to-market wired into
`ExecutionPipeline`; account-level realized P&L and commissions; the
`PortfolioValuation` read model; per-fill P&L attribution; unpriced-request and
terminal-rejection execution invariants; O(1) amortized append-only histories
(`common.AppendOnlyLog`) replacing the O(N²) tuple rebuilds.
See `CHANGELOG.md` and `ROADMAP.md`.

## Not yet addressed

- A single integrated runtime spanning all engines (only `ExecutionPipeline` is wired today)
- `replay` integration with the execution path
- The OMS order book copies its whole order dict per stored order — the execution
  path's remaining super-linear term
- Multi-currency valuation (`PortfolioValuation` values the base currency only)
- Consolidation of the overlapping data surfaces (`data` / `market` / `marketdata` / `feed`)
- Live broker connectivity into `ExecutionPipeline`

---

# Contributing

Contributions are welcome.

Please read:

- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`

before submitting issues or pull requests.

---

# License

Released under the MIT License.

See `LICENSE` for details.

---

<div align="center">

**AlphaLab v2.1.0**

Building deterministic infrastructure for quantitative research.

</div>