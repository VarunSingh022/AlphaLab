# ADR-0027: Instrument Classification and the Sector Provenance Boundary

## Status

Proposed — for v2.11.0.

Written before the implementation, for the reason ADR-0025 and ADR-0026 were:
the decision that matters — *who owns a classification, and what a completed run
remembers about it* — is cheap to settle now and expensive to change once
attribution reports depend on it.

Completes the deferral **ADR-0016** N5 was written to make safe, and supersedes
one clause of that ADR's testing invariant 9. Depends on **ADR-0016** for the
identity key and for the registry being caller-owned configuration, on
**ADR-0009** for `ExecutionPipeline` being the spine that may compose
subsystems, on **ADR-0019** for the precedent governing a label used as a map
key, and on **ADR-0023** for the snapshot envelope this release does not move.

---

# Context

ADR-0016 gave AlphaLab a canonical instrument identity and, in N5, deliberately
excluded `sector` from the identity key:

> Descriptive and **mutable**. A reclassification would silently re-identify the
> instrument and orphan every historical `Fill`. This exclusion is what makes
> deferred sector classification safe.

The deferral was taken and never returned to. Four releases of consequence sit
in the tree today.

`InstrumentRecord.sector` exists, is typed `str | None`, defaults to `None`, is
outside the identity key, and is populated by nothing.
`ExecutionPipeline._trade_record` hardcodes `sector_id=None` with a comment
explaining that AlphaLab has no security master.
`AttributionMetrics.pnl_by_sector` is fully implemented — it buckets by sector
when one is present and omits the trade when it is not, refusing the
`"UNCLASSIFIED"` placeholder v2.6 removed — is carried through the pipeline
snapshot, is decoded on restore, and is empty on every pipeline run ever
executed. `ExposureStatus.sector_exposure` is declared, typed, persisted and
decoded, and is populated by nothing at all.

Meanwhile `ExecutionPipelineConfig.instruments` already carries an
`InstrumentRegistry` through the entire execution path, and `record_for` is
already called on it — but only from `_classify_unpriced`, which a *healthy* run
never reaches. The authority is configured, threaded, and consulted only when
something has gone wrong.

Two consumers are written and waiting. What is missing is a way to say what an
instrument is classified as, and one line that reads it.

## The obstacle that makes this a decision rather than a patch

`register_instrument` refuses a record whose content differs from one the
registry already holds under the same `asset_id`, and
`InstrumentRecord.__eq__` is full dataclass equality that does not ignore
`sector`. Classifying `AAPL` after registering it therefore raises
`InstrumentRegistrationError`.

A field ADR-0016 documents as *mutable* is, in the only API that can write it,
**immutable**. Classification is not merely unpopulated today; it is impossible.

---

# Decision drivers

- **D1.** The v3.0 completion contract names "security master / sector
  attribution". The identity half shipped in v2.7; only classification is
  outstanding.
- **D2.** ADR-0016 N5's safety argument must survive intact. A reclassification
  that re-identified an instrument would orphan every fill recorded against it,
  which is the worst failure available in this area.
- **D3.** A completed run must be able to say what classification it used,
  after the fact, without the registry being present.
- **D4.** The execution path costs roughly 39 `dataclasses.replace` calls and 22
  identifiers per market event. Nothing may be added to the per-event path
  without a measurement.
- **D5.** v2.9 and v2.10 leave seven snapshot schema constants and a durable
  continuation guarantee. A release that moved a schema for a descriptive field
  would be spending compatibility on the cheapest item in the backlog.
- **D6.** AlphaLab ships no reference data and must not start. The mechanism has
  to work for an operator who declares their own classification, exactly as they
  already declare their own instruments.

---

# Decision

## 1. Classification is a registry write, and only sector

Two new module-level functions in `alphalab.instrument.registry`:

```text
classify_instrument(registry, asset_id, sector)      -> InstrumentRegistry
classify_instruments(registry, classifications)      -> InstrumentRegistry
```

Module-level rather than methods on `InstrumentRegistry`, because every write in
this package is already a function — `register_instrument`,
`register_instruments`, `register_alias` — and the registry's own methods are
pure reads — `resolve`, `record_for`. The split is deliberate and is preserved.

Keyed by `asset_id`, never by an `InstrumentRecord`. Accepting a record would
admit one whose identity fields disagree with the registered instrument's, which
is a question with no good answer: refuse it and the function is a worse
`register_instrument`; accept it and reclassification can re-identify.

`sector=None` unclassifies, and is a first-class operation rather than an error.

**Sector only.** Aliases have `register_alias`. `asset_type`, `exchange`,
`symbol` and `currency` are identity: changing one means a different instrument,
which is `register_instrument` with a new record and a new `asset_id`.

## 2. The identity-key boundary, restated and now enforced three ways

ADR-0016 N5 is unchanged: `sector` is outside the canonical key, and
`canonical_instrument_key` is not touched by this release.

v2.11 adds two further, independent guarantees. The classification API exposes
no parameter that can reach an identity field. And `dataclasses.replace` itself
raises `ValueError` for `asset_id`, because that field is declared `init=False`;
the replacement is reconstructed through `__init__`, which re-runs
`__post_init__` and re-derives `asset_id` from identity fields that did not
change, through a `normalize_key_field` that is idempotent.

Three mechanisms therefore guarantee identity stability: exclusion from the key,
a signature that cannot express the mutation, and a language-level refusal. All
three are pinned by tests, including a re-derivation of ADR-0016's golden
identifier `2b670078-27a6-57c2-b359-4e64d8809ea2` from a *classified* record.

## 3. A sector label is validated, and normalized differently from a key field

`normalize_sector_label` strips surrounding whitespace; refuses an empty or
whitespace-only label, a control character, and a non-string; and **permits**
internal whitespace and non-ASCII.

It is deliberately not `normalize_key_field`. That function refuses internal
whitespace and non-ASCII so that identity derivation cannot depend on Unicode
normalization form or on a caller's spacing. A sector derives no identifier, so
neither rule buys anything here — and both would refuse `"Consumer
Discretionary"`, which is a real sector name.

Empty is refused because `None` already means unclassified, and a `""` bucket in
a report is indistinguishable from an absent one. Control characters are refused
rather than escaped, following `normalize_key_field`: a newline inside a report
heading or a JSON key is corruption, and an escaping scheme is a second thing to
keep compatible forever.

`None` never reaches the normalizer. It is a mode of `classify_instrument`, not
a degenerate label, and conflating the two would put "is this unclassified?"
inside a string function.

**Case is preserved and never folded.** AlphaLab owns no sector taxonomy and so
has no canonical form to fold into; upper-casing `"Consumer Discretionary"`
would present the operator with a label they did not write. The precedent is
ADR-0019's currency rule, enforced in `_require_one_account_currency`:

> Comparison is exact: no `strip`, no `upper`. `CashLedger` keys balances by the
> string it is given, so `"usd"` and `"USD"` are already two separate balances —
> normalizing here would let the check pass while the ledger still split.

A label used as a map key is compared exactly. `"tech"` and `"TECH"` are
therefore two buckets; that is the operator's own registry being inconsistent,
and it is visible in their own registry.

## 4. Reclassification semantics

Reclassifying is allowed, silently, with no refusal and no event: `sector` is
descriptive and mutable, which is the entire content of ADR-0016 N5.

Reclassifying to the label already in effect — compared *after* normalization,
so surrounding whitespace does not make a second classification — returns the
**same registry object** and performs no write, matching `register_instrument`'s
no-op for an identical record and `_with_alias`'s early return for an alias
already pointing where it is asked to point.

Classification is copy-on-write through `PersistentMap.set`, which appends to
the key's version chain and rebuilds nothing, so it is O(1) and structure-shared
and N classifications cost O(N). The registry a classification came from remains
valid and unchanged, which is what makes a classified and an unclassified view
of one instrument set simultaneously readable.

Re-registering a *stale* unclassified record over a classified one remains
refused by the existing content check. That is preserved rather than relaxed: a
stale declaration must not silently un-classify an instrument.

An unknown `asset_id` raises `InstrumentInputError` through the existing
`get_instrument` vocabulary — "you named an instrument I do not hold" is the
same failure that function already reports, with the same message.

**No new exception class is introduced.** `InstrumentRegistrationError` means "a
registration would replace one identity with another", and classification
provably cannot do that: the signature cannot express it and `replace` refuses
it. A third exception for a condition that cannot arise would be inventing
vocabulary.

## 5. Provenance: two facts, two owners, two tenses

The registry answers **"what is this instrument classified as?"** — present
tense, mutable, configuration. Per ADR-0016's *Persistence semantics*, AlphaLab
does not persist it: `ConfigRecord.instruments_type` records only its type name,
and `restore` requires the caller to supply the object back.

`TradeRecord.sector_id` answers **"what was it classified as when this fill
happened?"** — past tense, frozen, run state, carried inside the pipeline
snapshot and decoded on restore.

Reclassification therefore cannot rewrite history, structurally rather than by
rule: `TradeRecord` is a frozen slotted dataclass, `trade_records` is an
`AppendOnlyLog`, and the sector is read once and copied in at the moment the
fill is applied. No record holds a reference back to the registry.

A run reclassified mid-run produces a `pnl_by_sector` that splits one asset's
P&L across two sectors. That is the correct answer — it is what happened — and
it is the reason the sector is frozen per fill rather than resolved at report
time.

`restore` does not check that a supplied registry classifies instruments the way
the captured run did, and must not. The registry is configuration, and AlphaLab
checks only its *type*, exactly as it does for `sizing_model` and `simulator`.
Records already written carry their own truth.

## 6. Pipeline integration: one site, on the fill path

The sector is resolved in `_apply_report_to_portfolio`, which already holds the
state, and is passed to `_trade_record` as an argument. `_trade_record` stays a
pure formatter with no access to state.

`_apply_report_to_portfolio` is reached from `_apply_reports`, which has exactly
two callers: the simulated path in `_process_requests`, and
`apply_execution_report`, the seam a real venue arrives through. **One insertion
point therefore serves backtest, replay, paper and live**, and simulated/venue
parity on sector is structural rather than a convention two call sites have to
keep.

Per fill and not per event, for four reasons: fills are strictly rarer than
events; the fact is only needed when a fill exists; the sector must be frozen as
of the fill, which is exactly where this sits; and the per-event path is the
budget D4 protects.

`None` is recorded in three situations — no registry configured, asset not
registered, registered but unclassified — and they are **not** distinguished.
Unlike `UnpricedReason`, where three cases call for opposite fixes and the run
otherwise produced nothing with no explanation, the consumer here asks only
whether a sector is known, and the absence is already visible as an empty
breakdown. A reason field would change `TradeRecord`'s shape, hence the
payload's shape, hence `PIPELINE_SNAPSHOT_SCHEMA`.

## 7. Attribution is a consumer and is not touched

`calculate_attribution` already buckets by `sector_id` when present, already
omits the trade when it is `None`, and already refuses a placeholder bucket. It
is already reached from `AnalyticsEngine.compile_report` through
`state.trade_records`, and `pnl_by_sector` is already round-tripped by the
snapshot decoder.

v2.11 supplies data to a correct consumer and does not open
`alphalab/analytics/attribution.py`. Touching a working consumer to make a
supplier's change look larger is the unnecessary refactor this release refuses.

## 8. Sector exposure is a SHOULD behind a measured gate

`ExposureStatus.sector_exposure` is populated inside the single pass
`_risk_exposure` already makes over positions, using **signed** market value so
that the buckets sum to `net_exposure` over classified positions, matching
`asset_exposure` rather than `gross_exposure`. Unclassified positions are
omitted rather than zeroed, for the same reason a trade with no sector is
omitted from `pnl_by_sector`.

`_risk_exposure` runs on the per-event path *and* on the per-fill path, which is
why this is gated rather than assumed. A run with no registry pays one identity
comparison per position and nothing else.

The gate: no more than 3% regression at 1,000 and at 4,000 events against
v2.10.0, measured back to back on one machine; no measurable regression for a
run with `instruments=None`; an identical `id_position.draws`; and a scaling
factor no worse than the same-session v2.10.0 control. If the gate fails, the
item ships deferred, this ADR records the measurement that deferred it,
`_risk_exposure` is left untouched, and the tolerance is not widened.

---

# Ownership

| Question | Owner |
| --- | --- |
| What identity does this instrument have? | `alphalab.instrument.identity` — unchanged |
| Which instrument does a provider symbol mean? | `InstrumentRegistry.resolve` — unchanged |
| What is this instrument classified as, now? | `InstrumentRegistry.instruments[asset_id].sector` |
| Is this a well-formed sector label? | `normalize_sector_label` |
| What was it classified as when this fill happened? | `TradeRecord.sector_id` |
| How is P&L grouped by sector? | `alphalab.analytics.attribution` — unchanged |
| What exposure sits in each sector? | `ExposureStatus.sector_exposure` (SHOULD) |

---

# Data model changes

**None.** No field is added to any type, and no type is added.

`InstrumentRecord.sector`, `TradeRecord.sector_id` and
`ExposureStatus.sector_exposure` already exist with the types this release
needs. Only the values change.

---

# Boundary behavior

| Situation | `TradeRecord.sector_id` | `pnl_by_sector` |
| --- | --- | --- |
| `config.instruments is None` | `None` | omitted |
| Registry configured, `asset_id` unregistered | `None` | omitted |
| Registered, `sector is None` | `None` | omitted |
| Registered and classified | the label | bucketed under the label |

---

# Persistence semantics

None. No new persisted type, no snapshot change, no serializer change, no schema
version movement, and no `DEFAULT_SCHEMA_VERSION` interaction.

`TradeRecord.sector_id` and `ExposureStatus.sector_exposure` are existing
persisted fields with existing decoders; their values change and their shape does
not. `PIPELINE_SNAPSHOT_SCHEMA` stays at 2, and `SESSION`, `BACKTEST`,
`ALLOCATION`, `OMS`, `PORTFOLIO` and `LIFECYCLE` all stay where v2.10 left them.

The registry remains caller-owned configuration, built at configuration time
exactly as `RiskLimits` and `CapitalBudget` are, and is not persisted — as
ADR-0016's *Persistence semantics* section already states.

---

# Testing invariants

1. Classification changes `sector` and leaves `asset_id`, `canonical_key`,
   `aliases` and every provider resolution byte-identical.
2. ADR-0016's golden identifier re-derives from a classified record.
3. `dataclasses.replace(record, asset_id=...)` raises, and the raise is pinned
   as a guarantee rather than left as an accident.
4. Classifying an unregistered `asset_id` is refused, naming it.
5. Reclassifying to the label already in effect returns the same object;
   reclassifying to a new one replaces it; `None` unclassifies.
6. Re-registering a stale unclassified record over a classified one is still
   refused.
7. A blank, whitespace-only, control-character or non-string sector is refused;
   an internally spaced or non-ASCII one is accepted; case is preserved.
8. A classified instrument reaches `TradeRecord.sector_id` through the real
   pipeline, and `pnl_by_sector` buckets a real run.
9. A venue fill applied through `apply_execution_report` records the same sector
   a simulated fill does.
10. Mid-run reclassification leaves earlier trade records unchanged and splits
    the breakdown across both sectors.
11. Resolving a sector mints no identifier: `id_position.draws` is identical to
    the v2.10.0 control for a fixed seeded workload.
12. A run with `instruments=None` is byte-identical to the v2.10.0 control.
13. Backtest, replay and paper agree on every sector.
14. The seven schema constants do not move, and a v2.10 pipeline payload
    restores and compares equal.
15. `alphalab.instrument` still imports no higher layer.
16. Sector resolution is a keyed lookup and never a scan of the registry.

Suites: `tests/unit/instrument/test_instrument_classification.py`,
`tests/regression/test_sector_classification_reaches_attribution.py`, and an
extension of `tests/regression/test_holding_period_and_sector.py`.

---

# Migration and compatibility

No persisted data is affected, so there is nothing to migrate. The public API is
additive: three new symbols, no signature change, no removal.

A caller who configures no registry, or a registry of unclassified instruments,
observes behaviour identical to v2.10.0 in every field. Every existing test in
the repository passes unchanged:
`test_the_pipeline_reports_no_sector_at_all` keeps its assertion because its
harness configures no registry, and the only test anywhere that constructs a
record with a sector asserts `asset_id` equality, which is preserved.

**Supersession.** ADR-0016's testing invariant 9 reads: "`InstrumentRecord.sector`
is `None` for every instrument v2.7 can construct, and `TradeRecord.sector_id`
remains `None`." Its first clause stands. Its second clause is superseded
**conditionally**: `TradeRecord.sector_id` remains `None` unless a configured
registry classifies the asset. ADR-0016's non-goal "Sector classification data,
or any sector value" is superseded for the *mechanism* only, and is **not**
superseded for shipping any taxonomy or reference data.

---

# Explicit non-goals

- Any sector taxonomy, classification dataset, or vendor mapping. AlphaLab ships
  no reference data. The operator declares classification the way they already
  declare instruments.
- Any classification dimension other than sector — industry, sub-industry,
  country, region, issuer, credit rating. Each is a separate decision with its
  own consumers.
- A canonical or case-folded sector vocabulary.
- Distinguishing *why* a sector is unknown, in the manner of `UnpricedReason`.
- Persisting the `InstrumentRegistry`, or checking on `restore` that a supplied
  registry classifies as the captured run did.
- Sector-based **risk limits** or enforcement. `sector_exposure` is visibility;
  `RiskLimits` is unchanged and no check reads a sector.
- `InstrumentRecord.currency` reaching `Position.currency`, and everything that
  follows from it. That is the FX release's work, and doing it here would trip
  `assert_single_currency` for every foreign instrument.
- Any change to `alphalab.analytics`.
- Any change to instrument identity derivation, the namespace, the scheme tag,
  or alias storage and resolution.

---

# Consequences

**Gained.** `pnl_by_sector` produces a real breakdown for the first time. The
instrument registry stops being consulted only when a run misbehaves and becomes
authoritative for runs that work. A v3.0 contract item closes with no schema
movement, no cost on the per-event path, no new package dependency, and no new
identifier draw. The mechanism — registry consulted per fill, answer frozen onto
the trade record — is the one the FX release will reuse for
`InstrumentRecord.currency`.

**Accepted.** AlphaLab still ships no classification data, so a sector breakdown
requires an operator who declares one. Two spellings of one sector remain two
buckets. A run reclassified mid-run reports across two sectors, which is correct
and may surprise a reader who expects the current classification to apply
retroactively.

---

# Alternatives Considered

**Relax `register_instrument` to permit a differing `sector`.** Rejected: it
turns one refusal into a conditional refusal that depends on *which* field
differs, and it makes accidental content drift indistinguishable from deliberate
reclassification. A separate function names the intent.

**A `classify` method on `InstrumentRegistry`.** Rejected: the class's methods
are pure reads and every write in the package is a module-level function.
Breaking that split for one operation costs more than it saves.

**Take an `InstrumentRecord` rather than an `asset_id`.** Rejected: it admits a
record whose identity fields disagree with the registered instrument's, which has
no good answer.

**Case-fold sector labels.** Rejected on the ADR-0019 precedent: normalizing a
label at one site while the map it keys still splits at another is worse than not
normalizing at all, and AlphaLab owns no taxonomy to fold into.

**Reuse `normalize_key_field` for the sector.** Rejected: it would refuse
`"Consumer Discretionary"` and every non-ASCII sector name, enforcing rules that
exist to make *identity derivation* byte-identical on a field that derives
nothing.

**Resolve the sector at report time from the registry, instead of freezing it
per fill.** Rejected: a completed run's attribution would depend on whichever
registry happened to be in hand when the report was compiled, so the same run
would report differently before and after a reclassification, and a run restored
without its registry would report nothing. Freezing per fill is what makes the
answer a fact.

**Add a `sector_source` or `classified_at` field to `TradeRecord`.** Rejected: it
changes the persisted payload shape and would move `PIPELINE_SNAPSHOT_SCHEMA` for
provenance the frozen `sector_id` already carries.

**Resolve the sector once per event rather than per fill.** Rejected: it puts
work on the path D4 protects, for a fact only a fill needs.

---

# Release impact

Minor version, additive public API, no persisted-format change. Three new
exported symbols; no removal, no signature change, and no schema constant moves.
