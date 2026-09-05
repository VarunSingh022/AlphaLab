# AlphaLab Architecture

## Overview

AlphaLab is an institutional-grade quantitative research and algorithmic trading platform built around deterministic execution, immutable state, and event-driven architecture.

Every subsystem follows the same engineering principles (immutable state, pure functional engines, deterministic execution). They are designed to compose through well-defined interfaces, but only `alphalab.runtime.ExecutionPipeline` and the `alphalab.backtesting` package that drives it actually wire a group of them together — see the **Implementation Status (v2.2)** section below.

The architecture emphasizes reproducibility, composability, testability, and production readiness.

Every component—from market data ingestion to production deployment—is designed to operate deterministically, enabling researchers to reproduce results across development, testing, and live environments.

---

# Implementation Status (v2.2)

Most of this document describes the **target** architecture. This section states
what is actually built as of v2.2 so the two are not confused.

## AlphaLab is a library

There is no server, daemon, scheduler process, event bus, or CLI. Every package
exposes pure functions / stateless engine classes that take an immutable state
value and return a new one. The caller owns the process and the event loop.

## The integrated path: `alphalab.runtime.ExecutionPipeline`

`ExecutionPipeline` is where the domain engines are wired together. It threads a
single immutable `ExecutionPipelineState` through, one market event at a time:

```
market event (Quote / Bar / Tick)
   → market price update                 → market_prices
   → PortfolioEngine.update_market_prices→ positions marked, unrealized P&L      (v2.1)
   → risk resync from the marked book    → NAV / exposure / margin               (v2.1)
   → StrategyEngine.process_event        → Intents
   → AllocationEngine.allocate           → core.OrderRequest[] + reservations    (v2.2)
   → (drop requests with no market price)→ result.unpriced_requests              (v2.1)
   → RiskEngine.evaluate                 → RiskDecision (rejection releases)     (v2.2)
   → OMSEngine.submit/accept             → oms.order.Order        (canonical)
   → FillPolicy.decide                   → FillDecision           (v2.2)
   → ExecutionEngine.simulate            → ExecutionReport        (deterministic fills)
   → PortfolioEngine.apply_fill          → cash / positions / realized P&L
   → PortfolioValuation.snapshot         → one snapshot per market event         (v2.1)
   → AnalyticsEngine.compile_report      → PerformanceReport      (on demand)
```

Packages on this path: `core`, `runtime`, `strategy`, `allocation`, `risk`,
`oms`, `execution`, `portfolio`, `analytics`, `market`.

## Backtesting and replay: `alphalab.backtesting` (v2.2)

`alphalab.backtesting` is the second integration package, and the only other one.
It adds no engine and no domain model: it turns a dataset into a run by calling
`ExecutionPipeline`.

```
MarketDataset (MarketRecord: Quote | Bar | Tick + event_id + timestamp)
   → MarketEngine.publish_quote / publish_bar / publish_tick
   → ExecutionPipeline.process_market_event      ← the path above, unchanged
   → BacktestStep recorded per record
   → AnalyticsEngine.compile_report              (on finalize)
```

`backtesting.engine.advance` is the canonical step, and there are two drivers
for it:

| Driver | Cursor | Everything else |
| --- | --- | --- |
| `BacktestEngine.run` | iterates `dataset.records` | `advance` |
| `ReplayBacktest.run` | `ReplayEngine.step_one_event` | `advance` |

Because both call the same function, backtest/replay parity is structural rather
than a coincidence the tests happen to observe. `MarketRecord` satisfies
`replay.HistoricalEventProtocol`, so one dataset type feeds both.

**Replay is now on the execution path.** `alphalab.replay` still owns exactly
what it owned before — the cursor, the replay clock, the session lifecycle and
chronological validation — and `alphalab.backtesting.replay` is what connects it
to execution. ADR-0009 listed `replay` as standalone; ADR-0010 supersedes that
for `replay` only.

### Execution semantics: fill policies (v2.2)

A `FillPolicy` decides what the venue does with one order at one market event.
It reads a `LiquidityContext` — asset, side, requested quantity, event price,
and the size the event showed — and returns a `FillDecision`:

| Policy | Behaviour |
| --- | --- |
| `ImmediateFill` | fills the whole request; liquidity assumed unlimited (default) |
| `StaticFill(status, quantity)` | always the same outcome; expresses the pre-v2.2 fixed `fill_status` argument |
| `LiquidityCappedFill(participation_rate)` | fills up to a share of the size the event showed; partial when capped, no fill when the event showed none |

Available size comes from the market event: a quote's `ask_size` / `bid_size` on
the side being crossed, a bar's `volume`, a tick's `quantity`. An event carrying
no size cannot be capped and fills in full.

Policies depend only on `alphalab.core` and hold no state, so the same policy
produces the same decisions in a backtest and in a replay.

### Determinism and the identifier source (v2.2)

Quantities on this path were always deterministic. Identifiers were not: every
event id, execution id, order id and transaction id came from `uuid4`, so two
runs of one workload agreed on every number and disagreed on every identity.

`alphalab.common.ids` now routes all of them through one `new_id()`, and
`use_id_source(source)` scopes where that source is for the duration of a block.
`BacktestConfig.seed` installs a `DeterministicIdSource` for the run and is
recorded on the result. With a seed, repeated runs are identical field for
field — orders, fills, positions, cash, realized and unrealized P&L, analytics.
Without one, identifiers stay on `uuid4` and only the economics reproduce.

The source is an ambient `ContextVar`, deliberately: threading an id-source
parameter through every engine method would put a reproducibility argument on
APIs that have nothing to do with reproducibility. The scope is explicit,
nests, and is restored on exit.

The replay cursor mints its own lifecycle event ids from a *separate* stream
(`seed + REPLAY_CURSOR_SEED_OFFSET`). Drawing them from the execution path's
stream would shift the identity of every order and fill in a replay, and parity
would fail for a reason unrelated to execution.

## Standalone engine libraries

Everything else is an independent, deterministic, individually tested library
that is **not** wired into `ExecutionPipeline`, `backtesting`, or a shared
runtime: `research`, `portfolio_optimizer`, `optimizer`, `reporting`,
`feature_store`, `factor_library`, `alt_data`, `ml`, `deep_learning`,
`reinforcement_learning`, `options`, `futures`, `crypto`, `macro`,
`cloud_research`, `cluster_scheduler`, `distributed`, `experiment_tracking`,
`model_registry`, `deployment_manager`, `research_assistant`, `studio`,
`workbench`, `enterprise`, `live`, `production`, `broker`, `brokers`,
`integrations`, `data`, `marketdata`, `feed`, `kernel`, `plugins`, `scheduler`,
`persistence`.

## Canonical domain models (v2.0.0, R1–R4)

- `alphalab.core.enums.Side` — the one order-direction enum.
- `alphalab.core.OrderRequest` — the one proposed-order DTO (allocation → risk).
- `alphalab.oms.order.Order` — the one lifecycle order (OMS + pipeline).
- `alphalab.core.Fill.filled_at` / `alphalab.core.Trade.executed_at` — `float`
  Unix seconds, like every other timestamp on the path.

See ADR-0008.

## Portfolio accounting and mark-to-market (v2.1)

`PortfolioState` keeps four quantities strictly separated, and the portfolio
accounting identity ties them together:

```
equity == deposits - withdrawals + realized_pnl + unrealized_pnl - commission_paid
```

| Quantity | Where it lives | Moved by |
| --- | --- | --- |
| `cash` | `PortfolioState.cash` (`CashLedger`) | deposits, withdrawals, trade proceeds/cost, commissions |
| `realized_pnl` | `PortfolioState.realized_pnl` (cumulative) | reducing or closing a position |
| `commission_paid` | `PortfolioState.commission_paid` (cumulative) | every fill, exactly once |
| unrealized P&L | derived, never stored | marking positions to market |

- **Realized P&L is not a cash movement.** It is already implicit in the entry
  cost and the exit proceeds, each applied on its own fill. Adding it to cash a
  second time was the D1 bug fixed in v2.0.0; `realized_pnl` is an accounting
  total alongside cash, never added into it.
- **`realized_pnl` and `commission_paid` are account-level and cumulative**, so
  they survive a position going flat and being dropped from `positions`. Before
  v2.1, closing a position discarded its realized P&L along with the position.
- **Commissions never enter a position's cost basis.** `average_cost` stays a
  clean price; commissions are expensed to cash at fill time.
- **`PortfolioEngine.apply_fill` rejects malformed fills** (zero quantity,
  non-positive price, negative commission) with `InvalidTransactionError` rather
  than producing an incoherent position or ledger entry.
- **Each fill produces exactly one** position update, cash movement, ledger
  transaction and portfolio event, so a fill cannot be applied twice.

### Mark-to-market

`PortfolioEngine.update_market_prices(state, prices, timestamp)` is the
mark-to-market step. It re-prices held positions and touches nothing else --
cash, realized P&L, commissions and the ledger are all left alone, so the only
thing a mark moves is unrealized P&L. Prices for assets that are not held are
ignored, non-positive prices are rejected as invalid market data, and a position
with no price in `prices` keeps its previous mark. A `MarketValueUpdated` event
is emitted only when at least one position was actually re-marked.

`ExecutionPipeline.process_market_event` marks **before** any decision is taken
on the event, and resyncs the risk state from the marked book, so **risk**
evaluates against a portfolio valued at the current market. Marks are applied at
the event's mid price (quote), close (bar) or trade price (tick).

Two honest limits on that reach:

- **The strategy does not see the marked portfolio.** `StrategyEngine.process_event`
  builds each strategy's `StrategyContext` from the caller-supplied
  `context_factory`; the pipeline does not populate it. A strategy that wants
  portfolio state must obtain it through its own context factory.
- **Allocation does not see the portfolio at all.** `AllocationEngine.allocate`
  sizes from market prices and its `CapitalBudget`, neither of which the mark
  changes.

`PortfolioValuation.snapshot(state, timestamp, currency)` is the read model:
cash, long/short/positions value, unrealized and realized P&L, commissions and
equity, computed deterministically from the state. It is what
`ExecutionPipelineResult.valuation` carries and what the analytics
`PortfolioSnapshot` is projected from. Long and short positions are handled by
sign: a short's `market_value` is negative, and its unrealized P&L is
`(average_cost - market_price) * abs(quantity)`.

## Execution guarantees (v2.1, extended in v2.2)

- **One portfolio snapshot per market event**, recorded after every fill that
  event produced (plus one at funding time). v2.0.0 recorded a snapshot per fill
  and none for events that did not trade, so the equity curve had no points
  where the portfolio was only marked.
- **A request for an asset with no known market price never reaches the OMS.**
  Allocation prices unknown assets at `0.00`; the pipeline drops such requests
  before risk and reports them on `ExecutionPipelineResult.unpriced_requests`.
  Previously the order was submitted and the execution leg raised `KeyError`.
  The condition is per-event: a later quote for the asset makes it tradeable.
- **Every non-trading execution outcome is terminal for the order.** An
  execution that produces no report moves its OMS order to `REJECTED`
  (venue rejection), `EXPIRED` (timeout) or `CANCELLED` (`NO_FILL`); the order
  leaves `active_orders`, and the reserved allocation notional is released
  exactly once. Before v2.1 the order stayed `ACCEPTED` and open forever, so
  open orders never reconciled with fills. `Order.reject` accepts `ACCEPTED` as
  well as `NEW` / `PENDING` for this reason; an order that has already traded
  still cannot be rejected.
  A **risk**-rejected request never reaches the OMS at all; as of v2.2 its
  allocation reservation is released at that point too (see below).
- **Analytics trade records attribute realized P&L to the fill that produced
  it.** `_trade_record` reads only the portfolio events of the current fill;
  v2.0.0 scanned the whole portfolio history in reverse and could credit an
  opening fill with an earlier close's P&L. (`sector_id="UNCLASSIFIED"` and
  `holding_period_seconds=0.0` are still hard-coded — "D3", deferred.)

## Allocation reservation lifecycle (v2.2)

`AllocationEngine.allocate` commits capital against every request it emits.
Until v2.2 that commitment was a single running total, `notional_allocated`:
anything could subtract from it, nothing could say which order the capital
belonged to, and a release that never happened was indistinguishable from one
that happened twice. A risk-rejected request was skipped with a bare `continue`
and a request with no market price was skipped earlier still, so neither
released anything and the total over-reported for the rest of the run.

`AllocationState.reservations` is now a per-order ledger, and
`notional_allocated` is its total.

| Event | Ledger |
| --- | --- |
| allocation emits a request | reserve `quantity * price` under the request's order id |
| a fill executes | consume up to the executed notional; drop the entry when exhausted |
| a partial fill | consume what executed, leave the residual reserved — the order is still working |
| risk rejects, or no market price | release the whole reservation |
| the venue rejects / expires / does not fill | release the whole reservation |

Ownership is split deliberately: the **allocation engine owns the amount**
(`release_reservation` takes no amount — it frees whatever the ledger holds, so
a release can neither free more than was reserved nor free it twice) and the
**pipeline owns the moment** (it is what knows a request's lifecycle has
ended). Releasing an order that holds no live reservation raises
`UnknownReservationError` rather than silently subtracting, which is what makes
"exactly once" a checkable property rather than an assertion.

`AllocationEngine.release_reservation(state, order_id, timestamp)` is a breaking
signature change: it previously took the amount to release.

## Monetary precision (v2.1)

`alphalab.portfolio.money` holds the portfolio's one and only rounding policy:

1. **Money is exact at the currency minor unit.** Every monetary amount stored
   in `PortfolioState` -- cash, cost basis, realized P&L, commissions, market
   value -- is an exact multiple of `0.01`. `to_money` is the only place
   rounding happens.
2. **Rounding happens once, at entry.** `PortfolioEngine.apply_fill` rounds the
   fill's notional and commission as they enter; the cash movement *and* the
   position's cost basis are then derived from those same rounded values.
3. **Prices and quantities are inputs, not money.** They keep their own finer
   precision (`PRICE_QUANT` 1e-4, `SHARE_QUANT` 1e-6) and become money only when
   multiplied into an amount.

`Position.cost_basis` is the authoritative money figure -- the exact cash paid
(long) or received (short) for the open quantity. Realized P&L is the difference
between the money that moved in and the money that moved out; unrealized P&L is
`market_value - basis`. `average_cost` is derived from the basis and remains the
reported per-unit cost. When a split is needed (a partial close, or a reversal
that both closes and opens), one part is rounded and the other is obtained by
*subtraction*, so the parts always sum to the exact whole.

Because of this, the accounting identity is **exact** -- an identity over exact
Decimal values, for any price and quantity the engine accepts, not an
approximation that happens to hold for round numbers:

```
equity == deposits - withdrawals + realized_pnl + unrealized_pnl - commission_paid
```

Before this policy, the cash ledger rounded `quantity * price + commission` while
the position independently rounded `(exit_price - average_cost) * quantity`. Two
roundings of one economic event disagreed by up to half a cent each and the error
accumulated: an ordinary penny-spread quote (bid 100.00 / ask 100.01, mid
100.005) put the identity out by a cent, and randomized multi-asset portfolios
drifted by up to five.

## Serialization of append-only histories (v2.1)

`dataclasses.asdict` recurses into tuples but deep-copies anything it does not
recognise, so an `AppendOnlyLog` reached the JSON encoder intact and a `str()`
fallback persisted it as `"AppendOnlyLog([...])"` -- silently, and passing
snapshot validation. Two changes fix this at the boundary:

- `alphalab.common.dataclass_to_dict` does its own recursion (`asdict`'s
  behaviour plus one rule: an `AppendOnlyLog` converts like the tuple it
  replaced), so histories serialize as sequences of objects.
- `DeterministicEncoder` handles `Decimal`, dataclasses, `AppendOnlyLog`, `Enum`
  and `UUID` by explicit branch and **raises `SerializationError` for anything
  else** instead of coercing it with `str()`. A silent stringify produces a
  plausible-looking payload that cannot be read back, which is how the defect
  went unnoticed.

## OMS state snapshots (v2.2)

`OMSState` could not be JSON-serialized as a whole state on v2.0.0 or v2.1:
`OrderBook` indexes orders by `OrderId`, and neither `asdict` nor `json.dumps`
accepts a dataclass as a mapping key. Its history logs serialized correctly —
the limitation was the typed identifier, not the log — but replay and
persistence need complete snapshots, not partial ones.

v2.2 fixes it without weakening the identifier. `OrderId` stays a dataclass in
memory; the state declares an explicit serializable projection
(`alphalab.oms.snapshot`):

- **orders serialize as an array**, in submission order, each carrying its own
  `OrderId` as a *value* (which encodes fine) rather than as a key;
- the book's asset and strategy indices are **omitted** — they are derived from
  the order array and rebuilt exactly by `restore`;
- `active_orders` / `completed_orders` serialize as arrays of `OrderId` in
  insertion order;
- **every event carries an `event_type` tag**, without which a heterogenous
  event log cannot be read back into typed events.

`capture(state)` and `restore(snapshot)` are inverses in memory;
`restore(from_primitives(deserialize(payload))) == state` across JSON, event log
included, and the restored state is a working state the engine carries on from.

The mechanism is a general one: `dataclass_to_dict` now honours a
`__serializable__()` projection, which is how a type whose in-memory shape has
no JSON form declares one. Anything *without* such a projection still reaches
the encoder unchanged and is still rejected there rather than stringified — a
raw `{OrderId: ...}` mapping raises exactly as before.

## Append-only histories and complexity (v2.1)

Engine histories (`state.events`, `state.history`, the transaction ledger and the
pipeline's fill/trade/snapshot accumulators) are
`alphalab.common.AppendOnlyLog`, not `tuple`. They are still immutable sequences
with value semantics; the difference is that appending is O(1) amortized instead
of rebuilding the whole tuple, so a run of N transitions costs O(N) rather than
O(N^2). Full history is retained — nothing is dropped to gain the speed.

Converted: `risk`, `market`, `execution`, `oms`, `allocation`, `portfolio` and
`ExecutionPipelineState`. `strategy` and `analytics` histories grow per lifecycle
transition or per compiled report, not per market event, and were left as tuples.

Measured on the development machine, full history retained in every case:

| Benchmark | v2.0.0 | v2.1 |
| --- | --- | --- |
| `benchmark_risk_engine` (100k evaluations) | 285.7s | 1.4s |
| `benchmarks_market_engine` (100k quotes) | 2,060 ops/sec | 163,816 ops/sec |
| `benchmarks_market_engine` (100k books) | 640 ops/sec | 165,970 ops/sec |
| `benchmark_portfolio_engine` (20k fills) | 1.90s | 0.22s |

`benchmark_risk_engine` was 9.5x outside its own 30s budget on v2.0.0, and
`benchmark_portfolio_engine`'s full 100k-fill workload could not complete at all.
The cost is now linear in the number of transitions rather than quadratic.

## Persistent containers and complexity (v2.2)

v2.1 made engine *histories* O(1) amortized to append, which left the execution
path's remaining super-linear term exposed: the OMS order book.
`OrderBook.add` rebuilt the whole order `dict` and both index `frozenset`s,
`OrderBook.replace` rebuilt the order `dict`, and `OMSEngine._update_sets`
rebuilt both order-id `frozenset`s — once per stored order, and the OMS stores
an order on submit and again on every lifecycle transition. Submitting N orders
copied O(N²) entries.

`alphalab.common.PersistentMap` and `PersistentSet` replace them. The idiom is
the one `AppendOnlyLog` established, generalised from "append to a sequence" to
"write to a key": a map is a *view* over shared append-only storage, identified
by `(store, version, size)`. The store keeps, per key, the chain of
`(version, value)` writes to it plus the order keys were first inserted in. A
view at version `v` reads a key by finding the newest chain entry at or before
`v`, so a later write is invisible to it and **older states keep observing
exactly what they observed before**. Writing to the newest view appends one
chain entry (O(1) amortized); writing to an older view copies — "copy on
branch" — which linear engine histories never do.

Two properties come free and are relied on: iteration is in first-insertion
order, so a state holding one serializes deterministically (`frozenset`
iterated in hash order), and `orders()` returns orders in submission order.

**The same defect existed twice.** Running the whole benchmark suite after the
order-book fix showed `benchmarks_execution.py` taking 85s for 100k fills:
`ExecutionEngine.execute` and `partial_fill` stored a report by rebuilding the
whole `ExecutionState.reports` dict, so N fills copied O(N²) entries — on the
same execution path, and paid by every fill a backtest produces.
`ExecutionState.reports` is a `PersistentMap` for the same reason the order book
is, and is still an immutable `Mapping` keyed by execution id that serializes as
the JSON object it always did.

States using the persistent containers: `OMSState.orders` (the `OrderBook`'s
order index and its asset/strategy indices), `OMSState.active_orders` /
`completed_orders`, `ExecutionState.reports`, and
`AllocationState.reservations`.

Measured on the development machine, full history retained:

| Benchmark | v2.1 | v2.2 |
| --- | --- | --- |
| `benchmark_oms` (100k order lifecycles) | 26.3 min | 6.7s |
| `benchmark_oms` scaling (10k → 20k) | 4.70x | 2.06x |
| `benchmarks_execution` (100k fills) | 85.3s | 1.55s |
| `benchmark_execution_pipeline` (4000 events) | 1.79s | 1.03s |
| `benchmark_execution_pipeline` scaling (4x workload) | ~7.4x | ~4.4x |

**On the residual above 4.00x in the pipeline benchmark.** It is the cyclic
garbage collector, not the pipeline. Orders, events and states are all container
objects, so a run keeps a large live heap for the collector to walk. Measured on
one build: 1k/2k/4k events cost 0.253s/0.546s/1.372s with the collector running
(5.43x across 4x) and 0.231s/0.476s/0.993s with it paused — 2.06x and 2.08x per
doubling, i.e. linear. The pipeline benchmark leaves it on because that is what
a real run pays; `benchmark_oms` and the complexity regression test pause it
around their timed sections, because otherwise the growth ratio measures the
collector rather than the data structure and has been observed both well above
and well below linear on the same build.

## Closed in v2.2

The four limitations the v2.1.0 review listed as blocking a real backtest are
fixed, and each has a regression test pinning it:

| v2.1 limitation | v2.2 |
| --- | --- |
| OMS order book copies its whole order dict per stored order | persistent containers; linear (`tests/regression/test_oms_book_complexity.py`) |
| (found while fixing the above) execution report index copies per stored report | persistent map; linear (`tests/regression/test_execution_reports_complexity.py`) |
| Risk-rejected requests retain their allocation reservation | per-order ledger, released exactly once (`tests/regression/test_risk_reservation_leak.py`) |
| `OMSState` cannot be JSON-serialized as a whole state | explicit snapshot projection, round-trips (`tests/regression/test_oms_state_snapshot.py`) |
| Replay is not integrated with the real execution path | `alphalab.backtesting.replay`, parity tested (`tests/integration/test_backtest_replay_parity.py`) |

## Known gaps and deferred areas

- **Market-data model convergence is not done.** `data.feed.Bar` and
  `market.bar.Bar` are still separate, incompatible types (a third `Bar` exists
  in `marketdata.feed`), and `data` / `marketdata` / `feed` still overlap. A
  backtest reads `alphalab.market` inputs only. Deferred to v2.3.
- **`broker` / `brokers` overlap, and live broker connectivity is not wired
  into the execution path.** Deferred to v2.3.
- **`kernel` and `core/events` are unused by the execution path.** Deferred.
- **A strategy still does not see the marked portfolio.** `StrategyContext`
  comes from the caller's `context_factory`; neither `ExecutionPipeline` nor
  `BacktestEngine` populates it. Allocation sizes from market prices and its
  capital budget, not from the portfolio.
- **`ExecutionPipeline` mints a fresh order per market event and never re-works
  an existing one.** A partially filled order stays `PARTIALLY_FILLED` with its
  residual reserved; it is not topped up on a later event. A participation-capped
  strategy that wants to finish a large order must keep expressing the intent.
- **Multi-currency valuation is not implemented.** `PortfolioValuation` and
  `NAVCalculator` value the base currency only; FX rates would be needed
  otherwise.
- **`ExecutionPipeline._trade_record`** still hard-codes
  `sector_id="UNCLASSIFIED"` and `holding_period_seconds=0.0` ("D3", deferred).
- **An unseeded run does not reproduce its identifiers.** `BacktestConfig.seed`
  defaults to `None`, which leaves identifiers on `uuid4`; only the economics
  reproduce. This is deliberate — the default is not silently made
  deterministic — and the source of nondeterminism is visible on the config.

See ADR-0009 for the integrated-path / standalone-engine split, and ADR-0010 for
the unified backtest/replay decision that supersedes it for `replay`.

---

# Design Goals

The architecture of AlphaLab is guided by several primary objectives.

## Deterministic Execution

Given identical inputs, AlphaLab always produces identical outputs.

This property is fundamental for quantitative research, backtesting, debugging, and production validation.

---

## Immutable State

State objects are immutable.

Operations never modify existing state.

Instead, every operation produces a new state object.

Benefits include:

- Predictable behavior
- Simplified debugging
- Safe parallel execution
- Easier testing
- Complete auditability

---

## Event-Driven Design

Subsystems communicate through immutable events.

Examples include:

- Market events
- Strategy events
- Portfolio events
- Runtime events
- Broker events
- Production events

This decouples components while preserving deterministic execution.

---

## Pure Functional APIs

Public APIs avoid hidden side effects.

Functions receive explicit inputs and return explicit outputs.

Example:

```python
new_state = ResearchEngine.run(state, payload)
```

instead of

```python
ResearchEngine.run()
```

where global state is modified implicitly.

---

## Modular Composition

Each subsystem has a clearly defined responsibility.

Examples include:

- Research
- Portfolio Optimization
- Universal Data
- Replay
- Runtime
- Broker Integrations

Subsystems cooperate through stable interfaces rather than direct coupling.

---

## Production First

AlphaLab is designed with production deployment in mind.

Features such as:

- Runtime supervision
- Health monitoring
- Checkpointing
- Deterministic replay
- Broker abstraction

are considered first-class architectural components rather than optional add-ons.

---

# Architectural Principles

Every package inside AlphaLab follows the same engineering standards.

- Immutable dataclasses
- Frozen objects where applicable
- Pure functional operations
- Event-driven communication
- Deterministic execution
- Strict static typing
- Comprehensive automated testing
- Explicit dependency boundaries

Consistency across packages significantly reduces maintenance complexity as the project grows.

---

# High-Level Architecture

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

The architecture is intentionally layered.

Higher-level modules orchestrate workflows.

Lower-level modules provide deterministic domain logic.

External systems communicate only through dedicated integration layers.

---

# Layered Architecture

AlphaLab is organized into five logical layers.

```
Presentation Layer

↓

Orchestration Layer

↓

Domain Engines

↓

Infrastructure

↓

External Providers
```

Each layer has clearly defined responsibilities and dependency rules.

```
+------------------------------------------------------+
|                  Presentation Layer                  |
|                AlphaLab Workbench                    |
+------------------------------------------------------+
                         │
                         ▼
+------------------------------------------------------+
|                Orchestration Layer                   |
|                 Strategy Studio                      |
+------------------------------------------------------+
                         │
                         ▼
+------------------------------------------------------+
|                 Domain Engines                       |
|  Research • Replay • Portfolio • Runtime • Data      |
+------------------------------------------------------+
                         │
                         ▼
+------------------------------------------------------+
|                Infrastructure Layer                  |
| Events • Persistence • Plugins • Scheduler • Kernel  |
+------------------------------------------------------+
                         │
                         ▼
+------------------------------------------------------+
|                External Integrations                 |
| Brokers • Market Data • Exchanges • Files • APIs     |
+------------------------------------------------------+
```

Dependencies always flow downward.

Lower layers never depend on higher layers.

This ensures loose coupling and simplifies long-term maintenance.

---

# Module Responsibilities

AlphaLab is intentionally divided into independent modules.

Each module has a single, well-defined responsibility and communicates with other modules through stable interfaces.

No module should attempt to duplicate the responsibilities of another.

---

# Presentation Layer

## Workbench

**Package**

```
alphalab/workbench
```

### Responsibility

The Workbench provides the primary user interface for AlphaLab.

It is responsible for presenting information and initiating workflows.

The Workbench **never implements business logic**.

Instead, it delegates every operation to the Strategy Studio.

Examples include:

- Opening projects
- Viewing datasets
- Running backtests
- Displaying reports
- Monitoring production systems
- Managing layouts
- Navigating workspaces

### Owns

- UI state
- Sessions
- Layouts
- Views
- Navigation
- Themes

### Never Owns

- Research algorithms
- Portfolio optimization
- Broker communication
- Data normalization
- Production runtime

---

## Strategy Studio

**Package**

```
alphalab/studio
```

### Responsibility

Strategy Studio is the orchestration layer of AlphaLab.

It coordinates complete quantitative research workflows.

Every high-level workflow passes through Strategy Studio.

Examples include:

- Creating projects
- Running pipelines
- Executing backtests
- Managing experiments
- Generating reports
- Organizing datasets

### Owns

- Projects
- Pipelines
- Experiments
- Sessions
- Reports
- Workspace state

### Never Owns

- Market data providers
- Portfolio algorithms
- Runtime supervision
- Broker implementations

Those responsibilities belong to dedicated engines.

---

# Domain Layer

The Domain Layer contains the core business logic of AlphaLab.

Each engine is completely independent.

---

## Universal Data Engine

**Package**

```
alphalab/data
```

### Responsibility

Provides a canonical representation of market data.

Responsibilities include:

- Loading datasets
- Schema detection
- Normalization
- Validation
- Cleaning
- Metadata extraction
- Symbol normalization
- Timezone normalization
- Dataset statistics

Every downstream module consumes canonical datasets produced here.

### Owns

- Dataset model
- Dataset registry
- Schema inference
- Validation
- Transformations

### Never Owns

- Research
- Trading logic
- Portfolio optimization

---

## Research Engine

**Package**

```
alphalab/research
```

### Responsibility

Transforms market data into research outputs.

Capabilities include:

- Statistical analysis
- Walk-forward testing
- Bootstrap analysis
- Monte Carlo simulation
- Capacity estimation
- Regime analysis
- Strategy diagnostics

### Inputs

Canonical datasets.

### Outputs

Research results.

---

## Replay Engine

**Package**

```
alphalab/replay
```

### Responsibility

Provides deterministic replay of historical events.

Replay guarantees reproducibility across multiple executions.

### Responsibilities

- Historical replay
- Event sequencing
- Time progression
- Replay validation

---

## Portfolio Optimizer

**Package**

```
alphalab/portfolio_optimizer
```

### Responsibility

Transforms research outputs into portfolios.

Supports:

- Equal Weight
- Risk Parity
- Minimum Variance
- Maximum Sharpe
- Weight constraints
- Exposure calculation
- Rebalancing
- Transaction cost estimation

### Owns

Portfolio mathematics.

### Never Owns

Research.

---

## Runtime

**Package**

```
alphalab/runtime
```

### Responsibility

Coordinates execution of AlphaLab components.

Handles runtime lifecycle while remaining independent of production deployment.

---

## Production Runtime

**Package**

```
alphalab/production
```

### Responsibility

Supervises live systems.

Provides:

- Process supervision
- Health monitoring
- Heartbeats
- Checkpoints
- Recovery
- Restart policies

Production Runtime exists independently from research.

---

# Integration Layer

## Broker Integrations

**Package**

```
alphalab/integrations
```

### Responsibility

Provides a unified abstraction over broker APIs.

Current providers include:

- Paper Trading
- Alpaca
- Interactive Brokers
- Zerodha

Future providers can be added without modifying existing modules.

---

## Market Data

**Package**

```
alphalab/marketdata
```

### Responsibility

Fetches market data from supported providers.

Examples include:

- Yahoo Finance
- Polygon
- Databento
- Binance
- NSE

Raw provider data is forwarded to the Universal Data Engine for normalization.

---

# Infrastructure Layer

Infrastructure packages provide shared capabilities used across the platform.

---

## Events

```
alphalab/events
```

Provides immutable event infrastructure.

Every major subsystem communicates through events.

---

## Persistence

```
alphalab/persistence
```

Provides durable storage.

Examples include:

- Snapshots
- Checkpoints
- Serialization

---

## Scheduler

```
alphalab/scheduler
```

Coordinates deterministic execution of scheduled tasks.

---

## Plugins

```
alphalab/plugins
```

Provides AlphaLab's extension mechanism.

Third-party modules integrate through plugins rather than modifying core packages.

---

## Kernel

```
alphalab/kernel
```

Provides the internal execution foundation shared across the platform.

---

# Dependency Rules

AlphaLab enforces strict dependency boundaries.

Dependencies always flow downward.

```
Workbench
      │
      ▼
Strategy Studio
      │
      ▼
Domain Engines
      │
      ▼
Infrastructure
      │
      ▼
Integrations
```

Dependencies in the opposite direction are prohibited.

---

# Allowed Dependencies

## Workbench

May depend on:

- Strategy Studio

Must not depend on:

- Research
- Portfolio Optimizer
- Runtime
- Market Data
- Broker APIs

---

## Strategy Studio

May depend on:

- Research
- Portfolio Optimizer
- Universal Data
- Replay
- Runtime
- Production
- Reporting

Must not depend directly on provider implementations.

---

## Universal Data

May depend on:

- Market Data
- Feed
- Persistence

Must not depend on:

- Research
- Portfolio
- Workbench

---

## Research

May depend on:

- Universal Data
- Analytics
- Replay

Must not depend on:

- Workbench
- Production

---

## Portfolio Optimizer

May depend on:

- Research outputs
- Analytics

Must never depend on:

- Workbench
- Broker APIs

---

## Production

May depend on:

- Runtime
- Integrations

Must not depend on:

- Research
- Portfolio

---

## Integrations

May depend only on:

- Core interfaces
- Protocols
- Authentication

They must never import higher-level AlphaLab modules.

---

# Circular Dependencies

Circular imports are prohibited.

For example:

```
Workbench

↓

Studio

↓

Research

↓

Workbench
```

is invalid.

Instead:

```
Workbench

↓

Studio

↓

Research
```

Communication must always return through immutable results rather than reverse imports.

---

# Architectural Invariants

Every new module introduced into AlphaLab should satisfy the following rules.

- One primary responsibility
- Immutable state
- Explicit inputs
- Explicit outputs
- Event-driven communication
- Pure functional APIs
- Strict typing
- Comprehensive testing
- No circular dependencies
- No hidden global state

These invariants ensure AlphaLab remains maintainable as the platform evolves beyond v1.0.

# Data Flow

One of the primary design goals of AlphaLab is to establish a deterministic, traceable flow of information from raw market data to production execution.

Rather than allowing each subsystem to manipulate data independently, AlphaLab follows a structured processing pipeline where every stage has a clearly defined responsibility.

Each layer transforms its inputs into immutable outputs before passing them to the next layer.

---

# End-to-End Workflow

> **Target, not current state.** The lifecycle below is the design goal. It is
> not a single pipeline that exists today: most stages are still separate
> engines. The concrete path is `alphalab.runtime.ExecutionPipeline` (market →
> strategy → allocation → risk → OMS → execution simulator → portfolio →
> analytics), driven end to end from a dataset by `alphalab.backtesting`. As of
> v2.2 `Replay Engine → Performance Report` *is* built, via
> `alphalab.backtesting.replay`, which drives that same path; the research,
> universal-data and reporting stages around it are not wired in.

The intended lifecycle of a quantitative strategy within AlphaLab is illustrated below.

```
                   External Data Sources
                           │
                           ▼
                  Market Data Providers
                           │
                           ▼
               Universal Data Engine
                           │
                           ▼
                  Canonical Datasets
                           │
                           ▼
                   Research Engine
                           │
                           ▼
                  Research Results
                           │
                           ▼
                Portfolio Optimizer
                           │
                           ▼
                  Target Portfolio
                           │
                           ▼
                     Replay Engine
                           │
                           ▼
                  Performance Report
                           │
                           ▼
                   Strategy Studio
                           │
                           ▼
                  AlphaLab Workbench
                           │
                           ▼
                 Production Runtime
                           │
                           ▼
                Broker Integrations
                           │
                           ▼
                     Live Markets
```

Each stage has a single responsibility and never bypasses another stage.

---

# Stage 1 — Data Acquisition

The lifecycle begins by acquiring market data.

Supported sources include

- CSV
- JSON
- Parquet
- Yahoo Finance
- Polygon
- Databento
- Binance
- NSE
- Broker exports
- Future providers

These providers expose different schemas, timestamps, symbols, and conventions.

Provider-specific formats never propagate beyond this stage.

---

# Stage 2 — Universal Data Engine

All raw datasets pass through the Universal Data Engine.

Responsibilities include

- Schema detection
- Symbol normalization
- Timestamp normalization
- Timezone conversion
- Missing value handling
- Duplicate detection
- Data validation
- Quality analysis
- Metadata extraction

The output is an immutable canonical dataset.

Every downstream subsystem consumes the same representation.

---

# Canonical Dataset

After normalization every dataset has a consistent structure.

Examples include

- Quote
- Trade
- Bar
- OrderBook
- FundamentalRecord
- CorporateAction
- EconomicEvent

No downstream package needs to understand provider-specific schemas.

---

# Stage 3 — Research Engine

Research operates exclusively on canonical datasets.

Responsibilities include

- Statistical analysis
- Walk-forward testing
- Monte Carlo simulation
- Bootstrap analysis
- Capacity estimation
- Regime detection
- Strategy diagnostics

Research produces immutable research results rather than executing trades.

---

# Research Outputs

Typical outputs include

- Performance statistics
- Risk metrics
- Regime analysis
- Capacity estimates
- Strategy diagnostics
- Generated signals

These outputs become inputs for portfolio construction.

---

# Stage 4 — Portfolio Optimization

The Portfolio Optimizer converts research outputs into investable portfolios.

Optimization methods include

- Equal Weight
- Risk Parity
- Maximum Sharpe
- Minimum Variance

Additional processing includes

- Position sizing
- Constraint enforcement
- Exposure analysis
- Transaction cost estimation
- Rebalancing logic

The output is a target portfolio.

---

# Stage 5 — Replay Engine

> As of v2.2 `alphalab.replay` drives the real execution path, through
> `alphalab.backtesting.replay.ReplayBacktest`. The replay package itself still
> owns only the cursor, the replay clock, the session lifecycle and
> chronological validation; `backtesting` is what connects each event it yields
> to strategy, allocation, risk, OMS, execution and portfolio. A replay and a
> backtest of the same dataset produce identical orders, fills and P&L (see
> ADR-0010).

Replay simulates historical event playback under reproducible conditions.

Responsibilities include

- Historical event playback
- Event ordering
- Time progression
- Deterministic execution

Replay never modifies research outputs.

---

# Stage 6 — Reporting

Results from replay are transformed into reports.

Typical reports include

- Performance
- Risk
- Trade summary
- Portfolio analysis
- Drawdown analysis
- Attribution

Reports are immutable snapshots.

---

# Stage 7 — Strategy Studio

Strategy Studio orchestrates the entire workflow.

It coordinates

- Projects
- Pipelines
- Datasets
- Strategies
- Experiments
- Reports
- Backtests

Strategy Studio never implements research algorithms.

Instead, it coordinates the specialized engines.

---

# Stage 8 — AlphaLab Workbench

The Workbench provides the graphical interface.

Users can

- Browse datasets
- Configure experiments
- Execute pipelines
- Monitor production
- Analyze reports
- Compare strategies

The Workbench delegates every operation to Strategy Studio.

---

# Stage 9 — Production Runtime

Once validated, strategies enter production.

Production Runtime provides

- Supervision
- Health monitoring
- Checkpointing
- Recovery
- Process management

Production is intentionally isolated from research.

---

# Stage 10 — Broker Integrations

The final stage communicates with external brokers.

Supported providers include

- Paper Trading
- Alpaca
- Interactive Brokers
- Zerodha

Additional providers can be integrated without modifying higher-level modules.

---

# Data Ownership

Every stage owns only its own data.

```
Market Data
        │
        ▼
Raw Dataset
        │
        ▼
Canonical Dataset
        │
        ▼
Research Result
        │
        ▼
Portfolio
        │
        ▼
Replay Result
        │
        ▼
Production State
```

Objects are never shared through mutable references.

Instead, immutable outputs are passed between stages.

---

# Data Transformations

Each subsystem transforms data but never mutates previous results.

```
Raw Data

↓

Normalized Data

↓

Research

↓

Portfolio

↓

Replay

↓

Reports

↓

Production
```

Every transformation is deterministic.

---

# Traceability

Every result in AlphaLab can be traced back to its origin.

```
Broker Fill

↓

Portfolio

↓

Research Result

↓

Canonical Dataset

↓

Raw Dataset

↓

Provider
```

This enables

- reproducibility
- auditing
- debugging
- validation
- compliance

---

# Separation of Responsibilities

The following responsibilities are intentionally separated.

| Layer | Responsibility |
|--------|----------------|
| Market Data | Acquire raw data |
| Universal Data | Normalize and validate |
| Research | Analyze markets |
| Portfolio Optimizer | Construct portfolios |
| Replay | Validate strategies |
| Reporting | Summarize results |
| Strategy Studio | Orchestrate workflows |
| Workbench | Present information |
| Production | Execute and supervise |
| Integrations | Connect external systems |

No subsystem duplicates another subsystem's responsibility.

---

# Architectural Guarantees

The data flow architecture provides several guarantees.

- Provider independence
- Deterministic execution
- Immutable processing
- Complete reproducibility
- Modular composition
- Separation of concerns
- Production readiness
- Extensibility for future modules

These guarantees form the foundation upon which future capabilities—such as the Feature Store, Machine Learning, and Cloud Research—will be built.

# Event Flow & State Lifecycle

AlphaLab is fundamentally an event-driven platform.

Every meaningful action within the framework is represented by an immutable event that transitions the system from one valid state to another.

Unlike traditional imperative architectures where objects are modified in place, AlphaLab models the evolution of the system as a sequence of immutable state transitions.

This approach provides deterministic execution, complete auditability, reproducibility, and simplified debugging.

---

# Event-Driven Architecture

Every subsystem emits events describing **what happened**, not **what should happen**.

For example

```
DatasetLoaded

↓

ResearchStarted

↓

ResearchCompleted

↓

PortfolioOptimized

↓

ReplayCompleted

↓

ReportGenerated
```

These events become part of the immutable execution history.

---

# Event Lifecycle

Every event follows the same lifecycle.

```
Request

↓

Validation

↓

Execution

↓

State Transition

↓

Event Creation

↓

Event Publication

↓

Immutable State Returned
```

At no point is existing state modified.

---

# Immutable State Transition

Traditional applications often perform operations such as

```
Portfolio.cash -= 1000
Portfolio.positions["AAPL"] += 10
```

AlphaLab instead performs

```
Old State

↓

Operation

↓

New State
```

Example

```python
new_state = PortfolioEngine.execute(
    previous_state,
    order,
)
```

The previous state continues to exist unchanged.

---

# State Evolution

Each engine maintains its own immutable state.

```
State₀

↓

Event₁

↓

State₁

↓

Event₂

↓

State₂

↓

Event₃

↓

State₃
```

The complete history remains reproducible.

---

# Event Ownership

Every subsystem owns its own event types.

Examples include

| Package | Events |
|----------|--------|
| Research | ResearchStarted, ResearchCompleted |
| Data | DatasetLoaded, DatasetValidated |
| Portfolio Optimizer | PortfolioOptimized, AllocationChanged |
| Replay | ReplayStarted, ReplayCompleted |
| Runtime | RuntimeStarted, RuntimeStopped |
| Production | CheckpointCreated, ProcessRestarted |
| Integrations | BrokerConnected, OrderSubmitted |
| Workbench | ProjectOpened, SessionCreated |

This separation prevents coupling between unrelated domains.

---

# Event Categories

Events can be grouped into several logical categories.

## Data Events

Represent movement of datasets through the platform.

Examples

```
DatasetLoaded

DatasetValidated

DatasetNormalized

DatasetTransformed
```

---

## Research Events

Represent quantitative research activities.

Examples

```
ResearchStarted

ResearchCompleted

BootstrapCompleted

MonteCarloCompleted
```

---

## Portfolio Events

Represent portfolio construction.

Examples

```
WeightsCalculated

PortfolioOptimized

ConstraintViolated

PortfolioRebalanced
```

---

## Replay Events

Represent historical simulation.

Examples

```
ReplayStarted

ReplayPaused

ReplayCompleted
```

---

## Runtime Events

Represent runtime management.

Examples

```
RuntimeStarted

RuntimeStopped

RuntimeRecovered
```

---

## Production Events

Represent production supervision.

Examples

```
HeartbeatReceived

CheckpointCreated

ProcessRestarted

HealthChanged
```

---

## Integration Events

Represent communication with external providers.

Examples

```
BrokerConnected

AuthenticationSucceeded

OrderSubmitted

OrderFilled

PortfolioSynchronized
```

---

# Event Propagation

Events flow upward through the architecture.

```
Market Data

↓

Universal Data

↓

Research

↓

Portfolio

↓

Replay

↓

Studio

↓

Workbench
```

Lower layers never consume higher-layer events.

---

# State Ownership

Every package owns exactly one primary state object.

Examples

| Package | State |
|----------|-------|
| Data | DatasetState |
| Research | ResearchState |
| Replay | ReplayState |
| Portfolio | PortfolioState |
| Runtime | RuntimeState |
| Production | ProductionState |
| Studio | StrategyStudioState |
| Workbench | WorkbenchState |

Each state is immutable.

---

# State Transition Rules

Every transition satisfies the following rules.

- Input state is immutable.
- Validation occurs before execution.
- Events are generated after successful execution.
- A new state object is returned.
- Previous states remain unchanged.

This guarantees deterministic behavior.

---

# Replay Determinism

Replay is one of AlphaLab's defining architectural principles.

Given

- identical datasets
- identical parameters
- identical event ordering
- identical configuration

Replay always produces

- identical trades
- identical metrics
- identical reports
- identical events

```
Input

↓

Replay

↓

Output
```

The output is deterministic.

---

# Event Ordering

Event ordering is deterministic.

```
DatasetLoaded

↓

ResearchStarted

↓

ResearchCompleted

↓

PortfolioOptimized

↓

ReplayStarted

↓

ReplayCompleted
```

Events are never reordered after publication.

---

# Event Immutability

Events never change after creation.

```
Event Created

↓

Published

↓

Stored

↓

Consumed

↓

Archived
```

Consumers may read events but never modify them.

---

# State Snapshots

Certain subsystems support snapshots.

Examples include

- Runtime
- Production
- Replay
- Persistence

Snapshots provide

- recovery
- checkpointing
- debugging
- auditing

Example

```
State₀

↓

Checkpoint

↓

State₁

↓

Checkpoint

↓

State₂
```

A system can recover from any checkpoint without replaying the entire execution history.

---

# Event Sourcing Philosophy

AlphaLab follows an event-sourcing-inspired architecture.

Current state is considered the result of all previous events.

```
Event₁

↓

Event₂

↓

Event₃

↓

Current State
```

While AlphaLab does not require persistent event stores for every module, the architectural model is designed around immutable event histories.

---

# Failure Handling

Failures are represented explicitly.

Instead of silently mutating state

```
Process Failed
```

becomes

```
ProcessFailedEvent

↓

ProductionState Updated
```

Likewise

```
BrokerDisconnected

↓

IntegrationState Updated
```

Every failure is observable.

---

# Validation Pipeline

Every operation follows the same execution model.

```
Input

↓

Validation

↓

Business Logic

↓

State Creation

↓

Event Creation

↓

Return New State
```

No operation bypasses validation.

---

# Event Contracts

Every event should satisfy the following properties.

- Immutable
- Timestamped
- Typed
- Serializable
- Deterministic
- Self-contained

Events should never rely on external mutable state.

---

# State Contracts

Every state object should satisfy the following properties.

- Immutable
- Frozen
- Deterministic
- Serializable
- Fully typed
- Independent of global variables

State should contain all information required to continue execution.

---

# Debugging Benefits

Because AlphaLab preserves immutable state transitions, debugging becomes significantly simpler.

Developers can inspect

```
State₀

↓

Event₁

↓

State₁

↓

Event₂

↓

State₂
```

without worrying that previous states have been overwritten.

---

# Testing Benefits

Immutable state enables highly deterministic testing.

Each unit test simply verifies

```
Input State

↓

Operation

↓

Expected Output State

↓

Expected Events
```

No mocking of hidden global state is required.

---

# Architectural Guarantees

The event and state architecture provides the following guarantees.

- Deterministic execution
- Complete reproducibility
- Immutable state transitions
- Predictable debugging
- Event traceability
- Safe concurrency
- Replay compatibility
- Production reliability

These guarantees form the foundation of every subsystem within AlphaLab and ensure that future capabilities—such as distributed research, machine learning pipelines, cloud execution, and enterprise deployments—can be integrated while preserving deterministic behavior and architectural consistency.

# Package Structure

AlphaLab is organized as a collection of independent, domain-driven packages.

Each package owns a single business capability and exposes a well-defined public interface.

The project intentionally avoids large monolithic modules in favor of smaller, focused components with explicit responsibilities.

---

# Repository Layout

```
AlphaLab/

├── alphalab/
├── benchmarks/
├── docs/
├── examples/
├── tests/
├── pyproject.toml
├── README.md
└── LICENSE
```

---

# Source Code

All production code resides under

```
alphalab/
```

Each package represents a single subsystem within the platform.

```
alphalab/

# Canonical execution core — wired together by runtime.ExecutionPipeline
core/  runtime/  strategy/  allocation/  risk/  oms/
execution/  portfolio/  analytics/  market/

# Shared infrastructure
common/  kernel/  plugins/  scheduler/  persistence/  optimizer/

# Standalone engines
research/  replay/  portfolio_optimizer/  reporting/
feature_store/  factor_library/  alt_data/
ml/  deep_learning/  reinforcement_learning/
options/  futures/  crypto/  macro/
cloud_research/  cluster_scheduler/  distributed/
experiment_tracking/  model_registry/  deployment_manager/
studio/  workbench/  research_assistant/  enterprise/

# Data surface (overlapping; consolidation deferred)
data/  marketdata/  feed/

# Live / ops surface (deferred product decisions)
live/  production/  broker/  brokers/  integrations/
```

Every package follows a consistent internal organization.

> `alphalab/core/events/` exists but is not used by the execution path.
> There is no top-level `alphalab/events/` package.

---

# Standard Package Layout

Most AlphaLab packages contain the following modules.

```
module/

__init__.py

adapter.py

config.py

engine.py

events.py

exceptions.py

manager.py

protocol.py

registry.py

state.py

validation.py

views.py
```

Not every package requires every file.

However, new packages should follow this convention whenever appropriate.

---

# Common Module Responsibilities

## adapter.py

Provides adapters between AlphaLab and external systems.

Examples include

- Broker adapters
- Market data adapters
- File adapters

Adapters isolate provider-specific implementations.

---

## config.py

Defines immutable configuration objects.

Configuration should never be represented as mutable dictionaries.

---

## engine.py

Contains the primary public API.

The engine coordinates package functionality while remaining stateless.

Typical usage

```python
new_state = ResearchEngine.run(state, payload)
```

---

## events.py

Defines immutable domain events.

Every significant operation produces one or more events.

---

## exceptions.py

Defines package-specific exceptions.

Examples

```
ResearchValidationError

PortfolioOptimizationError

BrokerConnectionError
```

Exceptions should remain local to the package whenever possible.

---

## manager.py

Implements business logic.

Managers perform the actual work executed by the engine.

Engines delegate to managers.

---

## protocol.py

Defines abstract interfaces.

Protocols enable dependency inversion and simplify testing.

---

## registry.py

Maintains immutable registries.

Examples include

- datasets
- brokers
- strategies
- plugins
- portfolios

Registries never mutate existing collections.

---

## state.py

Defines immutable package state.

Every package should expose exactly one primary state object.

---

## validation.py

Contains validation rules.

Validation occurs before business logic executes.

---

## views.py

Provides read-only projections of state.

Views simplify inspection while preserving encapsulation.

---

# Package Categories

AlphaLab packages fall into five architectural categories.

```
Presentation

Orchestration

Domain

Infrastructure

Integration
```

---

## Presentation

```
workbench/
```

Responsible for user interaction.

Presentation packages never contain business logic.

---

## Orchestration

```
studio/
```

Coordinates workflows across multiple engines.

---

## Domain

```
research/

portfolio_optimizer/

data/

runtime/

production/

replay/

reporting/
```

Implements business capabilities.

Each package owns one domain.

---

## Infrastructure

```
events/

kernel/

plugins/

scheduler/

persistence/
```

Provides platform-wide infrastructure.

Infrastructure packages support other domains without owning business workflows.

---

## Integration

```
marketdata/

integrations/

feed/
```

Connects AlphaLab to external systems.

Provider-specific logic is isolated here.

---

# Package Independence

Packages should remain independent whenever possible.

For example

```
Research

↓

Portfolio Optimizer
```

is acceptable.

However

```
Portfolio Optimizer

↓

Research
```

must never occur.

Dependencies always point downward through the architecture.

---

# Public Interfaces

Every package should expose a minimal public interface through

```
__init__.py
```

Users should interact with

```python
from alphalab.research import ResearchEngine
```

rather than importing internal modules.

Internal implementation details remain private.

---

# Internal Modules

Files such as

```
manager.py

registry.py

validation.py
```

are implementation details.

Applications should not import them directly.

Example

Preferred

```python
from alphalab.portfolio_optimizer import PortfolioEngine
```

Avoid

```python
from alphalab.portfolio_optimizer.manager import PortfolioManager
```

unless contributing to AlphaLab itself.

---

# Tests

Every production package should have a corresponding test package.

```
tests/unit/

research/

portfolio_optimizer/

studio/

workbench/

...
```

The directory structure of the tests should closely mirror the production code.

This simplifies navigation and improves maintainability.

---

# Benchmarks

Performance benchmarks are stored separately from unit tests.

```
benchmarks/
```

Benchmarks are intended to measure

- execution speed
- scalability
- memory usage
- throughput

They should never replace correctness tests.

---

# Examples

The

```
examples/
```

directory contains complete, executable demonstrations of AlphaLab workflows.

Examples illustrate best practices and should remain synchronized with public APIs.

They are considered part of the user-facing documentation.

---

# Documentation

All technical documentation resides under

```
docs/
```

This includes

- architecture
- system design
- engineering guidelines
- examples
- roadmap
- changelog
- architectural decision records

Documentation should evolve alongside the codebase.

---

# Naming Conventions

AlphaLab follows consistent naming conventions.

Packages

```
snake_case
```

Classes

```
PascalCase
```

Functions

```
snake_case
```

Constants

```
UPPER_SNAKE_CASE
```

Modules

```
snake_case.py
```

Event Classes

```
SomethingHappened
```

State Classes

```
SomethingState
```

Engine Classes

```
SomethingEngine
```

---

# Adding New Packages

When introducing a new subsystem, contributors should follow the existing architectural pattern.

A new package should include

```
__init__.py
engine.py
state.py
events.py
manager.py
validation.py
exceptions.py
views.py
tests/
```

Additional modules may be added where justified, but the overall structure should remain consistent with the rest of AlphaLab.

---

# Architectural Consistency

Maintaining a predictable package structure provides several benefits.

- Easier navigation
- Faster onboarding
- Consistent APIs
- Reduced maintenance
- Simpler testing
- Better tooling support

As AlphaLab grows, preserving this consistency becomes increasingly important.

Every new subsystem should integrate naturally into the existing architecture rather than introducing a new organizational style.

# Extension Architecture

One of the primary design goals of AlphaLab is extensibility.

The platform is designed so that new capabilities can be added without modifying existing subsystems.

Rather than tightly coupling implementations together, AlphaLab relies on well-defined interfaces, immutable state, protocols, and adapters.

This enables the platform to evolve while maintaining long-term architectural stability.

---

# Extensibility Philosophy

Every subsystem should be

- Replaceable
- Testable
- Independent
- Deterministic
- Loosely coupled

New functionality should be introduced by extending existing interfaces rather than modifying stable components.

---

# Architectural Layers

```
User Code

↓

Workbench

↓

Strategy Studio

↓

Public APIs

↓

Protocols

↓

Implementations

↓

External Systems
```

Only public APIs should be used by applications.

Internal implementations remain interchangeable.

---

# Public Interfaces

Every package exposes a small public API through

```
__init__.py
```

Example

```python
from alphalab.research import ResearchEngine
```

Applications should avoid importing internal implementation modules.

---

# Protocol-Based Design

AlphaLab uses protocol-oriented design wherever possible.

Protocols define behavior rather than implementation.

Example

```
ResearchProviderProtocol

BrokerProviderProtocol

MarketDataProviderProtocol

OptimizerProtocol
```

Implementations conform to protocols while remaining independent.

---

# Adapter Pattern

External systems rarely match AlphaLab's internal models.

Adapters translate external APIs into canonical AlphaLab objects.

```
External Provider

↓

Adapter

↓

Canonical AlphaLab Model
```

Adapters isolate provider-specific logic.

---

# Current Adapter Types

Examples include

```
Paper Trading

Yahoo Finance

Polygon

Databento

Binance

Interactive Brokers

Alpaca

Zerodha
```

Each adapter converts provider-specific behavior into deterministic AlphaLab operations.

---

# Provider Independence

Higher-level modules never depend directly on providers.

Instead

```
Research

↓

Universal Data

↓

Provider Adapter

↓

External API
```

Changing providers does not affect research code.

---

# Plugin Architecture

AlphaLab includes a plugin system for extending functionality.

Plugins may provide

- New brokers
- New market data providers
- New optimization algorithms
- New reports
- New analytics
- New execution engines

Plugins integrate through stable interfaces rather than modifying AlphaLab core.

---

# Extension Points

Future modules should extend one of the following areas.

```
Data Providers

↓

Research Engines

↓

Portfolio Optimizers

↓

Broker Providers

↓

Reporting

↓

Analytics

↓

Workbench Extensions
```

Every extension point exposes stable contracts.

---

# Custom Market Data Providers

A new provider should implement the Market Data protocol.

```
MyProvider

↓

MarketDataProviderProtocol

↓

Universal Data Engine

↓

Canonical Dataset
```

The remainder of AlphaLab remains unaware of the provider.

---

# Custom Broker Providers

Broker integrations follow the same pattern.

```
Broker API

↓

Broker Adapter

↓

Integration Protocol

↓

Production Runtime
```

Research and portfolio modules never communicate directly with broker APIs.

---

# Custom Optimizers

Portfolio optimization algorithms are interchangeable.

Example

```
Risk Parity

↓

Optimizer Protocol

↓

Portfolio Engine
```

Future optimizers may be added without changing portfolio orchestration.

---

# Custom Reports

Reporting is intentionally modular.

Future reports may include

- ESG Reports
- Attribution Reports
- Regulatory Reports
- Performance Dashboards
- Risk Summaries

All reports consume immutable result objects.

---

# Custom Analytics

Analytics modules can extend

- Risk metrics
- Performance metrics
- Capacity estimation
- Statistical diagnostics
- Market regime analysis

Existing research workflows remain unchanged.

---

# Future Machine Learning

Machine learning modules will integrate using the same architecture.

```
Canonical Dataset

↓

Feature Store

↓

Model

↓

Predictions

↓

Research Engine
```

The ML implementation remains isolated behind stable interfaces.

---

# Future Cloud Execution

Cloud execution will also extend existing abstractions.

```
Strategy Studio

↓

Cloud Scheduler

↓

Distributed Workers

↓

Results

↓

Workbench
```

Cloud execution becomes another execution backend rather than a separate platform.

---

# Future AI Assistant

The AI Research Assistant will consume existing APIs.

```
Workbench

↓

AI Assistant

↓

Strategy Studio

↓

Research Engine
```

The assistant orchestrates workflows without bypassing architectural boundaries.

---

# Backward Compatibility

Public APIs should remain stable whenever possible.

Breaking changes should be minimized and introduced only through major releases.

Deprecated interfaces should remain available for a transition period.

---

# Testing Extensions

Every extension should satisfy the same engineering standards as AlphaLab core.

Requirements include

- Immutable state
- Deterministic execution
- Comprehensive unit tests
- Strict typing
- Ruff compliance
- MyPy compliance

Extensions should integrate seamlessly with the existing testing framework.

---

# Architectural Stability

The extension architecture enables AlphaLab to grow without becoming tightly coupled.

As new capabilities are introduced, contributors should prefer extending existing interfaces over modifying established components.

This philosophy allows AlphaLab to evolve from a quantitative research platform into a comprehensive ecosystem while preserving consistency, maintainability, and long-term stability.

# Future Architecture

> **Historical note.** This section was written for v1.0.0. Most of what it calls
> "future" — the feature store, factor library, options / futures / crypto /
> macro engines, alternative data, ML / deep learning / RL, cloud research,
> cluster scheduler, experiment tracking, model registry, research assistant,
> deployment manager, and Enterprise — has since shipped (v1.34.0–v2.0.0) as
> standalone packages. The per-module "Future Version" tags below are left as
> written for the record; treat them as delivered. What remains genuinely
> unbuilt is the *integration* of these engines into one runtime (see
> **Implementation Status** and `ROADMAP.md`).

AlphaLab has been designed with long-term extensibility in mind.

The current architecture is intentionally modular so that future capabilities can be introduced without redesigning existing subsystems.

The architecture established in v1.0.0 serves as the stable foundation upon which subsequent releases have been built.

Rather than creating separate frameworks for machine learning, derivatives, cloud computing, or enterprise deployments, these modules extend the existing architecture through well-defined interfaces.

---

# Architectural Evolution

The long-term vision of AlphaLab follows a layered evolution.

```
                Applications
                      │
                      ▼
               AlphaLab Workbench
                      │
                      ▼
               Strategy Studio
                      │
     ┌────────────────┼─────────────────┐
     ▼                ▼                 ▼
 Research      Portfolio Engine    Production
     │                │                 │
     └────────────────┼─────────────────┘
                      ▼
          Universal Data Engine
                      │
               External Providers
```

Future releases expand individual layers without modifying their responsibilities.

---

# Feature Store

Future Version

```
v1.1
```

The Feature Store introduces reusable quantitative features.

```
Market Data

↓

Universal Data Engine

↓

Feature Store

↓

Research Engine
```

Responsibilities include

- Feature computation
- Feature versioning
- Feature metadata
- Feature validation
- Feature caching

The Feature Store becomes the single source of truth for engineered features.

---

# Factor Library

Future Version

```
v1.2
```

The Factor Library builds upon the Feature Store.

```
Feature Store

↓

Factor Library

↓

Research
```

Examples

- Momentum
- Value
- Quality
- Volatility
- Carry
- Liquidity
- Seasonality

Factors remain provider-independent.

---

# Derivatives Engines

Future releases introduce specialized engines.

```
Options Engine

Futures Engine

Crypto Engine
```

Each engine becomes another independent domain module.

```
Research

↓

Options Engine

↓

Portfolio
```

The Portfolio Optimizer remains unchanged.

---

# Macro Engine

The Macro Engine introduces economic data processing.

Examples

- Interest rates
- Inflation
- GDP
- Employment
- Central bank events

```
Macro Data

↓

Universal Data

↓

Macro Engine

↓

Research
```

No changes are required to Strategy Studio.

---

# Alternative Data

Alternative datasets integrate through the Universal Data Engine.

Examples

- News
- Satellite imagery
- Shipping
- Credit cards
- Social sentiment
- ESG
- Web traffic

```
Alternative Provider

↓

Universal Data

↓

Research
```

Provider-specific logic remains isolated.

---

# Machine Learning

Machine Learning integrates after the Feature Store.

```
Market Data

↓

Universal Data

↓

Feature Store

↓

Machine Learning

↓

Research
```

Responsibilities include

- Dataset preparation
- Model training
- Cross validation
- Prediction
- Evaluation

The Research Engine remains responsible for strategy evaluation.

---

# Deep Learning

Deep Learning extends Machine Learning.

Examples

- LSTM
- Transformer
- Temporal CNN
- Autoencoder

Architecture

```
Feature Store

↓

Deep Learning

↓

Predictions

↓

Research
```

Deep Learning models remain independent from execution.

---

# Reinforcement Learning

Reinforcement Learning introduces policy optimization.

```
Replay Engine

↓

RL Environment

↓

Policy

↓

Research
```

Replay becomes the simulation environment.

Production Runtime remains unchanged.

---

# Cloud Research

Cloud Research enables distributed execution.

```
Workbench

↓

Strategy Studio

↓

Cloud Scheduler

↓

Distributed Workers

↓

Results
```

Cloud execution becomes another orchestration backend.

---

# Cluster Scheduler

The scheduler coordinates distributed workloads.

Responsibilities include

- Worker allocation
- Resource scheduling
- Queue management
- Failure recovery
- Retry policies

The scheduler does not execute research directly.

---

# Experiment Tracking

Future versions introduce experiment management.

Each experiment stores

- Dataset
- Parameters
- Features
- Models
- Metrics
- Reports
- Runtime

Experiments remain immutable.

---

# Model Registry

Machine learning models become first-class objects.

```
Training

↓

Registry

↓

Deployment

↓

Production
```

Registry responsibilities include

- Versioning
- Metadata
- Promotion
- Validation
- Rollback

---

# AI Research Assistant

The AI Assistant becomes another client of Strategy Studio.

```
Workbench

↓

AI Assistant

↓

Strategy Studio

↓

Research
```

The assistant orchestrates workflows using existing APIs rather than bypassing them.

---

# Deployment Manager

Deployment becomes an orchestration concern.

Responsibilities include

- Packaging
- Validation
- Release management
- Rollback
- Monitoring

Deployment does not modify research logic.

---

# AlphaLab Cloud

Cloud introduces managed infrastructure.

```
Workbench

↓

Cloud API

↓

Strategy Studio

↓

Cloud Runtime

↓

Workers
```

The same workflows operate locally and in the cloud.

---

# AlphaLab Enterprise

Enterprise extends the platform with organizational capabilities.

Examples

- Authentication
- Authorization
- Multi-user workspaces
- Audit logs
- Compliance
- Secrets management
- Team collaboration

Enterprise builds on existing architecture rather than replacing it.

---

# Architectural Stability

The architecture established in v1.0.0 is expected to remain stable throughout future releases.

New capabilities should integrate by extending existing layers rather than introducing parallel architectures.

This approach minimizes technical debt while preserving consistency across the platform.

---

# Evolution Principles

Future development follows several guiding principles.

- Extend existing interfaces
- Preserve deterministic execution
- Maintain immutable state
- Avoid breaking public APIs
- Minimize architectural coupling
- Keep domain responsibilities isolated
- Preserve provider independence

These principles ensure that AlphaLab can continue evolving without requiring large-scale redesigns.

---

# Looking Beyond v1.0.0

The vision for AlphaLab extends beyond individual features.

The long-term objective is to build a unified quantitative research ecosystem where data ingestion, research, portfolio construction, production execution, machine learning, cloud infrastructure, and enterprise deployment all operate through a consistent architectural model.

Every future module should strengthen that vision while preserving the engineering principles established in the first major release.

# Architecture Summary

The architecture of AlphaLab is built around a simple principle:

> **Every subsystem should have a single responsibility, expose a minimal public interface, and compose with the rest of the platform through immutable state and deterministic execution.**

Rather than constructing a monolithic trading framework, AlphaLab is composed of independent domain modules connected through well-defined interfaces.

This architecture allows the platform to evolve while remaining maintainable, testable, and predictable.

---

# Architecture at a Glance

```
                          AlphaLab Workbench
                                   │
                                   ▼
                          Strategy Studio
                                   │
      ┌───────────────┬────────────┴────────────┬───────────────┐
      ▼               ▼                         ▼               ▼
 Universal Data   Research Engine     Portfolio Optimizer  Production Runtime
      │               │                         │               │
      └───────────────┴────────────┬────────────┴───────────────┘
                                   ▼
                         Broker & Market Integrations
                                   │
                                   ▼
                              External Systems
```

Every layer has a clearly defined purpose.

Higher layers coordinate workflows.

Lower layers implement deterministic domain logic.

---

# Architectural Invariants

Every subsystem inside AlphaLab should preserve the following invariants.

## Single Responsibility

Each package owns exactly one business capability.

Examples

- Research performs quantitative analysis.
- Universal Data normalizes datasets.
- Portfolio Optimizer constructs portfolios.
- Production supervises running systems.
- Workbench presents information.

Responsibilities should never overlap.

---

## Immutable State

State objects are immutable.

Operations return new state objects instead of modifying existing ones.

This guarantees

- reproducibility
- auditability
- simpler debugging
- safer concurrency
- deterministic testing

---

## Event-Driven Execution

Meaningful operations generate immutable events.

Events describe what occurred.

They never contain business logic.

Events enable

- replay
- tracing
- auditing
- diagnostics
- deterministic execution

---

## Explicit Data Flow

Information always flows through clearly defined stages.

```
Provider

↓

Universal Data

↓

Research

↓

Portfolio

↓

Replay

↓

Reporting

↓

Studio

↓

Workbench

↓

Production
```

Modules never bypass intermediate layers.

---

## Layered Dependencies

Dependencies always point downward.

```
Presentation

↓

Orchestration

↓

Domain

↓

Infrastructure

↓

Integrations
```

Lower layers never import higher layers.

This rule prevents circular dependencies and preserves modularity.

---

## Protocol-Oriented Design

External systems are accessed through protocols and adapters.

Examples include

- Broker providers
- Market data providers
- Portfolio optimizers
- Future machine learning engines

This allows implementations to evolve independently from the platform.

---

## Public API Stability

Applications should interact only with package-level public APIs.

Example

```python
from alphalab.research import ResearchEngine
```

Internal implementation modules should not be imported directly.

Maintaining stable public interfaces simplifies upgrades and minimizes breaking changes.

---

# Engineering Standards

Every package in AlphaLab is expected to satisfy the same quality standards.

- Immutable data models
- Strict static typing
- Pure functional APIs
- Comprehensive unit tests
- Explicit validation
- Deterministic behavior
- Consistent naming conventions
- Minimal public surface area

These standards ensure a consistent developer experience across the entire platform.

---

# Scalability

The current architecture is intentionally designed to accommodate future capabilities without structural redesign.

Planned additions include

- Feature Store
- Factor Library
- Derivatives Engines
- Machine Learning
- Cloud Research
- AI Research Assistant
- Enterprise Deployment

These capabilities extend the existing architecture rather than replacing it.

---

# Long-Term Vision

AlphaLab aims to provide a unified environment for quantitative research, portfolio construction, historical simulation, production deployment, and institutional workflow management.

Every subsystem contributes to that vision while remaining independently testable and maintainable.

The platform is intended to scale from individual researchers to enterprise quantitative teams without changing its architectural foundations.

---

# Architectural Principles

The following principles should guide every future contribution.

1. Preserve deterministic execution.
2. Prefer immutable state over mutable objects.
3. Favor composition over inheritance.
4. Introduce new functionality through extension points rather than modifying stable components.
5. Keep public APIs small and consistent.
6. Avoid circular dependencies.
7. Ensure every subsystem remains independently testable.
8. Maintain strict separation between presentation, orchestration, domain logic, and integrations.

These principles are considered architectural contracts rather than implementation details.

---

# Version History

| Version | Milestone |
|---------|-----------|
| v0.27 | Production Runtime |
| v0.28 | Broker Integrations |
| v0.29 | Market Data Integrations |
| v0.30 | Portfolio Optimization Engine |
| v0.31 | Universal Data Engine |
| v0.32 | Strategy Studio |
| v0.33 | AlphaLab Workbench |
| **v1.0.0** | Stable Architecture & Engineering Foundation |
| v1.34.0 – v1.46.0 | Engine series — feature store, factor library, options, futures, crypto, macro, alternative data, ML, deep learning, RL, cloud research, cluster scheduler, experiment tracking |
| **v2.0.0** | Model registry, research assistant, deployment manager, Enterprise; canonical execution domain-model unification (R1–R4); portfolio cash-accounting and `PerformanceReport` serialization fixes (D1/D2) |

---

# Conclusion

AlphaLab is more than a collection of trading utilities.

It is a modular, deterministic, event-driven platform for quantitative research and algorithmic trading.

By enforcing immutable state, explicit interfaces, layered dependencies, and comprehensive testing, AlphaLab establishes a stable architectural foundation capable of supporting future innovations in quantitative finance, machine learning, distributed computing, and enterprise-scale deployments.

The architecture documented here serves as the reference implementation for all future development. Any new subsystem should integrate into this framework while preserving the principles that define AlphaLab.

---

**Document Version**

```
Architecture Specification
Version: v2.0.0
Status: Target architecture; see "Implementation Status (v2.0.0)" for what is built
```