# ADR-0047: The Pre-v4 Hardening Release — Numerics, Persistence, Scale and Consolidation

## Status

**Accepted and implemented in v3.12.0.**

The third of the four releases the pre-v4 audit plans before the v4.0 freeze
(`docs/audit/PRE_V4_MASTER_AUDIT.md`; the plan of record is
`docs/audit/PRE_V4_COMPLETION_LEDGER.yaml`). v3.10.0 made the canonical path
correct and v3.11.0 gave it the capabilities a strategy needs. v3.12 hardens
what is there: numerical methods that were right in the middle of their range
and wrong at its edges, durable state that could be lost or misread, costs
that grew faster than the work, and packages whose authority was unclear. It
also builds the capabilities the audit deferred to here: calendars inside
simulation, per-strategy capital ceilings, classification limits, external
information on the execution path, a durable evidence store, multi-account
reconciliation, declared trade prints and trainable sequence models.

It adds **no package** and **removes two**, `alphalab.plugins` and
`alphalab.optimizer`, with the reporting package's dashboard layouts (decision
6). It adds **eight
package edges**, each one way and none a cycle: `runtime → data` and
`scheduler → data` (a `MarketCalendar`), `runtime → alt_data` and
`backtesting → alt_data` (observations on the path), `cloud_research →
research` and `research_assistant → research` (one search authority),
`lifecycle → execution` (`ExecutionReport` in multi-account reconciliation) and
`risk → instrument` (a limit's dimension and label normalized by the
registry's rules — the gate still never reads the registry). Snapshot schemas:
pipeline 5→6, run 3→4, allocation 2→3, instrument 2→3; two new envelopes,
checkpoint 1 and evidence 1. Every older payload is upgraded on read, and the
payloads v3.11.0 itself wrote are frozen as fixtures
(`tests/fixtures/snapshots/v3.11.0`) that every later build must read.

Amends **ADR-0030** (the run carries two more continuation fields, eleven in
all; the pipeline's sixteen are unchanged), **ADR-0027** and **ADR-0031**
(classification is along any dimension, with the sector's provenance rules), **ADR-0043** (construction has a
factor-structured solver beside the dense one), **ADR-0045** decision 10 (the
v1 research engine's exemption from the no-silent-defaults rule ends: it is
restated, RES-001) and **ADR-0046** decision 3 (a simulated DAY order reads its
venue's calendar). Depends on ADR-0045 and
ADR-0046 throughout.

---

# Context

The ledger assigns twenty-six items to v3.12. Three are numerical (NUM-003,
NUM-004, NUM-007): methods right in their ordinary range and wrong at its
edges — an R² reported as zero where it is undefined, a normal CDF that
underflows in the lower tail and a theta on a different year from its price,
a regression solved through its normal equations. One is a limitation to
state (DAT-008). Two are defects of durability and API (PER-003, a rename
reported durable before its directory was flushed; API-004) and one is
technical debt (ALC-004). Twelve are capabilities: the eight former optional
evolutions the audit made required (OFE-001, -003, -009, -011, -013, -016,
-017, -023), BRK-004, FEA-004, EXE-010 and SCF-004. Two are boundaries
re-examined (BDY-008, BDY-018). The rest are consolidation (SCF-003, RES-001),
scale (PRF-004, PRF-005) and test method (TST-011).

Building and auditing them found five more, recorded as findings of this
release: PER-006 (an allocation snapshot dropped its budget's currency on
restore, since v2.17), DAT-009 (cleaning kept quotes validation refused),
ANA-006 (a report wrote exact money as a binary float), and two costs the
stress program found — PRF-009 (a classification limit summed its whole bucket
for every order; introduced with OFE-001 and never shipped) and PRF-010 (every
event asked every strategy whether it subscribed; shipped since v3.11).

---

# Decision

## 1. Numerical methods are right at the edges of their range

* **R² of a constant series is undefined** [NUM-003]: `LinearFit.r_squared` is
  `None` when the response does not vary, never `0.0`.
* **The normal CDF is computed from `erfc`** [NUM-004]: `0.5 * erfc(-x/√2)`
  keeps its relative precision in the lower tail, where `1 + erf` cancels to
  zero; deep out-of-the-money values and their implied volatilities are now
  reachable. Theta uses the 365.25-day year the price uses.
* **Least squares by Householder QR** [NUM-007]: `common.linalg.least_squares`,
  column-scaled, with a condition bound (`MAXIMUM_DESIGN_CONDITION = 1e6`) and a
  named zero column; a rank-deficient design is refused, not inverted. At a
  condition number of 1.9e4 the error fell from 2.6e-8 to 5.5e-13.
* **Float instants have a stated resolution** [DAT-008, kept as a limitation]:
  `common.time.instant_resolution(instant)` is the spacing of representable
  instants (2⁻²² s between 2004 and 2038). A nanosecond feed is not
  representable exactly; the limitation is documented where instants are
  defined rather than hidden.

## 2. Durable means durable, and a restore is what was captured

* A rename is flushed with its directory [PER-003]
  (`persistence.durable.fsync_directory`, `ensure_directory`); the run store
  and the artifact store use it.
* An allocation budget's currency survives a snapshot [PER-006]; the decoder
  read nothing it had written since v2.17. No figure changed — the pipeline
  read the currency from its configuration — but `restore(capture(s)) == s`
  did not hold.
* `BacktestResult.valuation_in(currency, rates)` [API-004] values a
  multi-currency run; `valuation` stays the single-currency property.
* Duplicate intents are refused on the record (`AllocationRejected`) in linear
  time, and only validation errors are caught [ALC-004].

## 3. Simulation reads its venues' calendars

`ExecutionPipelineConfig.calendars` (`runtime.calendars.VenueCalendars`, keyed
by the instrument's exchange) [EXE-010]. A simulated DAY order with no
`expire_at` expires at its trading day's **last** close
(`MarketCalendar.day_order_expiry`) — not at `next_close`, which is the end of
the morning window on a market with a lunch break. A pipeline with no calendar
refuses such an order as v3.11 did. The same calendar drives the scheduler's
session timers (decision 6).

## 4. Capital, classification and information on the execution path

* **Per-strategy capital ceilings** [OFE-003]:
  `CapitalBudget.enforce_strategy_budgets` (off by default — every existing
  budget means what it meant). Deployed capital at cost plus what working
  orders reserve, kept per strategy; reductions are free; a breach is refused
  on the record with plain amounts.
* **Classification along any dimension, and limits on its buckets** [OFE-001]:
  `instrument.classify_dimension` with the sector's provenance rules, a
  registry index of every bucket's members, and `risk.ClassificationLimit`
  (gross, share of NAV, a labelled bucket overriding its dimension, optional
  refusal of the unclassified), counting working orders, reduce-only.
* **The bucket's gross is kept, not summed** [PRF-009, found by the stress
  run]: the position book keeps an exact count and gross per bucket and
  currency on every change, for the buckets the run's limits bound; the risk
  gate reads it and adds what working orders commit. Where the two could differ
  — a mixed-currency book, a registry other than the one grouped by, a sum that
  would round — it sums the members as before. The grouping is not part of the
  book's value: equality, snapshots and digests never see it.
* **External information** [OFE-009, OFE-011]: an observation is delivered to
  the strategies subscribed to it at the instant it became knowable
  (`ExecutionPipeline.process_observation`, `RunEngine.deliver_observation`,
  `BacktestEngine.run(observations=)`), its orders resting to the next market
  event; `ObservationStream` checks each arrival incrementally; per-share
  fundamentals are adjusted for share-count changes after publication, and a
  figure converted to another currency records the rate it used.

## 5. Durable evidence and bounded memory

* **An evidence store** [OFE-016, BDY-008]: `model_registry.EvidenceStore`
  files a manifest, fingerprint or report under its own identity on the
  content-addressed artifact store; filing the same value twice changes
  nothing, a different value under the same identity is refused.
* **Health over a window** [OFE-017]: `lifecycle.evaluate_health_window`.
* **Retention** [PRF-004]: `runtime.RetentionPolicy` bounds the market history,
  steps, audit events and results a run keeps; a strategy asking for history
  beyond the window is refused (`HistoryNotRetainedError`), never answered
  short. **Incremental checkpoints**: a base capture, then segments holding
  only what each log appended since the last mark, chained by digest; a chain
  that is missing a link, reordered or altered is refused on read. Not bounded,
  and stated: the OMS order book, execution reports by order, and a live
  session's routed and settled orders grow with the orders placed — a segment
  carries the whole order book.

## 6. One authority per capability (SCF-003, RES-001)

* **One parameter search.** `research.overfitting.parameter_sweep` gains
  `higher_is_better`; `research_assistant` and `cloud_research` enumerate
  through `research.ParameterSpace` and count their searches through
  `parameter_sweep`.
* **Distributed execution kept, fixed**: a cancellation is a `JobCancelled`
  event and `DistributedState.cancelled_jobs`, not a failure; an assigned job
  not yet running can be cancelled and frees its worker's slot.
* **The scheduler is reduced to deterministic timers over `MarketCalendar`**:
  `SESSION_OPEN` and `SESSION_CLOSE` fire at each trading day's first open and
  last close, skipping weekends and holidays, across lunch breaks and
  midnight. `CRON` and `BAR_BOUNDARY` stay refused at registration — a stated
  limitation, not a placeholder.
* **Reporting writes exact numbers**: a `Decimal` is its exact text, and a
  value the encoder does not know is refused instead of stringified.
* **The v1 research engine is restated** [RES-001]: no 252, no 0-100 scores.
  `ResearchPolicy` states every bound (no defaults); the reports are
  measurements; `periods_per_year` and `risk_free_rate` are required.
* **Removed: `plugins`, `optimizer` and the reporting dashboards.** A plugin
  loader whose `execute()` was a placeholder held state nothing read; a second
  parameter search beside `research.parameter_sweep` was a second answer to
  one question, and its `pending_trials` grew super-linearly (OFE-013), which
  the removal resolves rather than patches; dashboard layouts — cards, tables
  and charts arranged for a screen — are presentation, the host's. A report's
  exports stay: they are the data a presentation is built from. The removal
  first ran into the permission policy of the session that built the release,
  which refused deleting packages as irreversible; the maintainer authorized it,
  and it was made with `git rm`, so the history keeps every line.

## 7. Scale is measured, and the construction solver is structured

* **Factor-structured construction** [PRF-005]: a covariance built by
  `factor_model` carries its structure, and construction solves it with an
  interior point whose Newton step costs O(n k²), finished exactly on the
  binding set and certified by the dense solver's own criteria; anything it
  cannot certify goes to the dense solver, which alone may say INFEASIBLE.
  10,000 assets, five factors, budget, long-only and a 5% cap: 1.5 s of CPU in
  the stress run, 28 steps (the dense solver took 135 s at 800 assets).
* **The stress program** (`docs/audit/scripts/stress_v3_12.py`): 10,000
  assets under classification limits, 1,000 strategies with ceilings, 100
  venues' calendars, accounts and session timers, 10,000-asset construction,
  and 20,000-record checkpoint chains. Its first run found PRF-009 and
  PRF-010.
* **An event reaches strategies through an index** [PRF-010]: until v3.12
  dispatch asked every strategy whether it subscribed, so each cost about a
  third of a microsecond on every record whatever it had subscribed to.
  `strategy.subscription.RoutingIndex` names the strategies an event reaches,
  in registration order; `RuntimeState.reach` builds it once per state, and
  `RuntimeState.evolved` hands it on across the changes the runtime makes,
  rebuilding it when a strategy's subscriptions change. Dispatch order is
  unchanged.
* **Benchmarks judge ceilings by one method** [TST-011]: process CPU time,
  collector paused, sizes interleaved, fastest of three
  (`benchmarks/_stable_timing.py`).

## 8. Multi-account reconciliation, trade prints, trainable sequence models

* `lifecycle.reconcile_accounts` [BRK-004, OFE-023] compares one book with
  every account it is spread across: orders and fills per account over the
  orders declared for it, positions and cash in total with each account's
  share named; an order bound at another account than declared is a fifteenth
  mismatch class, and an undeclared order is listed, never placed by
  inference.
* **Declared trade prints** [FEA-004, BDY-018]: `data.TradeColumns` and
  `declare_trade_schema` — a print is read only from columns the caller names,
  deduplicated by its venue identifier (prints share instants), with its
  aggressor side mapped from declared codes. Depth stays out (boundary kept).
  Cleaning now judges quotes as validation does [DAT-009].
* **Backpropagation through time and attention** [SCF-004]: full BPTT for the
  LSTM and the backward pass of scaled dot-product and self-attention, each
  checked against central differences, with a trainable LSTM regressor.

---

# Consequences

* `alphalab.plugins` and `alphalab.optimizer` are gone, and importing either
  fails; a parameter search is `research.parameter_sweep` over a
  `research.ParameterSpace`, and a plugin is the host's to load.
* A run configured as in v3.11 behaves as in v3.11: ceilings, limits,
  calendars and retention are all off until declared.
* Breaking changes are in the CHANGELOG's migration table: the restated
  research engine, `LinearFit.r_squared` optional, theta's year, the distributed
  cancellation record, and a sweep space enumerated by axis name.
* Memory is bounded for the logs a policy names; the order book is not, and
  the v3.13 audit classifies it.
* Every v3.11.0 payload is read: fixtures generated by the v3.11.0 tag's own
  code are upgraded, restored and continued in the suite.

# Rejected alternatives

* **An index of bucket exposure kept by the pipeline.** It would be a second
  copy of the book's values that could drift; totals kept by the book move with
  every change the book makes, so they cannot.
* **A routing index rebuilt on every state change.** A run's first dispatch
  starts each strategy, which changes its state: 10,000 strategies would build
  10,000 indexes of 10,000 entries. The runtime knows which of its changes keep
  subscriptions, and hands the index on across those.
* **Answering a history request short when the window is exceeded.** A
  strategy reading 50 bars where it asked for 200 computes a different signal
  and is not told.
* **A trade's duplicate keyed by its instant.** Many prints share an instant;
  keying by it deletes real trades.
* **Defaulting the research engine's bounds.** Every default it had was a
  number nobody had chosen for the data at hand (252 periods, 0-100 scores).
