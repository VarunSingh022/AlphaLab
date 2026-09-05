<div align="center">

# AlphaLab

### Institutional-Grade Quantitative Research & Algorithmic Trading Framework

**Deterministic • Event-Driven • Immutable • Fully Typed • Production-Oriented**

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)]()
[![Version](https://img.shields.io/badge/Version-2.3.0-blue)]()
[![License](https://img.shields.io/badge/License-MIT-green.svg)]()
[![Tests](https://img.shields.io/badge/Tests-1599%20Passing-success)]()
[![Typing](https://img.shields.io/badge/MyPy-Strict-blue)]()
[![Style](https://img.shields.io/badge/Ruff-Clean-red)]()

*A modular Python library for quantitative research, systematic strategy development, portfolio optimization, market simulation, broker integration, and production tooling.*

</div>

---

# What is AlphaLab?

AlphaLab is an open-source Python **library** for building deterministic quantitative research and algorithmic trading components.

It is a library, not a running application: there is no server, daemon, scheduler process, or CLI. You import the packages you need and call their pure, immutable engine APIs from your own code.

AlphaLab ships two kinds of package:

- **The integrated execution path.** `alphalab.runtime.ExecutionPipeline` is the one spine that wires several domain engines together — market data → strategy → allocation → risk → OMS → execution simulator → portfolio → analytics — as a chain of pure functions over one immutable state snapshot. `alphalab.backtesting` drives that spine from a dataset, either straight through or through `alphalab.replay`'s cursor; both call the same step, so a backtest and a replay of one dataset produce identical orders, fills and P&L.
- **Standalone engine libraries.** Most other packages (research, portfolio optimizer, feature store, factor library, ML / deep learning / RL, options / futures / crypto / macro, alternative data, cloud research, cluster scheduler, experiment tracking, model registry, research assistant, deployment manager, enterprise, studio, workbench) are independent, deterministic, individually tested libraries. They share the engineering model but are **not** currently fused into a single runtime.

The framework is designed for researchers, quantitative developers, students, and engineering teams building reproducible trading infrastructure.

---

# Release Status

**Current Release:** **v2.3.0**

| Metric | Status |
|---------|--------|
| Python | 3.12+ |
| Version | 2.3.0 |
| Tests | **1737 Passing** |
| Static Typing | **Strict MyPy** (885 source files) |
| Linting | **Ruff Clean** |
| Package Build | ✅ Passing |
| Wheel Validation | ✅ Passing |
| Source Distribution | ✅ Passing |
| License | MIT |

v2.3.0 — "Market Data + Broker/Live Execution" — is a connectivity and convergence
release. It establishes one canonical market-data model (`alphalab.market`) with an
explicit normalization boundary over one canonical wire record, one canonical broker
adapter boundary (`alphalab.broker`) with reconciliation, and makes backtest, replay,
paper and live take the same canonical step through the same engines. It closes both
items v2.2 deferred: market-data model convergence and `broker` / `brokers`
consolidation. It also removed the quadratics those layers carried — the 100k-order
PaperBroker benchmark went from 676.70s to 4.65s, and market-data ingestion no longer
slows down as the universe widens. Contains breaking changes confined to
`alphalab.brokers` — see `CHANGELOG.md`.

> **AlphaLab does not support live trading.** v2.3 adds the adapter *contract* a live
> venue would be reached through, and tests both directions of it. There is no
> connectivity to any real venue in this repository; every vendor client is a stub.
> See `docs/ADR/0012-broker-boundary-and-environment-parity.md` for the precise
> implemented / adapter-only / absent breakdown.

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
| Research & simulation | `research`, `reporting` |
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
> not a single runtime that exists today. What is wired together is
> `ExecutionPipeline` and the packages that drive it: `backtesting` (including
> `replay`, as of v2.2) and `runtime.session` (paper and the live boundary, as of
> v2.3). Market data and the broker boundary converged in v2.3; connectivity to a
> real venue is still absent. See `ROADMAP.md`.

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
| 02 | `02_backtest.py` | Strategy Studio backtest bookkeeping |
| 03 | `03_replay.py` | Historical replay over the real execution path |
| 04 | `04_market_data.py` | Market Data |
| 05 | `05_broker_connection.py` | Broker Integrations |
| 06 | `06_portfolio_optimizer.py` | Portfolio Optimizer |
| 07 | `07_universal_data.py` | Universal Data Engine |
| 08 | `08_strategy_studio.py` | Strategy Studio |
| 09 | `09_workbench.py` | Workbench |
| 10 | `10_complete_pipeline.py` | Multi-engine walkthrough |
| 11 | `11_unified_backtest.py` | Dataset → orders → fills → P&L → analytics |

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
├── backtesting/     Dataset → execution path → analytics (backtest + replay)
├── replay/          Deterministic replay cursor (drives backtesting)
├── research/  portfolio_optimizer/  reporting/               Standalone engines
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

- ✅ 1599 passing tests (1441 unit, 57 integration, 101 regression)
- ✅ Strict MyPy type checking (870 source files)
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

**v2.2.0** — unified backtesting and replay: `alphalab.backtesting` composes
`ExecutionPipeline` into a real backtest, and `alphalab.replay` drives the same
path; fill policies (`ImmediateFill`, `StaticFill`, `LiquidityCappedFill`);
persistent order-book and execution-report containers
(`common.PersistentMap` / `PersistentSet`) replacing the quadratic
dict/frozenset copying; a per-order allocation
reservation ledger released exactly once; complete round-trippable `OMSState`
snapshots; seeded, reproducible identifiers.
**v2.3.0** — market data and broker/live execution: one canonical market-data
model (`alphalab.market`) over one canonical wire record, with an explicit
normalization boundary (`market.normalization`) and adapter contract
(`market.source`); one canonical broker boundary (`alphalab.broker`) that
`alphalab.brokers` routes rather than redefines; order/fill reconciliation with
defined answers for duplicate, out-of-order, unknown and terminal-order fills and
the cancel/fill race; `runtime.session.TradingSession` driving backtest, replay,
paper and live through one canonical step; `runtime.broker_routing` carrying an
order out to a venue and a fill back through the same portfolio accounting a
simulated fill uses; and the removal of the quadratic index rebuilds in the
broker, market, marketdata, live and feed states.

See `CHANGELOG.md` and `ROADMAP.md`.

## Not yet addressed

- Connectivity to a real venue. v2.3 built and tested the adapter contract, the
  routing gates, reconciliation and the fill-return path; the transport does not
  exist, and every vendor client is a stub
- A single integrated runtime spanning *all* engines (`ExecutionPipeline`,
  `backtesting` and `runtime.session` are what is wired today)
- Strategies do not see the marked portfolio: `StrategyContext` comes from the
  caller's `context_factory`
- Multi-currency valuation (`PortfolioValuation` values the base currency only)

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

**AlphaLab v2.3.0**

Building deterministic infrastructure for quantitative research.

</div>