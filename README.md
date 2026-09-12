<div align="center">

# AlphaLab

### Institutional-Grade Quantitative Research & Algorithmic Trading Framework

**Deterministic • Event-Driven • Immutable • Fully Typed • Production-Oriented**

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)]()
[![Version](https://img.shields.io/badge/Version-2.13.0-blue)]()
[![License](https://img.shields.io/badge/License-MIT-green.svg)]()
[![Tests](https://img.shields.io/badge/Tests-3190%20Passing-success)]()
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
- **State round-trip, and somewhere to put it.** `capture` / `restore` turn `OMSState`, `PortfolioState`, `LifecycleState`, `AllocationState` and — as of v2.9 — `ExecutionPipelineState`, `SessionState` and `BacktestState` into typed snapshots and back, through typed decoders that name the field when a payload is wrong. A snapshot records live objects (strategy instances, the simulator, the sizing model, the fill policy) by type and requires the caller to supply them back, raising rather than substituting. `TradingSession.resume` / `BacktestEngine.resume` continue a restored run on the identifier stream it left off on. As of **v2.13**, `alphalab.persistence.RunStateStore` stores those payloads durably — `FileRunStateStore` on the local filesystem, `MemoryRunStateStore` as an explicitly named test double — so a run can stop in one process and finish in another, byte-identically. `alphalab.market.provider` feeds the execution path from a market-data provider's history rather than only from a stored dataset.
- **Standalone engine libraries.** Most other packages (research, portfolio optimizer, feature store, factor library, ML / deep learning / RL, options / futures / crypto / macro, alternative data, cloud research, cluster scheduler, enterprise, studio, workbench) are independent, deterministic, individually tested libraries. They share the engineering model but are **not** currently fused into a single runtime.

The framework is designed for researchers, quantitative developers, students, and engineering teams building reproducible trading infrastructure.

---

# Release Status

**Current Release:** **v2.13.0**

| Metric | Status |
|---------|--------|
| Python | 3.12+ |
| Version | 2.13.0 |
| Tests | **3190 Passing** |
| Static Typing | **Strict MyPy** (903 source files) |
| Linting | **Ruff Clean** |
| Package Build | ✅ Passing |
| Wheel Validation | ✅ Passing |
| Source Distribution | ✅ Passing |
| License | MIT |

v2.13.0 — "Durable Run State" — gives a captured run somewhere to go.

`capture` / `restore` have covered `ExecutionPipelineState`, `SessionState` and
`BacktestState` since v2.9, and v2.10 closed the last precondition with
`StrategyStateProtocol`. A run could be described completely and rebuilt exactly
— and could not be **put anywhere**. There were zero filesystem calls in the
package; `MemoryStorage` was the only `PersistenceProtocol` implementation and
lost everything on exit; and nothing in production imported the snapshot modules
at all. The one object claiming to own recovery, `production.Checkpoint` with
`RecoveryEngine`, held six opaque strings and restored nothing.

`alphalab.persistence.RunStateStore` is the one owner of durable run state:
four methods over `(run_id, sequence) → payload`. It is **payload-agnostic** —
it moves a `str`, imports no snapshot type and never decodes one — which is what
keeps it correct through the runtime unification ADR-0023 anticipates.
`FileRunStateStore` is the real local backend: standard library only, atomic
temp-file-and-rename writes, a digest verified **before any decoder runs**, and a
root that must already exist. `MemoryRunStateStore` is an explicitly named test
double a caller constructs by hand — never a fallback, and the file store never
degrades into it. `RunStateRef(run_id, sequence)` is an **identity, not a
location**: it renders `run_id@sequence` and carries no path, so a second backend
would change no caller.

The run identity is **yours**: an opaque string supplied at `put`, never written
into any captured state, which is why no snapshot schema moves. And persistence
**draws no identifier** from the run's stream — the deprecated store took one per
operation, and the two it took were the run's own next two — so a mid-run
checkpoint leaves a seeded run reproducible.

Proven rather than asserted: a seeded run of five records, stored to disk,
continued in a **separate interpreter** over six more, produces a final
serialized payload **byte-identical** to a run that never stopped. Storage is an
explicit caller action between completed steps; nothing on the execution path
calls it, and there is no append-per-event API.

The original nine-module persistence store is **deprecated, removed in v3.0**,
with the codec spine (`serializer`, `decode`, `exceptions`) canonical and
untouched. Nothing is removed in v2.13.

**No FX, no multi-currency valuation, no `ArtifactStore` or artifact bytes, no
cloud storage, no streaming, no live venue transport, no governance/RBAC
implementation, and no runtime rewrite.** Runtime unification remains the next
architectural seam. See ADR-0029.

v2.11.0 — "The Security Master" — makes the instrument registry authoritative
for runs that *work*, not only for runs that fail.

`InstrumentRecord.sector` has existed since v2.7, outside the identity key by
ADR-0016 N5 so that a later classification could never re-identify an instrument
and orphan its fills. It was also unwritable: `register_instrument` refuses a
record whose content differs from one already held, so a field documented as
mutable was in practice immutable. `classify_instrument` is that write — keyed
by `asset_id`, sector only, `None` to unclassify, copy-on-write and `O(1)`, and
structurally incapable of touching an identity field.

The pipeline reads it **once per fill**, in `_apply_report_to_portfolio`, and
freezes the answer onto that fill's `TradeRecord.sector_id`. That site is the
one a simulated fill and a venue fill share, so backtest, replay, paper and live
agree on the sector by construction. `pnl_by_sector` produces a real breakdown
for the first time, and `ExposureStatus.sector_exposure` — declared, persisted
and decoded since before v2.6, populated by nothing — is filled from the same
authority, on signed market value, in the pass that already walked the
positions.

Two facts, two owners, two tenses: the registry says what an instrument **is**
classified as; a trade record says what it **was** classified as when the fill
happened. A later reclassification cannot rewrite a completed run, and a run
reclassified mid-run correctly splits across both sectors.

**No schema constant moves** — all seven stay where v2.10 left them — no
identifier is drawn, `alphalab.analytics` is untouched because the consumer was
already correct, and a run configured with no registry is byte-identical to
v2.10.0. AlphaLab still ships no classification data: the operator declares a
sector the way they already declare an instrument. See ADR-0027.

v2.10.0 — "The Strategy Boundary" — closes both halves of the one surface v2.9
could not: what a strategy tells the runtime about itself, and what the runtime
tells a strategy about the world.

`StrategyStateProtocol` lets a strategy declare durable internal state and
supply a two-sided codec for it, so a run that stops and continues restores a
**fresh** strategy instance's memory and reproduces an uninterrupted run exactly
— the one precondition v2.9's equivalence guarantee stated and no protocol could
express. The codec is two-sided because the shared encoder writes a `Decimal`
and a `str` identically and no generic decoder can tell them apart afterwards;
capture puts the state through that encoder immediately, so an unencodable value
is refused where it was produced rather than at some later `serialize`.
`PIPELINE_SNAPSHOT_SCHEMA` moves to **2** and reads version 1 as "no strategy was
asked"; `SESSION_` and `BACKTEST_SNAPSHOT_SCHEMA` do not move.

`StrategyContext` is populated by the pipeline for the first time since it was
written. A strategy now sees the **marked** portfolio — the book as of the event
being dispatched, after mark-to-market and the risk resync — its own **live
order shares**, risk headroom and a market view. Order attribution comes from
the allocation contribution ledger rather than `OrderBook.orders_for_strategy`,
which answers nothing for a netted order: two strategies whose intents net into
one `BUY 100` each see their own share and neither claims sole ownership. The
caller's `context_factory` keeps its signature and its `clock`, `logger` and
`config`; the pipeline overlays only what it owns. `history` and `universe`
remain deferred and say so.

**No live trading is added**, and no schema outside the pipeline envelope moves.

v2.9.0 — "Durable Run State" — let a run stop and continue. Every *quantity*
already survived a round trip — cash, positions, realized P&L, reservations,
contributions, risk NAV — but nothing recorded where the identifier stream had
reached, so a run that stopped and resumed rebuilt its generator at zero and
re-minted identifiers it had already used: up to **4 duplicates** in a
41-identifier workload, against zero for a run that never stopped, with nothing
raising at any layer. `capture` / `restore` now cover `ExecutionPipelineState`,
`SessionState` and `BacktestState` alongside the portfolio, the OMS and a new
allocation snapshot; a venue fill delivered twice is applied once; a working
external order the venue has ended can be ended here; `OMSState` payloads
declare a schema version and one bounded legacy shape; `alphalab.persistence`
appends in linear time (32,000 appends: **14.0 s → 0.24 s**); and `restore`
re-runs the construction-time validations `initialize` enforces, so v2.8's
currency invariant holds on both paths into a pipeline state. Its equivalence
guarantee was conditional on strategy-internal state the caller restored, which
`StrategyProtocol` gave no way to express; **v2.10 closes that precondition for
a strategy that declares its state**, and leaves it stated for one that does not.

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

## Instrument identity on the production path

Everything reaching the execution path crosses `alphalab.market.normalization`,
and as of v2.7 that boundary resolves identity through an authority rather than
passing a provider symbol through:

```text
(provider, symbol)  →  InstrumentRegistry  →  canonical asset_id (deterministic UUID)
```

An `asset_id` is opaque and *derived*, not minted — `uuid5` over a canonical key
(`asset_type`, `exchange`, `symbol`, `currency`) under a frozen namespace — so
two independently configured environments agree on the identity of one
instrument with no shared database. Registration is explicit: derivation alone
would turn every typo into a new instrument.

```python
from alphalab.core.enums import AssetType
from alphalab.instrument import InstrumentRecord, InstrumentRegistry, register_instrument
from alphalab.market.normalization import NormalizationPolicy

instruments = register_instrument(
    InstrumentRegistry(),
    InstrumentRecord(
        "BTCUSDT", AssetType.CRYPTO, "BINANCE", "USDT", aliases={"binance": "BTCUSDT"}
    ),
)
policy = NormalizationPolicy(
    venue="BINANCE", currency="USDT", identity=instruments, provider="binance"
)
```

**A production provider → execution path requires `InstrumentRegistry`-backed
resolution.** An unregistered `(provider, symbol)` is refused at the boundary
with `InstrumentResolutionError`, naming both — not carried onward to fail at the
first fill.

The second mode, `UnresolvedIdentity`, keeps the wire → canonical lift testable
without a registry. **It is not a production execution configuration**: it yields
provider symbols, which `core.Fill` and `core.Trade` refuse, and
`ProviderHistorySource.of` rejects it before calling the provider. `DEFAULT_POLICY`
uses this mode and is therefore a testing default.

`core.Fill` / `core.Trade` UUID validation is unchanged. See ADR-0016.

As of v2.11 the registry is read a second time, on the **fill** path rather than
the wire boundary: `ExecutionPipeline` asks it what sector the filled asset is
classified as and freezes that answer onto the fill's `TradeRecord.sector_id`.
Only `record_for` is ever called — the pipeline still resolves no provider
symbol, derives no `asset_id` and classifies nothing. Configuring no registry
remains fully supported and simply leaves the sector `None`. See ADR-0027.

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
├── instrument/      Canonical instrument identity, registry, classification
├── common/          Shared version, events, serialization, constants
├── backtesting/     Dataset → execution path → analytics (backtest + replay)
├── replay/          Deterministic replay cursor (drives backtesting)
├── lifecycle/       Research → model → strategy version → promotion → deployment
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

- ✅ 2926 passing tests (1782 unit, 109 integration, 1035 regression)
- ✅ Strict MyPy type checking (958 source files)
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
`TradingSession` through the v2.3 normalization boundary (as of v2.7 it requires
`InstrumentRegistry`-backed identity resolution — see below); explicit
`SessionConfig.ordering` semantics for unordered sources; terminal semantics for a
partially filled simulated order's remainder, with its reservation released; and the
removal of the replay cursor's O(N²), with the benchmark repointed at the API the
integrated path actually uses.

**v2.6.0** — allocation authority and attribution truth: the budget guard counts
outstanding commitment, so `EXTERNAL` routing can no longer over-commit across
events; a terminal order releases whatever a fill priced away from the reference
price left behind; `StrategyContribution` carries who asked for a netted order
through to `TradeRecord`, and `pnl_by_strategy` splits by signed contribution;
`Position.opened_at` makes holding periods real; `pnl_by_sector` is empty rather
than fictional; the portfolio snapshot moves to schema 2 and refuses version 1
without a migration framework; and `integrations`, `kernel`, `core.events` and
`CommonEvent` are deprecated for v3.0 removal. See ADR-0015.

**v2.7.0** — instrument identity and dataset provenance: `alphalab.instrument`
becomes the authority for what a provider symbol means, deriving a canonical
`asset_id` deterministically (`uuid5` over a fixed key under a frozen namespace)
so two independently configured environments agree with no shared database; an
unregistered symbol is refused at the normalization boundary naming provider and
symbol, instead of failing at the first fill; a run records the dataset or source
it consumed (`BacktestResult.dataset_id`, `SessionState.source_id`); and
`BACKTEST` evidence derives that identity from the run rather than accepting a
caller's claim — with `evidence_id_for` byte-identical to v2.6, so evidence
recorded then still verifies and still passes its policy.

**v2.8.0** — currency roles and run outcomes: `ExecutionPipeline.initialize`
refuses a configuration whose `currency` and `Account.base_currency` disagree,
which silently zeroed buying power and NAV and switched off the leverage and
margin checks; `PortfolioValuation.snapshot` refuses a book spanning two
currencies instead of adding them together under one label; a run records the
assets it declined to trade for want of a price, aggregated per asset and
surfaced on `BacktestResult`, `ReplayResult` and `SessionState`, optionally
distinguishing an unregistered identifier from an unpriced instrument through a
read-only `InstrumentRegistry` reference; an allocation contribution is retired
wherever a request's lifecycle ends rather than only where an order produced a
report; and `LIFECYCLE_SNAPSHOT_SCHEMA` becomes a literal without moving its
value. No breaking changes, no schema movement, no migration, and no FX.

**v2.9.0** — durable run state: `capture` / `restore` / `from_primitives` for
`ExecutionPipelineState` (`PIPELINE_SNAPSHOT_SCHEMA`), `SessionState`
(`SESSION_SNAPSHOT_SCHEMA`), `BacktestState` (`BACKTEST_SNAPSHOT_SCHEMA`) and
`AllocationState` (`ALLOCATION_SNAPSHOT_SCHEMA`), with live objects recorded by
type and required back from the caller rather than reconstructed;
`IdStreamPosition` giving the deterministic identifier stream the cursor it
never had, so a continued run no longer re-mints identifiers it already used;
`TradingSession.resume` / `BacktestEngine.resume` opening the scope a restored
position implies; a duplicate `execution_id` applied once rather than twice;
`ExecutionPipeline.apply_terminal_outcome` ending a restored external working
order and retiring its reservation and contribution; `OMS_SNAPSHOT_SCHEMA = 1`
with a bounded exact-five-key legacy shape and no generic "missing means
version 1"; `alphalab.persistence` migrated to `AppendOnlyLog` / `PersistentMap`
/ `PersistentSet`, turning a quadratic append linear; and `restore` re-running
every construction-time validation `initialize` enforces. No breaking changes,
no migration, no venue transport, and `PORTFOLIO_SNAPSHOT_SCHEMA` /
`LIFECYCLE_SNAPSHOT_SCHEMA` do not move.

**v2.10.0** — the strategy boundary: `StrategyStateProtocol`, a second and
separate protocol a strategy satisfies to declare durable internal state, with a
required two-sided codec (`strategy_state_version` / `capture_state` /
`restore_state`) whose encode side is validated and normalized through the
existing `DeterministicEncoder` at capture rather than at some later
`serialize`; `PIPELINE_SNAPSHOT_SCHEMA = 2` carrying a three-valued
`StrategyRecord.state` — absent means a version-1 payload nobody asked, `null`
means declared none, an object means declared — with version 1 still readable
and four reconciliation mismatches refusing the whole restore; deterministic
continuation into a **fresh** strategy instance at every record boundary, adding
no identifier draws; `StrategyContext` populated by the pipeline from the marked
portfolio and resynced risk locals, with strategy-scoped live order **shares**
read from the allocation contribution ledger rather than the OMS strategy index,
read-only risk and market views, and construction only for a running strategy;
the caller's `context_factory` signature, `clock`, `logger` and `config`
preserved. No breaking changes, no migration, no venue transport, and
`SESSION_`, `BACKTEST_`, `ALLOCATION_`, `OMS_`, `PORTFOLIO_` and
`LIFECYCLE_SNAPSHOT_SCHEMA` do not move.

**v2.11.0** — instrument classification and sector provenance:
`classify_instrument` / `classify_instruments` give `InstrumentRegistry` the
write ADR-0016 N5 kept the identity key clear for, keyed by `asset_id` and
writing `sector` alone — `None` unclassifies, the label already in effect
returns the same registry object, and the write is copy-on-write and `O(1)`;
`sector` stays outside the canonical key, so a reclassification cannot
re-identify an instrument or orphan its fills, and the classification API
exposes no identity field; `normalize_sector_label` refuses a blank,
whitespace-only, control-character or non-string label while permitting internal
whitespace and non-ASCII and preserving case, because a sector derives no
identifier; `ExecutionPipeline` reads the registry once per **fill**, on the one
path a simulated fill and a venue fill share, and freezes the answer onto
`TradeRecord.sector_id`, so a later reclassification never rewrites a completed
run and a run reclassified mid-run splits across both sectors; `pnl_by_sector`
produces a real breakdown for the first time and `ExposureStatus.sector_exposure`
is populated on signed market value, both from the same authority, with
`alphalab.analytics` untouched because the consumer was already correct. No
breaking changes, no migration, no venue transport; all seven snapshot schema
constants hold, identifier draw counts are unchanged, and a run configured with
no registry is byte-identical to v2.10.0. **AlphaLab ships no taxonomy or
reference data** — the operator declares a sector the way they already declare an
instrument — and sector is the only classification dimension. See ADR-0027.

**v2.12.0** — the currency authority: `InstrumentRecord.currency` decides what a
run may trade. A EUR-registered instrument traded on a USD pipeline used to
produce `Position.currency == "USD"` — a position labelled with a currency the
instrument does not trade in, silently — because `_instruction` stamped
`ExecutionPipelineConfig.currency` onto every instruction and nothing consulted
the registry. Two seams close it, answering different questions: `_process_requests`
asks whether this run may *trade* an instrument, needs the registry, and **drops**
the request before the OMS, retiring both allocation ledgers and reporting a
`SettlementRefusal` on `ExecutionPipelineResult`; `_apply_report_to_portfolio`
asks whether a report is denominated in what the pipeline *settles*, needs no
registry, and **raises** `RuntimeValidationError`, because a venue fill has
already happened and cannot be declined. The second closes `RoutingConfig.currency`,
a fifth currency site ADR-0019 did not name and nothing compared to anything, and
through which a live sell used to mix a book silently. `STRICT_MATCH` is the only
rule and there is no policy object: a permissive mode could only book honestly —
making the book mixed, which the next snapshot refuses — or convert, which is FX.
`assert_single_currency_book` becomes the one implementation of the mixed-book
rule, and `NAVCalculator.calculate`, `PortfolioValuation.portfolio_value` and
`_risk_exposure`/`sector_exposure` now refuse a book they cannot express,
discharging ADR-0020 decision 5 earlier than it expected — on measurement rather
than on a rate source. `long_value` and `short_value` deliberately do **not**:
they name no base currency, so they are components of a valuation rather than
valuations. `_currency_of` mirrors `_sector_of` — one keyed `record_for`, no scan,
no identifier — and the authority is **opt-in**: a run with `instruments=None`
behaves exactly as v2.11. No breaking changes beyond two deliberate correctness
refusals, no migration, no new identifier draw; all seven snapshot schema
constants hold and a single-currency run is byte-identical to v2.11.0. **No FX
rate source, no conversion, and no multi-currency aggregation** — a mixed book is
still refused, not valued. See ADR-0028.

**v2.13.0** — durable run state and the run-state store:
`alphalab.persistence.RunStateStore` over `(run_id, sequence) → payload`, with
`FileRunStateStore` (stdlib, atomic writes, digest verified before decode, no
fallback) and the explicitly named `MemoryRunStateStore`; `RunStateRef` as an
identity rendering `run_id@sequence` and carrying no backend location;
`RUN_STATE_ENVELOPE_SCHEMA = 1` as the only new constant, with all eight existing
schema constants unmoved; a caller-supplied opaque `run_id` that is never written
into captured state; zero identifiers drawn from the run's stream on any path;
cross-process continuation proven byte-identical against an uninterrupted run;
`__serializable__` resolved on the type for a 3.70× faster, byte-identical
encode; the nine-module placeholder store deprecated for v3.0 removal with the
codec spine canonical; and `production.Checkpoint` / `RecoveryEngine` documented
as the bookkeeping they are. No FX, no artifact storage, no streaming, no live
transport, no governance implementation, no runtime rewrite. See ADR-0029.

See `CHANGELOG.md` and `ROADMAP.md`.

## Not yet addressed

- Connectivity to a real venue for **order execution**. v2.3 built and tested the
  adapter contract, the routing gates, reconciliation and the fill-return path; no
  broker transport exists, and the `integrations` broker clients are canned-response
  stubs. (Market *data* is different — see the note above)
- A **streaming** market-data source. v2.5's provider source reads a finite historical
  range; polling, subscription and reconnect need a clock and a loop AlphaLab does not
  have
- Artifact storage. `ArtifactRef` records where a model's bytes live and what they
  should hash to; AlphaLab never reads, writes or hashes them, and there is no
  object store
- **Classification data.** v2.11 supplies the security master's *mechanism* —
  `classify_instrument` writes a sector and the execution path reads it onto
  every fill — but AlphaLab ships no taxonomy and no reference-data feed, so a
  sector breakdown requires an operator who declares one. Sector is also the
  only dimension: industry, country, issuer and rating are each a separate
  decision
- A single integrated runtime spanning *all* engines (`ExecutionPipeline`,
  `backtesting`, `runtime.session` and `lifecycle` are what is wired today, and
  the lifecycle is not joined to the execution path)
- Approval workflow. A promotion is an auditable privileged action, but it is not
  wired to `alphalab.enterprise`'s RBAC or audit log
- `StrategyContext.history` and `.universe`. v2.10 populates the marked
  portfolio, the strategy's live order shares, risk headroom and a market view;
  a clock-bounded historical accessor needs a look-ahead guard enforced at
  construction, and universe membership needs a decision about whether it is
  configuration, registry state or a risk control
- Allocation visibility inside `StrategyContext`. Reservations and contributions
  are post-intent facts; showing a strategy the capital its own intent will
  reserve invites it to pre-size, duplicating the allocation engine's authority
- Multi-currency valuation. `PortfolioValuation.snapshot` has refused a book it
  cannot express as one figure since v2.8, and as of v2.12 so do
  `NAVCalculator.calculate`, `portfolio_value` and `_risk_exposure`; valuing
  *across* currencies needs an FX rate source that does not exist here. A run
  settles in exactly one currency, which v2.12 makes explicit: a pipeline whose
  settlement currency is EUR trades EUR instruments end to end, and one whose
  settlement currency is USD refuses them rather than mis-booking them.
  `PortfolioEngine` and `CashLedger` remain multi-currency and a wholly foreign
  book still values in its own currency, but **a pipeline run cannot hold two
  currencies at once** — a second one makes the next portfolio snapshot raise.
  Earlier releases described the engine's capability as though it were the
  pipeline's; it never was

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

**AlphaLab v2.11.0**

Building deterministic infrastructure for quantitative research.

</div>