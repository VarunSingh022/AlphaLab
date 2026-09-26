# ADR-0042: Point-in-Time Research — Events, Alternative Data, Fundamentals, Regimes and Adaptive Strategies

## Status

**Accepted and implemented in v3.7.0.**

The seventh capability release on the architecture frozen at v3.0.0. It adds no
package. It extends the point-in-time core in `alphalab.common`, turns
`alphalab.alt_data` into the point-in-time foundation for external information,
and adds modules to `alphalab.factor_library`, `alphalab.research`,
`alphalab.strategy`, `alphalab.lifecycle` and `alphalab.api`. No ownership
boundary moves, no snapshot schema changes, no durable state is added, and every
v3.1 through v3.6 invariant holds.

Amends ADR-0009's standalone-engine classification **for `alt_data` only**:
`research`, `factor_library` and `api` now import it, so it is reached by the
lifecycle path through `research` — as `factor_library` has been since v3.2 —
while its own edges remain `alphalab.common` and nothing else.

Depends on **ADR-0017** and **ADR-0036** for the derived-identity idiom every new
identity follows and for the bytes a source is named by, **ADR-0025** for the
strategy state contract an adaptive strategy satisfies, **ADR-0029** for the run
record whose digest commits to learned state, **ADR-0035** for the class registry
an adaptive strategy is built through, **ADR-0037** for the feature engine,
signal diagnostics and study identity the new research reads and extends,
**ADR-0039** for the calendar authority sessions are read from, and **ADR-0041**
for the fingerprint and reproducibility manifest the adaptive work integrates
with.

---

# Context

Research on anything other than prices needs one fact AlphaLab could not record:
**when a piece of information became knowable.** Five capabilities rest on it,
and at v3.6 each was missing or unsafe:

**1. Events had no canonical form.** An earnings release, a CPI print, a split
announcement and a headline were each representable only in a shape of their
own — a wire `EconomicEvent` with one timestamp, `macro.CentralBankEvent`, a
`Delisting` with an effective date, a v1 `NewsSentiment` — and none could say
when the occurrence became knowable, which is the instant an event study must
anchor on.

**2. Alternative data had quality metadata and no identity.** v1's typed
categories (`NewsSentiment`, `SatelliteObservation`, …) closed the vocabulary —
a weather index needed a class — and `DataProvenance` recorded a vendor and a
confidence but no source identity, version or bytes. `known_as_of` assumed every
record had an availability instant; a vendor file that states only the period a
value describes has none.

**3. Fundamentals were a snapshot a caller assembled by hand.**
`factor_library.inputs.FundamentalSnapshot` said a future data package would
produce it. Nothing kept a fiscal period, a publication, an availability instant
and a restatement apart — the separation whose absence makes period-end-stamped,
latest-restated fundamentals look ahead twice.

**4. Regimes were labels a caller supplied.** v3.2 declined a taxonomy
(`conditional_diagnostics` takes caller labels), rightly, but offered no
deterministic, identifiable machinery for a caller's own rule, and v1's
`analyze_regimes` scores a return series rather than classifying anything.

**5. Nothing supported a strategy that learns.** A strategy that updates itself
kept its history in a mutable attribute, which the run snapshot could not see
and no rerun could verify.

---

# Decision

## 1. Placement: one leaf for external information, and no new package

The package graph decided the placement before any design did:

| Concern | Home | Why there |
| --- | --- | --- |
| Four instants, a basis, visibility, an index | `common.point_in_time` (extended) | every layer reads it; `common` imports nothing |
| Observations, events, fundamentals, sets, sessions | `alt_data` | a **leaf over `common`**: both `factor_library` and `research` must read it, and `data` may not be imported by `research` |
| Knowledge frames, PIT fundamental snapshots | `factor_library.knowledge`, `factor_library.fundamentals` | the feature engine's own input shapes |
| Event studies, regime detection | `research.event_study`, `research.regimes` | research reads `alt_data`, never `data` |
| The adaptive engine and strategy | `strategy.adaptive*` | `strategy` still imports only `common` |
| Fingerprint settings, manifest inputs, replay assessment | `lifecycle` (extended) | no key or schema changes |
| Row ingestion, wire-record lifting | `api` | the one module allowed to join `data` to a package that may not import it |

`test_v37_invariants.py` pins the three edges that matter: `alt_data` imports
only `common`; `strategy` imports only `common`; the research layer reads events
and never `alphalab.data`. The calendar is reached through
`alt_data.sessions.SessionCalendar`, a structural protocol that
`MarketCalendar` satisfies as it stands — the v3.4 `TradingDayCalendar`
precedent. A price source's bytes reach an `ObservationSource` through
`api.observation_source`, lifted from a `RawSource`, because `alt_data` cannot
import `alphalab.data`.

## 2. Four instants and a basis, stated rather than assumed

`PointInTimeStamp` carries **observed_at** (what the value describes),
**available_at** (when it became knowable), **effective_at** (when a known fact
takes effect) and **ingested_at** (when this system received it), and an
`AvailabilityBasis`: `DECLARED` by the source, `DERIVED` by a rule the stamp
names in words, or `UNKNOWN`. It refuses combinations that cannot be true —
availability before observation, ingestion before observation.

- **`UNKNOWN` is never visible.** It is not assumed available at its observation
  instant — the assumption that turns period-end-stamped data into look-ahead.
  Every selection counts such records as `unverified`, so a study reports how
  much of its source it could not use.
- **Two clocks.** `VisibilityRule.PUBLICATION` asks what the world could know;
  `INGESTION` asks what this system could know (`max(available, ingested)`), so a
  backfill is visible from its arrival.
- **Inclusive.** A record knowable exactly at an instant is visible at it — the
  convention `known_as_of` and a bar stamped at its close already follow.
- **Effective is never earlier than known.** A retroactive effective date does
  not make a fact visible before it was announced.

`PointInTimeIndex` orders records by knowledge instant and answers by bisection.
`known_as_of` is unchanged — its callers keep its semantics.

## 3. One canonical record per kind, and an open vocabulary

| Record | Carries | Identity scheme |
| --- | --- | --- |
| `ExternalObservation` | category, subject, metric, exact `Decimal` value, unit, stamp, source, optional reference period, revision | `alphalab.observation.v1` |
| `InformationEvent` | dotted `event_type`, subject, stamp, source, named measurements, text attributes, revision | `alphalab.information_event.v1` |
| `FundamentalObservation` | subject, statement, line item, fiscal period, value, unit, `published_at`, stamp, source, revision | `alphalab.fundamental.v1` |

Categories, metrics and event types are dotted identifiers the caller chooses,
so a new kind of data needs vocabulary, not code. **Revisions are vintages**: a
restatement is a new record with a higher `revision`, never an edit. Identities
are content-addressed over every field and keep a value's spelling (`1.50` is
not `1.5`). An `InformationEvent` is not an engine event (`BaseEvent`), and is
deliberately not called `Event`; `test_shared_names_stay_distinct.py` pins the
pair, and six more (provenance records, fundamental types, regime types, as-of
readers, state records, observation and replay types).

**Source identity versus source quality.** `ObservationSource` names a source by
`source_id` and `version`, which enter every record's identity; the
`content_hash` of the bytes enters the identity of a *set* read from them and
not of its records (one record read from two files is one record; the files are
two sets); `retrieved_at` and v1's `DataProvenance` (as `quality`) are recorded
and never hashed.

**Existing shapes are kept and lifted, not replaced.** v1's typed categories
lift through `observation_from_release`; `data.feed`'s single-timestamp wire
records lift through `api.lift_wire_records`, where the caller must declare what
the one timestamp meant (`WireTimestamp.AVAILABILITY` or `OBSERVATION` — the
latter yields `UNKNOWN` availability and a record research never reads).

## 4. A versioned set, checked vintages, and a view that answers at an instant

`ObservationSet` holds records of one kind with a version derived under
`alphalab.observation_set.v1` from the name, the kind, the source's identity and
the digest of its bytes, the lineage, and every record identity — independent
of arrival order. **Vintages are checked**: two records claiming one revision of
one figure are refused, and so is a revision available before the one it
revises. `restrict_subjects`, `restrict_observed`, `known_by` and
`originals_only` derive new sets that record their parent and every step, the
rule `derive_transformed_version` applies to datasets.

An `ObservationView` indexes a set once under one visibility rule — globally,
per series, and per figure — and answers `select(as_of)` (with `pending` and
`unverified` counts), `latest_in_series`, `latest_timeline` (the incremental
answer every frame samples), `vintage_as_of` and `require_vintage` (which
refuses with the instant the figure *will* become knowable, or with the fact
that nobody established when it did). `VintagePolicy` has two members:
`AS_KNOWN` (the highest revision knowable then) and `ORIGINAL`. **There is no
policy that reads today's restatement at a past instant**; hindsight has one
named home, `restatements`, which compares original and final figures across a
set.

## 5. Where information falls in the trading day

`place_in_session` classifies an instant as `IN_SESSION`, `BEFORE_OPEN`,
`BETWEEN_SESSIONS`, `AFTER_CLOSE` or `NON_TRADING_DAY` for a venue and returns
the first instant the venue is open at or after it; `place_record` applies it to
a record's knowledge instant under a visibility rule and refuses a record that
has none. There is no default calendar: round-the-clock markets are declared
with `MarketCalendar.continuous`.

## 6. Ingestion names its availability rule

`api.ingest_observations`, `ingest_events` and `ingest_fundamentals` read rows
into sets with an **explicit availability rule** — `AvailabilityFromColumn`,
`AvailabilityAfterLag` (a declared delivery lag, recorded as the `DERIVED`
rule), `AvailabilityAtNextOpen` (a stated publication read at the next session
open — how a date-only filing becomes knowable, never at midnight) or
`AvailabilityNotDeclared` (`UNKNOWN`). Timestamps are read by a declared
`TimestampReading` (format, zone, date-only policy) through `data.time`, the one
zone authority. A row that cannot be read is returned with its reason through
the data layer's own `RowRejection` and `ValidationFinding`, extended by one
finding kind, `INCONSISTENT_RECORD`.

## 7. Knowledge frames: the latest knowable value, joined honestly

A `KnowledgeFrame` samples, for each subject at each instant of a
`ResearchClock`, the most recent figure knowable then, and hands the feature
engine an `ObservationFrame` in the shape it already reads. **It is not a
forward fill** (ADR-0037 refuses to fill a price): it states a fact about the
information set, true until the next publication. A staleness bound is a
statement the caller makes, measured from the instant the figure describes;
`None` is allowed and means "any age", never a default. Every point names the
records behind it, and the frame counts what it could not answer.

**The design finding: joining two datasets without loosening a guard.** v3.2's
IC, diagnostics and decay refuse a factor and returns from different dataset
versions. A frame built from an observation set and measured against prices
needs both, and loosening the guard would admit genuine mismatches. Instead a
`ResearchClock` taken with `of_frame` records the price frame's dataset version;
the frame's identity (`alphalab.knowledge_frame.v1`) covers the set version, the
selection, the rule, the policy, the bound, the universe and the whole clock;
and `align_prices` checks that a frame was sampled on exactly this price frame's
clock and returns the prices identified by the frame's joint identity. Returns
computed from them then share the factor's identity because that identity names
both sources. `divide_frames` keeps a shared identity and derives a new one
otherwise. The v3.2 guards are unchanged.

## 8. Fundamentals as they were knowable

`FiscalPeriod` (year, optional quarter, span), `published_at`, availability no
earlier than publication, the fiscal period as the effective period, and
`revision` for restatements are separate facts. Every query takes a research
instant and a `VintagePolicy`:

- `statement_as_of`, `fundamental_as_of`, `latest_fundamental`;
- `trailing_twelve_months` sums the four most recent **consecutive** quarters
  knowable then, and refuses a balance-sheet item (a level is not a flow);
  `aggregate_timeline` computes the same aggregate incrementally, recomputed only
  when a figure arrives;
- `valuation_metrics` reads a caller-supplied price with the instant it was
  observed and refuses one observed after the research instant (a look-ahead
  through the denominator); `financial_ratios` and `year_over_year_growth`;
- undefined figures are `None` with a reason — never zero;
- units are checked (`"USD"`, `"USD/share"`, `"shares"`); a figure in another
  unit is refused, not converted;
- arithmetic is exact `Decimal` in an explicit context (28 digits, half-even),
  never the caller's thread context.

`factor_library.fundamental_snapshot_as_of` is the producer
`FundamentalSnapshot` was waiting for, feeding the v2 style factors;
`fundamental_frame` samples one input across a universe as a knowledge frame for
the v3.2 cross-sectional engine.

## 9. Event studies anchored where the news could be traded

`event_study` anchors each event at the instant it became knowable under the
study's visibility rule, places that instant in the venue's day, and takes the
**first price observation at or after the tradable instant** as offset 0 — so
nothing at a negative offset can contain the event, provided bars are stamped
when known (as AlphaLab's datasets stamp them). A correction shares its
original's vintage key and is excluded by name rather than counted as a second
reaction; an event whose availability was never established is excluded by
name. Models: `RAW`, `MARKET_ADJUSTED` (a named benchmark) and `MEAN_ADJUSTED`
(an estimation window that ends, with a gap, before the event window). Results
report per-event abnormal and cumulative returns and cross-event averages,
mean, median, dispersion and share positive with their counts, plus how many
events shared an anchor. **There is no t-statistic and no p-value**: events
cluster in calendar time, and a test that assumes independence overstates
significance by an amount that depends on the clustering — ADR-0037's reasoning
for IC.

## 10. Regimes from declared rules, with a reconstructable state

A `RegimeDefinition` (`alphalab.regime_definition.v1`) names a rule, its labels
and a **persistence** — consecutive observations a new label must hold before
the confirmed regime switches. Rules are data, so they have identities:
`ThresholdRule`, `TrailingQuantileRule` (rank within the series' own trailing
window, so thresholds use only the past) and `CompositeRule` (a table that must
name every combination — no default regime). Labels are the caller's words;
AlphaLab still ships no taxonomy. A `RegimeState` records the confirmed regime,
the candidate and its count, and the trailing windows, so **one pass and two
passes resumed from the first's final state produce the same labels,
transitions and state**, and truncating a series leaves every surviving label
unchanged. `labels_by_instant` feeds v3.2's `conditional_diagnostics`;
`regime_profile` reports spells, durations and returns per regime. v1's
`analyze_regimes` is untouched and pinned distinct.

## 11. Adaptive strategies: state that learns, and a history that replays

`strategy.adaptive` keeps seven things apart — the strategy's identity (its
fingerprint, unchanged by learning), the `AdaptiveConfiguration`
(`alphalab.adaptive_configuration.v1`), each `AdaptiveObservation`, the
immutable `AdaptiveState`, each update event (`AdaptiveTransition`), the
resulting version, and each `AdaptiveDecision` — and **`apply_update` is the one
function that moves a state**, pure and deterministic.

- **Cadence**: every observation is observed; `UpdateCadence` decides when the
  rule adapts — every observation, every *n*, or after a minimum of *event* time
  measured between observation timestamps, never a clock.
- **Ordering**: strictly increasing `(timestamp, sequence)`; a late observation
  is refused, never reordered, and is included by **reprocessing** from a
  checkpoint before its position.
- **Timing**: `DecisionTiming.BEFORE_UPDATE` (prequential) or `AFTER_UPDATE`.
- **Control**: `AdaptationMode.FROZEN` decides without learning — how a trained
  state is evaluated out of sample or deployed without further adaptation.
- **Warmup**: decisions withheld, not zeroed.
- **Lineage**: a hash chain over every step; a state's `state_id` includes it,
  so two states share an identity only if the same observations produced them in
  the same order.
- **Checkpoint and restore**: `checkpoint` renders a state as JSON-safe
  primitives with its identity; `restore` recomputes and compares it and refuses
  an edited checkpoint.
- **Rules**: `ExponentialMeanRule`, `TrailingZScoreRule` and
  `RecursiveLeastSquaresRule`, each split into `observe` and `adapt` so a slower
  cadence loses no observation. A state payload holds only floats, integers,
  strings, booleans and tuples of floats — values whose rendering and JSON form
  are both exact.

`AdaptiveStrategy` puts the engine on the execution path: a subclass says which
market event becomes an observation and what a decision means as intents, and
the base class calls the same `apply_update` a research replay folds — so **a
backtest ends in exactly the state a research replay over the same bars
reaches** (tested). It satisfies `StrategyStateProtocol`, so the run snapshot
records the adaptive state, lineage and all, and `digest_run` commits to every
update the run made: a rerun whose strategy started from a different state
diverges.

## 12. Lifecycle integration without changing a key

- **Fingerprint**: `research_configuration_with_adaptive` writes
  `adaptive.<name>.configuration` and `adaptive.<name>.initial_state` into the
  existing research-settings section. `canonical_fingerprint_key` is unchanged,
  every v3.6 fingerprint still verifies, and a strategy fingerprinted this way
  changes identity when its rule, cadence or starting state does.
- **Study inputs**: `ResearchStudy.inputs` maps a role to an identity (an
  observation-set version, a regime definition, a frame), rendered in the study
  key **only when non-empty**, so every pre-v3.7 study id is unchanged (pinned).
- **Manifests**: each study input is listed as an `ExternalInput.AUXILIARY_DATA`
  external requirement — held, like the dataset, by the caller.
- **Replay assessment**: `assess_adaptive_replay` answers with v3.6's
  `RerunOutcome` — `REPRODUCED`, `INPUTS_DIFFER` (another configuration, mode,
  starting state or stream), or `DIVERGED` with the first observation at which a
  rule proved non-deterministic — and refuses a record that does not verify.

## 13. Performance is a property of the algorithm, pinned by ratio

Every many-instant question is a bisection or a timeline, never a rescan:
selections, per-series latest values, knowledge frames, fundamental frames, event
anchoring, regime windows and replay steps. `test_v37_complexity.py` asserts
growth ratios at two sizes for each. **The v3.7 benchmark found one before
release**: `vintage_as_of` filtered its series' whole visible history on every
call, so reading one figure at *N* instants grew as *N* × history. The view now
indexes each figure's vintages at construction and reads them through
`PointInTimeStamp.is_visible` — the one visibility rule — and a regression test
holds the cost flat across sixteen times the history.

---

# Consequences

**No durable state, no store, no schema.** Every v3.7 value is frozen, produced
by a pure function, and serializes deterministically through
`alphalab.persistence.serialize`; a regression test asserts that v3.7 added no
snapshot owner and no schema constant. Identities are identical across hash
seeds and working directories, checked in fresh interpreters.

**Known limitations**

- **The execution path dispatches market events only** (wire bars and quotes
  become market inputs; other wire records do not). An adaptive strategy on the
  execution path learns from what it is dispatched; external information reaches
  adaptive state through a research replay, and a strategy can start from that
  trained checkpoint. Delivering point-in-time observations as execution-path
  events would change the canonical spine and its snapshots — a decision of its
  own.
- **Single-instant fundamental helpers scan the series.** `trailing_twelve_months`,
  `latest_fundamental` and `fundamental_inputs_as_of` read a series' visible
  history on each call — right for one instant, and the frames
  (`fundamental_frame`, `aggregate_timeline`) are the path for many.
- **`select` materializes what was visible**, so its cost is the size of its
  answer; counting is `PointInTimeIndex.count_visible`, one bisection.
- **`DataRequest.as_of` on wire records cuts on their one timestamp** — right for
  a price, and documented (pinned) as timestamp-based; stamps close the gap for
  everything else.
- **A rule is caller code.** Nothing can prove it pure; two replays compared by
  `assess_adaptive_replay` are the evidence, and a rule with hidden state shows
  up as `DIVERGED` (example 55 demonstrates one).
- **Per-share figures and share counts are read as published**, not adjusted for
  splits between a filing and the research instant, and figures in different
  currencies are refused rather than converted.
- **Event studies report no significance test**, by decision; clustering is
  reported instead.

**Deferred** — each would be a release decision of its own: statistical regime
models (hidden Markov, Markov switching), which need estimation with an identity
and a stated fitting window; execution-path delivery of external observations
(above); a streaming or incremental observation set for data arriving during a
live run.

**External** — vendor data and delivery, the bytes a set is read from, the
mapping from a vendor's line-item names to a caller's, a price for valuation,
exchange calendars' holidays, and every rule a caller writes. AlphaLab ships no
vendor adapter, fetches nothing and reads no clock.

**What this release does not add:** no vendor-specific API, adapter,
credential or network access; no LLM or AI-service dependency; no Quant-Mind,
OpenBB or RedDesk integration and no marketplace logic; no broker SDK; no regime
taxonomy; no p-values in event studies; no forward fill of prices; no default
availability, calendar, staleness bound or vintage policy.
