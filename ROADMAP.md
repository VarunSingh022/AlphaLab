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

**v3.8.0** is the eighth, and answers what a desk running several strategies
asks next: what to own, where the risk comes from, what the strategies share,
and how much capital each one gets. The risk model becomes values with
identities in `alphalab.analytics`, which also gains risk budgets and
cross-strategy risk; `alphalab.portfolio_optimizer` gains constrained
construction by one certified solver and Black–Litterman;
`alphalab.portfolio` gains multi-strategy books; `alphalab.allocation` gains
capital plans; and `factor_library`, `api` and `lifecycle` gain the joins. No
package is added, three package edges are, none a cycle, and no snapshot schema
changes. ADR-0043.

**v3.9.0** is the ninth, and is the path from a decision to a venue and back as
one contract, whichever adapter an application brings: what a venue can do,
declared where it is true and checked before anything is sent; one table of
legal order transitions for the OMS and the venue, with every venue report given
exactly one meaning; execution algorithms whose children stay their parent's;
routing decided from supplied evidence and explained; and execution measured
against named references. `alphalab.core`, `broker`, `execution`, `runtime` and
`lifecycle` deepen; no package and no package edge is added, and no snapshot
schema changes. ADR-0044.

**v3.12.0** is the third of four **pre-v4 releases**: numerical methods right
at the edges of their range, durable state that restores what was captured,
costs that follow the work at 10,000 assets, 1,000 strategies and 100 venues,
and the capabilities deferred to it — calendars inside simulation, strategy
capital ceilings, classification limits, external information on the
execution path, retention and incremental checkpoints, an evidence store,
multi-account reconciliation, declared trade prints and trainable sequence
models (ADR-0047). `plugins`, `optimizer` and the reporting dashboards leave
the library (SCF-003).

**v3.11.0** is the second of four **pre-v4 releases**: the capabilities a
strategy needs before its API is frozen — instrument economics, order terms,
target positions, slices, leak-proof research, construction with costs and
lots — with the application's packages moved out of the library (ADR-0046).

**v3.10.0** is the first of four **pre-v4 releases**, and is a correctness
release rather than a capability one. The pre-v4 audit re-read every subsystem
of v3.9.0 and recorded, item by item, what must be true before v4.0 freezes the
public surface: the master audit is `docs/audit/PRE_V4_MASTER_AUDIT.md` and the
plan of record is `docs/audit/PRE_V4_COMPLETION_LEDGER.yaml`, where every item
has an ID, a disposition and a release. v3.10 closes the items assigned to it —
risk, allocation, money, analytics, execution realism, performance, time,
determinism, upgradeable persistence, the vendor code and the silent defaults.
v3.11, v3.12 and v3.13 close the rest, and v4.0.0 certifies the result.
ADR-0045.

| Class | Meaning |
| --- | --- |
| **Delivered** | Built, tested, and described by the documentation |
| **Deliberate boundary** | Not built, on purpose, with a reason and usually a regression test |
| **External dependency** | Not AlphaLab's engineering to do — data, credentials, a vendor's API |
| **Known defect** | A real defect, found and stated where it matters, not yet fixed |
| **Planned before v4** | Required for a complete v4, scheduled in the pre-v4 ledger with an ID and a release |

Until v3.10 a fifth class, *optional future evolution*, held things that could
be built with no commitment. The pre-v4 audit re-classified every item in it:
most are required for a complete v4 and are now **planned**, a few are
**deliberate boundaries**, and one was removed with the code it described. At
v4.0 this document lists only boundaries and external dependencies.

Nothing classed as a deliberate boundary or an external dependency is a defect,
and none blocks a release. Known defects are listed apart, in their own class,
so they are never mistaken for decisions.

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

## v3.12.0 — the pre-v4 hardening release

- **Numerics** (NUM-003, NUM-004, NUM-007, DAT-008): R² undefined for a
  constant series; the normal CDF from `erfc`, precise deep in the lower tail;
  least squares by Householder QR with a condition bound; theta on the
  pricing year; a float instant's resolution stated.
- **Durability** (PER-003, PER-006): directories flushed after a rename; an
  allocation budget's currency restored; every v3.11.0 payload read from
  frozen fixtures.
- **Simulation** (EXE-010): a DAY order expires at its venue's last close of
  the trading day.
- **Capital and risk** (OFE-003, OFE-001, PRF-009): per-strategy capital
  ceilings; classification along any dimension and limits on its buckets,
  their gross kept by the book.
- **External information** (OFE-009, OFE-011): observations delivered at the
  instant they became knowable; streaming observation sets; split-adjusted and
  converted fundamentals.
- **Memory and checkpoints** (PRF-004): declared retention, history refused
  beyond it, incremental checkpoints verified link by link.
- **Evidence and health** (OFE-016, BDY-008, OFE-017): a durable evidence
  store; health over a window.
- **Reconciliation** (BRK-004, OFE-023): one book against every account it is
  spread across.
- **Data** (FEA-004, BDY-018, DAT-009): declared trade prints with venue
  identifiers and aggressor sides; cleaning judges quotes as validation does.
- **Models** (SCF-004): backpropagation through time for the LSTM, and
  attention's backward pass.
- **Research and consolidation** (RES-001, SCF-003, OFE-013): the v1 engine
  restated as measurements under a stated policy; one parameter-search
  authority, with `optimizer` removed; `plugins` and the reporting dashboards
  removed; distributed cancellation fixed; session timers over a calendar;
  exact report numbers (ANA-006).
- **Scale** (PRF-005, PRF-010, TST-011): factor-structured construction to
  10,000 assets; an event reaches strategies through an index; every benchmark
  ceiling judged by one method; a stress program at 10,000 assets, 1,000
  strategies and 100 venues.

## v3.11.0 — the pre-v4 capability release

- **Instruments** (ACC-005–007): declared economics — multiplier, settlement
  (fully paid, futures variation margin, option premium, perpetual), lot,
  minimum notional, negative prices; cash flows and splits through the path;
  maker rebates.
- **Orders** (EXE-003): limit, stop, stop-limit; IOC, FOK, GTD, DAY, OPG, CLS;
  resting orders filled as makers or takers.
- **Targets** (FEA-001, ALC-006, ALC-007): target quantities and weights against
  each strategy's own position, rounded toward zero onto whole units and lots;
  a sale commits no budget.
- **Dispatch** (EXE-004, EXE-005, EXE-007): enforced subscriptions; slices;
  `on_start`, `on_stop`, fill and order feedback.
- **Live** (BRK-002, BRK-003, BRK-008, EXE-009, LIV-001): venue sequence
  numbers, persisted requests, holds, FX settlement, a required paper cost
  model.
- **Research** (DAT-002, DAT-003, FEA-003, OFE-004–006, FEA-002, NUM-005):
  implementation lag, delisting returns, walk-forward optimization, multiple
  testing, deflated Sharpe, Newey-West IC, multivariate neutralization,
  benchmark statistics, carry in Black-Scholes.
- **Data and identity** (DAT-004, DAT-005, DAT-007, REP-002, DET-006): dated
  aliases, `TimeFrame` as a value, engine build and tz database in manifests,
  identities by value.
- **Construction** (OFE-002): Ledoit-Wolf, EWMA and factor-model covariance;
  linear costs solved exactly; lot rounding.
- **Boundary** (BND-002, BND-003, SCF-001, BRK-007): `enterprise`, `workbench`,
  `studio` and venue credentials moved to the application.
- **Numerics and performance** (NUM-012, NUM-013, PRF-006, PRF-007): every run
  entry point pinned; exact lot arithmetic; a run's cost linear in its length
  again (a log slice had copied the whole log since v2.1); v3.10's per-operation
  cost reduced.

## v3.10.0 — the pre-v4 correctness release

- **Risk on the projected book** (KD-001–003, RSK-001–006): one projection of
  the book after the order, counting working orders; buying power charged only
  for what grows a position; no limit refuses a trade that reduces what it
  limits; breaches refuse only what grows exposure; the daily loss limit
  maintained in a declared IANA zone; net exposure enforced; typed severity.
- **Allocation** (ALC-001–003, ALC-005): long-only against committed positions;
  `Intent` documented and typed as a delta; sizing refuses what it cannot size.
- **Money** (ACC-001–004, ACC-008): ISO 4217 minor units plus declared ones;
  exact prices and quantities; one pinned decimal context; no default currency
  on a valuation helper.
- **Analytics** (ANA-001–005): one point per instant; declared or observed
  annualization, recorded; `None` for undefined statistics; trade statistics
  over realizing fills.
- **Execution realism** (EXE-001, EXE-002, EXE-006, EXE-008, NUM-008):
  `FillTiming`; recorded `ExecutionAssumptions`; reported strategy failures and
  an optional halt; as-of FX on the risk path; exact costs.
- **Performance** (PRF-001–003): incremental marking over exact per-currency
  totals — linear in the universe; compacting persistent maps.
- **Time and data** (DAT-001, DAT-006, KD-004): bars stamped at the end of their
  interval, the source's convention required; the scheduler's UTC calendar
  removed; row-ingested datasets identified by their content.
- **Determinism, numerics, persistence** (DET-001–004, REP-001, BRK-001,
  NUM-001/002/010/011, PER-001/002, REL-001): seeds required and the stream
  pinned; one version source; numeric reconciliation; non-finite inputs
  refused; **versioned schema upgrades** replacing the "no migration framework"
  boundary; strict JSON; sockets released.
- **Boundary and defaults** (BND-001, BND-004, SCF-002, API-003): vendor
  market-data clients, `feed`, `live` and exchange symbol quirks removed; the
  last `"USD"` configuration defaults removed and the defaults sweep tightened.
- **Gates** (TST-001–007): stabilized complexity guards and a universe-growth
  guard; zero skips for every user; CI with `-W error`, examples, clean
  wheel/sdist installs and scheduled benchmarks; hooks aligned with CI; every
  mutation the audit's harness let through pinned.

## v3.9.0 — the universal execution contract

The ninth capability release on the frozen architecture. No package and no
package edge added, no schema touched. ADR-0044.

- **Capabilities declared where they are true** — `CapabilityDeclaration` at
  venue, market and account level, every answer `SUPPORTED`, `UNSUPPORTED` or
  `UNDECLARED`; `order_requirements` derives short sales and fractional
  quantities from the order; `check_compatibility` is `COMPATIBLE` only when
  every check is supported. The v3.5 `BrokerCapabilities` is projected from a
  declaration, refusing what was never declared.
- **One order lifecycle** — twelve normalized `ExecutionEventKind`s and
  `ORDER_TRANSITIONS`, read by `oms.order.Order` and by the venue boundary; every
  `VenueEvent` applied with one outcome (applied, duplicate, stale, conflict,
  unknown order, invalid); fills converge in any delivery order; a fill during a
  pending cancel keeps the cancel pending.
- **Idempotent requests** — `CancelRequest` and `ModifyRequest` identified by
  content and sequence, issued against a `RequestLedger`: a retry is recognised,
  never sent twice.
- **Snapshot reconciliation** — the mirror against a dated `VenueSnapshot`,
  freshness first, then every order, fill, position and balance, thirteen
  divergence kinds.
- **Execution algorithms** — TWAP, VWAP, participation, slicing and iceberg-like,
  with a stated urgency and whole-increment apportionment; top-up releases;
  children that carry the parent's strategies and settle on the parent through
  the canonical path, sent by `runtime.route_child_order` behind five gates.
- **Smart routing** — `select_route` from supplied quotes, declarations, cost
  models and latencies; every venue judged with a reason; single, split and
  partial routes; `INFEASIBLE` kept apart from `INSUFFICIENT_EVIDENCE`.
- **Execution analytics** — implementation shortfall with its components and
  each strategy's share, slippage against a named reference, fill quality,
  latency with clock sources, rejection rate, venue quality, and per-currency and
  FX-converted reports.
- **Lifecycle** — child orders in book-to-mirror reconciliation, and
  `research_configuration_with_execution` putting algorithm and routing
  identities in a fingerprint's research settings; the key is unchanged.
- **Found and fixed before release** — the OMS and the mirror disagreeing about a
  fill during a pending cancel; `EXPIRED` missing from the cancel validation;
  amendments validated as cancels; `reconcile` collapsing duplicated remote
  records and subtracting cash across currencies; OMS fills that could leave a
  `FILLED` order working or overfilled; the strategy split computing in the
  caller's decimal context; and two quadratic paths in the draft, found by the
  complexity guards.

## v3.8.0 — advanced portfolio and risk

The eighth capability release on the frozen architecture. No package added,
three package edges added and pinned, no schema touched. ADR-0043.

- **One risk model** — `CovarianceMatrix` with its currency, period, source,
  observation count and derivation in its identity; definiteness measured by a
  rank-revealing Cholesky; ridge and diagonal shrinkage as recorded derivations;
  `FactorLoadings` and `Classification` that refuse holes. v3.3's decomposition
  calls the same arithmetic, unchanged bit for bit.
- **Constrained construction** — `construct` for minimum variance,
  mean-variance (with an optional volatility cap), maximum diversification,
  risk parity with equal or stated budgets, and robust mean-variance over an
  ellipsoidal or box set, under bounds, concentration, gross, group, factor,
  turnover, notional and volatility constraints; one dual active-set solver,
  a KKT certificate, conflicts named, no weights unless optimal.
- **Black–Litterman** — a supplied equilibrium prior or supplied returns, a
  required `τ` and views with stated variances into a posterior mean,
  its uncertainty and the predictive covariance.
- **Risk budgets** — Euler contributions of exposure lines grouped by asset,
  strategy, sector, country and currency, every dimension summing to the same
  volatility; limits judged under a stated tolerance and reported.
- **Multi-strategy books** — sleeves from each strategy's own accounting
  state, holdings with every strategy's contribution, crossed and opposing
  positions visible, valued in one reporting currency at recorded rates and
  reconciled to the cent.
- **Cross-strategy risk** — return correlation with its basis, overlap of
  holdings, factor crowding within the portfolio, common exposures, capital
  concentration and shared pools.
- **Capital allocation** — plans across strategies, markets, brokers, accounts
  and currencies, allocated in each account's currency, reconciled exactly,
  refused rather than silently scaled, composed with the reservation ledger and
  the run budget. A broker is an identifier.
- **Lifecycle** — `research_configuration_with_portfolio` puts construction and
  capital identities in a fingerprint's research settings; the key is
  unchanged.
- **Found and fixed before release** — five v1 construction-engine defects (a
  missing covariance or forecast read as zero, a sector cap ignored, an excess
  above the target returned, the spread left out of the cost estimate, a
  constant clipped amount), and three per-call rescans in the new code found
  while writing the complexity guards.

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
- **Durable state for the v3.5 values — since v3.12.** The reason this was a
  boundary was that a new field would have made every earlier payload
  unreadable; v3.10's schema upgrades removed that reason, and v3.12's
  evidence store files manifests, fingerprints and reports under their own
  identities (BDY-008, OFE-016). `LifecycleState` itself is unchanged.
- **No `SettlementPolicy` object.** `STRICT_MATCH` is the only settlement rule
  because no alternative exists: a permissive mode could only book honestly —
  making the book mixed, which the next valuation refuses without rates — or
  convert, which is FX and is a deliberate act with a recorded rate (ADR-0028,
  ADR-0035).
- **No triangulation, no implicit inversion, no default rate.** A configured rate
  is an invented one. `with_inverses()` will mint the opposite direction, and
  marks what it mints as `derived` (ADR-0020, ADR-0035).
- ~~**No migration framework.**~~ **Replaced in v3.10** (BDY-007, PER-001): every
  snapshot subsystem declares its schema history and upgrades an older payload
  through explicit, pure steps that refuse rather than invent (ADR-0045).
- **No authentication, IAM or federation.** Out of scope per ADR-0018. Until
  v3.11 the library held venue credentials — an `api_key` and signing secret in
  `broker.transport.VenueCredentials` for the HMAC-signed venue transport — and
  `alphalab.enterprise` kept secret *references* with rotation metadata. Both
  are application concerns and left the library in v3.11 (BRK-007, BND-002;
  ADR-0046); it now holds no credential of any kind.
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
- **No depth ingestion from a flat file; trade prints only when declared.** A
  `price`/`size` pair is indistinguishable from a partially populated bar
  without a declaration, so since v3.12 a print is read only from the columns a
  caller names (`TradeColumns`, FEA-004) and never detected from a header; a
  depth book is not a flat table, and stays out (BDY-018).
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
- **No silent scaling of capital.** An account asked for more than it holds
  refuses the whole plan unless `PRO_RATA` is stated, and then the scale is
  recorded; a capital limit is never met by scaling (v3.8).
- **No unconstrained answer behind an infeasible one.** A construction whose
  constraints cannot all hold is `INFEASIBLE`, names the conflict and carries no
  weights; nothing is relaxed and no fallback objective is tried (v3.8).
- **No default risk aversion, `τ`, view confidence, market portfolio,
  tolerance or shrinkage intensity.** Each is a choice with no neutral value
  (v3.8).
- **No enforcement in a risk budget.** A breach is reported with the bucket and
  the limit named; nothing rebalances (v3.8).
- **No broker adapter in capital allocation.** A broker and an account are
  identifiers an application maps to its own adapters and credentials, none of
  which reach AlphaLab (v3.8).
- **No capability discovery, and no silence read as support.** A venue's
  capabilities are declared by the application that has the adapter; an answer
  nobody gave is `UNDECLARED`, and a check that meets one is `UNDETERMINED` —
  never compatible (v3.9).
- **No vendor inside the execution contract.** Adapters translate their venue's
  messages into normalized events; AlphaLab names no venue, holds no credential,
  opens no connection and defines no vendor message structure (v3.9).
- **No venue event in the broker's event log.** Normalized events are judged and
  applied to the mirror; `BrokerState.events` keeps its closed set of types and
  the broker snapshot schema is unchanged (v3.9).
- **No retry of a venue's refusal.** A rejected child fails its algorithm run;
  what to do next is the caller's decision (v3.9).
- **No conversion inside a routing decision.** A venue quoting in another
  currency is ineligible, not converted; routing compares like with like
  (v3.9).
- **No default urgency, quote age or tolerance, and no best-execution claim.**
  Each is a choice with no neutral value; whether an order may be routed away
  from a venue at all is not something AlphaLab knows (v3.9).

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
  registry's only dimension: industry, country, issuer and rating are each a
  separate decision with their own consumers. v3.8's risk budgets and
  construction read any classification a caller supplies with a named source —
  a country file, say — and AlphaLab holds none.
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
- **Portfolio and risk inputs.** v3.8 estimates a sample covariance and nothing
  else. Expected-return forecasts, market values for an equilibrium prior, views
  and their confidence, factor data, FX rates, account balances and the mapping
  from a broker or account identifier to an adapter and its credentials are the
  caller's.
- **Venues.** v3.9 adds the execution *contract* and **not one venue**: the
  capability declaration for a real venue, its quotes, volume profiles, printed
  volume and benchmark prices, and the adapter that translates its messages into
  `VenueEvent`s are the application's.

---

# Known defects — found, not yet fixed

The four defects this section listed through v3.9 — the position check that
compared different units, the daily loss limit that was never enforced, the net
exposure limit nothing read, and `ingest_rows` identifying rows by the source
its caller named — are fixed in v3.10 (KD-001–004).

What remains known is in the pre-v4 ledger, each with an ID and a release.
v3.12 fixed the research engine's daily assumption and 0–100 scores (RES-001),
the single-currency `BacktestResult.valuation` (API-004), the calendar-less
DAY order (EXE-010), the single-sample benchmark ceilings (TST-011), the three
numerical defects (NUM-003, NUM-004, NUM-007) and the unflushed directory
(PER-003). What is still known:

- **Memory** (stated by PRF-004; classified by the v3.13 audit): the OMS order
  book, execution reports by order, and a live session's routed and settled
  orders grow with the orders a run places — retention bounds the logs, not
  these, and a checkpoint segment carries the whole order book.
- **Performance** (PRF-006, v3.11 — bounded, not eliminated): against v3.9 on
  one machine (five interleaved rounds), the portfolio-engine micro-benchmark
  runs at 1.69× v3.9's time (1.51–1.72; exact per-currency totals and
  instrument economics on every fill); a one-asset backtest 1.05×, the pipeline
  1.01× and the OMS 0.83×.

---

# Planned before v4 — the former optional list, re-classified

Every item this section listed as optional future evolution through v3.9, with
the disposition the pre-v4 audit gave it.

**Planned** — each an item in `docs/audit/PRE_V4_COMPLETION_LEDGER.yaml`:

| Item | Ledger | Release |
| --- | --- | --- |
| Richer portfolio construction: estimated shrinkage, EWMA and factor-model covariance, costs in the objective, rounding to lots (cardinality and joint lot selection are integer programs: an explicit boundary, ADR-0046) | OFE-002 | v3.11 — delivered |
| Neutralization against several continuous exposures at once, by a rank-revealing solve | OFE-004 | v3.11 — delivered |
| A deflated Sharpe ratio; Holm and false-discovery-rate corrections beside Bonferroni | OFE-005 | v3.11 — delivered |
| A t-statistic on an information coefficient, corrected for overlapping windows (Newey–West) | OFE-006 | v3.11 — delivered |
| Pipeline-driven `on_fill` / `on_order` / `on_timer` | OFE-014 | v3.11 — delivered |
| A venue sequence number; persisted child bindings and request ledger | OFE-021, OFE-022 | v3.11 — delivered |
| Classification dimensions beyond sector; sector-based pre-trade limits | OFE-001 | v3.12 — delivered |
| Per-strategy capital ceilings on the execution path | OFE-003 | v3.12 — delivered |
| Execution-path delivery of external information | OFE-009 | v3.12 — delivered |
| A streaming observation set; split-adjusted and currency-converted fundamentals | OFE-011 | v3.12 — delivered |
| The optimizer's super-linear `pending_trials`, removed with the research consolidation | OFE-013 | v3.12 — delivered: the optimizer was removed (SCF-003) |
| A durable home for progressions, fingerprints, manifests and reports | OFE-016 | v3.12 — delivered |
| Health evaluated over a window | OFE-017 | v3.12 — delivered |
| Book-to-mirror reconciliation across several brokers' accounts | OFE-023 | v3.12 — delivered |
| A lock-file reader | OFE-019 | v3.13 |
| A rerun harness | OFE-020 | v3.13 |
| An optimal split; estimated urgency and randomized iceberg tranches | OFE-024, OFE-025 | v3.13 |

**Kept as deliberate boundaries**: statistical regime models, which need an
estimation step with an identity (OFE-010); a half-life fitted to a decay
profile and derived alignment for a comparison, each of which would report a
fit or a pairing as a measurement (OFE-007, OFE-018); per-strategy sub-ledgers
in `PortfolioEngine`, whose question the contribution ledger already answers
(OFE-015); process supervision, hot reload and hook timeouts, which are the
host's (OFE-026).

**External**: a vendor adapter package (OFE-008) — a venue's request shapes
belong to the application that connects to it.

**Removed**: the two provider-vocabulary `AssetClass` enums, with the packages
that held them (OFE-012, v3.10).

The two reasons the old list gave for not building a deflated Sharpe ratio and
an information-coefficient t-statistic did not hold on re-examination: Holm's
step-down needs no assumption beyond Bonferroni's, and overlapping windows are
what a Newey–West correction is for (DOC-002).

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

**v3.8.0** made six, all in the v1 construction engine and all of the same
class: a figure produced where a refusal or the stated figure belonged.
`PortfolioAdapter.dict_to_covariance_matrix` and `dict_to_expected_returns`
refuse a missing entry rather than reading it as `0.0`; `apply_weight_constraints`
refuses a `max_sector_exposure` it cannot apply and lower bounds that force the
weights above the target, rather than ignoring the one and returning the other;
the manager's cost estimate includes `CostModel.spread_rate`, in a new last field
`TransactionCostEstimate.estimated_spread` defaulting to zero; and
`ConstraintViolated` reports the weight the constraints moved rather than the
constant `1.0`.

**v3.9.0** made five, each a state no venue can report or an input a result
cannot describe, now refused: `oms.order.Order.fill` must complete the order
exactly and `partial_fill` must leave quantity working (a `FULL_FILL` short of
the order, from `fill_status` or `StaticFill`, now raises
`InvalidTransitionError`), a fill must be positive, and `replace` must leave
something to work; a fill during a pending cancel keeps the cancel pending in
the mirror; `validate_cancel_request` refuses `EXPIRED` and `replace_order`
validates an amendment; and `broker.reconcile` refuses duplicated remote records
and a remote account in another currency.

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
