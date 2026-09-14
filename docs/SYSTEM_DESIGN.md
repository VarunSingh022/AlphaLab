# AlphaLab System Design

## Overview

This document describes the internal system design of AlphaLab.

While the Architecture document explains the overall organization of the platform, this document focuses on implementation details, execution flow, package interactions, and subsystem responsibilities.

The intended audience includes

- contributors
- maintainers
- plugin developers
- enterprise adopters

---

# Design Objectives

AlphaLab is designed to satisfy several engineering objectives.

- Deterministic execution
- Immutable state
- Event-driven workflows
- High testability
- Low coupling
- High cohesion
- Production readiness
- Long-term extensibility

Every implementation decision should reinforce these objectives.

---

# System Overview

```
                        User
                          │
                          ▼
                  AlphaLab Workbench
                          │
                          ▼
                  Strategy Studio
                          │
     ┌────────────┬───────┴──────┬─────────────┐
     ▼            ▼              ▼             ▼
 Universal    Research      Portfolio      Model &
 Data         Engine        Optimizer     Strategy
     │            │              │        Lifecycle
     └────────────┴───────┬──────┴─────────────┘
                          ▼
               Execution path (RunEngine)
                          │
                          ▼
                   Broker boundary
                          │
                          ▼
                    External Systems
```

---

# Integrated Execution Path

`alphalab.runtime.ExecutionPipeline` is the one place several domain engines are
composed. It is a stateless facade: each method takes an immutable
`ExecutionPipelineState` and a market event and returns a new state plus an
`ExecutionPipelineResult`. Internally it calls, in order, `StrategyEngine`,
`AllocationEngine`, `RiskEngine`, `OMSEngine`, `ExecutionEngine` (deterministic
simulator), `PortfolioEngine`, and — on demand — `AnalyticsEngine`. Boundary
conversions between packages live in `runtime/execution_adapters.py`; the
canonical entities (`core.enums.Side`, `core.OrderRequest`, `oms.order.Order`,
`core.Fill`, `core.Trade`) are preserved end to end.

`replay` is **not** standalone: since v2.2, `alphalab.backtesting.replay` drives
`ExecutionPipeline` from the replay cursor, so a replay produces the same orders,
fills and P&L as the equivalent backtest (ADR-0010). This document said otherwise
from v2.2 until v2.5.

**The run is owned above the step, as of v2.14.** `alphalab.runtime.run.RunEngine`
over `RunState` holds the cursor, the skips, the per-record steps and the
identifier scope a stopped run continues in; `ExecutionPipeline` keeps the
execution step and nothing else. `TradingSession`, `BacktestEngine`,
`ReplayBacktest` and `LiveSession` are **drivers** — each decides only which
record comes next and what clock reading judges it, and holds no state of its own
(ADR-0030).

Two further paths are wired, and neither runs through `ExecutionPipeline`:
`alphalab.lifecycle` composes experiment tracking, the model registry, the
deployment manager, `studio`, `enterprise` and `research` (v2.4, ADR-0013), and
`alphalab.market.provider` / `alphalab.market.stream` turn a provider's history
or a live socket into a `MarketDataSource` the run can read (v2.5, v2.15).
**The lifecycle path and the execution path meet** at
`lifecycle.execution.authorize_run` and `strategy.registry` — a query with a
refusal and a mapping with a refusal, not a second runtime (v2.16, v2.17).

Every other engine described in this document (the learning and asset-class
engines, `reporting`, `portfolio_optimizer`, `workbench`, …) is standalone and is
not invoked by either path. `alphalab/production`, which earlier revisions of this
document named here, was removed in v2.17.

---

# Execution Philosophy

AlphaLab executes operations through immutable state transitions.

Every operation follows the same lifecycle.

```
Request

↓

Validation

↓

Business Logic

↓

New State

↓

Events

↓

Return
```

The previous state is never modified.

---

# Core Design Principles

## Stateless Engines

Engine classes do not own mutable state.

Example

```python
new_state = ResearchEngine.run(
    previous_state,
    payload,
)
```

The engine acts as a coordinator.

---

## Managers

Managers implement business logic.

Responsibilities include

- validation
- orchestration
- state creation
- event generation

Managers remain internal implementation details.

---

## Registries

Registries own immutable collections.

Examples

- datasets
- brokers
- portfolios
- strategies

Registries never expose mutable containers.

---

## Views

Views provide read-only projections of state.

Views simplify inspection while preserving encapsulation.

---

## Validation

Every operation validates its inputs before execution.

Validation is centralized inside

```
validation.py
```

Validation should never be duplicated across managers.

---

# Package Interaction

The following diagram illustrates communication between major subsystems.

```
Workbench

↓

Studio

↓

Research

↓

Portfolio

↓

Replay

↓

Lifecycle

↓

Adapters (broker, marketdata)
```

Communication always follows public APIs.

---

# Engine Pattern

Every engine follows a common structure.

```
Engine

↓

Manager

↓

Registry

↓

State

↓

Events
```

The engine itself contains very little business logic.

---

# Internal Package Pattern

Each subsystem follows the same implementation model.

```
engine.py

↓

manager.py

↓

validation.py

↓

state.py

↓

events.py

↓

views.py
```

Consistency is preferred over cleverness.

---

# Immutable State

Every package owns one immutable state object.

Example

```
ResearchState

PortfolioState

OMSState

ExecutionPipelineState   (the execution step)

RunState                 (the run)

LifecycleState

StrategyStudioState
```

Each operation returns a new instance. The full ownership table is in
`ARCHITECTURE.md` under **State Ownership**, and the snapshot and schema for each
is in `STATE_MODEL.md`.

---

# Event Lifecycle

Operations generate events after successful execution.

```
Validation

↓

Execution

↓

Event

↓

State

↓

Return
```

Events never mutate state.

---

# Error Handling

Recoverable problems are represented by package-specific exceptions.

Examples

```
ResearchValidationError

BrokerValidationError

OptimizationError

MissingRateError / StaleRateError
```

Validation errors occur before business logic executes. A condition the system
cannot represent honestly is **refused** rather than defaulted — a missing or
stale FX rate, an instrument in a currency the run does not settle, an unknown
strategy identity, and a snapshot whose schema this build does not read all
raise.

---

# Deterministic Processing

The same inputs must always produce the same outputs.

This applies to

- research
- replay
- optimization
- reporting

Randomized algorithms should expose explicit seeds.

---

# Package Isolation

Subsystems should remain independent.

For example

Research should not import

- Lifecycle
- Workbench

Portfolio Optimizer should not import

- Market Data providers
- UI components

Isolation simplifies testing and maintenance.

---

# Public API

Applications interact only through

```
__init__.py
```

Example

```python
from alphalab.research import ResearchEngine
```

Internal modules are considered implementation details.

---

# Testing Strategy

Every subsystem is expected to provide

- unit tests
- deterministic outputs
- type safety
- complete Ruff compliance
- complete MyPy compliance

Testing mirrors the production package layout.

---

# Performance

Performance optimization should never compromise determinism.

Before introducing optimization, contributors should verify

- correctness
- reproducibility
- maintainability

Optimization is secondary to correctness.

---

# Extension Model

New functionality should extend

- protocols
- adapters
- plugins

rather than modifying existing engines.

The architecture favors extension over modification.

---

# Future Compatibility

The system design established in v1.0.0 absorbed distributed execution, cloud
research, machine learning and enterprise deployment as standalone packages
(v1.34.0–v2.0.0) without architectural redesign, and then absorbed the
integration work the same way: v2.2 wired `replay` onto the execution step, v2.3
converged the market-data and broker models, v2.4 composed the lifecycle
packages, v2.5 connected the provider boundary, v2.14 gave the run one owner,
v2.15 made the transports real, and v2.16 joined the two paths.

**As of v3.0.0 the architecture is frozen.** Wiring the remaining standalone
engines into an integrated path is not pending work — it is a deliberate
boundary (ADR-0009). What a future release may still add is listed in
`../ROADMAP.md` under *Optional future evolution*, and nothing depends on any of
it.

---

# Summary

AlphaLab follows a consistent engineering model across every subsystem.

```
Request

↓

Validation

↓

Manager

↓

Immutable State

↓

Events

↓

Return
```

This pattern enables predictable behavior, comprehensive testing, and long-term maintainability.

Future modules should preserve this execution model to ensure consistency across the platform.