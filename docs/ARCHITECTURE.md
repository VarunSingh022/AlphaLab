# AlphaLab Architecture

## Overview

AlphaLab is an institutional-grade quantitative research and algorithmic trading platform built around deterministic execution, immutable state, and event-driven architecture.

Every subsystem follows the same engineering principles (immutable state, pure functional engines, deterministic execution). They are designed to compose through well-defined interfaces, but only `alphalab.runtime.ExecutionPipeline`, the `alphalab.runtime.run.RunEngine` that owns a run over it, and the drivers that feed it — `alphalab.runtime.session`, `alphalab.backtesting`, `alphalab.backtesting.replay` and `alphalab.runtime.live` — together with `alphalab.lifecycle`, which v2.16 joined to it, actually wire a group of them together. See the **Implementation Status (v3.1)** section below.

> **How to read this document.** The **Implementation Status** section and
> everything up to *Known boundaries* describe what is **built**. From
> **Design Goals** onward the document describes the architectural *model* —
> principles, layering rules, extension points and a long-term target. As of
> v3.3.0 both halves name only packages that exist; where the target half shows a
> capability AlphaLab does not implement, it says so.

The architecture emphasizes reproducibility, composability, testability, and production readiness.

Every component—from market data ingestion to production deployment—is designed to operate deterministically, enabling researchers to reproduce results across development, testing, and live environments.

---

# Implementation Status (v3.3)

Most of this document describes the **target** architecture. This section states
what is actually built so the two are not confused.

*(It was headed "v2.5" from v2.5 through v2.16 and still said the two paths below
were unjoined, which v2.16 made false; the v2.17 audit corrected that. The v3.0
audit read the rest of this document and found the same defect class throughout
its target-architecture half — see the v3.0.0 entry in `CHANGELOG.md` for the
full list. The release checklist now has to touch `README.md`, this section and
`docs/README.md` together.)*

**v3.1 (ADR-0036)** added the universal data-ingestion path inside
`alphalab.data` — source provenance, CSV reading, schema detection, structured
validation, cleaning under an explicit policy, quality reporting, market
calendars, multi-asset semantics, the raw/adjusted price basis, and a derived,
immutable dataset version. It moved no boundary and changed no owner, and the
derived version reaches `BacktestResult.dataset_id` and `ValidationEvidence`
without the evidence digest moving. `alphalab.api` is the
application-facing surface. See the **Universal Data Engine** section below.

There are **two** wired-together paths, and as of v2.16 they meet:

| Path | Package | Answers |
| --- | --- | --- |
| Execution | `runtime.ExecutionPipeline`, owned per run by `runtime.run.RunEngine`, driven by `runtime.session`, `backtesting`, `backtesting.replay` and `runtime.live` | what happens to one market event |
| Lifecycle | `alphalab.lifecycle` | which strategy version an environment should be running, and why |

A deployment names what should run; running it is the execution path's job.
**v2.16 joined them** with `lifecycle.execution`: `run_plan` resolves what an
environment has live and `authorize_run` refuses a run that would serve anything
else. That join is a query with a refusal, not a second runtime — it builds no
state and starts nothing. **v2.17 supplies its missing half**:
`strategy.registry` maps the identity a deployment names to the code that runs
it, deterministically and with a refusal, and it deliberately does not live in
`alphalab.lifecycle` (ADR-0035).

What is built, by release: the execution path can be fed by a market-data
provider rather than only a stored dataset (v2.5, `market.provider`) or by a live
stream (v2.15, `market.stream`); every state on it captures and restores as a
typed value and lives durably in a `RunStateStore` (v2.5 through v2.13); a run is
owned by `RunEngine` and driven by four interchangeable drivers (v2.14); orders
reach a real venue over signed HTTP and fills come back through the same path a
simulated fill takes (v2.15, v2.16); every act that changes what is live names
its principal (v2.16); and a run settles in more than one currency, reporting in
one, with rates that arrive across a feed boundary (v2.17). **v3.1.0 adds the
universal data-ingestion path inside `alphalab.data` and moves no boundary
(ADR-0036).** **v3.2.0 adds the strategy research and validation path inside
`alphalab.factor_library` and `alphalab.research`, plus one statistics module in
`alphalab.common`, and moves no boundary (ADR-0037).** **v3.3.0 adds the
institutional surfaces — itemized execution costs and capacity inside
`alphalab.execution`, attribution dimensions and risk decomposition inside
`alphalab.analytics`, one new standalone package `alphalab.scenario`, and one
function in `alphalab.common.statistics` — and moves no boundary (ADR-0038).** **v3.0.0 adds no
capability**: it freezes the architecture described here and makes the
documentation match it.

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

`ExecutionPipeline.process_record` is the canonical step (v2.3): publish one
`MarketRecord`, then process the event it produced. Every environment takes it.

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

`backtesting.engine.advance` wraps `RunEngine.advance` with the run's own
bookkeeping, and two of AlphaLab's four drivers live in this package:

| Driver | Cursor | Everything else |
| --- | --- | --- |
| `BacktestEngine.run` | iterates `dataset.records` | `advance` |
| `ReplayBacktest.run` | `ReplayEngine.step_one_event` | `advance` |

The other two are `runtime.session.TradingSession` over any `MarketDataSource`
and `runtime.live.LiveSession` over a venue. All four call the same step.

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
`RunConfig.seed` installs a `DeterministicIdSource` for the run and is
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

## Market data: one domain model, one wire record, one boundary (v2.3)

Five market-data surfaces coexisted at v2.2. Two of them were the same surface
written twice. v2.3 keeps the ones that answer different questions and collapses
the ones that did not.

| Layer | Package | Shape | Who fills it in |
| --- | --- | --- | --- |
| **Canonical domain** | `alphalab.market` | `Decimal`, `asset_id`, venue / currency / timeframe / sequence | AlphaLab, after normalization |
| **Wire record** | `alphalab.data.feed` | `float`, provider `symbol` | a provider, knowing nothing about AlphaLab |
| **Provider message** | `alphalab.live.message` | wire shape plus a `provider_id` tag | a provider, for a layer that routes by provider |

`alphalab.marketdata.feed` re-exports the wire records; before v2.3 it defined
field-for-field identical copies of all five, and `alphalab.live.message`
defined a third identical `OrderBookLevel`. Those are now the same class
objects, not merely equal shapes.

`data.Bar` and `market.Bar` both remain, deliberately. They sit on opposite
sides of a conversion: one is what a provider can send, the other is what the
execution path consumes. Merging them would force one to claim fields it does
not have.

### The canonical market-data types

| Concept | Canonical type |
| --- | --- |
| Top of book | `alphalab.market.quote.Quote` |
| Trade print | `alphalab.market.tick.Tick` |
| OHLCV bar | `alphalab.market.bar.Bar` |
| Depth book | `alphalab.market.snapshot.OrderBookSnapshot` |
| Book level | `alphalab.market.level.OrderBookLevel` |
| Stream record | `alphalab.market.record.MarketRecord` |

`MarketInput` / `MarketRecord` moved from `alphalab.backtesting.dataset` to
`alphalab.market.record` (re-exported unchanged), so a live feed adapter can
produce a record without importing the backtesting package.

### Normalization rules — `alphalab.market.normalization`

Everything reaching the execution path crosses this boundary.

| Rule | Behaviour |
| --- | --- |
| Precision | `Decimal(str(value))`, never `Decimal(value)`. `Decimal(0.1)` keeps the float's binary expansion; going through `str` keeps the number the provider wrote. This is what makes normalization deterministic. |
| Quantization | None. The venue's precision is preserved; rounding is a downstream decision. |
| Timestamps | Unix seconds as `float`, passed through. Must be strictly positive. |
| Identity | `(provider, symbol)` → `InstrumentRegistry` → a canonical, deterministic UUID `asset_id`. The registry is the production identity authority; an unregistered pair is refused here with `InstrumentResolutionError`, naming the provider and the symbol, rather than travelling on as an unusable identifier. See ADR-0016. |
| Venue / currency / timeframe | Not on the wire. Supplied by an explicit `NormalizationPolicy`, which names an unattributed venue `"UNKNOWN"` rather than guessing. |
| vwap, trade count, order counts | Not reported by a wire record. Set to zero and documented as *unreported*, not measured. |
| Trade direction | Not represented. A wire trade carries no aggressor flag, and none is inferred. |
| Book levels | Passed through in provider order; sequence is supplied by the caller, because `MarketEngine.publish_book` refuses a non-advancing sequence. |
| Invalid data | Raises `MarketValidationError` at the boundary, not deeper in the path. |
| Stale data | Not an error. `is_stale` / `reject_stale` let the caller decide, because how old is too old is a strategy property. |

#### Identity resolution — `alphalab.instrument`

An `asset_id` is opaque, UUID-shaped and *derived*, not minted: `uuid5` over a
canonical key (`asset_type`, `exchange`, `symbol`, `currency`) under a frozen
namespace. Two independently configured environments therefore agree on the
identity of one instrument with no shared database. Registration is still
required — derivation alone would turn every typo into a new instrument.

A `NormalizationPolicy` resolves identity in one of exactly two named modes.
There is no third, and there is no `None`.

| Mode | Behaviour | Permitted use |
| --- | --- | --- |
| `InstrumentRegistry` | Resolves `(provider, symbol)`; refuses an unregistered pair | The only mode permitted on any path that can reach a `Fill` or `Trade` |
| `UnresolvedIdentity` | Passes the provider symbol through, optionally via a `SymbolMap` | Low-level testing of the wire → canonical lift **only** |

`UnresolvedIdentity` is **not a production execution configuration**: the values
it produces are provider symbols, which `core.Fill` and `core.Trade` refuse.
`ProviderHistorySource.of` rejects it before calling the provider, so a
misconfigured source costs no request. `DEFAULT_POLICY` uses this mode and is
therefore a testing default, not a production one.

`core.Fill` / `core.Trade` UUID validation is **unchanged**. ADR-0016 supplies a
producer that can satisfy the existing invariant; it does not relax it.

### The market-data adapter boundary — `alphalab.market.source`

A `MarketDataSource` yields canonical `MarketRecord`s and nothing else, so the
execution path cannot tell a stored file from a socket. `OrderingGuarantee`
lets a source declare whether it can promise chronological order — a stored
dataset can, a venue feed cannot. No provider API is modelled here.

See ADR-0011.

## The broker boundary and the four environments (v2.3)

`alphalab.broker` is the canonical broker adapter boundary: the vocabulary
every adapter speaks (`BrokerOrder`, `BrokerExecution`, `BrokerAccount`,
`BrokerPosition`, `ConnectionStatus`) and the contract it implements
(`BrokerProtocol`: submit, cancel, replace, order status, execution reception,
account, positions, connectivity). `alphalab.brokers` is the multi-broker
router *above* that boundary and routes those types rather than redefining
them.

An order carries two identifiers — `oms_order_id` (AlphaLab's) and
`broker_order_id` (the venue's handle) — and a fill carries `external_id` (the
venue's own record). Distinguishing them is what makes reconciliation possible.

### Reconciliation — `alphalab.broker.reconciliation`

| Situation | Answer |
| --- | --- |
| Fill redelivered after a reconnect | `DUPLICATE`. A no-op, not an error. |
| Fill for an order this process never sent | `UNKNOWN_ORDER`. Never applied, always surfaced. |
| Fill against a terminal order | `TERMINAL_ORDER`. Recorded as a break — applying it would resurrect the order, dropping it would hide a position. |
| Fill exceeding the ordered quantity | `OVERFILL`. Refused, not truncated. |
| Two fills out of order | Applied. Fills are additive, so either order gives the same result. |
| Fill then cancel | Order fills; the cancel is refused. |
| Cancel then fill | Order stays cancelled; the fill is surfaced as a break. |
| Local and venue disagree | `reconcile()` **states** the differences and does not resolve them. |

### Parity across historical, replay, paper and live

| Layer | Backtest | Replay | Paper | Live |
| --- | --- | --- | --- | --- |
| Market record / event | same | same | same | same |
| Strategy → intents | same | same | same | same |
| Allocation → `OrderRequest` | same | same | same | same |
| Risk → `RiskDecision` | same | same | same | same |
| OMS order lifecycle | same | same | same | same |
| **Execution venue** | simulator | simulator | simulator | **broker** |
| `Fill` / portfolio / analytics | same | same | same | same |
| Record source | dataset | replay cursor | live source | live source |
| Clock | record ts | record ts | wall | wall |
| Staleness gate | none | none | optional | optional |

`alphalab.runtime.session.TradingSession` drives any `MarketDataSource` through
`ExecutionPipeline.process_record`. Paper is a backtest that reads from a live
source — no paper-only accounting, order model or fill model.

Live's one genuine difference is `ExecutionRouting.EXTERNAL`: an accepted order
stays working instead of being simulated, no fill is invented, and its
allocation reservation stays held because the capital is still committed.
`alphalab.runtime.broker_routing` carries an order out (`route_order`) and a
fill back (`apply_broker_execution`), and the return leg goes through
`ExecutionPipeline.apply_execution_report` — the same function a simulated fill
uses.

Two pre-trade gates: an order is never sent on a connection that is not
`CONNECTED`, and an OMS order already bound to a venue handle is never sent
again. The client order id is *derived* from the OMS order id, so a retry after
a lost response addresses the same order rather than creating a second one.

### What "live" does and does not mean here (updated v2.15)

| | Status |
| --- | --- |
| Backtest, replay, paper | **Implemented**, end to end, tested |
| Canonical broker vocabulary and `BrokerProtocol` | **Implemented** |
| `PaperBroker` | **Implemented** — a simulation, and the reference adapter |
| Routing, fill return, reconciliation, pre-trade gates | **Implemented and tested** |
| **A transport that reaches a venue** | **Implemented (v2.15).** `alphalab.broker.transport.HttpVenueTransport` — authenticated JSON-over-HTTP with HMAC request signing, on the standard library |
| **A `BrokerProtocol` adapter over it** | **Implemented (v2.15).** `alphalab.broker.venue.RestVenueBroker` — submit, acknowledge, reject, cancel, replace, poll fills, reconcile, recover |
| **A streaming market-data source** | **Implemented (v2.15).** `alphalab.market.stream.StreamingSource` over `alphalab.marketdata.websocket`, an RFC 6455 client |
| Verification against a commercial venue | **Not done, and cannot be here.** This environment has no network egress and holds no vendor credentials |
| Vendor market-data clients | **One real, four not.** `alphalab.marketdata.binance` is a real REST client over the shared HTTP transport, parsing `/api/v3/klines`, `/bookTicker`, `/trades` and `/depth` — real since v1.39.0 and **unverified from this environment**, which is not the same as a stub. `databento`, `nse`, `polygon` and `yahoo` raise `NotImplementedError` rather than returning fabricated data |
| Vendor *broker* adapters | **None.** The canned-response Alpaca / IB / Zerodha clients lived in `alphalab.integrations` and were removed in v2.17 (ADR-0034). Implementing one means implementing that venue's request shapes over `HttpVenueTransport` |
| **A live driver** | **Implemented (v2.16).** `alphalab.runtime.live.LiveSession` — settle the fills the venue reported, advance the run, route what is newly working — with the venue binding made durable by `alphalab.broker.snapshot`. See ADR-0033 |
| A supervised live *process* | **Not implemented.** Supervision — restart policy, alerting, scheduling — is an operator's concern and AlphaLab has no opinion about it. `live_health` answers "should a human look at this?"; acting on the answer is the caller's |

**What changed in v2.15, precisely.** AlphaLab now contains a genuine venue
transport and a genuine streaming client, and both are exercised end to end over
real sockets against local servers that speak the protocols — the venue server
verifies the signature, the timestamp window and the idempotency key; the
market-data server computes the WebSocket accept token and sends real frames.
An order produced by the execution path reaches a venue, and a fill it reports
becomes a canonical `Fill` through `apply_execution_report`, the same function a
simulated fill takes.

**What is still true.** The transports are **written to protocol, not verified
against a vendor**, and both say so in their own docstrings. Pointing one at a
named venue additionally requires that venue's request shapes, which differ per
venue and belong to an adapter. Nothing here makes AlphaLab a turnkey live
trading system, and the honest summary is that the *connectivity* exists and the
*vendor integration* does not.

See ADR-0012 and ADR-0031.

## The model and strategy lifecycle: `alphalab.lifecycle` (v2.4)

The third integration package. It adds no engine, and no state that any of the
packages it composes already defines. It imports `experiment_tracking`,
`model_registry`, `deployment_manager`, `studio`, `enterprise`, `research` and
`backtesting`; `research_assistant` below is the producer of the candidate and is
**not** imported — the dependency runs through the `StrategyDefinition`.

```
research candidate         (research_assistant.generate_candidates)
   → StrategyDefinition    (research_assistant.to_strategy_definition — canonical)
   → experiment run        (experiment_tracking: parameters, metric history)
   → model version         (model_registry: staged, cites the run)
   → strategy version      (lifecycle.StrategyVersion: immutable, numbered)
   → validation evidence   (from analytics.PerformanceReport / research.ResearchScore)
   → promotion             (lifecycle.promote_strategy_version — gated)
   → deployment            (deployment_manager: checksummed release + env ledger)
   → rollback              (lifecycle.rollback_environment)
```

### The four identities it keeps apart

| Identity | Type | Not to be confused with |
| --- | --- | --- |
| Strategy line | `StrategyVersion.name` | any one version of it |
| Strategy version | `StrategyVersionRef(name, version)` | the model it runs |
| Model version | `ModelRef(name, version)` | the strategy version referencing it |
| Deployment | `DeploymentRef(environment, release, version)` | the version deployed — one version in two environments is two deployments |

References render as `"name@version"`, the form `deployment_manager` already
documented for release components, and a name containing `"@"` is refused at
construction. `ModelRef` and `StrategyVersionRef` render identically and do not
compare equal, which is the whole point of them being separate types.

### Stages, and what is refused

`ModelStage` (`NONE` → `STAGING` → `PRODUCTION` → `ARCHIVED`) is AlphaLab's one
stage vocabulary for a registered, promotable artifact, and stages both model
versions and strategy versions. It is unrelated to
`strategy.LifecycleState` (`CREATED` … `DISPOSED`), which tracks a strategy
*instance running inside a session*: a deployed strategy version is started and
stopped many times without its stage changing.

| Attempted move | Answer |
| --- | --- |
| `NONE → STAGING` without passing evidence | Refused by `alphalab.lifecycle` |
| `NONE → STAGING` when the model version is not itself staged | Refused — a strategy cannot be more validated than the model in it |
| anything `→ PRODUCTION` by promotion | Refused — a version goes live by being *deployed* |
| `PRODUCTION → STAGING` | Refused by `model_registry.stages` — a live version leaves production by being archived or replaced |
| `ARCHIVED → PRODUCTION`, version never live | Refused — a resurrection, not a rollback |
| `ARCHIVED → PRODUCTION`, version is the rollback target | Allowed — this *is* rollback |
| `PRODUCTION → ARCHIVED` while still live elsewhere | Refused — roll back or replace it |
| `anything → NONE` | Refused — `NONE` is initial-only |

`model_registry.promote` remains the mechanism and refuses only what is
incoherent; the evidence gate is policy and lives in `alphalab.lifecycle`.

### One source of truth for what is live

The `deployment_manager` ledger, and nothing else.
`StrategyVersionRegistry` carries no production index, deliberately — a second
copy would be a second thing to keep true. `model_registry.DeploymentMetadata`
remains as a *note* on a version, and the integrated path derives it from the
deployment that happened.

### Validation evidence

| Property | Behaviour |
| --- | --- |
| Where the numbers come from | `analytics.PerformanceReport` (via `BacktestResult`) or `research.ResearchScore`. Extracted, never recomputed |
| Identity | SHA-256 digest of method + subject + dataset + seed + sorted metrics, the same construction `compute_checksum` uses for a release manifest |
| Tampering | `verify_evidence_id` fails, and `evaluate_policy` checks it before reading any threshold |
| A metric the policy asks for and the evidence lacks | A failure. An absent number is not a passing one |
| Failures reported | All of them, in policy order — never just the first |
| What a pass claims | The stated thresholds were met. **Not** statistical significance, out-of-sample validity, or a multiple-testing correction |

### Artifacts (updated v2.15)

`ArtifactRef` records a location, media type, checksum and size, and
`ModelVersion.__serializable__` projects a version to metadata plus that
reference, so a registry snapshot is metadata and references by construction
rather than a stringified model object.

Until v2.15 the sentence here read "**AlphaLab never reads, writes or hashes
those bytes**; there is no object store here", and the `checksum` field was a
place for a number nobody computed.
`alphalab.model_registry.artifact_store` is the store that was missing:

```
bytes -> ArtifactStore.put -> ArtifactRef -> ArtifactStore.get -> verified bytes
```

**Identity is the content.** An artifact is addressed by the SHA-256 of its
bytes, so storing identical bytes twice is one artifact, two environments that
never shared a database agree on the identity, and a reference cannot name bytes
that hash to something else — verification is a tautology the store checks
rather than a claim it trusts. The URI is `alphalab-artifact:sha256:<hex>` and
never a filesystem path, so a reference written into a registry snapshot on one
machine says nothing about where that machine keeps its files.

`FileArtifactStore` is the real backend and `MemoryArtifactStore` the
deterministic double a caller constructs **by name** — the shape
`persistence.run_store` and `marketdata.transport` established. It is **not** a
second persistence owner: `RunStateStore` owns run state addressed by
`(run_id, sequence)` and holding a `str`; this owns artifact bytes addressed by
content. Neither can answer the other's question. See ADR-0031.

### What a deployment is not

It is a lifecycle fact. Making a release active in `"live-eu"` records that
`"live-eu"` should be running that strategy version. It starts no process, opens
no connection, and reaches no venue.

See ADR-0013.

## State round-trip: `capture` / `restore` (v2.5 onward)

Every AlphaLab state has serialized deterministically since v2.1. Until v2.5
exactly one could be read back: `deserialize()` returns `Any`, so a snapshot came
back as nested dictionaries, and only `alphalab.oms.snapshot` turned those into
typed values again. **Every durable state round-trips now**, each through its own
owning module, its own schema constant and its own typed decoder:

| State | Snapshot owner | Schema | Since |
| --- | --- | --- | --- |
| `OMSState` | `oms.snapshot` | `OMS_SNAPSHOT_SCHEMA = 1` | v2.2, versioned v2.9 |
| `PortfolioState` | `portfolio.snapshot` | `PORTFOLIO_SNAPSHOT_SCHEMA = 3` | v2.5 |
| `LifecycleState` | `lifecycle.snapshot` | `LIFECYCLE_SNAPSHOT_SCHEMA = 2` | v2.5 |
| `AllocationState` | `allocation.snapshot` | `ALLOCATION_SNAPSHOT_SCHEMA = 1` | v2.9 |
| `ExecutionPipelineState` | `runtime.snapshot` | `PIPELINE_SNAPSHOT_SCHEMA = 3` | v2.9 |
| `RunState` | `runtime.run_snapshot` | `RUN_SNAPSHOT_SCHEMA = 1` | v2.14 |
| `InstrumentRegistry` | `instrument.snapshot` | `INSTRUMENT_SNAPSHOT_SCHEMA = 1` | v2.15 |
| `BrokerState` | `broker.snapshot` | `BROKER_SNAPSHOT_SCHEMA = 1` | v2.16 |
| `LiveRunState` | `runtime.live_snapshot` | `LIVE_SNAPSHOT_SCHEMA = 1` | v2.16 |
| `FxFeedState` | `portfolio.fx_feed` | `FX_FEED_SNAPSHOT_SCHEMA = 1` | v2.17 |

The blocker this table used to record for the pipeline and the run — that they
hold `StrategyProtocol` instances, an `ExecutionSimulator` and a `SizingModel` —
was never a serialization problem but an ownership one, and ADR-0023 answered it:
a snapshot records *what the object was*, by type, and a restore requires the
caller to supply it back, raising rather than substituting. `RunObjects` and
`RuntimeObjects` are that hand-back.

A payload is stored by `alphalab.persistence.RunStateStore` over
`(run_id, sequence)` — payload-agnostic, one real file backend with atomic writes
and a digest verified before any decoder runs, one explicitly named in-memory
double, and no fallback between them (ADR-0029).

### The contract

`restore(capture(state)) == state`. The restored value **compares equal**; it
does not reproduce internal container lineage, and nothing can observe the
difference — `PersistentMap` inherits `Mapping.__eq__`, `PersistentSet` inherits
`Set.__eq__`, `AppendOnlyLog` compares `to_tuple()`. One rule for every state.

### Typed decoding

`alphalab.persistence.decode` supplies the primitives — `require`, `as_decimal`,
`as_float`, `as_int`, `as_bool`, `as_str`, `as_mapping`, `as_sequence`,
`as_named_enum`, `as_value_enum` — and each raises `StateDecodeError` **naming
the field**. It is deliberately not a reflective object mapper: a domain package
states field by field what it expects, because that is where a wrong type or a
missing key gets caught.

| Situation | Answer |
| --- | --- |
| Missing field | `StateDecodeError`, naming it. Never a default |
| Wrong type | `StateDecodeError`, naming the field and what it got |
| String where an array belongs | Refused (a `str` is a `Sequence` in Python) |
| Unknown event type / enum member | Refused, listing what was expected |
| `"STAGING"` where `"ModelStage.STAGING"` is written | Refused — one encoding, one decoder |
| Unknown `schema_version` | Refused. There is no migration path (v2.6 refuses portfolio v1) |
| Unread extra field | Ignored, and not carried into the restored state |

### Live objects are referenced, not reconstructed

`ModelVersion.model` is an arbitrary object and `__serializable__` already drops
it for `model_type` plus the artifact reference. So
`lifecycle.snapshot.restore(snapshot, models={"momentum@1": obj})` takes the
objects back from the caller, and **raises** when one is missing or is not the
type that was captured. Never a substituted `None`.

## The live data path (v2.5)

```
provider adapter        marketdata.binance.binanceAdapter  (real /api/v3 parsing)
  -> wire bars          marketdata.feed.Bar                (float, provider symbol)
  -> normalization      market.normalization               (Decimal, asset_id)
  -> MarketRecord       market.record
  -> ProviderHistorySource                                 (a MarketDataSource)
  -> TradingSession     runtime.session
```

v2.3 built both ends and never joined them, so `normalize_wire_*` had no
production caller and `SequenceSource` was the only source in the repository.
`alphalab.market.provider` is that link and only that link — no HTTP, no vendor
API, no second provider.

It is a **history** source: a finite, closed range of bars, re-iterable, with
deterministic record ids. No polling, no subscription, no reconnect.

**v2.15 adds the other one.** `alphalab.market.stream.StreamingSource` is the
continuous counterpart -- it connects, subscribes, and yields records as a venue
pushes them, for as long as the caller reads:

```
websocket frame     marketdata.websocket.WebSocketConnection  (RFC 6455)
  -> JSON message   market.stream
  -> wire record    data.feed.Quote / Trade / Bar     (float, provider symbol)
  -> normalization  market.normalization              (Decimal, asset_id)
  -> MarketRecord   market.record
  -> StreamingSource                                  (a MarketDataSource)
  -> RunEngine.advance                                the canonical run step
```

Every stage but the first two already existed. That is the point: a streaming
source that normalized differently from a historical one would make live and
backtest results incomparable for a reason that has nothing to do with the
market. `TradingSession.run` drives it with the loop it drives a stored dataset
with, because `MarketDataSource.records()` already returns an iterator and a
generator pulling a socket satisfies it unchanged.

### Ordering

`MarketEngine.publish_quote` writes `latest_quotes[asset_id]` unconditionally and
the pipeline marks the portfolio to whatever it finds, so a late record rewrites
valuation backwards. AlphaLab does not reorder market data; it says so:

| `RunConfig.ordering` | A record whose timestamp regresses |
| --- | --- |
| `CHRONOLOGICAL` (default) | **Raises.** The source broke the guarantee it declared |
| `UNORDERED` | **Skipped and recorded** on `RunState.skipped`, with the reason |

A source declaring `UNORDERED` handed to a `CHRONOLOGICAL` session is refused by
`TradingSession.run` before any record is processed. Nothing is buffered or
reordered.

## Partial fills terminate (v2.5)

The pipeline mints a fresh order per market event and never re-works an existing
one. Every non-trading outcome was withdrawn under that rule; a *partial* fill
was the branch that skipped it, so the order stayed `PARTIALLY_FILLED` forever
and held the reservation for a quantity nothing would execute.

A partially filled order is now cancelled and its residual reservation released.
`Order.cancel` preserves `filled_quantity` and `average_fill_price`. **This
changes bookkeeping, not economics**: no fill is created or destroyed, so cash,
positions, P&L and the equity curve are unchanged.

## The integrated runtime: the live driver (v2.16)

ADR-0030's Tier-3 table listed `LiveDriver` as "(later)" and its consequences
section recorded "no live driver exists". Every half of the live path was here
and tested; what nothing owned was the **cycle**:

```text
venue fills ---> settle ---> RunEngine.advance ---> route working orders
     ^                                                      |
     +------------------------------------------------------+
```

`LiveSession` is that cycle, and it is a driver on ADR-0030's definition: every
method is a `@staticmethod` over an immutable value, and it holds no execution
state and no run state. `LiveRunState` is an **aggregate** of three existing
authorities — the `RunState`, the `BrokerState`, the `ExternalOrderMap` — with
no cursor, cash, positions or order book of its own. `RunState` gained no
fields and `RUN_SNAPSHOT_SCHEMA` did not move.

**The order of a step is the contract.** A fill the venue has already reported
reaches the portfolio *before* the strategy is dispatched, which is the same
rule `process_market_event` follows when it marks before it dispatches.

**Two ledgers, two questions.** `BrokerState.executions` answers "has the
venue-side bookkeeping recorded this fill?"; `ExecutionState.reports` answers
"has the *portfolio* booked it?". Both are keyed by `execution_id` and both
refuse their own repeat, and a fill in one and not the other is the normal case
— `poll_executions` applies every fill it fetches before returning it. A
**break** stops a fill; a duplicate does not.

**Venue-side durability.** `BrokerState` and `ExternalOrderMap` had no snapshot
at all through v2.15, so the duplicate-submission gate was worth exactly as much
as the mapping's durability across a process boundary, which was zero.
`BROKER_SNAPSHOT_SCHEMA` and `LIVE_SNAPSHOT_SCHEMA` are two nested constants, so
a backtest payload is unchanged.

**The lifecycle join.** `alphalab.lifecycle`'s docstring ended "the two are
joined by the caller", which meant nothing checked that a run served the version
an environment actually had live. `lifecycle.execution.run_plan` resolves a
deployment and `authorize_run` refuses a run that would serve anything else — a
query with a refusal, naming no runtime type at all. See ADR-0033.

## Governance: who may change what is live (v2.16)

ADR-0018 was written in v2.7 and deferred. Before v2.16 the lifecycle's audit
trail answered *what* changed and *when* and was silent on *who*, while
`alphalab.enterprise` held a complete RBAC implementation with zero production
consumers.

`Governance(enterprise, actor_id, approval_required_in)` is the **required**
second argument of `promote_strategy_version`, `deploy_strategy_version`,
`rollback_environment`, `retire_strategy_version` and `approve_deployment` —
ADR-0018's option (b), and required because an optional gate is the option (c)
that ADR rejected as "a gate anyone can bypass by calling the function
directly".

The actor reaches `StrategyPromotionRecord` and `DeploymentRecord`, so the
deployment ledger — "the only answer to what is live" — also answers who put it
there. An approval names an exact `(name, version, environment)` and one granted
by the deployer does not satisfy separation of duties. `governance_log` reads
the three append-only records the lifecycle already keeps and maintains no
fourth.

`LIFECYCLE_SNAPSHOT_SCHEMA` moves 1 → 2 and **refuses** a version 1 payload;
`DEFAULT_SCHEMA_VERSION` stayed at 1, which is the v2.8 de-alias paying for
itself. An act no principal requested — an incumbent archived because a
replacement displaced it — records `actor_id=""`, which is the honest answer.

## FX: a book in two currencies, valued as one figure (v2.16)

Four ADRs deferred to "the release that supplies the rate source". What it
supplies is shaped by ADR-0020's rejected alternative: a configured rate is an
invented one. So every rate in `alphalab.portfolio.fx` is **supplied** and
carries a source and an `as_of`; there is no default, no fallback of 1.0, **no
triangulation and no implicit inversion**, and a stale rate is refused rather
than used.

**The rule did not fork.** `assert_single_currency_book` is still the one
implementation. A currency it can convert stopped being one it must refuse; a
pair it has no rate for still is, and the message names which pair.

**The fast path is untouched.** A homogeneous book takes the same code it always
did, so the +1.78% ADR-0028 decision 7 measured for guarding the component sums
is paid by nobody. A converted valuation records every conversion it performed,
because a number in a currency the book is not wholly in, with no statement of
how it got there, is ADR-0020's defect wearing a rate.

**The settlement boundary did not move in v2.16**, and the reason was sharper
than "no rate exists": `realized_pnl` and `commission_paid` were single
cumulative scalars naming no currency, so a run that *traded* two would have
summed them across both. See ADR-0033 decision 13.

## Settlement in more than one currency (v2.17)

Those blockers are gone. `realized_pnl` and `commission_paid` are
`CurrencyAmounts` — currency to exact amount — so a EUR fill accrues EUR P&L and
no addition is ever performed across two.

**Settlement truth and reporting truth are different numbers and stay apart.**
The state records what was earned in the currency it was earned in, permanently;
a valuation names one currency and converts into it on demand, recording every
rate. Translating at fill time would have kept both fields scalars, and it
destroys the only record of what was actually earned while baking one instant's
rate into a cumulative figure.

`ExecutionPipelineConfig.also_settles` is **empty by default**, which is the
single-currency pipeline every run had before v2.17 and byte-identical to it.
Naming a currency there widens both ADR-0028 seams and makes the resulting book
genuinely mixed — so every valuation of it then needs a rate table and refuses
without one. It is not a licence to convert: an `OrderInstruction` is stamped
with the instrument's own currency, and a run that settles a currency it has not
funded is refused rather than financed.
`PortfolioEngine.convert_cash` is how the money gets there, and it records the
rate, its `as_of` and its source on a `CashConverted` event.

The other two blockers close too. `CapitalBudget.currency` is `""` — *unstated*,
not `"USD"` — which a single-currency pipeline determines and a multi-currency
one refuses; and `_sync_risk_from_portfolio` reads `cash_in` rather than
`cash.balance(base)`, which had silently dropped every other balance.

**`alphalab.allocation` still knows nothing about exchange rates**, and that is
measured rather than stylistic: threading an `FxRates` into it pulled all
eighteen `alphalab.portfolio` modules into a package that previously imported
none of them. The conversion happens at the pipeline, which already holds both
the registry and the rates, and allocation receives a second price map.

See ADR-0035.

## Where rates come from (v2.17)

`alphalab.portfolio.fx_feed` is a **boundary, not data** — AlphaLab ships no FX
rate, exactly as it ships no classification data. What was missing was the seam a
supplier crosses and the rules it enforces, because without one every caller
folded quotes into a table itself and answered three questions implicitly:

| A quote that is… | …is |
| --- | --- |
| newer than what is held for its pair | `APPLIED` |
| byte-identical to what is held | `DUPLICATE` |
| **older** than what is held | `SUPERSEDED`, and not applied |

The third is the one that matters: accepting it would move the book's view of
the market backwards because two packets arrived out of order. Two quotes
claiming one instant that disagree are **refused**, not ranked — `FxRates.of`
already refuses that shape, and a feed that let the later packet win would be
picking while looking authoritative.

`FxRateSource` takes `MarketDataSource`'s shape: identity, provenance, and
nothing about order. Staleness stays at conversion time
(`FxRates.max_age_seconds`); `FxFeedState.silent_for` answers the different
question of whether the *connection* has gone quiet.

## The strategy boundary, finished (v2.16)

Two halves of the same defect class, both left by a release that named the class
correctly and fixed a subset of it. See ADR-0032.

**Market events reach a hook by exact identity.** `alphalab.strategy.dispatcher`
selected four of its seven hooks by comparing `type(event).__name__` against a
string. Three packages here define a `TickReceived`, a `QuoteReceived` or a
`TradeReceived`, so `alphalab.live.events.TickReceived` — a different class with
`provider_id` / `symbol` / `tick_type` instead of a `tick` — was routed to
`on_tick`, and the `AttributeError` the strategy then raised was reported as a
*strategy* failure. Routing now matches the module and the name together, which
identifies a class exactly.

It is deliberately **not** `isinstance`. ADR-0016 decision 3 gives
`alphalab.strategy` no dependency on `alphalab.market` or
`alphalab.instrument`, and `test_instrument_identity_reaches_a_fill.py` enforces
it; importing the canonical types into the dispatcher was tried and that test
caught it. `alphalab.runtime.execution_pipeline` sits above both layers and does
use `isinstance`, and `test_strategy_event_routing.py` checks the two agree
class by class.

`BookUpdated` and `SnapshotCreated` reach no hook. `StrategyProtocol` declares
no depth hook and delivering a snapshot to `on_quote` would hand existing
strategies a payload with no `quote`; the canonical record path cannot produce
either event in any case, because `publish_record` refuses a record that is not
a quote, bar or tick. Stated boundary, pinned.

**Every context surface declares what it supplies.** ADR-0031 identified a field
whose protocol "declares no methods, and every construction site in the
repository passes `object()`" as decorative, and fixed `history` and
`universe`. The four protocols v2.10 had already populated —
`PortfolioSnapshotProtocol`, `MarketViewProtocol`, `RiskViewProtocol`,
`OrderFacadeProtocol` — were left empty, and fifteen construction sites still
passed `object()`. Since AlphaLab ships `py.typed`, the declared type is the
contract a downstream strategy is checked against: under `mypy --strict`,
`context.history.bars(asset)` checked and `context.portfolio.cash("USD")` did
not.

They now declare what `alphalab.runtime.context_views` implements. Payload types
stay `Any` for the same layering reason; `Decimal` is named, because it is
stdlib and it is where a wrong type costs money. `NoPortfolio`, `NoMarket`,
`NoRiskView` and `NoOrders` replace the placeholders, following `NoHistory` and
`NoUniverse`: they answer "nothing" and say so through `available`, so *no
portfolio was supplied* stays distinguishable from *the book is empty*.

## Standalone engine libraries

An independent, deterministic, individually tested library that is reached by
**neither** wired path: `portfolio_optimizer`, `optimizer`, `reporting`,
`feature_store`, `alt_data`, `ml`, `deep_learning`,
`reinforcement_learning`, `options`, `futures`, `crypto`, `macro`,
`cloud_research`, `cluster_scheduler`, `distributed`, `workbench`,
`research_assistant`, `live`, `feed`, `brokers`, `plugins`, `scheduler`,
`scenario`.
(`production`, `integrations` and `kernel` were on this list until v2.17, which
removed them — see ADR-0034.)

**`scenario` joined this list in v3.3**, and its being there is the design
rather than an omission. A `Scenario` applies to a `ScenarioState` — a flat
projection any holder of positions can produce — rather than to a portfolio
class, which is what lets one contract stress a backtest book, a live book, an
optimizer target and a hand-built one. A scenario package that imported
`alphalab.portfolio` would be usable by exactly one of those. It imports
`alphalab.common` and nothing else in AlphaLab, and
`tests/regression/test_shared_names_stay_distinct.py` reads the source to keep
it that way.

**Two packages on the spine were deepened in v3.3 without gaining or losing an
edge.** `alphalab.execution` gained the cost itemization (`costs.py`) and the
capacity model (`capacity.py`), both importing only `alphalab.core` and
`alphalab.common` as the package already did. `alphalab.analytics` gained the
attribution dimensions and `decomposition.py`, on the same terms. Neither
reaches into `alphalab.portfolio`: attribution consumes a `TradeRecord` and risk
decomposition consumes a `PositionRisk`, both of which the caller projects — the
separation `TradeRecord` has kept from `ExecutionReport` since v2.6.

**`factor_library` left this list in v3.2.** It is now imported by
`alphalab.research`, which `alphalab.lifecycle` imports, so it is reached by the
lifecycle path; and by `alphalab.api`, which is where an application enters. The
edge is one-way — nothing in `factor_library` imports `research`, `lifecycle` or
`api` — and `tests/regression/test_import_graph_stays_acyclic.py` measures that
on every run.

`feature_store` stayed on the list, and that is the architecture working rather
than an oversight: it owns registration, versioning and caching and computes
nothing, so the computation engine writes *through* it via
`FeatureValueProtocol` without either package importing the other. Its only
importer remains `ml`.

Six packages often listed as standalone are **not**, and the import graph is the
authority. `alphalab.broker` is reached from the execution path through
`alphalab.runtime.broker_routing` (v2.3); `alphalab.data.feed` and
`alphalab.marketdata.feed` supply the wire records
`alphalab.market.normalization` lifts, and `marketdata` is imported by `market`;
`instrument` is read by the pipeline on the fill path (v2.11, v2.12); and
`persistence` supplies the codec spine and the `RunStateStore` every snapshot
owner writes through.

`alphalab.lifecycle` imports `experiment_tracking`, `model_registry`,
`deployment_manager`, `studio`, `enterprise`, `research` and `backtesting`. It
does **not** import `research_assistant`: that package produces a candidate and
`to_strategy_definition` lifts it into the canonical `StrategyDefinition` the
lifecycle takes, so the dependency runs through the definition rather than the
package. `README.md`, `docs/README.md`, this document and ADR-0013 said
"composes `research_assistant`" from v2.4 until v3.0 corrected it.

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
| `realized_pnl` | `PortfolioState.realized_pnl` — `CurrencyAmounts`, cumulative **per currency** since v2.17 | reducing or closing a position |
| `commission_paid` | `PortfolioState.commission_paid` — `CurrencyAmounts`, cumulative **per currency** since v2.17 | every fill, exactly once |
| unrealized P&L | derived, never stored | marking positions to market |

Both totals were single scalars naming no currency until v2.17. They are now
per-currency, so a EUR fill accrues EUR P&L and nothing is ever summed across
two — see *Settlement in more than one currency* above.

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

Two notes on that reach:

- **The strategy sees the marked portfolio, as of v2.10.** `ExecutionPipeline`
  assembles the context from the marked-portfolio and resynced-risk locals and
  overlays them onto whatever the caller's `context_factory` returned, so a
  strategy reads the book as of the event being dispatched rather than the
  previous one. This paragraph said the opposite from v2.1 until v3.0, four
  releases after ADR-0026 made it false and two sections away from the text that
  already said so. See *The strategy boundary* below.
- **Allocation does not see the portfolio at all**, and that is unchanged.
  `AllocationEngine.allocate` sizes from market prices and its `CapitalBudget`,
  neither of which the mark changes.

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
  opening fill with an earlier close's P&L. As of v2.6 the record also carries
  real strategy contributions and a real holding period. As of v2.11 it carries
  the sector the run's registry classified the asset as, read once per fill and
  frozen onto the record, or `None` — never a placeholder.

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
| a partial fill | consume what executed, leave the residual reserved — the order is still working. The pipeline then cancels the remainder and releases it (v2.5) |
| the order reaches a terminal status | release whatever it still holds (v2.6) |
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

v2.16 adds `studio` and `workbench`, for the reason in the section below.

**v2.17 converts the rest.** `scheduler`, `feature_store`, `distributed`,
`plugins`, `reporting`, `optimizer`, `data`, `cluster_scheduler` and
`portfolio_optimizer` now take the canonical containers; `integrations` and
`production`, which were on the same list, were removed instead. Measured over a
2,500 → 20,000 doubling sweep, `distributed` went from 38.4s to 0.18s and from
4.1x to 2.1x per doubling, and every converted package now grows at ~2.0–2.1x.

**The containers were not the whole story in three of them**, and profiling
before changing anything is what found that. `distributed` spent ~65% of its
submit path re-sorting the whole queue and ~26% building a union of four
containers per validation; `plugins` spent ~54% calling `metadata()` on every
registered plugin; `feature_store` scanned the whole registry per registration.
Converting the containers alone would have left ~90% of `distributed`'s cost in
place and made `feature_store` four times *slower*, because a `PersistentMap`
iterates more slowly than a `dict`. Each is now answered by a derived index
carried on the state, which is the v2.2 OMS pattern.

**Batch operations keep their local copies.** `assign_jobs`, the two
`cluster_scheduler` assigners and `SchedulerEngine.advance_clock` each traverse a
whole collection in one call, so one copy in and one immutable value out is
O(collection) per *call* rather than per element — writing each element through
`PersistentMap.set` instead measured ~30% of the 100,000-timer benchmark.

One term is deliberately left super-linear: `OptimizerState.pending_trials`,
whose fix needs a start offset on `AppendOnlyLog` and was measured at **+3.9%**
on `benchmark_execution_pipeline`. That is the trade ADR-0028 decision 7 refused
at +1.78%. See ADR-0034.

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

## Studio and Workbench accumulation (v2.16)

The two presentation-layer packages never took the v2.1 / v2.2 containers, and
`benchmarks/benchmark_workbench.py` is where that showed. The benchmark had
never run: it crashed on its first iteration at every tag back to v2.14, and the
crash hid three further defects underneath it.

| Defect | Was | Now |
| --- | --- | --- |
| The rendered tab could not be named | `run_backtest` opens `bt-<result_id>`, where `result_id` is minted by Studio; `Tab.is_active` existed but no view exposed it | `alphalab.workbench.views.active_tab` |
| Finding Studio's result | filter the whole event log by `type(e).__name__ == "BacktestCompleted"` — O(events) per delegation, and a name match | read the events *this call* appended, by type |
| Event and index accumulation | `(*state.events, evt)`, `dict(state.backtest_results)`, `(*proj.backtests, config)` | `AppendOnlyLog` and `PersistentMap` |
| `close_project` | dropped the active tab and left pinned tabs unfocused | restores the one-active-tab invariant |

Measured on the development machine, full history retained:

| Workload | Before | After |
| --- | --- | --- |
| `benchmark_workbench` (100k UI cycles) | crashed on iteration 0 | 3.1s, 32,132 cycles/sec |
| Workbench session scaling (2x workload) | ~3.2x | ~2.1x |
| `benchmark_strategy_studio` (10k backtests) | 0.76s | 0.12s |

`tests/regression/test_workbench_delegation.py` pins all four.

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

## Known boundaries, and what closed them

- **Market-data model convergence was done in v2.3, and this entry described
  the state before it** (corrected in v2.16). The third `Bar` is gone:
  `marketdata.feed` re-exports `data.feed`'s definitions, and
  `tests/regression/test_market_model_convergence.py` asserts the identity.
  `data.feed.Bar` and `market.bar.Bar` remain separate *deliberately* — one is a
  `float`/`symbol` wire record, the other the canonical `Decimal`/`asset_id`
  domain record `market.normalization` lifts it into. A backtest still reads
  `alphalab.market` inputs only.
- **`broker` / `brokers` overlap was closed in v2.3, and this entry described
  the state before it** (corrected in v2.16). `brokers` routes the canonical
  types: `AccountSnapshot`, `PositionSnapshot`, `ExecutionReport`, `BrokerOrder`
  and `OrderStatus` *are* the `alphalab.broker` classes under this package's
  historical names, pinned by
  `tests/regression/test_shared_names_stay_distinct.py`. Live broker
  connectivity reached the execution path in v2.15; see ADR-0031.
- ~~**`kernel` and `core/events` are unused by the execution path.**~~
  **Removed in v2.17** (ADR-0034), along with `integrations`, `production`,
  `CommonEvent`, the nine-module persistence store and the ten-module orphan
  runtime lifecycle — all with zero production importers and no compatibility
  aliases. `tests/regression/test_removed_surfaces_stay_removed.py` asserts each
  name is absent, that no package serves one through a `__getattr__`, and that
  none is re-exported under a different spelling.
- ~~**A strategy still does not see the marked portfolio.**~~ **False since
  v2.10** (ADR-0026), and corrected in v3.0. The pipeline overlays the marked
  portfolio, the strategy's live order shares, and read-only risk and market
  views onto the caller's context; v2.15 added `history` and `universe` and v2.16
  gave all six protocols real members. Allocation still sizes from market prices
  and its capital budget, not from the portfolio, and that is deliberate
  (ADR-0015).
- **`ExecutionPipeline` mints a fresh order per market event and never re-works
  an existing one.** A partially filled order is cancelled at the end of its
  execution opportunity (v2.5) and its residual reservation released; it is not
  topped up on a later event. A participation-capped strategy that wants to
  finish a large order must keep expressing the intent.
- **Multi-currency valuation arrived in v2.16 and settlement in v2.17.**
  `alphalab.portfolio.fx` holds supplied rates with provenance, and
  `PortfolioValuation.snapshot`, `portfolio_value` and `NAVCalculator.calculate`
  each take a table and convert through the one rule. Without a table they refuse
  exactly as they did. v2.17 closes the settlement half — see the two sections
  above and ADR-0035. What is still absent is **FX data itself**: the feed adds a
  contract and not a single rate.
- **Streaming market data arrived in v2.15.** `alphalab.market.stream.StreamingSource`
  is a `MarketDataSource` over a real WebSocket connection, with subscription,
  sequence deduplication, gap counting, heartbeat-based liveness detection,
  reconnect-and-resubscribe and graceful shutdown. It declares `UNORDERED`,
  because a venue can reorder and ADR-0014's answer to that is unchanged: a run
  over it sets `RunConfig.ordering` to `UNORDERED` and a regressing record is
  skipped and recorded rather than marking the portfolio backwards. See
  ADR-0031.
- **Artifact bytes are held from v2.15.** See the Artifacts section above;
  `ArtifactRef.checksum` is now computed, and a changed artifact is detectable.
- **Classification carries provenance from v2.15.** `classify_instrument`
  records a `source` and an `as_of`, and every act is kept in an append-only
  `ClassificationHistory`, so a reclassification is auditable rather than
  silent. `alphalab.instrument.snapshot` makes that trail durable. See ADR-0031.
- **The strategy context is complete from v2.15.** `history` and `universe`,
  deferred by ADR-0026, are populated: history is bounded at the dispatched
  event's timestamp and universe membership is the instrument registry. See
  ADR-0031.
- **Sector attribution is available from v2.11, and still says so when it is
  not.** `classify_instrument` writes `InstrumentRecord.sector` without touching
  the identity key, and the pipeline reads it once per fill onto
  `TradeRecord.sector_id`, so `pnl_by_sector` and `ExposureStatus.sector_exposure`
  are real for a run whose registry classifies its instruments. Absent a
  registry, an unregistered asset, or an unclassified instrument, `sector_id` is
  `None` and the breakdown is empty rather than bucketed under a placeholder —
  the v2.6 rule, unchanged. AlphaLab still ships no classification data. See
  ADR-0027.
- **An unseeded run does not reproduce its identifiers.** `RunConfig.seed`
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
- The broker boundary

Subsystems cooperate through stable interfaces rather than direct coupling.

---

## Production First

AlphaLab is designed with production deployment in mind.

Features such as:

- Durable run state, continuable across a process boundary
- Deterministic replay, and backtest/replay/paper/live parity on one step
- Broker abstraction with reconciliation and idempotent submission
- Governance: every act that changes what is live names its principal
- Health *reporting* (`live_health`)

are considered first-class architectural components rather than optional add-ons.

**Supervision is not one of them.** Restart policy, alerting, process management
and scheduling are an operator's concern, and AlphaLab has no opinion about them:
`alphalab/production` tried to own them, had zero importers, and was removed in
v2.17 (ADR-0034). AlphaLab reports; the caller decides.

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
        ┌─────────────┬──────────┴──┬──────────────┐
        ▼             ▼             ▼              ▼
 Universal Data   Research      Portfolio     Model & Strategy
    Engine         Engine       Optimizer       Lifecycle
        │             │             │              │
        └─────────────┴──────┬──────┴──────────────┘
                             ▼
                  Execution path (RunEngine)
                             │
                             ▼
                     Broker boundary
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
|  Common • Persistence • Plugins • Scheduler          |
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

Turns a raw source into a canonical dataset that can say where it came from.

```
raw source  ->  schema detection  ->  validation  ->  cleaning (under a policy)
            ->  quality report    ->  canonical dataset
            ->  provenance + a derived, immutable version
```

Every downstream module consumes canonical datasets produced here. v3.1
(ADR-0036) implemented this path; before it, the module names existed and behind
each was a stub.

### One owner per step

| Step | Module | Owns |
| --- | --- | --- |
| Source provenance | `data.source` | `RawSource`, `SourceKind`, the content digest |
| Delimited reading | `data.csv_source` | `CsvDialect`, `RawTable`, delimiter detection, malformed rows |
| Column spellings | `data.formats` | `COLUMN_ALIASES`, `canonical_field` — the one alias table |
| Schema detection | `data.schema` | `FieldRole`, `RecordType`, `SchemaDetection`, `DatasetSchema` |
| Time | `data.time` | `TimestampFormat`, `DateOnlyPolicy`, `TimeFrequency`, zone resolution |
| Validation | `data.validation` | `FindingKind`, `ValidationFinding`, `RowRejection`, row coercion |
| Cleaning | `data.cleaning` | `CleaningPolicy`, `TransformationRecord`, `is_internally_consistent` |
| Quality | `data.quality` | `DataQualityReport` (authority) and `QualityReport` (its projection) |
| Asset semantics | `data.assets` | One spec per asset class |
| Calendars | `data.calendar` | `MarketCalendar`, `SessionWindow` |
| Corporate actions | `data.corporate_actions` | `PriceBasis`, `AdjustmentRecord` |
| Provenance | `data.provenance` | `DatasetProvenance`, `derive_dataset_version` |
| The dataset | `data.dataset` | `Dataset` |
| The pipeline | `data.ingestion` | `IngestionRequest`, `ingest_table` |
| Engine state | `data.engine`, `data.manager`, `data.state` | The catalogue, lineage and event log |
| Application API | `alphalab.api` | The surface a host platform calls — a **top-level** module, not part of `data` |

`alphalab.api` sits *above* both the data layer and the execution path rather
than inside either. `alphalab.market` imports `alphalab.data.feed` for the wire
records, so a join placed inside `alphalab.data` would close a package-level
import cycle. `alphalab.data`'s only outward edges are `alphalab.common` and
`alphalab.options`, nothing imports `alphalab.api`, and `import alphalab.data`
pulls in no part of the execution path —
`tests/regression/test_import_graph_stays_acyclic.py` measures all three.

### The rules it enforces

- **Nothing is altered silently.** Every rejected row carries its reason and
  source line; every applied change is a `TransformationRecord`.
- **The cleaning policy is the caller's** and has no default. There is no way to
  fill a missing price.
- **Ambiguity is refused**, not resolved — an unresolved schema, a delimiter two
  candidates fit, a naive timestamp with no zone, a numeric column that reads as
  a valid instant in both seconds and milliseconds.
- **A dataset version is immutable.** Cleaning and resampling derive a new one;
  both stay in state and `lineage` records the parent.
- **Provenance may be absent and says so.** `provenance=None` for a dataset
  built from rows in memory; `require_provenance()` refuses rather than
  manufacturing one.

### Owns

- The canonical wire record (`data.feed`)
- The `Dataset` model, its provenance and its derived identity
- The dataset registry, catalogue and lineage
- Schema detection, validation, cleaning and quality reporting
- Market calendars and the raw/adjusted price basis

### Never Owns

- Research
- Trading logic
- Portfolio optimization
- The wire → domain normalization boundary — that is `market.normalization`,
  and `alphalab.api.normalize_records` delegates to it rather than repeating it
- Holiday data, corporate actions, or any vendor feed

---

## Research Engine

**Package**

```
alphalab/research
```

### Responsibility

Two layers, consuming different things. They are not alternatives and neither
replaces the other.

**Run evaluation (v2).** Reads a *completed run's* returns, trades and
parameters through `ResearchPayload` and scores them. Nothing here knows about
a dataset, and there is nothing it could leak, because everything it touches has
already happened.

- Bias proxies, bootstrap confidence intervals, Monte Carlo drawdowns
- Capacity estimation, regime analysis, stress tests, diagnostics
- `walk_forward_analysis` — Sharpe consistency across equal chunks of an
  existing return series
- `compute_overall_score` — the aggregate grade `ResearchEngine` produces

**Study methodology (v3.2).** Runs *before* there is a return series to score,
from a canonical `Dataset` through `alphalab.factor_library`.

- `splits` — `TimeSplit` and `SplitInterval`, carrying the instants in each part
- `purging` — label windows read off the actual series, purge and embargo
- `walk_forward` — train / validate / test / roll, rolling or expanding
- `time_series_cv` — rolling, expanding, purged blocked k-fold, embargoed
- `signals` — forward-return analysis, quantile profiles, regime conditioning
- `perturbation` — seeded robustness experiments
- `overfitting` — sweeps, sensitivity, degradation, stability, Bonferroni
- `study` — the reproducible experiment contract and its result

The two meet at `alphalab.lifecycle.evidence`, where either can be recorded as
`ValidationEvidence`, and nowhere else.
`tests/regression/test_shared_names_stay_distinct.py` records why
`walk_forward_analysis` and `walk_forward_splits` stay apart, and likewise for
the two bootstraps, the two Monte Carlos and the two parameter diagnostics.

### Inputs

A canonical `Dataset` for the study layer; a `ResearchPayload` for the run
evaluation layer. Neither fetches its own data: a research function that could
fetch could fetch *different* data than the one beside it, and no two results
would be comparable.

### Outputs

`StudyResult` for the study layer, whose identity is derived from the study and
the numbers it produced; `ResearchState` and `ResearchScore` for the run
evaluation layer.

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

Owns execution in two tiers, and nothing else.

`ExecutionPipeline` owns the **execution step**: one market event through market,
strategy, allocation, risk, OMS, execution, portfolio and analytics, as pure
functions over one immutable `ExecutionPipelineState`. `RunEngine` owns the
**run**: how far it has read, what it declined to act on, what each record
produced, and the identifier scope a stopped run continues in.

Around them sit **drivers**, which own the input and the clock and nothing else —
`TradingSession`, `BacktestEngine`, `ReplayBacktest` and `LiveSession`. A driver
holds no execution state and no run state; that is what makes backtest, replay,
paper and live one loop rather than four. `runtime.broker_routing` is the
boundary an order crosses to reach a venue and its fills cross to come back.

See ADR-0030.

---

## Production Runtime — removed in v2.17

`alphalab/production` supervised live systems: process supervision, health
monitoring, heartbeats, checkpoints, recovery and restart policies. It had zero
production importers, its `Checkpoint` / `RecoveryEngine` held six opaque strings
and restored nothing, and it was deprecated in v2.14 (ADR-0030). **Removed in
v2.17** (ADR-0034).

Durable run state is `alphalab.persistence.run_store.RunStateStore` (ADR-0029);
a live loop is `alphalab.runtime.live.LiveSession` (ADR-0033).

---

# Integration Layer

## Broker Integrations — removed in v2.17

`alphalab/integrations` was a third broker surface speaking none of the canonical
`alphalab.broker` types, with canned-response clients for Alpaca, Interactive
Brokers, Zerodha and paper trading. v2.3 converged `broker` and `brokers` and
left it untouched; it was deprecated in v2.6 and had zero production importers.
**Removed in v2.17** (ADR-0034).

The broker boundary is `alphalab.broker.protocol.BrokerProtocol` — one venue —
and `alphalab.brokers.protocol.BrokerConnectorProtocol` — many brokers and many
accounts. See ADR-0012 and `examples/05_broker_connection.py`.

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

Raw provider data is forwarded to the Universal Data Engine for ingestion,
validation and canonicalization.

---

# Infrastructure Layer

Infrastructure packages provide shared capabilities used across the platform.

---

## Common

```
alphalab/common
```

Provides the shared substrate: the package version, `BaseEvent`, deterministic
serialization, the seeded identifier source, the persistent containers
(`AppendOnlyLog`, `PersistentMap`, `PersistentSet`) and the one TLS policy.

**There is no `alphalab/events` package and no event bus.** Events are immutable
values carried on the state that produced them; each package defines its own in
its own `events.py`, and a caller reads them off the returned state. `alphalab.common`
imports nothing else in `alphalab`, which is what keeps it the bottom layer.

---

## Persistence

```
alphalab/persistence
```

Provides the codec spine and the one durable-storage boundary.

- **Serialization** — `serialize` writes deterministic JSON and raises on
  anything it has no explicit branch for.
- **Typed decoding** — `alphalab.persistence.decode` raises `StateDecodeError`
  naming the field. It is deliberately not a reflective object mapper.
- **`RunStateStore`** — four methods over `(run_id, sequence) → payload`.
  Payload-agnostic: it moves a `str`, imports no snapshot type and decodes
  nothing. `FileRunStateStore` is the real backend; `MemoryRunStateStore` is an
  explicitly named double that nothing selects automatically.

Each domain package owns its own snapshot projection and its own schema constant;
persistence owns the codec and the store, never the schema. See ADR-0029.

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

## Kernel — removed in v2.17

`alphalab/kernel` described itself as "the internal execution foundation shared
across the platform" and was shared with nothing: it had zero importers and never
drove the execution path. Its `PortfolioState` and `PositionState` were
re-exports of the canonical `alphalab.portfolio` types rather than a second
model, which is what the v2.16 audit established (ADR-0032 finding B6). It was
deprecated in v2.6 and **removed in v2.17** (ADR-0034).

The execution foundation is `alphalab.runtime.execution_pipeline.ExecutionPipeline`
for a step and `alphalab.runtime.run.RunEngine` for a run (ADR-0030).

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
Adapters (alphalab.broker, alphalab.brokers, alphalab.marketdata, alphalab.live)
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
- Reporting

Must not depend directly on provider implementations.

*(In the implementation `alphalab.studio` depends only on `alphalab.common`; it
is `alphalab.lifecycle` that composes the lifecycle packages and takes Studio's
`StrategyDefinition`. The rule above is the layering permission, not a claim
about current imports.)*

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
- Lifecycle

---

## Portfolio Optimizer

May depend on:

- Research outputs
- Analytics

Must never depend on:

- Workbench
- Broker APIs

---

## Adapters (`broker`, `brokers`, `marketdata`, `live`, `feed`)

May depend only on:

- Core interfaces
- Protocols
- The canonical domain models they translate into

They must never import higher-level AlphaLab modules. An adapter converts a
provider's or venue's shapes into canonical AlphaLab values at the boundary, and
nothing above it learns which provider it was.

*(`alphalab/production` and `alphalab/integrations` had their own rules here
until v2.17 removed both packages — see ADR-0034. Supervision is an operator's
concern and AlphaLab has no opinion about it; the venue boundary is
`alphalab.broker`.)*

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
              Model & Strategy Lifecycle
                           │
                           ▼
                    Broker boundary
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

# Stage 9 — Model and Strategy Lifecycle

Once validated, a strategy version is promoted and deployed.
`alphalab.lifecycle` provides

- Validation evidence with content-derived identity
- A gated promotion
- The deployment ledger as the one answer to what is live
- Deterministic rollback
- Governance: who may change what is live, and who did

A deployment is a lifecycle *fact*: it records that an environment should be
running a strategy version. It starts no process and opens no connection.

**Supervision is not here.** Restart policy, alerting and process management are
an operator's concern; `alphalab/production` attempted them, had zero importers,
and was removed in v2.17 (ADR-0034). `live_health` answers "should a human look
at this?" and the caller decides what to do about it.

---

# Stage 10 — The broker boundary

The final stage reaches a venue.

- `alphalab.broker.protocol.BrokerProtocol` — **one** venue. `PaperBroker` is the
  reference simulation and `RestVenueBroker` the real adapter over
  `HttpVenueTransport`.
- `alphalab.brokers.protocol.BrokerConnectorProtocol` — **many** venues and many
  accounts, routing the canonical `alphalab.broker` types.

Additional venues are integrated by implementing the protocol in their own
package, without modifying higher-level modules. **No named vendor's request
shapes are implemented**, and nothing here has been verified against a commercial
venue; see ADR-0012 and ADR-0031.

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
| Execution path | Turn one market event into orders, fills and accounting |
| Lifecycle | Decide which strategy version an environment should run |
| Adapters | Connect external systems |

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
| OMS | OrderAccepted, OrderFilled, OrderCancelled |
| Portfolio | PositionOpened, PositionClosed, CashConverted |
| Lifecycle | StrategyPromoted, DeploymentRecorded |
| Broker | BrokerConnected, ExecutionReceived |
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

## Execution Events

Represent one market event's progress through the execution path.

Examples

```
MarketValueUpdated

AllocationReservationReleased

OrderAccepted

OrderFilled
```

*(`RuntimeStarted` / `RuntimeStopped` / `RuntimeRecovered` belonged to the orphan
lifecycle state machine inside `alphalab.runtime`, removed in v2.17. The strategy
runtime's own lifecycle transitions are `alphalab.strategy.events.LifecycleTransitioned`.)*

---

## Lifecycle Events

Represent a change to what an environment should be running.

Examples

```
StrategyVersionRegistered

StrategyPromoted

DeploymentRecorded

EnvironmentRolledBack
```

Every one of them names the principal that caused it (ADR-0018).

---

## Adapter Events

Represent communication with external providers and venues.

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
| Data | UniversalDataState |
| Research | ResearchState |
| Replay | ReplayState |
| Portfolio | PortfolioState |
| OMS | OMSState |
| Allocation | AllocationState |
| Market | MarketState |
| Risk | RiskState |
| Execution | ExecutionState |
| Strategy | RuntimeState (`alphalab.strategy.state`) |
| Runtime — step | ExecutionPipelineState |
| Runtime — run | RunState |
| Broker | BrokerState |
| Lifecycle | LifecycleState (`alphalab.lifecycle.state`) |
| Studio | StrategyStudioState |
| Workbench | WorkbenchState |

Each state is immutable.

**Two pairs of names are reused on purpose.** `RuntimeState` in
`alphalab.strategy.state` holds registered strategy instances and their lifecycle
status; it is not a runtime-package state, and the orphan `RuntimeState` that
once was one is removed. `LifecycleState` in `alphalab.lifecycle.state` is the
whole lifecycle registry, while `LifecycleState` in `alphalab.strategy.state` is
an enum naming the stages of a strategy *instance running inside a session*. A
deployed strategy version is started and stopped many times without its stage
changing.

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

Every durable state supports a snapshot, and each is owned by the package that
owns the state: `oms`, `portfolio`, `allocation`, `instrument`, `broker`,
`lifecycle`, `runtime` (the pipeline, the run and the live envelope) and
`portfolio.fx_feed`. See the **State round-trip** table above for the schema
constant each carries.

Snapshots provide

- continuation of a stopped run, byte-identically
- durability across a process boundary
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
A strategy hook raises

↓

LifecycleTransitioned(FAILED)

↓

strategy RuntimeState updated — the other strategies are unaffected
```

Likewise

```
BrokerDisconnected

↓

BrokerState updated
```

Every failure is observable. A failure the system cannot honestly represent is
**refused** rather than recorded: a missing FX rate, a stale one, an instrument
in a currency the run does not settle, and a snapshot whose schema this build
does not read each raise rather than produce a number.

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
execution/  portfolio/  analytics/  market/  instrument/

# Drivers over the execution core
backtesting/  replay/

# Shared infrastructure
common/  persistence/  plugins/  scheduler/

# The lifecycle path — composed by alphalab.lifecycle
lifecycle/  experiment_tracking/  model_registry/  deployment_manager/
studio/  enterprise/  research/  factor_library/

# Data and venue surfaces reached from the execution path
data/  marketdata/  broker/

# Standalone engines
portfolio_optimizer/  optimizer/  reporting/
feature_store/  factor_library/  alt_data/
ml/  deep_learning/  reinforcement_learning/
options/  futures/  crypto/  macro/
cloud_research/  cluster_scheduler/  distributed/
workbench/  research_assistant/
live/  feed/  brokers/
```

Every package follows a consistent internal organization.

> **There is no top-level `alphalab/events/` package**, and `alphalab/core/events`
> was removed in v2.17 along with `kernel`, `integrations` and `production`
> (ADR-0034). `alphalab.common.events` holds `BaseEvent`; every other package
> defines its own events in its own `events.py`.

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
runtime/  strategy/  allocation/  risk/  oms/  execution/  portfolio/  analytics/

market/  instrument/  core/

research/  portfolio_optimizer/  data/  replay/  reporting/

lifecycle/
```

Implements business capabilities.

Each package owns one domain.

---

## Infrastructure

```
common/

persistence/

plugins/

scheduler/
```

Provides platform-wide infrastructure.

Infrastructure packages support other domains without owning business workflows.

---

## Integration

```
marketdata/

broker/  brokers/

feed/  live/
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

BrokerProtocol

↓

runtime.broker_routing  →  ExecutionPipeline.apply_execution_report
```

Research and portfolio modules never communicate directly with broker APIs, and a
venue fill returns through the same function a simulated fill takes.

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
 Research      Portfolio Engine     Lifecycle
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

**Built.** The computation engine, substantially deepened in v3.2 (ADR-0037).

Feature Store owns registration, versioning, metadata, validation and caching
and **computes nothing** — that is stated in its own package docstring, and
`FeatureValueProtocol` is the seam it was designed to be written through.
Factor Library is the consumer it was designed for. Neither package imports the
other; `FactorResult` satisfies the protocol structurally, and
`to_factor_results` is the one place the two vocabularies meet.

```
Dataset  ->  ObservationFrame  ->  FeatureSeries  ->  FeaturePanel
                                        |
                                        +--> FactorResult --> Feature Store
                                        |
                                        +--> ranking / neutralization
                                             IC / decay / turnover / exposure
                                                     |
                                                     v
                                                  Research
```

Two layers share the `feature_id` vocabulary:

- **Style factors (v2)** — `compute_momentum`, `compute_value`,
  `compute_quality`, `compute_carry`, `compute_volatility`,
  `compute_liquidity`. One asset, one instant, one `FactorResult`, computed from
  a `PriceSeries` of domain bars or a `FundamentalSnapshot`.
- **The feature framework (v3.2)** — a typed `FeatureDefinition` with a derived
  identity, computed over an `ObservationFrame` read from a canonical `Dataset`
  into a `FeatureSeries` per symbol and a `FeaturePanel` across the universe,
  with cross-sectional ranking, three named neutralizations, the information
  coefficient, decay, turnover and exposure on top.

`feature_applicability` answers, per feature and per `DataAssetClass`, whether a
computation is meaningful — with a reason. It is advice rather than a gate: the
computation layer refuses what it cannot compute, and whether a defined number
is worth reading is a research judgement the library does not make for the
caller.

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

The execution path remains unchanged.

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
 Universal Data   Research Engine     Portfolio Optimizer      Lifecycle
      │               │                         │               │
      └───────────────┴────────────┬────────────┴───────────────┘
                                   ▼
                   Execution path  →  Broker & Market Adapters
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
- The lifecycle decides what should be running, and records who decided.
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

Lifecycle
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

Adapters
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
| v0.27 – v0.33 | Pre-1.0 engine series — runtime supervision, broker and market-data integrations, portfolio optimization, Universal Data, Strategy Studio, Workbench |
| **v1.0.0** | Stable architecture and engineering foundation |
| v1.34.0 – v1.46.0 | Engine series — feature store, factor library, options, futures, crypto, macro, alternative data, ML, deep learning, RL, cloud research, cluster scheduler, experiment tracking |
| **v2.0.0** | Model registry, research assistant, deployment manager, Enterprise; canonical execution domain models unified (ADR-0008) |
| v2.1.0 | Mark-to-market, the exact accounting identity, O(1) engine histories |
| v2.2.0 | Backtest and replay on one step (ADR-0010); persistent containers; seeded identifiers |
| v2.3.0 | One market-data model, one broker boundary, four environments (ADR-0011, ADR-0012) |
| v2.4.0 | Model and strategy lifecycle (ADR-0013) |
| v2.5.0 | Typed state round-trip and the provider → source link (ADR-0014) |
| v2.6.0 | Allocation authority and attribution truth (ADR-0015) |
| v2.7.0 | Instrument identity and dataset provenance (ADR-0016, ADR-0017) |
| v2.8.0 | Currency roles and run outcomes (ADR-0019 – ADR-0021) |
| v2.9.0 | Durable run state; deterministic identifier continuation (ADR-0022 – ADR-0024) |
| v2.10.0 | The strategy boundary: durable strategy state, populated context (ADR-0025, ADR-0026) |
| v2.11.0 | Instrument classification and sector provenance (ADR-0027) |
| v2.12.0 | The currency authority and the settlement boundary (ADR-0028) |
| v2.13.0 | The run-state store and the durability boundary (ADR-0029) |
| v2.14.0 | Runtime unification: one `RunEngine`, drivers hold no state (ADR-0030) |
| v2.15.0 | Venue transport, streaming, artifact bytes, classification provenance (ADR-0031) |
| v2.16.0 | Live driver, governance, FX valuation, and the refactor audit (ADR-0032, ADR-0033) |
| v2.17.0 | Settlement multi-currency, FX feed, strategy registry, seven removals (ADR-0034, ADR-0035) |
| **v3.0.0** | **Architecture frozen; documentation truth freeze. No capability added** |
| **v3.1.0** | **Universal data ingestion, provenance and the derived dataset version (ADR-0036)** |

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
Version: v3.1.0
Status: Implementation Status (v3.1) describes what is built and is authoritative.
        From "Design Goals" onward the document describes the architectural model
        and long-term target. Both halves name only packages that exist.
```