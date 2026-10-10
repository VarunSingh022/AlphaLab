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
| `GETTING_STARTED.md` | Installation, the repository, and building, checking and reproducing a first strategy |
| `EXAMPLES.md` | End-to-end examples covering research, backtesting, portfolio optimization, and production workflows |
| `api/PUBLIC_API.md` | The public API, its manifest, and the stability policy from v4.0 |
| `INTEGRATION.md` | Depending on AlphaLab from an application: how to install a release, the tested Python, and what an application may rely on |

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
| `../ROADMAP.md` | What is delivered, what is a deliberate boundary, what is external, what is a stated limitation, and the future work accepted for later |
| `audit/` | The pre-v4 audit and completion ledger, the v4.0 release audit, the historical inventory and the release certificate |
| `../CHANGELOG.md` | Version history and release notes. Each entry is scoped to its own release |
| `../nowandfuture.md` | The long-form project reference: ownership, invariants, and what must not change casually |
| `VISION.md` | Long-term goals and project philosophy |
| `work/` | Per-release working handoffs (v2.15, v2.16). **Historical**: each records the state during that release, not the current state |

---

# Architecture Overview

AlphaLab has **two** wired-together paths, and as of v2.16 they meet. Everything
else is a standalone, individually tested engine (ADR-0009).

```
            the host application (UI, accounts, vendor adapters, credentials)
                            │  uses AlphaLab's public API
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
                     Broker boundary  ──  the host's venue adapter
```

Presentation, orchestration, identity and vendor connectivity are the host
application's: the Strategy Studio, the Workbench and Enterprise left the
library in v3.11 (ADR-0046).

The paths that exist, precisely:

```
the execution step        (alphalab.runtime.ExecutionPipeline)
  market event → mark to market → strategy → allocation → risk → OMS
               → execution → portfolio → analytics

the run                   (alphalab.runtime.run.RunEngine, v2.14)
  driven by TradingSession | BacktestEngine | ReplayBacktest | LiveSession

market data in            (alphalab.market.provider v2.5, alphalab.market.stream v2.15)
  host provider → wire records → normalization (BarStamp, v3.10) → MarketDataSource → the run

orders out, fills back    (alphalab.runtime.broker_routing v2.3, LiveSession v2.16)
  OMS order → BrokerProtocol → venue → ExecutionPipeline.apply_execution_report

the execution contract    (alphalab.core / broker / execution / runtime, v3.9)
  capability check → algorithm children → route decision → route_child_order
    → normalized VenueEvents → mirror → apply_broker_execution (fills on the parent)
    → snapshot and book-to-mirror reconciliation → execution quality

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
(`BrokerProtocol`, implemented by `PaperBroker`; an adapter that reaches a real
venue is the host application's since v3.11), and
`alphalab.brokers` for **many** venues and accounts
(`BrokerConnectorProtocol`), which routes the canonical `alphalab.broker` types
rather than redefining them.

---

## Engine libraries (v1.34.0 – v2.0.0)

Additional standalone, individually tested engines added after v1.0.0:
`feature_store`, `factor_library`, `alt_data`, `options`, `futures`, `crypto`,
`macro`, `ml`, `deep_learning`, `reinforcement_learning`, `cloud_research`,
`cluster_scheduler`. None is wired into `ExecutionPipeline`.

**As of v3.2, `factor_library` is no longer standalone**: `alphalab.research`
imports it and `alphalab.lifecycle` imports `research`, so it is reached by the
lifecycle path, and `alphalab.api` reaches it directly.
`tests/regression/test_one_research_authority_per_concept.py` measures that
rather than leaving it to a sentence here. `feature_store` remains standalone,
which is the compute/registry seam working: it computes nothing, so the
computation engine writes through `FeatureValueProtocol` without either package
importing the other.

**As of v3.7, `alt_data` is no longer standalone either**: `alphalab.research`
imports it for event studies, `factor_library` for knowledge frames and
`alphalab.api` for ingestion, so it too is reached by the lifecycle path. It
imports `alphalab.common` and nothing else, and
`tests/regression/test_v37_invariants.py` measures that edge set (ADR-0042).

**As of v3.8, `portfolio_optimizer` reads the risk model in `analytics`** — the
one covariance authority — and is still standalone: nothing on either path
imports it, because a construction answers what to own and turning it into
orders is the caller's decision. `tests/regression/test_v38_invariants.py`
measures its edge set, `common`, `analytics` and, since v3.11, `conventions`
for lot arithmetic (ADR-0043, ADR-0046).

`experiment_tracking`, `model_registry`, `deployment_manager` and `research`
are imported by `alphalab.lifecycle` as of v2.4 (`studio` and `enterprise` were
too, until v3.11 removed them) and
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

`replay` is a separate engine you can call, and nothing chains
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
v4.0.0
```

*(This block read `v2.5.0` from v2.5 through v2.16 -- twelve releases that shipped
without updating it -- and the v2.17 audit corrected it. Since v3.13
`tests/regression/test_version_markers_agree.py` holds it to the package.)*

**v4.0.0 -- the freeze of the universal engine contract.** The v2.0.0 to v3.13.0
history read and every deferral, non-goal, limitation and open question in it
classified with evidence (`audit/V4_HISTORICAL_INVENTORY.md`); the canonical path
re-audited, four defects fixed -- a restore that accepted a differently
configured live object, ingestion that dropped rows a refusing policy should
have refused, option pricing that returned `NaN`, fractional fills in a
whole-unit run -- each with a test that fails without the fix; a strategy built,
checked and reproduced through the public API (`GETTING_STARTED.md`,
`../examples/70_build_a_strategy.py`); and the public API held stable from here,
`alphalab.api` and the snapshot modules now within it (`api/PUBLIC_API.md`).
ADR-0049; the audit is `audit/V4_RELEASE_AUDIT.md`.

The release-by-release history -- what each of v1.0 to v3.13 established, and
every breaking change with its migration -- is in `../CHANGELOG.md`; the four
major milestones are summarized in the root `README.md`.

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
- `ROADMAP.md` – Boundaries, limitations and future work
- `ADR/` – Architectural Decision Records

---

# Next Reading

If you are new to AlphaLab, continue with:

1. **GETTING_STARTED.md**
2. **ARCHITECTURE.md**
3. **SYSTEM_DESIGN.md**
4. **EXAMPLES.md**

These documents provide the recommended path for understanding the platform from installation through production deployment.