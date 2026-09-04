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
| `ROADMAP.md` | Development roadmap and future milestones |
| `CHANGELOG.md` | Version history and release notes |
| `VISION.md` | Long-term goals and project philosophy |

---

# Architecture Overview

The diagram below is a **design target**, not one running system. Today
`alphalab.runtime.ExecutionPipeline` is the only wired-together path; every other
subsystem is a standalone, individually tested engine.

```
                         AlphaLab Workbench
                                 │
                                 ▼
                        Strategy Studio
                                 │
        ┌─────────────┬─────────────┬─────────────┐
        ▼             ▼             ▼             ▼
 Universal Data   Research Engine Portfolio Optimizer Production Runtime
        │             │             │             │
        └─────────────┴─────────────┴─────────────┘
                                 │
                        Broker Integrations
                                 │
                                 ▼
                            Live Markets
```

Integrated execution path that actually exists:

```
market event → strategy → allocation → risk → OMS → execution simulator
             → portfolio → analytics       (alphalab.runtime.ExecutionPipeline)
```

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

Load, normalize, validate, and transform market data from multiple providers into a canonical format.

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

## Production Runtime

Manage long-running trading systems with health monitoring, checkpointing, supervision, and recovery.

---

## Broker Integrations

Unified interface for paper trading and supported broker APIs.

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
`cluster_scheduler`, `experiment_tracking`, `model_registry`,
`research_assistant`, `deployment_manager`, `enterprise`. None are wired into
`ExecutionPipeline`.

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

`replay`, `studio`, `production`, and the broker packages are separate engines you
can call, but nothing chains them automatically. For a wired-together
market-to-portfolio-to-analytics path, use `alphalab.runtime.ExecutionPipeline`
directly.

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
v2.0.0
```

v2.0.0 consolidates the v1.34.0–v1.46.0 engine series, adds the model registry,
research assistant, deployment manager, and enterprise packages, unifies the
canonical execution domain models (R1–R4), and fixes two portfolio/analytics
defects (D1/D2). It contains breaking public API changes — see `../CHANGELOG.md`.

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