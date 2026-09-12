# AlphaLab Roadmap

This document tracks the evolution of AlphaLab.

The roadmap is a guide rather than a strict schedule. Priorities may change based
on community feedback and project needs.

---

# Delivered

PR-034 through PR-050 are complete. Each PR shipped as its own minor/major release
and each package is a standalone, individually tested engine. The feature notes
below are kept as a historical record of scope.

| PR | Package | Shipped in |
|----|---------|------------|
| PR-034 | Feature Store | v1.34.0 |
| PR-035 | Factor Library | v1.35.0 |
| PR-036 | Options Engine | v1.36.0 |
| PR-037 | Futures Engine | v1.37.0 |
| PR-038 | Crypto Engine | v1.38.0 |
| PR-039 | Macro Engine | v1.39.0 |
| PR-040 | Alternative Data | v1.40.0 |
| PR-041 | Machine Learning | v1.41.0 |
| PR-042 | Deep Learning | v1.42.0 |
| PR-043 | Reinforcement Learning | v1.43.0 |
| PR-044 | Cloud Research | v1.44.0 |
| PR-045 | Cluster Scheduler | v1.45.0 |
| PR-046 | Experiment Tracking | v1.46.0 |
| PR-047 | Model Registry | v2.0.0 |
| PR-048 | AI Research Assistant | v2.0.0 |
| PR-049 | Deployment Manager | v2.0.0 |
| PR-050 | AlphaLab Enterprise | v2.0.0 |

v2.0.0 also unified the canonical execution-path domain models (R1–R4) and fixed
two portfolio/analytics defects (D1/D2). See `CHANGELOG.md`.

v2.1.0 — "Execution + Portfolio Correctness" — added mark-to-market to the
execution pipeline, separated the portfolio's cash / realized P&L / unrealized
P&L / commission accounting behind an explicit accounting identity, made
execution invariants (unpriced requests, terminal rejected orders, per-fill P&L
attribution) explicit, and fixed the O(N^2) event/history accumulation that had
prevented `benchmark_risk_engine.py` from completing. No new packages. See
`CHANGELOG.md`.

v2.2.0 — "Unified Backtesting + Replay" — turned the existing engines into one
deterministic dataset → analytics workflow, and closed the four v2.1 limitations
that blocked it:

- `alphalab.backtesting` composes `ExecutionPipeline` into a real backtest.
  There is no backtest-only order, fill or portfolio model.
- `alphalab.replay` now drives that same path, so a replay produces orders,
  fills and P&L identical to the equivalent backtest (ADR-0010).
- The OMS order book moved onto persistent containers: the 100k-order benchmark
  went from ~16 minutes to 7.5s, and from quadratic to linear.
- Allocation reservations became a per-order ledger, released exactly once.
- `OMSState` gained a complete, round-trippable snapshot projection.
- Identifiers became seedable, so a seeded run reproduces field for field.

One new package (`backtesting`), which is an integration package, not another
standalone engine. See `CHANGELOG.md`.

v2.3.0 — "Market Data + Broker/Live Execution" — closed both items v2.2
deferred, and is a connectivity/convergence release rather than a feature one:

- **One canonical market-data model.** `alphalab.market` is the domain model the
  execution path consumes; `alphalab.data.feed` is the one wire record, which
  `alphalab.marketdata.feed` and `alphalab.live.message` now re-export instead
  of redefining. `data.Bar` and `market.Bar` both remain, deliberately: they sit
  on opposite sides of a conversion (ADR-0011).
- **An explicit normalization boundary.** `alphalab.market.normalization` states
  its rules for precision, timestamps, symbol identity, and what happens to
  data that is invalid, unreported, or stale.
- **One canonical broker boundary.** `alphalab.broker` defines the vocabulary
  and the adapter contract; `alphalab.brokers` routes those types instead of
  redefining four of them. Reconciliation gives defined answers to duplicate
  fills, out-of-order fills, unknown fills, overfills and the cancel/fill race.
- **Four environments, one execution path.** Backtest, replay and paper produce
  byte-identical fills, orders, cash, positions and equity curve.
  `ExecutionRouting.EXTERNAL` is live's only genuine difference.
- **The broker and market-data layers stopped being quadratic.** The 100k-order
  PaperBroker benchmark went from 676.70s to 4.65s; market-data ingestion no
  longer slows down as the universe widens.

No new engine packages. See `CHANGELOG.md`, ADR-0011 and ADR-0012.

**v2.3 does not add live trading.** It adds the adapter contract a live venue
would be reached through. There is no connectivity to any real venue in this
repository, and every vendor client is a stub.

v2.4.0 — "Model + Strategy Lifecycle" — connects four packages that each shipped
one stage of a lifecycle and never met: Experiment Tracking (PR-046), the Model
Registry (PR-047), the AI Research Assistant (PR-048) and the Deployment Manager
(PR-049). Between them there was one link — `ModelVersion.run_id`, an
unvalidated string — and one convention nothing produced or parsed.

- **One package composes them.** `alphalab.lifecycle` is an integration package,
  not a fifth engine, following the `alphalab.backtesting` pattern from v2.2.
  Each of the four still works on its own and none changed shape.
- **A strategy version exists.** `StrategyDefinition.version` was a free-form
  string; `StrategyVersion` is immutable and numbered, and keeps the strategy
  line, the version, the model version it runs and its deployments apart.
- **Promotion has preconditions.** It requires evidence that passed a stated
  policy, and a model version that is itself staged. It refuses to reach
  `PRODUCTION` at all: a strategy version goes live by being deployed.
- **One source of truth for what is live.** The append-only deployment ledger.
  `model_registry.DeploymentMetadata` is now derived from the deployment that
  happened rather than asserted by a caller.
- **Evidence is recorded, not invented.** `ValidationEvidence` extracts metrics
  from the `PerformanceReport` and `ResearchScore` AlphaLab already produces,
  and its id is a digest of its own content. A passing policy claims that the
  stated thresholds were met — not statistical significance.
- **Rollback is first-class and deterministic.** The version restored is the one
  the ledger names as previously active.
- **The lifecycle registries stopped being quadratic.** Every write path in
  experiment tracking, the model registry and the deployment manager was
  ~4x per doubling; all are now ~2x.

One new package (`lifecycle`), which is an integration package. See
`CHANGELOG.md` and ADR-0013.

**v2.4 does not add artifact storage.** A model version *references* an
artifact — a location, a media type, a checksum, a size. AlphaLab never reads,
writes or hashes those bytes, and there is no object store in this repository.

v2.5.0 — "State Round-Trip and the Live Data Path" — takes three capabilities
that already existed, were already tested, and were unreachable, and makes them
reachable. It also decides two behaviours that had never been decided.

- **States can be read back.** `capture` / `restore` give `PortfolioState` and
  `LifecycleState` typed round-trip alongside `OMSState`. Before this, every
  state serialized deterministically and exactly one could be reconstructed;
  `deserialize()` returns `Any`. The contract is semantic equality —
  `restore(capture(s)) == s` — applied identically to every state (ADR-0014).
- **Decoding is typed and explicit.** `alphalab.persistence.decode` raises
  `StateDecodeError` naming the field, refuses an unknown `schema_version`, and
  is deliberately not a reflective object mapper.
- **`alphalab.persistence` has production consumers**, for the first time.
- **A market-data provider reaches the execution path.**
  `alphalab.market.provider` normalizes a provider's historical bars into
  canonical records and satisfies `MarketDataSource` — the link v2.3 was
  missing, which left `normalize_wire_*` with no production caller at all.
- **Unordered sources have defined semantics.** A regressing record raises under
  `CHRONOLOGICAL` and is skipped-and-recorded under `UNORDERED`. Nothing is
  buffered or reordered.
- **A partially filled order terminates.** Its remainder is withdrawn and its
  residual reservation released, applying the pipeline's own
  one-order-per-event rule to the branch that had skipped it. Bookkeeping
  changes; cash, positions, P&L and the equity curve do not.
- **The replay cursor stopped being quadratic** — the last one on a wired path,
  ×3.11 → ×2.01 per doubling — and the benchmark now measures the API the
  integrated path uses instead of steering around it.

Also corrects documentation that disagreed with the code: ADR-0012 called every
vendor client a stub, and a real HTTP transport and Binance market-data client
have existed since v1.39.0.

No new engine packages. See `CHANGELOG.md` and ADR-0014.

**v2.5 does not add live trading, streaming market data, or artifact storage.**
The provider source reads a finite historical range; no broker adapter reaches
any venue.

---

# Feature notes (delivered)

Scope notes for each delivered PR. See the table above for the release each shipped in.

## PR-034

Feature Store

Institutional feature engineering framework

Features

- Feature registry
- Versioning
- Metadata
- Feature validation
- Feature caching

---

## PR-035

Factor Library

Reusable quantitative factors

Examples

- Momentum
- Value
- Quality
- Carry
- Volatility
- Liquidity

---

## PR-036

Options Engine

Support for

- Option chains
- Greeks
- Volatility surfaces
- Pricing models
- Strategy simulation

---

## PR-037

Futures Engine

Support for

- Continuous contracts
- Rolls
- Curve analysis
- Calendar spreads

---

## PR-038

Crypto Engine

Support for

- Spot
- Futures
- Perpetuals
- Funding rates
- Exchange normalization

---

## PR-039

Macro Engine

Support for

- Economic indicators
- Central bank events
- Yield curves
- Inflation
- GDP

---

## PR-040

Alternative Data

Examples

- News
- Sentiment
- Satellite
- Shipping
- ESG
- Credit cards

---

## PR-041

Machine Learning

Features

- Feature pipelines
- Training
- Cross validation
- Prediction
- Evaluation

---

## PR-042

Deep Learning

Support for

- LSTM
- Transformers
- CNN
- Sequence models

---

## PR-043

Reinforcement Learning

Features

- Trading environments
- Policy optimization
- Agent evaluation

---

## PR-044

Cloud Research

Distributed quantitative research

Features

- Remote execution
- Worker pools
- Cluster management

---

## PR-045

Cluster Scheduler

Support for

- Job scheduling
- Queue management
- Distributed orchestration

---

## PR-046

Experiment Tracking

Features

- Experiment history
- Metrics
- Parameters
- Versioning

---

## PR-047

Model Registry

Features

- Versioning
- Promotion
- Rollback
- Deployment metadata

---

## PR-048

AI Research Assistant

Capabilities

- Strategy generation
- Research automation
- Report generation
- Workflow orchestration

---

## PR-049

Deployment Manager

Features

- Packaging
- Release management
- Rollbacks
- Production deployment

---

## PR-050

AlphaLab Enterprise

Enterprise capabilities

- Authentication
- RBAC
- Audit logging
- Collaboration
- Multi-user workspaces
- Compliance
- Secrets management

---

v2.13.0 — "Durable Run State" — gives a captured run somewhere to go, and is an
additive release with no new packages, no breaking changes and no movement of any
existing schema constant:

- **Durability has one owner.** `alphalab.persistence.RunStateStore` — four
  methods over `(run_id, sequence) -> payload`: `put`, `get`, `latest`,
  `list_runs`. Before it there were **zero filesystem calls** in `alphalab`,
  `MemoryStorage` was the only `PersistenceProtocol` implementation and lost
  everything on exit, and no production module imported `runtime.snapshot`,
  `runtime.session_snapshot` or `backtesting.snapshot` at all.
- **The store is payload-agnostic.** It moves a `str`, imports no snapshot,
  runtime, session, backtesting, portfolio, OMS or strategy module, and never
  decodes what it holds. That is deliberate and load-bearing: it is what keeps
  the boundary correct through the runtime unification ADR-0023 anticipates.
- **One real backend, one named double, no fallback.** `FileRunStateStore` is
  standard library only, writes atomically through a temporary file and
  `os.replace`, records a SHA-256 digest and **verifies it before any decoder
  runs**, and requires a root that already exists and is writable — a missing
  root, a file, or an unwritable directory each raise. `MemoryRunStateStore` is
  an explicitly named test double a caller constructs by hand; nothing selects it
  automatically and the file store never degrades into it. This is the
  `Transport` / `HttpTransport` / `StaticTransport` shape, applied to
  persistence.
- **`RunStateRef` is an identity, not a location.** `RunStateRef(run_id,
  sequence)` renders `run_id@sequence` on the separator
  `alphalab.lifecycle.identity` already uses, and carries no URI, path, checksum
  or byte size. A backend's addressing stays inside that backend, so a second
  backend would change no caller. Deliberately *not* shaped like `ArtifactRef`,
  which describes bytes AlphaLab never holds.
- **The run identity is the caller's.** An opaque string supplied at `put`,
  following ADR-0017's treatment of `dataset_id`. It is **never written into any
  captured state**, which is what keeps every snapshot schema still.
- **Persistence draws no identifier from the run's stream.** A full `put` / `get`
  cycle inside `id_scope` advances `draws` by **exactly zero** — both backends,
  every method in isolation, and the refusal paths too. The deprecated store took
  one per operation, and the two it took were the run's own next two identifiers;
  `load_snapshot` drew as well, so reading a run back would have moved its
  stream.
- **Cross-process continuation is proven, not asserted.** A seeded run processes
  five of eleven records, captures, serializes and stores; a **separate
  interpreter** — a fresh `sys.executable`, told everything in JSON and nothing by
  pickle — restores against newly constructed runtime objects, resumes, and
  processes the remaining six. The final serialized payload is **byte-identical**
  to an uninterrupted run. The workload's strategy trades on the parity of a
  counter it owns, so a child that lost the strategy state would fail loudly.
- **Storage is an explicit caller action between completed steps.** No store
  symbol appears anywhere in `alphalab.runtime`, `alphalab.backtesting`,
  `alphalab.strategy` or `alphalab.replay`, and there is no append-per-event API.
  One capture plus serialize of a 1,600-event run costs 1.43s against 0.40s for
  the run itself, so per-event capture would make a run quadratic in its own
  length. A run that never persists is bit-for-bit the v2.12 run.
- **The encoder got 3.70x faster for a byte-identical payload.** Resolving
  `__serializable__` on the type rather than through a `runtime_checkable`
  protocol check removed ~60% of `serialize`: 698.0 ms -> 188.8 ms on an
  800-event snapshot. Every snapshot in the repository benefits.
- **No schema constant moves.** `PIPELINE` stays 2, `SESSION`, `BACKTEST`,
  `ALLOCATION`, `OMS` and `LIFECYCLE` stay 1, `PORTFOLIO` stays 2,
  `DEFAULT_SCHEMA_VERSION` stays 1. Only `RUN_STATE_ENVELOPE_SCHEMA = 1` is new,
  and it versions the store's own wrapper and nothing inside it.
- **The original nine-module store is deprecated, removed in v3.0.** Measured at
  v2.12 it had zero production importers. The codec spine — `serializer`,
  `decode`, `exceptions` — is canonical and untouched, which is why the notice is
  a PEP 562 hook on the package rather than an import-time warning that would
  fire on the whole execution path. **Nothing is removed in v2.13.**
- **`production.Checkpoint` and `RecoveryEngine` now say what they do.**
  Documentation only: their state fields are opaque strings the package never
  decodes, and `recover` restores nothing.

**Deferred out of v2.13, deliberately:** artifact byte storage and any
`ArtifactStore` — nothing in the tree produces artifact bytes, and `ArtifactRef`
is unchanged and unmoved; cloud or vendor storage backends; FX and multi-currency
valuation; streaming; live venue transport; governance/RBAC; replay
resumability, since `ReplayState` has no snapshot and the replay cursor drives a
second identifier stream; recording live-object *parameters* rather than class
names, which would move the pipeline schema; trimming the payload, of which 75.2%
is engine event logs no engine *decision* reads but which ADR-0023's Class 1
includes; incremental or event-sourced checkpoints; and any removal of a
deprecated package. See ADR-0029.

**Runtime unification was the next architectural seam, and v2.14 closed it.**
ADR-0023 decision 1 recorded that `SessionState` and `BacktestState` were the
layer a future integrated-runtime release was expected to reshape, and split the
snapshot envelopes so that reshape could move their two constants without
versioning the stable pipeline core. v2.13's store was built so that reshape
could not reach it. ADR-0030 performed it: both states became one `RunState`
behind a single `RUN_SNAPSHOT_SCHEMA = 1`, `PIPELINE_SNAPSHOT_SCHEMA` stayed at
2, and `alphalab/persistence/` changed no line of code.

---

v2.11.0 — "The Security Master" — completes the deferral ADR-0016 N5 was written
to make safe, and is an additive release with no new packages, no breaking
changes and no schema movement:

- **An instrument can be classified, and reclassified.**
  `classify_instrument(registry, asset_id, sector)` and its plural write
  `InstrumentRecord.sector`, which ADR-0016 documented as mutable and
  `register_instrument` made immutable by refusing any record whose content
  differed. Keyed by `asset_id` rather than by a record; `sector=None`
  unclassifies; the label already in effect returns the same registry object and
  writes nothing; copy-on-write through `PersistentMap.set`, so a classification
  is `O(1)` and `N` cost `O(N)`.
- **It cannot re-identify an instrument**, guaranteed three ways: `sector` is
  outside the canonical key, the signature exposes no identity field, and
  `dataclasses.replace` refuses `asset_id` outright because that field is
  `init=False`. All three are pinned by tests, including a re-derivation of
  ADR-0016's golden identifier from a classified record.
- **A sector label is validated, and not like a key field.**
  `normalize_sector_label` strips surrounding whitespace and refuses an empty,
  whitespace-only, control-character or non-string label — but permits internal
  whitespace and non-ASCII, because a sector derives no identifier and
  `"Consumer Discretionary"` is a real name. Case is preserved on ADR-0019's
  precedent that a label used as a map key is compared exactly.
- **The pipeline reads it once per fill**, in `_apply_report_to_portfolio`, and
  freezes it onto `TradeRecord.sector_id`. That site is reached from the
  simulated path and from `apply_execution_report`, so backtest, replay, paper
  and live agree on the sector structurally rather than by convention.
- **`pnl_by_sector` produces a real breakdown**, and
  `ExposureStatus.sector_exposure` — declared, persisted and decoded since
  before v2.6 and populated by nothing — is filled from the same authority, on
  signed market value, inside the pass that already walked the positions.
  `alphalab.analytics` is untouched: the consumer was already correct.
- **Reclassification never rewrites history.** The registry says what an
  instrument *is* classified as; a trade record says what it *was* classified as
  when the fill happened. A run reclassified mid-run correctly splits across both
  sectors, and restoring a snapshot with a differently classified registry leaves
  every completed record alone.
- **No schema constant moves** — `PIPELINE` stays 2, `SESSION`, `BACKTEST`,
  `ALLOCATION`, `OMS` and `LIFECYCLE` stay 1, `PORTFOLIO` stays 2 — no identifier
  is drawn, and a run configured with no registry is byte-identical to v2.10.0:
  the same serialized payload, the same identifier draws, the same exposure
  figures.

**Deferred out of v2.11, deliberately:** any taxonomy or reference data;
classification dimensions other than sector; a canonical or case-folded sector
vocabulary; distinguishing *why* a sector is unknown; sector-based risk limits;
persisting the registry; `InstrumentRecord.currency` reaching `Position.currency`
and the FX work that depends on it; and everything already deferred below. See
ADR-0027.

---

v2.10.0 — "The Strategy Boundary" — closes both halves of the surface v2.9
deferred, and is an additive release with no new packages and no breaking
changes:

- **A strategy can declare durable internal state, and a fresh instance gets it
  back.** `StrategyStateProtocol` is a second, separate protocol
  (`strategy_state_version` / `capture_state` / `restore_state`);
  `StrategyProtocol` is unchanged and every existing strategy keeps its current
  guarantee. The codec is two-sided and both directions are required, because
  the shared encoder writes a `Decimal` and a `str` identically and no generic
  decoder can recover the type afterwards — "return something encodable" is only
  half a contract. A half-declared codec is refused at capture rather than read
  as no codec.
- **An unencodable state is refused where it was produced.** Capture puts the
  value through the existing `DeterministicEncoder` immediately, so it can never
  reach a snapshot that looks valid in memory and fails at some later
  `serialize`. The same step normalizes the payload to JSON-decoded primitives,
  so `restore_state` receives one shape whether the snapshot travelled through
  JSON or not. No encoder branch was added; a strategy type with no JSON form
  uses `__serializable__`, which already existed.
- **`PIPELINE_SNAPSHOT_SCHEMA` moves 1 → 2**, for one field per strategy record.
  A version-1 payload stays readable and means "no strategy was asked" — the OMS
  precedent, since it is missing nothing. `SESSION_`, `BACKTEST_`,
  `ALLOCATION_`, `OMS_`, `PORTFOLIO_` and `LIFECYCLE_SNAPSHOT_SCHEMA` do not
  move, which is ADR-0023's envelope split working as designed and exercised for
  the first time.
- **A strategy sees the marked portfolio.** `StrategyContext` had nine fields
  since v0.10.0 and every construction site passed `object()` for six of them.
  The pipeline now assembles the portfolio, order, risk and market fields from
  the marked and resynced locals that already existed two lines above the
  dispatch that could not see them.
- **Order attribution comes from the allocation contribution ledger.**
  `OrderBook.orders_for_strategy` answers nothing for a netted order, because
  ADR-0015 leaves one unattributed on purpose. Two strategies whose intents net
  into one `BUY 100` each see their own share, and neither claims sole
  ownership. The view covers live orders only, because ADR-0021 retires a
  contribution at terminal state.
- **The caller's `context_factory` is unchanged.** Its signature, and its
  `clock`, `logger` and `config`, survive; the pipeline overlays only what it
  owns, and a pipeline-owned field always wins so no caller can install a second
  source of truth. Contexts are built only for running strategies.
- Two ADRs are written to disk: **ADR-0025** (strategy state ownership and the
  capture contract) and **ADR-0026** (`StrategyContext` population and the
  visibility boundary). Both were written before their implementation, and both
  status lines now record what shipped.

**Deferred out of v2.10, deliberately:** `StrategyContext.history` and
`.universe`; allocation visibility through the context; mutable runtime services
in any context field; reviving the `on_fill` / `on_order` / `on_timer` hooks,
which would require a second strategy dispatch per event; unifying `SessionState`
and `BacktestState`; redefining `StrategyRecord.config` semantics, which remain
exactly as v2.9 left them (ADR-0025 decision 4); promoting the four cross-package
private decoders; and everything already deferred below.

---

v2.9.0 — "Durable Run State" — lets a run stop and continue, and is a
correctness release with no new packages and no breaking changes:

- **A continued run no longer re-mints identifiers it has already used.** A seed
  said where the deterministic stream started; nothing said where it had
  reached, so a resumed run rebuilt its generator at zero. Measured on v2.8.0,
  sweeping every restore point across a workload producing 41 identifiers found
  up to 4 duplicates — a later `fill_id` on a UUID an earlier `execution_id`
  already held — against zero for the uninterrupted control. Nothing raised.
  `IdStreamPosition` is `(seed, draws)` and lives on `ExecutionPipelineState`:
  two readable integers rather than a serialized generator state, so nothing in
  the persisted format is pinned to one PRNG implementation. Every `new_id()`
  call site, the `ContextVar` and `derive_asset_id` are untouched.
- **The execution path round-trips.** `PipelineSnapshot` captures
  `ExecutionPipelineState`; `SessionSnapshot` and `BacktestSnapshot` capture the
  run bookkeeping around it, one per owning package because
  `alphalab.backtesting` imports `alphalab.runtime` and never the reverse, so a
  single shared module would close an import cycle. `AllocationState` gets a
  versioned snapshot of its own, because ADR-0021's ledgers decide whether a
  restored run's committed capital and attribution are correct. Live objects are
  recorded by type and required back from the caller, raising on a missing or
  mistyped one and never substituting — ADR-0014's rule, finally applied to the
  execution path.
- **A venue fill delivered twice is applied once.** `_apply_reports` never wrote
  to `ExecutionState`, so the `reports` map a duplicate check reads stayed empty
  on the one path where redelivery happens. Measured on v2.8.0 for a partial
  fill: cash 999,600 → 999,200, position 4 → 8, two pipeline fills for one venue
  execution. A repeated `execution_id` now returns the state unchanged, the rule
  `broker.reconciliation` already stated for `DUPLICATE`.
- **A working external order can be ended.** Under `EXTERNAL` routing a venue
  fill had a route home and a rejection, cancellation or expiry had none:
  measured on v2.8.0, six externally routed events left six open orders holding
  six reservations, six contributions and 3,000 of committed notional with no
  way to retire any of it. `apply_terminal_outcome` is that route. The caller
  supplies the outcome; no venue is contacted and no `BrokerState` is built.
- **`OMSState` payloads declare a schema version.** `OMS_SNAPSHOT_SCHEMA = 1`,
  with exactly one bounded legacy path: an unversioned payload is read only when
  its top-level keys match `LEGACY_UNVERSIONED_V0` exactly. A missing
  `schema_version` is never read as version 1.
- **`alphalab.persistence` appends in linear time.** Three containers were
  rebuilt per append and the snapshot index per save. Measured on v2.8.0, 32,000
  appends took 14.0 s at ~4.5x per doubling and the 100,000-event benchmark did
  not finish; it is now 0.24 s at 2.02x per doubling, and the benchmark
  completes in 1.97 s. This applies the v2.1/v2.2 container pattern to the one
  package that missed it; `PersistenceProtocol` is unchanged.
- **`restore` re-runs every construction-time validation `initialize` enforces.**
  `_require_one_account_currency` had exactly one call site and restore did not
  go through it, so a snapshot could rebuild a state `initialize` would have
  refused. ADR-0019's guarantee now holds on both paths into a pipeline state.
- Six ADRs are written to disk: **ADR-0019**–**ADR-0021** record decisions taken
  and shipped during v2.8 without being written down, and **ADR-0022**–**ADR-0024**
  are this release's.

**Deferred out of v2.9, deliberately:** a `StrategyStateProtocol` and any capture
of strategy-internal state, which is the one precondition of the equivalence
contract the caller must satisfy itself; `StrategyContext` completion; unifying
`SessionState` and `BacktestState` or removing the parallel runtime mechanism;
promoting the four cross-package private decoders to a shared public surface;
schema constants for market, risk, execution, analytics or strategy state; a
migration framework; and everything already deferred below.

---

v2.8.0 — "Currency Roles and Run Outcomes" — makes three fields mean one thing
each, and is a correctness release with no new packages and no breaking changes:

- **A configuration cannot name two account currencies.**
  `ExecutionPipelineConfig.currency` funds the cash ledger and denominates every
  fill; `Account.base_currency` is what the risk resync and `NAVCalculator`
  read. When they disagreed, cash landed under one and risk read the other, so
  buying power and NAV were zero: every order was refused, and the leverage and
  margin checks stopped checking. Refused at the first statement of
  `ExecutionPipeline.initialize`, before the portfolio is funded.
- **A valuation cannot span two currencies.** `PortfolioValuation.snapshot` read
  cash for one currency and summed positions across all of them, so a mixed book
  returned a figure in no currency at all. It now refuses. **This is the absence
  of an FX rate, not a rule that foreign-currency instruments are invalid** — the
  cash ledger is already keyed by currency and every position declares its own.
- **A run says why it produced no fills.** `unpriced_assets` records the assets a
  run declined to trade, keyed by asset so a misconfigured session counts rather
  than grows, and surfaced on `BacktestResult`, `ReplayResult` and
  `SessionState`. An optional read-only `InstrumentRegistry` reference on the
  pipeline config separates "not a registered instrument" from "registered but
  never priced". This closes the ADR-0016 §3 failure mode, which that ADR
  documented and deferred.
- **An allocation contribution cannot outlive its request.** Four paths retired
  the reservation and kept the contribution entry forever: unpriced and
  risk-rejected requests never reach the OMS, and `NO_FILL` / `REJECTED` /
  `EXPIRED` and a withdrawn partial-fill remainder reach a terminal OMS state
  without producing a report, which is the only route that reached the retiring
  code. Retirement now happens wherever a request's lifecycle ends.
- `LIFECYCLE_SNAPSHOT_SCHEMA` is a literal rather than an alias of
  `DEFAULT_SCHEMA_VERSION`. The value does not move; the constant is now
  independently settable, which is what ADR-0018's bump needs.
- A regression guard records that listing exchange, market-data attribution and
  execution venue are three concepts, not one, and that none derives from
  another. The archaeology proposed merging them; reading the code refuted it.

**Deferred out of v2.8, deliberately:** an FX rate source and true
multi-currency valuation; instrument currency reaching `Position` / `CashLedger`
and a `currency` on `Bar`, both of which need the rate source first;
`OMSSnapshot.schema_version` and the OMS history/events envelope, which belong
with the next release that moves a persisted format; `AllocationState`
persistence, which delivers nothing until session round-trip exists; and
everything already deferred below.

---

v2.7.0 — "Instrument Identity and Dataset Provenance" — makes two asserted
things derived, and is a correctness release plus one new package:

- **A provider symbol resolves to a canonical instrument.** `alphalab.instrument`
  is the authority: `InstrumentRegistry` owns `(provider, symbol) -> asset_id`,
  and an `asset_id` is derived deterministically (`uuid5` over a fixed canonical
  key under a frozen namespace) rather than minted, so two independently
  configured environments agree with no shared database.
- **The documented provider path can reach a fill.** Normalization previously
  passed a provider symbol through as an `asset_id`, which `core.Fill` refused
  at the last stage — after market data, strategy, allocation and risk had all
  succeeded. Refusal now happens where the identity is created, naming the
  provider and the symbol. `Fill` / `Trade` UUID validation is unchanged.
- **A run names the data it consumed.** `BacktestResult.dataset_id` and
  `SessionState.source_id`. A hand-driven run records `None` — an absence, not
  an invented identity.
- **BACKTEST evidence derives its dataset** from the run instead of accepting a
  caller's claim. `evidence_id_for` is byte-identical to v2.6, so evidence
  recorded under v2.6 still verifies and still passes its policy: no schema
  bump, no migration, no dual verification.
- Four breaking changes, all declared in `CHANGELOG.md`: `NormalizationPolicy.symbols`
  removed, `ProviderHistorySource.of` policy now required, `evidence_from_backtest`
  loses `dataset_id`, and `DEFAULT_POLICY` is refused as a production source.

**Deferred out of v2.7, deliberately:** sector classification and a
security-master classification source; governance actors and enterprise RBAC
enforcement (ADR-0018 is written and deferred, and needs a lifecycle schema
decision); richer persisted provenance on evidence (source, time coverage,
normalization policy, provider identity), which would either enter the digest or
sit outside it untamper-evident; a dataset registry or uniqueness guarantee; and
per-environment promotion policy.

---

v2.6.0 — "Allocation Authority and Attribution Truth" — makes two production-path
numbers true rather than plausible, and is a correctness release with no new
packages:

- **Outstanding capital is enforced.** The budget guard now counts capital
  already committed to unsettled orders. Six EXTERNAL-routed events previously
  committed 5,400,000 against a 1,000,000 budget with no position held and no
  rejection recorded.
- **A terminal order holds no reservation.** A reservation is denominated at the
  reference price and consumption at the execution price, so any fill priced
  away from the reference stranded a residual on a `FILLED` order, without bound.
- **Strategy attribution is real.** `"ALLOC-NETTED"` is deleted.
  `StrategyContribution` carries who asked for a netted order and for how much,
  and realized P&L splits by signed contribution.
- **Holding periods are measured** from `Position.opened_at`, and **sectors are
  reported as absent** rather than as one placeholder bucket.
- The portfolio snapshot moves to schema 2 and refuses version 1. No migration
  framework: a v1 payload does not record when a position opened.
- `alphalab.integrations`, `alphalab.kernel`, `alphalab.core.events` and
  `CommonEvent` are deprecated for removal in v3.0. Nothing is removed.

See ADR-0015 and `CHANGELOG.md`.


# Not yet addressed

The engine packages exist and are individually tested. The following integration
and consolidation work has **not** been done:

- **Live venue connectivity for order execution** (v2.6+): v2.3 built the
  adapter contract, the routing gates, reconciliation and the fill-return path,
  and tested all of them. What does not exist is a *broker* transport. The
  `alphalab.integrations` clients (Alpaca, IB, Zerodha) return canned responses.
  An async live session loop, order-state polling and reconnect scheduling all
  wait on that transport. Market *data* is further along: `marketdata.binance` is
  a real REST client over a real HTTP transport (since v1.39.0), and v2.5
  connects a provider's history to a `TradingSession`.
- **Streaming market data** (v2.6+): v2.5's provider source reads a finite
  historical range and is re-iterable. Polling, subscription and reconnect need a
  clock and a loop AlphaLab does not have, and a streaming source would also need
  an answer to late arrivals beyond "skip and record".
- **`StrategyContext.history` and `.universe`** (v2.12+): v2.10 populates the
  marked portfolio, the strategy's live order shares, risk headroom and a market
  view (ADR-0026 decision 2). A clock-bounded historical accessor needs its
  bound enforced at construction plus a look-ahead regression suite, and
  universe membership needs a decision about whether it is configuration,
  instrument-registry state or a risk control. Both stay empty and say so rather
  than being populated approximately.
- **Allocation visibility inside `StrategyContext`** (v2.11+): reservations and
  contributions are *post*-intent facts. Showing a strategy the capital its own
  intent will later reserve invites it to pre-size, duplicating the allocation
  engine's authority (ADR-0015). The contribution ledger is read for attribution
  only, and no allocation decision is exposed.
- **Artifact storage** (v2.5+): `ArtifactRef` records where a model version's
  bytes live and what they should hash to. Nothing fetches, writes or verifies
  them, because there is no object store here and faking one would be the only
  untestable part of the lifecycle. v2.13 added a durable store for **run state**
  and deliberately did not generalise it: `RunStateStore` holds a payload string
  a caller already has, while an artifact store would need a producer of artifact
  bytes, and none exists — `reporting.export_json`, `export_csv` and
  `export_markdown` all return `str` and no caller writes them anywhere. The two
  references are also different in kind, which is why `RunStateRef` is not shaped
  like `ArtifactRef`: one identifies bytes AlphaLab holds, the other addresses
  bytes it never sees (ADR-0029).
- **Approval workflow** (v2.5+): a promotion is exactly the kind of auditable
  privileged action `alphalab.enterprise` models with RBAC and an audit log, but
  the two are not connected. `ValidationPolicy` states thresholds; it does not
  state who may apply one. **ADR-0018** writes the intended seam and defers it:
  recording an actor touches two persisted lifecycle records, which forces a
  lifecycle schema decision v2.7 deliberately avoided.
- **Per-environment promotion policy** (v2.5+): a strategy version has one stage
  across all environments, and `PRODUCTION` means "live somewhere". A policy
  that differs between `paper` and `live-eu` is not expressible.
- A single integrated runtime spanning *all* engines. The **run** layer is one
  owner as of v2.14: `RunEngine`/`RunState` (`alphalab.runtime.run`) over
  `ExecutionPipeline`, with `TradingSession`, `BacktestEngine` and
  `ReplayBacktest` reduced to drivers (ADR-0030). `alphalab.lifecycle` remains a
  separate wired path and is deliberately not joined: a deployment names what
  should run, and the execution path runs it. Research, reporting, feature store
  and the rest remain standalone libraries.
- Resolution of `kernel` and `core/events` (both entirely unused: nothing outside
  their own packages and tests imports either). **Deprecated in v2.6, removed in
  v3.0** — `kernel` warns at import, `core.events` deliberately does not, because
  `alphalab.core` re-exports its symbols eagerly and a warning there would fire
  on the canonical core package for every consumer (ADR-0015).
- `alphalab.integrations` is a third broker surface that speaks none of the
  canonical `alphalab.broker` types and is imported by nothing. v2.3 converged
  `broker` and `brokers` and left it untouched. **Deprecated in v2.6, removed in
  v3.0.**
- ~~Strategies still do not see the marked portfolio.~~ **Done in v2.10**
  (ADR-0026): the pipeline overlays the marked portfolio, the strategy's live
  order shares, and read-only risk and market views onto the context the
  caller's factory returns. This bullet contradicted the `StrategyContext` entry
  above it from v2.10 until v2.12, when it was noticed and corrected. Two of the
  nine fields — `history` and `universe` — genuinely remain unpopulated, and are
  tracked there.
- Multi-currency valuation. v2.8 made `PortfolioValuation.snapshot` refuse a book
  it cannot express as one figure in one currency, rather than returning a wrong
  one. v2.12 closed the rest of that gap without supplying a rate:
  `NAVCalculator`, `portfolio_value` and `_risk_exposure`/`sector_exposure` now
  refuse a book they cannot express, and `long_value` / `short_value` are
  reclassified as component sums that name no currency and therefore make no
  currency claim. What is still absent is FX itself — valuing *across*
  currencies needs a rate source that does not exist here.

  v2.12 also corrected a claim this list carried for four releases. A *pipeline
  run* settles in exactly one currency and always did: a second currency in the
  book makes the next portfolio snapshot raise, mid-event. `PortfolioEngine` and
  `CashLedger` are multi-currency and a wholly foreign book values in its own
  currency, so an EUR-settling pipeline trades EUR instruments end to end — but
  that is the engine's capability and the single-currency pipeline's, not a
  pipeline holding two at once. A USD pipeline now refuses a EUR instrument
  instead of booking it as USD.
- **Classification data, and dimensions other than sector** (v2.11+). The
  security master's *mechanism* is delivered: v2.7 gave the identity half, and
  v2.11 gives the classification half — `classify_instrument` writes a sector
  without touching an `asset_id`, the pipeline reads it once per fill onto
  `TradeRecord.sector_id`, and `pnl_by_sector` and `ExposureStatus.sector_exposure`
  are populated for the first time. What is still absent is the *data*: AlphaLab
  ships no taxonomy and no reference-data feed, so a breakdown requires an
  operator who declares one. Industry, sub-industry, country, region, issuer and
  credit rating are each a separate decision with their own consumers, and none
  is expressible today. Sector-based **risk limits** are also not in scope:
  `sector_exposure` is visibility, and no check reads a sector.

- `benchmark_workbench.py` fails on a tab-lifecycle assertion in
  `alphalab.workbench`. Pre-existing at v2.2.0 and unrelated to the execution
  path.

Delivered since this list was written: mark-to-market position repricing (v2.1),
`alphalab.replay` integration with the execution path (v2.2), market-data /
broker convergence with paper execution on the canonical path (v2.3), the
model/strategy lifecycle composing PR-046 through PR-049 (v2.4), typed state
round-trip plus the provider→source link (v2.5), allocation authority with
attribution truth (v2.6), instrument identity with dataset provenance (v2.7),
currency roles and run outcomes (v2.8), durable run state with deterministic
identifier continuation (v2.9), the strategy boundary — durable strategy state
and a populated `StrategyContext` (v2.10), instrument classification with sector
provenance (v2.11), the currency authority with the settlement boundary
(v2.12), and the run-state store with the durability boundary (v2.13).

---

# Long-Term Vision

AlphaLab aims to become a complete quantitative research platform covering market
data, feature engineering, research, portfolio construction, machine learning,
production trading, cloud infrastructure, and enterprise deployment — while
preserving its core engineering principles of determinism, immutability, modular
architecture, event-driven design, and production readiness.

Reaching that vision requires the integration work listed under **Not yet
addressed**, not just additional engine packages.

---

# Versioning

Major releases introduce architectural milestones or breaking public API changes
(v2.0.0 unified the canonical execution domain models).

Minor releases may still make small, documented breaking changes to a narrow
public API where correctness requires it: v2.2.0 changed
`AllocationEngine.release_reservation` to take no amount, because the reservation
ledger owns it, v2.3.0 changed `alphalab.brokers`' account, order and execution
field names so both broker packages speak one vocabulary, v2.4.0 refused two
model-registry stage transitions that made rollback indistinguishable from
promoting something old, and v2.5.0 gave a partially filled simulated order a
terminal state instead of leaving it working forever.

Minor releases introduce new capabilities (v1.34.0–v1.46.0 each added one engine).

Patch releases focus on stability, bug fixes, and performance improvements.

---

# Community

The roadmap will continue evolving as AlphaLab grows.

Community feedback and contributions will play an important role in shaping future development.