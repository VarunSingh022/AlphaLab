# AlphaLab Documentation

Welcome to the official AlphaLab documentation.

This documentation provides a comprehensive guide to AlphaLab's architecture, design philosophy, engineering standards, and development workflow.

Whether you are evaluating AlphaLab, contributing to the project, or building quantitative trading systems, this documentation serves as the central reference.

---

# Documentation Overview

The documentation is organized into several categories.

```
Getting Started
│
├── Installation
├── First Project
└── Quick Start

Architecture
│
├── System Architecture
├── System Design
├── Event Model
└── State Model

Development
│
├── Engineering Guidelines
├── Contributing
└── ADRs

Reference
│
├── Examples
├── Roadmap
├── Changelog
└── Vision
```

---

# Documentation Index

## Getting Started

| Document | Description |
|-----------|-------------|
| `GETTING_STARTED.md` | Installation, project setup, and first steps with AlphaLab |
| `EXAMPLES.md` | End-to-end examples covering research, backtesting, portfolio optimization, and production workflows |

---

## Architecture

| Document | Description |
|-----------|-------------|
| `ARCHITECTURE.md` | High-level architecture of AlphaLab |
| `SYSTEM_DESIGN.md` | Internal design and interaction between subsystems |
| `EVENT_MODEL.md` | Event-driven architecture and lifecycle |
| `STATE_MODEL.md` | Immutable state management across all modules |

---

## Engineering

| Document | Description |
|-----------|-------------|
| `ENGINEERING_GUIDELINES.md` | Coding standards, architectural principles, and project conventions |
| `CONTRIBUTING.md` | Development workflow and contribution process |
| `ADR/` | Architectural Decision Records documenting major design decisions |

---

## Project

| Document | Description |
|-----------|-------------|
| `../ROADMAP.md` | What is delivered, what is a deliberate boundary, what is external, what is optional |
| `../CHANGELOG.md` | Version history and release notes. Each entry is scoped to its own release |
| `../nowandfuture.md` | The long-form project reference: ownership, invariants, and what must not change casually |
| `VISION.md` | Long-term goals and project philosophy |
| `work/` | Per-release working handoffs (v2.15, v2.16). **Historical**: each records the state during that release, not the current state |

---

# Architecture Overview

AlphaLab has **two** wired-together paths, and as of v2.16 they meet. Everything
else is a standalone, individually tested engine (ADR-0009).

```
                    AlphaLab Workbench
                            │
                            ▼
                    Strategy Studio
                            │
    ┌───────────────┬───────┴───────┬───────────────┐
    ▼               ▼               ▼               ▼
 Universal      Research        Portfolio      Model & Strategy
   Data          Engine         Optimizer        Lifecycle
    │               │               │               │
    └───────────────┴───────┬───────┴───────────────┘
                            ▼
                 Execution path (RunEngine)
                            │
                            ▼
                     Broker boundary
                            │
                            ▼
                       Live Markets
```

The paths that exist, precisely:

```
the execution step        (alphalab.runtime.ExecutionPipeline)
  market event → mark to market → strategy → allocation → risk → OMS
               → execution → portfolio → analytics

the run                   (alphalab.runtime.run.RunEngine, v2.14)
  driven by TradingSession | BacktestEngine | ReplayBacktest | LiveSession

market data in            (alphalab.market.provider v2.5, alphalab.market.stream v2.15)
  provider adapter → normalization → MarketDataSource → the run

orders out, fills back    (alphalab.runtime.broker_routing v2.3, LiveSession v2.16)
  OMS order → BrokerProtocol → venue → ExecutionPipeline.apply_execution_report

the lifecycle             (alphalab.lifecycle, v2.4)
  research candidate → experiment run → validation evidence → model version
    → strategy version → promotion → deployment → rollback
```

**The two paths are joined, as of v2.16.** `lifecycle.execution.run_plan`
resolves what an environment has live and `authorize_run` refuses a run that
would serve anything else; `strategy.registry` (v2.17) turns the identity a
deployment names into the code a run executes. Both are queries with refusals,
not a second runtime — a deployment names what should run, and the execution path
runs it. *(This section said the two were "deliberately not joined" from v2.4
until v3.0 corrected it.)*

Each subsystem is independently testable, immutable where appropriate, and designed around deterministic execution.

---

# Core Components

AlphaLab consists of the packages below plus the canonical execution core
(`alphalab.core`, `runtime`, `strategy`, `allocation`, `risk`, `oms`,
`execution`, `portfolio`, `analytics`, `market`) that `ExecutionPipeline` wires
together.

## Research

Develop and validate quantitative strategies using deterministic research workflows.

---

## Universal Data Engine

Turn a raw source — a CSV on disk, an upload, a broker export, rows already in
memory — into a canonical dataset that can say where it came from.

Schema detection reports what it resolved and refuses what is ambiguous;
validation returns structured findings with source lines; cleaning runs under a
policy the caller supplies and records every change it makes. The result carries
provenance and a **derived, immutable version** that a backtest names as the
exact data it consumed.

AlphaLab ships no holiday data, no corporate actions and no vendor feed — the
calendars and the adjustment arithmetic are the mechanism, and the data is the
application's to supply.

---

## Market Data

Unified access to historical and live market data providers.

---

## Portfolio Optimizer

Construct institutional-grade portfolios using multiple optimization techniques, risk constraints, and transaction cost models.

---

## Replay Engine

Replay historical market events deterministically for repeatable backtests.

---

## Model and Strategy Lifecycle

Take a research candidate to a deployment and back: experiment run, validation
evidence, model version, strategy version, gated promotion, the deployment ledger
as the one answer to what is live, and deterministic rollback — with every act
naming the principal that caused it.

*(`alphalab/production`, which supervised long-running systems, had zero
importers and was removed in v2.17. Durable run state is
`alphalab.persistence.RunStateStore`; a live loop is
`alphalab.runtime.live.LiveSession`; supervision is the operator's.)*

---

## Broker boundary

Two boundaries, deliberately: `alphalab.broker` for **one** venue
(`BrokerProtocol`, implemented by `PaperBroker` and `RestVenueBroker`), and
`alphalab.brokers` for **many** venues and accounts
(`BrokerConnectorProtocol`), which routes the canonical `alphalab.broker` types
rather than redefining them.

---

## Strategy Studio

Coordinate research projects, pipelines, experiments, datasets, reports, and backtests through a single orchestration layer.

---

## AlphaLab Workbench

User-facing workspace for managing projects, monitoring strategies, visualizing results, and interacting with the Strategy Studio.

---

## Engine libraries (v1.34.0 – v2.0.0)

Additional standalone, individually tested engines added after v1.0.0:
`feature_store`, `factor_library`, `alt_data`, `options`, `futures`, `crypto`,
`macro`, `ml`, `deep_learning`, `reinforcement_learning`, `cloud_research`,
`cluster_scheduler`. None is wired into `ExecutionPipeline`.

`experiment_tracking`, `model_registry`, `deployment_manager`, `studio`,
`enterprise` and `research` are imported by `alphalab.lifecycle` as of v2.4 and
remain usable on their own. `research_assistant` is the one the lifecycle names
without importing: it produces a candidate and `to_strategy_definition` lifts it
into the canonical `StrategyDefinition` the lifecycle takes.

---

# Development Workflow

A typical research workflow composes the standalone engines by hand — you pass
each engine's immutable output into the next:

```
Market Data
      │
      ▼
Universal Data Engine
      │
      ▼
Research
      │
      ▼
Portfolio Optimization
      │
      ▼
Reporting
```

`replay` and the workbench are separate engines you can call, and nothing chains
the research → optimization → reporting sequence above automatically. For a
wired-together market-to-portfolio-to-analytics path, use
`alphalab.backtesting.BacktestEngine` over
`alphalab.runtime.ExecutionPipeline`; for the research-candidate-to-deployment
sequence, use `alphalab.lifecycle`.

---

# Engineering Principles

AlphaLab is built around a consistent set of engineering principles.

- Immutable state
- Event-driven architecture
- Deterministic execution
- Pure functional APIs
- Strict static typing
- Comprehensive automated testing
- Modular subsystem design
- Production-first engineering

These principles are applied consistently across every module.

---

# Version

```
v3.1.0
```

*(This block read `v2.5.0` from v2.5 through v2.16 — twelve releases that shipped
without updating it — and the v2.17 audit corrected it. The release checklist now
has to touch `README.md`, `docs/ARCHITECTURE.md`'s Implementation Status and this
block together, because all three have drifted independently before.)*

**v3.1.0 — universal data ingestion.** The first release after the v3.0
architecture freeze, and a capability release confined to one package.
`alphalab.data` gains the ingestion, validation, cleaning, provenance and
identity machinery it was named for and did not have: CSV as a first-class
input, schema detection that refuses to guess, structured validation findings,
cleaning under a policy with no defaults, market calendars, multi-asset
semantics, the raw/adjusted price basis, and a **derived, immutable dataset
version** that reaches `BacktestResult.dataset_id` and `ValidationEvidence`
unchanged — with the evidence digest untouched. No boundary moves and no
ownership changes. See `ADR/0036`.

**v3.0.0 — the stable release.** The architecture is frozen and the documentation
is made to match it. No capability is added, no boundary moves, no schema changes
and no public name is removed: v2.17 took the removals a release early so that
v3.0 could be additive. What "frozen" commits to is written down in
`../nowandfuture.md`, and `../ROADMAP.md` now classifies everything that remains
as a deliberate boundary, an external dependency, or optional evolution.

v2.17.0 — "The Final Engineering Release" — exists so that v3.0 had nothing to do
but freeze. **Settlement-level multi-currency**: a run settles fills in more than
one currency, accruing P&L and commission in the currency each was earned in, and
reports one figure in one currency with the rates that produced it. **An FX rate
feed** (`alphalab.portfolio.fx_feed`): the boundary rates arrive across, with
ordering, deduplication and conflict rules — AlphaLab ships no FX data. **A
strategy-class registry** (`alphalab.strategy.registry`): what turns the identity
a deployment names into the code a run executes. Alongside them, seven deprecated
surfaces removed with no aliases, all four of ADR-0032's category C items
implemented, and a suite reporting zero skips and zero warnings. See `ADR/0034`
and `ADR/0035`.

Earlier milestones: v2.16.0 closed three joins — the live driver, governance and
FX valuation — and classified twenty-one structural findings (ADR-0032,
ADR-0033); v2.15.0 built the five capabilities that had a contract and nothing
behind it (ADR-0031); v2.14.0 unified the runtime under `RunEngine` (ADR-0030);
v2.13.0 gave a captured run somewhere durable to go (ADR-0029); v2.12.0 made the
instrument registry the currency authority (ADR-0028); v2.11.0 added instrument
classification with sector provenance (ADR-0027); v2.10.0 finished the strategy
boundary (ADR-0025, ADR-0026); v2.9.0 added deterministic identifier continuation
(ADR-0022, ADR-0023); v2.8.0 settled currency roles and run outcomes (ADR-0019 to
ADR-0021); v2.7.0 established instrument identity and dataset provenance
(ADR-0016 to ADR-0018); v2.6.0 gave allocation authority and attribution truth
(ADR-0015); v2.5.0 made states round-trip and connected the live data path
(ADR-0014); v2.4.0 composed the model and strategy lifecycle (ADR-0013); v2.3.0
unified the market-data and broker models (ADR-0011, ADR-0012); v2.2.0 unified
backtesting and replay (ADR-0010); v2.1.0 added mark-to-market and removed the
O(N²) engine histories; v2.0.0 consolidated the v1.34.0–v1.46.0 engine series and
unified the canonical execution domain models. Several releases contain breaking
public API changes — see `../CHANGELOG.md`.

---

# Documentation Conventions

Throughout the documentation:

- Python examples target Python 3.12+
- All APIs use immutable state where applicable
- Event names follow PascalCase
- Modules follow consistent naming conventions
- Code snippets are simplified for clarity while remaining representative of the actual implementation

---

# Contributing to Documentation

Documentation improvements are welcome.

Please ensure that:

- Examples remain synchronized with the codebase.
- Architecture diagrams reflect the latest system design.
- New modules are added to the documentation index.
- Cross-references remain valid after changes.

See `CONTRIBUTING.md` for the full contribution workflow.

---

# Additional Resources

- Root `README.md` – Project overview
- `LICENSE` – Licensing information
- `CHANGELOG.md` – Release history
- `ROADMAP.md` – Planned features
- `ADR/` – Architectural Decision Records

---

# Next Reading

If you are new to AlphaLab, continue with:

1. **GETTING_STARTED.md**
2. **ARCHITECTURE.md**
3. **SYSTEM_DESIGN.md**
4. **EXAMPLES.md**

These documents provide the recommended path for understanding the platform from installation through production deployment.