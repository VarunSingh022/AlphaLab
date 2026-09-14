# AlphaLab — Now and Future

**A long-term project reference, written at v3.0.0.**

This document exists so that a future engineer — including a future version of
the person who wrote AlphaLab — can answer these questions without reconstructing
the whole history:

> What is this? Where does a new thing belong? Who owns this responsibility?
> How does it work? What can I safely change? What must I not change? What was
> deliberately deferred, and why? What is somebody else's to supply? How do I
> verify the project still works?

Everything here is traceable to source, to a test, to an ADR, or to behaviour
verified against the repository. Where something is genuinely unknown it says
**UNKNOWN**; where it is somebody else's to supply it says **EXTERNAL**; where it
is deliberately not part of AlphaLab it says **NON-GOAL**.

`README.md` is the introduction. `docs/ARCHITECTURE.md` is the architecture.
`CHANGELOG.md` is the history. This is the reference.

---

# 1. What AlphaLab is

A Python **library** for building deterministic quantitative research and
algorithmic trading components.

It is not an application. There is no server, no daemon, no scheduler process, no
CLI and no composition root — `tests/regression/test_shared_names_stay_distinct.py`
asserts that the package declares no entry point. The caller owns the process and
the event loop; AlphaLab supplies pure functions over immutable values.

It has **zero runtime dependencies**. `pyproject.toml` declares
`dependencies = []`, and everything — the HTTP transport, the RFC 6455 WebSocket
client, HMAC request signing, TLS policy, JSON serialization, the persistent
containers — is built on the standard library. This is load-bearing rather than
incidental: it is why `FileRunStateStore` can promise atomic writes without a
database, and why a security review of AlphaLab is a review of AlphaLab.

## Current identity

| | |
| --- | --- |
| Version | **3.0.0** |
| Python | 3.12+ |
| License | MIT |
| Author | Varun Kumar Singh |
| Repository | https://github.com/VarunSingh022/AlphaLab |
| Status | **Stable. Architecture frozen.** |

---

# 2. What v3.0.0 means

v3.0.0 adds no capability, moves no ownership boundary, changes no schema and
removes no public name. Three things make it the stable release:

1. **A whole-repository architecture audit** established that there is **no known
   internal problem that would require AlphaLab to be refactored immediately
   after declaring it stable.** Everything that remains is classified as
   deliberate design, an external dependency, or optional evolution.
2. **The work that would have made v3.0 breaking was taken in v2.17 instead** —
   seven deprecated surfaces removed with no compatibility aliases, all four of
   ADR-0032's category C items implemented, zero skipped tests, zero warnings.
3. **A documentation truth freeze** aligned every current-facing document with the
   code.

**"Frozen" does not mean finished.** It means the bar for a particular kind of
change is now an ADR and a major release. Section 14 lists exactly what is
frozen; section 17 lists what can still be added freely.

---

# 3. The shape of the system

AlphaLab is **two wired paths plus standalone engines**. This is the single most
important thing to understand about it, and it is ADR-0009.

```
                    ┌─────────────────────────────────┐
                    │  THE LIFECYCLE PATH             │
   research         │  alphalab.lifecycle             │
   candidate ──────►│  candidate → run → evidence     │
                    │  → model version                │
                    │  → strategy version             │
                    │  → promotion → deployment       │
                    └───────────────┬─────────────────┘
                                    │ authorize_run  (a query with a refusal)
                                    │ strategy.registry (identity → factory)
                                    ▼
                    ┌─────────────────────────────────┐
   market record ──►│  THE EXECUTION PATH             │──► orders, fills,
                    │  RunEngine owns the run         │    positions, P&L,
                    │  ExecutionPipeline owns the step│    analytics
                    │  4 drivers own only the cursor  │
                    └─────────────────────────────────┘

   everything else: standalone engines, reached by neither path
```

## Two tiers, one owner each

| Tier | Owner | State | Owns |
| --- | --- | --- | --- |
| The **step** | `runtime.execution_pipeline.ExecutionPipeline` | `ExecutionPipelineState` (16 fields) | what happens to one market event |
| The **run** | `runtime.run.RunEngine` | `RunState` (8 fields) | how far it has read, what it skipped, what each record produced, the identifier scope a stopped run continues in |

**Drivers hold no state.** `TradingSession`, `BacktestEngine`, `ReplayBacktest`
and `LiveSession` decide only which record comes next and what clock reading
judges it. None is a dataclass; none defines `__init__`. This is what makes
backtest, replay, paper and live *one* loop rather than four, and it is why
backtest/replay parity is structural rather than a coincidence the tests observe.

Verified at v3.0: none of the four drivers holds a field.

---

# 4. Package ownership

All 48 packages, and which path reaches each.

## The execution spine

| Package | Owns |
| --- | --- |
| `core` | The canonical execution domain models: `Side`, `OrderRequest`, `Fill`, `Trade`, `StrategyContribution`, `AssetType`, `OrderType`, `TimeInForce`, and the id validators |
| `runtime` | The execution step, the run, the four drivers, broker routing, and four snapshot modules |
| `strategy` | What a strategy *is*: `StrategyProtocol`, `StrategyStateProtocol`, `StrategyContext`, the `Dispatcher`, the `RuntimeSupervisor`, and the strategy-class registry |
| `allocation` | Intent sizing and netting into `OrderRequest`, the capital budget, the per-order reservation ledger and the contribution ledger |
| `risk` | Pre-trade checks and limits |
| `oms` | The order lifecycle. `oms.order.Order` is *the* lifecycle order |
| `execution` | The deterministic execution simulator, commission models, fill policies, slippage, latency |
| `portfolio` | Cash, positions, the transaction ledger, NAV, per-currency P&L, valuation, margin, exposure, FX and the FX feed |
| `analytics` | Performance reports and attribution |
| `market` | The canonical market-data model, the normalization boundary, market sources, streaming |
| `instrument` | Canonical instrument identity, the registry, classification and its provenance |
| `common` | Version, `BaseEvent`, deterministic serialization, the seeded identifier source, `AppendOnlyLog` / `PersistentMap` / `PersistentSet`, TLS policy, point-in-time helpers |
| `persistence` | The codec spine (`serialize`, typed `decode`, exceptions) and `RunStateStore` |
| `backtesting` | The dataset type and the two drivers over it |
| `replay` | The deterministic replay cursor, clock and session lifecycle |
| `broker` | **One** venue: `BrokerProtocol`, the canonical broker vocabulary, reconciliation, the HMAC transport, `RestVenueBroker`, `PaperBroker` |
| `data` | The canonical **wire** record, and the Universal Data Engine |
| `marketdata` | Provider clients, HTTP transport, the WebSocket client, symbols, subscriptions |

## The lifecycle path

| Package | Owns |
| --- | --- |
| `lifecycle` | The composition: registration, evidence, promotion, deployment, rollback, governance, and the join to the execution path |
| `experiment_tracking` | Experiment runs, parameters, metric history |
| `model_registry` | Model versions, stages, promotion, `ArtifactRef`, the content-addressed artifact store |
| `deployment_manager` | Release packages and the append-only environment ledger |
| `studio` | `StrategyDefinition` — the one record of what a strategy is — plus projects and orchestration |
| `enterprise` | Principals, RBAC, the audit log. Governance reads it |
| `research` | Research workflows and `ResearchScore`, which validation evidence extracts from |

## Standalone engines — reached by neither path

`portfolio_optimizer`, `optimizer`, `reporting`, `feature_store`,
`factor_library`, `alt_data`, `ml`, `deep_learning`, `reinforcement_learning`,
`options`, `futures`, `crypto`, `macro`, `cloud_research`, `cluster_scheduler`,
`distributed`, `workbench`, `research_assistant`, `live`, `feed`, `brokers`,
`plugins`, `scheduler`.

Each is deterministic, individually tested and individually benchmarked. **A
package with no in-repo consumer is a standalone engine by design, not an
orphan** — pinned by
`test_every_zero_consumer_production_package_is_a_standalone_engine`.

## One composition claim that was wrong for thirteen releases

`alphalab.lifecycle` does **not** import `research_assistant`. `README.md`,
`docs/README.md`, `docs/ARCHITECTURE.md` and ADR-0013 said it "composes" it from
v2.4 until v3.0. What actually happens:
`research_assistant` produces a candidate, `to_strategy_definition` lifts it into
the canonical `StrategyDefinition`, and the lifecycle takes *that*. The dependency
runs through the definition, not the package. Read the import graph, not the prose.

---

# 5. Canonical domain models

One name, one meaning, one definition. Changing any of these is a major release.

| Concept | Canonical type | ADR |
| --- | --- | --- |
| Order direction | `core.enums.Side` | ADR-0008 |
| Proposed order | `core.order_request.OrderRequest` | ADR-0008 |
| Lifecycle order | `oms.order.Order` | ADR-0008 |
| Fill / trade | `core.fill.Fill`, `core.trade.Trade` — `float` Unix timestamps | ADR-0008 |
| Strategy attribution | `core.contribution.StrategyContribution` | ADR-0015 |
| Instrument identity | `asset_id` — `uuid5` over `(asset_type, exchange, symbol, currency)` under a frozen namespace | ADR-0016 |
| Top of book | `market.quote.Quote` | ADR-0011 |
| Trade print | `market.tick.Tick` | ADR-0011 |
| Bar | `market.bar.Bar` | ADR-0011 |
| Depth | `market.snapshot.OrderBookSnapshot` | ADR-0011 |
| Stream record | `market.record.MarketRecord` | ADR-0011 |
| Wire record | `data.feed.*` — `float` prices keyed by provider symbol | ADR-0011 |
| What a strategy emits | `strategy.events.Intent` | ADR-0008, `SIGNAL_MODEL.md` |
| What a strategy is | `studio.strategy.StrategyDefinition` | ADR-0035 |
| Money | `Decimal`, exact at the currency minor unit | ADR-0008 |

## The wire/domain split

The single most misread part of the model. `data.feed.Bar` and `market.bar.Bar`
both exist **on purpose**: one is what a provider can fill in knowing nothing
about AlphaLab (`float`, provider symbol, lossy), the other is what the execution
path consumes (`Decimal`, `asset_id`, venue, currency, timeframe). They sit on
opposite sides of one explicit conversion, `market.normalization`.

Collapsing them would force one to lie. `marketdata.feed` and `live.message`
re-export the *same class objects* from `data.feed`, so there is exactly one wire
record per concept, and `test_market_model_convergence.py` asserts it.

---

# 6. The execution path, in order

```
market record
  → publish to the market engine
  → mark to market                 positions re-priced; ONLY unrealized P&L moves
  → resync risk from the marked book
  → strategy dispatch              sees the MARKED portfolio (v2.10)
  → Intents
  → allocation                     sizes, nets, reserves capital per order
  → drop requests with no price    recorded on unpriced_assets
  → settlement authority check     drops an instrument this run may not trade
  → risk                           approve / reject; a rejection releases
  → OMS                            order lifecycle
  → fill policy                    what the venue does with this order now
  → execution                      ExecutionReport (simulated) — or a venue fill returning
  → settlement integrity check     refuses a report in the wrong currency
  → portfolio                      cash, positions, per-currency realized P&L
  → valuation snapshot             one per market event
  → analytics                      on demand
```

Two rules that are easy to break and expensive to re-derive:

- **Mark before decide.** The portfolio is marked and risk resynced *before* the
  strategy is dispatched and before risk evaluates. Otherwise a strategy reads a
  book priced at the previous event and risk evaluates the resulting order
  against a different one.
- **A fresh order per market event.** `ExecutionPipeline` mints a new order per
  event and never re-works an existing one. A partially filled order is cancelled
  and its residual reservation released (v2.5). A strategy wanting to finish a
  large order keeps expressing the intent.

---

# 7. Accounting

The identity, exact over `Decimal` for any price and quantity the engine accepts:

```
equity == deposits - withdrawals + realized_pnl + unrealized_pnl - commission_paid
```

Verified at v3.0 through the real pipeline at 500 / 1k / 2k / 4k / 8k records.

## The rounding policy — `portfolio.money`

1. **Money is exact at the currency minor unit.** Every monetary amount stored in
   `PortfolioState` is an exact multiple of `0.01`. `to_money` is the *only*
   place rounding happens.
2. **Rounding happens once, at entry.** `apply_fill` rounds the notional and
   commission as they enter; the cash movement *and* the cost basis derive from
   those same rounded values.
3. **Prices and quantities are inputs, not money** (`PRICE_QUANT` 1e-4,
   `SHARE_QUANT` 1e-6) and become money only when multiplied into an amount.
4. **A split rounds one part and derives the other by subtraction**, so the parts
   always sum to the exact whole.

Before this policy the ledger and the position rounded the same economic event
independently and drifted by up to five cents on randomized portfolios. Do not
add a second rounding site.

## What is stored and what is derived

- **Stored:** cash (`CashLedger`, keyed by currency), positions, the transaction
  ledger, the event log, and cumulative `realized_pnl` / `commission_paid` — both
  `CurrencyAmounts`, currency to exact amount.
- **Derived, never stored:** unrealized P&L and equity, by
  `PortfolioValuation.snapshot`, which is a **read model** over `PortfolioState`,
  not a second state.

**Realized P&L is not a cash movement.** It is already implicit in the entry cost
and the exit proceeds. Adding it to cash was the D1 defect fixed in v2.0.0.

---

# 8. Money in more than one currency

The hardest part of the system to reason about, and the part most likely to be
broken by a well-meaning simplification.

## Settlement truth and reporting truth are different numbers

- **Settlement truth** is what was earned, in the currency it was earned in,
  permanently. `realized_pnl` and `commission_paid` are per-currency, so a EUR
  fill accrues EUR P&L and **nothing is ever summed across two currencies**.
- **Reporting truth** is one figure in one named currency, converted **on
  demand**, recording every rate used.

Translating at fill time would have been the smaller change and is wrong: it
destroys the only record of what was actually earned and bakes one instant's rate
into a cumulative figure.

## Four currency roles, and they are not one thing

| Role | Where | Authority over |
| --- | --- | --- |
| Instrument currency | `InstrumentRecord.currency` | whether this run may trade the instrument at all |
| Settlement currency | `ExecutionPipelineConfig.currency` + `.also_settles` | what a fill may be denominated in |
| Reporting currency | the argument to a valuation | the one figure a human reads |
| Market-data attribution | `NormalizationPolicy.currency` | what a quote is *labelled* with — **never** accounting |

`ExecutionPipelineConfig.also_settles` is **empty by default**, which is the
single-currency pipeline every run had before v2.17 and is byte-identical to it.

## Two seams, because there are two questions (ADR-0028)

| | Seam 1 — authority | Seam 2 — integrity |
| --- | --- | --- |
| Question | *May this run trade this instrument?* | *Is this report denominated in what this pipeline settles?* |
| Site | `_process_requests`, before the OMS | `_apply_report_to_portfolio` |
| Needs a registry | yes | no |
| Disposition | **drops** the request, retiring both ledgers | **raises** |

The dispositions differ because the vocabularies differ: the pipeline may decline
to create an order; it may not decline a fill that already happened at a venue.
Neither seam subsumes the other, and both were shown necessary.

## FX: every rate is supplied

There is **no default rate, no fallback of 1.0, no triangulation and no implicit
inversion.** Verified behaviour at v3.0:

| Asked for | Answer |
| --- | --- |
| A pair the table does not hold | `MissingRateError` |
| USD→EUR from a EUR/USD rate | **refused** — a real quote has two sides |
| EUR→JPY from EUR/USD and USD/JPY | **refused** — that is a rate nobody quoted |
| A rate older than `max_age_seconds` | `StaleRateError` |
| A zero or negative rate | refused at construction |
| A rate with no `source` | refused at construction — an unattributed rate is the configured rate ADR-0020 rejected |
| Two rates for one pair | refused — which is right is not a question a table answers by picking |
| `with_inverses()` | mints the opposite direction, marked `derived=True`, never replacing a real quote |
| Same-currency conversion | identity, marked `source="identity"` |

Every conversion records the rate, its `as_of` and its source.

## The feed boundary (v2.17)

`portfolio.fx_feed` is a **boundary, not data**. Three rules, one refusal:

| A quote that is… | …is |
| --- | --- |
| newer than what is held for its pair | `APPLIED` |
| byte-identical to what is held | `DUPLICATE` |
| **older** than what is held | `SUPERSEDED`, and **not applied** |
| the same instant, a different rate or source | **refused**, not ranked |

The third is the one that matters: accepting it would move the book's view of the
market backwards because two packets arrived out of order.

**AlphaLab ships no FX rate.** EXTERNAL.

---

# 9. State, snapshots and durability

Ten durable states. Each has **one** snapshot owner, **one** schema constant and
**one** typed decoder.

| State | Snapshot module | Constant | Value |
| --- | --- | --- | --- |
| `OMSState` | `oms.snapshot` | `OMS_SNAPSHOT_SCHEMA` | 1 |
| `PortfolioState` | `portfolio.snapshot` | `PORTFOLIO_SNAPSHOT_SCHEMA` | 3 |
| `LifecycleState` | `lifecycle.snapshot` | `LIFECYCLE_SNAPSHOT_SCHEMA` | 2 |
| `AllocationState` | `allocation.snapshot` | `ALLOCATION_SNAPSHOT_SCHEMA` | 1 |
| `ExecutionPipelineState` | `runtime.snapshot` | `PIPELINE_SNAPSHOT_SCHEMA` | 3 |
| `RunState` | `runtime.run_snapshot` | `RUN_SNAPSHOT_SCHEMA` | 1 |
| `InstrumentRegistry` | `instrument.snapshot` | `INSTRUMENT_SNAPSHOT_SCHEMA` | 1 |
| `BrokerState` | `broker.snapshot` | `BROKER_SNAPSHOT_SCHEMA` | 1 |
| `LiveRunState` | `runtime.live_snapshot` | `LIVE_SNAPSHOT_SCHEMA` | 1 |
| `FxFeedState` | `portfolio.fx_feed` | `FX_FEED_SNAPSHOT_SCHEMA` | 1 |

Plus `persistence.run_state.RUN_STATE_ENVELOPE_SCHEMA = 1`, which versions what
the *store* records about a payload and nothing inside it.

The contract is `restore(capture(s)) == s` — semantic equality, not container
lineage.

## Rules that must not be relaxed

- **Every constant is a module-local literal.** None aliases
  `DEFAULT_SCHEMA_VERSION`, because that constant also versions `BaseEvent`:
  bumping it would version every event in the system as a side effect of one
  subsystem's change. Four regression tests pin the de-aliasing, and each snapshot
  module carries a comment saying why.
- **One readable version per subsystem. No migration framework.**
  `require_schema_version` refuses anything else and names the build that wrote
  it. Two bounded exceptions, both documented: `PIPELINE_SNAPSHOT_SCHEMA` reads
  1/2/3 because the missing fields genuinely *mean* the values it supplies, and
  `OMS_SNAPSHOT_SCHEMA` reads one exact legacy unversioned key set.
- **Envelopes nest, never merge.** The live envelope carries a run snapshot and a
  broker snapshot, each versioned by its own constant, which is why adding venue
  durability in v2.16 moved no schema a backtest writes.
- **Live objects are referenced, not reconstructed.** A strategy instance, a
  simulator, a sizing model, a fill policy and an instrument registry are recorded
  *by type*; `restore` requires them back from the caller and **raises** on a
  missing or mistyped one. Never substituted.
- **Derived indexes are rebuilt, not stored.** The order book's asset and strategy
  indexes, and the instrument registry's provider index. One fact, one home.
- **Identity is re-derived, never read.** `restore` recomputes every `asset_id`
  from its declaration, so a payload cannot assert an identity.
- **Two snapshots deliberately drop fields**, and each says so in its own
  docstring: `LiveRunSnapshot` omits `routed` / `settled` (derivable from the two
  halves — carrying them would give one fact two homes that could disagree), and
  `InstrumentRegistrySnapshot` omits `by_provider` (a rebuilt index).

## Where a payload goes

`persistence.RunStateStore` — four methods over `(run_id, sequence) → payload`.
**Payload-agnostic**: it moves a `str`, imports no snapshot type and decodes
nothing, which is what keeps it correct across schema changes.
`FileRunStateStore` writes atomically and verifies a SHA-256 digest **before any
decoder runs**; `MemoryRunStateStore` is an explicitly named double that nothing
selects automatically and that the file store never degrades into.

A seeded run can stop in one process and finish in another, producing a
**byte-identical** payload — proven across a real interpreter boundary, not
asserted.

## Determinism

`RunConfig.seed` installs a `DeterministicIdSource` for the run.
`IdStreamPosition` is `(seed, draws)` — two readable integers, not a serialized
generator state, so nothing in the persisted format pins a PRNG implementation.
Without a seed, identifiers stay on `uuid4` and only the economics reproduce;
that is deliberate and the source of nondeterminism is visible on the config.

---

# 10. Identity and classification

**An `asset_id` is derived, not minted**: `uuid5` over
`(asset_type, exchange, symbol, currency)` under a frozen namespace, so two
independently configured environments agree with no shared database. Registration
is still required — derivation alone would turn every typo into a new instrument.

`NormalizationPolicy` resolves identity in exactly two named modes, never `None`:

| Mode | Behaviour | Permitted use |
| --- | --- | --- |
| `InstrumentRegistry` | refuses an unregistered `(provider, symbol)` at the boundary | the only mode that may reach a `Fill` |
| `UnresolvedIdentity` | passes the provider symbol through | testing the wire→canonical lift **only** |

`DEFAULT_POLICY` uses the second mode and is therefore a **testing** default;
`ProviderHistorySource.of` refuses it before calling a provider.

**Two tenses, two owners.** The registry says what an instrument *is* classified
as; a `TradeRecord` says what it *was* classified as when the fill happened. A
later reclassification cannot rewrite a completed run, and a run reclassified
mid-run correctly splits across both sectors. Classification is append-only with
`source` and `as_of`, so a correction is a new fact and the entry it corrects
stays readable.

`sector` is deliberately **outside** the canonical key, so a classification can
never re-identify an instrument and orphan its fills — guaranteed three ways, and
ADR-0016's golden identifier is re-derived from a classified record.

**AlphaLab ships no taxonomy and no reference data.** EXTERNAL. Sector is also
the only dimension; industry, country, issuer and rating are each a separate
decision.

---

# 11. Strategies

## What a strategy is

`StrategyProtocol` — a `runtime_checkable` Protocol declaring ten hooks.
`BaseStrategy` is an optional convenience ABC. A strategy may implement the
Protocol directly.

**Of the ten hooks, seven are routed and three are not.** `Dispatcher` routes
`on_tick` / `on_quote` / `on_trade` / `on_bar` by **module and name together**
(never the bare name — three packages define a `TickReceived`, and matching the
name alone routed the wrong class and then blamed the strategy for the resulting
`AttributeError`), and routes `on_fill` / `on_order` / `on_timer` when a caller
supplies those events. `on_start` / `on_stop` / `on_shutdown` are declared and
**never invoked by the runtime**.

`BookUpdated` and `SnapshotCreated` reach no hook at all. NON-GOAL, stated and
pinned.

## Three registries, three different questions

Easy to confuse; they are not the same thing.

| | Answers | Lives in |
| --- | --- | --- |
| `studio.StrategyDefinition` | *what is this strategy?* — author, parameters | `studio` |
| `lifecycle` registration | *which version of it should be live, and who said so?* | `lifecycle` |
| `strategy.registry.StrategyClassRegistry` | *which code runs it?* | `strategy` |

The class registry is deliberately **not** in `lifecycle`, which still constructs
nothing. It stores an identity and a factory and nothing about what a strategy
*is*. It **never resolves a name to code** — no `importlib`, no class-name
derivation, because a name is not a type. A duplicate registration is refused
naming both incumbent and challenger; an unknown identity is refused listing what
*is* registered; a factory returning something undispatchable is refused at
construction rather than at its first market event.

## What a strategy may see

`StrategyContext`, nine fields, all populated: `portfolio` (the **marked** book),
`market`, `clock`, `logger`, `risk_view`, `config`, `orders`, `history`,
`universe`. `history` is bounded at the dispatched event's timestamp, taken from
the **event** and never a wall clock, enforced at construction — that is the
look-ahead guard.

`context.orders` is a **builder and query facade**, not an order-placement API.
The only channel of effect is the `Iterable[Intent]` a hook returns.

A strategy sees its **share** of a netted order, from the allocation contribution
ledger — two strategies whose intents net into one `BUY 100` see 60 and 40, and
neither claims sole ownership.

## The layering rule that constrains everything above

**ADR-0016 decision 3: `alphalab.strategy` acquires no dependency on
`alphalab.market` or `alphalab.instrument`.** It is enforced by a test.

This is why `Intent.instrument` is a bare `str`, why event routing matches
module-and-name rather than `isinstance`, and why the strategy runtime is
importable without the identity authority. Importing the canonical market types
into the dispatcher was tried during v2.16 and the boundary test caught it.

---

# 12. The other subsystems, briefly

**Allocation.** Owns the amount; the pipeline owns the moment.
`release_reservation` takes no amount — it frees whatever the ledger holds, so a
release can neither free more than was reserved nor free it twice. Releasing an
order holding no live reservation **raises** rather than silently subtracting,
which is what makes "exactly once" checkable. `alphalab.allocation` knows nothing
about exchange rates, and that is measured: threading an `FxRates` into it pulled
all eighteen `portfolio` modules into a package that imported none of them.

**OMS.** The sole authority on order state. Every non-trading outcome is terminal
for the order.

**Broker.** Two boundaries: `BrokerProtocol` for one venue,
`BrokerConnectorProtocol` for many venues and many accounts. The connector routes
the canonical types under its historical names — `brokers.ExecutionReport` **is**
`broker.BrokerExecution`, asserted by test. Reconciliation is total: `APPLIED`,
`DUPLICATE`, `UNKNOWN_ORDER`, `TERMINAL_ORDER`, `OVERFILL`, `INVALID` — nothing
is silently dropped. Two pre-trade gates: never on a disconnected connection,
never twice for one OMS order, with the client order id **derived** from the OMS
order id so a retry after a lost response addresses the same order.

**Live.** `LiveSession` is settle → advance → route. The order is the contract: a
fill the venue already reported reaches the portfolio **before** the strategy is
dispatched. Two ledgers answer two different questions —
`BrokerState.executions` ("has the venue-side bookkeeping recorded this fill?")
and `ExecutionState.reports` ("has the *portfolio* booked it?") — and a fill in
one and not the other is the normal case. Gating the portfolio on the broker
layer's answer silently dropped every live fill in the first implementation.

**Governance.** `Governance(enterprise, actor_id, approval_required_in)` is the
**required** second argument of every entry point that changes what is live. Not
optional: an optional gate is one anyone bypasses by calling the function
directly. An approval names an exact `(name, version, environment)` and one
granted by the deployer does not count. An act no principal requested records
`actor_id=""`, which is the honest answer. A rollback needs no approval — a
control that stops a firm taking a bad release down is not a control.

**Artifacts.** Content-addressed: an artifact is the SHA-256 of its bytes, so
storing identical bytes twice is one artifact and a reference cannot name bytes
that hash to something else. The URI is `alphalab-artifact:sha256:<hex>`, never a
filesystem path. It is **not** a second persistence owner: `RunStateStore` owns
run state addressed by `(run_id, sequence)`; this owns bytes addressed by content.

**Streaming.** `market.stream.StreamingSource` **is** a `MarketDataSource` and
nothing more, because `records()` already returned an iterator. It declares
`UNORDERED`, and a regressing record is skipped-and-recorded rather than marking
the portfolio backwards.

**TLS.** One policy, `common.tls`, for all three outbound call sites. It states a
TLS 1.2 floor **explicitly** rather than inheriting whatever the host's OpenSSL
allows — a guarantee that holds because of how a machine happens to be configured
is not a guarantee the code has. No parameter lowers the floor; no
retry-on-older-protocol fallback exists.

---

# 13. Verification

```bash
ruff check .                              # lint
ruff format --check .                     # format
mypy .                                    # strict, 910 source files
pytest -q                                 # 3956 tests, 0 skipped, 0 warnings
pytest -q -W error::DeprecationWarning    # the same 3956
git diff --check
python -m build && twine check dist/*
for f in examples/*.py; do python "$f"; done    # 14
for f in benchmarks/*.py; do python "$f"; done  # 47
```

`make check` runs the first four.

**3956 tests: 1646 unit, 239 integration, 2071 regression.** The regression suite
is the largest deliberately — 77 files, most of them pinning a decision rather
than a behaviour, so a future "simplification" has to break an assertion and read
a reason first.

Neither the zero skips nor the zero warnings can be satisfied by configuration:
`test_the_suite_reports_nothing_deferred.py` reads the **collected items** rather
than the summary line, and spawns a **fresh interpreter** with
`-W error::DeprecationWarning` to import every module in the tree.

## The tests to read before changing anything

| File | Pins |
| --- | --- |
| `test_shared_names_stay_distinct.py` | Eight pairs of same-named things that are not one thing |
| `test_venue_concepts_stay_distinct.py` | Listing exchange vs market-data attribution vs execution venue |
| `test_no_silent_financial_defaults.py` | An AST sweep of the whole package; each exemption earned by a refusal test |
| `test_snapshot_field_coverage.py` | Silent state loss when a state gains a field |
| `test_removed_surfaces_stay_removed.py` | That removed names stay gone, including under a different spelling |
| `test_currency_authority.py`, `test_settlement_multi_currency.py` | The currency roles and the two seams |
| `test_durable_run_state_cross_process.py` | Byte-identical continuation across a real process boundary |
| `test_instrument_identity_reaches_a_fill.py` | The `strategy` → `market` layering ban |
| `test_the_suite_reports_nothing_deferred.py` | Zero skips, zero warnings |

## Performance

The canonical path is **linear**: ~2.0× per doubling, measured 500 → 8,000
records at v3.0, with the accounting identity holding at every size. Engine
histories are `AppendOnlyLog` (O(1) amortized append) and keyed state is
`PersistentMap` / `PersistentSet` (O(1) amortized write, copy-on-branch).

One term is deliberately left super-linear — see section 17.

---

# 14. What must not be changed casually

**These are the frozen invariants.** Changing any of them is a major release with
an ADR.

1. **One owner per responsibility.** The step is `ExecutionPipeline`'s, the run is
   `RunEngine`'s, and drivers hold no state. Do not give a driver a field.
2. **One snapshot owner and one module-local schema literal per state.** Do not
   alias `DEFAULT_SCHEMA_VERSION`. Do not add a migration framework.
3. **The accounting identity, and one rounding site.** `to_money` is the only
   place rounding happens. Do not add a second.
4. **Settlement truth is per-currency and permanent; reporting converts on
   demand.** Do not translate at fill time.
5. **No invented financial value.** No default rate, no 1.0 fallback, no
   triangulation, no silent inversion, no assumed currency, no invented margin
   rate. Refuse instead.
6. **Identity is derived and frozen.** `asset_id` derivation, the canonical key,
   the namespace, and `sector`'s exclusion from the key.
7. **`alphalab.strategy` depends on neither `market` nor `instrument`.**
8. **`alphalab.common` imports nothing else in `alphalab`.** It is the bottom
   layer; the whole graph rests on that.
9. **Zero runtime dependencies.** `dependencies = []`.
10. **Zero package-level import cycles.**
11. **Live objects are referenced by type and required back from the caller**, never
    substituted on restore.
12. **Governance is required, not optional**, at every entry point that changes
    what is live.
13. **No wall clock on the execution path.** Every timestamp comes from the record
    or the event; the one clock in a run is the `now` argument to
    `RunEngine.advance`.
14. **Zero skipped tests and zero warnings.**

## The failure mode to watch for

The most expensive defects in AlphaLab's history were not unknown problems. They
were **known problems fixed unevenly** — a release naming a defect class
correctly and closing only some of its instances. ADR-0031 wrote "every
construction site in the repository passes `object()`" while fifteen remained on
four other fields of the same object. When you fix a class of defect, sweep for
the whole class, mechanically.

The second most expensive: **a document that was true when written and was never
re-read.** Hence section 13's release checklist and
`docs/ENGINEERING_GUIDELINES.md`'s note that the version lives in three places
and the current-state claim in four documents.

---

# 15. Deliberate design — B

Not defects. Each is a decision with a reason and, in most cases, a test that a
future "unification" must break first.

| Kept apart | Why |
| --- | --- |
| `oms.book.OrderBook` / `data.feed.OrderBook` | *My* working orders vs *the market's* resting size. They share no operation. Merging is a category error |
| `portfolio.PortfolioEngine` / `portfolio_optimizer.PortfolioEngine` | Accounting vs construction. Only the first is reachable from the execution path |
| `optimizer` / `portfolio_optimizer` | Two searches, two subjects |
| `broker` / `brokers` | One venue vs many venues and many accounts. Converged in v2.3; the connector routes canonical types under historical names |
| `data.feed.Bar` / `market.bar.Bar` | Wire vs domain, opposite sides of one conversion |
| Three things called a venue | Listing exchange, market-data attribution, execution venue. None derives from another |
| `strategy.LifecycleState` / `lifecycle.LifecycleState` | A strategy *instance's* stage vs the lifecycle registry |
| `strategy.RuntimeState` | Holds strategy instances. Not a runtime-package state |
| Two matrix inversions | Different input classes; neither is a shared numerical layer |
| `AppendOnlyLog` batch operations keeping local copies | One copy in and one value out is O(collection) per *call*, not per element; writing each element through `PersistentMap.set` measured ~30% worse |

Also deliberate: **no CLI, no server, no daemon, no event bus, no composition
root, no `SettlementPolicy` object, no migration framework, no per-environment
promotion policy, no supervised live process, no authentication or credential
handling.** Each is a NON-GOAL with a recorded reason.

---

# 16. External dependencies — C

Real, and **not AlphaLab's engineering to complete.** None of these is unfinished
internal work.

| | Status |
| --- | --- |
| **Verification against a commercial venue** | EXTERNAL. The transports are written to protocol and exercised end to end over real sockets against local servers that verify signatures, timestamp windows, idempotency keys and accept tokens. This environment has no network egress and holds no vendor credentials |
| **Named vendor request shapes** | EXTERNAL. Each differs per venue and belongs to an adapter |
| **FX data** | EXTERNAL. v2.17 ships the rate-feed boundary and not a single rate |
| **Classification data** | EXTERNAL. v2.11 ships the mechanism and v2.15 its provenance; no taxonomy and no reference-data feed |

The honest summary of connectivity: **the connectivity exists; the vendor
integration does not.**

---

# 17. Optional future evolution — D

Could be built. **Nothing depends on any of it**, and no commitment is made. None
of these is a defect.

- **Classification dimensions beyond sector**, and sector-based risk limits.
  `sector_exposure` is visibility; no risk check reads a sector.
- **A vendor adapter package** over the existing transport — the smallest step
  from connectivity to integration.
- **Consolidating the two identical `AssetClass` enums** in `live.provider` and
  `marketdata.symbols`. Field-for-field identical, un-aliased, and the one
  collision of this kind the repository has not resolved — `brokers` aliases the
  canonical `core.enums.AssetType` and `data` renames its own `DataAssetClass`.
  Neither is on the canonical path and neither is persisted.
- **A look-ahead guard on FX rates.** `max_age_seconds` bounds how *old* a rate
  may be; a **future-dated** rate is not refused. The feed path cannot produce one
  (`SUPERSEDED`), and every conversion records the rate's `as_of` and source, so
  the fact is visible. A caller handing over a table by hand is trusted with it.
- **A start offset on `AppendOnlyLog`**, which is what
  `OptimizerState.pending_trials` needs to stop being super-linear. It was
  implemented, measured at **+3.9%** on `benchmark_execution_pipeline`, and
  **refused on that evidence** — the same trade ADR-0028 refused at +1.78%.
  `alphalab.optimizer` has no in-repo consumer, so the term is off every canonical
  path. Do not reopen this without new measurement.
- **Pipeline-driven `on_fill` / `on_order` / `on_timer`.** Declared and routed;
  the pipeline constructs no such events. Wiring them needs a second dispatch per
  event and would change intent ordering and every parity baseline.
- **Per-strategy sub-ledgers in `PortfolioEngine`.** The strategy-runtime design
  anticipated them; the contribution ledger answers the attribution question
  without a second book.
- **A hook timeout, a plugin static-analysis pass, an independently versioned
  `strategy-api`, hot reload, a threading model.** All designed in
  `docs/architecture/strategy/`, none implemented; that directory's README
  carries the full list.

---

# 18. Release history

| | |
| --- | --- |
| v1.0.0 | Architectural foundation |
| v1.34.0 – v1.46.0 | The engine series (13 standalone packages) |
| v2.0.0 | Canonical execution domain models; four more packages |
| v2.1.0 – v2.5.0 | Correctness and integration: mark-to-market, the exact identity, unified backtest/replay, the market-data and broker convergence, the lifecycle, typed state round-trip |
| v2.6.0 – v2.13.0 | Authority and durability: allocation authority, instrument identity, currency roles, identifier continuation, the strategy boundary, classification, the currency authority, the run-state store |
| v2.14.0 – v2.17.0 | Unification and completion: one `RunEngine`, real transports, the live driver and governance and FX, then settlement multi-currency and the final cleanup |
| **v3.0.0** | **Architecture frozen. Documentation true. No capability added** |

35 ADRs, in `docs/ADR/`. Every supersession is stated explicitly in the
superseding ADR's Status block; read the Status block first.

---

# 19. How to extend this safely

**Adding a standalone engine.** Follow the existing shape: `__init__.py` with an
honest `__all__`, `engine.py`, `state.py`, `events.py`, `validation.py`,
`exceptions.py`, `views.py`. Immutable state, pure functions, its own tests under
`tests/unit/<package>/` and its own benchmark. It will have no in-repo consumer,
and that is correct.

**Adding a vendor adapter.** Implement `BrokerProtocol` (one venue) or a
`MarketDataSource` in your own package, and normalize into the canonical types on
the way out. Nothing above the boundary learns which vendor it was.

**Adding a strategy.** Implement `StrategyProtocol` or subclass `BaseStrategy`.
Return `Intent`s; never place orders. If it holds durable internal state,
implement `StrategyStateProtocol` too — both directions of the codec, because the
shared encoder writes a `Decimal` and a `str` identically and no generic decoder
can tell them apart afterwards.

**Changing something on the execution path.** Read the relevant ADR first, then
the regression test that pins it. If your change requires breaking an assertion
in `test_shared_names_stay_distinct.py` or `test_venue_concepts_stay_distinct.py`,
stop and read the reason in that test's docstring — it was written for you.

**Before any release**, run the full checklist in
`docs/ENGINEERING_GUIDELINES.md`. The version lives in three places and the
current-state claim in four documents; they have drifted before.

---

# 20. Open questions

Genuinely unresolved, recorded so they are not rediscovered:

- **UNKNOWN: how the transports behave against a real venue.** They are correct
  against the protocols as written and against local servers that verify the hard
  parts. Whether a commercial venue's quirks break them cannot be known from here.
- **UNKNOWN: whether the linear scaling holds at a workload far beyond what is
  benchmarked.** Measured to 20,000 transitions in the standalone packages and
  8,000 records on the canonical path; beyond that is extrapolation.
- **UNKNOWN: whether the single-threaded model is sufficient** for a deployment
  running many strategies at high event rates. The design anticipated sharding;
  nothing was built, and nothing has needed it.

---

*Written at v3.0.0. If you are reading this long after, check the version in
`pyproject.toml` first: where this document and the code disagree, the code is
right, and this document has a bug worth fixing.*
