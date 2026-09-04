# Changelog

All notable changes to AlphaLab are documented in this file.

This project follows the principles of
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and adheres to Semantic Versioning.

---

# [2.1.0] - 2026-09-04

## Overview

AlphaLab 2.1.0 is "Execution + Portfolio Correctness". It makes the existing
execution spine correct and fast rather than adding new packages: mark-to-market,
a single monetary precision policy with an exact accounting identity, explicit
execution invariants, correct persistence of engine histories, and the fix for
the O(N^2) event accumulation that stopped the risk benchmark from completing.

No new packages. No new domain models. `PortfolioState` remains the single
canonical portfolio state and `oms.order.Order` the single lifecycle order.

---

## Breaking Changes

Public symbols or behaviour changed. Import sites and callers may need updating.

- **Engine histories are `AppendOnlyLog`, not `tuple`.** `RiskState`,
  `MarketState`, `ExecutionState`, `OMSState`, `AllocationState`,
  `PortfolioState`, `TransactionLedger` and the `ExecutionPipelineState`
  accumulators now hold `alphalab.common.AppendOnlyLog`. It is an immutable
  `Sequence` and compares equal to tuples and lists, so `len()`, indexing,
  slicing, iteration, `in`, `reversed()` and `== (...)` are unchanged. Code that
  required a literal `tuple` (`isinstance(..., tuple)`, concatenation with `+`)
  must call `.to_tuple()`.
- **`PortfolioEngine.apply_fill` rejects malformed fills** with
  `InvalidTransactionError`: non-positive price, negative commission, and a
  quantity that is zero or rounds to zero at `SHARE_QUANT`. These previously
  produced an incoherent position, a fabricated `PositionClosed` event, or a
  ledger entry for a trade that did not happen.
- **Every non-trading execution outcome is terminal for the order.** A
  rejected, expired or unfilled execution moves the OMS order to `REJECTED` /
  `EXPIRED` / `CANCELLED` and out of `active_orders`; previously it stayed
  `ACCEPTED` and open forever. `Order.reject` accordingly accepts `ACCEPTED` in
  addition to `NEW` / `PENDING`; an order that has already traded still cannot
  be rejected.
- **One portfolio snapshot per market event**, not one per fill.
  `ExecutionPipelineState.portfolio_snapshots` now also has a point for events
  that only marked the book and did not trade.
- **`DeterministicEncoder` no longer stringifies unknown objects.** `Decimal`,
  dataclasses, `AppendOnlyLog`, `Enum` and `UUID` are handled by explicit
  branch; anything else raises `SerializationError` naming the type. Callers
  that relied on the previous silent `str()` fallback were receiving payloads
  that could not be read back.
- **`AnalyticsEngine.compile_report`, `AllocationEngine.allocate` and
  `calculate_attribution`** accept `Sequence` where they previously required
  `tuple`. Existing tuple callers are unaffected.

---

## Added

- **Mark-to-market.** `PortfolioEngine.update_market_prices` is wired into
  `ExecutionPipeline.process_market_event` and runs *before* any decision is
  taken on the event, so unrealized P&L and NAV reflect the current market and
  the risk state is resynced from the marked book. It moves unrealized P&L only
  -- cash, realized P&L, commissions and the ledger are untouched. Non-positive
  prices are rejected as invalid market data and unheld assets ignored; a
  position with no price keeps its previous mark. Emits `MarketValueUpdated`
  when something was actually re-marked.

  The marked portfolio reaches **risk** only. The strategy's `StrategyContext`
  comes from the caller's `context_factory`, which the pipeline does not
  populate, and allocation sizes from market prices and its capital budget.
- **`PortfolioValuation.snapshot` / `PortfolioValuationSnapshot`** -- the
  deterministic read model over `PortfolioState`: cash, long/short/positions
  value, unrealized and realized P&L, commissions, and equity. Carried on
  `ExecutionPipelineResult.valuation` and projected into the analytics
  `PortfolioSnapshot`. Not a second portfolio state.
- **`PortfolioState.realized_pnl` and `PortfolioState.commission_paid`** --
  cumulative account totals that survive a position being closed and dropped
  from `positions`.
- **`alphalab.portfolio.money`** -- the portfolio's single monetary precision
  policy (see *Monetary precision* below), and **`Position.cost_basis`**, the
  authoritative money figure it rests on.
- **`ExecutionPipelineResult.unpriced_requests`** -- order requests dropped
  because the pipeline had no market price for the asset.
- **`alphalab.common.AppendOnlyLog`** -- immutable append-only sequence with
  O(1) amortized append and copy-on-branch structural sharing.
- **`benchmarks/benchmark_execution_pipeline.py`** -- end-to-end pipeline
  throughput and scaling benchmark.
- Tests: `tests/unit/common/test_append_log.py`,
  `tests/unit/portfolio/test_portfolio_invariants.py`,
  `tests/unit/portfolio/test_monetary_precision.py`,
  `tests/integration/test_mark_to_market_pipeline.py`,
  `tests/regression/test_event_accumulation_complexity.py`,
  `tests/regression/test_state_serialization.py`.

---

## Monetary precision

`alphalab.portfolio.money` holds one rounding policy for the whole portfolio:

1. **Money is exact at the currency minor unit.** Every monetary amount stored
   in `PortfolioState` -- cash, cost basis, realized P&L, commissions, market
   value -- is an exact multiple of `0.01`. `to_money` is the only place
   rounding happens.
2. **Rounding happens once, at entry.** `PortfolioEngine.apply_fill` rounds the
   fill's notional and commission as they enter, and both the cash movement and
   the position's cost basis are derived from those same rounded values.
3. **Prices and quantities are inputs, not money.** They keep their own finer
   precision (`PRICE_QUANT` 1e-4, `SHARE_QUANT` 1e-6).

`Position.cost_basis` is the authoritative money figure -- the exact cash paid
(long) or received (short) for the open quantity. Realized P&L is the difference
between money in and money out; unrealized P&L is `market_value - basis`.
Splits (partial close, reversal) round one part and derive the other by
subtraction, so the parts always sum to the exact whole. `average_cost` is
derived from the basis and keeps its meaning; a `Position` constructed without a
`cost_basis` derives one from `average_cost * |quantity|`.

The accounting identity is therefore **exact** -- an identity over exact Decimal
values for any price and quantity the engine accepts, not an approximation that
happens to hold for round numbers:

```
equity == deposits - withdrawals + realized_pnl + unrealized_pnl - commission_paid
```

---

## Fixed

- **O(N^2) event/history accumulation.** Every engine grew its append-only
  history with `(*state.events, event)`, rebuilding the whole tuple on each
  transition; N transitions copied O(N^2) elements. Measured on the development
  machine, with full history retained in every case:

  | Benchmark | v2.0.0 | v2.1.0 |
  | --- | --- | --- |
  | `benchmark_risk_engine` (100k evaluations) | 285.7s | **1.4s** |
  | `benchmarks_market_engine` (100k quotes) | 2,060 ops/sec | **~167,000 ops/sec** |
  | `benchmarks_market_engine` (100k books) | 640 ops/sec | **~165,000 ops/sec** |
  | `benchmark_portfolio_engine` (20k fills) | 1.90s | **0.22s** |
  | `benchmark_execution_pipeline` (4000 events) | 6.76s | **~1.8s** |

  `benchmark_risk_engine` was 9.5x outside its own 30s budget on v2.0.0.
  `benchmark_portfolio_engine`'s full 100k-fill workload could not complete on
  v2.0.0 at all; it now runs in ~1.9s. End-to-end, the pipeline benchmark's
  scaling across a 4x workload dropped from ~17x to ~7.5x.
- **The accounting identity was not exact.** The cash ledger rounded
  `quantity * price + commission` while the position independently rounded
  `(exit_price - average_cost) * quantity`. Two roundings of one economic event
  disagreed by up to half a cent each and the error accumulated: an ordinary
  penny-spread quote (bid 100.00 / ask 100.01, mid 100.005) put the identity out
  by $0.01, and randomized multi-asset portfolios drifted by up to $0.05. See
  *Monetary precision* above.
- **Append-only histories serialized as a repr string.** `dataclasses.asdict`
  recurses into tuples but deep-copies anything else, so an `AppendOnlyLog`
  reached `DeterministicEncoder`, whose `str()` fallback wrote
  `"events": "AppendOnlyLog([...])"` instead of a JSON array. It raised nothing
  and passed snapshot validation, so `PersistenceAdapter.to_snapshot` of a
  migrated state silently persisted unreadable history.
  `alphalab.common.dataclass_to_dict` now does its own recursion -- `asdict`'s
  behaviour plus one rule: an `AppendOnlyLog` converts like the tuple it
  replaced.
- **Realized P&L was discarded when a position closed.** Closing removes the
  position from `positions`, taking its `realized_pnl` with it, so account-level
  realized P&L was unrecoverable after a round trip. It now accumulates on
  `PortfolioState`.
- **Analytics trade records could be credited with another fill's P&L.**
  `ExecutionPipeline._trade_record` scanned the whole portfolio history in
  reverse for the asset's last realized-P&L event, so an opening fill inherited
  an earlier close's P&L and the performance report overstated realized P&L on
  every re-entry. It now reads only the events the current fill produced.
- **An order for an asset with no market price raised `KeyError`.** Allocation
  prices unknown assets at `0.00`; the pipeline now drops such requests before
  the OMS and reports them on `unpriced_requests`. The condition is per-event --
  a later quote makes the asset tradeable.
- **`FillStatus.NO_FILL` left the order open.** It now moves the order to
  `CANCELLED`, removes it from `active_orders`, and releases the allocation
  reservation exactly once -- fabricating no fill, trade or position.
- **Venue-rejected orders stayed open forever** in `oms.active_orders`, so open
  orders never reconciled with fills.
- **Positions were never repriced between fills**, so unrealized P&L, NAV, risk
  NAV and the equity curve were stale until the next trade.
- **`benchmarks/benchmarks_market_engine.py` could never run.** Its first quote
  carried timestamp `0.0`, which `market.timestamp.is_valid_timestamp` rejects
  as not strictly positive, so the benchmark raised `MarketValidationError`
  immediately. The sequence now starts at `1.0`. (Pre-existing on v2.0.0.)

---

## Not Changed

- The D1 close-fill cash accounting fix from 2.0.0 stands: realized P&L is still
  never added to cash on top of the trade proceeds.
- Commissions still stay out of a position's cost basis; `average_cost` remains
  a clean per-unit price.
- No package was added, removed, or merged. The standalone-engine /
  integrated-path split from ADR-0009 is unchanged.

---

## Known Limitations

- **The OMS order book is the execution path's remaining super-linear term.**
  `OrderBook.add` / `.replace` copy the whole order dict and
  `OMSEngine._update_sets` copies both order-id frozensets, once per stored
  order. This is a persistent-map problem, not event accumulation, and was
  deliberately left out of scope. `benchmarks/benchmark_execution_pipeline.py`
  measures it.
- **`OMSState` cannot be JSON-serialized as a whole state**, on 2.1.0 exactly as
  on 2.0.0: `OrderBook` keys orders by the `OrderId` dataclass, which neither
  `asdict` nor `json.dumps` accepts as a mapping key. Its history logs serialize
  correctly; the limitation is the typed identifier, not the log.
- **A risk-rejected request does not release its allocation reservation**, so
  `notional_allocated` over-reports after a risk rejection. Pre-existing; it
  does not gate trading, because the budget check reads
  `available_global_capital`.
- `dataclasses.asdict` cannot be extended, so it still returns an
  `AppendOnlyLog` for a history field. `alphalab.common.dataclass_to_dict` and
  `alphalab.persistence.serialize` are the supported serialization boundary.
- **Valuation is single-currency.** `PortfolioValuation` and `NAVCalculator`
  value the base currency only.
- **`_trade_record` still hard-codes** `sector_id="UNCLASSIFIED"` and
  `holding_period_seconds=0.0` ("D3", deferred).

---

## Quality Gates

`ruff check`, `ruff format --check`, `mypy` (strict, 843 source files), `pytest`
(1429 tests), `git diff --check`, `python -m build`, and `twine check dist/*`
all pass.

---

# [2.0.0] - 2026-09-04

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

## Build / Packaging

- The release build and distribution validation were aligned with **Core
  Metadata 2.5 / Twine 7**. Hatchling 1.30 emits `Metadata-Version: 2.5`
  (PEP 639), which Twine 6 rejects; the dev toolchain pin is now
  `twine>=7.0,<8`, and the redundant unpinned `pip install build twine` step
  was dropped from the CI and release workflows so they use the pinned
  toolchain. `python -m build` and `twine check dist/*` pass for the 2.0.0
  wheel and sdist. Hatchling is unchanged; no application dependency changed
  (`dependencies = []`).

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