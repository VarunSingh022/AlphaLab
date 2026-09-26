# AlphaLab Roadmap

This document states what AlphaLab has delivered, what it deliberately does not
do, what it depends on from outside, and what remains genuinely open.

As of **v3.0.0** the architecture is frozen. That changes what a roadmap is for:
it is no longer a queue of structural work, because the v3.0 audit established
that there is no known internal problem requiring AlphaLab to be refactored. What
remains is classified below, and each class means something different.

**v3.1.0** is the first release after the freeze, and shows what "frozen" is
meant to permit: a capability release confined to one package, adding the data
layer `alphalab.data` was named for without moving a boundary or changing an
owner.

**v3.2.0** is the second, and deepens two packages rather than adding one:
`alphalab.factor_library` becomes a feature and factor research engine, and
`alphalab.research` gains the validation methodology — walk-forward splits,
purged and embargoed cross-validation, robustness perturbations and overfitting
diagnostics. No boundary moves, no owner changes, and every v3.1 invariant
holds. ADR-0037.

**v3.3.0** is the third, and answers the questions an institution asks before
allocating to a strategy: what it costs to trade, how much it can carry, where
the P&L came from, where the risk comes from, and what a crisis would do to it.
It deepens `alphalab.execution` and `alphalab.analytics`, adds
`alphalab.scenario`, and extends `alphalab.common.statistics`. ADR-0038.

**v3.4.0** is the fourth, and makes AlphaLab say what an instrument's numbers
*mean* outside the market whose conventions had been written into the defaults.
It adds the leaf package `alphalab.conventions`, deepens `alphalab.futures`,
`alphalab.options`, `alphalab.crypto`, `alphalab.macro` and
`alphalab.portfolio`, and makes six silently-defaulted market conventions
required. ADR-0039.

**v3.5.0** is the fifth, and is the bridge between research and real trading. It
deepens exactly one package, `alphalab.lifecycle`, carrying it past the
deployment record into the thing a deployment becomes: the progression from
research to live money, a specification of what a strategy needs to run as it
was researched, structured runtime health from supplied observations, an
expected/paper/live comparison, and deterministic reconciliation against a
normalized broker state. No package is added, no boundary moves and no snapshot
schema changes. ADR-0040.

**v3.6.0** is the sixth, and makes a strategy version evaluable by somebody who
did not write it. It deepens `alphalab.lifecycle` again: an immutable strategy
fingerprint over code, dependencies, parameters, research configuration and
engine; a reproducibility manifest from which a result can be recreated;
eight machine-verifiable certification properties with no overall score; and a
portability check against declared environment capabilities. These are the
evidence contracts a research marketplace such as RedDesk consumes — AlphaLab
provides them and contains no marketplace logic. No package is added, no
boundary moves and no snapshot schema changes. ADR-0041.

**v3.7.0** is the seventh, and lets research use information other than prices
without looking ahead, and a strategy learn without becoming irreproducible. It
extends the point-in-time core in `alphalab.common`, makes `alphalab.alt_data`
the point-in-time foundation for events, alternative data and fundamentals — a
leaf over `common` — and adds knowledge frames to `alphalab.factor_library`,
event studies and regime detection to `alphalab.research`, the adaptive engine to
`alphalab.strategy`, adaptive integration to `alphalab.lifecycle` and ingestion
to `alphalab.api`. No package is added, no boundary moves and no snapshot schema
changes. ADR-0042.

| Class | Meaning |
| --- | --- |
| **Delivered** | Built, tested, and described by the documentation |
| **Deliberate boundary** | Not built, on purpose, with a reason and usually a regression test |
| **External dependency** | Not AlphaLab's engineering to do — data, credentials, a vendor's API |
| **Known defect** | A real defect, found and stated where it matters, not yet fixed |
| **Optional future evolution** | Could be built; no commitment; nothing depends on it |

Nothing classed as a deliberate boundary, an external dependency or optional
future evolution is a defect, and none of them blocks a release. Known defects
are listed apart, in their own class, so they are never mistaken for
decisions.

---

# Delivered

## The engine series (v1.34.0 – v2.0.0)

Each shipped as its own release, and each package is a standalone, individually
tested engine. The scope notes are kept below as the historical record.

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

## The v2 line

Full detail is in `CHANGELOG.md`; the reasoning is in `docs/ADR/`. Each entry
names what the release established and the ADR that records the decision.

| Release | Established | ADR |
| --- | --- | --- |
| **v2.0.0** | Canonical execution domain models: one `Side`, one `OrderRequest`, one lifecycle `Order`, float timestamps. Four new standalone packages | ADR-0008, ADR-0009 |
| **v2.1.0** | Mark-to-market; the **exact** accounting identity and one rounding policy; O(1) amortized engine histories | — |
| **v2.2.0** | Backtest and replay on **one** step, so parity is structural; persistent containers; per-order reservation ledger; seeded identifiers | ADR-0010 |
| **v2.3.0** | One canonical market-data model over one wire record with an explicit normalization boundary; one broker boundary that `brokers` routes; four environments, one path | ADR-0011, ADR-0012 |
| **v2.4.0** | The model and strategy lifecycle: numbered `StrategyVersion`, typed refs, evidence with content-derived ids, a gated promotion, the deployment ledger as the one source of truth | ADR-0013 |
| **v2.5.0** | Typed `capture` / `restore` over typed decoders; `market.provider` connecting a provider's history to a session; explicit unordered-source semantics; partial fills terminate | ADR-0014 |
| **v2.6.0** | Allocation authority: outstanding capital enforced, terminal orders release what they hold, real strategy attribution replacing `"ALLOC-NETTED"` | ADR-0015 |
| **v2.7.0** | Instrument identity derived (`uuid5` over a canonical key) rather than asserted; dataset provenance derived from the run rather than claimed | ADR-0016, ADR-0017 |
| **v2.8.0** | One meaning per currency field; a valuation that refuses to span two currencies; a run that records why it produced no fills | ADR-0019 – ADR-0021 |
| **v2.9.0** | Deterministic identifier continuation; the execution path round-trips; a duplicate venue fill applies once; a working external order can be ended | ADR-0022 – ADR-0024 |
| **v2.10.0** | `StrategyStateProtocol` and a two-sided codec; `StrategyContext` populated from the **marked** portfolio, with contribution-based order shares | ADR-0025, ADR-0026 |
| **v2.11.0** | The security master's mechanism: `classify_instrument` writes a sector without touching identity, and the pipeline freezes it onto each fill | ADR-0027 |
| **v2.12.0** | The instrument registry becomes the **currency authority**. Two seams: `_process_requests` drops a foreign instrument, `_apply_report_to_portfolio` refuses a mismatched report. `STRICT_MATCH` and no policy object | ADR-0028 |
| **v2.13.0** | `RunStateStore` as the one owner of durable run state — payload-agnostic, one real file backend, one named double, no fallback. Cross-process continuation proven byte-identical | ADR-0029 |
| **v2.14.0** | Runtime unification: `RunEngine` over `RunState` owns the run; `ExecutionPipeline` keeps the step; the drivers hold no state | ADR-0030 |
| **v2.15.0** | Five capabilities that had a contract and nothing behind it: venue transport, WebSocket streaming, artifact bytes, classification provenance, the completed strategy context | ADR-0031 |
| **v2.16.0** | Three joins: the live driver, governance/RBAC/audit, and FX valuation — plus the refactor audit that classified twenty-one structural findings | ADR-0032, ADR-0033 |
| **v2.17.0** | Settlement-level multi-currency, the FX rate feed, the strategy-class registry; seven deprecated surfaces removed with no aliases; zero skips and zero warnings | ADR-0034, ADR-0035 |

## v3.0.0 — the stable release

v3.0.0 adds no capability and moves no boundary. It is the point at which:

- the architecture is **frozen** — the invariants, ownership boundaries and
  schema contracts in `nowandfuture.md` are the ones AlphaLab intends to keep;
- the repository **describes itself truthfully** — every current-facing document
  matches the code, and historical records are labelled as historical;
- the remaining open items are **classified** rather than queued, which is what
  the rest of this document is.

The v2.17 release exists so that this one could be additive: everything that
would have been a breaking removal in v3.0 was taken a release early.

## v3.1.0 — universal data ingestion

The first capability release on the frozen architecture, confined to
`alphalab.data`. ADR-0036.

- **Source provenance** — `RawSource` records the channel, the location, the
  retrieval time and the SHA-256 of the exact bytes.
- **CSV as a first-class input** — four delimiters, quoted fields, headerless
  files, vendor header spellings, and every discrepancy preserved rather than
  padded away.
- **Schema detection that refuses to guess** — bindings with stated reasons,
  ambiguities reported rather than resolved, every assumption returned.
- **Explicit timezones** — a naive timestamp is refused until a zone is named;
  a bare date needs a stated time-of-day convention.
- **Structured validation** — thirteen `FindingKind`s, each carrying severity,
  source line and column.
- **Cleaning under a policy with no defaults**, every change recorded as a
  `TransformationRecord`. There is no way to fill a missing price.
- **Market calendars** — sessions, lunch breaks, overnight sessions, half days,
  holidays and 24/7, for any venue. No holiday data ships.
- **Multi-asset semantics** — one spec per asset class, each keeping the fields
  its class needs.
- **Corporate actions** — `PriceBasis` distinguishes raw from adjusted, and
  every adjustment is traceable to the action that caused it.
- **A derived, immutable dataset version**, carried into `MarketDataset`,
  `RunState.source_id`, `BacktestResult.dataset_id` and `ValidationEvidence` —
  with the evidence digest unchanged.
- **`alphalab.api`** — the application-facing Python API, so a host
  platform imports one module rather than reaching into internals.

## v3.7.0 — advanced quant research

The seventh capability release on the frozen architecture. No package added, no
boundary moved, no schema touched. ADR-0042.

- **When information became knowable, stated** — `PointInTimeStamp` with
  observed, available, effective and ingested instants and an availability basis
  (`DECLARED`, `DERIVED` by a named rule, `UNKNOWN`); `VisibilityRule`
  `PUBLICATION` and `INGESTION`; `PointInTimeIndex`. A record of unknown
  availability is ingested, counted and never read.
- **Event-driven research** — one canonical `InformationEvent` with an open
  dotted vocabulary; session placement (in session, before the open, after the
  close, between sessions, non-trading day) through a structural calendar
  protocol; `event_study` anchored at the first observation at or after the
  instant an event could be traded, corrections and unknown deliveries excluded
  by name, clustering reported, no p-value.
- **Alternative data with provenance** — `ExternalObservation` of any category,
  `ObservationSource` (identity and version in every record, the bytes' digest
  in the set), versioned `ObservationSet`s with checked vintages and lineage,
  `AS_KNOWN` / `ORIGINAL` vintage reads and no hindsight policy; ingestion with
  an explicit availability rule; wire records lifted only with their timestamp's
  meaning declared.
- **Fundamental research** — `FundamentalObservation` keeping fiscal period,
  publication, availability and restatement apart; statements, trailing twelve
  months, valuation, ratios, growth and restatement bias, each at an instant, with
  undefined figures explained and units checked; `fundamental_snapshot_as_of`
  and `fundamental_frame` for the factor engine.
- **Knowledge frames** — the latest knowable figure per subject on a research
  clock, not a forward fill, with a checked join to the prices it was sampled on.
- **Regime detection** — declared threshold, trailing-quantile and composite
  rules with the caller's labels, persistence in the identity, a reconstructable
  state, transitions, profiles and conditioned diagnostics.
- **Adaptive strategies** — immutable learned state with hash-chained lineage,
  one pure update function, explicit cadence, ordering, timing, warmup and
  freezing, checkpoints that refuse an edit, reprocessing of late data; three
  rules; `AdaptiveStrategy` on the execution path ending in its research
  replay's state; the state in the run snapshot, the digest and the
  fingerprint; `assess_adaptive_replay`.
- **Found and fixed before release** — a single-figure vintage read that
  scanned its series' history on every call (found by the new benchmark).

## v3.6.0 — strategy evaluation and research-marketplace infrastructure

The sixth capability release on the frozen architecture. One package deepened,
none added, nothing changed. ADR-0041.

- **A strategy fingerprint** — `"<name>@<sha256>"` over five defining inputs:
  the code (the entry point read from the class registry, and a digest of the
  source files by relative path), the dependencies (exact pins with a declared
  `EXACT_CLOSURE`, `DIRECT_ONLY` or `UNDECLARED` completeness), the parameters
  (read from the registered version), the research configuration and the
  engine version. Each changes the identity on its own; mapping order, file
  order and name spelling do not; nothing environmental enters it, so it is the
  identity a strategy carries from research to live.
- **A reproducibility manifest** — dataset version and the digest of its bytes,
  the fingerprint, the run's own recorded configuration, the seed with what it
  does, the engine, and the result's identity: the digest of the run's complete
  canonical record. Every field is read from its owner. An unseeded run is
  refused; a study's absent seed is recorded, never invented; a dataset whose
  provenance records no bytes is refused.
- **Four reproducibility answers, not one boolean** — identity, metadata
  completeness, a rerun (`REPRODUCED`, `DIVERGED`, `INPUTS_DIFFER`,
  `NOT_ATTEMPTED`) and the external inputs a rerun needs, which are never empty.
- **Certification primitives** — eight properties (deterministic, reproducible,
  risk limits, maximum leverage, supported markets, required data, resource
  usage, runtime behaviour), each `PASS`, `FAIL`, `NOT_ASSESSED` or
  `INSUFFICIENT_EVIDENCE` with its methodology, evidence and limitations. No
  overall score. Evidence is observed, never asserted: every verdict is derived
  inside, and leverage and drawdown are read as the pre-trade gate reads them.
- **Portability** — one fingerprint against `TargetEnvironment` declarations
  built from the capability types that already exist, across eight requirements
  that are each satisfied, blocked, unverified or not applicable. A missing
  capability is a named blocker, never a substitution; "broker A" and "broker B"
  are two declarations, never two adapters.
- **Found, stated, not changed here** — three properties of the pre-trade gate
  and one of in-memory ingestion that predate this release; see *Known defects*
  below.

## v3.5.0 — strategy execution and production intelligence

The fifth capability release on the frozen architecture. One package deepened,
none added, nothing changed. ADR-0040.

- **A progression, and it is a third axis rather than a third status flag** —
  `StrategyLifecycleStage` names the eight stages from research to archived with
  a declared transition table, an append-only history and an actor on every
  move. It replaces neither `ModelStage` (which calls research, backtest and
  validation all `NONE`, calls paper and live both `PRODUCTION`, and has no
  member for paused) nor `strategy.state.LifecycleState` (which is about an
  instance in a session). `PROGRESSION_MODEL_STAGES` relates it to the first,
  totally and in the open, and `progression_conflicts` **reports** a
  disagreement rather than resolving it.
- **A pause that cannot be used to skip a stage** — only a running stage can be
  paused, and `resume_progression` returns to the stage the pause interrupted,
  derived from the history. A paper strategy that pauses cannot resume into
  `LIVE`.
- **No environment on the progression** — the "no per-environment promotion
  policy" boundary below is unchanged. `PAPER` and `LIVE` are maturity, not
  addresses, and the deployment ledger remains the one answer to what is live
  where.
- **A deployment specification that can reproduce its own assumptions** —
  strategy version, parameters read from the registered version, dataset
  assumptions by *derived* identity, the `RiskLimits` the pre-trade gate
  actually enforces, a capital policy, and typed broker, market and runtime
  requirements. It identifies itself by the same SHA-256 content digest
  `evidence_id_for` uses, so an edited specification stops verifying.
- **Broker requirements are capabilities** — `BrokerRequirements` says the
  strategy needs stop orders, IOC, short selling and equities. It does not say
  which broker, and `BrokerCapabilities` is a declaration an application fills
  in. No vendor is named anywhere and nothing reaches one.
- **Runtime health that cannot read a missing observation as a healthy one** —
  seven categories evaluated from **supplied** observations against the budgets
  a specification declares. Every category is judged or reported as unevaluated,
  and `HealthStatus.UNKNOWN` exists so a clean-but-incomplete report is never
  `HEALTHY`. Findings are categorised, severity-bearing and carry the observed
  value and the threshold as machine-readable detail.
- **`live_health` is unchanged** — it still reports the live driver's own
  aggregate in plain sentences, and `observation_from_live_run` is the bridge,
  so the two surfaces cannot disagree about a run they can both see.
- **Expected against paper against live** — trades, fills, slippage, P&L,
  exposure and execution latency, with alignment declared rather than guessed
  (`ORDER_ID` for runs sharing a seeded identifier stream, `ASSET_AND_TIME`
  otherwise) and every tolerance stated. A metric with no tolerance is reported
  not-comparable, never matching.
- **A venue's unmeasured slippage stays missing** — `execution_report_from_broker`
  has called it "absent, not zero" since v2.3, and this is where that became a
  value rather than a comment. Money is compared per currency and never summed
  across two.
- **Reconciliation of the pair nothing compared** — `reconcile_execution_state`
  compares AlphaLab's own execution state against a normalized `BrokerState`
  across fourteen mismatch classes. `broker.reconcile` is unchanged and still
  owns the other pair. Neither side is declared authoritative, nothing is
  mutated, and repeating it returns an equal report.
- **Agreement and completeness are different facts** —
  `StateReconciliation.reconciled` says the two sides agree about what was
  compared; `fully_reconciled` also requires that nothing was skipped. A
  currency the broker account cannot speak about is an `UnreconciledArea`.
- **One tolerance authority** — `Tolerance` is shared by health, comparison and
  reconciliation. One that bounds nothing is refused at construction.
- **No durable state added** — a progression, a specification, a health report
  and a reconciliation are values a caller holds. `LifecycleState` is unchanged
  and `LIFECYCLE_SNAPSHOT_SCHEMA` is still 2, so every payload written before
  this release still reads.

## v3.4.0 — global markets and multi-asset research

The fourth capability release on the frozen architecture. One leaf package added,
five deepened, six defaults made required. ADR-0039.

- **One convention authority, usable from everywhere** — `alphalab.conventions`
  holds `MarketConvention` (venue, calendar id, quote *and* settlement currency,
  multiplier, tick schedule, lot specification, settlement rule), with **every
  field required**. It imports `alphalab.common` and nothing else in `alphalab`,
  which is what lets `options`, `futures`, `crypto`, `portfolio`, `data` and
  `api` all use it without closing a package cycle.
- **Settlement dates** — `SettlementBasis` distinguishes trade-date, trading-day
  and calendar-day counting, because T+2 trading days across a long weekend is
  four calendar days. Trading days are counted over a supplied calendar reached
  through a one-method structural protocol, so the calendar authority does not
  move.
- **Tick and lot grids** — tiered tick schedules (the normal shape outside the
  US), a tick *size* and a tick *value* as separate types, and a lot
  specification that **refuses** a partial lot rather than rounding it.
- **The multiplier, multiplied once** — `contract_notional` is the only site in
  AlphaLab that multiplies a contract count by a multiplier, and a regression
  test reads every module's source to keep a second from appearing.
  `alphalab.portfolio.contracts` pairs a `Position` with its convention; the
  1,000x gap between that and the unmultiplied `ExposureEngine` figure is
  demonstrated in a test.
- **Reproducible continuous futures** — a series is reproducible from four
  stated things: `ContractChain`, `RollPolicy`, the observations, and the
  `AdjustmentMethod`. `roll_schedule` says when each handover happened and why;
  a rule refuses the input it needs and was not given rather than approximating
  it; a missing print at a roll raises rather than being interpolated.
- **An implied volatility that refuses** — `implied_volatility` inverts the same
  expression the pricer rounds, and raises in five cases where a quoted price
  has no answer. `surface_from_chain` returns the surface **and every refusal
  with its reason**, and the two account for every contract in the chain.
- **Greeks that carry their model** — `ModelAssumptions` names the four things
  Black-Scholes does not do, as a value a figure travels with.
- **Expiry as an event, not a number** — `resolve_expiration` reports exercised,
  assigned, abandoned or worthless, and moves cash and underlying units as two
  separate signed quantities.
- **Cross rates, forwards and carry** — a cross is derived only when asked and
  only through a **named** third currency; a forward is covered parity with both
  deposit rates and the day-count basis required. `FxRates.convert` triangulates
  nothing, as it never has.
- **Both directions of time on an FX rate** — a rate dated after the conversion
  instant is now `FutureDatedRateError`. This was listed under *optional future
  evolution* through v3.3; applying it found a genuine look-ahead in the
  repository's own settlement fixture.
- **Currency attribution** — a reporting-currency return split into what the
  assets did and what the currency did, as an identity with no residual. It
  imports nothing from `alphalab.analytics`: the two currency breakdowns are
  different measurements and neither derives the other.
- **Venue differences as metadata** — `VenueSpecification` declares a crypto
  venue's funding interval, fees, price source, minimum notional and settlement
  asset, with nothing defaulted and no adapter, client or credential anywhere.
- **A 24/7 clock that does not invent observations** — `coverage` measures
  against a theoretical clock computed from the window and the declared cadence,
  and reports gaps as counts of absences. No fill, no carry, no interpolation.
- **A fixed-income foundation** — bond cash flows, accrued interest, clean and
  dirty price, the yield inversion, duration in years and convexity in years
  squared. Deliberately **not** an engine: see the boundary below.
- **The wire/domain contract join, where v3.1 said it would be** — a
  `FutureSpec` lifts into a `FutureContract` through `alphalab.api`, above both,
  because `alphalab.data` importing either engine would close a package cycle.
  A spec with no contract month is refused rather than having one derived.
- **Six US and Binance defaults made required** — a futures contract's currency,
  an option's multiplier and exercise style, a funding interval and a crypto
  contract size. Each produced a number rather than an error when wrong, and
  each was invisible to the v2.17 sweep because that sweep reads function
  parameters and these are dataclass fields. The sweep now reads both.

## v3.3.0 — institutional backtesting and portfolio intelligence

The third capability release on the frozen architecture, confined to
`alphalab.execution`, `alphalab.analytics`, the new `alphalab.scenario`, and one
function added to `alphalab.common.statistics`. ADR-0038.

- **An itemized execution-cost contract** — `ExecutionCostModel` names six roles
  (spread, slippage, impact, commission, fee, tax) where a fill carried two
  numbers, and every role is required. `CostSettlement` keeps costs that move
  the fill price apart from costs debited to cash, because collapsing the two
  double-counts. The ordering is stated in the module and asserted in tests.
- **One costing path** — a simulator configured the pre-v3.3 way is a cost model
  whose other four roles are the named absences, and produces byte-identical
  reports. `FREE` is how a caller asks for a frictionless run, visibly.
- **A quoted spread that is a measurement** — `QuotedHalfSpread` reads the
  event's bid and ask and refuses when the feed quoted neither, rather than
  assuming one. The pipeline now forwards the event's quote and shown size, so
  the liquidity-aware roles are reachable from the canonical execution path.
- **Capacity as a liquidity question** — `CapacityModel` connects capital,
  position size, ADV, turnover, participation and impact, reports the capital at
  which a *named* constraint binds, and names the asset that bound it. It reads
  the same impact model a fill is priced with. No default participation limit,
  turnover or impact budget: each moves the answer by orders of magnitude.
- **Attribution across nine dimensions** — strategy, asset, sector, country,
  currency, venue, broker, factor and execution, extending the existing
  authority and reusing `split_realized_pnl`. Each carries an `Availability`,
  and a dimension nothing was supplied for comes back **empty and labelled**
  rather than as one `UNKNOWN` bucket. Currency deliberately does not total.
  Factor attribution carries the unexplained residual, which is what makes it
  reconcile.
- **Risk decomposition with a named method** — `VaRPolicy` carries method and
  confidence together (historical, Gaussian, Cornish-Fisher), so a figure cannot
  travel without its assumptions. `risk_contributions` is the Euler
  decomposition and sums to portfolio volatility exactly. Concentration,
  leverage, beta, correlation, factor exposure, liquidity risk, drawdown and
  tail ratio alongside it. Degenerate samples refuse rather than returning zero.
- **One scenario contract** — a `Scenario` is a named, ordered list of shocks
  applied to a `ScenarioState`, a flat projection any portfolio class can
  produce, so the same object stresses a backtest book, a live book and an
  optimizer target. Applying returns a new state and never mutates. Identity is
  derived from content. Unsupported fields are refused, not skipped.
- **Historical scenarios as contracts, not numbers** — `CRISIS_2008`,
  `COVID_CRASH_2020`, `RATES_REPRICING_2022` and `COMMODITY_SHOCK_2022` each
  name their window and the observations they need, and refuse until a caller
  supplies them from a real dataset. AlphaLab ships no market data and invents
  no historical move.
- **`sample_covariance`** — the same `n - 1` estimator as `sample_variance`,
  built from the same expression, so `sample_covariance(x, x)` is exactly
  `sample_variance(x)`.
- **A determinism fix** — `DeterministicLatency` drew from `hash()`, which PEP
  456 salts per process, so it reproduced within a run and not across runs. It
  now uses a stable digest, asserted from separate interpreters.

---

## v3.2.0 — strategy research and validation

The second capability release on the frozen architecture, confined to
`alphalab.factor_library`, `alphalab.research` and one new module in
`alphalab.common`. ADR-0037.

- **A typed feature framework** — `FeatureDefinition` states the field, the
  window in *periods*, the parameters and the missing-data policy, and defaults
  none of them. Nineteen `FeatureKind`s share one contract rather than being a
  zoo of free functions: returns, rolling statistics, volatility, momentum,
  mean reversion, moving averages, z-scores, volume ratios, volatility regime,
  time-of-day, and three cross-sectional forms.
- **A derived feature version**, hashed from the definition exactly as a
  dataset version is hashed from its content and configuration. Changing a
  window changes the identity; describing the same feature twice does not.
- **Feature lineage** — a `FeatureSeries` carries the dataset version, the
  feature version and the symbol, and derives a `lineage_id` from the three.
  `require_lineage()` refuses a series computed from a dataset with no
  provenance, the rule `Dataset.require_provenance` applies one layer down.
- **Factor research** — cross-sectional ranking with a stated tie method, three
  neutralizations that are *not* interchangeable (mean, group, beta), the
  information coefficient with its sample counts, decay across horizons,
  turnover under a named convention, and exposure by any grouping the caller
  supplies.
- **Signal diagnostics** — forward-return analysis, quantile profiles,
  monotonicity, and conditioning on regimes the caller labels. No blended
  "signal score".
- **Walk-forward validation** — train, validate, test, roll, with rolling or
  expanding windows and every fold carrying the instants in each of its parts.
- **Time-series cross-validation** — rolling, expanding, purged blocked k-fold
  and embargoed. Purging is defined by label windows read off the actual
  series, not by subtracting dates.
- **Robustness testing** — parameter, data and signal perturbation, missing
  data by deletion, execution delay, execution cost, block bootstrap and Monte
  Carlo. Every stochastic step takes an explicit seed and records it.
- **Overfitting diagnostics** — parameter sweeps that count every configuration
  evaluated, sensitivity, neighbour drop, out-of-sample degradation, period and
  symbol stability, and a Bonferroni threshold whose assumption is stated.
  Measurements, thresholds and findings are separate fields.
- **A reproducible experiment contract** — `ResearchStudy` derives an identity
  from its description and `StudyResult` derives one from the numbers it
  produced; `run_study` refuses a dataset the study was not written for.
- **`ValidationMethod.STUDY`** — a study result becomes evidence through
  `evidence_from_study`, with `evidence_id_for` unchanged, so every promotion
  recorded since v2.6 still verifies.
- **`alphalab.common.statistics`** — the one deterministic statistics
  authority. Five private copies of the unbiased sample variance were
  consolidated onto it, with every published number unchanged.

---

# Deliberate boundaries

Not built, on purpose. Each has a reason, and most have a regression test that a
future "simplification" would have to break first —
`tests/regression/test_shared_names_stay_distinct.py` and
`test_venue_concepts_stay_distinct.py` hold the reasons.

- **No single runtime spanning all engines.** The run layer has one owner
  (`RunEngine` over `ExecutionPipeline`, four drivers), and `alphalab.lifecycle`
  is joined to it by a query with a refusal. Reporting, the feature store, the
  factor library and the rest stay standalone: ADR-0009 is a description of the
  codebase that became a rule, and chaining them would create a runtime nobody
  asked for.
- **No allocation visibility inside `StrategyContext`.** Reservations and
  contributions are *post*-intent facts. Showing a strategy the capital its own
  intent will later reserve invites it to pre-size, duplicating the allocation
  engine's authority (ADR-0015). The contribution ledger is read for attribution
  only.
- **No depth hook on `StrategyProtocol`.** `BookUpdated` and `SnapshotCreated`
  reach no hook. Delivering an `OrderBookSnapshot` to `on_quote` would hand
  existing strategies a payload with no `quote`, and the canonical record path
  cannot produce either event in any case. A stated boundary, pinned by the
  routing test (ADR-0032).
- **No per-environment promotion policy.** A strategy version has one stage
  across all environments and `PRODUCTION` means "live somewhere". A policy that
  differs between `paper` and `live-eu` is not expressible, and making it so
  would put a second source of truth beside the deployment ledger. v3.5's
  `StrategyLifecycleStage` does not change this: it is finer-grained on the
  *maturity* axis and still one stage per strategy version, naming no
  environment at all.
- **No remediation in health or reconciliation.** Both detect and report.
  Nothing cancels an order, reconnects an adapter, pauses a progression or edits
  either side of a reconciliation, and neither side is declared authoritative —
  re-sending an order that actually exists would duplicate it, so that decision
  stays with the caller.
- **No default tolerance anywhere.** A metric with none stated is reported
  not-comparable rather than compared for equality. A hidden `== 0` calls a
  one-cent difference a break and a hidden `0.01` says a cent is fine for a
  position count; both are policies nobody chose.
- **No exposure computed by the comparison layer.** What a book means by
  exposure depends on whether it holds shares or contracts, so it is supplied by
  the caller from whichever authority their book calls for. A third exposure
  site would be the one that forgot the multiplier.
- **No durable state for the v3.5 values.** Each snapshot subsystem supports
  exactly one schema version and AlphaLab has no migration framework, so a new
  field on `LifecycleState` would make every earlier payload unreadable to serve
  a value the caller can simply hold.
- **No `SettlementPolicy` object.** `STRICT_MATCH` is the only settlement rule
  because no alternative exists: a permissive mode could only book honestly —
  making the book mixed, which the next valuation refuses without rates — or
  convert, which is FX and is a deliberate act with a recorded rate (ADR-0028,
  ADR-0035).
- **No triangulation, no implicit inversion, no default rate.** A configured rate
  is an invented one. `with_inverses()` will mint the opposite direction, and
  marks what it mints as `derived` (ADR-0020, ADR-0035).
- **No migration framework.** Each snapshot subsystem supports exactly one schema
  version and refuses any other, naming the build that wrote it. The field exists
  so the first schema change is a decision rather than a silent misread.
- **No authentication, credential handling, IAM or federation.** Permanently out
  of scope per ADR-0018. `alphalab.enterprise` models principals and roles; it
  accepts and stores no credentials.
- **No supervised live *process*.** Restart policy, alerting and scheduling are
  an operator's concern. `live_health` answers "should a human look at this?"
  for a run this process is driving, and v3.5's `evaluate_health` answers the
  same question in structured form from observations somebody supplied; acting
  on either answer is still the caller's.
- **No CLI, no server, no daemon, no event bus.** AlphaLab is a library with no
  composition root and no declared entry point, and a test asserts it.
- **No way to fill a missing price.** `MissingValuePolicy` has `REFUSE` and
  `DROP_ROW` and deliberately no `FILL`. Forward-fill, interpolation and
  last-known-value each invent a print that never happened, and the invention is
  invisible by the time it reaches an equity curve. The absence is structural
  rather than a member that raises, so the option is not discoverable and then
  refused (ADR-0036 decision 3).
- **No repair of an impossible bar.** A bar with `high < low` is a row whose
  meaning is unknown, not one with a small error in it; clamping it produces a
  plausible bar the source never reported.
- **No holiday data, and no corporate-action feed.** `MarketCalendar` and
  `apply_adjustments` are the mechanism and the arithmetic; the data is an
  application's to supply. An exchange's holiday list changes annually and
  differs between segments of one venue, so a list baked in here would be wrong
  within a year while looking authoritative — the same position v2.11 took on
  taxonomies and v2.17 on FX rates.
- **No curve bootstrapper.** `YieldCurve` holds *observed* yields and v3.4
  deliberately did not make it a discount curve. Bootstrapping requires choosing
  an interpolation scheme over an incomplete set of quotes, and the choice
  changes every forward rate read off the result — which is the researcher's
  decision, not a library's. `discount_factor_at` turns an observed yield into a
  factor under a **named** compounding convention and stops there (ADR-0039
  decision 11).
- **No fixed-income engine.** `alphalab.macro.bond` covers what a fixed-rate
  bond with known coupon dates admits exactly. Credit spreads and default,
  embedded calls and puts, floating and inflation-linked coupons, and the
  30E/360 and ACT/ACT ISDA day-count variants are each absent because each needs
  a model or an end-of-month rule whose correct form depends on the instrument's
  own terms. The module, the example and this line all say *foundation*.
- **No volatility-surface fit, and no interpolation across expiries.** A
  `VolatilitySurface` interpolates along strikes at a matching expiry and refuses
  an expiry nobody quoted. Variance accumulates with time, so the quantity that
  interpolates sensibly between two maturities is total variance rather than
  volatility, and an SVI or SABR fit is a model with parameters somebody has to
  choose. `term_structure` reports the expiries that actually quote a strike.
- **No American option pricing.** `black_scholes_price` is a European closed
  form and `ModelAssumptions.prices_early_exercise` is `False`, carried on every
  implied volatility so a figure cannot travel without it. `ExerciseStyle` is
  required on a contract and is read by `resolve_expiration`, not by the pricer.
- **No inferred roll rule.** Volume, open interest and days-to-expiry each give
  a defensible answer and they disagree. A `RollPolicy` has no default, and a
  trigger refuses the input it needs rather than approximating it from another —
  approximating is a different rule reported under this one's name.
- **No exchange registry, tick table, lot schedule or venue list.** The same
  position `MarketCalendar` takes on holidays, for the same reason: an exchange
  revises them, they differ between segments of one venue, and a table baked in
  here would be wrong within a year while looking authoritative.
- **No trade or depth ingestion from a flat file.** A `price`/`size` pair is
  indistinguishable from a partially populated bar without a declaration, and a
  depth book is not a flat table. `RecordType` has `BAR` and `QUOTE` only; a
  caller that builds the records itself can still ingest them.
- **No marketplace logic.** AlphaLab provides fingerprints, manifests,
  certification reports and portability reports; listing, publishing, purchase,
  payment, subscription, ranking, search, seller and buyer accounts, licensing
  and tenancy belong to the application that consumes them. A regression test
  sweeps the v3.6 modules for any of it, and none of them names a consumer.
- **No overall certification score.** A blended figure would decide how many
  failed properties one passing one outweighs — a policy presented as a
  measurement. The report states eight properties and stops.
- **No verdict accepted as evidence.** Certification takes runs, manifests,
  observations and measurements and derives every judgement itself; it has no
  field for a health report, an assessment or a status.
- **No dependency resolver and no environment snapshot.** A dependency closure
  is declared, with its completeness stated. Listing "whatever is installed
  here" and calling it exact is the false reproducibility claim the manifest
  exists to prevent.
- **No adaptation for portability.** A strategy that needs a capability an
  environment lacks is not portable there; nothing converts an order type,
  drops a short or retunes a parameter to make it fit.
- **No durable state for the v3.6 values**, for the reason v3.5 gave: a field on
  `LifecycleState` would move its schema, and a fingerprint, manifest or report
  is a value the caller can hold. A registered version stores no fingerprint.
- **No default availability.** A record whose source does not say when it was
  knowable is `UNKNOWN` and never read — not assumed available when observed,
  which is how period-end-stamped data looks ahead. There is likewise no
  default calendar, staleness bound or vintage policy (v3.7).
- **No hindsight vintage policy.** Reading today's restatement at a past
  instant is not offered; `restatements` measures hindsight under its own name.
- **No forward fill of a price, and no knowledge frame joined to prices by a
  loosened guard.** `align_prices` is a checked join (v3.7).
- **No regime taxonomy, and no p-value in an event study.** Labels are the
  caller's words; events cluster in calendar time, so a test assuming
  independence overstates significance by an unmeasured amount.
- **No vendor data feed of any kind** for events, alternative data or
  fundamentals, and no LLM, AI-service, Quant-Mind, OpenBB or RedDesk
  integration.

---

# External dependencies

Real, and not AlphaLab's engineering to complete.

- **Verification against a commercial venue.** The venue transport and the
  WebSocket client are written to protocol and exercised end to end over real
  sockets against local servers that verify signatures, timestamp windows,
  idempotency keys and accept tokens. They have never been pointed at a
  commercial venue: this environment has no network egress and holds no vendor
  credentials. Both transports say so in their own docstrings.
- **Named vendor request shapes.** Pointing a transport at a named venue needs
  that venue's request shapes, which differ per venue and belong to an adapter.
- **FX data.** v2.17 adds the rate-feed *boundary* — ordering, deduplication,
  conflict refusal, provenance and staleness — and **not a single rate**.
- **Market calendars and corporate actions.** v3.1 adds the calendar
  abstraction and the split/dividend arithmetic, and **not one holiday and not
  one action**. Both are vendor- or exchange-supplied and change on their own
  schedule.
- **Market conventions.** v3.4 adds the convention *contract* — the tick
  schedule, the lot specification, the settlement rule, the multiplier — and
  **not one venue's values**. An exchange publishes them and revises them.
- **Deposit and discount curves.** `covered_forward_rate` requires both deposit
  rates and the day-count basis as arguments; AlphaLab holds no curve and
  bootstraps none. A computed parity forward is arbitrage-free and is not a
  price anyone traded.
- **Futures margin figures.** A clearing house sets initial and maintenance
  margin per contract and revises them without notice. `ContractMarginSpec`
  carries what was published, and `position_margin` refuses a specification
  dated after the research instant.
- **Crypto venue specifications.** Funding interval, fee tier, price source,
  minimum notional and settlement asset differ per venue and per contract.
  `VenueSpecification` declares them; AlphaLab holds no exchange adapter, no API
  client and no credential.
- **A reader for any binary columnar format.** Parquet is expressible in
  provenance through `RawSource.media_type`; reading it needs a third-party
  dependency, and AlphaLab has none.
- **Classification data.** v2.11 supplies the security master's *mechanism* and
  v2.15 its provenance. AlphaLab ships no taxonomy and no reference-data feed, so
  a sector breakdown requires an operator who declares one. Sector is also the
  only dimension: industry, country, issuer and rating are each a separate
  decision with their own consumers.
- **What a rerun needs.** A reproducibility manifest identifies the dataset
  bytes, the strategy code, the dependency set and the engine; AlphaLab stores
  none of them, and the live objects a run records by type are supplied back by
  the caller.
- **What an environment offers.** Every `TargetEnvironment` is an application's
  declaration of a broker's, a market's and a runtime's capabilities; AlphaLab
  discovers none of them.
- **Runtime observations and resource measurements.** Supplied by whoever
  watched the deployment or timed the run. AlphaLab observes and measures
  nothing on its own.
- **Events, alternative data and fundamentals.** v3.7 adds the point-in-time
  contract and **not one record**: the rows, the bytes they were read from, a
  vendor's line-item names and the price a valuation divides by are the
  caller's.

---

# Known defects — found, not yet fixed

A class of its own, because unlike everything above these are defects. Each
predates v3.6, was found by building real evidence for it, and is stated where
v3.6 meets it rather than silently relied on. Fixing the first three needs
position quantities in the persisted risk state — a pipeline snapshot schema
decision that deserves its own ADR (ADR-0041, *Consequences*).

- **The pre-trade position check compares different units.**
  `check_position_limit` adds an asset's notional exposure
  (`ExposureStatus.asset_exposure` holds market values) to an order's quantity
  and compares the sum with `PositionLimit.max_quantity`. A cap near a real
  share count refuses orders the position does not warrant. Every
  `RISK_LIMITS` certification states it.
- **`DailyLossLimit` is never enforced.** `RiskState.daily_loss` is not
  maintained on the execution path, so the daily-loss check always reads zero.
- **`ExposureLimit.max_net_exposure` is read by no pre-trade check**, and its
  sign convention is defined nowhere.
- **`ingest_rows` identifies what its caller's source says.** It records the
  `RawSource` it is given (ADR-0036), so rows recorded with an empty payload
  share one dataset version whatever they contain. Reproducibility manifests
  refuse such a dataset and certification does not count it as verified data;
  the ingestion contract itself is unchanged.

---

# Optional future evolution

Could be built. Nothing depends on any of it, and no commitment is made here.

- **Classification dimensions beyond sector**, and sector-based risk limits.
  `sector_exposure` is visibility; no risk check reads a sector.
- **Neutralization against several continuous exposures at once.** v3.2 offers
  mean, group and single-regressor beta, each of which is exact. A general
  least-squares solve over a rank-deficient or nearly-collinear design — which
  factor exposures routinely are — produces residuals that look like a result
  and are numerically meaningless, so it is not offered. Composing two
  neutralizations is supported, and the transform chain records that this is
  what was done.
- **A deflated Sharpe ratio, and corrections beyond Bonferroni.** Šidák and
  false-discovery-rate corrections need distributional assumptions
  `alphalab.research` cannot check; a deflated Sharpe needs the variance of the
  trial statistics *and* normality that daily returns do not satisfy.
  Bonferroni is offered because its assumption fits in a line, and the report
  says where it is conservative.
- **A t-statistic on an information coefficient.** Overlapping forward-return
  windows make consecutive ICs strongly autocorrelated by construction, so the
  usual `IC_mean / (IC_std / sqrt(n))` is inflated by a factor this package
  cannot measure. The per-instant series is reported instead, so a caller who
  can model the overlap has what they need.
- **A half-life fitted to a decay profile.** It requires assuming a functional
  form and fitting it to a handful of noisy points, after which the fit is
  quoted as though it were measured. The profile and the first negative horizon
  are reported instead.
- **A vendor adapter package** implementing one named venue's request shapes over
  the existing transport, which is the smallest step from connectivity to
  integration.
- **Execution-path delivery of external information.** An adaptive strategy on
  the execution path learns from the market events it is dispatched; external
  information reaches adaptive state through a research replay and a trained
  checkpoint. Delivering observations as execution-path events changes the
  canonical spine and its snapshots, and needs its own ADR (ADR-0042).
- **Statistical regime models** — hidden Markov, Markov switching — which need
  an estimation step with an identity and a stated fitting window.
- **A streaming observation set** for data arriving during a live run, and
  split-adjusted per-share fundamentals or currency-converted figures (v3.7 reads
  them as published and refuses a mixed-currency input).
- **Consolidating the two identical provider-vocabulary `AssetClass` enums** in
  `live.provider` and `marketdata.symbols`. Neither is on the canonical path and
  neither is persisted; the canonical asset taxonomy is `core.enums.AssetType`,
  which `brokers` aliases and `data` deliberately renames `DataAssetClass`.
- **A start offset on `AppendOnlyLog`**, which is what
  `OptimizerState.pending_trials` would need to stop being super-linear. It was
  implemented, measured at **+3.9%** on `benchmark_execution_pipeline`, and
  refused on that evidence — the same trade ADR-0028 refused at +1.78%.
  `alphalab.optimizer` is a standalone package with no in-repo consumer, so the
  term is off every canonical path.
- **Reviving `on_fill` / `on_order` / `on_timer` as pipeline-driven hooks.**
  `StrategyProtocol` declares them and `Dispatcher` routes them, but nothing in
  `ExecutionPipeline` constructs the events that would reach them; a caller
  driving `StrategyEngine.process_event` directly can. Wiring them into the
  pipeline needs a second strategy dispatch per event and would change intent
  ordering and every parity baseline.
- **Per-strategy sub-ledgers in `PortfolioEngine`.** The strategy-runtime design
  anticipated them; what shipped instead is the allocation contribution ledger
  and `order_shares_by_strategy`, which answers the attribution question without
  a second book.
- **A durable home for a `StrategyProgression`.** v3.5 keeps it as a value the
  caller holds, because a field on `LifecycleState` would move
  `LIFECYCLE_SNAPSHOT_SCHEMA` and AlphaLab has no migration framework. An
  application that wants one persists it through its own store, exactly as it
  persists a `RunAuthorization`.
- **Health evaluated over a window rather than an instant.** `evaluate_health`
  judges one observation, so "this has been degraded for ten minutes" and "it
  recovered and broke again" are a caller's to derive from a series of reports.
  A window needs a retention policy and a clock, and neither belongs in a pure
  function.
- **Derived alignment for a comparison.** `AlignmentKey` has two members and no
  inference. A fuzzy matcher over price, quantity and time would pair records no
  identity connects and report the pairing as a measurement.
- **A lock-file reader** that turns a caller's lock file into a
  `DependencyManifest`. It would read a file the caller names and resolve
  nothing, so it would not break the "no environment snapshot" boundary; it is
  simply not needed to state the contract.
- **A durable home for fingerprints, manifests and reports.** An application
  that wants one persists them through its own store; each has a deterministic
  JSON form through `alphalab.persistence.serialize`.
- **A rerun harness.** A rerun is the caller's to perform from a manifest's
  declared inputs; the lifecycle constructs and executes nothing (ADR-0033
  decision 5), so a harness would live beside the execution path, not in it.

---

# Versioning

**Major releases** introduce architectural milestones or breaking public API
changes. v2.0.0 unified the canonical execution domain models; **v3.0.0 freezes
the architecture and is deliberately additive** — the removals that would have
made it breaking were taken in v2.17.

**Minor releases** introduce new capabilities, and have occasionally made small,
documented breaking changes to a narrow public API where correctness required it:
v2.2.0 changed `AllocationEngine.release_reservation` to take no amount because
the reservation ledger owns it; v2.3.0 changed `alphalab.brokers`' field names so
both broker packages speak one vocabulary; v2.4.0 refused two model-registry
stage transitions that made rollback indistinguishable from promoting something
old; v2.5.0 gave a partially filled simulated order a terminal state; v2.16.0
made `governance` a required argument at every governed entry point; and v2.17.0
required the margin rates and currencies that had been silently defaulted.

**Patch releases** focus on stability, bug fixes, and performance.

**v3.1.0** made two narrow documented changes of the kind described above, both
because a v3.1 requirement contradicted a v3.0 behaviour directly:
`UniversalDataEngine.clean` now requires a `CleaningPolicy` rather than applying
an implicit one, and it — like `convert_timeframe` — derives a new dataset
version rather than replacing records in place.

**v3.4.0** made seven, all of the same class: a market convention that had been
defaulted to one market's value became required. `FutureContract.currency`,
`OptionContract.multiplier`, `OptionContract.style`, `OptionSpec.style`,
`FundingRate.interval_hours`, `CryptoInstrument.contract_size` and
`compute_funding_payment`'s `contract_size`. Each produced a number rather than
an error when it was wrong. `FxRates.convert` additionally began refusing a rate
dated after the conversion instant, which is a look-ahead it previously allowed.

After v3.0.0, the bar for a change rises: the invariants listed in
`nowandfuture.md` are frozen, and a change to any of them is a major release with
an ADR.

---

# Feature notes (delivered)

Scope notes for each delivered PR, kept as the historical record of what each
engine was built to do. See the table at the top for the release each shipped in.

## PR-034 — Feature Store

Institutional feature engineering framework: feature registry, versioning,
metadata, validation, caching.

## PR-035 — Factor Library

Reusable quantitative factors: momentum, value, quality, carry, volatility,
liquidity.

## PR-036 — Options Engine

Option chains, Greeks, volatility surfaces, pricing models, strategy simulation.

## PR-037 — Futures Engine

Continuous contracts, rolls, curve analysis, calendar spreads.

## PR-038 — Crypto Engine

Spot, futures, perpetuals, funding rates, exchange normalization.

## PR-039 — Macro Engine

Economic indicators, central bank events, yield curves, inflation, GDP.

## PR-040 — Alternative Data

News, sentiment, satellite, shipping, ESG, credit cards. Since v3.7 also the
point-in-time foundation for any category of external information — events,
observations and fundamentals with source identity and versioned sets (ADR-0042).

## PR-041 — Machine Learning

Feature pipelines, training, cross validation, prediction, evaluation.

## PR-042 — Deep Learning

LSTM, transformers, CNN, sequence models.

## PR-043 — Reinforcement Learning

Trading environments, policy optimization, agent evaluation.

## PR-044 — Cloud Research

Distributed quantitative research: remote execution, worker pools, cluster
management.

## PR-045 — Cluster Scheduler

Job scheduling, queue management, distributed orchestration.

## PR-046 — Experiment Tracking

Experiment history, metrics, parameters, versioning.

## PR-047 — Model Registry

Versioning, promotion, rollback, deployment metadata.

## PR-048 — AI Research Assistant

Deterministic, offline grid-search research driver: candidate generation,
caller-supplied evaluation, best-first ranking, Markdown reporting, and the
bridge that lifts a chosen candidate into a canonical `StrategyDefinition`. No
LLM and no network.

## PR-049 — Deployment Manager

Packaging, release management, rollbacks, production deployment.

## PR-050 — AlphaLab Enterprise

Principals and sessions (no credentials accepted or stored), RBAC, append-only
audit log, multi-user workspaces, secret *references* and rotation metadata, and
a compliance snapshot.

---

# Long-Term Vision

AlphaLab set out to be a complete quantitative research platform covering market
data, feature engineering, research, portfolio construction, machine learning,
production trading, cloud infrastructure and enterprise deployment — while
preserving determinism, immutability, modular architecture, event-driven design
and production readiness.

The engine expansion planned after v1.0.0 is delivered. The integration work that
followed it is delivered too: the execution path is one spine with one run owner
and four drivers, the lifecycle path is joined to it, orders reach a real venue
and fills come back through the same accounting a simulated fill takes, and state
round-trips durably across processes.

What remains is not a missing layer. It is the three classes above — deliberate
boundaries that should stay, external dependencies that are somebody else's to
supply, and optional evolution that nothing is waiting on.

---

# Community

Contributions are welcome; see `CONTRIBUTING.md`.

Because the architecture is frozen, a contribution that changes an ownership
boundary, a schema contract or a documented invariant needs an ADR and a major
release. Everything else — a vendor adapter, a new standalone engine, a strategy,
a benchmark, a test, a documentation fix — follows the ordinary workflow.
