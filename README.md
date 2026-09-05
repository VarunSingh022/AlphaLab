<div align="center">

# AlphaLab

### Institutional-Grade Quantitative Research & Algorithmic Trading Framework

**Deterministic • Event-Driven • Immutable • Fully Typed • Production-Oriented**

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)]()
[![Version](https://img.shields.io/badge/Version-2.5.0-blue)]()
[![License](https://img.shields.io/badge/License-MIT-green.svg)]()
[![Tests](https://img.shields.io/badge/Tests-2008%20Passing-success)]()
[![Typing](https://img.shields.io/badge/MyPy-Strict-blue)]()
[![Style](https://img.shields.io/badge/Ruff-Clean-red)]()

*A modular Python library for quantitative research, systematic strategy development, portfolio optimization, market simulation, broker integration, and production tooling.*

</div>

---

# What is AlphaLab?

AlphaLab is an open-source Python **library** for building deterministic quantitative research and algorithmic trading components.

It is a library, not a running application: there is no server, daemon, scheduler process, or CLI. You import the packages you need and call their pure, immutable engine APIs from your own code.

AlphaLab ships three kinds of package:

- **The integrated execution path.** `alphalab.runtime.ExecutionPipeline` is the one spine that wires several domain engines together — market data → strategy → allocation → risk → OMS → execution simulator → portfolio → analytics — as a chain of pure functions over one immutable state snapshot. `alphalab.backtesting` drives that spine from a dataset, either straight through or through `alphalab.replay`'s cursor; both call the same step, so a backtest and a replay of one dataset produce identical orders, fills and P&L.
- **The lifecycle path.** `alphalab.lifecycle` composes experiment tracking, the model registry, strategy definitions and the deployment manager into one flow: research candidate → experiment run → validation evidence → model version → strategy version → promotion → deployment → rollback. It sits *above* the execution path, not inside it: a deployment names what should run, and running it is the execution path's job.
- **State round-trip.** `capture` / `restore` turn `OMSState`, `PortfolioState` and `LifecycleState` into typed snapshots and back, through typed decoders that name the field when a payload is wrong. `alphalab.market.provider` feeds the execution path from a market-data provider's history rather than only from a stored dataset.
- **Standalone engine libraries.** Most other packages (research, portfolio optimizer, feature store, factor library, ML / deep learning / RL, options / futures / crypto / macro, alternative data, cloud research, cluster scheduler, enterprise, studio, workbench) are independent, deterministic, individually tested libraries. They share the engineering model but are **not** currently fused into a single runtime.

The framework is designed for researchers, quantitative developers, students, and engineering teams building reproducible trading infrastructure.

---

# Release Status

**Current Release:** **v2.5.0**

| Metric | Status |
|---------|--------|
| Python | 3.12+ |
| Version | 2.5.0 |
| Tests | **2008 Passing** |
| Static Typing | **Strict MyPy** (915 source files) |
| Linting | **Ruff Clean** |
| Package Build | ✅ Passing |
| Wheel Validation | ✅ Passing |
| Source Distribution | ✅ Passing |
| License | MIT |

v2.5.0 — "State Round-Trip and the Live Data Path" — takes three capabilities that
already existed, were already tested, and were unreachable, and makes them reachable.
`capture` / `restore` give **typed round-trip** to `PortfolioState` and
`LifecycleState` alongside `OMSState`, so the states AlphaLab writes can be read back
as typed values rather than nested dictionaries. `alphalab.market.provider` connects a
market-data provider to the execution path through the **normalization boundary v2.3
built and nothing called**. The replay cursor's O(N²) — on a path v2.2 had wired into
execution, and which the benchmark was written to avoid measuring — is gone. Two
behaviours that had never been decided now are: what a session does with an
**unordered source**, and what happens to a **partially filled order's remainder**.
Contains breaking changes confined to simulated-execution bookkeeping — see
`CHANGELOG.md` and `docs/ADR/0014-state-round-trip-and-the-live-data-path.md`.

> **A deployment is a lifecycle fact, not an operation on a machine.** It records that
> an environment *should* be running a strategy version. It starts no process, opens no
> connection and reaches no venue.
>
> **AlphaLab does not support live trading.** No broker adapter reaches any venue: the
> `alphalab.integrations` clients (Alpaca, IB, Zerodha) are canned-response stubs, and
> v2.3 added the adapter *contract* rather than a transport.
>
> Market *data* is the exception, and v2.5 corrects three releases of documentation
> that said otherwise: `alphalab.marketdata.binance` is a real REST client over a real
> HTTP transport, and has been since v1.39.0. It has never been run against a live
> endpoint from this environment, so treat it as unverified — but it is not a stub.
> See `docs/ADR/0012-broker-boundary-and-environment-parity.md`.

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
| Workflow & governance | `studio`, `workbench`, `enterprise` |
| Live / ops surface | `live`, `production`, `broker`, `brokers`, `integrations` |

> The Workbench → Studio → live-markets flow shown in `docs/` is a design target,
> not a single runtime that exists today. What is wired together is
> `ExecutionPipeline` and the packages that drive it — `backtesting` (including
> `replay`, as of v2.2) and `runtime.session` (paper and the live boundary, as of
> v2.3) — and, separately, `alphalab.lifecycle` (as of v2.4), which composes
> `experiment_tracking`, `model_registry`, `research_assistant` and
> `deployment_manager`. The two are not joined into one runtime: a deployment
> names what should run, and the execution path runs it. Connectivity to a real
> venue is still absent. See `ROADMAP.md`.

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
| 12 | `12_model_lifecycle.py` | Research candidate → model → strategy version → deploy → rollback |

Run any example:

```bash
python examples/01_research.py
```

Examples `01`–`10` date from v1.0.0 and exercise the standalone engine APIs;
`11` (v2.2) drives the integrated execution path and `12` (v2.4) drives the
lifecycle path. None are part of the automated test suite.

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

- ✅ 2008 passing tests (1698 unit, 109 integration, 201 regression)
- ✅ Strict MyPy type checking (915 source files)
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

**v2.4.0** — model and strategy lifecycle: `alphalab.lifecycle` composing
experiment tracking, the model registry, strategy definitions and the deployment
manager into research candidate → experiment run → validation evidence → model
version → strategy version → promotion → deployment → rollback; `StrategyVersion`,
the numbered immutable record that did not exist; typed `ModelRef` /
`StrategyVersionRef` / `DeploymentRef` replacing opaque manifest strings;
`ValidationEvidence` with content-derived ids, extracted from the
`PerformanceReport` and `ResearchScore` AlphaLab already produces; a promotion gate
requiring passing evidence and a staged model; the deployment ledger as the one
source of truth for what is live; `ArtifactRef` and a serializable `ModelVersion`
projection; declared stage transitions refusing `PRODUCTION → STAGING` and the
resurrection of an archived version that was never live; and the removal of the
quadratic writes in all three stateful lifecycle registries.

**v2.5.0** — state round-trip and the live data path: `capture` / `restore` for
`PortfolioState` and `LifecycleState` joining `OMSState`, over typed decoders
(`alphalab.persistence.decode`) that name the field they reject and refuse an
unknown schema version; `PersistenceAdapter.snapshot_payload` giving
`alphalab.persistence` its first production consumers;
`alphalab.market.provider.ProviderHistorySource` connecting a provider adapter to
`TradingSession` through the v2.3 normalization boundary; explicit
`SessionConfig.ordering` semantics for unordered sources; terminal semantics for a
partially filled simulated order's remainder, with its reservation released; and the
removal of the replay cursor's O(N²), with the benchmark repointed at the API the
integrated path actually uses.

See `CHANGELOG.md` and `ROADMAP.md`.

## Not yet addressed

- Connectivity to a real venue for **order execution**. v2.3 built and tested the
  adapter contract, the routing gates, reconciliation and the fill-return path; no
  broker transport exists, and the `integrations` broker clients are canned-response
  stubs. (Market *data* is different — see the note above)
- A **streaming** market-data source. v2.5's provider source reads a finite historical
  range; polling, subscription and reconnect need a clock and a loop AlphaLab does not
  have
- Round-trip for `ExecutionPipelineState` and `SessionState`. They hold strategy
  instances, a simulator, a sizing model and a fill policy, so restoring them means
  reconstructing the whole run configuration
- Artifact storage. `ArtifactRef` records where a model's bytes live and what they
  should hash to; AlphaLab never reads, writes or hashes them, and there is no
  object store
- A single integrated runtime spanning *all* engines (`ExecutionPipeline`,
  `backtesting`, `runtime.session` and `lifecycle` are what is wired today, and
  the lifecycle is not joined to the execution path)
- Approval workflow. A promotion is an auditable privileged action, but it is not
  wired to `alphalab.enterprise`'s RBAC or audit log
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

**AlphaLab v2.4.0**

Building deterministic infrastructure for quantitative research.

</div>