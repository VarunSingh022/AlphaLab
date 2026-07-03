# AlphaLab Architecture

## Overview

AlphaLab is an institutional-grade, event-driven quantitative trading framework designed around immutable state, deterministic execution, and modular architecture.

Every subsystem has a single responsibility and communicates through well-defined interfaces.

The framework is designed to support the complete quantitative research lifecycle:

- Historical Replay
- Strategy Research
- Portfolio Construction
- Risk Management
- Order Management
- Execution Simulation
- Analytics
- Optimization
- Reporting
- Live Market Infrastructure
- Broker Connectivity

---

# Core Design Principles

## Immutable State

Every subsystem is built around immutable state.

State objects are implemented using frozen dataclasses and are never modified after creation.

Instead of mutating existing state, every engine returns a completely new state.

```
Old State
      │
      ▼

 Pure Function

      │
      ▼

New State
```

Benefits:

- Deterministic execution
- Easier debugging
- Thread safety
- Replayability
- Predictable testing

---

## Pure Functional Engines

Each engine behaves like a mathematical function.

```
(state, input)
      │
      ▼
Engine
      │
      ▼
(new_state, events)
```

There are no hidden side effects.

---

## Event-Driven Architecture

Subsystems communicate using immutable domain events.

```
Market Event
      │
      ▼
Strategy
      │
      ▼
Allocation
      │
      ▼
Risk
      │
      ▼
OMS
      │
      ▼
Execution
      │
      ▼
Portfolio
      │
      ▼
Analytics
```

This allows components to remain loosely coupled while preserving deterministic execution.

---

# System Architecture

```
                         AlphaLab

                         Kernel
                            │
            ┌───────────────┼───────────────┐
            │               │               │
         Replay          Scheduler       Runtime
            │
         Market
            │
          Feed
            │
        Strategy
            │
       Allocation
            │
           Risk
            │
            OMS
            │
       Execution
            │
        Portfolio
            │
       Analytics
            │
       Reporting
            │
      Persistence
            │
       Optimizer
            │
        Plugins
            │
      Distributed
            │
     Live Market Layer
            │
 Broker Connector Layer
```

---

# Module Overview

## Core

Provides the shared foundation used throughout the framework.

Responsibilities:

- Identifiers
- Enumerations
- Base models
- Shared utilities

---

## Events

Provides immutable event definitions used across all engines.

Responsibilities:

- Event types
- Event registry
- Event pipeline
- Event queue

---

## Kernel

Coordinates system lifecycle.

Responsibilities:

- Startup
- Shutdown
- Engine orchestration

---

## Strategy

Defines trading logic.

Strategies consume market information and produce trading intentions.

Strategies remain independent from execution and brokerage concerns.

---

## Replay

Provides deterministic historical replay.

Features:

- Single-step replay
- Timestamp replay
- Replay lifecycle
- Pause
- Resume
- Reset

---

## Feed

Responsible for market data ingestion.

Supports deterministic historical feeds.

---

## Market

Maintains normalized market state.

Responsibilities:

- Market updates
- Price state
- Instrument state

---

## Allocation

Transforms strategy signals into portfolio allocations.

Supports multiple allocation methodologies while remaining independent of execution.

---

## Risk

Applies portfolio and order constraints before execution.

Responsibilities include:

- Exposure limits
- Position limits
- Order validation

---

## OMS

Order Management System.

Responsible for maintaining order lifecycle.

States include:

- Submitted
- Accepted
- Filled
- Cancelled
- Rejected

---

## Execution

Simulates deterministic execution.

Handles:

- Partial fills
- Complete fills
- Execution events

---

## Portfolio

Maintains account state.

Responsibilities:

- Cash
- Positions
- Equity
- Holdings

---

## Analytics

Computes quantitative performance metrics.

Includes:

- Returns
- Volatility
- Sharpe Ratio
- Sortino Ratio
- Drawdowns
- Value at Risk
- Rolling statistics
- Attribution

---

## Runtime

Supervises long-running execution.

Provides:

- Runtime lifecycle
- Metrics
- Heartbeats
- Monitoring

---

## Persistence

Provides deterministic persistence.

Supports:

- Snapshots
- Event storage
- Serialization

---

## Optimizer

Parameter optimization framework.

Supports:

- Search spaces
- Objectives
- Validation
- Optimization engine

---

## Reporting

Transforms analytical results into reports.

Supported formats:

- JSON
- CSV
- Markdown

---

## Plugins

Extensible plugin architecture.

Allows custom:

- Strategies
- Data providers
- Future framework extensions

---

## Distributed

Infrastructure for distributed research.

Provides:

- Worker management
- Job scheduling
- Distributed execution primitives

---

## Live

Vendor-independent live market infrastructure.

Provides:

- Market snapshots
- Trade ticks
- Quote ticks
- Subscription management
- Provider abstraction

No networking implementation is included.

---

## Broker

Execution-side broker domain.

Responsible for:

- Paper broker implementation
- Broker state
- Order execution
- Position management

---

## Brokers

Broker Connector Framework.

Provides abstractions for connecting AlphaLab to external broker APIs.

Responsibilities:

- Account management
- Connection management
- Order routing
- Execution reports
- Broker registry

This module intentionally contains no vendor-specific implementations.

---

# Dependency Rules

Higher-level modules may depend on lower-level modules.

Lower-level modules must never depend on higher-level modules.

```
Core

↓

Events

↓

Market

↓

Strategy

↓

Allocation

↓

Risk

↓

OMS

↓

Execution

↓

Portfolio

↓

Analytics

↓

Reporting
```

Circular dependencies are prohibited.

---

# Quality Standards

Every module in AlphaLab follows the same engineering standards.

- Immutable state
- Frozen dataclasses
- `slots=True`
- Strict MyPy typing
- Ruff compliant
- Pure functional engines
- Comprehensive unit tests

---

# Testing Philosophy

Each subsystem is independently tested.

The project currently includes over 400 automated unit tests covering:

- Engine lifecycle
- Validation
- State transitions
- Edge cases
- Error handling
- Deterministic behavior

Every pull request is expected to pass:

```bash
ruff check .

mypy .

pytest
```

before merging.

---

# Future Architecture

The current architecture provides the foundation for future capabilities including:

- Strategy validation
- Paper trading
- Broker integrations
- Live execution
- Production deployment
- Cloud orchestration
- Multi-node distributed research

The architectural principles of immutability, deterministic execution, and modularity will remain unchanged as the framework evolves.