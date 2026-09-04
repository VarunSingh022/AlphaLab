# Changelog

All notable changes to AlphaLab are documented in this file.

This project follows the principles of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and adheres to Semantic Versioning.

---

# [2.0.0] - Unreleased

## Overview

AlphaLab 2.0.0 is the v2 release line. It consolidates the v1.34.0–v1.46.0 engine
series, adds four new standalone packages, unifies the canonical execution-path
domain models, and fixes two portfolio/analytics defects found by an end-to-end
trading-research validation.

AlphaLab remains a library: there is no server, daemon, scheduler process, or CLI.
`alphalab.runtime.ExecutionPipeline` is the only spine that wires domain engines
together (market → strategy → allocation → risk → OMS → execution simulator →
portfolio → analytics); every other package is an independent, individually tested
engine that is not fused into a single runtime.

---

## Breaking Changes

Public symbols removed or changed. Import sites must be updated.

- **`alphalab.core.OrderRequest` is now the single proposed-order DTO.** The
  independent `alphalab.allocation.request.OrderRequest` /
  `alphalab.allocation.request.OrderSide` and the independent
  `alphalab.risk.models.OrderRequest` / `alphalab.risk.models.OrderSide` are
  removed. `alphalab.allocation.request` no longer exists. `alphalab.risk.models`
  now contains only `RiskViolation`. `OrderSide` is no longer part of the
  `alphalab.allocation` or `alphalab.risk` public API — use
  `alphalab.core.enums.Side`.
- **`alphalab.core.enums.Side` is the canonical order direction** everywhere on
  the execution path (`OrderRequest`, `oms.order.Order`, `Fill`, `Trade`, risk
  and allocation checks). `BUY`/positive and `SELL`/negative semantics are
  unchanged.
- **`alphalab.oms.order.Order` is the canonical lifecycle order.** The
  `alphalab.core.order` module and the `alphalab.core.Order` re-export are
  removed, along with `oms.order.Order`'s unused adapter surface
  (`to_core_order`, `from_core_order`, `canonical_order`). Every
  `oms.order.Order` field, property, and lifecycle transition is unchanged.
- **`Fill.filled_at` and `Trade.executed_at` are now `float`** (Unix seconds),
  matching every other timestamp on the execution path. They were previously
  timezone-aware `datetime`; the tz-aware `__post_init__` guard is removed. In
  `dataclasses.asdict` output these fields are now numbers, not `datetime`
  objects.
- **Removed proven-dead `alphalab.core` symbols:** `OrderCompat`, `Event`
  (`core/event.py`), `Signal` (`core/signal.py`), and the
  `alphalab.core.portfolio` / `alphalab.core.position` re-export shims. Use
  `alphalab.portfolio.engine.PortfolioState` and
  `alphalab.portfolio.position.Position` directly. `core.ids`
  (`PortfolioId` / `PositionId` / `SignalId` / `new_*`) is unaffected.
- **Package version** is `2.0.0`; the `alphalab.common.version` fallback and the
  `Development Status` classifier (`4 - Beta`) are updated to match.

---

## Added

### New packages (PR047–PR050)

- **Model Registry** (`alphalab.model_registry`) — versioned registration of
  trained model artifacts, `NONE → STAGING/PRODUCTION/ARCHIVED` promotion with an
  immutable promotions log, production rollback, and deployment metadata. Links
  to `alphalab.experiment_tracking` runs by id. State threaded functionally
  through immutable `ModelRegistry` values; no on-disk serialization.
- **AI Research Assistant** (`alphalab.research_assistant`) — deterministic,
  offline (no LLM, no network) grid-search research driver: candidate generation
  over a parameter grid, evaluation via a caller-supplied evaluator, best-first
  ranking, Markdown reporting, a one-call `run_research_workflow`, and a bridge
  that lifts a chosen candidate into an `alphalab.studio` `StrategyDefinition`.
- **Deployment Manager** (`alphalab.deployment_manager`) — packaging of
  strategy/model stacks into versioned, SHA-256-checksummed `ReleasePackage`s
  (manifest only, no artifact bytes), an append-only ledger of which release is
  active per environment, and previous-release rollback.
- **AlphaLab Enterprise** (`alphalab.enterprise`) — deterministic in-memory
  governance layer over one immutable `EnterpriseState`: session-lifecycle
  identity (no credentials accepted or stored), RBAC, append-only audit log,
  multi-user workspaces, secret *references* + rotation metadata (no secret
  values), and a compliance snapshot.

### Engine series (v1.34.0 – v1.46.0, PR034–PR046)

Delivered on `main` before the v2 line and included in 2.0.0: feature store,
factor library, options engine, futures engine, crypto engine, macro engine,
alternative data engine, machine learning engine, deep learning engine,
reinforcement learning engine, cloud research engine, cluster scheduler, and
experiment tracking. Each is a standalone deterministic package with its own
tests and benchmark.

---

## Changed — canonical execution domain models (R1–R4)

- **R1** — unified allocation/risk `Side` and `OrderRequest` into
  `alphalab.core` (see Breaking Changes). `alphalab.runtime.execution_pipeline`
  no longer converts requests field-by-field across the allocation → risk → OMS
  boundary; the `_risk_request` / `_core_side` bridges and
  `execution_adapters.core_side_from_oms` are deleted.
- **R2** — removed the five proven-dead `alphalab.core` symbols (see Breaking
  Changes). Implementations deleted outright; no aliases left behind.
- **R3** — retitled `alphalab.oms.order` as the canonical lifecycle order and
  removed its dormant adapter hooks; deleted `alphalab.core.order`. The order the
  execution pipeline produces is byte-for-byte identical.
- **R4** — `Fill.filled_at` / `Trade.executed_at` changed from `datetime` to
  `float`. `execution_adapters.canonical_execution_from_report` now passes
  `report.timestamp` straight through instead of
  `datetime.fromtimestamp(..., tz=UTC)`.

---

## Fixed

- **D1 (critical) — portfolio close/reduce cash accounting.**
  `alphalab.portfolio.engine.PortfolioEngine.apply_fill` computed
  `cash_impact = -(quantity * price) - commission + pnl`. On a closing or
  reducing fill the `+ pnl` term double-counted realized P&L into cash —
  overstating cash / NAV / `PerformanceReport.ending_capital` /
  `RiskState.current_nav` on wins and understating them on losses. The `+ pnl`
  term is removed; position cost-basis math and the
  `PositionReduced` / `PositionClosed` event payloads are unchanged. Guarded by
  new unit tests (winning, losing, and partial-reduction round-trips) and
  `tests/regression/test_close_fill_cash_accounting.py`, which drives the real
  `ExecutionPipeline` open → hold → close and asserts
  `NAV == ending_capital == risk.current_nav == starting_cash + realized_pnl - commissions`.
- **D2 (medium) — `PerformanceReport` serialization.**
  `alphalab.analytics.attribution.calculate_attribution` wrapped
  `AttributionMetrics` fields in `types.MappingProxyType`, which
  `alphalab.persistence` (via `dataclasses.asdict`) cannot copy
  (`cannot pickle 'mappingproxy' object`). It now returns ordinary `dict`s, like
  every other frozen dataclass in AlphaLab. Field names and the
  `Mapping[str, Decimal]` annotations are unchanged.

---

## Known gaps (unchanged in 2.0.0)

- `alphalab.runtime.execution_pipeline._trade_record` still attributes realized
  P&L by scanning portfolio events in reverse and hard-codes
  `sector_id="UNCLASSIFIED"` and `holding_period_seconds=0.0` (deferred — "D3").
- Mark-to-market position repricing is not implemented.
- `alphalab.replay` is a standalone engine; it does not drive `ExecutionPipeline`.
- `alphalab.data.feed.Bar` and `alphalab.market.bar.Bar` are separate,
  incompatible models (a third `Bar` lives in `alphalab.marketdata.feed`).
- The `broker` / `brokers`, `marketdata` / `data` / `feed`, `kernel`, and
  `core/events` areas remain intentionally unresolved / product-surface
  decisions.

---

## Quality gates

- ruff check, ruff format --check, mypy --strict (833 source files) — clean
- pytest — 1221 passed (1207 unit, 4 integration, 10 regression)
- `git diff --check` — clean

---

# [1.0.0] - 2026-07-05

## First Stable Release

AlphaLab 1.0.0 is the first stable public release of the framework.

This release establishes the core architecture for deterministic quantitative research, systematic strategy development, portfolio optimization, historical replay, production runtime management, and institutional research workflows.

---

## Added

### Core Framework

- Immutable domain models
- Deterministic engine APIs
- Event-driven architecture
- Shared validation utilities
- Shared registry utilities
- Common infrastructure package
- Python 3.12 support
- Strict static typing throughout the framework

### Research

- Research Engine
- Statistical research workflows
- Strategy evaluation
- Research payload validation

### Strategy Runtime

- Strategy lifecycle management
- Event dispatch
- Runtime supervision
- Context abstraction
- Intent validation

### Universal Data Engine

- Canonical datasets
- Dataset metadata
- Schema validation
- Data quality reporting
- Timeframe conversion
- Dataset cataloguing

### Replay Engine

- Historical event replay
- Deterministic market simulation
- Timeline reconstruction

### Portfolio Optimizer

- Capital allocation
- Equal Weight optimization
- Minimum Variance optimization
- Maximum Sharpe optimization
- Inverse Volatility optimization
- Portfolio constraints
- Exposure analysis
- Transaction cost estimation
- Portfolio rebalancing

### Broker Integrations

- Provider abstraction
- Paper Trading
- Alpaca integration architecture
- Interactive Brokers integration architecture
- Zerodha integration architecture
- Authentication workflows
- Connection management

### Production Runtime

- Runtime supervision
- Health monitoring
- Checkpointing
- Recovery workflows
- Runtime metrics

### Strategy Studio

- Project management
- Strategy registration
- Research sessions
- Pipelines
- Reports
- Workspace management
- Backtest orchestration

### AlphaLab Workbench

- Unified workspace
- Project management
- Research orchestration
- Dataset management
- Dashboard infrastructure

---

## Documentation

Added comprehensive documentation including:

- README
- Getting Started Guide
- Architecture Guide
- System Design
- Engineering Guidelines
- Architectural Decision Records (ADRs)
- Examples documentation
- Contributing Guide

---

## Examples

Added ten fully synchronized runnable examples covering:

1. Research Engine
2. Strategy Runtime
3. Replay Engine
4. Market Data
5. Broker Integrations
6. Portfolio Optimizer
7. Universal Data Engine
8. Strategy Studio
9. Workbench
10. Complete end-to-end workflow

---

## Engineering

Improved overall project quality through:

- Shared validation infrastructure
- Shared registry utilities
- Common package refactoring
- Consistent immutable APIs
- Packaging improvements
- Version alignment
- Example synchronization
- Release engineering

---

## Quality Assurance

Validated with:

- ✅ 583 passing unit tests
- ✅ Strict MyPy (631 source files)
- ✅ Ruff clean
- ✅ Python package build
- ✅ Wheel validation
- ✅ Source distribution validation
- ✅ Twine package verification

---

## Packaging

AlphaLab 1.0.0 is distributed as:

- Source Distribution (`sdist`)
- Universal Python Wheel (`py3-none-any`)

---

## Notes

This release establishes the stable architectural foundation of AlphaLab.

Future releases will expand the framework with additional quantitative research capabilities while maintaining backward compatibility wherever practical.